#!/usr/bin/env python3
"""
EasyStitch Core — Road-marking graph (Stage 0A backend).

Data model for the road-marking system:
  - Rung:   a crossbar between two boundary points.
  - Node:   a decision point on the polygon boundary.
  - Edge:   a boundary segment connecting two nodes.
  - RoadMarkedPath: top-level container that ties a polygon to its
    road-marking graph.

Initial graph builder (Stage 0A):
  Detects sharp corners on the polygon exterior, uses the sharpest
  corner as the circuit-break for closed loops, and produces nodes +
  edges with a clockwise stitch-order.  No junction / narrow-waist
  detection is performed here.
"""

import math
import uuid
from dataclasses import dataclass, field
from typing import Optional

from shapely.geometry import Polygon, LineString, Point


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Rung:
    """A crossbar (guide-rung) between two points on the boundary."""
    id: str
    p1: tuple   # (x, y)
    p2: tuple   # (x, y)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "p1": list(self.p1),
            "p2": list(self.p2),
        }


@dataclass
class Node:
    """A decision / stopping point on the polygon boundary."""
    id: str
    type: str          # "sharp_corner" | "user_cut" | "manual_yield" | "endpoint"
    position: tuple    # (x, y)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "position": list(self.position),
        }


@dataclass
class Edge:
    """A directed boundary segment between two adjacent nodes."""
    id: str
    priority: int = 0
    start_node_id: str = ""
    end_node_id: str = ""
    start_rung_id: Optional[str] = None
    end_rung_id: Optional[str] = None
    yield_to_edge_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "priority": self.priority,
            "start_node_id": self.start_node_id,
            "end_node_id": self.end_node_id,
            "start_rung_id": self.start_rung_id,
            "end_rung_id": self.end_rung_id,
            "yield_to_edge_id": self.yield_to_edge_id,
        }


@dataclass
class RoadMarkedPath:
    """Top-level container: a polygon with its road-marking graph attached."""
    path_id: str
    rungs: dict        # id -> Rung
    nodes: dict        # id -> Node
    edges: dict        # id -> Edge
    stitch_order: list  # list of edge ids, clockwise traversal
    boundary_coords: list = field(default_factory=list)  # [[x,y],...] for overlay rendering

    def to_dict(self) -> dict:
        return {
            "path_id": self.path_id,
            "rungs": {k: v.to_dict() for k, v in self.rungs.items()},
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": {k: v.to_dict() for k, v in self.edges.items()},
            "stitch_order": self.stitch_order,
            "boundary_coords": self.boundary_coords,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Graph builder (Stage 0A)
# ─────────────────────────────────────────────────────────────────────────────

def _is_clockwise(coords: list) -> bool:
    """Return True if the ring vertices are in clockwise order (signed area > 0)."""
    area = 0.0
    n = len(coords)
    for i in range(n):
        x1, y1 = coords[i]
        x2, y2 = coords[(i + 1) % n]
        area += (x2 - x1) * (y2 + y1)
    return area > 0


def _process_boundary(bound_poly: Polygon, coords: list, node_offset: int,
                     edge_offset: int) -> tuple[dict, dict, list, list]:
    """
    Detect sharp corners on a single boundary and build its sub-graph
    (nodes, edges).  Returns the three dicts + ordered node ids list.
    Uses globally-scoped offsets so IDs are unique across boundaries.
    """
    from .geometry import detect_sharp_corners

    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    n = len(coords)
    if n < 3:
        return {}, {}, [], list(coords)

    raw_corners = detect_sharp_corners(bound_poly, angle_threshold_deg=150.0,
                                       spatial_radius_px=12.0)

    if not raw_corners:
        # No corners: create two synthetic nodes at most-distant vertices
        best_dist = -1.0
        best_pair = (0, n // 2)
        for i in range(n):
            for j in range(i + 1, n):
                d = math.hypot(coords[i][0] - coords[j][0],
                               coords[i][1] - coords[j][1])
                if d > best_dist:
                    best_dist = d
                    best_pair = (i, j)
        i0, i1 = best_pair
        nodes = {
            f"n{node_offset}": Node(id=f"n{node_offset}", type="endpoint",
                                    position=(float(coords[i0][0]), float(coords[i0][1]))),
            f"n{node_offset+1}": Node(id=f"n{node_offset+1}", type="endpoint",
                                      position=(float(coords[i1][0]), float(coords[i1][1]))),
        }
        edges = {
            f"e{edge_offset}": Edge(id=f"e{edge_offset}", priority=0,
                                    start_node_id=f"n{node_offset}",
                                    end_node_id=f"n{node_offset+1}"),
            f"e{edge_offset+1}": Edge(id=f"e{edge_offset+1}", priority=0,
                                      start_node_id=f"n{node_offset+1}",
                                      end_node_id=f"n{node_offset}"),
        }
        return nodes, edges, [f"e{edge_offset}", f"e{edge_offset+1}"], list(coords)

    # Index each corner by its position on the ring
    corner_by_index: dict[int, tuple] = {}
    for pos, angle in raw_corners:
        px, py = pos
        best_i = None
        best_d = float("inf")
        for i, (cx, cy) in enumerate(coords):
            d = math.hypot(px - cx, py - cy)
            if d < best_d:
                best_d = d
                best_i = i
        if best_i is not None and best_d < 1.0:
            corner_by_index[best_i] = (pos, angle)

    sorted_indices = sorted(corner_by_index.keys())

    # Circuit-break at sharpest corner
    sharpest_idx = max(corner_by_index.keys(),
                       key=lambda i: corner_by_index[i][1])
    pivot = sorted_indices.index(sharpest_idx)
    ordered_indices = sorted_indices[pivot:] + sorted_indices[:pivot]

    # Clockwise
    cw = _is_clockwise(coords)
    if not cw:
        ordered_indices = [ordered_indices[0]] + ordered_indices[:0:-1]

    # Build nodes
    nodes: dict = {}
    for i, idx in enumerate(ordered_indices):
        pos, angle = corner_by_index[idx]
        nid = f"n{node_offset + i}"
        nodes[nid] = Node(id=nid, type="sharp_corner",
                          position=(float(pos[0]), float(pos[1])))

    # Build edges (cyclic)
    m = len(ordered_indices)
    edges: dict = {}
    s_order: list = []
    for i in range(m):
        eid = f"e{edge_offset + i}"
        start_nid = f"n{node_offset + i}"
        end_nid = f"n{node_offset + (i + 1) % m}"
        edges[eid] = Edge(id=eid, priority=0,
                          start_node_id=start_nid, end_node_id=end_nid)
        s_order.append(eid)

    # Boundary coords for this boundary
    bound_coords = [[float(c[0]), float(c[1])] for c in coords]

    return nodes, edges, s_order, bound_coords


def build_initial_graph(polygon: Polygon) -> RoadMarkedPath:
    """
    Stage 0A graph builder.

    Detects sharp corners on ALL polygon boundaries (exterior + every
    interior hole) and builds one combined road-marking graph.  Every
    boundary becomes its own connected sub-graph with unique node/edge
    IDs.  No junction detection — just corners and edges.

    The exterior is skipped only when it appears to be a canvas
    bounding-box rectangle (area > 90 % of bbox area).
    """
    # ── collect boundaries ───────────────────────────────────────────────
    boundaries: list[tuple[str, Polygon, list]] = []  # (label, poly, coords)

    # Exterior
    ext_coords = list(polygon.exterior.coords)
    minx, miny, maxx, maxy = polygon.bounds
    bbox_area = (maxx - minx) * (maxy - miny)
    is_bbox = (bbox_area > 0 and polygon.area / bbox_area > 0.90)

    if not is_bbox:
        boundaries.append(("ext", polygon, ext_coords))

    # Interior holes
    if polygon.interiors:
        for hole in polygon.interiors:
            hole_coords = list(hole.coords)
            try:
                hp = Polygon(hole_coords)
                if hp.area > 0:
                    boundaries.append(("hole", hp, hole_coords))
            except Exception:
                continue

    # If nothing collected (bbox exterior + no valid holes), use exterior anyway
    if not boundaries:
        boundaries.append(("ext", polygon, ext_coords))

    # ── build sub-graphs for each boundary ───────────────────────────────
    all_nodes: dict = {}
    all_edges: dict = {}
    all_stitch_order: list = []
    all_boundary_coords: list = []  # list of boundaries, each a list of [x,y] pairs

    node_offset = 0
    edge_offset = 0

    for label, bound_poly, bound_coords in boundaries:
        nodes, edges, s_order, render_coords = _process_boundary(
            bound_poly, bound_coords, node_offset, edge_offset
        )
        if nodes:
            all_nodes.update(nodes)
            all_edges.update(edges)
            all_stitch_order.extend(s_order)
            all_boundary_coords.append(render_coords)
            node_offset += len(nodes)
            edge_offset += len(edges)

    return RoadMarkedPath(
        path_id="",
        rungs={},
        nodes=all_nodes,
        edges=all_edges,
        stitch_order=all_stitch_order,
        boundary_coords=all_boundary_coords,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stage 0B — Manual road-marking operations
# ─────────────────────────────────────────────────────────────────────────────

# Global counters for unique IDs (module-level to avoid collisions)
_next_rung_id = 0
_next_node_id = 0
_next_edge_id = 0


def _reset_counters():
    """Reset global ID counters (useful for testing)."""
    global _next_rung_id, _next_node_id, _next_edge_id
    _next_rung_id = 0
    _next_node_id = 0
    _next_edge_id = 0


def _init_counters_from_path(path: RoadMarkedPath):
    """Initialise ID counters based on existing IDs in a path."""
    global _next_rung_id, _next_node_id, _next_edge_id

    def _extract_num(s, prefix):
        try:
            return int(s[len(prefix):])
        except ValueError:
            return -1

    max_rung = max((_extract_num(k, "rung_") for k in path.rungs), default=-1)
    max_node = max((_extract_num(k, "n") for k in path.nodes), default=-1)
    max_edge = max((_extract_num(k, "e") for k in path.edges), default=-1)

    _next_rung_id = max_rung + 1
    _next_node_id = max_node + 1
    _next_edge_id = max_edge + 1


def _get_boundary_coords(polygon: Polygon) -> list:
    """
    Get the boundary coords that nodes lie on.
    Same logic as build_initial_graph: for ring polygons, use the largest
    interior boundary; otherwise use the exterior.
    """
    coords = list(polygon.exterior.coords)

    if polygon.interiors:
        largest_hole = max(polygon.interiors, key=lambda h: len(h.coords))
        hole_coords = list(largest_hole.coords)
        try:
            Polygon(hole_coords)  # validate
            coords = hole_coords
        except Exception:
            pass

    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return coords


def _find_closest_vertex_index(coords: list, position: tuple) -> int:
    """Find the index of the closest vertex in coords to position."""
    best_idx = 0
    best_dist = float("inf")
    for i, (cx, cy) in enumerate(coords):
        d = math.hypot(position[0] - cx, position[1] - cy)
        if d < best_dist:
            best_dist = d
            best_idx = i
    return best_idx


def _extract_boundary_segment(coords: list, start_pos: tuple, end_pos: tuple) -> list:
    """
    Extract the boundary segment (list of (x,y) points) between start_pos
    and end_pos, following the coords list in its natural order.
    """
    si = _find_closest_vertex_index(coords, start_pos)
    ei = _find_closest_vertex_index(coords, end_pos)
    n = len(coords)
    if si <= ei:
        return coords[si:ei + 1]
    else:
        return coords[si:] + coords[:ei + 1]


def _edge_boundary_line(path: RoadMarkedPath, edge_id: str, coords: list) -> LineString | None:
    """
    Build a LineString representing the boundary segment of an edge.
    Returns None if the edge or nodes are not found.
    """
    edge = path.edges.get(edge_id)
    if edge is None:
        return None
    start_node = path.nodes.get(edge.start_node_id)
    end_node = path.nodes.get(edge.end_node_id)
    if start_node is None or end_node is None:
        return None
    seg = _extract_boundary_segment(coords, start_node.position, end_node.position)
    if len(seg) < 2:
        # If segment is too short, create a direct line between nodes
        seg = [start_node.position, end_node.position]
    return LineString(seg)


def _find_edge_for_position(path: RoadMarkedPath, position: tuple, coords: list) -> str | None:
    """
    Find the edge whose boundary segment is closest to the given position.
    Returns the edge_id, or None if no edge found.
    """
    best_edge_id = None
    best_dist = float("inf")

    for edge_id in path.stitch_order:
        line = _edge_boundary_line(path, edge_id, coords)
        if line is None:
            continue
        pt = Point(position[0], position[1])
        dist = line.distance(pt)
        if dist < best_dist:
            best_dist = dist
            best_edge_id = edge_id

    return best_edge_id


def _project_point_on_edge(path: RoadMarkedPath, edge_id: str, position: tuple, coords: list) -> tuple | None:
    """
    Project a position onto an edge's boundary segment.
    Returns the (x, y) of the projected point, or None.
    """
    line = _edge_boundary_line(path, edge_id, coords)
    if line is None:
        return None
    pt = Point(position[0], position[1])
    proj_dist = line.project(pt)
    proj_pt = line.interpolate(proj_dist)
    return (float(proj_pt.x), float(proj_pt.y))


# ─────────────────────────────────────────────────────────────────────────────
# Rung creation helper
# ─────────────────────────────────────────────────────────────────────────────


def create_rung_at_point(polygon: Polygon, position: tuple) -> Rung | None:
    """
    Create a rung (crossbar) at a given position, perpendicular to the
    local boundary tangent, spanning the polygon from one side to the other.

    Returns a Rung with a unique auto-incremented ID, or None if a valid
    crossbar cannot be computed.
    """
    from .geometry import _normal_crossbar_inside_geom

    global _next_rung_id

    coords = _get_boundary_coords(polygon)
    if len(coords) < 3:
        return None

    # Build closed boundary line
    boundary_line = LineString(coords + [coords[0]])
    if boundary_line.length < 2.0:
        return None

    # Find nearest point on the boundary
    pt = Point(position[0], position[1])
    nearest_dist = boundary_line.project(pt)
    nearest_pt = boundary_line.interpolate(nearest_dist)

    # Compute tangent at nearest point using small offset along boundary
    eps = max(2.0, boundary_line.length * 0.005)
    t1 = boundary_line.interpolate(max(0.0, nearest_dist - eps))
    t2 = boundary_line.interpolate(min(boundary_line.length, nearest_dist + eps))
    tangent = (t2.x - t1.x, t2.y - t1.y)

    # Use a generous half_len to ensure the crossbar spans the shape
    half_len = max(polygon.bounds[2] - polygon.bounds[0],
                   polygon.bounds[3] - polygon.bounds[1]) * 1.5

    crossbar = _normal_crossbar_inside_geom(
        polygon,
        (float(nearest_pt.x), float(nearest_pt.y)),
        tangent,
        half_len,
    )

    if crossbar is None:
        return None

    rung_id = f"rung_{_next_rung_id}"
    _next_rung_id += 1
    return Rung(id=rung_id, p1=crossbar[0], p2=crossbar[1])


# ─────────────────────────────────────────────────────────────────────────────
# Operation 1: place_split_node
# ─────────────────────────────────────────────────────────────────────────────


def place_split_node(path: RoadMarkedPath, position: tuple, polygon: Polygon,
                     edge_id: str | None = None) -> RoadMarkedPath:
    """
    Place a user-cut node at the given position:

    1. Creates a new node at position (type="user_cut").
    2. Finds the closest edge (or uses explicit edge_id) and splits it
       into two edges connected through the new node.
    3. Creates a rung at the split point (perpendicular to local boundary).
    4. Updates stitch_order.

    Returns a modified copy of the path.
    """
    global _next_node_id, _next_edge_id

    _init_counters_from_path(path)

    coords = _get_boundary_coords(polygon)

    # 1. Find which edge to split — use explicit edge_id if provided
    if edge_id is not None and edge_id in path.edges:
        split_edge_id = edge_id
    else:
        split_edge_id = _find_edge_for_position(path, position, coords)
    if split_edge_id is None:
        raise ValueError("Could not find an edge near the given position.")

    # 2. Project position onto the edge's boundary segment
    proj_pos = _project_point_on_edge(path, split_edge_id, position, coords)
    if proj_pos is None:
        proj_pos = position  # fallback

    # 3. Create the new node
    new_node_id = f"n{_next_node_id}"
    _next_node_id += 1
    new_node = Node(id=new_node_id, type="user_cut", position=proj_pos)

    # 4. Split the edge
    old_edge = path.edges[split_edge_id]
    old_start = old_edge.start_node_id
    old_end = old_edge.end_node_id

    # Two new edges: old_start -> new_node, new_node -> old_end
    new_edge_a_id = f"e{_next_edge_id}"
    _next_edge_id += 1
    new_edge_a = Edge(
        id=new_edge_a_id,
        priority=old_edge.priority,
        start_node_id=old_start,
        end_node_id=new_node_id,
        start_rung_id=old_edge.start_rung_id,
        end_rung_id=None,
        yield_to_edge_id=old_edge.yield_to_edge_id,
    )

    new_edge_b_id = f"e{_next_edge_id}"
    _next_edge_id += 1
    new_edge_b = Edge(
        id=new_edge_b_id,
        priority=old_edge.priority,
        start_node_id=new_node_id,
        end_node_id=old_end,
        start_rung_id=None,
        end_rung_id=old_edge.end_rung_id,
        yield_to_edge_id=None,
    )

    # 5. Create rung at split point
    rung = create_rung_at_point(polygon, proj_pos)

    # 6. Update path
    new_path = RoadMarkedPath(
        path_id=path.path_id,
        rungs=dict(path.rungs),
        nodes=dict(path.nodes),
        edges=dict(path.edges),
        stitch_order=list(path.stitch_order),
    )

    new_path.nodes[new_node_id] = new_node
    del new_path.edges[split_edge_id]
    new_path.edges[new_edge_a_id] = new_edge_a
    new_path.edges[new_edge_b_id] = new_edge_b

    if rung:
        new_path.rungs[rung.id] = rung

    # 7. Update stitch_order: replace old edge with the two new edges
    try:
        idx = new_path.stitch_order.index(split_edge_id)
        new_path.stitch_order[idx:idx + 1] = [new_edge_a_id, new_edge_b_id]
    except ValueError:
        new_path.stitch_order.extend([new_edge_a_id, new_edge_b_id])

    return new_path


# ─────────────────────────────────────────────────────────────────────────────
# Operation 2: place_yield_rung
# ─────────────────────────────────────────────────────────────────────────────


def place_yield_rung(path: RoadMarkedPath, position: tuple,
                     primary_edge_id: str, secondary_edge_id: str,
                     polygon: Polygon) -> RoadMarkedPath:
    """
    Place a yield rung at the given position:

    1. Creates a yield rung at position (perpendicular to local boundary).
    2. Sets secondary_edge.yield_to_edge_id = primary_edge_id.
    3. Sets secondary_edge.start_rung_id to the new rung.
    4. The primary edge passes through unchanged.

    Returns a modified copy of the path.
    """
    _init_counters_from_path(path)

    if primary_edge_id not in path.edges:
        raise ValueError(f"Primary edge '{primary_edge_id}' not found.")
    if secondary_edge_id not in path.edges:
        raise ValueError(f"Secondary edge '{secondary_edge_id}' not found.")

    new_path = RoadMarkedPath(
        path_id=path.path_id,
        rungs=dict(path.rungs),
        nodes=dict(path.nodes),
        edges=dict(path.edges),
        stitch_order=list(path.stitch_order),
    )

    # Create the yield rung
    rung = create_rung_at_point(polygon, position)
    if rung is None:
        raise RuntimeError("Could not create a yield rung at the given position.")

    new_path.rungs[rung.id] = rung

    # Modify the secondary edge
    sec_edge = new_path.edges[secondary_edge_id]
    sec_edge.yield_to_edge_id = primary_edge_id
    sec_edge.start_rung_id = rung.id

    return new_path


# ─────────────────────────────────────────────────────────────────────────────
# Operation 3: set_edge_priority
# ─────────────────────────────────────────────────────────────────────────────


def set_edge_priority(path: RoadMarkedPath, edge_id: str, priority: int) -> RoadMarkedPath:
    """
    Update the priority of a single edge. Simple setter.

    Returns a modified copy of the path.
    """
    if edge_id not in path.edges:
        raise ValueError(f"Edge '{edge_id}' not found.")

    if priority not in (0, 1, 2):
        raise ValueError(f"Invalid priority {priority}. Must be 0 (primary), 1 (secondary), or 2 (tertiary).")

    new_path = RoadMarkedPath(
        path_id=path.path_id,
        rungs=dict(path.rungs),
        nodes=dict(path.nodes),
        edges=dict(path.edges),
        stitch_order=list(path.stitch_order),
    )
    new_path.edges[edge_id].priority = priority
    return new_path


# ─────────────────────────────────────────────────────────────────────────────
# Operation 4: merge_edges
# ─────────────────────────────────────────────────────────────────────────────


def merge_edges(path: RoadMarkedPath, edge_a_id: str, edge_b_id: str) -> RoadMarkedPath:
    """
    Merge two adjacent edges that share a node into a single edge.

    1. Finds the shared node between edge_a and edge_b.
    2. Removes the shared node.
    3. Creates a new merged edge from the free end of edge_a to the free
       end of edge_b.
    4. Updates stitch_order.

    Returns a modified copy of the path.
    """
    global _next_edge_id

    _init_counters_from_path(path)

    if edge_a_id not in path.edges:
        raise ValueError(f"Edge A '{edge_a_id}' not found.")
    if edge_b_id not in path.edges:
        raise ValueError(f"Edge B '{edge_b_id}' not found.")

    edge_a = path.edges[edge_a_id]
    edge_b = path.edges[edge_b_id]

    # Find the shared node
    shared_node_id = None
    a_nodes = {edge_a.start_node_id, edge_a.end_node_id}
    b_nodes = {edge_b.start_node_id, edge_b.end_node_id}
    common = a_nodes & b_nodes

    if len(common) != 1:
        raise ValueError(
            f"Edges '{edge_a_id}' and '{edge_b_id}' do not share exactly one node "
            f"(shared: {common}). Edges must be adjacent."
        )

    shared_node_id = common.pop()

    # Determine the free ends
    free_a = (
        edge_a.start_node_id
        if edge_a.end_node_id == shared_node_id
        else edge_a.end_node_id
    )
    free_b = (
        edge_b.end_node_id
        if edge_b.start_node_id == shared_node_id
        else edge_b.start_node_id
    )

    # Determine the correct rungs for the merged edge.
    # start_rung comes from the free side of edge_a,
    # end_rung comes from the free side of edge_b.
    if free_a == edge_a.start_node_id:
        merged_start_rung = edge_a.start_rung_id
    else:
        merged_start_rung = edge_a.end_rung_id

    if free_b == edge_b.end_node_id:
        merged_end_rung = edge_b.end_rung_id
    else:
        merged_end_rung = edge_b.start_rung_id

    # Sanity: free_a and free_b must be different
    if free_a == free_b:
        raise ValueError("Cannot merge edges that share both nodes (would create a loop edge).")

    # The merged edge goes from the free end of edge_a to the free end of edge_b.
    new_edge_id = f"e{_next_edge_id}"
    _next_edge_id += 1

    merged_edge = Edge(
        id=new_edge_id,
        priority=edge_a.priority,  # preserve priority from first edge
        start_node_id=free_a,
        end_node_id=free_b,
        start_rung_id=merged_start_rung,
        end_rung_id=merged_end_rung,
        yield_to_edge_id=edge_a.yield_to_edge_id or edge_b.yield_to_edge_id,
    )

    # Build new path
    new_path = RoadMarkedPath(
        path_id=path.path_id,
        rungs=dict(path.rungs),
        nodes=dict(path.nodes),
        edges=dict(path.edges),
        stitch_order=list(path.stitch_order),
    )

    # Remove old edges and shared node
    del new_path.edges[edge_a_id]
    del new_path.edges[edge_b_id]
    if shared_node_id in new_path.nodes:
        del new_path.nodes[shared_node_id]

    new_path.edges[new_edge_id] = merged_edge

    # Update stitch_order: replace edge_a and edge_b with the merged edge
    try:
        idx_a = new_path.stitch_order.index(edge_a_id)
        idx_b = new_path.stitch_order.index(edge_b_id)
        # Remove them in reverse order to preserve indices
        if idx_a < idx_b:
            new_path.stitch_order.pop(idx_b)
            new_path.stitch_order.pop(idx_a)
        else:
            new_path.stitch_order.pop(idx_a)
            new_path.stitch_order.pop(idx_b)
        new_path.stitch_order.insert(min(idx_a, idx_b), new_edge_id)
    except ValueError:
        # If edges aren't in stitch_order for some reason, append
        new_path.stitch_order.append(new_edge_id)

    return new_path


# ─────────────────────────────────────────────────────────────────────────────
# Operation 5: reorder_stitch_order
# ─────────────────────────────────────────────────────────────────────────────


def reorder_stitch_order(path: RoadMarkedPath, new_order: list) -> RoadMarkedPath:
    """
    Update the stitch order of edges.

    1. Validates that all edge IDs in new_order exist in the path.
    2. Validates that new_order contains exactly the same set of edge IDs
       as the current stitch_order.
    3. Updates stitch_order.

    Returns a modified copy of the path.
    """
    current_set = set(path.stitch_order)
    new_set = set(new_order)

    if new_set != current_set:
        missing = current_set - new_set
        extra = new_set - current_set
        msg_parts = []
        if missing:
            msg_parts.append(f"missing edges: {sorted(missing)}")
        if extra:
            msg_parts.append(f"unknown edges: {sorted(extra)}")
        raise ValueError(f"New stitch order must contain exactly the same edges. " + "; ".join(msg_parts))

    # Validate all edge IDs exist
    for eid in new_order:
        if eid not in path.edges:
            raise ValueError(f"Edge '{eid}' not found in path.")

    new_path = RoadMarkedPath(
        path_id=path.path_id,
        rungs=dict(path.rungs),
        nodes=dict(path.nodes),
        edges=dict(path.edges),
        stitch_order=list(new_order),
    )
    return new_path


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Auto-detect junctions, split edges, and place yield rungs
# ─────────────────────────────────────────────────────────────────────────────


def auto_detect_junctions(path: RoadMarkedPath, polygon: Polygon,
                          stitch_spacing: float = 20.0) -> RoadMarkedPath:
    """
    Stage 2: Auto-detect narrow-waist junctions, split edges at those
    junctions, and place yield rungs so satin stitches don't bleed
    across narrow waists.

    1. Calls ``geometry.detect_junctions()`` to find junction midpoints.
    2. For each junction:
       a. Finds the edges closest to the junction midpoint.
       b. Splits each edge at the projected position (creating rungs).
       c. After all splits, places yield rungs between edges meeting
          at the junction node.

    Returns a modified copy of the path (the original is unchanged).
    """
    from .geometry import detect_junctions

    junctions = detect_junctions(polygon, stitch_spacing)
    if not junctions:
        return RoadMarkedPath(
            path_id=path.path_id,
            rungs=dict(path.rungs),
            nodes=dict(path.nodes),
            edges=dict(path.edges),
            stitch_order=list(path.stitch_order),
            boundary_coords=list(path.boundary_coords),
        )

    current_path = RoadMarkedPath(
        path_id=path.path_id,
        rungs=dict(path.rungs),
        nodes=dict(path.nodes),
        edges=dict(path.edges),
        stitch_order=list(path.stitch_order),
        boundary_coords=list(path.boundary_coords),
    )

    coords = _get_boundary_coords(polygon)

    # Track which junction nodes were created (for yield rung placement)
    junction_nodes = []

    for junction_pt in junctions:
        _init_counters_from_path(current_path)

        # Find edges near this junction point
        edge_dists = []
        for eid in current_path.stitch_order:
            line = _edge_boundary_line(current_path, eid, coords)
            if line is not None:
                pt = Point(junction_pt[0], junction_pt[1])
                edge_dists.append((eid, line.distance(pt)))

        if not edge_dists:
            continue

        # Only split the closest edge — this is the edge that the waist
        # line crosses.
        edge_dists.sort(key=lambda x: x[1])
        closest_edge_id = edge_dists[0][0]

        # Project the junction midpoint onto the edge's boundary segment
        proj_pos = _project_point_on_edge(
            current_path, closest_edge_id, junction_pt, coords
        )
        if proj_pos is None:
            proj_pos = junction_pt

        # Split the edge at the projected point
        try:
            current_path = place_split_node(current_path, proj_pos, polygon)
        except (ValueError, RuntimeError):
            continue

        # Find the newly created node (the one at the split position)
        best_nid = None
        best_nd = float("inf")
        for nid, node in current_path.nodes.items():
            if node.type == "user_cut":
                d = math.hypot(
                    node.position[0] - proj_pos[0],
                    node.position[1] - proj_pos[1],
                )
                if d < stitch_spacing * 2 and d < best_nd:
                    best_nd = d
                    best_nid = nid

        if best_nid is not None:
            junction_nodes.append(best_nid)

    # ── Place yield rungs at junction nodes ──────────────────────────────
    for jnid in junction_nodes:
        if jnid not in current_path.nodes:
            continue

        _init_counters_from_path(current_path)
        node_pos = current_path.nodes[jnid].position

        # Find all edges that meet at this junction node
        incident_edges = []
        for eid in current_path.stitch_order:
            edge = current_path.edges[eid]
            if edge.start_node_id == jnid or edge.end_node_id == jnid:
                incident_edges.append(eid)

        if len(incident_edges) < 2:
            continue

        # The first edge is primary (passes through), others yield
        primary_edge_id = incident_edges[0]

        for sec_edge_id in incident_edges[1:]:
            try:
                current_path = place_yield_rung(
                    current_path, node_pos,
                    primary_edge_id, sec_edge_id, polygon,
                )
            except (ValueError, RuntimeError):
                continue

    return current_path
