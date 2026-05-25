#!/usr/bin/env python3
"""Phase C.10 self-near-intersection splitting tests."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easystitch_core.road_marker import (
    _segment_min_distance,
    _walk_distances,
    split_self_near_intersections,
)


def test_walk_distances():
    points = [[0.0, 0.0], [3.0, 4.0], [3.0, 8.0]]
    dists = _walk_distances(points)
    assert dists == [0.0, 5.0, 9.0]


def test_segment_min_distance_crossing():
    d, pa, pb = _segment_min_distance([0, 0], [10, 0], [5, -5], [5, 5])
    assert d == 0.0
    assert pa == [5.0, 0.0]


def test_segment_min_distance_near():
    d, pa, pb = _segment_min_distance([0, 0], [10, 0], [5, 3], [15, 3])
    assert abs(d - 3.0) < 1e-9


def test_self_crossing_figure_eight():
    """A figure-eight-shaped edge should create a self_intersection node."""
    graph = {
        "nodes": [
            {"id": "node_a", "x": 0, "y": 0, "type": "endpoint", "degree": 1},
            {"id": "node_b", "x": 10, "y": 0, "type": "endpoint", "degree": 1},
        ],
        "edges": [
            {
                "id": "edge_fig8",
                "source": "node_a",
                "target": "node_b",
                "points": [[0, 0], [10, 5], [0, 5], [10, 0]],
                "source_object_ids": ["sat_1"],
            },
        ],
    }

    result = split_self_near_intersections(graph, snap_tolerance=4.0, segment_separation=2.0)

    self_nodes = [n for n in result["nodes"] if n.get("type") == "self_intersection"]
    assert len(self_nodes) >= 1, f"Expected at least 1 self_intersection node, got {len(self_nodes)}"
    assert len(result["edges"]) > 1, "Edge should have been split"


def test_endpoint_nears_own_edge():
    """A polyline that wraps such that its endpoint is near its own interior."""
    graph = {
        "nodes": [
            {"id": "node_a", "x": 0, "y": 0, "type": "endpoint", "degree": 1},
            {"id": "node_b", "x": 10, "y": 1, "type": "endpoint", "degree": 1},
        ],
        "edges": [
            {
                "id": "edge_wrap",
                "source": "node_a",
                "target": "node_b",
                "points": [[0, 0], [1, 8], [9, 8], [10, 1], [10, 5]],
                "source_object_ids": ["sat_1"],
            },
        ],
    }

    result = split_self_near_intersections(graph, snap_tolerance=4.0, segment_separation=2.0)

    near_nodes = [n for n in result["nodes"] if n.get("type") == "self_near_junction"]
    assert len(near_nodes) >= 1, f"Expected self_near_junction nodes, got {len(near_nodes)}"


def test_u_shape_no_split():
    """A smooth U shape with no self-contact should NOT be split."""
    graph = {
        "nodes": [
            {"id": "node_a", "x": 0, "y": 0, "type": "endpoint", "degree": 1},
            {"id": "node_b", "x": 20, "y": 0, "type": "endpoint", "degree": 1},
        ],
        "edges": [
            {
                "id": "edge_u",
                "source": "node_a",
                "target": "node_b",
                "points": [[0, 0], [10, -1], [20, 0]],
                "source_object_ids": ["sat_1"],
            },
        ],
    }

    result = split_self_near_intersections(graph, snap_tolerance=4.0)

    self_nodes = [n for n in result["nodes"] if n.get("type", "").startswith("self_")]
    assert len(self_nodes) == 0, f"U-shape should not split, got {len(self_nodes)} nodes"


def test_rectangle_loop_no_split():
    """A simple rectangle loop should NOT be split at its corners."""
    graph = {
        "nodes": [
            {"id": "node_a", "x": 0, "y": 0, "type": "endpoint", "degree": 1},
            {"id": "node_b", "x": 10, "y": 10, "type": "endpoint", "degree": 1},
        ],
        "edges": [
            {
                "id": "edge_rect",
                "source": "node_a",
                "target": "node_b",
                "points": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                "source_object_ids": ["sat_1"],
            },
        ],
    }

    result = split_self_near_intersections(graph, snap_tolerance=4.0)

    self_nodes = [n for n in result["nodes"] if n.get("type", "").startswith("self_")]
    assert len(self_nodes) == 0, f"Rectangle should not split, got {len(self_nodes)} nodes"
