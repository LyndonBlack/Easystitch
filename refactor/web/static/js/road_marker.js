/**
 * EasyStitch — Satin V2 Road Marker checklist pipeline.
 *
 * Frontend scope:
 * - collect current Pane 3 objects/assignments
 * - call /api/roads/mask_only — display mask stats and preview
 * - call /api/roads/centerline — display graph stats and overlay SVG
 * - clear overlay display
 */

const RoadMarker = (function() {
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

  function renderCenterlineGraph(data) {
    const stats = document.getElementById('road-graph-stats');
    const overlay = document.getElementById('road-graph-overlay');
    if (!stats) return;

    if (!data || !data.ok) {
      stats.innerHTML = 'Centerline build failed: <b>' + (data?.error || 'unknown') + '</b>';
      if (overlay) overlay.innerHTML = '<span style="color:#933">No graph generated</span>';
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
      // Scale overlay to fit the container
      const svgEl = overlay.querySelector('svg');
      if (svgEl) {
        svgEl.style.maxWidth = '100%';
        svgEl.style.maxHeight = '100%';
        svgEl.style.width = 'auto';
        svgEl.style.height = 'auto';
        svgEl.removeAttribute('width');
        svgEl.removeAttribute('height');
      }
    }
  }

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
  }

  return {
    collectPane3RoadGraphPayload,
    buildSatinMaskOnly,
    renderSatinMaskPreview,
    buildCenterlineGraph,
    renderCenterlineGraph,
    clearOverlay,
  };
})();
