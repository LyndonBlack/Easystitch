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
from dataclasses import dataclass, field
from typing import Optional

from shapely.geometry import Polygon


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

    def to_dict(self) -> dict:
        return {
            "path_id": self.path_id,
            "rungs": {k: v.to_dict() for k, v in self.rungs.items()},
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": {k: v.to_dict() for k, v in self.edges.items()},
            "stitch_order": self.stitch_order,
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


def build_initial_graph(polygon: Polygon) -> RoadMarkedPath:
    """
    Stage 0A graph builder.
    Detects sharp corners on the polygon boundary and creates an
    initial road-marking graph (nodes + edges) with no junction
    detection or narrow-waist splitting.

    For ring polygons (with holes), corners are detected on the
    largest interior boundary (which traces the actual shape
    outline) rather than the exterior canvas bounds.

    For closed polygons the sharpest corner is used as the
    circuit-break — the stitch order starts after that corner.
    """
    from .geometry import detect_sharp_corners

    # ── choose the right boundary for corner detection ──────────────────
    # For ring polygons (thick outlines with interior holes), the exterior
    # is often just the canvas bounding box.  The actual shape detail is
    # in the largest interior boundary.
    coords = list(polygon.exterior.coords)
    corner_poly = polygon

    if polygon.interiors:
        # Find the largest hole (most vertices = most detail)
        largest_hole = max(polygon.interiors, key=lambda h: len(h.coords))
        hole_coords = list(largest_hole.coords)
        # Use the hole boundary for corner detection.
        # Create a simple polygon from the hole for detect_sharp_corners.
        try:
            corner_poly = Polygon(hole_coords)
            coords = hole_coords
        except Exception:
            pass  # fall back to exterior

    # ── exterior ring as plain coordinate list ──────────────────────────
    # Shapely rings are closed — drop the duplicated endpoint.
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    n = len(coords)

    # ── detect sharp corners ────────────────────────────────────────────
    # Use a lower threshold (45° deviation ≈ interior angle < 135°) to
    # catch ear-corner junctions and other moderate turns.
    raw_corners = detect_sharp_corners(corner_poly, angle_threshold_deg=150.0,
                                      spatial_radius_px=12.0)
    # raw_corners: list of ((x, y), angle_deg)

    if not raw_corners:
        # Degenerate case: no sharp corners found.
        # Create two synthetic nodes at opposite ends of the shape and
        # one edge between them so the graph is at least minimally usable.
        if n < 3:
            # Not enough vertices — return empty graph.
            return RoadMarkedPath(
                path_id="",
                rungs={},
                nodes={},
                edges={},
                stitch_order=[],
            )

        # Pick the two most-distant vertices as synthetic nodes.
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
            "n0": Node(id="n0", type="endpoint",
                       position=(float(coords[i0][0]), float(coords[i0][1]))),
            "n1": Node(id="n1", type="endpoint",
                       position=(float(coords[i1][0]), float(coords[i1][1]))),
        }
        edges = {
            "e0": Edge(id="e0", priority=0,
                       start_node_id="n0", end_node_id="n1"),
            "e1": Edge(id="e1", priority=0,
                       start_node_id="n1", end_node_id="n0"),
        }
        return RoadMarkedPath(
            path_id="",
            rungs={},
            nodes=nodes,
            edges=edges,
            stitch_order=["e0", "e1"],
        )

    # ── index each corner by its position on the ring ───────────────────
    # Map coord-index -> (position, angle)
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

    # Sort in boundary-traversal order.
    sorted_indices = sorted(corner_by_index.keys())

    # ── circuit-break: the sharpest corner (largest deviation) ──────────
    sharpest_idx = max(corner_by_index.keys(),
                       key=lambda i: corner_by_index[i][1])

    # Rotate the sorted-indices list so that the sharpest corner is first.
    pivot = sorted_indices.index(sharpest_idx)
    ordered_indices = sorted_indices[pivot:] + sorted_indices[:pivot]

    # ── ensure clockwise traversal ──────────────────────────────────────
    cw = _is_clockwise(coords)
    if not cw:
        # Reverse the traversal so stitch_order is clockwise.
        ordered_indices = [ordered_indices[0]] + ordered_indices[:0:-1]

    # ── build nodes ─────────────────────────────────────────────────────
    nodes: dict = {}
    for i, idx in enumerate(ordered_indices):
        pos, angle = corner_by_index[idx]
        nid = f"n{i}"
        nodes[nid] = Node(
            id=nid,
            type="sharp_corner",
            position=(float(pos[0]), float(pos[1])),
        )

    # ── build edges (cyclic — last edge connects back to first node) ────
    m = len(ordered_indices)
    edges: dict = {}
    stitch_order: list = []
    for i in range(m):
        eid = f"e{i}"
        start_nid = f"n{i}"
        end_nid = f"n{(i + 1) % m}"
        edges[eid] = Edge(
            id=eid,
            priority=0,
            start_node_id=start_nid,
            end_node_id=end_nid,
            start_rung_id=None,
            end_rung_id=None,
            yield_to_edge_id=None,
        )
        stitch_order.append(eid)

    return RoadMarkedPath(
        path_id="",
        rungs={},
        nodes=nodes,
        edges=edges,
        stitch_order=stitch_order,
    )
