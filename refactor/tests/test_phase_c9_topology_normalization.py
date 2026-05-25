#!/usr/bin/env python3
"""Phase C.9 topology normalization tests for Satin V2 road graphs."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easystitch_core.road_marker import normalize_graph_topology


def edge_between(graph, source, target):
    for edge in graph["edges"]:
        if edge["source"] == source and edge["target"] == target:
            return edge
    raise AssertionError(f"missing edge {source}->{target}; edges={graph['edges']}")


def test_normalize_graph_topology_splits_long_edge_at_near_branch_endpoint():
    graph = {
        "nodes": [
            {"id": "node_a", "x": 0.0, "y": 0.0, "type": "endpoint", "degree": 1},
            {"id": "node_c", "x": 30.0, "y": 0.0, "type": "endpoint", "degree": 1},
            # Branch endpoint is close to the long edge interior but not exactly on it.
            {"id": "node_b", "x": 10.0, "y": 2.0, "type": "endpoint", "degree": 1},
            {"id": "node_d", "x": 10.0, "y": 14.0, "type": "endpoint", "degree": 1},
        ],
        "edges": [
            {
                "id": "edge_long",
                "source": "node_a",
                "target": "node_c",
                "points": [[0.0, 0.0], [30.0, 0.0]],
                "length": 30.0,
                "source_object_ids": ["satin_1"],
                "priority": None,
                "assignment": "unmarked",
            },
            {
                "id": "edge_branch",
                "source": "node_b",
                "target": "node_d",
                "points": [[10.0, 2.0], [10.0, 14.0]],
                "length": 12.0,
                "source_object_ids": ["satin_1"],
                "priority": None,
                "assignment": "unmarked",
            },
        ],
    }

    normalized = normalize_graph_topology(graph, snap_tolerance=8.0)

    long_edges = [edge for edge in normalized["edges"] if edge.get("source_edge_id") == "edge_long"]
    assert [(edge["source"], edge["target"]) for edge in long_edges] == [
        ("node_a", "node_b"),
        ("node_b", "node_c"),
    ]
    assert edge_between(normalized, "node_a", "node_b")["points"] == [[0.0, 0.0], [10.0, 0.0]]
    assert edge_between(normalized, "node_b", "node_c")["points"] == [[10.0, 0.0], [30.0, 0.0]]
    assert edge_between(normalized, "node_b", "node_d")["source_object_ids"] == ["satin_1"]

    nodes = {node["id"]: node for node in normalized["nodes"]}
    assert nodes["node_b"]["type"] == "junction"
    assert nodes["node_b"]["degree"] == 3
    assert nodes["node_a"]["type"] == "endpoint"
    assert nodes["node_c"]["type"] == "endpoint"


def test_normalize_graph_topology_preserves_manual_boundary_type_when_splitting():
    graph = {
        "nodes": [
            {"id": "node_a", "x": 0.0, "y": 0.0, "type": "endpoint", "degree": 1},
            {"id": "node_c", "x": 20.0, "y": 0.0, "type": "endpoint", "degree": 1},
            {"id": "node_split", "x": 9.5, "y": 1.0, "type": "manual_split_boundary", "degree": 2},
        ],
        "edges": [
            {
                "id": "edge_long",
                "source": "node_a",
                "target": "node_c",
                "points": [[0.0, 0.0], [20.0, 0.0]],
                "length": 20.0,
                "source_object_ids": ["satin_1", "satin_2"],
                "priority": None,
                "assignment": "unmarked",
            }
        ],
    }

    normalized = normalize_graph_topology(graph, snap_tolerance=8.0)

    assert [(edge["source"], edge["target"]) for edge in normalized["edges"]] == [
        ("node_a", "node_split"),
        ("node_split", "node_c"),
    ]
    nodes = {node["id"]: node for node in normalized["nodes"]}
    assert nodes["node_split"]["type"] == "manual_split_boundary"
    assert nodes["node_split"]["degree"] == 2


def test_normalize_graph_topology_rejects_close_parallel_endpoint_without_evidence():
    graph = {
        "nodes": [
            {"id": "node_a", "x": 0.0, "y": 0.0, "type": "endpoint", "degree": 1},
            {"id": "node_b", "x": 30.0, "y": 0.0, "type": "endpoint", "degree": 1},
            {"id": "node_c", "x": 10.0, "y": 5.0, "type": "endpoint", "degree": 1},
            {"id": "node_d", "x": 30.0, "y": 5.0, "type": "endpoint", "degree": 1},
        ],
        "edges": [
            {
                "id": "edge_top",
                "source": "node_a",
                "target": "node_b",
                "points": [[0.0, 0.0], [30.0, 0.0]],
                "length": 30.0,
                "source_object_ids": ["satin_1"],
            },
            {
                "id": "edge_parallel",
                "source": "node_c",
                "target": "node_d",
                "points": [[10.0, 5.0], [30.0, 5.0]],
                "length": 20.0,
                "source_object_ids": ["satin_2"],
            },
        ],
    }

    normalized = normalize_graph_topology(graph, snap_tolerance=8.0)

    assert len(normalized["edges"]) == 2
    assert {edge["id"] for edge in normalized["edges"]} == {"edge_top", "edge_parallel"}


def test_normalize_graph_topology_allows_very_close_different_object_endpoint():
    graph = {
        "nodes": [
            {"id": "node_a", "x": 0.0, "y": 0.0, "type": "endpoint", "degree": 1},
            {"id": "node_b", "x": 30.0, "y": 0.0, "type": "endpoint", "degree": 1},
            {"id": "node_c", "x": 15.0, "y": 1.0, "type": "endpoint", "degree": 1},
            {"id": "node_d", "x": 15.0, "y": 12.0, "type": "endpoint", "degree": 1},
        ],
        "edges": [
            {
                "id": "edge_top",
                "source": "node_a",
                "target": "node_b",
                "points": [[0.0, 0.0], [30.0, 0.0]],
                "length": 30.0,
                "source_object_ids": ["satin_1"],
            },
            {
                "id": "edge_branch",
                "source": "node_c",
                "target": "node_d",
                "points": [[15.0, 1.0], [15.0, 12.0]],
                "length": 11.0,
                "source_object_ids": ["satin_2"],
            },
        ],
    }

    normalized = normalize_graph_topology(graph, snap_tolerance=8.0)

    assert [(edge["source"], edge["target"]) for edge in normalized["edges"] if edge.get("source_edge_id") == "edge_top"] == [
        ("node_a", "node_c"),
        ("node_c", "node_b"),
    ]


def test_normalize_topology_creates_node_at_edge_crossing():
    """Two edges cross at 90° with no node at the intersection."""
    graph = {
        "nodes": [
            {"id": "node_l", "x": 0, "y": 10, "type": "endpoint", "degree": 1},
            {"id": "node_r", "x": 20, "y": 10, "type": "endpoint", "degree": 1},
            {"id": "node_t", "x": 10, "y": 0, "type": "endpoint", "degree": 1},
            {"id": "node_b", "x": 10, "y": 20, "type": "endpoint", "degree": 1},
        ],
        "edges": [
            {"id": "edge_h", "source": "node_l", "target": "node_r",
             "points": [[0, 10], [20, 10]], "length": 20, "source_object_ids": ["satin_1"]},
            {"id": "edge_v", "source": "node_t", "target": "node_b",
             "points": [[10, 0], [10, 20]], "length": 20, "source_object_ids": ["satin_2"]},
        ],
    }

    normalized = normalize_graph_topology(graph, snap_tolerance=8.0)

    gen_nodes = [node for node in normalized["nodes"]
                 if node.get("id", "").startswith("gen_junction_")]
    assert len(gen_nodes) == 1
    gen_id = gen_nodes[0]["id"]

    # Both original edges should now be split into two spans each
    h_children = [e for e in normalized["edges"]
                  if e.get("source_edge_id") == "edge_h" and e["id"].startswith("edge_h")]
    v_children = [e for e in normalized["edges"]
                  if e.get("source_edge_id") == "edge_v" and e["id"].startswith("edge_v")]
    assert len(h_children) == 2
    assert len(v_children) == 2
    assert {e["source"] for e in h_children} == {"node_l", gen_id}
    assert {e["target"] for e in h_children} == {gen_id, "node_r"}
    assert {e["source"] for e in v_children} == {"node_t", gen_id}
    assert {e["target"] for e in v_children} == {gen_id, "node_b"}


def test_normalize_topology_allows_endpoint_T_junction_across_objects():
    """A vertical branch projects 5px from a horizontal edge. Since the angle
    is 90° (genuine T-junction), allow the snap despite different object ids."""
    graph = {
        "nodes": [
            {"id": "node_a", "x": 0, "y": 0, "type": "endpoint", "degree": 1},
            {"id": "node_b", "x": 30, "y": 0, "type": "endpoint", "degree": 1},
            {"id": "node_c", "x": 15, "y": 5, "type": "endpoint", "degree": 1},
            {"id": "node_d", "x": 15, "y": 15, "type": "endpoint", "degree": 1},
        ],
        "edges": [
            {"id": "edge_top", "source": "node_a", "target": "node_b",
             "points": [[0, 0], [30, 0]], "length": 30, "source_object_ids": ["satin_1"]},
            {"id": "edge_branch", "source": "node_c", "target": "node_d",
             "points": [[15, 5], [15, 15]], "length": 10, "source_object_ids": ["satin_2"]},
        ],
    }

    normalized = normalize_graph_topology(graph, snap_tolerance=8.0)

    top_children = [e for e in normalized["edges"]
                    if e.get("source_edge_id") == "edge_top"]
    assert len(top_children) == 2
    endpoints = {top_children[0]["source"], top_children[0]["target"],
                 top_children[1]["source"], top_children[1]["target"]}
    assert "node_c" in endpoints
