#!/usr/bin/env python3
"""
EasyStitch Core — DST file format export.

Extracted from the golden monolith:
  reference/easystitch_unified_app_v116_continuous_satin_zigzag.py

Functions:
  _dst_encode_record    — Encode one Tajima DST record (3-byte binary)
  _split_dst_delta      — Split large moves into DST-encodable chunks
  _dst_header           — Build 512-byte ASCII DST header
  export_stitch_plan_to_dst — Convert internal stitch plan to DST binary
"""

import math
import os
import re
from pathlib import Path


def _dst_encode_record(dx: int, dy: int, command: str = "stitch") -> bytes:
    """
    Encode one Tajima DST record using the same bit layout used by
    pyembroidery/libembroidery.

    dx/dy are in 0.1 mm units, each within [-121, 121].
    command: stitch, jump, color, stop, end.

    Important: DST's y-axis is inverted at the record-encoding level.
    """
    dx = int(round(dx))
    dy = int(round(dy))

    if command == "end":
        return bytes([0x00, 0x00, 0xF3])
    if command in ("color", "stop"):
        return bytes([0x00, 0x00, 0xC3])

    if dx < -121 or dx > 121 or dy < -121 or dy > 121:
        raise ValueError(f"DST movement out of range: dx={dx}, dy={dy}")

    # Pyembroidery flips the coordinate y space here.
    x = dx
    y = -dy

    b0 = 0
    b1 = 0
    b2 = 0

    if command == "jump":
        b2 |= 1 << 7

    # Stitch/jump low marker bits.
    b2 |= 1 << 0
    b2 |= 1 << 1

    # X: +81/-81, +27/-27, +9/-9, +3/-3, +1/-1
    if x > 40:
        b2 |= 1 << 2
        x -= 81
    if x < -40:
        b2 |= 1 << 3
        x += 81
    if x > 13:
        b1 |= 1 << 2
        x -= 27
    if x < -13:
        b1 |= 1 << 3
        x += 27
    if x > 4:
        b0 |= 1 << 2
        x -= 9
    if x < -4:
        b0 |= 1 << 3
        x += 9
    if x > 1:
        b1 |= 1 << 0
        x -= 3
    if x < -1:
        b1 |= 1 << 1
        x += 3
    if x > 0:
        b0 |= 1 << 0
        x -= 1
    if x < 0:
        b0 |= 1 << 1
        x += 1
    if x != 0:
        raise ValueError(f"Could not encode DST dx component: {dx}")

    # Y: +81/-81, +27/-27, +9/-9, +3/-3, +1/-1
    if y > 40:
        b2 |= 1 << 5
        y -= 81
    if y < -40:
        b2 |= 1 << 4
        y += 81
    if y > 13:
        b1 |= 1 << 5
        y -= 27
    if y < -13:
        b1 |= 1 << 4
        y += 27
    if y > 4:
        b0 |= 1 << 5
        y -= 9
    if y < -4:
        b0 |= 1 << 4
        y += 9
    if y > 1:
        b1 |= 1 << 7
        y -= 3
    if y < -1:
        b1 |= 1 << 6
        y += 3
    if y > 0:
        b0 |= 1 << 7
        y -= 1
    if y < 0:
        b0 |= 1 << 6
        y += 1
    if y != 0:
        raise ValueError(f"Could not encode DST dy component: {dy}")

    return bytes([b0, b1, b2])


def _split_dst_delta(dx: int, dy: int):
    """
    Split a large relative move into DST-encodable chunks.
    """
    dx = int(round(dx))
    dy = int(round(dy))
    chunks = []
    while dx != 0 or dy != 0:
        sx = max(-121, min(121, dx))
        sy = max(-121, min(121, dy))
        chunks.append((sx, sy))
        dx -= sx
        dy -= sy
    if not chunks:
        chunks.append((0, 0))
    return chunks


def _dst_header(label: str, record_count: int, color_changes: int,
                min_x: int, max_x: int, min_y: int, max_y: int) -> bytes:
    """
    Build 512 byte DST ASCII header.
    Extents are in 0.1mm units.
    """
    label = re.sub(r"[^A-Za-z0-9_ -]", "_", str(label or "EASYSTITCH"))[:16]
    lines = [
        f"LA:{label}\\\r",
        f"ST:{int(record_count):7d}\\\r",
        f"CO:{int(color_changes):3d}\\\r",
        f"+X:{max(0, max_x):5d}\\\r",
        f"-X:{abs(min(0, min_x)):5d}\\\r",
        f"+Y:{max(0, max_y):5d}\\\r",
        f"-Y:{abs(min(0, min_y)):5d}\\\r",
        "AX:+00000\\\r",
        "AY:+00000\\\r",
        "MX:+00000\\\r",
        "MY:+00000\\\r",
        "PD:******\\\r",
    ]
    data = "".join(lines).encode("ascii", "replace")
    if len(data) > 512:
        data = data[:512]
    return data + b" " * (512 - len(data))


def export_stitch_plan_to_dst(plan: dict, filename: str = "easystitch.dst",
                              settings: dict | None = None) -> tuple[bytes, dict, dict]:
    """
    Convert EasyStitch internal stitch plan to a simple DST binary.

    Coordinates:
      SVG px -> mm using plan/settings dpi
      mm -> DST 0.1mm units
      centred around the SVG viewBox centre
      Y is flipped so embroidery positive Y is upward
    """
    if not plan or not isinstance(plan, dict):
        raise ValueError("No stitch plan supplied.")

    settings = settings or {}
    dpi = float(settings.get("dpi") or plan.get("settings", {}).get("dpi") or 96.0)
    svg_w = float(plan.get("svg_w") or plan.get("stats", {}).get("estimated_width_px") or 500)
    svg_h = float(plan.get("svg_h") or plan.get("stats", {}).get("estimated_height_px") or 500)
    px_to_dst = (25.4 / dpi) * 10.0

    events = plan.get("events") or []
    if not events:
        raise ValueError("Stitch plan has no events.")

    current = (0, 0)
    records = []
    positions = [(0, 0)]
    debug_records = []
    debug_trims = []
    debug_color_changes = []
    debug_long_moves = []
    color_changes = 0
    trims = 0
    jumps = 0
    stitches = 0

    def to_dst_point(ev):
        x = float(ev.get("x", 0.0))
        y = float(ev.get("y", 0.0))
        # Centre coordinates.  The DST record encoder performs the required
        # y-axis inversion, matching pyembroidery's writer.
        dxu = int(round((x - svg_w / 2.0) * px_to_dst))
        dyu = int(round((y - svg_h / 2.0) * px_to_dst))
        return dxu, dyu

    def emit_move_to(target, command, source_event=None):
        nonlocal current, jumps, stitches
        start = current
        total_dx = target[0] - current[0]
        total_dy = target[1] - current[1]
        move_len = math.hypot(total_dx, total_dy)
        chunks = _split_dst_delta(total_dx, total_dy)

        if move_len > 30:  # >3mm, useful for debug even when valid
            debug_long_moves.append({
                "from_01mm": list(start),
                "to_01mm": list(target),
                "dx_01mm": total_dx,
                "dy_01mm": total_dy,
                "length_mm": round(move_len / 10.0, 3),
                "command": command,
                "source_type": (source_event or {}).get("type"),
                "source_layer": (source_event or {}).get("layer"),
                "object_id": (source_event or {}).get("object_id"),
            })

        for i, (sx, sy) in enumerate(chunks):
            rec_cmd = command
            records.append(_dst_encode_record(sx, sy, rec_cmd))
            current = (current[0] + sx, current[1] + sy)
            positions.append(current)
            debug_records.append({
                "record_index": len(records),
                "command": rec_cmd,
                "dx_01mm": sx,
                "dy_01mm": sy,
                "x_01mm": current[0],
                "y_01mm": current[1],
                "source_type": (source_event or {}).get("type"),
                "source_layer": (source_event or {}).get("layer"),
                "object_id": (source_event or {}).get("object_id"),
            })
            if rec_cmd == "jump":
                jumps += 1
            elif rec_cmd == "stitch":
                stitches += 1

    for ev in events:
        et = ev.get("type")
        if et == "color_change":
            records.append(_dst_encode_record(0, 0, "color"))
            color_changes += 1
            debug_color_changes.append({
                "record_index": len(records),
                "color": ev.get("color"),
                "object_id": ev.get("object_id"),
                "layer": ev.get("layer"),
            })
            debug_records.append({
                "record_index": len(records),
                "command": "color",
                "dx_01mm": 0,
                "dy_01mm": 0,
                "x_01mm": current[0],
                "y_01mm": current[1],
                "source_type": et,
                "source_layer": ev.get("layer"),
                "object_id": ev.get("object_id"),
            })
            continue

        if et == "trim":
            # DST has no universal explicit trim.  Three zero-length jumps are
            # commonly used as a trim hint; many machines/software trim on
            # subsequent jumps according to machine settings.
            trim_record_start = len(records) + 1
            for _ in range(3):
                records.append(_dst_encode_record(0, 0, "jump"))
                debug_records.append({
                    "record_index": len(records),
                    "command": "trim_hint_jump",
                    "dx_01mm": 0,
                    "dy_01mm": 0,
                    "x_01mm": current[0],
                    "y_01mm": current[1],
                    "source_type": et,
                    "source_layer": ev.get("layer"),
                    "object_id": ev.get("object_id"),
                    "reason": ev.get("reason"),
                })
            debug_trims.append({
                "record_index": trim_record_start,
                "x_01mm": current[0],
                "y_01mm": current[1],
                "object_id": ev.get("object_id"),
                "color": ev.get("color"),
                "reason": ev.get("reason"),
                "distance_px": ev.get("distance_px"),
            })
            trims += 1
            jumps += 3
            continue

        if et not in ("move", "jump", "stitch"):
            continue

        target = to_dst_point(ev)
        if et == "stitch":
            emit_move_to(target, "stitch", ev)
        else:
            emit_move_to(target, "jump", ev)

    records.append(_dst_encode_record(0, 0, "end"))

    if positions:
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    else:
        min_x = max_x = min_y = max_y = 0

    label = Path(filename or "easystitch.dst").stem
    header = _dst_header(label, len(records), color_changes, min_x, max_x, min_y, max_y)
    dst_bytes = header + b"".join(records)

    stats = {
        "records": len(records),
        "stitches": stitches,
        "jumps": jumps,
        "trims": trims,
        "color_changes": color_changes,
        "min_x_01mm": min_x,
        "max_x_01mm": max_x,
        "min_y_01mm": min_y,
        "max_y_01mm": max_y,
        "width_mm": round((max_x - min_x) / 10.0, 2),
        "height_mm": round((max_y - min_y) / 10.0, 2),
        "format": "DST",
        "note": "DST has limited colour metadata and no guaranteed explicit trim command; trim events are encoded as jump hints. DST bit encoding follows pyembroidery/libembroidery layout."
    }
    debug = {
        "version": "easystitch-export-debug-v1",
        "format": "DST",
        "filename": str(filename or "easystitch.dst"),
        "stats": stats,
        "source_plan_stats": plan.get("stats", {}),
        "coordinate_scale": {
            "dpi": dpi,
            "px_to_01mm": px_to_dst,
            "svg_w": svg_w,
            "svg_h": svg_h,
            "centered": True,
            "design_scale": plan.get("settings", {}).get("design_scale", {}),
        },
        "trim_hints": debug_trims,
        "color_changes": debug_color_changes,
        "long_moves_over_3mm": debug_long_moves,
        # Keep the per-record list capped to avoid huge browser JSON in large
        # designs.  The counts/stats above remain complete.
        "records_sample_first_5000": debug_records[:5000],
        "records_total": len(debug_records),
        "notes": [
            "DST stores colour changes/stops but not rich thread palette metadata.",
            "DST trim events are encoded as three zero-length jump records as trim hints.",
            "Use VP3 or another richer format later for stronger trim/color metadata on compatible machines."
        ],
    }

    return dst_bytes, stats, debug
