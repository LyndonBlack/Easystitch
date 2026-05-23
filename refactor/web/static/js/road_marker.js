/**
 * EasyStitch — Road Marker (Stage 0A)
 *
 * Debug overlay renderer for the satin_v2 road-marking system.
 * When a satin path is selected in Pane 3, this builds the
 * initial graph (sharp corners + endpoints) from the backend
 * and renders it as an SVG overlay.
 */

const RoadMarker = (function() {
  // ── State ──────────────────────────────────────────────────────
  let currentRoadData = null;
  let currentPathId = null;
  let isLoading = false;

  // ── Color helpers ─────────────────────────────────────────────
  const NODE_COLORS = {
    sharp_corner: '#4488ff',
    endpoint: '#ff4444',
    default: '#aaa',
  };

  // Priority edge colours — Stage 0B will expand this
  const EDGE_COLORS = {
    0: '#888888',  // unset
  };

  function edgeColor(priority) {
    return EDGE_COLORS[priority] || '#888888';
  }

  // ── Rendering ─────────────────────────────────────────────────

  /**
   * Render the debug overlay inside a container element.
   * @param {HTMLElement} container — DOM element for the overlay
   * @param {Object} roadData — API response from /api/roads/build_graph
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

    // Add padding
    const padding = 30;
    const viewBoxX = minX - padding;
    const viewBoxY = minY - padding;
    const viewBoxW = Math.max(100, (maxX - minX) + padding * 2);
    const viewBoxH = Math.max(100, (maxY - minY) + padding * 2);

    // Get container dimensions for scaling
    const cw = container.clientWidth || 400;
    const ch = container.clientHeight || 300;

    // Build SVG
    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', `${viewBoxX} ${viewBoxY} ${viewBoxW} ${viewBoxH}`);
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    svg.style.display = 'block';
    svg.style.overflow = 'visible';

    // Draw edges first (below nodes)
    edgeIds.forEach(eid => {
      const edge = edges[eid];
      const startNode = nodes[edge.start_node_id];
      const endNode = nodes[edge.end_node_id];
      if (!startNode || !endNode) return;

      const line = document.createElementNS(svgNS, 'line');
      line.setAttribute('x1', String(startNode.position[0]));
      line.setAttribute('y1', String(startNode.position[1]));
      line.setAttribute('x2', String(endNode.position[0]));
      line.setAttribute('y2', String(endNode.position[1]));
      line.setAttribute('stroke', edgeColor(edge.priority));
      line.setAttribute('stroke-width', '2.5');
      line.setAttribute('stroke-linecap', 'round');
      line.setAttribute('opacity', '0.7');
      svg.appendChild(line);
    });

    // Draw nodes
    nodeIds.forEach(nid => {
      const node = nodes[nid];
      const pos = node.position;
      const color = NODE_COLORS[node.type] || NODE_COLORS.default;

      // Circle
      const circle = document.createElementNS(svgNS, 'circle');
      circle.setAttribute('cx', String(pos[0]));
      circle.setAttribute('cy', String(pos[1]));
      circle.setAttribute('r', node.type === 'endpoint' ? '6' : '4');
      circle.setAttribute('fill', color);
      circle.setAttribute('stroke', '#111');
      circle.setAttribute('stroke-width', '1.2');
      svg.appendChild(circle);

      // Label
      const text = document.createElementNS(svgNS, 'text');
      text.setAttribute('x', String(pos[0] + 10));
      text.setAttribute('y', String(pos[1] - 8));
      text.setAttribute('fill', '#ccc');
      text.setAttribute('font-size', '10');
      text.setAttribute('font-family', 'monospace');
      text.textContent = nid;
      svg.appendChild(text);

      // Type label
      const typeText = document.createElementNS(svgNS, 'text');
      typeText.setAttribute('x', String(pos[0] + 10));
      typeText.setAttribute('y', String(pos[1] + 4));
      typeText.setAttribute('fill', color);
      typeText.setAttribute('font-size', '8');
      typeText.setAttribute('font-family', 'monospace');
      typeText.textContent = node.type;
      svg.appendChild(typeText);
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
      etext.textContent = eid.replace('edge_', 'e');
      svg.appendChild(etext);
    });

    // Legend
    const lg = document.createElementNS(svgNS, 'g');
    lg.setAttribute('transform', `translate(${viewBoxX + 8}, ${viewBoxY + 14})`);
    [
      {label: 'endpoint', color: NODE_COLORS.endpoint},
      {label: 'sharp_corner', color: NODE_COLORS.sharp_corner},
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
  }

  // ── API helpers ────────────────────────────────────────────────

  /**
   * Fetch the road graph from the backend for a given path.
   * @param {string} pathId — the structure object id
   * @returns {Promise<Object|null>} — road data or null on error
   */
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

  /**
   * Show a loading state in the overlay container.
   */
  function showLoading(container, pathId) {
    container.innerHTML = `
      <div style="padding:12px;color:#888;font-size:0.85rem">
        <span class="spinner" style="display:inline-block;width:14px;height:14px;border:2px solid #4488ff;border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite;vertical-align:middle;margin-right:8px"></span>
        Building road graph for ${pathId}&hellip;
      </div>`;
  }

  /**
   * Show an error message in the overlay container.
   */
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

  // ── Public API ────────────────────────────────────────────────

  return {
    /**
     * Build and render the road graph for a selected satin path.
     * @param {string} pathId — structure object id
     */
    async showRoadGraph(pathId) {
      const container = document.getElementById('road-overlay-container');
      if (!container) return;

      if (isLoading) return;
      isLoading = true;
      currentPathId = pathId;

      showLoading(container, pathId);

      const data = await fetchRoadGraph(pathId);
      isLoading = false;

      if (!data) {
        showError(container, 'Failed to fetch road graph');
        currentRoadData = null;
        return;
      }

      if (data.error) {
        showError(container, data.error);
        currentRoadData = null;
        return;
      }

      currentRoadData = data;
      renderDebugOverlay(container, data);
    },

    /**
     * Clear the overlay.
     */
    clear() {
      const container = document.getElementById('road-overlay-container');
      if (container) {
        container.innerHTML = '<span style="color:#555;padding:12px;display:block">Select a SATIN path to view road graph.</span>';
      }
      currentRoadData = null;
      currentPathId = null;
    },

    /**
     * Get current road data.
     */
    getData() {
      return currentRoadData;
    },

    /**
     * Render overlay directly (for reuse).
     */
    renderDebugOverlay,
  };
})();
