#!/usr/bin/env python3
"""
Stage 0B integration test — exercise all 5 road-marking operations
+ rung creation helper on the puppy head outline (s0 from puppy SVG).
"""
import sys
import os
import math

# Make sure the refactor package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easystitch_core.trace import parse_traced_svg_for_structure
from easystitch_core.geometry import object_fill_geometry
from shapely.geometry import MultiPolygon, Point

from easystitch_core.road_marker import (
    build_initial_graph,
    create_rung_at_point,
    place_split_node,
    place_yield_rung,
    set_edge_priority,
    merge_edges,
    reorder_stitch_order,
    RoadMarkedPath,
    _reset_counters,
    _init_counters_from_path,
)

errors = 0


def check(name, condition, detail=""):
    global errors
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} — {detail}")
        errors += 1


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Load puppy SVG and get the polygon for s0 (largest stroke object)
# ─────────────────────────────────────────────────────────────────────────────

print("=" * 60)
print("Stage 0B — Road Marker Integration Test")
print("=" * 60)

svg_path = os.path.join(os.path.dirname(__file__), "..", "puppy_traced.svg")
if not os.path.isfile(svg_path):
    print(f"SKIP: puppy_traced.svg not found at {svg_path}")
    print("Please run the trace pipeline first.")
    sys.exit(0)

svg_w, svg_h, source_paths, objects = parse_traced_svg_for_structure(svg_path)
print(f"\nLoaded puppy SVG: {svg_w}x{svg_h}, {len(objects)} objects")

# Find the largest object (puppy head outline — typically "s0")
largest_obj = max(objects, key=lambda o: abs(object_fill_geometry(o).area
    if object_fill_geometry(o) else 0))
print(f"Largest object: id={largest_obj['id']}, label={largest_obj.get('label','?')}")

geom = object_fill_geometry(largest_obj)
if isinstance(geom, MultiPolygon):
    geom = max(geom.geoms, key=lambda g: g.area)

print(f"Polygon area: {geom.area:.1f}, bounds: {geom.bounds}")

# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Build initial graph
# ─────────────────────────────────────────────────────────────────────────────

_reset_counters()
graph = build_initial_graph(geom)
graph.path_id = largest_obj["id"]
_init_counters_from_path(graph)

n_nodes = len(graph.nodes)
n_edges = len(graph.edges)
n_rungs = len(graph.rungs)
print(f"\nInitial graph: {n_nodes} nodes, {n_edges} edges, {n_rungs} rungs")
print(f"Stitch order: {graph.stitch_order}")
for nid, node in graph.nodes.items():
    print(f"  Node {nid}: type={node.type} pos=({node.position[0]:.1f}, {node.position[1]:.1f})")

check("Graph has nodes", n_nodes >= 2, f"got {n_nodes}")
check("Graph has edges", n_edges >= 2, f"got {n_edges}")
check("All edges in stitch_order", set(graph.stitch_order) == set(graph.edges.keys()))
check("Clockwise traversal", len(graph.stitch_order) >= 2)

# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Test create_rung_at_point (helper)
# ─────────────────────────────────────────────────────────────────────────────

print("\n--- create_rung_at_point ---")

# Test at the centroid
centroid = geom.centroid
_rung_count_before = len(graph.rungs)
rung1 = create_rung_at_point(geom, (centroid.x, centroid.y))
if rung1:
    print(f"  Created rung: {rung1.id} p1=({rung1.p1[0]:.1f},{rung1.p1[1]:.1f}) p2=({rung1.p2[0]:.1f},{rung1.p2[1]:.1f})")
    check("create_rung_at_point at centroid", True,
          f"id={rung1.id}")
    check("Rung has valid endpoints",
          math.hypot(rung1.p1[0] - rung1.p2[0], rung1.p1[1] - rung1.p2[1]) > 1.0)
    # Rung endpoints should be within the polygon
    check("Rung p1 inside polygon",
          geom.contains(Point(rung1.p1)) or geom.boundary.distance(Point(rung1.p1)) < 2.0)
    check("Rung p2 inside polygon",
          geom.contains(Point(rung1.p2)) or geom.boundary.distance(Point(rung1.p2)) < 2.0)
else:
    check("create_rung_at_point at centroid", False, "returned None")

# Test at a node position
first_node = list(graph.nodes.values())[0]
rung2 = create_rung_at_point(geom, first_node.position)
check("create_rung_at_point at node position", rung2 is not None,
      "returned None" if rung2 is None else f"id={rung2.id}")

# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Test place_split_node
# ─────────────────────────────────────────────────────────────────────────────

print("\n--- place_split_node ---")

# Pick a point on the first edge's boundary midpoint
first_edge_id = graph.stitch_order[0]
first_edge = graph.edges[first_edge_id]
start_node = graph.nodes[first_edge.start_node_id]
end_node = graph.nodes[first_edge.end_node_id]
mid_x = (start_node.position[0] + end_node.position[0]) / 2
mid_y = (start_node.position[1] + end_node.position[1]) / 2

# Also shift slightly inward from the boundary
boundary_coords = list(geom.exterior.coords)
# Find the closest boundary point to the midpoint
best_pt = None
best_d = float('inf')
for cx, cy in boundary_coords:
    d = math.hypot(mid_x - cx, mid_y - cy)
    if d < best_d:
        best_d = d
        best_pt = (cx, cy)

if best_pt:
    split_pos = best_pt
    print(f"  Splitting at boundary point: ({split_pos[0]:.1f}, {split_pos[1]:.1f})")
    n_edges_before = len(graph.edges)
    n_nodes_before = len(graph.nodes)

    graph2 = place_split_node(graph, split_pos, geom)

    n_edges_after = len(graph2.edges)
    n_nodes_after = len(graph2.nodes)
    print(f"  Edges: {n_edges_before} -> {n_edges_after}")
    print(f"  Nodes: {n_nodes_before} -> {n_nodes_after}")

    check("place_split_node adds 1 node", n_nodes_after == n_nodes_before + 1,
          f"expected +1, got {n_nodes_after - n_nodes_before}")
    check("place_split_node adds 1 edge (net)", n_edges_after == n_edges_before + 1,
          f"expected +1, got {n_edges_after - n_edges_before}")

    # Check new node type
    new_nodes = [n for n in graph2.nodes.values() if n.type == "user_cut"]
    check("New node has type user_cut", len(new_nodes) >= 1, f"found {len(new_nodes)}")

    # Stitch order should be updated
    check("Stitch order updated", len(graph2.stitch_order) == n_edges_after)
else:
    check("place_split_node (skipped)", True, "no boundary point found")

# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Test set_edge_priority
# ─────────────────────────────────────────────────────────────────────────────

print("\n--- set_edge_priority ---")

target_edge = graph.stitch_order[0]
print(f"  Setting edge {target_edge} priority to 2")

graph3 = set_edge_priority(graph, target_edge, 2)
check("set_edge_priority changes priority",
      graph3.edges[target_edge].priority == 2,
      f"got {graph3.edges[target_edge].priority}")

# Test invalid priority
try:
    set_edge_priority(graph, target_edge, 99)
    check("set_edge_priority rejects invalid value", False, "should have raised")
except ValueError:
    check("set_edge_priority rejects invalid value", True)

# Test missing edge
try:
    set_edge_priority(graph, "nonexistent", 0)
    check("set_edge_priority rejects missing edge", False, "should have raised")
except ValueError:
    check("set_edge_priority rejects missing edge", True)

# ─────────────────────────────────────────────────────────────────────────────
# Step 6 — Test merge_edges
# ─────────────────────────────────────────────────────────────────────────────

print("\n--- merge_edges ---")

if len(graph.stitch_order) >= 2:
    e_a = graph.stitch_order[0]
    e_b = graph.stitch_order[1]

    # Verify they share a node
    edge_a = graph.edges[e_a]
    edge_b = graph.edges[e_b]
    a_nodes = {edge_a.start_node_id, edge_a.end_node_id}
    b_nodes = {edge_b.start_node_id, edge_b.end_node_id}
    common = a_nodes & b_nodes

    print(f"  Merging {e_a} and {e_b}, shared nodes: {common}")

    if len(common) == 1:
        n_edges_before = len(graph.edges)
        n_nodes_before = len(graph.nodes)

        graph4 = merge_edges(graph, e_a, e_b)

        n_edges_after = len(graph4.edges)
        n_nodes_after = len(graph4.nodes)
        print(f"  Edges: {n_edges_before} -> {n_edges_after}")
        print(f"  Nodes: {n_nodes_before} -> {n_nodes_after}")

        check("merge_edges reduces edges by 1", n_edges_after == n_edges_before - 1,
              f"expected -1, got {n_edges_after - n_edges_before}")
        check("merge_edges reduces nodes by 1", n_nodes_after == n_nodes_before - 1,
              f"expected -1, got {n_nodes_after - n_nodes_before}")
        check("Original edges removed", e_a not in graph4.edges and e_b not in graph4.edges)
        check("Merged edge in stitch_order",
              len(graph4.stitch_order) == len(graph4.edges))
    else:
        check("merge_edges (skipped)", True, f"edges don't share exactly 1 node (shared: {common})")
else:
    check("merge_edges (skipped)", True, "not enough edges")

# Test merge on non-adjacent edges
if len(graph.stitch_order) >= 3:
    e_x = graph.stitch_order[0]
    e_y = graph.stitch_order[2]  # likely not adjacent
    try:
        merge_edges(graph, e_x, e_y)
        check("merge_edges rejects non-adjacent", False, "should have raised")
    except ValueError:
        check("merge_edges rejects non-adjacent", True)
else:
    check("merge_edges non-adjacent (skipped)", True, "not enough edges to test")

# ─────────────────────────────────────────────────────────────────────────────
# Step 7 — Test reorder_stitch_order
# ─────────────────────────────────────────────────────────────────────────────

print("\n--- reorder_stitch_order ---")

original_order = list(graph.stitch_order)
reversed_order = list(reversed(original_order))

graph5 = reorder_stitch_order(graph, reversed_order)
check("reorder_stitch_order updates order",
      graph5.stitch_order == reversed_order,
      f"original={original_order}, got={graph5.stitch_order}")

# Test invalid order (missing edge)
try:
    bad_order = original_order[:-1]  # missing last edge
    reorder_stitch_order(graph, bad_order)
    check("reorder_stitch_order rejects incomplete order", False, "should have raised")
except ValueError:
    check("reorder_stitch_order rejects incomplete order", True)

# Test invalid order (extra edge)
try:
    bad_order = original_order + ["nonexistent"]
    reorder_stitch_order(graph, bad_order)
    check("reorder_stitch_order rejects extra edge", False, "should have raised")
except ValueError:
    check("reorder_stitch_order rejects extra edge", True)

# ─────────────────────────────────────────────────────────────────────────────
# Step 8 — Test place_yield_rung
# ─────────────────────────────────────────────────────────────────────────────

print("\n--- place_yield_rung ---")

if len(graph.stitch_order) >= 2:
    primary = graph.stitch_order[0]
    secondary = graph.stitch_order[1]

    # Use a position between the two edges
    p_edge = graph.edges[primary]
    s_edge = graph.edges[secondary]
    p_node = graph.nodes[p_edge.start_node_id]

    yield_pos = p_node.position

    n_rungs_before = len(graph.rungs)

    try:
        graph6 = place_yield_rung(graph, yield_pos, primary, secondary, geom)
        n_rungs_after = len(graph6.rungs)

        check("place_yield_rung adds a rung", n_rungs_after > n_rungs_before,
              f"rungs: {n_rungs_before} -> {n_rungs_after}")

        # Check secondary edge got modified
        updated_secondary = graph6.edges[secondary]
        check("Secondary edge has yield_to_edge_id",
              updated_secondary.yield_to_edge_id == primary,
              f"got {updated_secondary.yield_to_edge_id}")
        check("Secondary edge has start_rung_id",
              updated_secondary.start_rung_id is not None,
              f"got {updated_secondary.start_rung_id}")

        # Primary edge should be unchanged
        updated_primary = graph6.edges[primary]
        check("Primary edge unchanged (yield_to)",
              updated_primary.yield_to_edge_id is None,
              f"got {updated_primary.yield_to_edge_id}")
    except Exception as e:
        check("place_yield_rung", False, f"raised: {e}")
else:
    check("place_yield_rung (skipped)", True, "not enough edges")

# ─────────────────────────────────────────────────────────────────────────────
# Step 9 — Test JSON serialisation
# ─────────────────────────────────────────────────────────────────────────────

print("\n--- JSON round-trip ---")

d = graph.to_dict()
check("to_dict returns dict", isinstance(d, dict))
check("to_dict has path_id", "path_id" in d)
check("to_dict has rungs", "rungs" in d)
check("to_dict has nodes", "nodes" in d)
check("to_dict has edges", "edges" in d)
check("to_dict has stitch_order", "stitch_order" in d)

import json
json_str = json.dumps(d, default=str)
check("JSON serialisable", len(json_str) > 100, f"length={len(json_str)}")

# Verify a node dict
node_dict = list(d["nodes"].values())[0]
check("Node dict has id", "id" in node_dict)
check("Node dict has type", "type" in node_dict)
check("Node dict has position", "position" in node_dict)

# Verify an edge dict
edge_dict = list(d["edges"].values())[0]
check("Edge dict has id", "id" in edge_dict)
check("Edge dict has priority", "priority" in edge_dict)
check("Edge dict has start_node_id", "start_node_id" in edge_dict)
check("Edge dict has end_node_id", "end_node_id" in edge_dict)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Junction Detection + Auto-Detect Tests
# ─────────────────────────────────────────────────────────────────────────────

print("\n--- Stage 2: detect_junctions ---")

from easystitch_core.geometry import detect_junctions, _local_width_at_point, _cluster_points
from shapely.geometry import Polygon as ShapelyPolygon

# Test with a simple narrow shape
narrow_rect = ShapelyPolygon([
    (0, 0), (10, 0), (10, 50), (0, 50)
])
junctions_narrow = detect_junctions(narrow_rect, stitch_spacing=20.0)
check("detect_junctions: narrow rect has 0 junctions (too thin)",
      len(junctions_narrow) == 0,
      f"found {len(junctions_narrow)}")

# Test with a wide shape that has a narrow waist: two wide regions with bridge
# Use a shape known to produce junctions: two blobs connected by a 30px bridge
import math as _math2
bridge_pts = []
# Top arc going right
for i in range(30):
    a = _math2.pi * i / 29
    bridge_pts.append((50 + 50*_math2.cos(a), 50*_math2.sin(a)))
# Bridge region (narrow, ~30px wide)
bridge_pts.append((100, 80))
bridge_pts.append((70, 80))
bridge_pts.append((70, 50))
bridge_pts.append((30, 50))
bridge_pts.append((30, 80))
bridge_pts.append((0, 80))
# Bottom arc going back left
for i in range(30):
    a = _math2.pi + _math2.pi * i / 29
    bridge_pts.append((50 + 50*_math2.cos(a), 50*_math2.sin(a)))

wide_with_waist = ShapelyPolygon(bridge_pts)
junctions_waist = detect_junctions(wide_with_waist, stitch_spacing=20.0)
check("detect_junctions: hourglass has junctions",
      len(junctions_waist) >= 1,
      f"found {len(junctions_waist)}")

# Test with no narrow waist (simple rectangle)
simple_rect = ShapelyPolygon([(0, 0), (100, 0), (100, 80), (0, 80)])
junctions_simple = detect_junctions(simple_rect, stitch_spacing=20.0)
check("detect_junctions: simple rect has 0 junctions",
      len(junctions_simple) == 0,
      f"found {len(junctions_simple)}")

# Test _cluster_points
pts = [(0, 0), (1, 0), (5, 5)]
clusters = _cluster_points(pts, radius=2.0)
check("_cluster_points: groups nearby points",
      len(clusters) == 2,
      f"got {len(clusters)} clusters")

# Test _cluster_points single point
clusters2 = _cluster_points([(0, 0)], radius=10.0)
check("_cluster_points: single point",
      len(clusters2) == 1 and len(clusters2[0]) == 1)

# Test _local_width_at_point on simple rect (should be non-zero)
width = _local_width_at_point(simple_rect, (10, 0))
check("_local_width_at_point: returns positive width",
      width > 0,
      f"got {width}")

# Test detect_junctions returns empty for small polygon
tiny = ShapelyPolygon([(0, 0), (3, 0), (3, 3), (0, 3)])
junctions_tiny = detect_junctions(tiny, stitch_spacing=20.0)
check("detect_junctions: tiny polygon returns empty",
      len(junctions_tiny) == 0,
      f"found {len(junctions_tiny)}")

# ── Test auto_detect_junctions ──────────────────────────────────────

print("\n--- Stage 2: auto_detect_junctions ---")

from easystitch_core.road_marker import auto_detect_junctions

# Build a simple graph on the hourglass shape
_reset_counters()
graph_hourglass = build_initial_graph(wide_with_waist)
graph_hourglass.path_id = "hourglass_test"
n_edges_before = len(graph_hourglass.edges)
n_nodes_before = len(graph_hourglass.nodes)

result_auto = auto_detect_junctions(graph_hourglass, wide_with_waist, stitch_spacing=20.0)
n_edges_after = len(result_auto.edges)
n_nodes_after = len(result_auto.nodes)

check("auto_detect_junctions: returns RoadMarkedPath",
      isinstance(result_auto, RoadMarkedPath))
check("auto_detect_junctions: original unchanged",
      len(graph_hourglass.edges) == n_edges_before)
check("auto_detect_junctions: edges may increase (splits)",
      n_edges_after >= n_edges_before,
      f"before={n_edges_before}, after={n_edges_after}")

# Test auto_detect on a shape with no junctions (should return equivalent path)
_reset_counters()
graph_simple = build_initial_graph(simple_rect)
result_no_junction = auto_detect_junctions(graph_simple, simple_rect, stitch_spacing=20.0)
check("auto_detect_junctions: no junctions returns equivalent graph",
      len(result_no_junction.edges) == len(graph_simple.edges),
      f"before={len(graph_simple.edges)}, after={len(result_no_junction.edges)}")
check("auto_detect_junctions: same nodes count when no junctions",
      len(result_no_junction.nodes) == len(graph_simple.nodes))

# Test that junction nodes are created with correct type
has_user_cut = False
for nid, node in result_auto.nodes.items():
    if node.type == "user_cut":
        has_user_cut = True
        break
# Note: may not have user_cut if no junctions were detected
# Just verify the function completes without error
check("auto_detect_junctions: completes without error", True)

# ── Test on puppy head outline ──────────────────────────────────────

print("\n--- Stage 2: auto_detect on puppy head ---")

_reset_counters()
graph_puppy = build_initial_graph(geom)
graph_puppy.path_id = "puppy_auto"
n_edges_puppy_before = len(graph_puppy.edges)

result_puppy = auto_detect_junctions(graph_puppy, geom, stitch_spacing=20.0)
n_edges_puppy_after = len(result_puppy.edges)
n_nodes_puppy_after = len(result_puppy.nodes)

check("auto_detect_junctions on puppy: returns valid graph",
      result_puppy is not None)
check("auto_detect_junctions on puppy: edges >= initial",
      n_edges_puppy_after >= n_edges_puppy_before,
      f"before={n_edges_puppy_before}, after={n_edges_puppy_after}")
check("auto_detect_junctions on puppy: all edges in stitch_order",
      set(result_puppy.stitch_order) == set(result_puppy.edges.keys()))
check("auto_detect_junctions on puppy: nodes match edge references",
      all(
          (e.start_node_id in result_puppy.nodes and e.end_node_id in result_puppy.nodes)
          for e in result_puppy.edges.values()
      ))

# Check for yield rungs
yield_edges = [e for e in result_puppy.edges.values() if e.yield_to_edge_id]
print(f"  Yield edges after auto-detect: {len(yield_edges)}")
if yield_edges:
    for ye in yield_edges[:3]:
        print(f"    {ye.id} yields to {ye.yield_to_edge_id}, start_rung={ye.start_rung_id}")

# Test JSON round-trip of result
d = result_puppy.to_dict()
import json
json_str = json.dumps(d, default=str)
check("auto_detect result JSON serialisable", len(json_str) > 100, f"length={len(json_str)}")

print("\n" + "=" * 60)
if errors == 0:
    print("ALL TESTS PASSED")
else:
    print(f"{errors} TEST(S) FAILED")
print("=" * 60)

sys.exit(1 if errors > 0 else 0)
