#!/usr/bin/env python3
"""Regression test: underlay → satin top stitch handoff distance.

Verifies that after satin underlay finishes, the satin top stitching
starts near the underlay's end point — NOT jumping across the full shape.

This tests the _satin_bars_to_continuous_zigzag fix.
"""
import sys
import math
import json

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easystitch_core.stitch_plan import build_stitch_plan
from easystitch_core.fill import _satin_bars_to_continuous_zigzag

errors = 0

def check(name, condition, detail=""):
    global errors
    if condition:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name} — {detail}")
        errors += 1

# ── Test 1: Unit test on _satin_bars_to_continuous_zigzag ──────────
def test_zigzag_alternates_rails():
    """Verify the function produces alternating rail crossings, not side-steps."""
    # Simulate 4 satin bars across a column (start=left, end=right)
    bars = [
        [(0, 0),   (10, 0)],    # bar0: left→right
        [(0, 10),  (10, 10)],   # bar1: left→right
        [(0, 20),  (10, 20)],   # bar2: left→right
        [(0, 30),  (10, 30)],   # bar3: left→right
    ]
    path = _satin_bars_to_continuous_zigzag(bars)

    check("zigzag path has ≥ 2 points", len(path) >= 2, f"got {len(path)}")

    # Check: no two consecutive points should be on the same rail
    # (same rail = same X coordinate)
    same_rail_steps = 0
    for i in range(1, len(path)):
        if abs(path[i][0] - path[i-1][0]) < 1:  # same X = same rail
            same_rail_steps += 1

    check("no same-rail side-steps in path", same_rail_steps == 0,
           f"got {same_rail_steps} same-rail steps")

    # Check: path alternates left→right→left→right
    expected_xs = [0, 10, 0, 10, 0, 10]  # alternating
    actual_xs = [p[0] for p in path]
    check("zigzag alternates rails correctly",
           all(abs(ax - ex) < 1 for ax, ex in zip(actual_xs, expected_xs)),
           f"got x sequence: {actual_xs}")


def test_zigzag_preserves_first_bar_orientation():
    """Verify the first bar's start point is preserved from the ordering function."""
    # Bars where the first one is REVERSED (right→left instead of left→right)
    bars = [
        [(10, 0),  (0, 0)],     # bar0: RIGHT→left (reversed orientation)
        [(0, 10),  (10, 10)],   # bar1: left→right
    ]
    path = _satin_bars_to_continuous_zigzag(bars)

    check("first bar orientation preserved (starts at x=10)",
           len(path) >= 2 and abs(path[0][0] - 10) < 1,
           f"first point: {path[0] if path else 'empty'}")

    # After right→left, should go to opposite rail of next bar
    # bar0 ends at (0,0), bar1 starts at (0,10) — same rail
    # So should cross to bar1's other end: (10,10)
    check("zigzag crosses to opposite rail on bar1",
           len(path) >= 3 and abs(path[2][0] - 10) < 1,
           f"third point: {path[2] if len(path) >= 3 else 'N/A'}")


# ── Test 2: Integration test via stitch plan builder ──────────────
def test_satin_underlay_handoff_distance():
    """Generate a stitch plan with satin + underlay, verify handoff distance is small."""
    # A thin elongated polygon simulating a satin column (like HappySun's smile)
    # Needs to be a proper closed polygon for object_fill_geometry to work
    payload = {
        "svg_w": 500,
        "svg_h": 500,
        "objects": [
            {
                "id": "smile_satin",
                # A thin elongated polygon — 400px wide, 20px tall
                "d": "M50,240 L450,240 L450,260 L50,260 Z",
                "render_mode": "fill",
                "color": "#000000",
                "label": "Smile",
                "tx": 0.0,
                "ty": 0.0,
                "order": 1.0,
                "color_index": 0,
            }
        ],
        "assignments": {"smile_satin": "satin"},
        "manual_rungs": {},
        "settings": {
            "enable_underlay": True,
            "underlay_row_mm": 1.6,
            "underlay_inset_mm": 0.8,
            "underlay_jump_trim_threshold_mm": 5.0,
            "satin_spacing_mm": 0.45,
            "satin_max_width_mm": 12.0,
            "stitch_length_mm": 2.5,
            "row_spacing_mm": 0.4,
            "fill_angle": 45.0,
            "dpi": 96.0,
            "jump_trim_threshold_mm": 3.0,
            "design_scale": {
                "target_longest_mm": 100.0,
                "hoop_width_mm": 200.0,
                "hoop_height_mm": 260.0,
                "hoop_label": "260 × 200 mm (V × H)",
                "scaling_applied": True,
                "svg_to_mm": 0.2,
            },
        },
    }

    plan = build_stitch_plan(payload)
    events = plan.get("events", [])

    # Find the last underlay_satin event and first top_satin event
    last_underlay = None
    first_top_satin = None
    for i, ev in enumerate(events):
        layer = ev.get("layer", "")
        if "underlay_satin" in layer:
            last_underlay = (i, ev)
        if "top_satin" in layer and first_top_satin is None:
            first_top_satin = (i, ev)

    check("underlay events exist", last_underlay is not None)
    check("top satin events exist", first_top_satin is not None)

    if last_underlay and first_top_satin:
        ul_pos = (last_underlay[1].get("x", 0), last_underlay[1].get("y", 0))
        ts_pos = (first_top_satin[1].get("x", 0), first_top_satin[1].get("y", 0))
        dist = math.hypot(ts_pos[0] - ul_pos[0], ts_pos[1] - ul_pos[1])

        # The path is 400px wide (50 to 450). A jump across the full thing
        # would be ~400px. A good handoff should be under 100px (1/4 width).
        max_acceptable = 100.0
        check(f"underlay→satin handoff distance ({dist:.1f}px) < {max_acceptable}px",
              dist < max_acceptable, f"jumped {dist:.1f}px")

        # Also verify there's no jump event between underlay end and satin start
        between_events = events[last_underlay[0]+1:first_top_satin[0]]
        jump_count = sum(1 for e in between_events if e.get("type") == "jump")
        check("no jump events between underlay end and satin start",
              jump_count == 0, f"found {jump_count} jump events")


# ── Run all tests ─────────────────────────────────────────────────
if __name__ == "__main__":
    print("Testing _satin_bars_to_continuous_zigzag unit tests...")
    test_zigzag_alternates_rails()
    test_zigzag_preserves_first_bar_orientation()

    print("\nTesting stitch-plan integration (satin underlay handoff)...")
    test_satin_underlay_handoff_distance()

    print()
    if errors == 0:
        print("✅ All satin handoff regression checks passed")
    else:
        print(f"❌ {errors} check(s) failed")
        sys.exit(1)
