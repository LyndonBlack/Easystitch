#!/usr/bin/env python3
"""Quick regression test for manual object splitting in geometry.py.

This exercises the distance_point_to_segment fix and ensures
fill/stroke splitting still produces correct output structure.
"""
import sys
sys.path.insert(0, '/tmp/Easystitch')

from refactor.easystitch_core.geometry import (
    distance_point_to_segment,
    manual_split_object,
    split_fill_object_by_line,
    split_stroke_object_by_line,
)

errors = 0

def check(name, condition, detail=""):
    global errors
    if condition:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name} — {detail}")
        errors += 1

# --- distance_point_to_segment ---
d, _ = distance_point_to_segment(0, 0, 1, 0, 0, 1)
check("dist to segment midpoint ~0.7071", abs(d - 0.7071) < 0.001, f"got {d}")

d, _ = distance_point_to_segment(0, 0, 0, 0, 1, 0)
check("dist to endpoint = 0", abs(d) < 0.001, f"got {d}")

d, _ = distance_point_to_segment(5, 0, 0, 0, 1, 0)
check("dist beyond segment = 4", abs(d - 4.0) < 0.001, f"got {d}")

d, _ = distance_point_to_segment(0.5, 0, 0, 0, 1, 0)
check("dist on segment = 0", abs(d) < 0.001, f"got {d}")

# --- manual_split_object (fill) ---
obj = {
    'd': 'M10,10 L200,10 L200,200 L10,200 Z',
    'transform': '',
    'render_mode': 'fill',
    'id': 'test_fill',
    'tx': 0.0, 'ty': 0.0,
    'label': 'T', 'color': '#ff0000',
    'order': 1.0
}
result = manual_split_object(obj, [[10,10], [200,200]])
check("fill split returns 2 parts", len(result) == 2, f"got {len(result)}")
check("fill part[0] has d", bool(result[0].get('d')))
check("fill part[1] has d", bool(result[1].get('d')))
check("fill parts have render_mode", all(r.get('render_mode') == 'fill' for r in result))

# --- manual_split_object (stroke) ---
obj2 = dict(obj, **{
    'render_mode': 'stroke',
    'd': 'M10,50 L200,50 L200,150 L10,150',
    'id': 'test_stroke',
})
try:
    result2 = manual_split_object(obj2, [[10,50], [200,150]])
    check("stroke split returns 2 parts", len(result2) == 2, f"got {len(result2)}")
    check("stroke part[0] has d", bool(result2[0].get('d')))
    check("stroke part[1] has d", bool(result2[1].get('d')))
except Exception as e:
    check("stroke split works", False, str(e))

# Summary
print()
if errors == 0:
    print("✅ All regression checks passed")
else:
    print(f"❌ {errors} check(s) failed")
    sys.exit(1)
