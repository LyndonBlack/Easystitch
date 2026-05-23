/**
 * EasyStitch — Road Marker (Stage 0B)
 *
 * Debug overlay renderer + manual road-marking tools.
 * When a satin path is selected in Pane 3, this builds the
 * initial graph from /api/roads/build_graph and renders it.
 * The toolbar allows manual priority assignment, splitting,
 * yield rungs, merging, and reordering.
 */

const RoadMarker = (function() {
  // ── State ──────────────────────────────────────────────────────
  let currentRoadData = null;
  let currentPathId = null;
  let isLoading = false;

  // Tool state
  let currentTool = 'select';
  let selectedEdgeId = null;       // for merge tool first click
  let yieldFirstEdgeId = null;     // for yield tool first click

  // ── Color helpers ─────────────────────────────────────────────
  const NODE_COLORS = {
    sharp_corner: '#4488ff',
    endpoint: '#ff4444',
    split_node: '#44ccaa',
    default: '#aaa',
  };

  const EDGE_COLORS = {
    0: '#4488ff',   // primary (blue)
    1: '#44cc44',   // secondary (green)
    2: '#ff8844',   // tertiary (orange)
    undefined: '#888888', // unset (grey)
  };

  function edgeColor(priority) {
    return EDGE_COLORS[priority] !== undefined ? EDGE_COLORS[priority] : EDGE_COLORS[undefined];
  }

  function priorityLabel(priority) {
    if (priority === 0) return 'primary';
    if (priority === 1) return 'secondary';
    if (priority === 2) return 'tertiary';
    return 'unset';
  }

  // ── Toolbar wiring ───────────────────────────────────────────

  function initToolbar() {
    const toolbar = document.getElementById('road-toolbar');
    if (!toolbar) return;

    // Remove old listeners by cloning
    toolbar.querySelectorAll('.road-tool-btn').forEach(btn => {
      btn.addEventListener('click', function() {
        const tool = this.getAttribute('data-tool');
        setTool(tool);
      });
    });
  }

  function setTool(tool) {
    currentTool = tool;
    selectedEdgeId = null;
    yieldFirstEdgeId = null;
    clearAllHighlights();

    // Update button active states
    document.querySelectorAll('.road-tool-btn').forEach(btn => {
      btn.classList.toggle('active', btn.getAttribute('data-tool') === tool);
    });

    // Update cursor on overlay container
    const container = document.getElementById('road-overlay-container');
    if (container) {
      container.querySelectorAll('svg').forEach(svg => {
        updateCursor(svg);
      });
    }

    // Show status
    setStatus(tool === 'select' ? 'Select mode' :
              tool === 'mark-primary' ? 'Mark Primary: click an edge' :
              tool === 'mark-secondary' ? 'Mark Secondary: click an edge' :
              tool === 'mark-tertiary' ? 'Mark Tertiary: click an edge' :
              tool === 'split' ? 'Split: click on an edge' :
              tool === 'yield' ? 'Yield: click first edge' :
              tool === 'promote' ? 'Cycle: click edge to cycle priority' :
              tool === 'merge' ? 'Merge: click first edge' :
              tool === 'reorder' ? 'Reorder: drag edges in panel below' : '');

    // Show/hide stitch order panel for reorder tool
    const panel = document.getElementById('stitch-order-panel');
    if (panel) panel.style.display = (tool === 'reorder') ? 'block' : 'none';

    // If reorder mode, refresh the stitch order list
    if (tool === 'reorder') rebuildStitchOrderPanel();
  }

  function setStatus(msg) {
    let el = document.getElementById('road-overlay-status');
    if (!el) {
      el = document.createElement('div');
      el.id = 'road-overlay-status';
      el.className = 'road-overlay-status';
      const container = document.getElementById('road-overlay-container');
      if (container) container.appendChild(el);
    }
    el.textContent = msg;
  }

  function updateCursor(svgEl) {
    if (!svgEl) return;
    if (currentTool === 'split' || currentTool === 'yield') {
      svgEl.style.cursor = 'crosshair';
    } else if (currentTool.startsWith('mark-') || currentTool === 'promote') {
      svgEl.style.cursor = 'default';
    } else if (currentTool === 'merge') {
      svgEl.style.cursor = 'pointer';
    } else {
      svgEl.style.cursor = 'default';
    }
  }

  // ── Click handling on SVG ─────────────────────────────────────

  function attachClickHandlers(svgEl) {
    if (!svgEl) return;

    // Remove old listeners via clone
    const newSvg = svgEl.cloneNode(true);
    svgEl.parentNode.replaceChild(newSvg, svgEl);
    updateCursor(newSvg);

    // Edge click detection: edges + invisible hit areas
    newSvg.querySelectorAll('[data-edge-id]').forEach(el => {
      el.addEventListener('click', function(e) {
        e.stopPropagation();
        const edgeId = this.getAttribute('data-edge-id');
        handleEdgeClick(edgeId, e);
      });
    });

    // Node click detection
    newSvg.querySelectorAll('[data-node-id]').forEach(el => {
      el.addEventListener('click', function(e) {
        e.stopPropagation();
        const nodeId = this.getAttribute('data-node-id');
        handleNodeClick(nodeId, e);
      });
    });

    // Background click: deselect
    newSvg.addEventListener('click', function(e) {
      clearAllHighlights();
      if (currentTool === 'yield') yieldFirstEdgeId = null;
      if (currentTool === 'merge') selectedEdgeId = null;
      setStatus(currentTool === 'yield' ? 'Yield: click first edge' :
                 currentTool === 'merge' ? 'Merge: click first edge' : '');
    });

    return newSvg;
  }

  async function handleEdgeClick(edgeId, event) {
    if (!currentPathId) {
      showToast('No path loaded. Select a SATIN path first.', 'error');
      return;
    }

    const edges = currentRoadData ? currentRoadData.edges : null;
    if (!edges || !edges[edgeId]) return;

    highlightEdge(edgeId);

    switch (currentTool) {
      case 'select':
        // Just highlight
        setStatus('Selected edge: ' + edgeId);
        break;

      case 'mark-primary':
        await apiSetPriority(edgeId, 0);
        break;

      case 'mark-secondary':
        await apiSetPriority(edgeId, 1);
        break;

      case 'mark-tertiary':
        await apiSetPriority(edgeId, 2);
        break;

      case 'split':
        await apiPlaceSplit(edgeId, event);
        break;

      case 'yield':
        if (!yieldFirstEdgeId) {
          yieldFirstEdgeId = edgeId;
          setStatus('Yield: first edge selected (' + edgeId + '). Click second edge.');
          highlightEdge(edgeId, '#ff88ff');
        } else {
          const firstId = yieldFirstEdgeId;
          yieldFirstEdgeId = null;
          setStatus('Yield: placing rung between ' + firstId + ' and ' + edgeId + '...');
          await apiPlaceYield(firstId, edgeId, event);
        }
        break;

      case 'promote':
        await apiCyclePriority(edgeId);
        break;

      case 'merge':
        if (!selectedEdgeId) {
          selectedEdgeId = edgeId;
          setStatus('Merge: first edge selected (' + edgeId + '). Click adjacent edge.');
          highlightEdge(edgeId, '#ffcc44');
        } else if (selectedEdgeId === edgeId) {
          // Deselect
          selectedEdgeId = null;
          setStatus('Merge: click first edge');
          clearAllHighlights();
        } else {
          const firstId = selectedEdgeId;
          selectedEdgeId = null;
          await apiMergeEdges(firstId, edgeId);
        }
        break;

      case 'reorder':
        setStatus('Reorder: use the panel below to drag edges');
        break;
    }
  }

  function handleNodeClick(nodeId, event) {
    if (!currentRoadData) return;
    const nodes = currentRoadData.nodes;
    if (!nodes || !nodes[nodeId]) return;

    highlightNode(nodeId);

    if (currentTool === 'select') {
      setStatus('Selected node: ' + nodeId + ' (' + (nodes[nodeId].type || '?') + ')');
    }
  }

  // ── Highlighting ──────────────────────────────────────────────

  function clearAllHighlights() {
    const container = document.getElementById('road-overlay-container');
    if (!container) return;
    const svg = container.querySelector('svg');
    if (!svg) return;

    svg.querySelectorAll('.highlighted-edge').forEach(el => {
      el.setAttribute('stroke-width', '2.5');
      el.setAttribute('opacity', '0.7');
      el.classList.remove('highlighted-edge');
    });
    svg.querySelectorAll('.highlighted-node').forEach(el => {
      el.setAttribute('stroke-width', '1.2');
      el.classList.remove('highlighted-node');
    });
  }

  function highlightEdge(edgeId, color) {
    clearAllHighlights();
    const container = document.getElementById('road-overlay-container');
    if (!container) return;
    const svg = container.querySelector('svg');
    if (!svg) return;

    const el = svg.querySelector('[data-edge-id="' + edgeId + '"]');
    if (!el) return;

    el.setAttribute('stroke-width', '5');
    el.setAttribute('opacity', '1');
    if (color) el.setAttribute('stroke', color);
    el.classList.add('highlighted-edge');
  }

  function highlightNode(nodeId) {
    clearAllHighlights();
    const container = document.getElementById('road-overlay-container');
    if (!container) return;
    const svg = container.querySelector('svg');
    if (!svg) return;

    const el = svg.querySelector('[data-node-id="' + nodeId + '"]');
    if (!el) return;

    el.setAttribute('stroke-width', '3');
    el.setAttribute('stroke', '#fff');
    el.classList.add('highlighted-node');
  }

  // ── API calls ────────────────────────────────────────────────

  async function apiCall(endpoint, body) {
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const data = await res.json();
      return data;
    } catch (e) {
      console.error('API error:', endpoint, e);
      return {error: 'Network error: ' + e.message};
    }
  }

  async function apiSetPriority(edgeId, priority) {
    setStatus('Setting priority ' + priorityLabel(priority) + ' on ' + edgeId + '...');
    const result = await apiCall('/api/roads/set_priority', {
      path_id: currentPathId,
      edge_id: edgeId,
      priority: priority,
    });

    if (result.error) {
      showToast('Error: ' + result.error, 'error');
      setStatus('Error: ' + result.error);
      return;
    }

    // Incremental update: update local data and re-render
    if (currentRoadData && currentRoadData.edges && currentRoadData.edges[edgeId]) {
      currentRoadData.edges[edgeId].priority = priority;
    }
    refreshOverlay();
    showToast('Set ' + edgeId + ' to ' + priorityLabel(priority), 'success');
    setStatus('Priority updated: ' + priorityLabel(priority));
  }

  async function apiCyclePriority(edgeId) {
    if (!currentRoadData || !currentRoadData.edges || !currentRoadData.edges[edgeId]) return;

    const currentPriority = currentRoadData.edges[edgeId].priority;
    const nextPriority = (typeof currentPriority === 'number') ? (currentPriority + 1) % 3 : 0;
    await apiSetPriority(edgeId, nextPriority);
  }

  async function apiPlaceSplit(edgeId, event) {
    setStatus('Placing split on ' + edgeId + '...');

    // Get click position relative to SVG
    const svgEl = event.target.closest('svg');
    if (!svgEl) return;

    const pt = svgEl.createSVGPoint();
    pt.x = event.clientX;
    pt.y = event.clientY;
    const svgPt = pt.matrixTransform(svgEl.getScreenCTM().inverse());

    const result = await apiCall('/api/roads/place_split', {
      path_id: currentPathId,
      edge_id: edgeId,
      x: svgPt.x,
      y: svgPt.y,
    });

    if (result.error) {
      showToast('Split error: ' + result.error, 'error');
      setStatus('Split error: ' + result.error);
      return;
    }

    // Full refresh to get new nodes/edges
    currentRoadData = result;
    refreshOverlay();
    showToast('Split placed on ' + edgeId, 'success');
    setStatus('Split complete');
  }

  async function apiPlaceYield(edgeId1, edgeId2, event) {
    const svgEl = overlayEl.querySelector('svg');
    const pt = svgEl.createSVGPoint();
    pt.x = event.clientX;
    pt.y = event.clientY;
    const svgPt = pt.matrixTransform(svgEl.getScreenCTM().inverse());
    setStatus('Placing yield rung...');
    const result = await apiCall('/api/roads/place_yield', {
      path_id: currentPathId,
      primary_edge_id: edgeId1,
      secondary_edge_id: edgeId2,
      x: svgPt.x,
      y: svgPt.y,
    });

    if (result.error) {
      showToast('Yield error: ' + result.error, 'error');
      setStatus('Yield error: ' + result.error);
      yieldFirstEdgeId = null;
      return;
    }

    // Full refresh
    currentRoadData = result;
    refreshOverlay();
    showToast('Yield rung placed', 'success');
    setStatus('Yield complete');
    yieldFirstEdgeId = null;
  }

  async function apiMergeEdges(edgeId1, edgeId2) {
    setStatus('Merging ' + edgeId1 + ' + ' + edgeId2 + '...');
    const result = await apiCall('/api/roads/merge_edges', {
      path_id: currentPathId,
      edge_a_id: edgeId1,
      edge_b_id: edgeId2,
    });

    if (result.error) {
      showToast('Merge error: ' + result.error, 'error');
      setStatus('Merge error: ' + result.error);
      selectedEdgeId = null;
      return;
    }

    currentRoadData = result;
    refreshOverlay();
    showToast('Edges merged', 'success');
    setStatus('Merge complete');
    selectedEdgeId = null;
  }

  async function apiReorderEdges(orderedEdgeIds) {
    if (!currentPathId) return;
    setStatus('Reordering...');
    const result = await apiCall('/api/roads/reorder', {
      path_id: currentPathId,
      stitch_order: orderedEdgeIds,
    });

    if (result.error) {
      showToast('Reorder error: ' + result.error, 'error');
      setStatus('Reorder error: ' + result.error);
      return;
    }

    currentRoadData = result;
    refreshOverlay();
    rebuildStitchOrderPanel();
    showToast('Stitch order updated', 'success');
    setStatus('Reorder complete');
  }

  // ── Refresh overlay ──────────────────────────────────────────

  function refreshOverlay() {
    const container = document.getElementById('road-overlay-container');
    if (!container || !currentRoadData) return;
    renderDebugOverlay(container, currentRoadData);
    if (currentTool === 'reorder') rebuildStitchOrderPanel();
  }

  // ── Toast helper ─────────────────────────────────────────────

  function showToast(msg, type) {
    const toast = document.getElementById('toast');
    if (!toast) return;

    if (type === 'error') {
      toast.style.background = '#a33';
    } else {
      toast.style.background = '#2d7a52';
    }
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(function() {
      toast.classList.remove('show');
    }, 2000);
  }

  // ── Rendering ─────────────────────────────────────────────────

  /**
   * Render the debug overlay inside a container element.
   */
  function renderDebugOverlay(container, roadData) {
    if (!container || !roadData) return;

    // Clear container
    container.innerHTML = '';

    const nodes = roadData.nodes || {};
    const edges = roadData.edges || {};
    const nodeIds = Object.keys(nodes);
    const edgeIds = Object.keys(edges);

    if (nodeIds.length === 0) {
      container.innerHTML = '<span style="color:#888;padding:12px;display:block">No nodes found in road graph.</span>';
      return;
    }

    // Gather all positions to compute viewBox
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodeIds.forEach(nid => {
      const pos = nodes[nid].position;
      minX = Math.min(minX, pos[0]);
      minY = Math.min(minY, pos[1]);
      maxX = Math.max(maxX, pos[0]);
      maxY = Math.max(maxY, pos[1]);
    });

    // Compute raw bbox dimensions (before padding)
    const rawW = maxX - minX;
    const rawH = maxY - minY;

    // Minimum viewBox size to prevent extreme zoom on small polygons
    const MIN_VIEW = 200;
    const BASE_PAD = 30;

    let padX = BASE_PAD;
    let padY = BASE_PAD;
    if (rawW < MIN_VIEW) padX = (MIN_VIEW - rawW) / 2;
    if (rawH < MIN_VIEW) padY = (MIN_VIEW - rawH) / 2;
    // Always keep at least the base padding
    padX = Math.max(padX, BASE_PAD);
    padY = Math.max(padY, BASE_PAD);

    const viewBoxX = minX - padX;
    const viewBoxY = minY - padY;
    const viewBoxW = rawW + padX * 2;
    const viewBoxH = rawH + padY * 2;

    // Build SVG — reuse existing SVG element if container IS an SVG
    const svgNS = 'http://www.w3.org/2000/svg';
    let svg;
    if (container.tagName === 'svg' || container.tagName === 'SVG') {
      svg = container;
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      svg.setAttribute('viewBox', `${viewBoxX} ${viewBoxY} ${viewBoxW} ${viewBoxH}`);
      svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    } else {
      container.innerHTML = '';
      svg = document.createElementNS(svgNS, 'svg');
      svg.setAttribute('viewBox', `${viewBoxX} ${viewBoxY} ${viewBoxW} ${viewBoxH}`);
      svg.setAttribute('width', '100%');
      svg.setAttribute('height', '100%');
      svg.style.display = 'block';
      svg.style.overflow = 'visible';
      container.appendChild(svg);
    }

    // Draw polygon boundary (faint, behind everything) so user can see the actual shape
    if (roadData.boundary_coords && roadData.boundary_coords.length > 0) {
      const polyline = document.createElementNS(svgNS, 'polyline');
      const pts = roadData.boundary_coords.map(c => `${c[0]},${c[1]}`).join(' ');
      polyline.setAttribute('points', pts);
      polyline.setAttribute('fill', 'none');
      polyline.setAttribute('stroke', '#334466');
      polyline.setAttribute('stroke-width', '1');
      polyline.setAttribute('opacity', '0.5');
      svg.appendChild(polyline);
    }

    // Draw edges first (below nodes)
    // Each edge gets TWO elements: a thick invisible hit area + visible line
    edgeIds.forEach(eid => {
      const edge = edges[eid];
      const startNode = nodes[edge.start_node_id];
      const endNode = nodes[edge.end_node_id];
      if (!startNode || !endNode) return;

      const color = edgeColor(edge.priority);

      // Invisible wide hit area for easy clicking
      const hitLine = document.createElementNS(svgNS, 'line');
      hitLine.setAttribute('x1', String(startNode.position[0]));
      hitLine.setAttribute('y1', String(startNode.position[1]));
      hitLine.setAttribute('x2', String(endNode.position[0]));
      hitLine.setAttribute('y2', String(endNode.position[1]));
      hitLine.setAttribute('stroke', 'transparent');
      hitLine.setAttribute('stroke-width', '14');
      hitLine.setAttribute('stroke-linecap', 'round');
      hitLine.setAttribute('data-edge-id', eid);
      hitLine.style.cursor = 'pointer';
      svg.appendChild(hitLine);

      // Visible line
      const line = document.createElementNS(svgNS, 'line');
      line.setAttribute('x1', String(startNode.position[0]));
      line.setAttribute('y1', String(startNode.position[1]));
      line.setAttribute('x2', String(endNode.position[0]));
      line.setAttribute('y2', String(endNode.position[1]));
      line.setAttribute('stroke', color);
      line.setAttribute('stroke-width', '2.5');
      line.setAttribute('stroke-linecap', 'round');
      line.setAttribute('opacity', '0.7');
      line.setAttribute('data-edge-id', eid);
      line.setAttribute('pointer-events', 'none'); // clicks go through to hit area
      svg.appendChild(line);
    });

    // Draw nodes
    nodeIds.forEach(nid => {
      const node = nodes[nid];
      const pos = node.position;
      const color = NODE_COLORS[node.type] || NODE_COLORS.default;

      // Group for click target
      const g = document.createElementNS(svgNS, 'g');
      g.setAttribute('data-node-id', nid);
      g.style.cursor = 'pointer';

      // Invisible hit area
      const hitCircle = document.createElementNS(svgNS, 'circle');
      hitCircle.setAttribute('cx', String(pos[0]));
      hitCircle.setAttribute('cy', String(pos[1]));
      hitCircle.setAttribute('r', '12');
      hitCircle.setAttribute('fill', 'transparent');
      hitCircle.setAttribute('stroke', 'transparent');
      g.appendChild(hitCircle);

      // Visible circle
      const circle = document.createElementNS(svgNS, 'circle');
      circle.setAttribute('cx', String(pos[0]));
      circle.setAttribute('cy', String(pos[1]));
      circle.setAttribute('r', node.type === 'endpoint' ? '6' : '4');
      circle.setAttribute('fill', color);
      circle.setAttribute('stroke', '#111');
      circle.setAttribute('stroke-width', '1.2');
      circle.setAttribute('pointer-events', 'none');
      g.appendChild(circle);

      // Label
      const text = document.createElementNS(svgNS, 'text');
      text.setAttribute('x', String(pos[0] + 10));
      text.setAttribute('y', String(pos[1] - 8));
      text.setAttribute('fill', '#ccc');
      text.setAttribute('font-size', '10');
      text.setAttribute('font-family', 'monospace');
      text.setAttribute('pointer-events', 'none');
      text.textContent = nid;
      g.appendChild(text);

      // Type label
      const typeText = document.createElementNS(svgNS, 'text');
      typeText.setAttribute('x', String(pos[0] + 10));
      typeText.setAttribute('y', String(pos[1] + 4));
      typeText.setAttribute('fill', color);
      typeText.setAttribute('font-size', '8');
      typeText.setAttribute('font-family', 'monospace');
      typeText.setAttribute('pointer-events', 'none');
      typeText.textContent = node.type;
      g.appendChild(typeText);

      svg.appendChild(g);
    });

    // Edge labels at midpoints
    edgeIds.forEach(eid => {
      const edge = edges[eid];
      const startNode = nodes[edge.start_node_id];
      const endNode = nodes[edge.end_node_id];
      if (!startNode || !endNode) return;

      const mx = (startNode.position[0] + endNode.position[0]) / 2;
      const my = (startNode.position[1] + endNode.position[1]) / 2;

      const etext = document.createElementNS(svgNS, 'text');
      etext.setAttribute('x', String(mx));
      etext.setAttribute('y', String(my - 5));
      etext.setAttribute('fill', '#889');
      etext.setAttribute('font-size', '8');
      etext.setAttribute('font-family', 'monospace');
      etext.setAttribute('text-anchor', 'middle');
      etext.setAttribute('pointer-events', 'none');
      etext.textContent = eid.replace('edge_', 'e');
      svg.appendChild(etext);
    });

    // Legend
    const lg = document.createElementNS(svgNS, 'g');
    lg.setAttribute('transform', `translate(${viewBoxX + 8}, ${viewBoxY + 14})`);
    [
      {label: 'endpoint', color: NODE_COLORS.endpoint},
      {label: 'sharp_corner', color: NODE_COLORS.sharp_corner},
      {label: 'pri 0', color: EDGE_COLORS[0]},
      {label: 'pri 1', color: EDGE_COLORS[1]},
      {label: 'pri 2', color: EDGE_COLORS[2]},
    ].forEach((item, i) => {
      const g = document.createElementNS(svgNS, 'g');
      const circ = document.createElementNS(svgNS, 'circle');
      circ.setAttribute('cx', '6');
      circ.setAttribute('cy', String(i * 14));
      circ.setAttribute('r', '4');
      circ.setAttribute('fill', item.color);
      circ.setAttribute('stroke', '#111');
      circ.setAttribute('stroke-width', '0.8');
      g.appendChild(circ);
      const lt = document.createElementNS(svgNS, 'text');
      lt.setAttribute('x', '14');
      lt.setAttribute('y', String(i * 14 + 4));
      lt.setAttribute('fill', '#aaa');
      lt.setAttribute('font-size', '9');
      lt.setAttribute('font-family', 'monospace');
      lt.textContent = item.label;
      g.appendChild(lt);
      lg.appendChild(g);
    });
    svg.appendChild(lg);

    container.appendChild(svg);

    // Ring polygon info note
    let ringNote = container.querySelector('.ring-polygon-note');
    if (roadData.has_holes) {
      if (!ringNote) {
        ringNote = document.createElement('div');
        ringNote.className = 'ring-polygon-note';
        container.appendChild(ringNote);
      }
      ringNote.innerHTML = '&#9432; Ring polygon detected &mdash; use <b>Split</b> tool to separate branches (e.g., ears, limbs).';
      ringNote.style.cssText = 'padding:6px 10px;margin-top:6px;background:#2a2a3a;color:#aac;font-size:0.78rem;' +
        'border-left:3px solid #4488ff;border-radius:2px;';
    } else {
      if (ringNote) ringNote.remove();
    }

    // Attach click handlers after rendering
    attachClickHandlers(svg);

    // Restore status element
    setStatus(currentTool === 'select' ? 'Select mode' :
              currentTool === 'mark-primary' ? 'Mark Primary: click an edge' :
              currentTool === 'mark-secondary' ? 'Mark Secondary: click an edge' :
              currentTool === 'mark-tertiary' ? 'Mark Tertiary: click an edge' :
              currentTool === 'split' ? 'Split: click on an edge' :
              currentTool === 'yield' ? 'Yield: click first edge' :
              currentTool === 'promote' ? 'Cycle: click edge to cycle priority' :
              currentTool === 'merge' ? 'Merge: click first edge' :
              currentTool === 'reorder' ? 'Reorder: drag edges in panel below' : '');
  }

  // ── Stitch order panel ────────────────────────────────────────

  function rebuildStitchOrderPanel() {
    const listEl = document.getElementById('stitch-order-list');
    if (!listEl || !currentRoadData) return;

    const edges = currentRoadData.edges || {};
    const edgeIds = Object.keys(edges);

    if (edgeIds.length === 0) {
      listEl.innerHTML = '<div style="padding:6px;color:#666">No edges</div>';
      return;
    }

    // Sort by some order field if available, else by ID
    const sortedEdgeIds = [...edgeIds].sort((a, b) => {
      const oa = edges[a].stitch_order !== undefined ? edges[a].stitch_order : parseInt(a.replace(/\D/g, '0'), 10);
      const ob = edges[b].stitch_order !== undefined ? edges[b].stitch_order : parseInt(b.replace(/\D/g, '0'), 10);
      return oa - ob;
    });

    listEl.innerHTML = '';
    sortedEdgeIds.forEach((eid, idx) => {
      const edge = edges[eid];
      const priority = edge.priority;
      const div = document.createElement('div');
      div.className = 'stitch-order-item';
      div.setAttribute('data-edge-id', eid);
      div.setAttribute('draggable', 'true');
      div.style.cursor = 'grab';

      div.innerHTML = '<span class="so-priority" style="background:' + edgeColor(priority) + '" title="' + priorityLabel(priority) + '"></span>' +
                      '<span style="flex:1">' + eid + '</span>' +
                      '<span style="color:' + edgeColor(priority) + '">' + priorityLabel(priority) + '</span>';

      // Drag and drop handlers
      div.addEventListener('dragstart', function(e) {
        this.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', eid);
        setTimeout(() => { if (this.classList.contains('dragging')) this.style.display = 'none'; }, 0);
      });

      div.addEventListener('dragend', function(e) {
        this.classList.remove('dragging');
        this.style.display = '';
        listEl.querySelectorAll('.stitch-order-item').forEach(item => item.classList.remove('drag-over'));
      });

      div.addEventListener('dragover', function(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        this.classList.add('drag-over');
      });

      div.addEventListener('dragleave', function(e) {
        this.classList.remove('drag-over');
      });

      div.addEventListener('drop', async function(e) {
        e.preventDefault();
        this.classList.remove('drag-over');

        const draggedId = e.dataTransfer.getData('text/plain');
        const targetId = this.getAttribute('data-edge-id');

        if (draggedId === targetId) return;

        // Rebuild order from the DOM
        const items = Array.from(listEl.querySelectorAll('.stitch-order-item'));
        const newOrder = items.map(el => el.getAttribute('data-edge-id'));

        // Remove the dragged one from its old position and insert at target
        const draggedIndex = newOrder.indexOf(draggedId);
        const targetIndex = newOrder.indexOf(targetId);
        if (draggedIndex >= 0) {
          newOrder.splice(draggedIndex, 1);
          newOrder.splice(targetIndex, 0, draggedId);
        }

        await apiReorderEdges(newOrder);
      });

      listEl.appendChild(div);
    });
  }

  // ── API helpers ────────────────────────────────────────────────

  async function fetchRoadGraph(pathId) {
    try {
      const res = await fetch('/api/roads/build_graph', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path_id: pathId}),
      });
      const data = await res.json();
      if (!data.nodes && !data.ok && data.error) {
        console.error('Road graph error:', data.error);
        return null;
      }
      return data;
    } catch (e) {
      console.error('Road graph fetch error:', e);
      return null;
    }
  }

  function showLoading(container, pathId) {
    container.innerHTML = `
      <div style="padding:12px;color:#888;font-size:0.85rem">
        <span class="spinner" style="display:inline-block;width:14px;height:14px;border:2px solid #4488ff;border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite;vertical-align:middle;margin-right:8px"></span>
        Building road graph for ${escapeHtml(pathId)}&hellip;
      </div>`;
  }

  function showError(container, message) {
    container.innerHTML = `
      <div style="padding:12px;color:#ff6666;font-size:0.85rem">
        Road graph error: ${escapeHtml(message)}
      </div>`;
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Initialization ────────────────────────────────────────────

  function init() {
    initToolbar();
    setTool('select');
  }

  // Auto-init on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // ── Public API ────────────────────────────────────────────────

  function getOverlaySvg() {
    return document.getElementById('road-graph-modal-svg');
  }

  function getOverlayDiv() {
    return document.getElementById('road-graph-modal');
  }

  return {
    async showRoadGraph(pathId) {
      if (isLoading) return;
      isLoading = true;
      currentPathId = pathId;

      const data = await fetchRoadGraph(pathId);
      isLoading = false;

      if (!data || data.error) {
        currentRoadData = null;
        console.warn('Road graph fetch failed:', data?.error);
        return;
      }

      currentRoadData = data;
      // Render but don't auto-show — user clicks toggle button
      renderDebugOverlay(getOverlaySvg(), data);
    },

    toggleOverlay() {
      const overlay = getOverlayDiv();
      const btn = document.getElementById('road-graph-toggle-btn');
      if (!overlay || !btn) return;

      const showing = overlay.style.display === 'flex';
      if (showing) {
        overlay.style.display = 'none';
        btn.textContent = 'Show Road Graph';
        btn.style.background = '';
      } else {
        // Re-render if we have data (may have been fetched already by path selection)
        const overlaySvg = getOverlaySvg();
        if (currentRoadData && overlaySvg) {
          renderDebugOverlay(overlaySvg, currentRoadData);
        } else if (!currentRoadData && overlaySvg) {
          // No data yet — show a message
          overlaySvg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#666" font-size="18">Select a SATIN path first, then click Show Road Graph</text>';
        }
        overlay.style.display = 'flex';
        btn.textContent = 'Hide Road Graph';
        btn.style.background = '#1a3a5c';
      }
    },

    clear() {
      const overlay = getOverlayDiv();
      if (overlay) overlay.style.display = 'none';
      const btn = document.getElementById('road-graph-toggle-btn');
      if (btn) { btn.textContent = 'Show Road Graph'; btn.style.background = ''; }
      const overlaySvg = getOverlaySvg();
      if (overlaySvg) overlaySvg.innerHTML = '';
      const panel = document.getElementById('stitch-order-panel');
      if (panel) panel.style.display = 'none';
      currentRoadData = null;
      currentPathId = null;
      selectedEdgeId = null;
      yieldFirstEdgeId = null;
    },

    getData() {
      return currentRoadData;
    },

    renderDebugOverlay,

    // Expose tool control
    setTool: setTool,
    getTool: function() { return currentTool; },
  };
})();
