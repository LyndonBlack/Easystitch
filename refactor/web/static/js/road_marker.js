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
      case 'secondary': return '#ff9800';
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

  function overlayVisibleEdgeElement(svg, edgeId) {
    if (!svg || !edgeId) return null;
    return svg.querySelector(`.road-small-edge-visible[data-edge-id="${edgeId}"]`)
      || svg.querySelector(`[data-edge-id="${edgeId}"]:not(.road-small-edge-hit)`);
  }

  function setRoadPathStroke(path, color) {
    if (!path) return;
    path.setAttribute('stroke', color);
    path.style.setProperty('stroke', color);
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
    updateRoadEditorSegmentColors();
    updateRoadEditorSelectedDetails();
  }

  function deselectRoadSegment() {
    roadSelectedSegmentId = null;
    roadSegments.forEach(s => { s.selected = false; });
    updateRoadSegmentStats();
    updateRoadEditorSegmentColors();
    updateRoadEditorSelectedDetails();
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
    updateRoadEditorSegmentColors();
    updateRoadEditorSelectedDetails();
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
    updateRoadEditorSegmentColors();
    updateRoadEditorSelectedDetails();
    roadMaskToast(`Segment ${seg.segment_id} cleared to Unmarked`, 2000);
  }

  function updateSmallOverlaySelectionHighlight(svg) {
    if (!svg) return;
    svg.querySelectorAll('.road-small-edge-selected-highlight').forEach(el => el.remove());
    roadSegments.filter(seg => seg.selected).forEach(seg => {
      const visible = overlayVisibleEdgeElement(svg, seg.edge_id);
      if (!visible || !visible.parentNode) return;
      const highlight = visible.cloneNode(false);
      highlight.removeAttribute('id');
      highlight.removeAttribute('style');
      highlight.setAttribute('class', 'road-small-edge-selected-highlight');
      highlight.setAttribute('data-edge-id', seg.edge_id);
      highlight.setAttribute('stroke', '#ffeb3b');
      highlight.style.setProperty('stroke', '#ffeb3b');
      highlight.setAttribute('stroke-width', '5');
      highlight.setAttribute('fill', 'none');
      highlight.setAttribute('stroke-linecap', 'round');
      highlight.setAttribute('stroke-linejoin', 'round');
      highlight.setAttribute('pointer-events', 'none');
      highlight.setAttribute('vector-effect', 'non-scaling-stroke');
      visible.parentNode.insertBefore(highlight, visible);
    });
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
      const el = overlayVisibleEdgeElement(svg, seg.edge_id);
      if (el) {
        setRoadPathStroke(el, roleColor(seg.role));
        if (seg.role === 'ignore') {
          el.setAttribute('stroke-dasharray', '4,4');
        } else {
          el.setAttribute('stroke-dasharray', 'none');
        }
        el.setAttribute('stroke-width', seg.selected ? '2.2' : '1.5');
      }
    });
    updateSmallOverlaySelectionHighlight(svg);
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
      const el = overlayVisibleEdgeElement(svg, seg.edge_id);
      if (el) {
        el.setAttribute('stroke-width', seg.selected ? '2.2' : '1.5');
        setRoadPathStroke(el, roleColor(seg.role));
      }
    });
    updateSmallOverlaySelectionHighlight(svg);
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

  // ── Phase C: large Segment Editor overlay ────────────────────────────────

  function editorSvg() {
    return document.getElementById('road-editor-svg');
  }

  function pathDataFromPoints(points) {
    if (!points || !points.length) return '';
    return points.map((p, idx) => `${idx === 0 ? 'M' : 'L'} ${Number(p[0]).toFixed(3)} ${Number(p[1]).toFixed(3)}`).join(' ');
  }

  function segmentById(segmentId) {
    return roadSegments.find(s => s.segment_id === segmentId) || null;
  }

  function setRoadEditorViewBox(vb) {
    const svg = editorSvg();
    if (!svg || !vb) return;
    roadEditorViewBox = {
      x: Number(vb.x),
      y: Number(vb.y),
      w: Math.max(1, Number(vb.w)),
      h: Math.max(1, Number(vb.h)),
    };
    svg.setAttribute('viewBox', `${roadEditorViewBox.x} ${roadEditorViewBox.y} ${roadEditorViewBox.w} ${roadEditorViewBox.h}`);
  }

  function resetRoadEditorView() {
    setRoadEditorViewBox({x: 0, y: 0, w: structureSvgW || 500, h: structureSvgH || 500});
  }

  function zoomRoadEditor(factor, center) {
    const svg = editorSvg();
    if (!svg) return;
    if (!roadEditorViewBox) resetRoadEditorView();
    const vb = roadEditorViewBox;
    const c = center || {x: vb.x + vb.w / 2, y: vb.y + vb.h / 2};
    const nextW = Math.max(5, vb.w * factor);
    const nextH = Math.max(5, vb.h * factor);
    setRoadEditorViewBox({
      x: c.x - (c.x - vb.x) * (nextW / vb.w),
      y: c.y - (c.y - vb.y) * (nextH / vb.h),
      w: nextW,
      h: nextH,
    });
  }

  function roadEditorSvgPoint(evt) {
    const svg = editorSvg();
    if (!svg) return null;
    const matrix = svg.getScreenCTM();
    if (!matrix) return null;
    const pt = svg.createSVGPoint();
    pt.x = evt.clientX;
    pt.y = evt.clientY;
    const p = pt.matrixTransform(matrix.inverse());
    return {x: p.x, y: p.y};
  }

  function clearLayer(id) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = '';
    return el;
  }

  function renderRoadEditorBackground() {
    const layer = clearLayer('road-bg-layer');
    if (!layer || typeof structureObjects === 'undefined') return;
    const objs = [...structureObjects].sort((a, b) => (a.order || 0) - (b.order || 0));
    objs.forEach(obj => {
      const assigned = (typeof stitchAssignments !== 'undefined' && stitchAssignments[obj.id])
        ? stitchAssignments[obj.id]
        : ((typeof defaultStitchType === 'function') ? defaultStitchType(obj) : 'fill');
      if (String(assigned).toLowerCase() !== 'satin') return;
      const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      p.setAttribute('class', 'road-bg-path');
      p.setAttribute('d', obj.d || '');
      if (obj.tx || obj.ty) p.setAttribute('transform', `translate(${obj.tx || 0},${obj.ty || 0})`);
      layer.appendChild(p);
    });
  }

  function updateRoadEditorSelectedDetails() {
    const details = document.getElementById('road-editor-selected-details');
    if (!details) return;
    const seg = segmentById(roadSelectedSegmentId);
    if (!seg) {
      details.textContent = roadSegmentsBuilt ? 'No segment selected.' : 'Build the centerline graph before opening the editor.';
      return;
    }
    const roleNames = {unmarked: 'Unmarked', primary: 'Primary', secondary: 'Secondary', ignore: 'Ignore'};
    details.innerHTML = `Selected: <b>${seg.segment_id}</b> · length <b>${seg.length.toFixed(1)} px</b> · role <b style="color:${roleColor(seg.role)}">${roleNames[seg.role] || seg.role}</b> · edge <b>${seg.edge_id}</b> · nodes <b>${seg.source_node || '?'} → ${seg.target_node || '?'}</b>`;
  }

  function updateRoadEditorToolButtons() {
    document.querySelectorAll('.road-editor-tool-btn').forEach(btn => btn.classList.remove('active'));
    const active = document.getElementById(roadEditorMode === 'pan' ? 'road-editor-pan-btn' : 'road-editor-select-btn');
    if (active) active.classList.add('active');
    const svg = editorSvg();
    if (svg) svg.style.cursor = roadEditorMode === 'pan' ? 'grab' : 'default';
  }

  function setSegmentEditorMode(mode) {
    roadEditorMode = (mode === 'pan') ? 'pan' : 'select';
    updateRoadEditorToolButtons();
  }

  function updateRoadEditorSelectionHighlight() {
    const layer = document.getElementById('road-edge-layer');
    if (!layer) return;
    layer.querySelectorAll('.road-edge-selected-highlight').forEach(el => el.remove());
    roadSegments.filter(seg => seg.selected).forEach(seg => {
      const d = pathDataFromPoints(seg.points);
      if (!d) return;
      const highlight = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      highlight.setAttribute('class', 'road-edge-selected-highlight');
      highlight.setAttribute('d', d);
      highlight.setAttribute('fill', 'none');
      highlight.setAttribute('stroke', '#ffeb3b');
      highlight.style.setProperty('stroke', '#ffeb3b');
      highlight.setAttribute('stroke-width', '6');
      highlight.setAttribute('stroke-linecap', 'round');
      highlight.setAttribute('stroke-linejoin', 'round');
      highlight.setAttribute('pointer-events', 'none');
      highlight.setAttribute('vector-effect', 'non-scaling-stroke');
      layer.insertBefore(highlight, layer.firstChild);
    });
  }

  function updateRoadEditorSegmentColors() {
    const svg = editorSvg();
    if (!svg) return;
    roadSegments.forEach(seg => {
      const visible = svg.querySelector(`.road-edge-visible[data-segment-id="${seg.segment_id}"]`);
      if (!visible) return;
      setRoadPathStroke(visible, roleColor(seg.role));
      visible.setAttribute('stroke-width', seg.selected ? '2.6' : '1.8');
      visible.setAttribute('stroke-dasharray', seg.role === 'ignore' ? '6 4' : 'none');
    });
    updateRoadEditorSelectionHighlight();
  }

  function selectRoadSegmentFromEditor(segmentId) {
    if (!segmentId) return;
    selectRoadSegment(segmentId);
  }

  function renderRoadEditorEdges() {
    const layer = clearLayer('road-edge-layer');
    if (!layer) return;
    roadSegments.forEach(seg => {
      const d = pathDataFromPoints(seg.points);
      if (!d) return;
      const hit = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      hit.setAttribute('class', 'road-edge-hit');
      hit.setAttribute('data-segment-id', seg.segment_id);
      hit.setAttribute('data-edge-id', seg.edge_id);
      hit.setAttribute('d', d);
      hit.addEventListener('click', ev => {
        if (roadEditorMode !== 'select') return;
        ev.stopPropagation();
        selectRoadSegmentFromEditor(seg.segment_id);
      });
      const visible = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      visible.setAttribute('class', 'road-edge-visible');
      visible.setAttribute('data-segment-id', seg.segment_id);
      visible.setAttribute('data-edge-id', seg.edge_id);
      visible.setAttribute('d', d);
      layer.appendChild(hit);
      layer.appendChild(visible);
    });
  }

  function renderRoadEditorNodes() {
    const layer = clearLayer('road-node-layer');
    if (!layer) return;
    const nodes = (roadGraphData && roadGraphData.nodes) ? roadGraphData.nodes : [];
    nodes.forEach(node => {
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      c.setAttribute('class', 'road-node');
      c.setAttribute('cx', String(node.x));
      c.setAttribute('cy', String(node.y));
      c.setAttribute('r', node.type === 'manual_split_boundary' ? '3.8' : (node.type === 'junction' ? '3.2' : '2.4'));
      c.setAttribute('fill', node.type === 'manual_split_boundary' ? '#ff00ff' : (node.type === 'junction' ? '#ff9800' : '#2ecc71'));
      c.setAttribute('stroke', '#fff');
      c.setAttribute('stroke-width', '0.8');
      layer.appendChild(c);
    });
  }

  function renderSegmentEditor() {
    const svg = editorSvg();
    if (!svg) return;
    svg.setAttribute('viewBox', `0 0 ${structureSvgW || 500} ${structureSvgH || 500}`);
    if (!roadEditorViewBox) resetRoadEditorView();
    else setRoadEditorViewBox(roadEditorViewBox);
    renderRoadEditorBackground();
    renderRoadEditorEdges();
    renderRoadEditorNodes();
    clearLayer('road-highlight-layer');
    updateRoadEditorSegmentColors();
    updateRoadEditorSelectedDetails();
    updateRoadEditorToolButtons();
  }

  function installRoadEditorPanHandlers() {
    const svg = editorSvg();
    if (!svg || svg._roadEditorHandlersInstalled) return;
    svg._roadEditorHandlersInstalled = true;
    let dragging = false;
    let last = null;

    svg.addEventListener('wheel', ev => {
      ev.preventDefault();
      const p = roadEditorSvgPoint(ev);
      zoomRoadEditor(ev.deltaY < 0 ? 0.88 : 1.14, p);
    }, {passive: false});

    svg.addEventListener('mousedown', ev => {
      if (roadEditorMode !== 'pan' && ev.button !== 1) return;
      ev.preventDefault();
      dragging = true;
      last = roadEditorSvgPoint(ev);
      svg.style.cursor = 'grabbing';
    });

    window.addEventListener('mousemove', ev => {
      if (!dragging || !last || !roadEditorViewBox) return;
      const now = roadEditorSvgPoint(ev);
      if (!now) return;
      setRoadEditorViewBox({
        x: roadEditorViewBox.x - (now.x - last.x),
        y: roadEditorViewBox.y - (now.y - last.y),
        w: roadEditorViewBox.w,
        h: roadEditorViewBox.h,
      });
      last = roadEditorSvgPoint(ev);
    });

    window.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      last = null;
      updateRoadEditorToolButtons();
    });
  }

  function openSegmentEditor() {
    if (!roadSegmentsBuilt || !roadSegments.length) {
      roadMaskToast('Build the centerline graph before opening the segment editor.', 3200);
      return;
    }
    const modal = document.getElementById('road-segment-editor-modal');
    if (!modal) return;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    if (!roadEditorViewBox) resetRoadEditorView();
    installRoadEditorPanHandlers();
    renderSegmentEditor();
  }

  function closeSegmentEditor() {
    const modal = document.getElementById('road-segment-editor-modal');
    if (!modal) return;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }

  function applySegmentEditor() {
    updateRoadSegmentStats();
    updateOverlaySegmentColors();
    updateRoadEditorSegmentColors();
    roadMaskToast('Segment editor marks applied to Pane 3 state.', 1800);
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
    roadGraphData = graph;
    roadEditorViewBox = null;
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

        // Tag each edge path with data-edge-id for click selection and add
        // transparent hit clones for the small toolbar preview.
        edges.forEach(edge => {
          const path = svgEl.querySelector(`[id="${edge.id}"]`);
          if (path) {
            path.classList.add('road-small-edge-visible');
            path.setAttribute('data-edge-id', edge.id);
            path.style.cursor = 'pointer';

            const hit = path.cloneNode(false);
            hit.removeAttribute('id');
            hit.removeAttribute('style');
            hit.setAttribute('class', 'road-small-edge-hit');
            hit.setAttribute('data-edge-id', edge.id);
            hit.setAttribute('stroke', 'transparent');
            hit.setAttribute('stroke-width', '5');
            hit.setAttribute('fill', 'none');
            hit.setAttribute('stroke-linecap', 'round');
            hit.setAttribute('stroke-linejoin', 'round');
            hit.setAttribute('pointer-events', 'stroke');
            hit.setAttribute('vector-effect', 'non-scaling-stroke');
            hit.style.cursor = 'pointer';
            path.parentNode.insertBefore(hit, path.nextSibling);
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
    roadGraphData = null;
    roadEditorViewBox = null;
    closeSegmentEditor();
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

    // Phase C — large road segment editor overlay
    openSegmentEditor,
    closeSegmentEditor,
    renderSegmentEditor,
    setSegmentEditorMode,
    setRoadEditorViewBox,
    resetRoadEditorView,
    zoomRoadEditor,
    applySegmentEditor,
    selectRoadSegmentFromEditor,
  };
})();
