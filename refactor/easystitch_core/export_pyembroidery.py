#!/usr/bin/env python3
"""
EasyStitch pyembroidery export — JEF and VP3 machine-format writers.

Provides generic pyembroidery-based export for secondary machine formats
(JEF, VP3, etc.) that pyembroidery supports natively.

Dependencies:
    pip install pyembroidery
"""

import math
import os
import tempfile
from pathlib import Path


def export_stitch_plan_to_pyembroidery_format(plan: dict, filename: str,
                                             fmt: str,
                                             settings: dict | None = None,
                                             trim_anchor: bool = False) -> tuple[bytes, dict, dict]:
    """
    Generic pyembroidery writer for secondary machine formats such as JEF/VP3.

    Coordinates:
      SVG units -> 0.1mm units using the plan/settings effective DPI
      centred around the SVG viewBox centre
    """
    if not plan or not isinstance(plan, dict):
        raise ValueError("No stitch plan supplied.")

    try:
        import pyembroidery
    except Exception as e:
        raise RuntimeError(
            f"{fmt.upper()} export requires pyembroidery. Install it with: pip install pyembroidery"
        ) from e

    settings = settings or {}
    dpi = float(settings.get("dpi") or plan.get("settings", {}).get("dpi") or 96.0)
    svg_w = float(plan.get("svg_w") or plan.get("stats", {}).get("estimated_width_px") or 500)
    svg_h = float(plan.get("svg_h") or plan.get("stats", {}).get("estimated_height_px") or 500)
    px_to_01mm = (25.4 / dpi) * 10.0

    events = plan.get("events") or []
    if not events:
        raise ValueError("Stitch plan has no events.")

    pattern = pyembroidery.EmbPattern()

    color_order = []
    for ev in events:
        if ev.get("type") == "color_change":
            c = str(ev.get("color") or "#000000")
            if c not in color_order:
                color_order.append(c)
    if not color_order:
        color_order = ["#000000"]

    def parse_hex_colour(hex_color: str) -> tuple[int, int, int]:
        h = (hex_color or "#000000").strip()
        if h.startswith("#"):
            h = h[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) != 6:
            return (0, 0, 0)
        try:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except Exception:
            return (0, 0, 0)

    try:
        EmbThread = getattr(pyembroidery, "EmbThread", None)
        for c in color_order:
            rgb = parse_hex_colour(c)
            if EmbThread is not None:
                th = EmbThread()
                try:
                    th.set_color(rgb[0], rgb[1], rgb[2])
                except Exception:
                    th.color = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
                th.description = c
                pattern.add_thread(th)
            else:
                pattern.add_thread({"color": (rgb[0] << 16) | (rgb[1] << 8) | rgb[2], "description": c})
    except Exception:
        pass

    STITCH = getattr(pyembroidery, "STITCH", 0)
    JUMP = getattr(pyembroidery, "JUMP", 1)
    TRIM = getattr(pyembroidery, "TRIM", 2)
    COLOR_CHANGE = getattr(pyembroidery, "COLOR_CHANGE", getattr(pyembroidery, "STOP", 4))
    END = getattr(pyembroidery, "END", 8)

    debug_records = []
    debug_trims = []
    debug_color_changes = []
    debug_long_moves = []
    positions = []
    color_changes = 0
    trims = 0
    jumps = 0
    stitches = 0
    records = 0
    current = None

    def to_01mm_point(ev):
        x = float(ev.get("x", 0.0))
        y = float(ev.get("y", 0.0))
        return int(round((x - svg_w / 2.0) * px_to_01mm)), int(round((y - svg_h / 2.0) * px_to_01mm))

    def add_abs(command, x, y, source_event=None):
        nonlocal records, jumps, stitches, current
        try:
            pattern.add_stitch_absolute(command, x, y)
        except AttributeError:
            pattern.add_stitch(command, x, y)
        records += 1
        if command == JUMP:
            jumps += 1
        elif command == STITCH:
            stitches += 1
        if current is not None:
            dist = math.hypot(x - current[0], y - current[1])
            if dist > 30:
                debug_long_moves.append({
                    "from_01mm": list(current),
                    "to_01mm": [x, y],
                    "length_mm": round(dist / 10.0, 3),
                    "command": "jump" if command == JUMP else "stitch",
                    "source_type": (source_event or {}).get("type"),
                    "source_layer": (source_event or {}).get("layer"),
                    "object_id": (source_event or {}).get("object_id"),
                })
        current = (x, y)
        positions.append(current)
        debug_records.append({
            "record_index": records,
            "command": "jump" if command == JUMP else "stitch",
            "x_01mm": x,
            "y_01mm": y,
            "source_type": (source_event or {}).get("type"),
            "source_layer": (source_event or {}).get("layer"),
            "object_id": (source_event or {}).get("object_id"),
        })

    def add_command_at_current(command, source_event=None):
        nonlocal records, current
        x, y = current if current is not None else (0, 0)
        try:
            pattern.add_stitch_absolute(command, x, y)
        except AttributeError:
            pattern.add_stitch(command, x, y)
        records += 1
        debug_records.append({
            "record_index": records,
            "command": "trim" if command == TRIM else ("color" if command == COLOR_CHANGE else "command"),
            "x_01mm": x,
            "y_01mm": y,
            "source_type": (source_event or {}).get("type"),
            "source_layer": (source_event or {}).get("layer"),
            "object_id": (source_event or {}).get("object_id"),
        })

    for ev in events:
        et = ev.get("type")

        if et == "color_change":
            add_command_at_current(COLOR_CHANGE, ev)
            color_changes += 1
            debug_color_changes.append({
                "event_index": len(debug_records),
                "color": ev.get("color"),
                "at_01mm": list(current or (0, 0)),
            })
            continue

        if et == "trim":
            if trim_anchor and current is not None:
                add_abs(STITCH, current[0], current[1], {
                    "type": "stitch",
                    "layer": f"{fmt}_trim_anchor",
                    "object_id": ev.get("object_id"),
                    "color": ev.get("color"),
                })
            add_command_at_current(TRIM, ev)
            trims += 1
            debug_trims.append({
                "event_index": len(debug_records),
                "object_id": ev.get("object_id"),
                "color": ev.get("color"),
                "at_01mm": list(current or (0, 0)),
                "trim_anchor": bool(trim_anchor),
            })
            continue

        if et in ("move", "jump"):
            x, y = to_01mm_point(ev)
            add_abs(JUMP, x, y, ev)
            continue

        if et == "stitch":
            x, y = to_01mm_point(ev)
            add_abs(STITCH, x, y, ev)
            continue

    add_command_at_current(END, {"type": "end"})

    tmp_path = None
    ext = "." + fmt.lower().lstrip(".")
    try:
        with tempfile.NamedTemporaryFile(prefix=f"easystitch_{fmt.lower()}_", suffix=ext, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            pyembroidery.write(pattern, tmp_path, settings={"name": Path(filename).stem[:8] or "EASY"})
        except TypeError:
            pyembroidery.write(pattern, tmp_path)

        with open(tmp_path, "rb") as f:
            out_bytes = f.read()
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    if not out_bytes:
        raise RuntimeError(f"pyembroidery {fmt.upper()} writer returned an empty file.")

    if positions:
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    else:
        min_x = max_x = min_y = max_y = 0

    stats = {
        "records": records,
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
        "format": fmt.upper(),
        "note": f"{fmt.upper()} written through pyembroidery. Exact trim/colour behaviour depends on the reader/machine."
    }

    debug = {
        "version": "easystitch-export-debug-v1",
        "format": fmt.upper(),
        "filename": str(filename or f"easystitch.{fmt.lower()}"),
        "stats": stats,
        "source_plan_stats": plan.get("stats", {}),
        "coordinate_scale": {
            "dpi": dpi,
            "px_to_01mm": px_to_01mm,
            "svg_w": svg_w,
            "svg_h": svg_h,
            "centered": True,
            "design_scale": plan.get("settings", {}).get("design_scale", {}),
        },
        "trim_hints": debug_trims,
        "color_changes": debug_color_changes,
        "long_moves_over_3mm": debug_long_moves,
        "records_sample_first_5000": debug_records[:5000],
        "records_total": len(debug_records),
        "notes": [
            f"{fmt.upper()} export requires pyembroidery in the Python environment.",
            "Use DST as the known-good comparison export.",
            "If a viewer shows connector lines or missing trim-boundary stitches, compare with the debug JSON and DST."
        ],
    }

    return out_bytes, stats, debug


def export_stitch_plan_to_jef(plan: dict, filename: str = "easystitch.jef",
                              settings: dict | None = None) -> tuple[bytes, dict, dict]:
    return export_stitch_plan_to_pyembroidery_format(
        plan, filename=filename, fmt="jef", settings=settings, trim_anchor=False
    )


def export_stitch_plan_to_vp3(plan: dict, filename: str = "easystitch.vp3",
                              settings: dict | None = None) -> tuple[bytes, dict, dict]:
    """
    Convert EasyStitch internal stitch plan to VP3 using pyembroidery.

    pyembroidery is the same general library family used by Ink/Stitch for
    many machine-format exports.  It must be installed in the Python
    environment: pip install pyembroidery

    Coordinates:
      SVG units -> 0.1mm units using the plan/settings effective DPI
      centred around the SVG viewBox centre
    """
    if not plan or not isinstance(plan, dict):
        raise ValueError("No stitch plan supplied.")

    try:
        import pyembroidery
    except Exception as e:
        raise RuntimeError(
            "VP3 export requires pyembroidery. Install it with: pip install pyembroidery"
        ) from e

    settings = settings or {}
    dpi = float(settings.get("dpi") or plan.get("settings", {}).get("dpi") or 96.0)
    svg_w = float(plan.get("svg_w") or plan.get("stats", {}).get("estimated_width_px") or 500)
    svg_h = float(plan.get("svg_h") or plan.get("stats", {}).get("estimated_height_px") or 500)
    px_to_01mm = (25.4 / dpi) * 10.0

    events = plan.get("events") or []
    if not events:
        raise ValueError("Stitch plan has no events.")

    pattern = pyembroidery.EmbPattern()

    # Build a simple colour/thread map. VP3 can carry richer colour information
    # than DST, though exact machine display still depends on the reader.
    color_order = []
    for ev in events:
        if ev.get("type") == "color_change":
            c = str(ev.get("color") or "#000000")
            if c not in color_order:
                color_order.append(c)
    if not color_order:
        color_order = ["#000000"]

    def parse_hex_colour(hex_color: str) -> tuple[int, int, int]:
        h = (hex_color or "#000000").strip()
        if h.startswith("#"):
            h = h[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) != 6:
            return (0, 0, 0)
        try:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except Exception:
            return (0, 0, 0)

    try:
        EmbThread = getattr(pyembroidery, "EmbThread", None)
        for c in color_order:
            rgb = parse_hex_colour(c)
            if EmbThread is not None:
                th = EmbThread()
                # pyembroidery accepts thread colour as integer RGB in most versions.
                try:
                    th.set_color(rgb[0], rgb[1], rgb[2])
                except Exception:
                    th.color = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
                th.description = c
                pattern.add_thread(th)
            else:
                pattern.add_thread({"color": (rgb[0] << 16) | (rgb[1] << 8) | rgb[2], "description": c})
    except Exception:
        # Thread metadata is useful but not worth failing export over.
        pass

    STITCH = getattr(pyembroidery, "STITCH", 0)
    JUMP = getattr(pyembroidery, "JUMP", 1)
    TRIM = getattr(pyembroidery, "TRIM", 2)
    COLOR_CHANGE = getattr(pyembroidery, "COLOR_CHANGE", getattr(pyembroidery, "STOP", 4))
    END = getattr(pyembroidery, "END", 8)

    debug_records = []
    debug_trims = []
    debug_color_changes = []
    debug_long_moves = []
    positions = []
    color_changes = 0
    trims = 0
    jumps = 0
    stitches = 0
    records = 0
    current = None

    def to_01mm_point(ev):
        x = float(ev.get("x", 0.0))
        y = float(ev.get("y", 0.0))
        # Match our DST centring convention.  Let pyembroidery's VP3 writer
        # handle the file-format coordinate details.
        return int(round((x - svg_w / 2.0) * px_to_01mm)), int(round((y - svg_h / 2.0) * px_to_01mm))

    def add_abs(command, x, y, source_event=None):
        nonlocal records, jumps, stitches, current
        try:
            pattern.add_stitch_absolute(command, x, y)
        except AttributeError:
            pattern.add_stitch(command, x, y)
        records += 1
        if command == JUMP:
            jumps += 1
        elif command == STITCH:
            stitches += 1
        if current is not None:
            dist = math.hypot(x - current[0], y - current[1])
            if dist > 30:
                debug_long_moves.append({
                    "from_01mm": list(current),
                    "to_01mm": [x, y],
                    "length_mm": round(dist / 10.0, 3),
                    "command": "jump" if command == JUMP else "stitch",
                    "source_type": (source_event or {}).get("type"),
                    "source_layer": (source_event or {}).get("layer"),
                    "object_id": (source_event or {}).get("object_id"),
                })
        current = (x, y)
        positions.append(current)
        debug_records.append({
            "record_index": records,
            "command": "jump" if command == JUMP else "stitch",
            "x_01mm": x,
            "y_01mm": y,
            "source_type": (source_event or {}).get("type"),
            "source_layer": (source_event or {}).get("layer"),
            "object_id": (source_event or {}).get("object_id"),
        })

    def add_command_at_current(command, source_event=None):
        """
        pyembroidery's VP3 writer is more reliable when non-stitch commands are
        tied to the current absolute coordinate. Command-only calls can be
        interpreted by some writers/viewers as happening at an implicit origin,
        creating stray connector lines and, in testing, missed tiny colour
        details after colour changes.
        """
        nonlocal records, current
        x, y = current if current is not None else (0, 0)
        try:
            pattern.add_stitch_absolute(command, x, y)
        except AttributeError:
            pattern.add_stitch(command, x, y)
        records += 1
        debug_records.append({
            "record_index": records,
            "command": "trim" if command == TRIM else ("color" if command == COLOR_CHANGE else "command"),
            "x_01mm": x,
            "y_01mm": y,
            "source_type": (source_event or {}).get("type"),
            "source_layer": (source_event or {}).get("layer"),
            "object_id": (source_event or {}).get("object_id"),
        })

    for ev in events:
        et = ev.get("type")

        if et == "color_change":
            add_command_at_current(COLOR_CHANGE, ev)
            color_changes += 1
            debug_color_changes.append({
                "event_index": len(debug_records),
                "color": ev.get("color"),
                "at_01mm": list(current or (0, 0)),
            })
            continue

        if et == "trim":
            # VP3/pyembroidery viewers can visually drop the final stitch before
            # a trim if the trim command lands exactly on that last point. Add a
            # zero-length stitch at the current point first as a conservative
            # anchor, then emit the trim at the same coordinate.
            if current is not None:
                add_abs(STITCH, current[0], current[1], {
                    "type": "stitch",
                    "layer": "vp3_trim_anchor",
                    "object_id": ev.get("object_id"),
                    "color": ev.get("color"),
                })
            add_command_at_current(TRIM, ev)
            trims += 1
            debug_trims.append({
                "event_index": len(debug_records),
                "object_id": ev.get("object_id"),
                "color": ev.get("color"),
                "at_01mm": list(current or (0, 0)),
                "vp3_trim_anchor": True,
            })
            continue

        if et in ("move", "jump"):
            x, y = to_01mm_point(ev)
            add_abs(JUMP, x, y, ev)
            continue

        if et == "stitch":
            x, y = to_01mm_point(ev)
            add_abs(STITCH, x, y, ev)
            continue

    add_command_at_current(END, {"type": "end"})

    import tempfile
    import os as _os

    # pyembroidery 1.5.x VP3 writer expects a filesystem path rather than an
    # in-memory BytesIO object, so write to a temporary .vp3 file and read it
    # back for the browser download response.
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="easystitch_", suffix=".vp3", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            pyembroidery.write(pattern, tmp_path, settings={"name": Path(filename).stem[:8] or "EASY"})
        except TypeError:
            # Older/newer pyembroidery variants may not accept settings.
            pyembroidery.write(pattern, tmp_path)

        with open(tmp_path, "rb") as f:
            vp3_bytes = f.read()
    finally:
        if tmp_path:
            try:
                _os.remove(tmp_path)
            except Exception:
                pass

    if not vp3_bytes:
        raise RuntimeError("pyembroidery VP3 writer returned an empty file.")

    if positions:
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    else:
        min_x = max_x = min_y = max_y = 0

    stats = {
        "records": records,
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
        "format": "VP3",
        "note": "VP3 written through pyembroidery. VP3 trim commands include a zero-length stitch anchor at the current needle coordinate before each trim. Exact trim display still depends on the reader/machine."
    }

    debug = {
        "version": "easystitch-export-debug-v1",
        "format": "VP3",
        "filename": str(filename or "easystitch.vp3"),
        "stats": stats,
        "source_plan_stats": plan.get("stats", {}),
        "coordinate_scale": {
            "dpi": dpi,
            "px_to_01mm": px_to_01mm,
            "svg_w": svg_w,
            "svg_h": svg_h,
            "centered": True,
            "design_scale": plan.get("settings", {}).get("design_scale", {}),
        },
        "trim_hints": debug_trims,
        "color_changes": debug_color_changes,
        "long_moves_over_3mm": debug_long_moves,
        "records_sample_first_5000": debug_records[:5000],
        "records_total": len(debug_records),
        "notes": [
            "VP3 export requires pyembroidery in the Python environment.",
            "VP3 is the Husqvarna Viking / Pfaff format family targeted here.",
            "If a viewer still shows connector lines, compare with the DST and debug JSON to check trim handling."
        ],
    }

    return vp3_bytes, stats, debug
