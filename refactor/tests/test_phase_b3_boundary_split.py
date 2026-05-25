#!/usr/bin/env python3
"""Tests for boundary-split-before-simplify reorder and bisection."""

import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easystitch_core.road_marker import (
    _bisect_boundary,
    _sample_label_at,
    clean_centerline_polylines,
    split_polylines_at_object_boundaries,
)


def _make_label_map(width, height, scale, left_label, right_label, boundary_x):
    """Create a label map image with different labels on left/right of boundary_x."""
    w = int(width * scale)
    h = int(height * scale)
    img = Image.new("L", (w, h), 0)
    bpx = int(boundary_x * scale)
    for y in range(h):
        for x in range(w):
            img.putpixel((x, y), left_label if x < bpx else right_label)
    return img


def test_bisection_finds_accurate_boundary():
    """A 100px-wide label map with label 1 on left half and 2 on right half.
    Raw polyline points at x=10 and x=90 cross the boundary at x=50.
    Bisection should find the boundary near x=50, not the midpoint between
    the two sample points (which would be x=50 anyway since they're symmetric).
    Use asymmetric points to test: from x=10 (label 1) to x=90 (label 2).
    The true boundary is at x=50. Bisection should find it within 1px."""
    scale = 2.0
    label_map = _make_label_map(100, 10, scale, 1, 2, 50)

    a = [10.0, 5.0]
    b = [90.0, 5.0]
    result = _bisect_boundary(label_map, scale, a, b, 1, iterations=10)

    # Should be very close to x=50
    assert abs(result[0] - 50.0) < 1.0, f"Boundary at x={result[0]}, expected ~50"


def test_bisection_asymmetric():
    """Points at x=30 and x=80 with boundary at x=50. Bisection should
    find the boundary near x=50, not at the midpoint (55)."""
    scale = 1.0
    label_map = _make_label_map(100, 10, scale, 1, 2, 50)

    a = [30.0, 5.0]
    b = [80.0, 5.0]
    result = _bisect_boundary(label_map, scale, a, b, 1, iterations=10)

    assert abs(result[0] - 50.0) < 1.0, f"Boundary at x={result[0]}, expected ~50"


def test_bisection_steep_angle():
    """Points crossing the boundary at a steep diagonal angle."""
    scale = 2.0
    label_map = _make_label_map(100, 100, scale, 1, 2, 50)

    # Diagonal crossing from (20, 80) to (80, 20), boundary at x=50
    a = [20.0, 80.0]
    b = [80.0, 20.0]
    result = _bisect_boundary(label_map, scale, a, b, 1, iterations=10)

    # The crossing point should be near x=50, y=50
    assert abs(result[0] - 50.0) < 1.0, f"Boundary x at {result[0]}, expected ~50"
    assert abs(result[1] - 50.0) < 2.0, f"Boundary y at {result[1]}, expected ~50"


def test_split_before_simplify_preserves_corner_accuracy():
    """A raw polyline with a sharp corner near a label boundary.
    Splitting before simplification should preserve the corner."""
    scale = 4.0
    objects = [
        {"id": "left", "d": "M 0 0 L 60 0 L 60 100 L 0 100 Z"},
        {"id": "right", "d": "M 60 0 L 100 0 L 100 100 L 60 100 Z"},
    ]
    assignments = {"left": "satin", "right": "satin"}
    svg_w = 100.0
    svg_h = 100.0

    # Polyline that goes from left object through the boundary at x=60
    # with a sharp corner at (55, 10) in the left object
    raw_polylines = [{
        "id": "cline_1",
        "points": [[20.0, 10.0], [55.0, 10.0], [55.0, 10.5], [55.0, 11.0],
                    [60.0, 11.0], [80.0, 11.0]],
        "length": 60.0,
    }]

    split_result = split_polylines_at_object_boundaries(
        raw_polylines, objects, assignments,
        svg_w, svg_h, scale=scale,
    )

    # Should split at the boundary near x=60
    assert len(split_result) == 2, f"Expected 2 segments, got {len(split_result)}"
    # First segment should end near x=60 (the boundary)
    first_end = split_result[0]["points"][-1]
    assert abs(first_end[0] - 60.0) <= 5.0, \
        f"First segment end at x={first_end[0]}, expected ~60"

    # After clean/simplify, the child endpoints should still be at the boundary
    cleaned = clean_centerline_polylines(
        split_result, min_length_px=2.0, simplify_tolerance=0.5,
    )
    for child in cleaned:
        first = child["points"][0]
        last = child["points"][-1]
        # One of the children should start or end near x=60
        for pt in [first, last]:
            if abs(pt[0] - 60.0) <= 3.0:
                break
        else:
            pytest.fail(f"No endpoint near x=60 in child: ends at ({last[0]:.1f}, {last[1]:.1f})")
