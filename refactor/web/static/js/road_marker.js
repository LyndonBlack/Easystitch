/**
 * EasyStitch — Satin V2 Road Marker checklist pipeline.
 *
 * Frontend scope:
 * - collect current Pane 3 objects/assignments
 * - call /api/roads/mask_only — display mask stats and preview
 * - call /api/roads/centerline — display graph stats, overlay SVG, build road segments
 * - road segment editor: convert graph edges to segments, mark primary/secondary/ignore
 * - export road JSON
 * - clear overlay and/or road segments
 */

const RoadMarker = (function() {
  // ── helpers ──────────────────────────────────────────────────────────────

  async function apiCall(endpoint, body) {
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      return await res.json();
    } catch (e) {
      console.error('RoadMarker API error:', endpoint, e);
      return {ok: false, error: 'Network error: ' + e.message};
    }
  }

  function roadMaskToast(message, ms) {
    if (typeof toast === 'function') toast(message, ms || 3200);
    else console.log(message);
  }

  function collectPane3RoadGraphPayload() {
    if (typeof structureLoaded === 'undefined' || !structureLoaded) {
      throw new Error('Load Pane 3 structure first.');
    }
    if (typeof structureObjects === 'undefined' || !Array.isArray(structureObjects) || !structureObjects.length) {
      throw new Error('No current Pane 3 objects available.');
    }

    const assignments = {};
    structureObjects.forEach(obj => {
      const assigned = (typeof stitchAssignments !== 'undefined' && stitchAssignments)
        ? stitchAssignments[obj.id]
        : null;
      const fallback = (typeof defaultStitchType === 'function') ? defaultStitchType(obj) : 'fill';
      assignments[obj.id] = assigned || fallback;
    });

    return {
      svg_w: structureSvgW,
      svg_h: structureSvgH,
      objects: JSON.parse(JSON.stringify(structureObjects)),
      assignments,
      settings: {
        mask_scale: 4,
        threshold: 128,
        median_filter: true,
        min_length_px: 5,
        simplify_tolerance: 1,
        snap_distance: 3,
        despeckle_level: 8,
        filter_iterations: 4,
        error_threshold: 2.0,
      },
    };
  }

  // ── mask-only preview (Phase A) ──────────────────────────────────────────

  function renderSatinMaskPreview(data) {
    const stats = document.getElementById('road-mask-stats');
    const preview = document.getElementById('road-mask-preview');
    if (!stats || !preview) return;

    if (!data || !data.ok) {
      stats.innerHTML = 'Mask build failed: <b>' + (data?.error || 'unknown') + '</b>';
      preview.innerHTML = '<span style="color:#933">No mask generated</span>';
      return;
    }

    const mask = data.mask || {};
    const debug = data.debug || {};
    const satinIds = mask.satin_object_ids || [];
    const excludedIds = mask.excluded_object_ids || [];

    stats.innerHTML = `
      Satin objects included: <b>${satinIds.length}</b><br>
      Excluded objects: <b>${excludedIds.length}</b><br>
      Mask: <b>${mask.width_px}×${mask.height_px}</b> px @ scale <b>${mask.scale}</b><br>
      SVG: <b>${mask.svg_w}×${mask.svg_h}</b>
    `;

    if (debug.mask_png_base64) {
      preview.innerHTML = `<img src="data:image/png;base64,${debug.mask_png_base64}" alt="Satin-only road mask" style="max-width:100%;max-height:240px;image-rendering:pixelated;background:#fff">`;
    } else {
      preview.innerHTML = '<span style="color:#555">Mask returned without image preview.</span>';
    }
  }

  // ── centerline graph + road segment builder (Phase A + B) ────────────────

  /**
   * Convert backend graph edges into frontend road segment model (Phase B.1).
   * Stores result in global `roadSegments` and `roadSegmentsBuilt`.
   */
  function buildRoadSegmentsFromGraph(graphData) {
    roadSegments = [];
    roadSelectedSegmentId = null;
    if (!graphData || !graphData.edges || !graphData.nodes) {
      roadSegmentsBuilt = false;
      return;
    }

    graphData.edges.forEach((edge, idx) => {
      const segId = 'seg_' + String(idx + 1).padStart(4, '0');
      roadSegments.push({
        segment_id: segId,
        edge_id: edge.id,
        source_node: edge.source,
        target_node: edge.target,
        points: edge.points || [],
        length: edge.length || 0,
        source_object_ids: edge.source_object_ids || [],
        priority: null,
        role: 'unmarked',
        selected: false,
        locked: false,
        start_rung_id: null,
        end_rung_id: null,
        notes: '',
      });
    });
    roadSegmentsBuilt = true;
  }

  /**
   * Update the side-panel stats display for road segments and selected info.
   */
  function updateRoadSegmentStats() {
    const container = document.getElementById('road-segment-stats');
    if (!container) return;

    const segCount = roadSegments.length;
    const nodeMap = new Map();
    const selSeg = roadSegments.find(s => s.segment_id === roadSelectedSegmentId);

    // Count unique nodes referenced by segments
    roadSegments.forEach(s => {
      if (s.source_node) nodeMap.set(s.source_node, true);
      if (s.target_node) nodeMap.set(s.target_node, true);
    });

    // Count junctions (nodes referenced by at least 2 edges) using degree inference
    const nodeDegree = {};
    roadSegments.forEach(s => {
      if (s.source_node) nodeDegree[s.source_node] = (nodeDegree[s.source_node] || 0) + 1;
      if (s.target_node) nodeDegree[s.target_node] = (nodeDegree[s.target_node] || 0) + 1;
    });
    const junctionCount = Object.values(nodeDegree).filter(d => d >= 2).length;

    let html = '';
    html += `<span style="color:#8f96b3">Segments:</span> <b>${segCount}</b>`;
    html += ` &middot; <span style="color:#8f96b3">Nodes:</span> <b>${nodeMap.size}</b>`;
    html += ` &middot; <span style="color:#8f96b3">Junctions:</span> <b>${junctionCount}</b>`;

    if (selSeg) {
      const roleNames = {unmarked: 'Unmarked', primary: 'Primary', secondary: 'Secondary', ignore: 'Ignore'};
      const roleLabel = roleNames[selSeg.role] || selSeg.role;
      html += `<br><br><span style="color:#8f96b3">Selected:</span> <b>${selSeg.segment_id}</b>`;
      html += `<br><span style="color:#8f96b3">Length:</span> ${selSeg.length.toFixed(1)} px`;
      html += `<br><span style="color:#8f96b3">Role:</span> <b style="color:${roleColor(selSeg.role)}">${roleLabel}</b>`;
      html += `<br><span style="color:#8f96b3">Edge:</span> ${selSeg.edge_id}`;
    } else {
      html += '<br><br><span style="color:#555">No segment selected.</span>';
    }

    container.innerHTML = html;
    updateSegmentSelectionUI();
  }

  function roleColor(role) {
    switch (role) {
      case 'primary':   return '#4a9eff';
      case 'secondary': return '#4caf50';
      case 'ignore':    return '#888';
      default:          return '#00bcd4'; // unmarked = cyan
    }
  }

  function roleDisplayClass(role) {
    switch (role) {
      case 'primary':   return 'road-seg-primary';
      case 'secondary': return 'road-seg-secondary';
      case 'ignore':    return 'road-seg-ignore';
      default:          return 'road-seg-unmarked';
    }
  }

  // ── selection ────────────────────────────────────────────────────────────

  function selectRoadSegment(segmentId) {
    if (segmentId === roadSelectedSegmentId) {
      deselectRoadSegment();
      return;
    }
    roadSelectedSegmentId = segmentId;
    roadSegments.forEach(s => { s.selected = (s.segment_id === segmentId); });
    updateRoadSegmentStats();
  }

  function deselectRoadSegment() {
    roadSelectedSegmentId = null;
    roadSegments.forEach(s => { s.selected = false; });
    updateRoadSegmentStats();
  }

  // ── marking / role assignment (Phase B.2) ────────────────────────────────

  function markSelectedSegment(role) {
    if (!roadSelectedSegmentId) {
      roadMaskToast('Select a road segment first (click on the overlay).', 3200);
      return;
    }
    const seg = roadSegments.find(s => s.segment_id === roadSelectedSegmentId);
    if (!seg) return;
    seg.role = role;
    seg.priority = (role === 'primary') ? 0 : (role === 'secondary') ? 1 : null;
    updateRoadSegmentStats();
    updateOverlaySegmentColors();
    const label = role.charAt(0).toUpperCase() + role.slice(1);
    roadMaskToast(`Segment ${seg.segment_id} marked as ${label}`, 2000);
  }

  function clearSelectedSegmentMark() {
    if (!roadSelectedSegmentId) {
      roadMaskToast('Select a road segment first.', 2000);
      return;
    }
    const seg = roadSegments.find(s => s.segment_id === roadSelectedSegmentId);
    if (!seg) return;
    seg.role = 'unmarked';
    seg.priority = null;
    updateRoadSegmentStats();
    updateOverlaySegmentColors();
    roadMaskToast(`Segment ${seg.segment_id} cleared to Unmarked`, 2000);
  }

  /**
   * Update the existing overlay SVG stroke colours to reflect current segment roles.
   */
  function updateOverlaySegmentColors() {
    const overlay = document.getElementById('road-graph-overlay');
    if (!overlay) return;
    const svg = overlay.querySelector('svg');
    if (!svg) return;

    roadSegments.forEach(seg => {
      const el = svg.querySelector(`[data-edge-id="${seg.edge_id}"]`);
      if (el) {
        el.setAttribute('stroke', roleColor(seg.role));
        if (seg.role === 'ignore') {
          el.setAttribute('stroke-dasharray', '4,4');
        } else {
          el.setAttribute('stroke-dasharray', 'none');
        }
        el.setAttribute('stroke-width', seg.selected ? '3' : '1.5');
      }
    });
  }

  /**
   * Highlight currently selected segment in the overlay.
   */
  function updateSegmentSelectionUI() {
    const overlay = document.getElementById('road-graph-overlay');
    if (!overlay) return;
    const svg = overlay.querySelector('svg');
    if (!svg) return;

    roadSegments.forEach(seg => {
      const el = svg.querySelector(`[data-edge-id="${seg.edge_id}"]`);
      if (el) {
        el.setAttribute('stroke-width', seg.selected ? '3' : '1.5');
        // selected highlight
        if (seg.selected) {
          el.setAttribute('stroke', '#ffeb3b'); // yellow highlight
        } else {
          el.setAttribute('stroke', roleColor(seg.role));
        }
      }
    });
  }

  // ── clear / reset ────────────────────────────────────────────────────────

  function clearAllSegments() {
    roadSegments = [];
    roadSelectedSegmentId = null;
    roadSegmentsBuilt = false;
    const stats = document.getElementById('road-segment-stats');
    if (stats) stats.innerHTML = 'No road segments built yet.';
    roadMaskToast('Road segments cleared.', 2000);
  }

  // ── export JSON (Phase B.2) ──────────────────────────────────────────────

  function exportRoadJson() {
    const graphEl = document.getElementById('road-graph-overlay');
    const svgEl = graphEl ? graphEl.querySelector('svg') : null;
    let svgW = structureSvgW || 500;
    let svgH = structureSvgH || 500;
    if (svgEl) {
      const vb = svgEl.getAttribute('viewBox');
      if (vb) {
        const parts = vb.split(/\s+/).map(Number);
        if (parts.length === 4 && parts[2] > 0 && parts[3] > 0) {
          svgW = parts[2];
          svgH = parts[3];
        }
      }
    }

    const nodeSet = new Set();
    const nodeData = [];
    roadSegments.forEach(s => {
      if (s.source_node && !nodeSet.has(s.source_node)) {
        nodeSet.add(s.source_node);
        nodeData.push({ node_id: s.source_node });
      }
      if (s.target_node && !nodeSet.has(s.target_node)) {
        nodeSet.add(s.target_node);
        nodeData.push({ node_id: s.target_node });
      }
    });

    const exportData = {
      version: 1,
      svg_w: svgW,
      svg_h: svgH,
      segments: roadSegments.map(s => ({
        segment_id: s.segment_id,
        edge_id: s.edge_id,
        source_node: s.source_node,
        target_node: s.target_node,
        points: s.points,
        length: s.length,
        source_object_ids: s.source_object_ids,
        priority: s.priority,
        role: s.role,
        locked: s.locked,
        start_rung_id: s.start_rung_id,
        end_rung_id: s.end_rung_id,
        notes: s.notes,
      })),
      nodes: nodeData,
      rungs: {},
      stitch_order: [],
      metadata: {
        created_from: 'autotrace_centerline',
        mask_scale: 4,
      },
    };

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'road_segments.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    roadMaskToast('Road JSON exported.', 2000);
  }

  // ── overlay click handler for segment selection ──────────────────────────

  function attachOverlayClickHandler() {
    const overlay = document.getElementById('road-graph-overlay');
    if (!overlay) return;
    // Remove old handler if any
    const old = overlay._clickHandler;
    if (old) overlay.removeEventListener('click', old);
    const handler = function(e) {
      if (!roadSegmentsBuilt || !roadSegments.length) return;
      // Find the clicked edge element
      let target = e.target;
      while (target && target !== overlay) {
        const edgeId = target.getAttribute && target.getAttribute('data-edge-id');
        if (edgeId) {
          selectRoadSegmentByEdgeId(edgeId);
          return;
        }
        target = target.parentNode;
      }
    };
    overlay._clickHandler = handler;
    overlay.addEventListener('click', handler);
  }

  function selectRoadSegmentByEdgeId(edgeId) {
    const seg = roadSegments.find(s => s.edge_id === edgeId);
    if (seg) {
      selectRoadSegment(seg.segment_id);
    }
  }

  // ── render: centerline graph + overlay + segments (Phase A + B) ─────────

  function renderCenterlineGraph(data) {
    const stats = document.getElementById('road-graph-stats');
    const overlay = document.getElementById('road-graph-overlay');
    if (!stats) return;

    if (!data || !data.ok) {
      stats.innerHTML = 'Centerline build failed: <b>' + (data?.error || 'unknown') + '</b>';
      if (overlay) overlay.innerHTML = '<span style="color:#933">No graph generated</span>';
      clearAllSegments();
      return;
    }

    const s = data.stats || {};
    const graph = data.graph || {};
    const nodes = graph.nodes || [];
    const edges = graph.edges || [];
    const junctionCount = nodes.filter(n => n.type === 'junction').length;

    stats.innerHTML = `
      Satin objects included: <b>${s.satin_object_count}</b><br>
      Excluded objects: <b>${s.excluded_object_count}</b><br>
      Centerline paths: <b>${s.clean_polyline_count} / ${s.raw_polyline_count}</b> (clean / raw)<br>
      Graph nodes: <b>${s.graph_node_count}</b><br>
      Graph edges: <b>${s.graph_edge_count}</b><br>
      Junction nodes: <b>${junctionCount}</b><br>
      Mask: <b>${s.mask_width_px}×${s.mask_height_px}</b> px @ scale <b>${s.mask_scale}</b>
    `;

    if (overlay && data.debug && data.debug.overlay_svg) {
      overlay.innerHTML = data.debug.overlay_svg;
      const svgEl = overlay.querySelector('svg');
      if (svgEl) {
        svgEl.style.maxWidth = '100%';
        svgEl.style.maxHeight = '100%';
        svgEl.style.width = 'auto';
        svgEl.style.height = 'auto';
        svgEl.removeAttribute('width');
        svgEl.removeAttribute('height');

        // Tag each edge path with data-edge-id for click selection
        edges.forEach(edge => {
          const path = svgEl.querySelector(`[id="${edge.id}"]`);
          if (path) {
            path.setAttribute('data-edge-id', edge.id);
            path.style.cursor = 'pointer';
          }
        });
      }
    }

    // Phase B.1: build road segments from graph edges
    buildRoadSegmentsFromGraph(graph);
    updateRoadSegmentStats();
    attachOverlayClickHandler();
    updateOverlaySegmentColors();

    // Enable segment action buttons
    document.querySelectorAll('.road-seg-action-btn').forEach(b => b.disabled = false);
  }

  // ── async actions ────────────────────────────────────────────────────────

  async function buildSatinMaskOnly() {
    const btn = document.getElementById('road-mask-build-btn');
    const stats = document.getElementById('road-mask-stats');
    try {
      const payload = collectPane3RoadGraphPayload();
      const satinCount = Object.values(payload.assignments).filter(v => String(v).toLowerCase() === 'satin').length;
      if (stats) stats.innerHTML = 'Building Satin-only mask from current Pane 3 state…<br>Satin assignments: <b>' + satinCount + '</b>';
      if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span>Building mask…';
      }

      const result = await apiCall('/api/roads/mask_only', payload);
      renderSatinMaskPreview(result);
      if (!result || !result.ok) {
        roadMaskToast('Satin mask failed: ' + (result?.error || 'unknown'), 7000);
      } else {
        roadMaskToast('Satin mask built: ' + (result.mask?.satin_object_ids || []).length + ' Satin object(s) included');
      }
      return result;
    } catch (e) {
      renderSatinMaskPreview({ok: false, error: e.message || String(e)});
      roadMaskToast('Satin mask error: ' + (e.message || e), 7000);
      return {ok: false, error: e.message || String(e)};
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Build Satin Mask';
      }
    }
  }

  async function buildCenterlineGraph() {
    const btn = document.getElementById('road-graph-build-btn');
    const stats = document.getElementById('road-graph-stats');
    try {
      const payload = collectPane3RoadGraphPayload();
      if (stats) stats.innerHTML = 'Building centerline graph from current Pane 3 state…';
      if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span>Building graph…';
      }

      const result = await apiCall('/api/roads/centerline', payload);
      renderCenterlineGraph(result);
      if (!result || !result.ok) {
        roadMaskToast('Centerline graph failed: ' + (result?.error || 'unknown'), 7000);
      } else {
        const g = result.graph || {};
        roadMaskToast('Centerline graph built: ' + (g.nodes || []).length + ' nodes, ' + (g.edges || []).length + ' edges');
      }
      return result;
    } catch (e) {
      renderCenterlineGraph({ok: false, error: e.message || String(e)});
      roadMaskToast('Centerline graph error: ' + (e.message || e), 7000);
      return {ok: false, error: e.message || String(e)};
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Build Centerline Graph';
      }
    }
  }

  function clearOverlay() {
    const stats = document.getElementById('road-graph-stats');
    const overlay = document.getElementById('road-graph-overlay');
    if (stats) stats.textContent = 'No centerline graph built yet.';
    if (overlay) overlay.innerHTML = '<span style="color:#555">Road graph overlay will appear here.</span>';
    const segStats = document.getElementById('road-segment-stats');
    if (segStats) segStats.innerHTML = 'No road segments built yet.';
    // Disable action buttons
    document.querySelectorAll('.road-seg-action-btn').forEach(b => b.disabled = true);
    clearAllSegments();
  }

  // ── public API ───────────────────────────────────────────────────────────

  return {
    collectPane3RoadGraphPayload,
    buildSatinMaskOnly,
    renderSatinMaskPreview,
    buildCenterlineGraph,
    renderCenterlineGraph,
    clearOverlay,

    // Phase B — road segment editor
    buildRoadSegmentsFromGraph,
    updateRoadSegmentStats,
    selectRoadSegment,
    deselectRoadSegment,
    markSelectedSegment,
    clearSelectedSegmentMark,
    clearAllSegments,
    exportRoadJson,
    updateOverlaySegmentColors,
    updateSegmentSelectionUI,
  };
})();
