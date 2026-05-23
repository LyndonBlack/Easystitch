#!/usr/bin/env python3
"""
Focused tests for Bug 1 (merge_edges deletes both parts) and
Bug 2 (reorder doesn't persist).

These tests use minimal hand-built RoadMarkedPath fixtures so they
run fast and independently of SVG/trace pipeline.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easystitch_core.road_marker import (
    Edge,
    Node,
    RoadMarkedPath,
    merge_edges,
    reorder_stitch_order,
    _reset_counters,
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
# Helper: build a simple 3-node, 2-edge path
#   nA ---eA---> nB ---eB---> nC
# ─────────────────────────────────────────────────────────────────────────────

def build_chain_path():
    _reset_counters()
    nA = Node(id="nA", type="sharp_corner", position=[0.0, 0.0])
    nB = Node(id="nB", type="sharp_corner", position=[10.0, 0.0])
    nC = Node(id="nC", type="sharp_corner", position=[20.0, 0.0])

    eA = Edge(
        id="eA", priority=1,
        start_node_id="nA", end_node_id="nB",
        start_rung_id=None, end_rung_id=None,
    )
    eB = Edge(
        id="eB", priority=1,
        start_node_id="nB", end_node_id="nC",
        start_rung_id=None, end_rung_id=None,
    )

    return RoadMarkedPath(
        path_id="chain",
        rungs={},
        nodes={"nA": nA, "nB": nB, "nC": nC},
        edges={"eA": eA, "eB": eB},
        stitch_order=["eA", "eB"],
    )


# =============================================================================
# BUG 1: merge_edges
# =============================================================================

print("=" * 60)
print("BUG 1: merge_edges — merged edge endpoint integrity")
print("=" * 60)

# Test 1a: basic forward chain (nA -> nB -> nC)
print("\n--- Test 1a: forward chain (eA: nA->nB, eB: nB->nC) ---")
path = build_chain_path()
merged = merge_edges(path, "eA", "eB")

check("Edges reduced to 1", len(merged.edges) == 1)
check("Nodes reduced to 2", len(merged.nodes) == 2)
check("eA removed", "eA" not in merged.edges)
check("eB removed", "eB" not in merged.edges)
check("nB (shared) removed", "nB" not in merged.nodes)
check("nA still present", "nA" in merged.nodes)
check("nC still present", "nC" in merged.nodes)

merged_edge = list(merged.edges.values())[0]
check("Merged edge start is nA", merged_edge.start_node_id == "nA",
      f"got {merged_edge.start_node_id}")
check("Merged edge end is nC", merged_edge.end_node_id == "nC",
      f"got {merged_edge.end_node_id}")
check("Merged edge endpoints in nodes dict",
      merged_edge.start_node_id in merged.nodes
      and merged_edge.end_node_id in merged.nodes,
      f"start={merged_edge.start_node_id} in nodes={merged_edge.start_node_id in merged.nodes}, end={merged_edge.end_node_id} in nodes={merged_edge.end_node_id in merged.nodes}")
check("Stitch order has 1 entry", len(merged.stitch_order) == 1,
      f"got {merged.stitch_order}")
check("Stitch order contains merged edge", merged.stitch_order[0] == merged_edge.id)

# Test 1b: reverse chain (eA: nB->nA, eB: nC->nB) — shared is nB
print("\n--- Test 1b: reverse chain (eA: nB->nA, eB: nC->nB, shared=nB) ---")
path2 = RoadMarkedPath(
    path_id="chain_rev",
    rungs={},
    nodes={"nA": Node(id="nA", type="sharp_corner", position=[0.0, 0.0]),
           "nB": Node(id="nB", type="sharp_corner", position=[10.0, 0.0]),
           "nC": Node(id="nC", type="sharp_corner", position=[20.0, 0.0])},
    edges={
        "eA": Edge(id="eA", priority=1,
                   start_node_id="nB", end_node_id="nA",
                   start_rung_id=None, end_rung_id=None),
        "eB": Edge(id="eB", priority=1,
                   start_node_id="nC", end_node_id="nB",
                   start_rung_id=None, end_rung_id=None),
    },
    stitch_order=["eA", "eB"],
)
merged2 = merge_edges(path2, "eA", "eB")
me2 = list(merged2.edges.values())[0]
check("Merged edge start is nA (free of eA)", me2.start_node_id == "nA",
      f"got {me2.start_node_id}")
check("Merged edge end is nC (free of eB)", me2.end_node_id == "nC",
      f"got {me2.end_node_id}")
check("Endpoints valid in nodes", me2.start_node_id in merged2.nodes
      and me2.end_node_id in merged2.nodes)

# Test 1c: A's end is B's end  (eA: nA->nB, eB: nC->nB, shared=nB)
print("\n--- Test 1c: both edges end at shared node (eA: nA->nB, eB: nC->nB) ---")
path3 = RoadMarkedPath(
    path_id="both_end",
    rungs={},
    nodes={"nA": Node(id="nA", type="sharp_corner", position=[0.0, 0.0]),
           "nB": Node(id="nB", type="sharp_corner", position=[10.0, 0.0]),
           "nC": Node(id="nC", type="sharp_corner", position=[20.0, 0.0])},
    edges={
        "eA": Edge(id="eA", priority=1,
                   start_node_id="nA", end_node_id="nB",
                   start_rung_id=None, end_rung_id=None),
        "eB": Edge(id="eB", priority=1,
                   start_node_id="nC", end_node_id="nB",
                   start_rung_id=None, end_rung_id=None),
    },
    stitch_order=["eA", "eB"],
)
merged3 = merge_edges(path3, "eA", "eB")
me3 = list(merged3.edges.values())[0]
check("Merged edge start is nA", me3.start_node_id == "nA",
      f"got {me3.start_node_id}")
check("Merged edge end is nC", me3.end_node_id == "nC",
      f"got {me3.end_node_id}")
check("Endpoints valid", me3.start_node_id in merged3.nodes
      and me3.end_node_id in merged3.nodes)

# Test 1d: A's start is B's start (eA: nB->nA, eB: nB->nC, shared=nB)
print("\n--- Test 1d: both edges start at shared node (eA: nB->nA, eB: nB->nC) ---")
path4 = RoadMarkedPath(
    path_id="both_start",
    rungs={},
    nodes={"nA": Node(id="nA", type="sharp_corner", position=[0.0, 0.0]),
           "nB": Node(id="nB", type="sharp_corner", position=[10.0, 0.0]),
           "nC": Node(id="nC", type="sharp_corner", position=[20.0, 0.0])},
    edges={
        "eA": Edge(id="eA", priority=1,
                   start_node_id="nB", end_node_id="nA",
                   start_rung_id=None, end_rung_id=None),
        "eB": Edge(id="eB", priority=1,
                   start_node_id="nB", end_node_id="nC",
                   start_rung_id=None, end_rung_id=None),
    },
    stitch_order=["eA", "eB"],
)
merged4 = merge_edges(path4, "eA", "eB")
me4 = list(merged4.edges.values())[0]
check("Merged edge start is nA", me4.start_node_id == "nA",
      f"got {me4.start_node_id}")
check("Merged edge end is nC", me4.end_node_id == "nC",
      f"got {me4.end_node_id}")
check("Endpoints valid", me4.start_node_id in merged4.nodes
      and me4.end_node_id in merged4.nodes)

# Test 1e: verify rung propagation
print("\n--- Test 1e: rung propagation across merge ---")
path5 = RoadMarkedPath(
    path_id="rung_chain",
    rungs={},
    nodes={"nA": Node(id="nA", type="sharp_corner", position=[0.0, 0.0]),
           "nB": Node(id="nB", type="sharp_corner", position=[10.0, 0.0]),
           "nC": Node(id="nC", type="sharp_corner", position=[20.0, 0.0])},
    edges={
        "eA": Edge(id="eA", priority=1,
                   start_node_id="nA", end_node_id="nB",
                   start_rung_id="rA_start", end_rung_id="rA_end"),
        "eB": Edge(id="eB", priority=1,
                   start_node_id="nB", end_node_id="nC",
                   start_rung_id="rB_start", end_rung_id="rB_end"),
    },
    stitch_order=["eA", "eB"],
)
merged5 = merge_edges(path5, "eA", "eB")
me5 = list(merged5.edges.values())[0]
# free_a = nA (eA.start_node_id since eA.end == shared)
# free_b = nC (eB.end_node_id since eB.start == shared)
# start_rung should be from free_a side of eA = eA.start_rung_id = "rA_start"
# end_rung should be from free_b side of eB = eB.end_rung_id = "rB_end"
check("Start rung preserved from free side of eA",
      me5.start_rung_id == "rA_start",
      f"got {me5.start_rung_id}")
check("End rung preserved from free side of eB",
      me5.end_rung_id == "rB_end",
      f"got {me5.end_rung_id}")

# Test 1f: rung propagation for reverse edges
print("\n--- Test 1f: rung propagation with reverse edge orientation ---")
path6 = RoadMarkedPath(
    path_id="rung_rev",
    rungs={},
    nodes={"nA": Node(id="nA", type="sharp_corner", position=[0.0, 0.0]),
           "nB": Node(id="nB", type="sharp_corner", position=[10.0, 0.0]),
           "nC": Node(id="nC", type="sharp_corner", position=[20.0, 0.0])},
    edges={
        # eA goes nB->nA, eB goes nC->nB. Shared is nB.
        # free_a = nA (eA.end since eA.end != shared? No: eA.end_node_id=nA, shared=nB, so eA.end != shared, free_a = eA.end = nA)
        # Wait: free_a = start if end==shared else end. end==nA, shared=nB, not equal, so free_a = eA.end_node_id = nA. ✓
        # free_b = end if start==shared else start. start=nC, shared=nB, not equal, so free_b = eB.start_node_id = nC. ✓
        "eA": Edge(id="eA", priority=1,
                   start_node_id="nB", end_node_id="nA",
                   start_rung_id="rB_side", end_rung_id="rA_side"),
        "eB": Edge(id="eB", priority=1,
                   start_node_id="nC", end_node_id="nB",
                   start_rung_id="rC_side", end_rung_id="rB_other"),
    },
    stitch_order=["eA", "eB"],
)
merged6 = merge_edges(path6, "eA", "eB")
me6 = list(merged6.edges.values())[0]
# free_a = nA (eA.end_node_id). Start rung = eA.end_rung_id = "rA_side"
# free_b = nC (eB.start_node_id). End rung = eB.start_rung_id (since free_b != eB.end) = "rC_side"
check("Reverse rung: start from eA free end",
      me6.start_rung_id == "rA_side",
      f"got {me6.start_rung_id}")
check("Reverse rung: end from eB free start",
      me6.end_rung_id == "rC_side",
      f"got {me6.end_rung_id}")

# Test 1g: error on non-adjacent edges
print("\n--- Test 1g: reject non-adjacent ---")
path7 = RoadMarkedPath(
    path_id="nonadj",
    rungs={},
    nodes={
        "nA": Node(id="nA", type="sharp_corner", position=[0.0, 0.0]),
        "nB": Node(id="nB", type="sharp_corner", position=[10.0, 0.0]),
        "nC": Node(id="nC", type="sharp_corner", position=[20.0, 0.0]),
        "nD": Node(id="nD", type="sharp_corner", position=[30.0, 0.0]),
    },
    edges={
        "eA": Edge(id="eA", priority=1,
                   start_node_id="nA", end_node_id="nB"),
        "eB": Edge(id="eB", priority=1,
                   start_node_id="nC", end_node_id="nD"),
    },
    stitch_order=["eA", "eB"],
)
try:
    merge_edges(path7, "eA", "eB")
    check("Reject non-adjacent (no shared node)", False, "should have raised ValueError")
except ValueError:
    check("Reject non-adjacent (no shared node)", True)

# Test 1h: error on missing edge
print("\n--- Test 1h: reject missing edge ---")
try:
    merge_edges(path, "eA", "nonexistent")
    check("Reject missing edge_b", False, "should have raised ValueError")
except ValueError:
    check("Reject missing edge_b", True)


# =============================================================================
# BUG 2: reorder persistence
# =============================================================================

print("\n" + "=" * 60)
print("BUG 2: reorder_stitch_order persistence")
print("=" * 60)

# Test 2a: basic reorder
print("\n--- Test 2a: basic reorder creates new path with new order ---")
orig = build_chain_path()
reordered = reorder_stitch_order(orig, ["eB", "eA"])
check("New path has reversed order", reordered.stitch_order == ["eB", "eA"],
      f"got {reordered.stitch_order}")
check("Original path unchanged", orig.stitch_order == ["eA", "eB"],
      f"got {orig.stitch_order}")
check("Returned path is different object", reordered is not orig)

# Test 2b: reorder with correct set but different order
print("\n--- Test 2b: reorder same set, different order ---")
orig2 = build_chain_path()
reordered2 = reorder_stitch_order(orig2, ["eB", "eA"])
check("Stitch order updated", reordered2.stitch_order == ["eB", "eA"])
check("Edges still intact", len(reordered2.edges) == 2)
check("Nodes still intact", len(reordered2.nodes) == 3)

# Test 2c: reorder rejects missing edge
print("\n--- Test 2c: reject missing edge in new order ---")
orig3 = build_chain_path()
try:
    reorder_stitch_order(orig3, ["eA"])  # missing eB
    check("Reject incomplete order", False, "should have raised ValueError")
except ValueError:
    check("Reject incomplete order", True)

# Test 2d: reorder rejects extra edge
print("\n--- Test 2d: reject extra edge in new order ---")
try:
    reorder_stitch_order(orig3, ["eA", "eB", "eX"])
    check("Reject extra edge", False, "should have raised ValueError")
except ValueError:
    check("Reject extra edge", True)

# Test 2e: reorder rejects non-existent edge
print("\n--- Test 2e: reject non-existent edge ---")
try:
    reorder_stitch_order(orig3, ["eA", "nonexistent"])
    check("Reject non-existent edge", False, "should have raised ValueError")
except ValueError:
    check("Reject non-existent edge", True)

# Test 2f: simulate API endpoint persistence pattern
#   endpoint: current = _get_road_state(path_id)
#             updated = reorder_stitch_order(current, order)
#             _road_state[path_id] = updated
#             result = updated.to_dict()
print("\n--- Test 2f: simulate endpoint cache update pattern ---")
cache = {}
cache["chain"] = build_chain_path()

# First operation
current = cache["chain"]
updated = reorder_stitch_order(current, ["eB", "eA"])
cache["chain"] = updated
result = updated.to_dict()

check("Endpoint response has reversed stitch_order",
      result["stitch_order"] == ["eB", "eA"],
      f"got {result['stitch_order']}")
check("Cache updated with reversed order",
      cache["chain"].stitch_order == ["eB", "eA"],
      f"got {cache['chain'].stitch_order}")

# Second operation (like merge after reorder)
current2 = cache["chain"]
check("Second read sees reordered state",
      current2.stitch_order == ["eB", "eA"],
      f"got {current2.stitch_order}")

# Merge after reorder
merged_after_reorder = merge_edges(current2, "eB", "eA")
cache["chain"] = merged_after_reorder
check("Merge after reorder works", len(merged_after_reorder.edges) == 1)
me = list(merged_after_reorder.edges.values())[0]
check("Merged after reorder has valid endpoints",
      me.start_node_id in merged_after_reorder.nodes
      and me.end_node_id in merged_after_reorder.nodes)

# Test 2g: reorder-to-dict round-trip preserves metadata
print("\n--- Test 2g: to_dict after reorder includes all fields ---")
orig4 = build_chain_path()
reordered4 = reorder_stitch_order(orig4, ["eB", "eA"])
d = reordered4.to_dict()
check("to_dict has path_id", d.get("path_id") == "chain")
check("to_dict has rungs", "rungs" in d)
check("to_dict has nodes", "nodes" in d and len(d["nodes"]) == 3)
check("to_dict has edges", "edges" in d and len(d["edges"]) == 2)
check("to_dict stitch_order matches", d["stitch_order"] == ["eB", "eA"])

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
if errors == 0:
    print("ALL BUG-FIX TESTS PASSED")
else:
    print(f"{errors} TEST(S) FAILED")
print("=" * 60)

sys.exit(1 if errors > 0 else 0)
