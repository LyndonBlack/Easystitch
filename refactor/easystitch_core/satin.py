#!/usr/bin/env python3
"""
EasyStitch Core — Satin stitch generation.

Extracted from the monolith: satin stitch generation functions including
rail/rung pairing, axis splitting, guide-rail normals, skeleton chords,
manual rung clipping, and debug overlay generation.
"""

import math

import numpy as np

from shapely.geometry import Polygon, MultiPolygon, LineString, Point, box
from shapely.ops import split as shapely_split, unary_union
from shapely import affinity

from .utils import (
    _rotate_xy,
    _rotate_geom,
    _arc_coords_between,
    _svg_polyline,
    _svg_debug_polyline,
    _svg_debug_dot,
    _svg_debug_text,
    mm_to_px,
)

from .geometry import (
    _sample_linestring,
    _sample_line_by_length_preview,
    _sample_line_by_spacing_preview,
    _nearest_point_on_line_preview,
    _rasterize_geom_mask,
    _skeleton_world_segments_for_geom,
    _line_geom_intersections_as_segments,
    _normal_crossbar_inside_geom,
    _close_ring,
    _geometry_polygons,
    geometry_to_svg_d,
)


# ─────────────────────────────────────────────────────────────────────────────
# Rail/nearest-point satin bars
# ─────────────────────────────────────────────────────────────────────────────


def _rail_nearest_satin_bars(rail_a: LineString, rail_b: LineString, spacing_px: float,
                             max_bar_len_px: float | None = None,
                             geom=None,
                             include_endpoints: bool = False) -> list:
    """
    Sample one rail and connect every sample to the nearest point on the
    opposite rail.

    include_endpoints=True is important for open split satin objects. Without
    this, the sampler avoids the very ends and leaves the gaps seen on mouths,
    eyebrows, and cut line sections.
    """
    if rail_a is None or rail_b is None or rail_a.length <= 1e-9 or rail_b.length <= 1e-9:
        return []

    # Sample the longer rail. This tends to avoid missing bars on curves while
    # still pairing each bar to the nearest opposite side.
    source = rail_a if rail_a.length >= rail_b.length else rail_b
    target = rail_b if source is rail_a else rail_a

    samples = _sample_line_by_spacing_preview(
        source, spacing_px, include_endpoint=include_endpoints
    )

    # For open rails, force the real rail endpoints into the sample set. This
    # gives visible cap stitches at cut ends and natural stroke endpoints.
    if include_endpoints:
        coords = list(source.coords)
        if coords:
            samples = [tuple(coords[0])] + samples + [tuple(coords[-1])]

    out = []
    seen = set()

    def add_bar(p, q, allow_slightly_long: bool = False):
        if q is None:
            return
        length = math.hypot(p[0] - q[0], p[1] - q[1])
        if length <= 0.25:
            return

        effective_max = max_bar_len_px
        if allow_slightly_long and effective_max is not None:
            effective_max *= 1.18

        if effective_max is not None and length > effective_max:
            return

        mid = Point((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)
        if geom is not None:
            try:
                if not geom.buffer(0.45).covers(mid):
                    return
            except Exception:
                pass

        key = (
            round(p[0] / 0.35), round(p[1] / 0.35),
            round(q[0] / 0.35), round(q[1] / 0.35),
        )
        rkey = (key[2], key[3], key[0], key[1])
        if key in seen or rkey in seen:
            return
        seen.add(key)
        out.append([p, q])

    for p in samples:
        q = _nearest_point_on_line_preview(target, p)
        add_bar(p, q, allow_slightly_long=False)

    # Explicit cap stitch attempts for both rails. This helps when the longer
    # rail is not the one that owns a visually important end cap.
    if include_endpoints:
        for rail_from, rail_to in ((rail_a, rail_b), (rail_b, rail_a)):
            coords = list(rail_from.coords)
            if len(coords) >= 2:
                for endpoint in (tuple(coords[0]), tuple(coords[-1])):
                    add_bar(
                        endpoint,
                        _nearest_point_on_line_preview(rail_to, endpoint),
                        allow_slightly_long=True,
                    )

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Ring rail satin (closed outlines with holes)
# ─────────────────────────────────────────────────────────────────────────────


def _ring_rail_satin_preview_lines(poly: Polygon, spacing_px: float, max_bar_len_px: float | None = None) -> list:
    """
    Rail/rung satin for closed outline regions with holes.

    Exterior and interior rails are paired by nearest opposite point, not by
    equal arclength. This keeps ring stitches radial/local instead of dragging
    across to a distant part of the same contour.
    """
    if poly is None or poly.is_empty or poly.geom_type != "Polygon":
        return []
    if not poly.interiors:
        return []

    ext = LineString(list(poly.exterior.coords))
    if ext.length <= 1e-6:
        return []

    out = []
    for interior in poly.interiors:
        inner = LineString(list(interior.coords))
        if inner.length <= 1e-6:
            continue
        out.extend(_rail_nearest_satin_bars(ext, inner, spacing_px, max_bar_len_px, geom=poly, include_endpoints=False))

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Rail cap rung satin (open satin ends)
# ─────────────────────────────────────────────────────────────────────────────


def _rail_cap_rung_satin_preview_lines(rail1: LineString, rail2: LineString,
                                       spacing_px: float,
                                       max_bar_len_px: float | None = None,
                                       geom=None) -> list:
    """
    Fill open satin ends using rail-to-rail cap rungs.

    This follows the Ink/Stitch satin-column idea more closely than the older
    axis cap filler: use the two rails and create short rungs near the start
    and end, so stitch direction is still controlled by the side rails rather
    than by an arbitrary global axis.

    rail1 runs start -> end. rail2 runs end -> start in our boundary-split
    construction, so:
      start cap connects rail1[d]         to rail2[length-d]
      end cap   connects rail1[length-d]  to rail2[d]
    """
    if rail1 is None or rail2 is None or rail1.length <= 1e-9 or rail2.length <= 1e-9:
        return []

    spacing_px = max(0.45, float(spacing_px))
    cap_depth = min(max(rail1.length, rail2.length) * 0.18, spacing_px * 5.0)
    cap_depth = max(cap_depth, spacing_px * 2.0)

    out = []
    seen = set()

    def add_bar(a, b):
        if a is None or b is None:
            return
        length = math.hypot(a[0] - b[0], a[1] - b[1])
        if length <= 0.25:
            return
        if max_bar_len_px is not None and length > max_bar_len_px * 1.15:
            return

        mid = Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        if geom is not None:
            try:
                if not geom.buffer(0.45).covers(mid):
                    return
            except Exception:
                pass

        key = (
            round(a[0] / 0.35), round(a[1] / 0.35),
            round(b[0] / 0.35), round(b[1] / 0.35),
        )
        rkey = (key[2], key[3], key[0], key[1])
        if key in seen or rkey in seen:
            return
        seen.add(key)
        out.append([a, b])

    steps = max(3, int(math.ceil(cap_depth / spacing_px)) + 1)
    for i in range(steps):
        d = min(i * spacing_px, cap_depth)
        d1 = min(d, rail1.length)
        d2 = min(d, rail2.length)

        # Start cap.
        p1 = rail1.interpolate(d1)
        p2 = rail2.interpolate(max(0.0, rail2.length - d2))
        add_bar((float(p1.x), float(p1.y)), (float(p2.x), float(p2.y)))

        # End cap.
        p3 = rail1.interpolate(max(0.0, rail1.length - d1))
        p4 = rail2.interpolate(d2)
        add_bar((float(p3.x), float(p3.y)), (float(p4.x), float(p4.y)))

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Axis endcap satin preview lines
# ─────────────────────────────────────────────────────────────────────────────


def _axis_endcap_satin_preview_lines(poly: Polygon, spacing_px: float,
                                      max_bar_len_px: float | None = None) -> list:
    """
    Fill the open ends of a simple satin shape.

    The rail pairing is good through the body of a stroke, but at rounded or
    manually cut ends the two rails meet and nearest-rail matching can produce
    tiny/duplicate bars or skip the end completely. This pass adds short-axis
    cross-section bars near both long-axis ends, clipped to the polygon.

    It behaves like using the good contour/underlay boundary as the constraint:
    every cap bar is generated by intersecting a line with the actual selected
    geometry, so it should not jump outside the path.
    """
    if poly is None or poly.is_empty or poly.geom_type != "Polygon":
        return []
    if len(poly.interiors) > 0:
        return []

    minx, miny, maxx, maxy = poly.bounds
    spanx, spany = maxx - minx, maxy - miny
    long_span = max(spanx, spany)
    if long_span < 4:
        return []

    try:
        rect = list(poly.minimum_rotated_rectangle.exterior.coords)[:-1]
        edges = []
        for i in range(4):
            x1, y1 = rect[i]
            x2, y2 = rect[(i + 1) % 4]
            edges.append((math.hypot(x2 - x1, y2 - y1), x2 - x1, y2 - y1))
        _, ax, ay = max(edges, key=lambda e: e[0])
    except Exception:
        ax, ay = (1.0, 0.0) if spanx >= spany else (0.0, 1.0)

    amag = math.hypot(ax, ay) or 1e-9
    ax, ay = ax / amag, ay / amag
    nx, ny = -ay, ax

    coords = [(float(x), float(y)) for x, y in list(poly.exterior.coords)]
    if coords and coords[0] == coords[-1]:
        coords = coords[:-1]
    if not coords:
        return []

    projs = [x * ax + y * ay for x, y in coords]
    minp, maxp = min(projs), max(projs)

    # Estimate stroke width from area/length-ish. Keep cap fill local so it
    # does not over-thicken the full object.
    area = abs(poly.area)
    estimated_width = max(1.0, min(long_span * 0.35, area / max(long_span, 1.0)))
    cap_depth = max(spacing_px * 3.0, estimated_width * 1.4)
    cap_depth = min(cap_depth, long_span * 0.22)

    scan_half = long_span * 0.75 + estimated_width * 4.0 + 4.0
    max_len = max_bar_len_px if max_bar_len_px is not None else estimated_width * 4.0
    max_len = max(max_len, estimated_width * 2.2)

    out = []
    seen = set()

    def add_cross_section(t):
        cx = ax * t
        cy = ay * t
        # Because t is an axis projection, choose a point on that projected
        # axis nearest the polygon centroid to position the infinite scan line.
        cen = poly.representative_point()
        offset = cen.x * nx + cen.y * ny
        px = ax * t + nx * offset
        py = ay * t + ny * offset

        probe = LineString([
            (px - nx * scan_half, py - ny * scan_half),
            (px + nx * scan_half, py + ny * scan_half),
        ])

        try:
            inter = poly.intersection(probe)
        except Exception:
            return

        segs = _line_geom_intersections_as_segments(inter)
        if not segs:
            return

        # Choose the segment closest to the polygon representative point.
        cpt = poly.representative_point()
        segs.sort(key=lambda s: s.distance(cpt))
        seg = segs[0]
        if seg.length <= 0.35 or seg.length > max_len:
            return

        pts = list(seg.coords)
        if len(pts) < 2:
            return
        a = (float(pts[0][0]), float(pts[0][1]))
        b = (float(pts[-1][0]), float(pts[-1][1]))

        mid = Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        try:
            if not poly.buffer(0.35).covers(mid):
                return
        except Exception:
            pass

        key = (
            round(a[0] / 0.35), round(a[1] / 0.35),
            round(b[0] / 0.35), round(b[1] / 0.35),
        )
        rkey = (key[2], key[3], key[0], key[1])
        if key in seen or rkey in seen:
            return
        seen.add(key)
        out.append([a, b])

    # Include the true endpoints and a few inward bars. The endpoint itself can
    # sometimes collapse to a tiny segment, but the next bars usually fill the
    # rounded/cut cap cleanly.
    steps = max(3, int(math.ceil(cap_depth / max(spacing_px, 0.45))) + 1)
    for i in range(steps):
        d = i * spacing_px
        if d > cap_depth:
            break
        add_cross_section(minp + d)
        add_cross_section(maxp - d)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Merge satin lines without duplicates
# ─────────────────────────────────────────────────────────────────────────────


def _merge_satin_lines_without_duplicates(primary: list, extra: list,
                                          tolerance_px: float = 0.7) -> list:
    """
    Merge satin bar sets without adding near-identical duplicate bars.
    """
    out = list(primary)
    mids = []
    for line in out:
        if len(line) >= 2:
            mids.append(((line[0][0] + line[1][0]) / 2.0, (line[0][1] + line[1][1]) / 2.0))

    for line in extra:
        if len(line) < 2:
            continue
        mid = ((line[0][0] + line[1][0]) / 2.0, (line[0][1] + line[1][1]) / 2.0)
        if any(math.hypot(mid[0] - m[0], mid[1] - m[1]) < tolerance_px for m in mids):
            continue
        out.append(line)
        mids.append(mid)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Paired rail rung satin preview lines
# ─────────────────────────────────────────────────────────────────────────────


def _paired_rail_rung_satin_preview_lines(poly: Polygon,
                                          rail1: LineString,
                                          rail2: LineString,
                                          spacing_px: float,
                                          max_bar_len_px: float | None = None,
                                          extra_end_rungs: int = 0) -> list:
    """
    Pure rail/rung satin for open stroke-like shapes.

    The rails shown in the debug overlay are already correct, so this version
    deliberately avoids the previous over-strict rejection rules.  For each
    matching rail position it creates a rung, clips that rung to the actual
    polygon, and keeps the clipped segment.

    This is closer to how a satin column should behave: the rails define the
    stitch column, and the rungs simply connect those rails.  The ends are not
    treated as a special geometry case; extra_end_rungs just adds more rail
    samples near the first and last interval.
    """
    if poly is None or poly.is_empty:
        return []
    if rail1 is None or rail2 is None or rail1.length <= 1e-6 or rail2.length <= 1e-6:
        return []

    spacing_px = max(0.45, float(spacing_px))
    extra_end_rungs = max(0, int(extra_end_rungs))
    n = max(4, int(math.ceil(max(rail1.length, rail2.length) / spacing_px)) + 1)

    def build_t_values(count, extra):
        if count <= 1:
            return [0.0, 1.0]
        base = [i / (count - 1) for i in range(count)]
        if extra <= 0:
            return base

        # Add extra positions inside the first and last interval.
        # These should add more short/tapering rungs near ends without moving
        # the main sample sequence away from the ends.
        step = 1.0 / (count - 1)
        vals = list(base)
        for k in range(1, extra + 1):
            frac = k / (extra + 1)
            vals.append(frac * step)
            vals.append(1.0 - frac * step)

        vals = sorted(max(0.0, min(1.0, v)) for v in vals)
        uniq = []
        for v in vals:
            if not uniq or abs(v - uniq[-1]) > 1e-9:
                uniq.append(v)
        return uniq

    tvals = build_t_values(n, extra_end_rungs)

    def sample_with_t(line, reverse=False):
        pts = []
        for t in tvals:
            tt = 1.0 - t if reverse else t
            tt = max(0.0, min(1.0, tt))
            p = line.interpolate(tt * line.length)
            pts.append((float(p.x), float(p.y)))
        return pts

    pts1 = sample_with_t(rail1, reverse=False)
    pts2_fwd = sample_with_t(rail2, reverse=False)
    pts2_rev = sample_with_t(rail2, reverse=True)

    def avg_dist(a, b):
        if not a or not b:
            return float("inf")
        m = min(len(a), len(b))
        total = 0.0
        for i in range(m):
            total += math.hypot(a[i][0] - b[i][0], a[i][1] - b[i][1])
        return total / max(m, 1)

    pts2 = pts2_rev if avg_dist(pts1, pts2_rev) < avg_dist(pts1, pts2_fwd) else pts2_fwd

    out = []
    seen = set()

    def best_clipped_segment(a, b):
        raw_len = math.hypot(a[0] - b[0], a[1] - b[1])
        if raw_len <= 0.15:
            return None
        if max_bar_len_px is not None and raw_len > max_bar_len_px * 1.20:
            return None

        line = LineString([a, b])
        try:
            inter = poly.buffer(0.05).intersection(line)
        except Exception:
            try:
                inter = poly.intersection(line)
            except Exception:
                return None

        segs = _line_geom_intersections_as_segments(inter)
        if not segs:
            # If the rail endpoints are on the boundary but numerical precision
            # causes the intersection to vanish, allow the original rung only
            # when its midpoint is safely inside/covered.
            mid = Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            try:
                if poly.buffer(0.45).covers(mid):
                    return [a, b]
            except Exception:
                pass
            return None

        mid = Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        segs.sort(key=lambda s: s.distance(mid))

        for seg in segs:
            if seg.length <= 0.18:
                continue
            if max_bar_len_px is not None and seg.length > max_bar_len_px * 1.20:
                continue
            coords = list(seg.coords)
            if len(coords) < 2:
                continue
            p0 = (float(coords[0][0]), float(coords[0][1]))
            p1 = (float(coords[-1][0]), float(coords[-1][1]))
            return [p0, p1]

        return None

    for a, b in zip(pts1, pts2):
        seg = best_clipped_segment(a, b)
        if seg is None:
            continue

        p0, p1 = seg
        key = (
            round(p0[0] / 0.35), round(p0[1] / 0.35),
            round(p1[0] / 0.35), round(p1[1] / 0.35),
        )
        rkey = (key[2], key[3], key[0], key[1])
        if key in seen or rkey in seen:
            continue
        seen.add(key)
        out.append(seg)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Guide rail normal satin preview lines
# ─────────────────────────────────────────────────────────────────────────────


def _guide_rail_normal_satin_preview_lines(poly: Polygon,
                                           rail1: LineString,
                                           rail2: LineString,
                                           spacing_px: float,
                                           max_bar_len_px: float | None = None) -> list:
    """
    Satin bars for open stroke-like shapes using local normal chords from one
    guide rail.

    Why this exists:
    - nearest-point rail pairing can bunch up on small bumps
    - end-cap rungs alone do not fully solve missing ends
    - the underlay/contour already shows that the actual polygon boundary is a
      very good constraint

    So for open shapes we borrow the successful ring logic:
      sample along a guide rail,
      compute the local tangent,
      turn that into the inward normal,
      intersect that normal with the actual polygon,
      use that local intersection segment as the satin bar.

    The result is constrained by the real path boundary and usually reaches the
    ends more naturally than nearest-opposite matching.
    """
    if poly is None or poly.is_empty:
        return []
    if rail1 is None or rail2 is None or rail1.length <= 1e-6 or rail2.length <= 1e-6:
        return []

    guide = rail1 if rail1.length >= rail2.length else rail2
    if guide.length <= 1e-6:
        return []

    minx, miny, maxx, maxy = poly.bounds
    local_span = max(maxx - minx, maxy - miny)
    span = local_span * 3.0 + 20.0

    n = max(5, int(math.ceil(guide.length / max(spacing_px, 0.45))) + 1)
    eps = max(0.35, min(spacing_px * 0.65, guide.length / max(n, 1)))
    probe = max(0.18, min(0.9, spacing_px * 0.35))

    def point_at(dist):
        d = max(0.0, min(guide.length, dist))
        p = guide.interpolate(d)
        return (float(p.x), float(p.y))

    def tangent_at(dist):
        d0 = max(0.0, dist - eps)
        d1 = min(guide.length, dist + eps)
        if abs(d1 - d0) < 1e-9:
            d0 = max(0.0, dist - eps * 2.0)
            d1 = min(guide.length, dist + eps * 2.0)
        p0 = guide.interpolate(d0)
        p1 = guide.interpolate(d1)
        tx, ty = (p1.x - p0.x), (p1.y - p0.y)
        mag = math.hypot(tx, ty)
        if mag <= 1e-9:
            return None
        return (tx / mag, ty / mag)

    def choose_inward_normal(px, py, tx, ty):
        candidates = [(-ty, tx), (ty, -tx)]
        # Prefer the candidate that immediately enters the polygon interior.
        for nx, ny in candidates:
            try:
                if poly.buffer(0.05).covers(Point(px + nx * probe, py + ny * probe)):
                    return (nx, ny)
            except Exception:
                pass
        # Fallback: point roughly toward representative point.
        rp = poly.representative_point()
        vx, vy = rp.x - px, rp.y - py
        vmag = math.hypot(vx, vy) or 1e-9
        return (vx / vmag, vy / vmag)

    def local_chord(px, py, nx, ny):
        # Shoot through the polygon in both directions and keep the segment
        # closest to the sampled guide-rail point.
        line = LineString([
            (px - nx * span, py - ny * span),
            (px + nx * span, py + ny * span),
        ])
        try:
            pieces = _line_geom_intersections_as_segments(poly.intersection(line))
        except Exception:
            return None
        if not pieces:
            return None

        p = Point(px, py)
        best = None
        best_d = float("inf")
        for seg in pieces:
            coords = list(seg.coords)
            if len(coords) < 2:
                continue
            a, b = coords[0], coords[-1]
            d = min(
                math.hypot(a[0] - px, a[1] - py),
                math.hypot(b[0] - px, b[1] - py),
                seg.distance(p),
            )
            if d < best_d:
                best_d = d
                best = (a, b)

        if best is None:
            return None

        a, b = best
        # Ensure a is the endpoint nearest the sampled guide rail point.
        if math.hypot(b[0] - px, b[1] - py) < math.hypot(a[0] - px, a[1] - py):
            a, b = b, a

        chord_len = math.hypot(a[0] - b[0], a[1] - b[1])
        if chord_len <= 0.25:
            return None
        if max_bar_len_px is not None and chord_len > max_bar_len_px:
            return None
        return [(float(a[0]), float(a[1])), (float(b[0]), float(b[1]))]

    out = []
    seen = set()

    for i in range(n):
        dist = (i / max(n - 1, 1)) * guide.length
        px, py = point_at(dist)
        tan = tangent_at(dist)
        if tan is None:
            continue
        nx, ny = choose_inward_normal(px, py, tan[0], tan[1])
        chord = local_chord(px, py, nx, ny)
        if chord is None:
            continue

        a, b = chord
        key = (
            round(a[0] / 0.35), round(a[1] / 0.35),
            round(b[0] / 0.35), round(b[1] / 0.35),
        )
        rkey = (key[2], key[3], key[0], key[1])
        if key in seen or rkey in seen:
            continue
        seen.add(key)
        out.append([a, b])

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Axis split outline satin preview lines
# ─────────────────────────────────────────────────────────────────────────────


def _axis_split_outline_satin_preview_lines(poly: Polygon, spacing_px: float,
                                             max_bar_len_px: float | None = None,
                                             use_guide_helper: bool = False,
                                             extra_end_rungs: int = 0) -> list:
    """
    Rail/rung satin for simple filled stroke shapes without holes.

    Boundary is split into two rails at the long-axis endpoints. Each sample on
    one rail is then connected to the nearest point on the opposite rail. This
    is more stable on tapered sun spikes than arclength rail matching.
    """
    if poly is None or poly.is_empty or poly.geom_type != "Polygon":
        return []
    if len(poly.interiors) > 0:
        return []

    coords = [(float(x), float(y)) for x, y in list(poly.exterior.coords)]
    if len(coords) < 8:
        return []
    if coords[0] == coords[-1]:
        coords = coords[:-1]
    if len(coords) < 8:
        return []

    minx, miny, maxx, maxy = poly.bounds
    spanx, spany = maxx - minx, maxy - miny
    if max(spanx, spany) < 2:
        return []

    try:
        rect = list(poly.minimum_rotated_rectangle.exterior.coords)[:-1]
        edges = []
        for i in range(4):
            x1, y1 = rect[i]
            x2, y2 = rect[(i + 1) % 4]
            edges.append((math.hypot(x2 - x1, y2 - y1), x2 - x1, y2 - y1))
        _, ax, ay = max(edges, key=lambda e: e[0])
    except Exception:
        ax, ay = (1.0, 0.0) if spanx >= spany else (0.0, 1.0)

    amag = math.hypot(ax, ay) or 1e-9
    ax, ay = ax / amag, ay / amag

    projections = [x * ax + y * ay for x, y in coords]
    i_min = min(range(len(coords)), key=lambda i: projections[i])
    i_max = max(range(len(coords)), key=lambda i: projections[i])
    if i_min == i_max:
        return []

    arc1 = _arc_coords_between(coords, i_min, i_max)
    arc2 = _arc_coords_between(coords, i_max, i_min)
    if len(arc1) < 3 or len(arc2) < 3:
        return []

    rail1 = LineString(arc1)
    rail2 = LineString(arc2)
    if rail1.length <= 1e-6 or rail2.length <= 1e-6:
        return []

    # If user-selected max satin width is generous, still cap obviously wrong
    # jumps to less than most of the object's long axis.
    local_max = max(spanx, spany) * 0.72 + 2.0
    if max_bar_len_px is not None:
        local_max = min(local_max, max_bar_len_px)

    # Primary method for open satin shapes: pure paired rails + rungs.
    pair_lines = _paired_rail_rung_satin_preview_lines(
        poly, rail1, rail2, spacing_px, local_max, extra_end_rungs=extra_end_rungs
    )
    expected = max(4, int(math.ceil(max(rail1.length, rail2.length) / max(spacing_px, 0.45))) + 1 + max(0, int(extra_end_rungs) * 2))
    if len(pair_lines) >= max(3, int(expected * 0.70)):
        return pair_lines

    # Optional helper only when explicitly enabled in the toolbar.
    if use_guide_helper:
        guide_lines = _guide_rail_normal_satin_preview_lines(
            poly, rail1, rail2, spacing_px, local_max
        )
        if guide_lines:
            if pair_lines:
                merged = _merge_satin_lines_without_duplicates(
                    pair_lines, guide_lines, tolerance_px=max(0.7, spacing_px * 0.45)
                )
                if merged:
                    return merged
            return guide_lines

    # Stay rail/rung only.  If the optional helper is off, do not silently
    # switch to nearest-opposite behaviour at the ends.
    return pair_lines


# ─────────────────────────────────────────────────────────────────────────────
# Skeleton chord satin preview lines (fallback)
# ─────────────────────────────────────────────────────────────────────────────


def _skeleton_chord_satin_preview_lines(geom, spacing_px: float, max_probe_px: float) -> list:
    """
    Fallback for unusual/branched filled shapes. The rail methods above are
    preferred because they cover stroke ends better.
    """
    lines = []
    centre_segments = _skeleton_world_segments_for_geom(geom, max_pixels=360)
    if not centre_segments:
        return lines

    spacing_px = max(1.0, float(spacing_px))
    max_probe_px = max(6.0, float(max_probe_px))

    for seg in centre_segments:
        if len(seg) < 2:
            continue

        line = LineString(seg)
        if line.length < spacing_px:
            continue

        n = max(2, int(math.ceil(line.length / spacing_px)))
        tangent_window = max(spacing_px * 2.0, 2.0)

        for i in range(n + 1):
            d = min(line.length, (i / n) * line.length)
            c = line.interpolate(d)
            p0 = line.interpolate(max(0.0, d - tangent_window))
            p1 = line.interpolate(min(line.length, d + tangent_window))
            tangent = (p1.x - p0.x, p1.y - p0.y)
            bar = _normal_crossbar_inside_geom(geom, (c.x, c.y), tangent, max_probe_px)
            if bar:
                lines.append(bar)

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Axis split outline rails for debug overlay
# ─────────────────────────────────────────────────────────────────────────────


def _axis_split_outline_rails_for_debug(poly: Polygon,
                                        max_bar_len_px: float | None = None):
    """
    Return the two rails currently used by the open-shape satin generator.

    This intentionally mirrors _axis_split_outline_satin_preview_lines(), so the
    debug overlay shows the exact rail split the satin logic is using.
    """
    if poly is None or poly.is_empty or poly.geom_type != "Polygon":
        return None
    if len(poly.interiors) > 0:
        return None

    coords = [(float(x), float(y)) for x, y in list(poly.exterior.coords)]
    if len(coords) < 8:
        return None
    if coords[0] == coords[-1]:
        coords = coords[:-1]
    if len(coords) < 8:
        return None

    minx, miny, maxx, maxy = poly.bounds
    spanx, spany = maxx - minx, maxy - miny
    if max(spanx, spany) < 2:
        return None

    try:
        rect = list(poly.minimum_rotated_rectangle.exterior.coords)[:-1]
        edges = []
        for i in range(4):
            x1, y1 = rect[i]
            x2, y2 = rect[(i + 1) % 4]
            edges.append((math.hypot(x2 - x1, y2 - y1), x2 - x1, y2 - y1))
        _, ax, ay = max(edges, key=lambda e: e[0])
    except Exception:
        ax, ay = (1.0, 0.0) if spanx >= spany else (0.0, 1.0)

    amag = math.hypot(ax, ay) or 1e-9
    ax, ay = ax / amag, ay / amag

    projections = [x * ax + y * ay for x, y in coords]
    i_min = min(range(len(coords)), key=lambda i: projections[i])
    i_max = max(range(len(coords)), key=lambda i: projections[i])
    if i_min == i_max:
        return None

    arc1 = _arc_coords_between(coords, i_min, i_max)
    arc2 = _arc_coords_between(coords, i_max, i_min)
    if len(arc1) < 3 or len(arc2) < 3:
        return None

    rail1 = LineString(arc1)
    rail2 = LineString(arc2)
    if rail1.length <= 1e-6 or rail2.length <= 1e-6:
        return None

    local_max = max(spanx, spany) * 0.72 + 2.0
    if max_bar_len_px is not None:
        local_max = min(local_max, max_bar_len_px)

    return rail1, rail2, local_max, (i_min, i_max)


# ─────────────────────────────────────────────────────────────────────────────
# Satin debug overlay SVG builder
# ─────────────────────────────────────────────────────────────────────────────


def build_satin_debug_overlay_svg(geom, spacing_px: float, max_probe_px: float,
                                  extra_end_rungs: int = 0) -> tuple[str, dict]:
    """
    Draw diagnostic information for satin objects:
      - open-shape rail 1: bright green
      - open-shape rail 2: cyan
      - start/end rail markers
      - paired-rung candidate count
      - ring rails: green exterior, cyan interior
    """
    if geom is None or geom.is_empty:
        return "", {"debug_open_rails": 0, "debug_ring_rails": 0}

    chunks = []
    counts = {"debug_open_rails": 0, "debug_ring_rails": 0, "debug_pair_candidates": 0}

    for poly in _geometry_polygons(geom):
        if poly.is_empty:
            continue
        if not poly.is_valid:
            poly = poly.buffer(0)
            if poly.is_empty:
                continue

        if len(poly.interiors) > 0:
            ext_pts = [(float(x), float(y)) for x, y in list(poly.exterior.coords)]
            chunks.append(_svg_debug_polyline(ext_pts, "#00ff66", 2.4, 0.95))
            if ext_pts:
                chunks.append(_svg_debug_dot(ext_pts[0][0], ext_pts[0][1], "#ffff00", 3.2))
                chunks.append(_svg_debug_text(ext_pts[0][0] + 4, ext_pts[0][1] - 4, "outer rail", "#00ff66"))
            for interior in poly.interiors:
                inner_pts = [(float(x), float(y)) for x, y in list(interior.coords)]
                chunks.append(_svg_debug_polyline(inner_pts, "#00d9ff", 2.4, 0.95))
                if inner_pts:
                    chunks.append(_svg_debug_dot(inner_pts[0][0], inner_pts[0][1], "#ff66ff", 3.2))
            counts["debug_ring_rails"] += 1
            continue

        rail_info = _axis_split_outline_rails_for_debug(poly, max_probe_px * 2.0)
        if not rail_info:
            continue

        rail1, rail2, local_max, idxs = rail_info
        r1 = [(float(x), float(y)) for x, y in rail1.coords]
        r2 = [(float(x), float(y)) for x, y in rail2.coords]

        chunks.append(_svg_debug_polyline(r1, "#00ff66", 2.8, 0.98))
        chunks.append(_svg_debug_polyline(r2, "#00d9ff", 2.8, 0.98))

        if r1:
            chunks.append(_svg_debug_dot(r1[0][0], r1[0][1], "#ffff00", 3.4))
            chunks.append(_svg_debug_text(r1[0][0] + 4, r1[0][1] - 5, "R1 start", "#00ff66"))
            chunks.append(_svg_debug_dot(r1[-1][0], r1[-1][1], "#ff9900", 3.4))
            chunks.append(_svg_debug_text(r1[-1][0] + 4, r1[-1][1] - 5, "R1 end", "#00ff66"))
        if r2:
            chunks.append(_svg_debug_dot(r2[0][0], r2[0][1], "#ff66ff", 3.4))
            chunks.append(_svg_debug_text(r2[0][0] + 4, r2[0][1] + 12, "R2 start", "#00d9ff"))
            chunks.append(_svg_debug_dot(r2[-1][0], r2[-1][1], "#9966ff", 3.4))
            chunks.append(_svg_debug_text(r2[-1][0] + 4, r2[-1][1] + 12, "R2 end", "#00d9ff"))

        pair_lines = _paired_rail_rung_satin_preview_lines(
            poly, rail1, rail2, spacing_px, local_max, extra_end_rungs=extra_end_rungs
        )
        counts["debug_pair_candidates"] += len(pair_lines)

        # Draw every 6th candidate rung in orange so the user can see the
        # rail-to-rail pairing direction without overwhelming the preview.
        for i, line in enumerate(pair_lines):
            if i % 6 == 0:
                chunks.append(_svg_debug_polyline(line, "#ff7a00", 1.2, 0.82, "2 2"))

        counts["debug_open_rails"] += 1

    return "".join(chunks), counts


# ─────────────────────────────────────────────────────────────────────────────
# Top-level satin preview line generator
# ─────────────────────────────────────────────────────────────────────────────


def generate_satin_preview_lines(geom, spacing_px: float, max_probe_px: float,
                                 use_guide_helper: bool = False,
                                 extra_end_rungs: int = 0) -> list:
    """
    Generate satin-like bars constrained inside selected satin objects.

    Hybrid approach:
      1. Ring/outline objects with holes use two rails: exterior ↔ interior.
      2. Simple filled stroke objects use boundary split into two rails.
      3. Only unusual/branched shapes fall back to centreline-skeleton chords.

    This is specifically intended to reduce the 20–40% missing-gaps behaviour
    from skeleton-only satin preview.
    """
    lines = []
    if geom is None or geom.is_empty:
        return lines

    spacing_px = max(0.45, float(spacing_px))
    max_probe_px = max(6.0, float(max_probe_px))

    for poly in _geometry_polygons(geom):
        if poly.is_empty:
            continue

        if not poly.is_valid:
            poly = poly.buffer(0)
            if poly.is_empty:
                continue

        if len(poly.interiors) > 0:
            piece_lines = _ring_rail_satin_preview_lines(poly, spacing_px, max_probe_px * 2.0)
        else:
            piece_lines = _axis_split_outline_satin_preview_lines(poly, spacing_px, max_probe_px * 2.0, use_guide_helper=use_guide_helper, extra_end_rungs=extra_end_rungs)

        if not piece_lines:
            piece_lines = _skeleton_chord_satin_preview_lines(poly, spacing_px, max_probe_px)

        lines.extend(piece_lines)

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Clip rung segment to polygon
# ─────────────────────────────────────────────────────────────────────────────


def _clip_rung_segment_to_poly(poly: Polygon, a, b, max_len_px: float | None = None):
    raw_len = math.hypot(a[0] - b[0], a[1] - b[1])
    if raw_len <= 0.15:
        return None
    if max_len_px is not None and raw_len > max_len_px * 1.35:
        return None

    line = LineString([a, b])
    try:
        inter = poly.buffer(0.05).intersection(line)
    except Exception:
        try:
            inter = poly.intersection(line)
        except Exception:
            return None

    segs = _line_geom_intersections_as_segments(inter)
    if not segs:
        mid = Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        try:
            if poly.buffer(0.45).covers(mid):
                return [a, b]
        except Exception:
            pass
        return None

    mid = Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    segs.sort(key=lambda s: s.distance(mid))

    for seg in segs:
        if seg.length <= 0.18:
            continue
        if max_len_px is not None and seg.length > max_len_px * 1.35:
            continue
        coords = list(seg.coords)
        if len(coords) < 2:
            continue
        return [
            (float(coords[0][0]), float(coords[0][1])),
            (float(coords[-1][0]), float(coords[-1][1])),
        ]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Guided satin preview lines (manual rungs as checkpoints)
# ─────────────────────────────────────────────────────────────────────────────


def generate_guided_satin_preview_lines(poly: Polygon,
                                         spacing_px: float,
                                         max_bar_len_px: float,
                                         manual_rungs_for_obj: list,
                                         extra_end_rungs: int = 0) -> list:
    """
    Generate satin from rails using user manual rungs as guide checkpoints.

    Manual rungs are projected onto the two rails.  Between each pair of guide
    checkpoints, rail positions are interpolated locally instead of using one
    global 0..1 mapping for the whole curve.  This is closer to the Ink/Stitch
    rails+rungs model and should fix phase drift on long curves.
    """
    if not manual_rungs_for_obj:
        return []

    rail_info = _axis_split_outline_rails_for_debug(poly, max_bar_len_px)
    if not rail_info:
        return []

    rail1, rail2_raw, local_max, _ = rail_info
    if rail1.length <= 1e-6 or rail2_raw.length <= 1e-6:
        return []

    # Try rail2 in both directions.  Choose the orientation that makes the
    # manual rungs most consistent and low-error.
    coords2 = list(rail2_raw.coords)
    rail2_rev = LineString(list(reversed(coords2))) if len(coords2) >= 2 else rail2_raw

    def project_point_to_line(line, p):
        pt = Point(float(p[0]), float(p[1]))
        d = line.project(pt)
        q = line.interpolate(d)
        err = math.hypot(q.x - pt.x, q.y - pt.y)
        t = d / max(line.length, 1e-9)
        return t, err

    def build_checkpoints(rail2):
        cps = [(0.0, 0.0, "start"), (1.0, 1.0, "end")]
        total_err = 0.0

        for idx, rung in enumerate(manual_rungs_for_obj):
            a = rung.get("a")
            b = rung.get("b")
            if not a or not b:
                continue

            a1, ea1 = project_point_to_line(rail1, a)
            b2, eb2 = project_point_to_line(rail2, b)
            b1, eb1 = project_point_to_line(rail1, b)
            a2, ea2 = project_point_to_line(rail2, a)

            # Either endpoint may have been clicked first.  Pick the assignment
            # where one endpoint belongs to each rail.
            err_ab = ea1 + eb2
            err_ba = eb1 + ea2
            if err_ab <= err_ba:
                t1, t2, err = a1, b2, err_ab
            else:
                t1, t2, err = b1, a2, err_ba

            # Ignore accidental guide rungs nowhere near the rails.
            if err > max(3.0, spacing_px * 2.5):
                continue

            src = rung.get("source") or "manual_rung"
            cps.append((max(0.0, min(1.0, t1)), max(0.0, min(1.0, t2)), f"manual{idx}:{src}"))
            total_err += err

        cps.sort(key=lambda x: x[0])

        # De-duplicate very close checkpoints, keeping manual points over auto
        # endpoints if they are distinct enough.
        clean = []
        for cp in cps:
            if clean and abs(cp[0] - clean[-1][0]) < 0.015:
                if cp[2].startswith("manual") and not clean[-1][2].startswith("manual"):
                    clean[-1] = cp
                continue
            clean.append(cp)

        # Penalise very non-monotonic rail2 mapping.
        mono_penalty = 0.0
        for i in range(1, len(clean)):
            if clean[i][1] < clean[i - 1][1] - 0.03:
                mono_penalty += 25.0

        return clean, total_err + mono_penalty

    cps_fwd, err_fwd = build_checkpoints(rail2_raw)
    cps_rev, err_rev = build_checkpoints(rail2_rev)

    if len(cps_rev) > len(cps_fwd) or (len(cps_rev) == len(cps_fwd) and err_rev < err_fwd):
        rail2 = rail2_rev
        checkpoints = cps_rev
    else:
        rail2 = rail2_raw
        checkpoints = cps_fwd

    # Need at least one real guide checkpoint.  Cut-derived guide rungs often
    # sit exactly at the generated start/end and replace those endpoint
    # checkpoints during de-duplication.  That can leave only two checkpoints
    # total, which is still valid: it means "generate satin between the two
    # user-cut end rungs".
    if len(checkpoints) < 2 or not any(str(cp[2]).startswith("manual") for cp in checkpoints):
        return []

    # If rail2 mapping is still non-monotonic, sort by average progress.  This
    # is a fallback for odd drawings but keeps things stable.
    fixed = [checkpoints[0]]
    for cp in checkpoints[1:-1]:
        fixed.append(cp)
    fixed.append(checkpoints[-1])
    checkpoints = fixed

    def point_pair(t1, t2):
        p1 = rail1.interpolate(max(0.0, min(1.0, t1)) * rail1.length)
        p2 = rail2.interpolate(max(0.0, min(1.0, t2)) * rail2.length)
        return (float(p1.x), float(p1.y)), (float(p2.x), float(p2.y))

    def t_values_for_segment(n, extra_first=False, extra_last=False):
        if n <= 1:
            vals = [0.0, 1.0]
        else:
            vals = [i / (n - 1) for i in range(n)]
        extra = max(0, int(extra_end_rungs))
        if extra > 0 and (extra_first or extra_last):
            step = 1.0 / max(n - 1, 1)
            for k in range(1, extra + 1):
                frac = k / (extra + 1)
                if extra_first:
                    vals.append(frac * step)
                if extra_last:
                    vals.append(1.0 - frac * step)
        return sorted(set(round(max(0.0, min(1.0, v)), 9) for v in vals))

    out = []
    seen = set()

    def add_forced_bar(a, b):
        if not a or not b:
            return
        clipped = _clip_rung_segment_to_poly(poly, a, b, max_bar_len_px)
        if not clipped:
            return
        p0, p1 = clipped
        key = (
            round(p0[0] / 0.35), round(p0[1] / 0.35),
            round(p1[0] / 0.35), round(p1[1] / 0.35),
        )
        rkey = (key[2], key[3], key[0], key[1])
        if key in seen or rkey in seen:
            return
        seen.add(key)
        out.append(clipped)

    # Add split-cut rungs as hard first/last bars.  User-supplied normal manual
    # rungs still act as guide checkpoints, but cut rungs are special because
    # they represent a deliberate perpendicular segment boundary.
    for rung in manual_rungs_for_obj:
        if not isinstance(rung, dict):
            continue
        if rung.get("source") == "manual_split_cut":
            add_forced_bar(rung.get("a"), rung.get("b"))

    for si in range(len(checkpoints) - 1):
        t1a, t2a, _ = checkpoints[si]
        t1b, t2b, _ = checkpoints[si + 1]
        if abs(t1b - t1a) < 1e-6 and abs(t2b - t2a) < 1e-6:
            continue

        seg_len = max(abs(t1b - t1a) * rail1.length, abs(t2b - t2a) * rail2.length)
        n = max(2, int(math.ceil(seg_len / max(spacing_px, 0.45))) + 1)

        for u in t_values_for_segment(n, extra_first=(si == 0), extra_last=(si == len(checkpoints) - 2)):
            t1 = t1a * (1 - u) + t1b * u
            t2 = t2a * (1 - u) + t2b * u
            a, b = point_pair(t1, t2)
            clipped = _clip_rung_segment_to_poly(poly, a, b, max_bar_len_px)
            if not clipped:
                continue

            p0, p1 = clipped
            key = (
                round(p0[0] / 0.35), round(p0[1] / 0.35),
                round(p1[0] / 0.35), round(p1[1] / 0.35),
            )
            rkey = (key[2], key[3], key[0], key[1])
            if key in seen or rkey in seen:
                continue
            seen.add(key)
            out.append(clipped)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Satin preview lines with guides (top-level)
# ─────────────────────────────────────────────────────────────────────────────


def generate_satin_preview_lines_with_guides(geom,
                                             spacing_px: float,
                                             max_probe_px: float,
                                             manual_rungs_for_obj: list,
                                             use_guide_helper: bool = False,
                                             extra_end_rungs: int = 0) -> list:
    """
    Generate satin lines using manual rungs as guide checkpoints where possible.
    Falls back to normal auto satin for parts without usable guide rungs.
    """
    if geom is None or geom.is_empty:
        return []

    spacing_px = max(0.45, float(spacing_px))
    max_probe_px = max(6.0, float(max_probe_px))
    out = []

    for poly in _geometry_polygons(geom):
        if poly.is_empty:
            continue
        if not poly.is_valid:
            poly = poly.buffer(0)
            if poly.is_empty:
                continue

        if len(poly.interiors) > 0:
            out.extend(_ring_rail_satin_preview_lines(poly, spacing_px, max_probe_px * 2.0))
            continue

        guided = generate_guided_satin_preview_lines(
            poly,
            spacing_px,
            max_probe_px * 2.0,
            manual_rungs_for_obj,
            extra_end_rungs=extra_end_rungs
        )
        if guided:
            out.extend(guided)
        else:
            out.extend(
                _axis_split_outline_satin_preview_lines(
                    poly,
                    spacing_px,
                    max_probe_px * 2.0,
                    use_guide_helper=use_guide_helper,
                    extra_end_rungs=extra_end_rungs
                )
            )

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Clip manual rung to geometry
# ─────────────────────────────────────────────────────────────────────────────


def clip_manual_rung_to_geometry(geom, p1, p2, max_len_px: float | None = None):
    """
    Clip a user supplied manual guide-rung to the actual satin object geometry.
    Returns a two-point line suitable for preview/stitch output, or None.
    """
    if geom is None or geom.is_empty:
        return None
    try:
        a = (float(p1[0]), float(p1[1]))
        b = (float(p2[0]), float(p2[1]))
    except Exception:
        return None

    raw_len = math.hypot(a[0] - b[0], a[1] - b[1])
    if raw_len <= 0.2:
        return None
    if max_len_px is not None and raw_len > max_len_px * 1.35:
        return None

    line = LineString([a, b])
    try:
        inter = geom.buffer(0.05).intersection(line)
    except Exception:
        try:
            inter = geom.intersection(line)
        except Exception:
            return None

    segs = _line_geom_intersections_as_segments(inter)
    if not segs:
        mid = Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        try:
            if geom.buffer(0.45).covers(mid):
                return [a, b]
        except Exception:
            pass
        return None

    midpoint = Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    segs.sort(key=lambda s: s.distance(midpoint))

    for seg in segs:
        if seg.length <= 0.2:
            continue
        if max_len_px is not None and seg.length > max_len_px * 1.35:
            continue
        coords = list(seg.coords)
        if len(coords) < 2:
            continue
        return [
            (float(coords[0][0]), float(coords[0][1])),
            (float(coords[-1][0]), float(coords[-1][1])),
        ]

    return None
