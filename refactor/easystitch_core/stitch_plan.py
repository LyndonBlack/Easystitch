#!/usr/bin/env python3
"""
EasyStitch Core — Stitch plan builder and preview SVG generator.

Extracted from the monolith: builds an internal stitch-plan JSON from object
geometry and settings, and generates a full SVG stitch preview with underlay,
top fill, and satin layers.

Dependencies: math, json, numpy, PIL, shapely.
"""

import math
import json

import numpy as np
from PIL import Image, ImageDraw

from shapely.geometry import Polygon, MultiPolygon, LineString, Point, box
from shapely.ops import unary_union

from .utils import (
    mm_to_px,
    _hex_color_to_rgb,
    _svg_polyline,
    _svg_debug_polyline,
    _svg_debug_dot,
    _svg_debug_text,
    _rotate_xy,
    _rotate_geom,
    color_luminance,
)
from .geometry import (
    object_fill_geometry,
    geometry_to_svg_d,
    _sample_linestring,
    _sample_line_by_length_preview,
    _sample_line_by_spacing_preview,
    _nearest_point_on_line_preview,
    _geometry_polygons,
    _close_ring,
    generate_edge_walk_preview,
    _sample_subpath_points,
)
from .fill import (
    generate_fill_preview_lines,
    _order_fill_rows_serpentine,
    _order_fill_rows_lane_serpentine,
    _fill_row_groups,
    _match_fill_segments_into_lanes,
    _orient_fill_lane_segments,
    _satin_entry_candidates,
    _order_satin_bars_zigzag,
    _satin_bars_to_continuous_zigzag,
    _order_underlay_to_finish_near,
    _nearest_line_order,
    _ordered_lines_nearest_even_without_start,
    fill_angle_for_geometry,
    sorted_design_colors,
    objects_for_pass,
)
from .satin import (
    generate_satin_preview_lines,
    generate_guided_satin_preview_lines,
    generate_satin_preview_lines_with_guides,
    clip_manual_rung_to_geometry,
    _clip_rung_segment_to_poly,
    build_satin_debug_overlay_svg,
)
from .underlay import (
    generate_satin_underlay_preview_lines,
    lighter_object_blocker_geometry_for_underlay,
    foreground_blocker_geometry_for_object,
    subtract_blockers_for_top_fill,
    combined_satin_guide_rungs_for_object,
)


# ─────────────────────────────────────────────────────────────────────────────
# Stitch-plan helpers (not extracted elsewhere)
# ─────────────────────────────────────────────────────────────────────────────


def _line_points_for_plan(line, max_step_px: float, min_gap_px: float = 1.0) -> list:
    """
    Convert a preview polyline/bar into stitch points.

    Always reaches exact endpoints.  If a tiny final corner/remainder exists,
    the step spacing is redistributed down to min_gap_px instead of skipping
    the corner.  This gives a local "small gap fill" without lowering the
    global running stitch length.
    """
    if not line or len(line) < 2:
        return []

    max_step_px = max(1.0, float(max_step_px))
    min_gap_px = max(0.25, float(min_gap_px))
    pts = []

    for i in range(len(line) - 1):
        x1, y1 = line[i]
        x2, y2 = line[i + 1]
        seg_len = math.hypot(x2 - x1, y2 - y1)
        if seg_len <= 1e-6:
            continue

        if seg_len <= max_step_px:
            seg_pts = [(x1, y1), (x2, y2)]
        else:
            n = int(math.floor(seg_len / max_step_px))
            rem = seg_len - n * max_step_px
            if rem >= min_gap_px:
                n += 1
            n = max(1, n)
            seg_pts = []
            for k in range(n + 1):
                t = k / n
                seg_pts.append((x1 * (1 - t) + x2 * t, y1 * (1 - t) + y2 * t))

        for p in seg_pts:
            p = (float(p[0]), float(p[1]))
            if pts and math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) <= 1e-6:
                continue
            pts.append(p)

    return pts


def _append_polyline_stitches(events: list, line: list, max_step_px: float,
                              current_pos: tuple | None,
                              jump_threshold_px: float,
                              layer: str,
                              object_id: str,
                              color: str,
                              min_gap_px: float = 1.0,
                              connector_geom=None,
                              hard_trim_threshold_px: float | None = None) -> tuple:
    pts = _line_points_for_plan(line, max_step_px, min_gap_px=min_gap_px)
    if len(pts) < 2:
        return current_pos, 0, 0

    jumps = 0
    stitches = 0

    first = pts[0]
    if current_pos is None:
        events.append({"type": "move", "x": first[0], "y": first[1], "layer": layer, "object_id": object_id, "color": color})
        current_pos = first
    else:
        dist = math.hypot(first[0] - current_pos[0], first[1] - current_pos[1])
        if dist > jump_threshold_px:
            connector_used = False
            connector_allowed = True
            if hard_trim_threshold_px is not None and dist > hard_trim_threshold_px:
                connector_allowed = False

            if connector_allowed and connector_geom is not None and layer in ("top_satin", "top_fill"):
                try:
                    travel = LineString([current_pos, first])
                    if connector_geom.buffer(0.75).covers(travel):
                        conn_pts = _line_points_for_plan([current_pos, first], max_step_px, min_gap_px=min_gap_px)
                        for cp in conn_pts[1:]:
                            events.append({
                                "type": "stitch",
                                "x": cp[0],
                                "y": cp[1],
                                "layer": layer + "_hidden_connector",
                                "object_id": object_id,
                                "color": color
                            })
                            stitches += 1
                        connector_used = True
                except Exception:
                    connector_used = False

            if not connector_used:
                should_trim = layer in ("top_satin", "top_fill")
                if hard_trim_threshold_px is not None and dist > hard_trim_threshold_px:
                    should_trim = True
                if should_trim:
                    events.append({
                        "type": "trim",
                        "object_id": object_id,
                        "color": color,
                        "reason": "long_jump_before_" + layer,
                        "distance_px": dist
                    })
                events.append({"type": "jump", "x": first[0], "y": first[1], "distance_px": dist, "layer": layer, "object_id": object_id, "color": color})
                jumps += 1
        else:
            events.append({"type": "stitch", "x": first[0], "y": first[1], "layer": layer, "object_id": object_id, "color": color})
            stitches += 1
        current_pos = first

    for p in pts[1:]:
        events.append({"type": "stitch", "x": p[0], "y": p[1], "layer": layer, "object_id": object_id, "color": color})
        stitches += 1
        current_pos = p

    return current_pos, stitches, jumps


# ─────────────────────────────────────────────────────────────────────────────
# Stitch plan builder
# ─────────────────────────────────────────────────────────────────────────────


def build_stitch_plan(payload: dict) -> dict:
    """
    Build an internal stitch-plan from the same geometry as the preview.

    Output is deliberately neutral JSON:
      - color_change
      - move
      - jump
      - trim
      - stitch

    This is not machine-specific export yet.  It gives us the ordered stitch
    stream and statistics needed before DST/VP3/PES style export.
    """
    svg_w = float(payload.get("svg_w") or 500)
    svg_h = float(payload.get("svg_h") or 500)
    objects = payload.get("objects") or []
    assignments = payload.get("assignments") or {}
    manual_rungs = payload.get("manual_rungs") or {}
    cfg = payload.get("settings") or {}
    design_scale = cfg.get("design_scale") or {}

    dpi = float(cfg.get("dpi", 96.0))
    stitch_len_px = mm_to_px(float(cfg.get("stitch_length_mm", 2.5)), dpi)
    # Fixed automatic small-gap minimum. Tiny corners may use shorter
    # local stitches, but the global running stitch length remains the main
    # density/control setting.
    small_gap_px = mm_to_px(0.5, dpi)
    row_px = mm_to_px(float(cfg.get("row_spacing_mm", 0.4)), dpi)
    underlay_row_px = mm_to_px(float(cfg.get("underlay_row_mm", 1.6)), dpi)
    underlay_inset_px = mm_to_px(float(cfg.get("underlay_inset_mm", 0.8)), dpi)
    satin_spacing_px = mm_to_px(float(cfg.get("satin_spacing_mm", 0.45)), dpi)
    satin_max_probe_px = mm_to_px(float(cfg.get("satin_max_width_mm", 7.0)), dpi) / 2.0
    satin_end_extra_rungs = int(cfg.get("satin_end_extra_rungs", 2))
    satin_use_guide_helper = bool(cfg.get("satin_use_guide_helper", False))
    fill_angle = float(cfg.get("fill_angle", 45.0))
    auto_fill_direction = bool(cfg.get("auto_fill_direction", True))
    auto_fill_threshold = float(cfg.get("auto_fill_threshold", 2.0))
    stitch_order_mode = str(cfg.get("stitch_order_mode", "quality"))
    avoid_top_fill_overlap = bool(cfg.get("avoid_top_fill_overlap", True))
    underlay_protect_lighter = bool(cfg.get("underlay_protect_lighter", True))
    underlay_light_threshold = float(cfg.get("underlay_light_threshold", 45.0))
    underlay_hard_trim_px = mm_to_px(float(cfg.get("underlay_jump_trim_threshold_mm", 5.0)), dpi)
    enable_underlay = bool(cfg.get("enable_underlay", True))

    # Underlay strategy:
    # - fill objects: edge-walk + sparse perpendicular coarse fill
    # - satin objects: light centre-ish/edge underlay.  We use a 45-degree
    #   sparse hatch here as a simple general-purpose stabiliser for now.
    # Ink/Stitch exposes multiple satin underlay types such as center-walk,
    # contour and zig-zag; this is a first internal plan approximation.
    satin_underlay_angle = 45.0
    jump_threshold_px = max(mm_to_px(float(cfg.get("jump_trim_threshold_mm", 3.0)), dpi), stitch_len_px * 1.2)

    events = []
    stats = {
        "objects_used": 0,
        "objects_skipped": 0,
        "color_changes": 0,
        "stitches": 0,
        "jumps": 0,
        "trims": 0,
        "jump_threshold_mm": float(cfg.get("jump_trim_threshold_mm", 3.0)),
        "underlay_jump_trim_threshold_mm": float(cfg.get("underlay_jump_trim_threshold_mm", 5.0)),
        "underlay_protect_lighter": underlay_protect_lighter,
        "underlay_light_threshold": underlay_light_threshold,
        "small_gap_fill_mm": 0.5,
        "satin_underlay_mode": "contour_centerline",
        "satin_top_order": "continuous_zigzag_no_side_steps",
        "top_fill_order": "lane_serpentine",
        "long_jump_connector_policy": "hidden_if_inside_object_else_trim",
        "underlay_sparse_order": "protect_lighter_lane_serpentine",
        "top_fill_segment_connectors": "trim_long_reposition_moves",
        "underlay_stitches": 0,
        "top_stitches": 0,
        "fill_objects": 0,
        "satin_objects": 0,
        "manual_rungs": 0,
        "cut_guide_rungs": 0,
        "auto_fill_direction_objects": 0,
        "avoid_top_fill_overlap": avoid_top_fill_overlap,
        "estimated_width_px": svg_w,
        "estimated_height_px": svg_h,
        "estimated_width_mm": float(design_scale.get("target_width_mm") or (svg_w / dpi * 25.4)),
        "estimated_height_mm": float(design_scale.get("target_height_mm") or (svg_h / dpi * 25.4)),
        "design_scale_applied": bool(design_scale.get("scaling_applied")),
        "effective_dpi": float(dpi),
        "svg_to_mm": float(design_scale.get("svg_to_mm") or (25.4 / dpi)),
        "target_longest_mm": float(design_scale.get("target_longest_mm") or 0.0),
        "hoop_width_mm": float(design_scale.get("hoop_width_mm") or 0.0),
        "hoop_height_mm": float(design_scale.get("hoop_height_mm") or 0.0),
        "hoop_label": str(design_scale.get("hoop_label") or ""),
    }

    current_color = None
    current_pos = None

    sorted_objects = sorted(objects, key=lambda o: float(o.get("order", 0)))
    colors = sorted_design_colors(sorted_objects)

    if stitch_order_mode == "color_min":
        passes = []
        for color in colors:
            passes.append(("fill", color))
            passes.append(("satin", color))
    else:
        passes = [("fill", color) for color in colors] + [("satin", color) for color in colors]

    def start_color_if_needed(color):
        nonlocal current_color, current_pos
        if color != current_color:
            events.append({"type": "color_change", "color": color})
            stats["color_changes"] += 1
            current_color = color
            current_pos = None

    def add_trim(obj_id, color):
        nonlocal current_pos
        events.append({"type": "trim", "object_id": obj_id, "color": color})
        stats["trims"] += 1
        current_pos = None

    def stitch_fill_object(obj, color):
        nonlocal current_pos
        obj_id = str(obj.get("id"))
        geom = object_fill_geometry(obj)
        if geom is None or geom.is_empty:
            stats["objects_skipped"] += 1
            return

        stats["objects_used"] += 1
        stats["fill_objects"] += 1

        chosen_angle, angle_info = fill_angle_for_geometry(
            geom, fill_angle, auto_fill_direction, auto_fill_threshold
        )
        if angle_info.get("auto_used"):
            stats["auto_fill_direction_objects"] += 1
            events.append({
                "type": "note",
                "layer": "fill_direction",
                "object_id": obj_id,
                "color": color,
                "chosen_angle": chosen_angle,
                "elongation_ratio": angle_info.get("ratio"),
                "long_axis_angle": angle_info.get("long_axis_angle"),
            })

        if enable_underlay:
            edge_lines = generate_edge_walk_preview(geom, underlay_inset_px, stitch_len_px)
            try:
                underlay_fill_geom = geom.buffer(-max(underlay_inset_px * 0.45, 0.6))
                if underlay_fill_geom.is_empty:
                    underlay_fill_geom = geom
            except Exception:
                underlay_fill_geom = geom
            light_blockers = lighter_object_blocker_geometry_for_underlay(
                obj, sorted_objects, assignments,
                enabled=underlay_protect_lighter,
                threshold=underlay_light_threshold
            )
            sparse_underlay_geom = subtract_blockers_for_top_fill(
                underlay_fill_geom, light_blockers, safety_px=max(0.35, underlay_row_px * 0.20)
            )
            fill_underlay_lines = generate_fill_preview_lines(
                sparse_underlay_geom, underlay_row_px, stitch_len_px, chosen_angle + 90.0,
                min_segment_px=small_gap_px
            )
            # Edge walk first and structurally unchanged, but with better line
            # ordering and a hard-trim fallback for very long early jumps.
            for line in _ordered_lines_nearest_even_without_start(edge_lines, current_pos):
                current_pos, stitches, jumps = _append_polyline_stitches(
                    events, line, stitch_len_px, current_pos, jump_threshold_px,
                    "underlay_fill_edge", obj_id, color,
                    min_gap_px=small_gap_px,
                    hard_trim_threshold_px=underlay_hard_trim_px
                )
                stats["stitches"] += stitches
                stats["underlay_stitches"] += stitches
                stats["jumps"] += jumps

            # Sparse underlay only avoids much lighter protected objects.
            for line in _order_fill_rows_lane_serpentine(fill_underlay_lines, current_pos, chosen_angle + 90.0):
                current_pos, stitches, jumps = _append_polyline_stitches(
                    events, line, stitch_len_px, current_pos, jump_threshold_px,
                    "underlay_fill_sparse", obj_id, color,
                    min_gap_px=small_gap_px,
                    hard_trim_threshold_px=underlay_hard_trim_px
                )
                stats["stitches"] += stitches
                stats["underlay_stitches"] += stitches
                stats["jumps"] += jumps

        blocker_geom = foreground_blocker_geometry_for_object(
            obj, sorted_objects, assignments, enabled=avoid_top_fill_overlap
        )
        top_geom = subtract_blockers_for_top_fill(
            geom, blocker_geom, safety_px=max(0.35, row_px * 0.35)
        )

        top_lines = generate_fill_preview_lines(
            top_geom, row_px, stitch_len_px, chosen_angle,
            min_segment_px=small_gap_px
        )
        for line in _order_fill_rows_lane_serpentine(top_lines, current_pos, chosen_angle):
            current_pos, stitches, jumps = _append_polyline_stitches(
                events, line, stitch_len_px, current_pos, jump_threshold_px,
                "top_fill", obj_id, color,
                min_gap_px=small_gap_px,
                connector_geom=top_geom,
                hard_trim_threshold_px=jump_threshold_px
            )
            stats["stitches"] += stitches
            stats["top_stitches"] += stitches
            stats["jumps"] += jumps

        add_trim(obj_id, color)

    def stitch_satin_object(obj, color):
        nonlocal current_pos
        obj_id = str(obj.get("id"))
        geom = object_fill_geometry(obj)
        if geom is None or geom.is_empty:
            stats["objects_skipped"] += 1
            return

        stats["objects_used"] += 1
        stats["satin_objects"] += 1

        obj_manual = combined_satin_guide_rungs_for_object(obj, manual_rungs)
        if obj_manual:
            top_lines = generate_satin_preview_lines_with_guides(
                geom, satin_spacing_px, satin_max_probe_px, obj_manual,
                use_guide_helper=satin_use_guide_helper,
                extra_end_rungs=satin_end_extra_rungs
            )
            stats["manual_rungs"] += len(obj_manual)
            stats["cut_guide_rungs"] += sum(1 for r in obj_manual if r.get("source") == "manual_split_cut")
        else:
            top_lines = generate_satin_preview_lines(
                geom, satin_spacing_px, satin_max_probe_px,
                use_guide_helper=satin_use_guide_helper,
                extra_end_rungs=satin_end_extra_rungs
            )

        if enable_underlay:
            satin_underlay_lines = generate_satin_underlay_preview_lines(
                geom, satin_spacing_px, underlay_inset_px, stitch_len_px
            )

            # Compute entry points from the actual continuous zigzag path
            # candidates, not just raw bar endpoints.  The zigzag converter
            # always starts with bars[0][0] of the chosen candidate — but
            # bars[0][0] might not be the same as _satin_entry_candidates
            # after orient_sequence reverses bars.  We build all 4 possible
            # zigzag paths and extract their actual first stitch point as
            # underlay targets so that underlay finishes near a real zigzag
            # entry.
            candidate_entries = set()
            for entry_side in (0, 1):
                for use_reversed in (False, True):
                    wip = list(reversed([list(b) for b in top_lines if b and len(b) >= 2])) if use_reversed else [list(b) for b in top_lines if b and len(b) >= 2]
                    if not wip:
                        continue
                    # Manually orient the first bar as _order_satin_bars_zigzag would
                    if entry_side == 1:
                        wip[0] = list(reversed(wip[0]))
                    # Build zigzag from just this candidate orientation
                    zigzag_candidate = _satin_bars_to_continuous_zigzag(wip)
                    if zigzag_candidate and len(zigzag_candidate) >= 2:
                        candidate_entries.add((round(zigzag_candidate[0][0], 1), round(zigzag_candidate[0][1], 1)))

            underlay_targets = list(candidate_entries) if candidate_entries else _satin_entry_candidates(top_lines)
            for line in _order_underlay_to_finish_near(satin_underlay_lines, current_pos, underlay_targets):
                current_pos, stitches, jumps = _append_polyline_stitches(
                    events, line, stitch_len_px, current_pos, jump_threshold_px,
                    "underlay_satin_contour_center", obj_id, color,
                    min_gap_px=small_gap_px,
                    hard_trim_threshold_px=underlay_hard_trim_px
                )
                stats["stitches"] += stitches
                stats["underlay_stitches"] += stitches
                stats["jumps"] += jumps

        ordered_satin_bars = _order_satin_bars_zigzag(top_lines, current_pos)
        satin_zigzag_path = _satin_bars_to_continuous_zigzag(ordered_satin_bars)
        if satin_zigzag_path:
            current_pos, stitches, jumps = _append_polyline_stitches(
                events, satin_zigzag_path, stitch_len_px, current_pos, jump_threshold_px,
                "top_satin", obj_id, color,
                min_gap_px=small_gap_px,
                connector_geom=geom
            )
            stats["stitches"] += stitches
            stats["top_stitches"] += stitches
            stats["jumps"] += jumps

        add_trim(obj_id, color)

    for pass_type, color in passes:
        pass_objects = objects_for_pass(sorted_objects, assignments, color, pass_type)
        if not pass_objects:
            continue
        start_color_if_needed(color)
        for obj in pass_objects:
            if pass_type == "fill":
                stitch_fill_object(obj, color)
            elif pass_type == "satin":
                stitch_satin_object(obj, color)

    stats["trims"] = sum(1 for ev in events if ev.get("type") == "trim")

    return {
        "version": "easystitch-stitch-plan-v1",
        "svg_w": svg_w,
        "svg_h": svg_h,
        "settings": cfg,
        "stats": stats,
        "events": events,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SVG preview helpers
# ─────────────────────────────────────────────────────────────────────────────


def _svg_cut_guide_rungs_overlay(obj: dict) -> str:
    chunks = []
    for rung in obj.get("cut_guide_rungs") or []:
        a = rung.get("a")
        b = rung.get("b")
        if not a or not b:
            continue
        try:
            x1, y1 = float(a[0]), float(a[1])
            x2, y2 = float(b[0]), float(b[1])
        except Exception:
            continue
        chunks.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="#ff00ff" stroke-width="2.2" stroke-opacity="0.95" '
            f'stroke-dasharray="2 2" vector-effect="non-scaling-stroke"/>'
        )
    return "".join(chunks)


def build_stitch_preview_svg(payload: dict) -> dict:
    """
    Build a stitch-line preview from Pane 4 assignments.

    First implementation scope:
      - underlay edge walk
      - coarse underlay fill at angle + 90
      - top fill for objects assigned "fill"
      - satin objects get underlay now; satin top stitching comes next
    """
    svg_w = float(payload.get("svg_w") or 500)
    svg_h = float(payload.get("svg_h") or 500)
    objects = payload.get("objects") or []
    assignments = payload.get("assignments") or {}
    manual_rungs = payload.get("manual_rungs") or {}
    cfg = payload.get("settings") or {}
    design_scale = cfg.get("design_scale") or {}

    dpi = float(cfg.get("dpi", 96.0))
    stitch_len_px = mm_to_px(float(cfg.get("stitch_length_mm", 2.5)), dpi)
    small_gap_px = mm_to_px(0.5, dpi)
    row_px = mm_to_px(float(cfg.get("row_spacing_mm", 0.4)), dpi)
    underlay_row_px = mm_to_px(float(cfg.get("underlay_row_mm", 1.6)), dpi)
    underlay_inset_px = mm_to_px(float(cfg.get("underlay_inset_mm", 0.8)), dpi)
    satin_spacing_px = mm_to_px(float(cfg.get("satin_spacing_mm", 0.45)), dpi)
    satin_max_probe_px = mm_to_px(float(cfg.get("satin_max_width_mm", 7.0)), dpi) / 2.0
    satin_end_extra_rungs = int(cfg.get("satin_end_extra_rungs", 2))
    satin_use_guide_helper = bool(cfg.get("satin_use_guide_helper", False))
    satin_debug_rails = bool(cfg.get("satin_debug_rails", False))
    fill_angle = float(cfg.get("fill_angle", 45.0))
    auto_fill_direction = bool(cfg.get("auto_fill_direction", True))
    auto_fill_threshold = float(cfg.get("auto_fill_threshold", 2.0))
    avoid_top_fill_overlap = bool(cfg.get("avoid_top_fill_overlap", True))
    underlay_protect_lighter = bool(cfg.get("underlay_protect_lighter", True))
    underlay_light_threshold = float(cfg.get("underlay_light_threshold", 45.0))
    enable_underlay = bool(cfg.get("enable_underlay", True))

    chunks = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}">']
    counts = {
        "underlay_edge_lines": 0,
        "underlay_fill_lines": 0,
        "top_fill_lines": 0,
        "satin_bars": 0,
        "satin_endpoint_caps_enabled": 1,
        "satin_rail_cap_rungs_enabled": 1,
        "satin_objects_pending": 0,
        "satin_debug_open_rails": 0,
        "satin_debug_ring_rails": 0,
        "satin_debug_pair_candidates": 0,
        "manual_rungs": 0,
        "cut_guide_rungs": 0,
        "objects_used": 0,
        "design_scale_applied": bool(design_scale.get("scaling_applied")),
        "effective_dpi": float(dpi),
        "svg_to_mm": float(design_scale.get("svg_to_mm") or (25.4 / dpi)),
        "target_width_mm": float(design_scale.get("target_width_mm") or 0.0),
        "target_height_mm": float(design_scale.get("target_height_mm") or 0.0),
    }
    preview_layers = {"underlay": [], "top": []}
    debug_svg_chunks = []

    def add_preview_line(layer_name, line, color, width=0.8, opacity=0.9, dash=""):
        preview_layers[layer_name].append({
            "points": [[float(x), float(y)] for x, y in line],
            "color": color,
            "width": float(width),
            "opacity": float(opacity),
            "dash": dash,
        })

    for obj in sorted(objects, key=lambda o: float(o.get("order", 0))):
        obj_id = obj.get("id")
        stype = assignments.get(obj_id, "fill")
        if stype == "skip":
            continue

        geom = object_fill_geometry(obj)
        if geom is None or geom.is_empty:
            continue

        color = obj.get("color", "#000000")
        counts["objects_used"] += 1
        object_fill_angle, object_fill_angle_info = fill_angle_for_geometry(
            geom, fill_angle, auto_fill_direction, auto_fill_threshold
        )

        # Track the last underlay point so satin ordering knows where
        # the underlay finished.  This prevents satin from starting at the
        # opposite end of the column and creating an apparent jump.
        underlay_end_pos = None

        if enable_underlay:
            edge_lines = generate_edge_walk_preview(geom, underlay_inset_px, stitch_len_px)
            for line in edge_lines:
                chunks.append(_svg_polyline(line, color, 0.75, 0.55, "3 2"))
                add_preview_line("underlay", line, color, 0.85, 0.65, "3 2")
                if line:
                    underlay_end_pos = line[-1]
                counts["underlay_edge_lines"] += len(edge_lines)

                try:
                    underlay_fill_geom = geom.buffer(-max(underlay_inset_px * 0.45, 0.6))
                    if underlay_fill_geom.is_empty:
                        underlay_fill_geom = geom
                except Exception:
                    underlay_fill_geom = geom
                light_blockers = lighter_object_blocker_geometry_for_underlay(
                    obj, objects, assignments,
                    enabled=underlay_protect_lighter,
                    threshold=underlay_light_threshold
                )
                sparse_underlay_geom = subtract_blockers_for_top_fill(
                    underlay_fill_geom, light_blockers, safety_px=max(0.35, underlay_row_px * 0.20)
                )
                underlay_lines = generate_fill_preview_lines(
                    sparse_underlay_geom, underlay_row_px, stitch_len_px, object_fill_angle + 90.0,
                    min_segment_px=small_gap_px
                )
                for line in underlay_lines:
                    chunks.append(_svg_polyline(line, color, 0.65, 0.35, "5 4"))
                    add_preview_line("underlay", line, color, 0.75, 0.5, "5 4")
                    if line:
                        underlay_end_pos = line[-1]
                counts["underlay_fill_lines"] += len(underlay_lines)

            if stype == "fill":
                blocker_geom = foreground_blocker_geometry_for_object(
                    obj, objects, assignments, enabled=avoid_top_fill_overlap
                )
                top_geom = subtract_blockers_for_top_fill(
                    geom, blocker_geom, safety_px=max(0.35, row_px * 0.35)
                )
                top_lines = generate_fill_preview_lines(
                    top_geom, row_px, stitch_len_px, object_fill_angle,
                    min_segment_px=small_gap_px
                )
                for line in top_lines:
                    chunks.append(_svg_polyline(line, color, 1.05, 1.0))
                    add_preview_line("top", line, color, 1.05, 1.0, "")
                counts["top_fill_lines"] += len(top_lines)
            elif stype == "satin":
                if satin_debug_rails:
                    overlay = _svg_cut_guide_rungs_overlay(obj)
                    if overlay:
                        chunks.append(overlay)
                        debug_svg_chunks.append(overlay)
                obj_manual_rungs = combined_satin_guide_rungs_for_object(obj, manual_rungs)
                if obj_manual_rungs:
                    satin_lines = generate_satin_preview_lines_with_guides(
                        geom,
                        satin_spacing_px,
                        satin_max_probe_px,
                        obj_manual_rungs,
                        use_guide_helper=satin_use_guide_helper,
                        extra_end_rungs=satin_end_extra_rungs
                    )
                    counts["manual_rungs"] += len(obj_manual_rungs)
                    counts["cut_guide_rungs"] = counts.get("cut_guide_rungs", 0) + sum(1 for r in obj_manual_rungs if r.get("source") == "manual_split_cut")
                else:
                    satin_lines = generate_satin_preview_lines(
                        geom, satin_spacing_px, satin_max_probe_px,
                        use_guide_helper=satin_use_guide_helper,
                        extra_end_rungs=satin_end_extra_rungs
                    )

                # Use the last underlay point as the start position for satin
                # ordering, so satin begins near where underlay finished.
                ordered_satin_preview_bars = _order_satin_bars_zigzag(satin_lines, underlay_end_pos)
            satin_zigzag_path = _satin_bars_to_continuous_zigzag(ordered_satin_preview_bars)
            if satin_zigzag_path:
                chunks.append(_svg_polyline(satin_zigzag_path, color, 1.15, 1.0))
                add_preview_line("top", satin_zigzag_path, color, 1.15, 1.0, "")
            counts["satin_bars"] += len(satin_lines)

            # Keep a faint transformed outline for debugging/preview context.
            outline_d = geometry_to_svg_d(geom)
            if outline_d:
                chunks.append(
                    f'<path d="{outline_d}" fill="none" stroke="{color}" '
                    f'stroke-width="0.6" stroke-opacity="0.32" stroke-linejoin="round"/>'
                )
            if satin_debug_rails:
                overlay, dbg_counts = build_satin_debug_overlay_svg(
                    geom, satin_spacing_px, satin_max_probe_px,
                    extra_end_rungs=satin_end_extra_rungs
                )
                if overlay:
                    chunks.append(overlay)
                    debug_svg_chunks.append(overlay)
                counts["satin_debug_open_rails"] += dbg_counts.get("debug_open_rails", 0)
                counts["satin_debug_ring_rails"] += dbg_counts.get("debug_ring_rails", 0)
                counts["satin_debug_pair_candidates"] += dbg_counts.get("debug_pair_candidates", 0)

            if not satin_lines:
                counts["satin_objects_pending"] += 1

    chunks.append("</svg>")
    return {
        "svg": "".join(chunks),
        "counts": counts,
        "layers": preview_layers,
        "debug_svg": "".join(debug_svg_chunks),
    }
