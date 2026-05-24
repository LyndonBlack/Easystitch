#!/usr/bin/env python3
"""Tests for Satin V2 /api/roads/centerline endpoint."""

import base64
from io import BytesIO

from PIL import Image

from app import create_app


def _client():
    app = create_app(None, "/tmp/easystitch_centerline_endpoint_tests")
    return app.test_client()


def test_roads_centerline_endpoint_returns_graph_debug_outputs_and_stats():
    client = _client()
    payload = {
        "svg_w": 120,
        "svg_h": 80,
        "objects": [
            {"id": "satin_a", "d": "M 10 20 L 90 20 L 90 30 L 10 30 Z"},
            {"id": "fill_b", "d": "M 10 50 L 90 50 L 90 60 L 10 60 Z"},
            {"id": "skip_c", "d": "M 95 10 L 115 10 L 115 30 L 95 30 Z"},
        ],
        "assignments": {"satin_a": "satin", "fill_b": "fill", "skip_c": "skip"},
        "settings": {
            "mask_scale": 4,
            "threshold": 128,
            "median_filter": True,
            "min_length_px": 5,
            "simplify_tolerance": 1,
            "snap_distance": 3,
            "despeckle_level": 8,
            "filter_iterations": 4,
            "error_threshold": 2.0,
        },
    }

    res = client.post("/api/roads/centerline", json=payload)
    data = res.get_json()

    assert res.status_code == 200
    assert data["ok"] is True
    assert data["mask"]["satin_object_ids"] == ["satin_a"]
    assert set(data["mask"]["excluded_object_ids"]) == {"fill_b", "skip_c"}

    assert "nodes" in data["graph"]
    assert "edges" in data["graph"]
    assert data["stats"]["satin_object_count"] == 1
    assert data["stats"]["excluded_object_count"] == 2
    assert data["stats"]["raw_polyline_count"] >= data["stats"]["clean_polyline_count"] >= 0
    assert data["stats"]["graph_edge_count"] == len(data["graph"]["edges"])
    assert data["stats"]["graph_node_count"] == len(data["graph"]["nodes"])

    debug = data["debug"]
    assert debug["autotrace_svg"].lstrip().startswith("<?xml") or "<svg" in debug["autotrace_svg"]
    assert debug["overlay_svg"].lstrip().startswith("<svg")

    png_bytes = base64.b64decode(debug["mask_png_base64"])
    image = Image.open(BytesIO(png_bytes))
    assert image.size == (480, 320)


def test_roads_centerline_endpoint_respects_manual_split_child_assignments():
    client = _client()
    payload = {
        "svg_w": 80,
        "svg_h": 40,
        "objects": [
            {"id": "child_1", "d": "M 5 10 L 35 10 L 35 20 L 5 20 Z"},
            {"id": "child_2", "d": "M 45 10 L 75 10 L 75 20 L 45 20 Z"},
        ],
        "assignments": {"parent": "satin", "child_1": "satin", "child_2": "fill"},
        "settings": {"mask_scale": 4, "min_length_px": 5},
    }

    res = client.post("/api/roads/centerline", json=payload)
    data = res.get_json()

    assert res.status_code == 200
    assert data["ok"] is True
    assert data["mask"]["satin_object_ids"] == ["child_1"]
    assert data["mask"]["excluded_object_ids"] == ["child_2"]
    assert data["stats"]["satin_object_count"] == 1
