#!/usr/bin/env python3
"""
EasyStitch Core — Geometry and shape operations.

Extracted from the monolith: functions for constructing shapely geometry from
SVG paths, splitting/cutting geometry, preview generation, skeleton extraction,
and related geometric utilities using shapely and numpy.
"""

import math

import numpy as np

from shapely.geometry import Polygon, MultiPolygon, LineString, Point, box
from shapely.ops import split as shapely_split, polygonize, unary_union, transform
from shapely import affinity

from .utils import (
    _rotate_xy,
    _rotate_geom,
    _neighbors8,
    _simplify_points,
    _points_to_svg_path,
    mm_to_px,
    _polyline_length,
    NeedSecondCutError,
)


# ─────────────────────────────────────────────────────────────────────────────
# Sharp corner detection (for road-marking graph)
# ─────────────────────────────────────────────────────────────────────────────


def detect_sharp_corners(polygon, angle_threshold_deg: float = 90.0,
                        simplify_px: float = None,
                        spatial_radius_px: float = 12.0) -> list:
    """
    Detect sharp corners on a polygon boundary.

    The polygon is first simplified with tolerance *simplify_px*
    (auto-computed from perimeter if None) to collapse densely-sampled
    traced curves into a manageable set of structural vertices.
    Then each vertex of the simplified ring is checked: the angle
    between incoming and outgoing edges is computed, and vertices whose
    turning-angle deviation (= 180° - interior_angle) is below
    *angle_threshold_deg* are returned as sharp corners.
    Nearby qualifying vertices are spatially deduplicated.

    Parameters
    ----------
    polygon : shapely.geometry.Polygon
    angle_threshold_deg : float
        Maximum deviation to qualify as sharp (lower = sharper; a
        right-angle turn has deviation ≈ 90°).  Default 90° catches
        right-angle turns and sharper.  Use 100-110° for moderate
        turns like ear junctions.
    simplify_px : float or None
        Simplification tolerance in pixels.  If None, auto-computed
        as perimeter / 60 (≈ 2-4% of vertices kept).
    spatial_radius_px : float
        Deduplication radius in pixels.

    Returns
    -------
    list of ((float, float), float)
        Each element is ((x, y), deviation_degrees) for a sharp corner,
        in boundary-traversal order.
    """
    coords_full = list(polygon.exterior.coords)
    if len(coords_full) > 1 and coords_full[0] == coords_full[-1]:
        coords_full = coords_full[:-1]
    n_full = len(coords_full)
    if n_full < 3:
        return []

    # ── Simplify ─────────────────────────────────────────────────────────
    if simplify_px is None:
        perimeter = 0.0
        for i in range(n_full):
            j = (i + 1) % n_full
            perimeter += math.hypot(
                coords_full[i][0] - coords_full[j][0],
                coords_full[i][1] - coords_full[j][1])
        simplify_px = max(2.0, perimeter / 100.0)

    simplified = polygon.simplify(simplify_px, preserve_topology=True)
    if simplified.is_empty or simplified.geom_type != 'Polygon':
        simplified = polygon  # fallback

    coords = list(simplified.exterior.coords)
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    n = len(coords)
    if n < 3:
        return []

    # ── Find sharp corners ──────────────────────────────────────────────
    candidates: list[tuple[tuple[float, float], float, int]] = []
    for i in range(n):
        prev = coords[(i - 1) % n]
        curr = coords[i]
        nxt = coords[(i + 1) % n]

        ix = curr[0] - prev[0]
        iy = curr[1] - prev[1]
        ox = nxt[0] - curr[0]
        oy = nxt[1] - curr[1]

        im = math.hypot(ix, iy)
        om = math.hypot(ox, oy)
        if im < 1e-9 or om < 1e-9:
            continue

        cos_a = (ix * ox + iy * oy) / (im * om)
        cos_a = max(-1.0, min(1.0, cos_a))
        angle_deg = math.degrees(math.acos(cos_a))
        deviation = 180.0 - angle_deg  # low = sharp corner, high = straight

        if deviation < angle_threshold_deg:
            candidates.append((
                (float(curr[0]), float(curr[1])),
                deviation,
                i,
            ))

    if not candidates:
        return []

    # ── Spatial deduplication ───────────────────────────────────────────
    candidates.sort(key=lambda c: c[1])  # sharpest first (lowest dev)
    kept: list[tuple[int, tuple[tuple[float, float], float]]] = []

    for pos, dev, ri in candidates:
        too_close = False
        for _, (kpos, _) in kept:
            if math.hypot(pos[0] - kpos[0], pos[1] - kpos[1]) < spatial_radius_px:
                too_close = True
                break
        if not too_close:
            kept.append((ri, (pos, dev)))

    kept.sort(key=lambda item: item[0])
    return [item[1] for item in kept]


# ─────────────────────────────────────────────────────────────────────────────
# Junction detection (Stage 2 — Auto-detect narrow waists)
# ─────────────────────────────────────────────────────────────────────────────


def _local_width_at_point(polygon, point, max_width: float = 500.0) -> float:
    """
    Compute the local interior width of a polygon at a boundary point.

    Finds the closest vertex on the boundary, computes the inward normal
    direction, casts a ray inward, and measures the distance to the
    opposite boundary intersection.

    Returns 0.0 if the width cannot be determined.
    """
    coords = list(polygon.exterior.coords)
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    n = len(coords)
    if n < 3:
        return 0.0

    px, py = float(point[0]), float(point[1])

    # Find closest vertex on the boundary
    best_i = 0
    best_d = float("inf")
    for i, (cx, cy) in enumerate(coords):
        d = math.hypot(px - cx, py - cy)
        if d < best_d:
            best_d = d
            best_i = i

    # Compute tangent at that vertex using neighbours
    prev_i = (best_i - 1) % n
    next_i = (best_i + 1) % n
    dx = coords[next_i][0] - coords[prev_i][0]
    dy = coords[next_i][1] - coords[prev_i][1]
    tlen = math.hypot(dx, dy)
    if tlen < 1e-9:
        return 0.0

    # Right-pointing normal (rotate tangent 90° CW)
    nx = -dy / tlen
    ny = dx / tlen

    # Determine which normal direction points inward
    test_dist = min(2.0, max_width * 0.01)
    inward_found = False
    in_nx = nx
    in_ny = ny
    for sign in (1.0, -1.0):
        tx = px + nx * sign * test_dist
        ty = py + ny * sign * test_dist
        try:
            if polygon.contains(Point(tx, ty)):
                in_nx = nx * sign
                in_ny = ny * sign
                inward_found = True
                break
        except Exception:
            continue

    if not inward_found:
        # Fallback: try both sides and pick whichever gives a longer intersection
        candidates = []
        for sign in (1.0, -1.0):
            try:
                ray = LineString([
                    (px + nx * sign * 0.5, py + ny * sign * 0.5),
                    (px + nx * sign * max_width, py + ny * sign * max_width),
                ])
                inter = polygon.boundary.intersection(ray)
                if not inter.is_empty:
                    if inter.geom_type == "Point":
                        candidates.append((sign, math.hypot(inter.x - px, inter.y - py)))
                    elif inter.geom_type == "MultiPoint":
                        pts_list = list(inter.geoms)
                        if pts_list:
                            d = math.hypot(pts_list[0].x - px, pts_list[0].y - py)
                            candidates.append((sign, d))
            except Exception:
                continue
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            in_nx = nx * candidates[0][0]
            in_ny = ny * candidates[0][0]
        else:
            return 0.0

    # Cast ray inward from just inside the boundary
    try:
        ray = LineString([
            (px + in_nx * 0.5, py + in_ny * 0.5),
            (px + in_nx * max_width, py + in_ny * max_width),
        ])
        inter = polygon.boundary.intersection(ray)
    except Exception:
        return 0.0

    if inter.is_empty:
        return 0.0

    # Parse intersection result
    if inter.geom_type == "Point":
        return math.hypot(inter.x - px, inter.y - py)
    elif inter.geom_type == "MultiPoint":
        pts_list = list(inter.geoms)
        # Find the closest intersection that isn't right at the boundary point
        min_d = float("inf")
        for pt_geom in pts_list:
            d = math.hypot(pt_geom.x - px, pt_geom.y - py)
            if 0.5 < d < min_d:
                min_d = d
        return min_d if min_d < float("inf") else 0.0
    elif inter.geom_type in ("LineString", "MultiLineString"):
        segs = _line_geom_intersections_as_segments(inter)
        if segs:
            coords_inter = list(segs[0].coords)
            if len(coords_inter) >= 2:
                return math.hypot(
                    coords_inter[-1][0] - coords_inter[0][0],
                    coords_inter[-1][1] - coords_inter[0][1],
                )

    return 0.0


def _cluster_points(points, radius: float) -> list:
    """
    Simple distance-based clustering of 2D points.

    For each point, add it to an existing cluster if within *radius*
    of any cluster member; otherwise create a new cluster.

    Returns a list of clusters, where each cluster is a list of
    (x, y) tuples.
    """
    clusters: list[list[tuple[float, float]]] = []
    for pt in points:
        added = False
        for cluster in clusters:
            for member in cluster:
                if math.hypot(pt[0] - member[0], pt[1] - member[1]) <= radius:
                    cluster.append(pt)
                    added = True
                    break
            if added:
                break
        if not added:
            clusters.append([pt])
    return clusters


def detect_junctions(polygon, stitch_spacing: float = 20.0) -> list:
    """
    Detect narrow-waist junction points on a polygon boundary (Stage 2).

    For each boundary point *p*, finds the closest non-adjacent boundary
    point *q*.  If distance(p, q) < 3 × stitch_spacing AND the local
    shape width at *p* exceeds 5 × stitch_spacing, a junction candidate
    is recorded at the midpoint of (p, q).

    Nearby candidates (within 2 × stitch_spacing) are clustered into
    single junction nodes.  The centroid of each cluster is returned.

    Parameters
    ----------
    polygon : shapely.geometry.Polygon
        The polygon to scan for narrow waists.
    stitch_spacing : float
        Nominal stitch spacing in pixels (default 20.0 for 6-ply satin).

    Returns
    -------
    list of (float, float)
        Junction midpoints, each as an (x, y) tuple.
    """
    coords = list(polygon.exterior.coords)
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    n = len(coords)
    if n < 5:
        return []

    candidates = []

    for i in range(n):
        p = coords[i]

        # Find closest non-adjacent point
        best_j = -1
        best_dist = float("inf")
        for j in range(n):
            # Skip adjacent (±2 indices, and wrap-around adjacency)
            if abs(i - j) <= 2:
                continue
            # Skip wrap-around adjacent (e.g., i=0, j=n-1)
            if abs(i - j) >= n - 2:
                continue

            q = coords[j]
            dist = math.hypot(p[0] - q[0], p[1] - q[1])
            if dist < best_dist:
                best_dist = dist
                best_j = j

        if best_j < 0 or best_dist >= 3.0 * stitch_spacing:
            continue

        q = coords[best_j]

        # Width check: is the shape wide at p?
        width = _local_width_at_point(polygon, p)
        if width > 5.0 * stitch_spacing:
            mid_x = (p[0] + q[0]) / 2.0
            mid_y = (p[1] + q[1]) / 2.0
            candidates.append((mid_x, mid_y))

    if not candidates:
        return []

    # Cluster nearby candidates
    clusters = _cluster_points(candidates, 2.0 * stitch_spacing)

    # Return centroid of each cluster
    junctions = []
    for cluster in clusters:
        if not cluster:
            continue
        cx = sum(pt[0] for pt in cluster) / len(cluster)
        cy = sum(pt[1] for pt in cluster) / len(cluster)
        junctions.append((cx, cy))

    return junctions


# ─────────────────────────────────────────────────────────────────────────────
# Geometry construction from SVG paths
# ─────────────────────────────────────────────────────────────────────────────


def _sample_subpath_points(subpath, tx: float = 0.0, ty: float = 0.0, target_step: float = 4.0) -> list:
    try:
        length = float(abs(subpath.length(error=1e-3)))
    except Exception:
        length = 40.0
    n = max(16, min(300, int(length / max(1.0, target_step)) + 1))
    pts = []
    for i in range(n + 1):
        t = i / n
        z = subpath.point(t)
        pts.append((float(z.real) + tx, float(z.imag) + ty))
    out = []
    for x, y in pts:
        if not out or math.hypot(out[-1][0] - x, out[-1][1] - y) > 0.2:
            out.append((x, y))
    return out


def _close_ring(points: list) -> list:
    if not points:
        return points
    if math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]) > 0.5:
        points = points + [points[0]]
    return points


def object_fill_geometry(obj: dict):
    """
    Convert a fill-region SVG path object into shapely geometry.
    Preserves inner contours where possible by sampling subpaths.
    """
    if parse_path is None or Polygon is None:
        return None

    try:
        parsed = parse_path(obj["d"])
        subpaths = parsed.continuous_subpaths()
    except Exception:
        return None

    rings = []
    for sp in subpaths:
        pts = _close_ring(_sample_subpath_points(sp, obj.get("tx", 0.0), obj.get("ty", 0.0)))
        if len(pts) < 4:
            continue
        try:
            poly = Polygon(pts)
        except Exception:
            continue
        if poly.is_empty:
            continue
        rings.append({
            "points": pts,
            "poly": poly,
            "area": abs(poly.area),
        })

    if not rings:
        return None

    parents = {i: None for i in range(len(rings))}
    for i, child in enumerate(rings):
        child_pt = child["poly"].representative_point()
        candidates = []
        for j, outer in enumerate(rings):
            if i == j:
                continue
            if outer["area"] <= child["area"] * 1.01:
                continue
            try:
                if outer["poly"].buffer(0.01).contains(child_pt):
                    candidates.append((outer["area"], j))
            except Exception:
                pass
        if candidates:
            candidates.sort()
            parents[i] = candidates[0][1]

    polys = []
    for i, ring in enumerate(rings):
        if parents[i] is not None:
            continue
        holes = [rings[j]["points"] for j, p in parents.items() if p == i]
        try:
            poly = Polygon(ring["points"], holes)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty:
                polys.append(poly)
        except Exception:
            continue

    if not polys:
        return None
    if len(polys) == 1:
        return polys[0]
    return MultiPolygon(polys)


def geometry_to_svg_d(geom) -> str:
    if geom is None or geom.is_empty:
        return ""
    geoms = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
    chunks = []

    def ring_to_d(coords):
        pts = list(coords)
        if len(pts) < 3:
            return ""
        s = f"M {pts[0][0]:.2f} {pts[0][1]:.2f}"
        for x, y in pts[1:]:
            s += f" L {x:.2f} {y:.2f}"
        s += " Z"
        return s

    for poly in geoms:
        d = ring_to_d(poly.exterior.coords)
        if d:
            chunks.append(d)
        for interior in poly.interiors:
            d = ring_to_d(interior.coords)
            if d:
                chunks.append(d)
    return " ".join(chunks)


# ─────────────────────────────────────────────────────────────────────────────
# Cut line helpers
# ─────────────────────────────────────────────────────────────────────────────


def extend_cut_line_local(p1: tuple, p2: tuple, amount: float = 2.0) -> list:
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return [p1, p2]
    ux, uy = dx / dist, dy / dist
    return [
        (p1[0] - ux * amount, p1[1] - uy * amount),
        (p2[0] + ux * amount, p2[1] + uy * amount),
    ]


def _intersection_count(geom) -> int:
    if geom.is_empty:
        return 0
    gt = geom.geom_type
    if gt == "Point":
        return 1
    if gt == "MultiPoint":
        return len(list(geom.geoms))
    if gt == "GeometryCollection":
        total = 0
        for g in geom.geoms:
            total += _intersection_count(g)
        return total
    if gt in ("LineString", "MultiLineString"):
        # a line overlap is not a clean cut for our use
        return 999
    return 0


def _build_local_cut_line(p1: tuple, p2: tuple):
    return LineString(extend_cut_line_local(p1, p2, amount=2.0))


def _filter_polygonized_parts(geom, pieces):
    out = []
    seen = set()
    for poly in pieces:
        if poly.is_empty:
            continue
        try:
            clipped = poly.intersection(geom).buffer(0)
        except Exception:
            clipped = poly.buffer(0)
        if clipped.is_empty:
            continue
        subgeoms = [clipped] if clipped.geom_type == "Polygon" else list(getattr(clipped, "geoms", []))
        for sg in subgeoms:
            if sg.is_empty or sg.area <= 1.0:
                continue
            key = sg.wkb
            if key in seen:
                continue
            seen.add(key)
            out.append(sg)
    out.sort(key=lambda g: g.area, reverse=True)
    return out


def _split_fill_geometry_with_lines(geom, cut_lines):
    merged = unary_union([geom.boundary, *cut_lines])
    pieces = list(polygonize(merged))
    return _filter_polygonized_parts(geom, pieces)


def _manual_cut_segments_from_original_geom(original_geom, cut_lines: list) -> list:
    """
    Clip each user cut line to the original object before splitting.

    This is more reliable than intersecting the cut line with each resulting
    split part: after polygon splitting, the cut edge is often represented as a
    boundary-only segment and may come back as points/empty due precision.
    """
    out = []
    if original_geom is None or original_geom.is_empty:
        return out

    for idx, line in enumerate(cut_lines or []):
        try:
            inter = original_geom.intersection(line)
            segs = _line_geom_intersections_as_segments(inter)
        except Exception:
            segs = []

        segs = [s for s in segs if getattr(s, "length", 0.0) > 0.5]
        if not segs:
            continue

        seg = max(segs, key=lambda s: s.length)
        coords = list(seg.coords)
        if len(coords) < 2:
            continue

        a = [float(coords[0][0]), float(coords[0][1])]
        b = [float(coords[-1][0]), float(coords[-1][1])]
        if math.hypot(a[0] - b[0], a[1] - b[1]) <= 0.5:
            continue

        mid = Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        out.append({
            "a": a,
            "b": b,
            "source": "manual_split_cut",
            "cut_index": idx,
            "_mid": mid,
            "_line": LineString([tuple(a), tuple(b)]),
        })

    return out


def _cut_line_guide_rungs_for_part(part_geom, original_cut_segments: list) -> list:
    """
    Attach original cut segments to every split piece that touches that cut.

    The same cut rung is intentionally attached to both neighbouring split
    pieces, because it is a useful terminal rung for each new satin column.
    """
    rungs = []
    if part_geom is None or part_geom.is_empty:
        return rungs

    for seg in original_cut_segments or []:
        mid = seg.get("_mid")
        line = seg.get("_line")
        if mid is None or line is None:
            continue

        try:
            # A real split piece should have this cut segment on or very close
            # to its boundary.  The buffered covers check handles tiny numeric
            # precision errors.
            boundary_dist = part_geom.boundary.distance(mid)
            touches = boundary_dist <= 1.25 or part_geom.boundary.buffer(1.25).intersects(line)
            insideish = part_geom.buffer(1.25).covers(mid)
            if not (touches and insideish):
                continue
        except Exception:
            continue

        rungs.append({
            "a": list(seg["a"]),
            "b": list(seg["b"]),
            "source": "manual_split_cut",
            "cut_index": int(seg.get("cut_index", 0)),
        })

    return rungs


def _junction_cut_lines_from_points(center, branch_points: list,
                                    min_offset_px: float = 4.0,
                                    max_offset_px: float = 18.0,
                                    min_half_len_px: float = 7.0,
                                    max_half_len_px: float = 32.0) -> list:
    """
    Build local cap/cut lines for an N-way junction.

    User workflow:
      centre click = junction centre
      each branch click = direction of a branch

    For every branch, create a short cap line perpendicular to that branch,
    slightly away from the centre.  These cap lines split each branch off from
    the shared junction hub and also become guide rungs for satin.
    """
    cx, cy = float(center[0]), float(center[1])
    lines = []

    for bp in branch_points:
        bx, by = float(bp[0]), float(bp[1])
        dx, dy = bx - cx, by - cy
        dist = math.hypot(dx, dy)
        if dist < 2.0:
            continue

        ux, uy = dx / dist, dy / dist
        nx, ny = -uy, ux

        offset = min(max(min_offset_px, dist * 0.28), max_offset_px)
        half_len = min(max(min_half_len_px, dist * 0.32), max_half_len_px)

        mx, my = cx + ux * offset, cy + uy * offset
        p1 = (mx - nx * half_len, my - ny * half_len)
        p2 = (mx + nx * half_len, my + ny * half_len)
        lines.append(LineString([p1, p2]))

    return lines


def split_fill_object_by_junction(obj: dict, center: list, branch_points: list) -> list:
    if Polygon is None or LineString is None or Point is None or shapely_split is None:
        raise RuntimeError("Junction split requires shapely to be installed.")

    geom = object_fill_geometry(obj)
    if geom is None or geom.is_empty:
        raise RuntimeError("Could not convert selected fill object into split geometry.")

    if not branch_points or len(branch_points) < 3:
        raise RuntimeError("Junction cut needs a centre point and at least three branch points.")

    if len(branch_points) > 8:
        raise RuntimeError("Junction cut currently supports up to 8 branches.")

    try:
        cpt = Point(float(center[0]), float(center[1]))
    except Exception:
        raise RuntimeError("Invalid junction centre.")

    # Centre should usually be inside or very close to the selected object.
    if not geom.buffer(2.0).covers(cpt):
        raise RuntimeError("Place the junction centre inside or very close to the selected shape.")

    cut_lines = _junction_cut_lines_from_points(center, branch_points)
    if len(cut_lines) < 3:
        raise RuntimeError("Could not create enough branch cuts. Click further along each branch.")

    # Keep only lines that meaningfully cross the selected geometry.
    usable = []
    for line in cut_lines:
        try:
            inter = geom.intersection(line)
            segs = _line_geom_intersections_as_segments(inter)
            max_len = max([s.length for s in segs], default=0.0)
        except Exception:
            max_len = 0.0
        if max_len >= 1.0:
            usable.append(line)

    if len(usable) < 3:
        raise RuntimeError("The branch cuts did not cross enough of the selected shape. Try clicking farther out along each branch.")

    original_cut_segments = _manual_cut_segments_from_original_geom(geom, usable)
    parts = _split_fill_geometry_with_lines(geom, usable)

    if len(parts) <= 1:
        raise RuntimeError("Junction cut did not separate the selected shape. Try branch points farther from the centre.")

    out = []
    for i, part in enumerate(parts):
        d = geometry_to_svg_d(part)
        if not d:
            continue

        new_obj = dict(obj)
        new_obj["id"] = f'{obj["id"]}_jcut{i+1}'
        new_obj["d"] = d
        new_obj["tx"] = 0.0
        new_obj["ty"] = 0.0
        new_obj["label"] = f'{obj["label"]}j{chr(ord("a")+i)}'
        new_obj["part_index"] = i
        new_obj["part_count"] = len(parts)
        new_obj["prep_note"] = "junction split from selected object"
        new_obj["order"] = float(obj.get("order", 0.0)) + i / 1000.0

        cut_rungs = _cut_line_guide_rungs_for_part(part, original_cut_segments)
        existing_cut_rungs = obj.get("cut_guide_rungs") or []
        new_obj["cut_guide_rungs"] = list(existing_cut_rungs) + cut_rungs

        # Tiny hub fragments can occur at the centre.  Keep them visible in the
        # structure pane rather than auto-deleting them; the user can assign
        # Skip later if desired.
        try:
            new_obj["junction_area"] = float(part.area)
        except Exception:
            pass

        out.append(new_obj)

    if len(out) <= 1:
        raise RuntimeError("No usable junction split pieces were created.")

    return out


def split_fill_object_by_line(obj: dict, cut_points: list) -> list:
    if Polygon is None or LineString is None or Point is None or shapely_split is None:
        raise RuntimeError("Manual split requires shapely to be installed.")
    geom = object_fill_geometry(obj)
    if geom is None or geom.is_empty:
        raise RuntimeError("Could not convert selected fill object into split geometry.")

    if len(cut_points) not in (2, 4):
        raise RuntimeError("Manual split needs one cut (2 points) or two cuts (4 points).")

    cut_lines = []
    pair_count = len(cut_points) // 2
    for i in range(pair_count):
        p1 = tuple(cut_points[i * 2])
        p2 = tuple(cut_points[i * 2 + 1])

        if Point(p1).within(geom) or Point(p2).within(geom):
            raise RuntimeError("For fill shapes, place both cut points outside the target shape, one on each side.")

        local_line = _build_local_cut_line(p1, p2)
        boundary_hits = _intersection_count(geom.boundary.intersection(local_line))
        if boundary_hits < 2 or boundary_hits == 999:
            raise RuntimeError("The cut line must pass cleanly across the selected shape from one outside side to the other.")

        cut_lines.append(local_line)

    original_cut_segments = _manual_cut_segments_from_original_geom(geom, cut_lines)
    parts = _split_fill_geometry_with_lines(geom, cut_lines)

    if len(parts) <= 1 and len(cut_lines) == 1:
        raise NeedSecondCutError(
            "This selected shape likely needs a second cut to isolate a section. Place two more points for the second cut."
        )
    if len(parts) <= 1:
        raise RuntimeError("The two cuts did not separate the selected shape into independent parts.")

    out = []
    for i, part in enumerate(parts):
        d = geometry_to_svg_d(part)
        if not d:
            continue
        new_obj = dict(obj)
        new_obj["id"] = f'{obj["id"]}_cut{i+1}'
        new_obj["d"] = d
        new_obj["tx"] = 0.0
        new_obj["ty"] = 0.0
        new_obj["label"] = f'{obj["label"]}{chr(ord("a")+i)}'
        new_obj["part_index"] = i
        new_obj["part_count"] = len(parts)
        new_obj["prep_note"] = "manually split from selected object"
        new_obj["order"] = float(obj.get("order", 0.0)) + i / 1000.0
        cut_rungs = _cut_line_guide_rungs_for_part(part, original_cut_segments)
        existing_cut_rungs = obj.get("cut_guide_rungs") or []
        new_obj["cut_guide_rungs"] = list(existing_cut_rungs) + cut_rungs
        out.append(new_obj)
    if len(out) <= 1:
        raise RuntimeError("No usable split pieces were created.")
    return out


def split_stroke_object_by_line(obj: dict, cut_points: list) -> list:
    if parse_path is None:
        raise RuntimeError("Manual stroke split requires svgpathtools.")
    try:
        parsed = parse_path(obj["d"])
        subpaths = parsed.continuous_subpaths()
    except Exception:
        raise RuntimeError("Could not parse selected stroke path.")

    pts = []
    for sp in subpaths:
        pts.extend(_sample_subpath_points(sp, obj.get("tx", 0.0), obj.get("ty", 0.0), target_step=3.0))
    if len(pts) < 4:
        raise RuntimeError("Selected stroke is too short to split.")

    (ax, ay), (bx, by) = cut_points[0], cut_points[1]
    best_i = None
    best_d = 1e9
    for i, (x, y) in enumerate(pts[1:-1], start=1):
        d, _ = distance_point_to_segment(x, y, ax, ay, bx, by)
        if d < best_d:
            best_d = d
            best_i = i

    if best_i is None or best_i < 2 or best_i > len(pts) - 3:
        raise RuntimeError("Could not find a usable split point on the stroke.")

    left = pts[: best_i + 1]
    right = pts[best_i:]
    if _polyline_length(left) < 6 or _polyline_length(right) < 6:
        raise RuntimeError("Manual split would create a very short stroke segment.")

    out = []
    for i, seg in enumerate([left, right]):
        d = _points_to_svg_path(seg)
        new_obj = dict(obj)
        new_obj["id"] = f'{obj["id"]}_cut{i+1}'
        new_obj["d"] = d
        new_obj["tx"] = 0.0
        new_obj["ty"] = 0.0
        new_obj["label"] = f'{obj["label"]}{chr(ord("a")+i)}'
        new_obj["part_index"] = i
        new_obj["part_count"] = 2
        new_obj["prep_note"] = "manually split stroke object"
        new_obj["order"] = float(obj.get("order", 0.0)) + i / 1000.0
        out.append(new_obj)
    return out


def manual_split_object(obj: dict, cut_points: list) -> list:
    if (obj.get("render_mode") or "fill") == "stroke":
        return split_stroke_object_by_line(obj, cut_points)
    return split_fill_object_by_line(obj, cut_points)


# ─────────────────────────────────────────────────────────────────────────────
# Geometry iteration / preview
# ─────────────────────────────────────────────────────────────────────────────


def _geometry_polygons(geom):
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return [g for g in geom.geoms if not g.is_empty]
    return []


def _sample_linestring(line, step_px: float) -> list:
    if line is None or line.is_empty:
        return []
    try:
        length = float(line.length)
    except Exception:
        return []
    if length <= 0:
        return []
    n = max(2, int(length / max(1.0, step_px)) + 1)
    pts = []
    for i in range(n + 1):
        pt = line.interpolate(i / n, normalized=True)
        pts.append((float(pt.x), float(pt.y)))
    return pts


def generate_edge_walk_preview(geom, inset_px: float, stitch_len_px: float) -> list:
    """
    Edge-walk underlay preview: running stitch just inside the region boundary.
    Returns polylines in SVG coordinates.
    """
    out = []
    for poly in _geometry_polygons(geom):
        try:
            inset = poly.buffer(-inset_px)
        except Exception:
            inset = None
        if inset is None or inset.is_empty:
            inset = poly

        for piece in _geometry_polygons(inset):
            rings = [piece.exterior] + list(piece.interiors)
            for ring in rings:
                line = LineString(list(ring.coords))
                pts = _sample_linestring(line, stitch_len_px)
                if len(pts) >= 2:
                    out.append(pts)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Rasterisation helpers
# ─────────────────────────────────────────────────────────────────────────────


def _rasterize_geom_mask(geom, bounds, width_px: int, height_px: int) -> np.ndarray:
    """
    Rasterise a shapely geometry into a boolean mask using point-in-polygon tests.
    This is intentionally dependency-light and suited to small selected embroidery shapes.
    """
    minx, miny, maxx, maxy = bounds
    if width_px <= 1 or height_px <= 1:
        return np.zeros((1, 1), dtype=bool)

    mask = np.zeros((height_px, width_px), dtype=bool)
    for y in range(height_px):
        gy = miny + (y + 0.5)
        for x in range(width_px):
            gx = minx + (x + 0.5)
            try:
                if geom.contains(Point(gx, gy)) or geom.touches(Point(gx, gy)):
                    mask[y, x] = True
            except Exception:
                pass
    return mask


def _skeleton_world_segments_for_geom(geom, max_pixels: int = 260):
    """
    Build rough centreline segments from a filled satin object.
    This uses the existing Zhang-Suen thinning code, but only after the user has
    split paths into simpler objects. It is used to place perpendicular satin bars.
    """
    if geom is None or geom.is_empty:
        return []

    minx, miny, maxx, maxy = geom.bounds
    w = max(2, int(math.ceil(maxx - minx)) + 4)
    h = max(2, int(math.ceil(maxy - miny)) + 4)

    # Keep preview responsive on very large shapes by scaling down.
    scale = 1.0
    if max(w, h) > max_pixels:
        scale = max_pixels / max(w, h)
        w = max(2, int(w * scale))
        h = max(2, int(h * scale))

    # Build scaled geometry by mapping world coordinates into mask coordinates.
    def to_mask(x, y, z=None):
        return ((x - minx + 2) * scale, (y - miny + 2) * scale)

    def to_world_xy(mx, my):
        return (mx / scale + minx - 2, my / scale + miny - 2)

    try:
        mask_geom = transform(lambda x, y, z=None: to_mask(x, y), geom)
    except Exception:
        return []

    mask = _rasterize_geom_mask(mask_geom, mask_geom.bounds, w, h)
    if mask.sum() < 4:
        return []

    skel = zhang_suen_thin(mask)
    raw_segments = _skeleton_segments(skel)
    world_segments = []

    for seg in raw_segments:
        pts = []
        for py, px in seg:
            wx, wy = to_world_xy(px + 0.5, py + 0.5)
            pts.append((wx, wy))
        if len(pts) >= 2 and _polyline_length(pts) >= 4:
            world_segments.append(_simplify_points(pts, step=2))

    return world_segments


# ─────────────────────────────────────────────────────────────────────────────
# Intersection + crossbar helpers
# ─────────────────────────────────────────────────────────────────────────────


def _line_geom_intersections_as_segments(intersection):
    """
    Convert shapely line intersection result into candidate line segments.
    """
    if intersection.is_empty:
        return []
    if intersection.geom_type == "LineString":
        return [intersection]
    if intersection.geom_type == "MultiLineString":
        return list(intersection.geoms)
    if intersection.geom_type == "GeometryCollection":
        return [g for g in intersection.geoms if g.geom_type == "LineString"]
    return []


def _normal_crossbar_inside_geom(geom, point, tangent, half_len: float):
    tx, ty = tangent
    tlen = math.hypot(tx, ty)
    if tlen < 1e-6:
        return None
    tx, ty = tx / tlen, ty / tlen
    nx, ny = -ty, tx

    x, y = point
    probe = LineString([
        (x - nx * half_len, y - ny * half_len),
        (x + nx * half_len, y + ny * half_len),
    ])

    try:
        inter = geom.intersection(probe)
    except Exception:
        return None

    segs = _line_geom_intersections_as_segments(inter)
    if not segs:
        return None

    p = Point(x, y)
    segs.sort(key=lambda s: s.distance(p))
    seg = segs[0]
    coords = list(seg.coords)
    if len(coords) < 2:
        return None

    # Avoid wild over-wide jumps caused by bad centreline/branch locations.
    if seg.length <= 0.5 or seg.length > half_len * 1.95:
        return None

    return [(float(coords[0][0]), float(coords[0][1])), (float(coords[-1][0]), float(coords[-1][1]))]


# ─────────────────────────────────────────────────────────────────────────────
# Line sampling helpers
# ─────────────────────────────────────────────────────────────────────────────


def _sample_line_by_length_preview(line: LineString, n: int, reverse: bool = False,
                                   include_endpoint: bool = False):
    """
    Sample arclength-even points on a LineString.

    Closed rings should normally exclude the duplicated endpoint.
    Open satin rails should include both endpoints so the satin reaches the
    ends of eyebrows, mouth pieces, and manually split line sections.
    """
    if n <= 0 or line is None or line.length <= 1e-9:
        return []

    if include_endpoint and n < 2:
        n = 2

    pts = []
    denom = max(n - 1, 1) if include_endpoint else max(n, 1)

    for i in range(n):
        t = i / denom
        if not include_endpoint and t >= 1.0:
            t = 0.999999
        if reverse:
            t = 1.0 - t
            if not include_endpoint and t >= 1.0:
                t = 0.999999
        t = max(0.0, min(1.0, t))
        pt = line.interpolate(t * line.length)
        pts.append((float(pt.x), float(pt.y)))

    return pts


def _sample_line_by_spacing_preview(line: LineString, spacing_px: float,
                                    include_endpoint: bool = False):
    if line is None or line.length <= 1e-9:
        return []
    n = max(3, int(math.ceil(line.length / max(spacing_px, 0.45))))
    if include_endpoint:
        n += 1
    return _sample_line_by_length_preview(
        line, n, reverse=False, include_endpoint=include_endpoint
    )


def _nearest_point_on_line_preview(line: LineString, point_xy):
    """
    Return nearest point on a LineString to point_xy.
    """
    if line is None or line.length <= 1e-9:
        return None
    try:
        p = Point(point_xy[0], point_xy[1])
        d = line.project(p)
        q = line.interpolate(d)
        return (float(q.x), float(q.y))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Internal helper: point-to-segment distance (used by stroke splitting)
# ─────────────────────────────────────────────────────────────────────────────


def distance_point_to_segment(px, py, ax, ay, bx, by):
    """
    Minimum Euclidean distance from point (px, py) to segment (ax, ay)-(bx, by).
    Returns (distance, None) for compatibility with the monolith's call site.
    """
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        # Degenerate segment — treat as point.
        return (math.hypot(px - ax, py - ay), None)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    if t < 0.0:
        near_x, near_y = ax, ay
    elif t > 1.0:
        near_x, near_y = bx, by
    else:
        near_x = ax + t * dx
        near_y = ay + t * dy
    return (math.hypot(px - near_x, py - near_y), None)


# Late-bound imports from sibling modules to avoid circular imports at load time.
# These are imported at module level but resolved on first access via lazy
# imports below.  The monolith originally defined these at the top level
# wrapped in try/except blocks; we mirror that pattern here.

try:
    from svgpathtools import parse_path
except Exception:
    parse_path = None

from .trace import zhang_suen_thin, _skeleton_segments
