#!/usr/bin/env python3
"""
EasyStitch Core — Underlay stitch generation and blocker geometry.

Extracted from the monolith.  Contains satin underlay preview generation,
colour-based blocker geometry, foreground blocker geometry, subtraction
helpers, and satin guide rung combination.
"""

import math

import numpy as np
from shapely.geometry import Point, Polygon, MultiPolygon, box
from shapely.ops import unary_union

from .geometry import (
    _geometry_polygons,
    _sample_line_by_length_preview,
    generate_edge_walk_preview,
    object_fill_geometry,
)
from .utils import _hex_color_to_rgb, color_luminance


# ─────────────────────────────────────────────────────────────────────────────
# Satin underlay preview
# ─────────────────────────────────────────────────────────────────────────────


def generate_satin_underlay_preview_lines(geom, spacing_px: float,
                                          inset_px: float,
                                          stitch_len_px: float) -> list:
    """
    Satin underlay should support the column, not behave like a fill hatch over
    the whole bounding shape.

    This generates:
      - a light contour/edge walk inset inside the satin object
      - for open satin columns, a centreline walk between the two detected rails

    That avoids the large jump-across-hole behaviour seen when using generic
    45-degree fill hatching on rings/curves.
    """
    if geom is None or geom.is_empty:
        return []

    lines = []

    # Contour walk first.  Keep it modestly inset so it stays under the top satin.
    try:
        contour_inset = max(0.45, inset_px * 0.45)
        lines.extend(generate_edge_walk_preview(geom, contour_inset, stitch_len_px))
    except Exception:
        pass

    # Centreline for open satin-like shapes.  Rings already get a useful contour
    # walk; trying to hatch or bridge a ring creates long jumps across the hole.
    for poly in _geometry_polygons(geom):
        if poly.is_empty or len(poly.interiors) > 0:
            continue
        try:
            rail_info = _axis_split_outline_rails_for_debug(poly, spacing_px, None)
        except TypeError:
            try:
                rail_info = _axis_split_outline_rails_for_debug(poly, None)
            except Exception:
                rail_info = None
        except Exception:
            rail_info = None

        if not rail_info:
            continue

        # Support both tuple-style rail debug return and dict-style versions.
        if isinstance(rail_info, dict):
            rail1 = rail_info.get("rail1")
            rail2 = rail_info.get("rail2")
        else:
            try:
                rail1, rail2 = rail_info[0], rail_info[1]
            except Exception:
                rail1 = rail2 = None

        if rail1 is None or rail2 is None or rail1.length <= 1e-6 or rail2.length <= 1e-6:
            continue

        n = max(3, int(math.ceil(max(rail1.length, rail2.length) / max(stitch_len_px, 1.0))) + 1)

        pts1 = _sample_line_by_length_preview(rail1, n, reverse=False, include_endpoint=True)
        pts2_fwd = _sample_line_by_length_preview(rail2, n, reverse=False, include_endpoint=True)
        pts2_rev = _sample_line_by_length_preview(rail2, n, reverse=True, include_endpoint=True)

        def avg_dist(a, b):
            m = min(len(a), len(b))
            if m == 0:
                return float("inf")
            return sum(math.hypot(a[i][0] - b[i][0], a[i][1] - b[i][1]) for i in range(m)) / m

        pts2 = pts2_rev if avg_dist(pts1, pts2_rev) < avg_dist(pts1, pts2_fwd) else pts2_fwd
        m = min(len(pts1), len(pts2))
        if m < 2:
            continue

        centre = []
        for i in range(m):
            centre.append(((pts1[i][0] + pts2[i][0]) / 2.0, (pts1[i][1] + pts2[i][1]) / 2.0))

        # Only keep centreline points that are within the satin object.  This
        # prevents odd centreline segments escaping near complex intersections.
        clean = []
        for p in centre:
            try:
                if poly.buffer(0.35).covers(Point(p[0], p[1])):
                    clean.append(p)
                else:
                    if len(clean) >= 2:
                        lines.append(clean)
                    clean = []
            except Exception:
                clean.append(p)
        if len(clean) >= 2:
            lines.append(clean)

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Blocker geometry
# ─────────────────────────────────────────────────────────────────────────────


def lighter_object_blocker_geometry_for_underlay(obj: dict,
                                                 objects: list,
                                                 assignments: dict,
                                                 enabled: bool = True,
                                                 threshold: float = 45.0):
    if not enabled:
        return None
    obj_id = str(obj.get("id"))
    obj_lum = color_luminance(obj.get("color", "#000000"))
    blockers = []
    for other in objects:
        other_id = str(other.get("id"))
        if other_id == obj_id:
            continue
        if assignments.get(other_id, "fill") == "skip":
            continue
        other_lum = color_luminance(other.get("color", "#000000"))
        if other_lum <= obj_lum + float(threshold):
            continue
        try:
            g = object_fill_geometry(other)
            if g is not None and not g.is_empty:
                blockers.append(g)
        except Exception:
            continue
    if not blockers:
        return None
    try:
        return unary_union(blockers).buffer(0)
    except Exception:
        return None


def foreground_blocker_geometry_for_object(obj: dict, objects: list, assignments: dict,
                                           enabled: bool = True):
    """
    Build a union of different-colour stitch objects that should be kept clear
    from this object's top fill.

    Purpose:
      - yellow face top fill should not run under black eyes/mouth/eyebrows
      - black eye top fill should not run under white highlight
      - same-colour satin borders are not subtracted, so they can still clean
        edges without creating visible gaps.
    """
    if not enabled:
        return None

    obj_id = str(obj.get("id"))
    obj_color = obj.get("color", "#000000")
    blockers = []

    for other in objects:
        other_id = str(other.get("id"))
        if other_id == obj_id:
            continue
        if assignments.get(other_id, "fill") == "skip":
            continue
        if other.get("color", "#000000") == obj_color:
            continue

        try:
            g = object_fill_geometry(other)
            if g is not None and not g.is_empty:
                blockers.append(g)
        except Exception:
            continue

    if not blockers:
        return None

    try:
        return unary_union(blockers).buffer(0)
    except Exception:
        return None


def subtract_blockers_for_top_fill(geom, blocker_geom, safety_px: float = 0.25):
    """
    Remove foreground different-colour objects from top fill geometry.

    A small positive buffer on blockers reduces the chance of top fill peeking
    into tiny traced gaps, while still allowing later satin to cover edges.
    """
    if geom is None or geom.is_empty or blocker_geom is None or blocker_geom.is_empty:
        return geom

    try:
        blocked = blocker_geom.buffer(max(0.0, float(safety_px)))
        out = geom.difference(blocked)
        if out is None or out.is_empty:
            return geom
        return out.buffer(0)
    except Exception:
        return geom


# ─────────────────────────────────────────────────────────────────────────────
# Satin guide rungs
# ─────────────────────────────────────────────────────────────────────────────


def combined_satin_guide_rungs_for_object(obj: dict, manual_rungs: dict) -> list:
    obj_id = str(obj.get("id"))
    cut_rungs = obj.get("cut_guide_rungs") or []
    user_rungs = manual_rungs.get(obj_id, []) or []

    out = []
    for rung in cut_rungs:
        if not isinstance(rung, dict):
            continue
        if rung.get("a") and rung.get("b"):
            nr = dict(rung)
            nr.setdefault("source", "manual_split_cut")
            out.append(nr)

    for rung in user_rungs:
        if not isinstance(rung, dict):
            continue
        if rung.get("a") and rung.get("b"):
            nr = dict(rung)
            nr.setdefault("source", "manual_rung")
            out.append(nr)

    return out
