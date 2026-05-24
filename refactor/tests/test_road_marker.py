#!/usr/bin/env python3
"""Checklist-aligned tests for Satin V2 road marker pipeline."""

import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easystitch_core.road_marker import (
    build_centerline_graph,
    build_road_graph_overlay_svg,
    clean_centerline_polylines,
    collect_satin_objects,
    parse_centerline_svg_to_polylines,
    render_satin_mask,
    run_autotrace_centerline,
)


def test_only_satin_objects_included():
    objects = [
        {"id": "path_a", "d": "M 0 0 L 10 0 L 10 10 Z"},
        {"id": "path_b", "d": "M 20 0 L 30 0 L 30 10 Z"},
        {"id": "path_c", "d": "M 40 0 L 50 0 L 50 10 Z"},
    ]
    assignments = {"path_a": "satin", "path_b": "fill", "path_c": "skip"}

    result = collect_satin_objects(objects, assignments)

    assert [obj["id"] for obj in result] == ["path_a"]


def test_split_child_assignments_respected():
    objects = [
        {"id": "child_1", "d": "M 0 0 L 10 0 L 10 10 Z"},
        {"id": "child_2", "d": "M 20 0 L 30 0 L 30 10 Z"},
    ]
    assignments = {"parent": "satin", "child_1": "satin", "child_2": "fill"}

    mask = render_satin_mask(objects, assignments, svg_w=40, svg_h=20, scale=2)

    assert mask["satin_object_ids"] == ["child_1"]
    assert mask["excluded_object_ids"] == ["child_2"]


def test_mask_dimensions():
    mask = render_satin_mask([], {}, svg_w=500, svg_h=400, scale=4)

    assert mask["width_px"] == 2000
    assert mask["height_px"] == 1600
    assert mask["scale"] == 4


def test_missing_autotrace_gives_clear_error():
    image = Image.new("L", (20, 20), 255)

    with pytest.raises(RuntimeError, match="AutoTrace not found"):
        run_autotrace_centerline(image, autotrace_path="/missing/autotrace")


def test_parse_centerline_svg_to_polylines_supports_polyline_line_and_path():
    svg_text = """
    <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">
      <polyline points="0,0 20,0 20,20" />
      <line x1="0" y1="10" x2="20" y2="10" />
      <path d="M 0 20 L 10 20 L 20 30" />
    </svg>
    """

    polylines = parse_centerline_svg_to_polylines(svg_text, scale=2)

    assert len(polylines) == 3
    assert polylines[0]["points"] == [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]]
    assert polylines[1]["points"] == [[0.0, 5.0], [10.0, 5.0]]
    assert polylines[2]["points"][0] == [0.0, 10.0]
    assert polylines[2]["points"][-1] == [10.0, 15.0]


def test_parse_centerline_svg_to_polylines_splits_multi_move_paths():
    svg_text = """
    <svg xmlns="http://www.w3.org/2000/svg" width="100" height="40">
      <path d="M 0 0 L 10 0 M 50 0 L 60 0 M 80 0 L 90 0" />
    </svg>
    """

    polylines = parse_centerline_svg_to_polylines(svg_text, scale=1)

    assert len(polylines) == 3
    assert polylines[0]["points"] == [[0.0, 0.0], [10.0, 0.0]]
    assert polylines[1]["points"] == [[50.0, 0.0], [60.0, 0.0]]
    assert polylines[2]["points"] == [[80.0, 0.0], [90.0, 0.0]]


def test_parse_real_autotrace_svg_does_not_connect_separate_subpaths():
    fixture = os.path.abspath(os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "test_assets",
        "autotrace_review",
        "test_png_direct_autotrace_centerline_raw.svg",
    ))
    if not os.path.exists(fixture):
        pytest.skip("AutoTrace review fixture has not been generated yet")

    svg_text = open(fixture, "r", encoding="utf-8").read()
    polylines = parse_centerline_svg_to_polylines(svg_text, scale=1)

    assert len(polylines) > 1

    graph = build_centerline_graph(polylines, snap_distance=3.0)
    assert len(graph["edges"]) == len(polylines)


def test_clean_real_autotrace_fixture_preserves_both_closed_cheek_loops():
    fixture = os.path.abspath(os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "test_assets",
        "autotrace_review",
        "test_png_direct_autotrace_centerline_raw.svg",
    ))
    if not os.path.exists(fixture):
        pytest.skip("AutoTrace review fixture has not been generated yet")

    svg_text = open(fixture, "r", encoding="utf-8").read()
    raw = parse_centerline_svg_to_polylines(svg_text, scale=1)
    cleaned = clean_centerline_polylines(raw, min_length_px=5.0, simplify_tolerance=1.0)

    assert len(raw) == 6
    assert len(cleaned) == 6

    boxes = []
    for polyline in cleaned:
        points = polyline["points"]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))

    assert any(130 <= x1 <= 140 and 210 <= y1 <= 220 and 165 <= x2 <= 175 and 230 <= y2 <= 240 for x1, y1, x2, y2 in boxes)
    assert any(345 <= x1 <= 355 and 210 <= y1 <= 220 and 385 <= x2 <= 395 and 230 <= y2 <= 240 for x1, y1, x2, y2 in boxes)


def test_clean_centerline_polylines_removes_tiny_paths_and_preserves_endpoints():
    polylines = [
        {"id": "short", "points": [[0, 0], [1, 0]], "length": 1.0},
        {"id": "long", "points": [[0, 0], [5, 0.2], [10, 0]], "length": 10.01},
    ]

    cleaned = clean_centerline_polylines(polylines, min_length_px=5.0, simplify_tolerance=0.5)

    assert [line["id"] for line in cleaned] == ["long"]
    assert cleaned[0]["points"][0] == [0, 0]
    assert cleaned[0]["points"][-1] == [10, 0]


def test_graph_node_classification_for_three_lines_meeting_at_endpoint():
    polylines = [
        {"id": "a", "points": [[0, 0], [10, 10]], "length": 14.14},
        {"id": "b", "points": [[10.5, 10.2], [20, 10]], "length": 9.5},
        {"id": "c", "points": [[9.8, 10.4], [10, 20]], "length": 9.6},
    ]

    graph = build_centerline_graph(polylines, snap_distance=1.0)

    junctions = [node for node in graph["nodes"] if node["type"] == "junction"]
    assert len(junctions) == 1
    assert junctions[0]["degree"] == 3
    assert len(graph["edges"]) == 3
    assert all(edge["priority"] is None for edge in graph["edges"])
    assert all(edge["assignment"] == "unmarked" for edge in graph["edges"])


def test_overlay_svg_contains_satin_edges_and_nodes():
    satin_objects = [{"id": "s1", "d": "M 0 0 L 20 0 L 20 20 Z", "tx": 0, "ty": 0}]
    graph = {
        "nodes": [
            {"id": "node_1", "x": 0, "y": 0, "type": "endpoint", "degree": 1},
            {"id": "node_2", "x": 20, "y": 20, "type": "junction", "degree": 3},
        ],
        "edges": [
            {"id": "edge_1", "source": "node_1", "target": "node_2", "points": [[0, 0], [20, 20]], "length": 28.28},
        ],
    }

    svg = build_road_graph_overlay_svg(30, 30, satin_objects, graph)

    assert "<svg" in svg
    assert "data-edge-id=\"edge_1\"" in svg
    assert "data-node-id=\"node_1\"" in svg
    assert "data-node-id=\"node_2\"" in svg
    assert "#00d5ff" in svg or "cyan" in svg
