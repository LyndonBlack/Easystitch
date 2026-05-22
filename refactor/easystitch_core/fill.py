#!/usr/bin/env python3
"""
EasyStitch Core — Fill stitch generation and ordering.

Extracted from the monolith.  Contains scanline fill generation, fill-row
grouping/laning/ordering, satin zigzag ordering, and design-colour helpers.
"""

import math
from typing import Any

import numpy as np
from shapely.geometry import LineString

from .utils import _polyline_length, _neighbors8, color_luminance, mm_to_px, _rotate_xy, _rotate_geom


# ─────────────────────────────────────────────────────────────────────────────
# Fill preview generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_fill_preview_lines(geom, row_spacing_px: float, stitch_len_px: float, angle_deg: float,
                                min_segment_px: float = 1.0) -> list:
    """
    Scanline fill preview. Produces short polylines at the requested angle.
    Used for coarse underlay fill and top fill preview.
    """
    if geom is None or geom.is_empty:
        return []

    angle_rad = math.radians(float(angle_deg))
    try:
        rot_geom = _rotate_geom(geom, -angle_rad)
    except Exception:
        return []

    minx, miny, maxx, maxy = rot_geom.bounds
    lines = []
    y = miny + row_spacing_px / 2.0
    direction = 1

    while y <= maxy:
        scan = LineString([(minx - 2, y), (maxx + 2, y)])
        try:
            inter = rot_geom.intersection(scan)
        except Exception:
            y += row_spacing_px
            direction *= -1
            continue

        segments = []
        if inter.is_empty:
            pass
        elif inter.geom_type == "LineString":
            segments = [inter]
        elif inter.geom_type == "MultiLineString":
            segments = list(inter.geoms)
        elif inter.geom_type == "GeometryCollection":
            segments = [g for g in inter.geoms if g.geom_type == "LineString"]

        for seg in segments:
            coords = list(seg.coords)
            if len(coords) < 2:
                continue
            x1, _ = coords[0]
            x2, _ = coords[-1]
            length = abs(x2 - x1)
            # Automatic small-gap fill: include short corner/edge segments down
            # to a fixed local minimum instead of dropping anything below the
            # global running stitch length.
            if length < max(0.5, float(min_segment_px)):
                continue

            n = max(2, int(math.ceil(length / max(1.0, stitch_len_px))) + 1)
            xs = np.linspace(min(x1, x2), max(x1, x2), n)
            if direction < 0:
                xs = xs[::-1]
            rot_pts = [(float(x), y) for x in xs]
            pts = [_rotate_xy(x, yy, angle_rad) for x, yy in rot_pts]
            if len(pts) >= 2:
                lines.append(pts)

        y += row_spacing_px
        direction *= -1

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Line ordering helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ordered_lines_nearest_even_without_start(lines: list, start_pos: tuple | None = None) -> list:
    remaining = [list(line) for line in lines if line and len(line) >= 2]
    if not remaining:
        return []

    def line_len(line):
        return sum(math.hypot(line[i+1][0] - line[i][0], line[i+1][1] - line[i][1]) for i in range(len(line)-1))

    ordered = []
    if start_pos is None:
        best_idx = max(range(len(remaining)), key=lambda i: line_len(remaining[i]))
        line = remaining.pop(best_idx)
        ordered.append(line)
        cur = line[-1]
    else:
        cur = start_pos
    while remaining:
        best_idx = 0
        best_rev = False
        best_d = float("inf")
        for i, line in enumerate(remaining):
            d0 = math.hypot(line[0][0] - cur[0], line[0][1] - cur[1])
            d1 = math.hypot(line[-1][0] - cur[0], line[-1][1] - cur[1])
            if d0 < best_d:
                best_idx, best_rev, best_d = i, False, d0
            if d1 < best_d:
                best_idx, best_rev, best_d = i, True, d1
        line = remaining.pop(best_idx)
        if best_rev:
            line = list(reversed(line))
        ordered.append(line)
        cur = line[-1]
    return ordered


def _nearest_line_order(lines: list, start_pos: tuple | None) -> list:
    """
    Greedy order to reduce jumps.  Lines may be reversed.
    """
    remaining = [list(line) for line in lines if line and len(line) >= 2]
    if not remaining:
        return []

    if start_pos is None:
        return remaining

    ordered = []
    cur = start_pos
    while remaining:
        best_idx = 0
        best_rev = False
        best_d = float("inf")
        for i, line in enumerate(remaining):
            d0 = math.hypot(line[0][0] - cur[0], line[0][1] - cur[1])
            d1 = math.hypot(line[-1][0] - cur[0], line[-1][1] - cur[1])
            if d0 < best_d:
                best_idx, best_rev, best_d = i, False, d0
            if d1 < best_d:
                best_idx, best_rev, best_d = i, True, d1
        line = remaining.pop(best_idx)
        if best_rev:
            line = list(reversed(line))
        ordered.append(line)
        cur = line[-1]
    return ordered


# ─────────────────────────────────────────────────────────────────────────────
# Fill row ordering — serpentine
# ─────────────────────────────────────────────────────────────────────────────

def _order_fill_rows_serpentine(lines: list, start_pos: tuple | None = None,
                                angle_deg: float = 0.0,
                                row_tolerance_px: float = 1.25) -> list:
    rows = [list(line) for line in lines if line and len(line) >= 2]
    if not rows:
        return []

    theta = math.radians(float(angle_deg))
    ux, uy = math.cos(theta), math.sin(theta)
    nx, ny = -math.sin(theta), math.cos(theta)

    def midpoint(line):
        return ((line[0][0] + line[-1][0]) / 2.0, (line[0][1] + line[-1][1]) / 2.0)

    def nproj(line):
        m = midpoint(line)
        return m[0] * nx + m[1] * ny

    def uproj(p):
        return p[0] * ux + p[1] * uy

    rows.sort(key=lambda line: (nproj(line), uproj(midpoint(line))))

    groups = []
    for line in rows:
        p = nproj(line)
        if not groups or abs(p - groups[-1]["p"]) > row_tolerance_px:
            groups.append({"p": p, "lines": [line]})
        else:
            groups[-1]["lines"].append(line)

    ordered = []
    cur = start_pos
    for gi, group in enumerate(groups):
        remaining = [list(l) for l in group["lines"]]
        remaining.sort(key=lambda l: uproj(midpoint(l)), reverse=(gi % 2 == 1))
        while remaining:
            if cur is None:
                line = remaining.pop(0)
                if gi % 2 == 1:
                    line = list(reversed(line))
                ordered.append(line)
                cur = line[-1]
                continue

            best_idx = 0
            best_rev = False
            best_d = float("inf")
            for i, line in enumerate(remaining):
                d0 = math.hypot(line[0][0] - cur[0], line[0][1] - cur[1])
                d1 = math.hypot(line[-1][0] - cur[0], line[-1][1] - cur[1])
                if d0 < best_d:
                    best_idx, best_rev, best_d = i, False, d0
                if d1 < best_d:
                    best_idx, best_rev, best_d = i, True, d1
            line = remaining.pop(best_idx)
            if best_rev:
                line = list(reversed(line))
            ordered.append(line)
            cur = line[-1]

    return ordered


# ─────────────────────────────────────────────────────────────────────────────
# Fill row grouping & lane matching (island-aware)
# ─────────────────────────────────────────────────────────────────────────────

def _fill_row_groups(lines: list, angle_deg: float = 0.0,
                     row_tolerance_px: float = 1.25):
    rows = [list(line) for line in lines if line and len(line) >= 2]
    if not rows:
        return [], None

    theta = math.radians(float(angle_deg))
    ux, uy = math.cos(theta), math.sin(theta)
    nx, ny = -math.sin(theta), math.cos(theta)

    def midpoint(line):
        return ((line[0][0] + line[-1][0]) / 2.0, (line[0][1] + line[-1][1]) / 2.0)

    def nproj(line):
        m = midpoint(line)
        return m[0] * nx + m[1] * ny

    def uproj(pt):
        return pt[0] * ux + pt[1] * uy

    info = []
    for line in rows:
        u0 = uproj(line[0])
        u1 = uproj(line[-1])
        info.append({
            "line": line,
            "row_p": nproj(line),
            "u0": min(u0, u1),
            "u1": max(u0, u1),
            "uc": (u0 + u1) / 2.0,
        })

    info.sort(key=lambda d: (d["row_p"], d["uc"]))
    groups = []
    for item in info:
        if not groups or abs(item["row_p"] - groups[-1]["row_p"]) > row_tolerance_px:
            groups.append({"row_p": item["row_p"], "segments": [item]})
        else:
            groups[-1]["segments"].append(item)

    for g in groups:
        g["segments"].sort(key=lambda d: d["uc"])

    axes = {"ux": ux, "uy": uy, "nx": nx, "ny": ny}
    return groups, axes


def _match_fill_segments_into_lanes(lines: list, angle_deg: float = 0.0,
                                    row_tolerance_px: float = 1.25,
                                    overlap_slack_px: float = 4.0,
                                    centre_slack_px: float = 18.0) -> list:
    """
    Build 'lanes' of fill row segments around islands/holes.

    Instead of consuming every segment within a row (which causes a jump across
    an eye/mouth island), we connect segments that stay on the same side across
    neighbouring rows.  Each lane is then stitched serpentine on its own.
    """
    groups, _axes = _fill_row_groups(lines, angle_deg, row_tolerance_px)
    if not groups:
        return []

    lanes = []
    lane_id_counter = 0

    for gi, group in enumerate(groups):
        used_lanes = set()
        for seg in group["segments"]:
            best_lane_idx = None
            best_score = None

            for li, lane in enumerate(lanes):
                if li in used_lanes:
                    continue
                # only match to recent neighbouring rows
                row_gap = gi - lane["last_group_idx"]
                if row_gap < 1 or row_gap > 2:
                    continue

                prev = lane["last_seg"]
                overlap = min(prev["u1"], seg["u1"]) - max(prev["u0"], seg["u0"])
                centre_dist = abs(prev["uc"] - seg["uc"])

                # Prefer overlapping intervals strongly.  Otherwise allow a small
                # centre drift for tapering regions.
                if overlap >= -overlap_slack_px or centre_dist <= centre_slack_px:
                    score = (
                        0 if overlap >= -overlap_slack_px else 1,
                        abs(row_gap - 1),
                        -overlap,
                        centre_dist,
                    )
                    if best_score is None or score < best_score:
                        best_score = score
                        best_lane_idx = li

            if best_lane_idx is None:
                lanes.append({
                    "lane_id": lane_id_counter,
                    "segments": [seg],
                    "last_seg": seg,
                    "last_group_idx": gi,
                })
                lane_id_counter += 1
                used_lanes.add(len(lanes) - 1)
            else:
                lanes[best_lane_idx]["segments"].append(seg)
                lanes[best_lane_idx]["last_seg"] = seg
                lanes[best_lane_idx]["last_group_idx"] = gi
                used_lanes.add(best_lane_idx)

    # Sort each lane by row order and assign a stable lateral sort key.
    for lane in lanes:
        lane["segments"].sort(key=lambda s: (s["row_p"], s["uc"]))
        lane["lane_u"] = sum(s["uc"] for s in lane["segments"]) / max(1, len(lane["segments"]))

    lanes.sort(key=lambda lane: lane["lane_u"])
    return lanes


def _orient_fill_lane_segments(lane_segments: list, start_pos: tuple | None = None,
                               reverse_rows: bool = False) -> list:
    segs = [list(seg["line"]) for seg in lane_segments]
    if reverse_rows:
        segs = list(reversed(segs))
    if not segs:
        return []

    ordered = []
    cur = start_pos

    first = segs[0]
    if cur is not None:
        d0 = math.hypot(first[0][0] - cur[0], first[0][1] - cur[1])
        d1 = math.hypot(first[-1][0] - cur[0], first[-1][1] - cur[1])
        if d1 < d0:
            first = list(reversed(first))
    ordered.append(first)
    cur = first[-1]

    for seg in segs[1:]:
        d0 = math.hypot(seg[0][0] - cur[0], seg[0][1] - cur[1])
        d1 = math.hypot(seg[-1][0] - cur[0], seg[-1][1] - cur[1])
        if d1 < d0:
            seg = list(reversed(seg))
        ordered.append(seg)
        cur = seg[-1]

    return ordered


def _order_fill_rows_lane_serpentine(lines: list, start_pos: tuple | None = None,
                                     angle_deg: float = 0.0,
                                     row_tolerance_px: float = 1.25) -> list:
    """
    Top-fill routing that minimises jumps over internal islands.

    Strategy:
      1. group row segments into lanes that stay on the same side of blockers
      2. stitch each lane serpentine through successive rows
      3. choose the next lane greedily from the current needle position
      4. only fall back to a jump/trim when moving between truly separate lanes
    """
    lanes = _match_fill_segments_into_lanes(lines, angle_deg, row_tolerance_px)
    if not lanes:
        return []

    remaining = lanes[:]
    ordered = []
    cur = start_pos

    while remaining:
        best_idx = 0
        best_reverse = False
        best_dist = float("inf")

        for i, lane in enumerate(remaining):
            fwd = _orient_fill_lane_segments(lane["segments"], cur, reverse_rows=False)
            rev = _orient_fill_lane_segments(lane["segments"], cur, reverse_rows=True)

            if cur is None:
                d_fwd = 0.0
                d_rev = 0.0
            else:
                d_fwd = math.hypot(fwd[0][0][0] - cur[0], fwd[0][0][1] - cur[1]) if fwd else float("inf")
                d_rev = math.hypot(rev[0][0][0] - cur[0], rev[0][0][1] - cur[1]) if rev else float("inf")

            if d_fwd < best_dist:
                best_idx, best_reverse, best_dist = i, False, d_fwd
            if d_rev < best_dist:
                best_idx, best_reverse, best_dist = i, True, d_rev

        lane = remaining.pop(best_idx)
        lane_lines = _orient_fill_lane_segments(lane["segments"], cur, reverse_rows=best_reverse)
        ordered.extend(lane_lines)
        if lane_lines:
            cur = lane_lines[-1][-1]

    return ordered


# ─────────────────────────────────────────────────────────────────────────────
# Satin ordering helpers
# ─────────────────────────────────────────────────────────────────────────────

def _satin_entry_candidates(lines: list) -> list:
    bars = [list(line) for line in lines if line and len(line) >= 2]
    if not bars:
        return []
    return [bars[0][0], bars[0][-1], bars[-1][0], bars[-1][-1]]


def _order_underlay_to_finish_near(lines: list,
                                   start_pos: tuple | None,
                                   target_points: list) -> list:
    """
    Order/orient satin underlay lines so their final point is close to the
    chosen top-satin entry area.  This prevents the underlay from finishing at
    one end of the column and forcing a long jump to begin the visible satin.
    """
    base = _nearest_line_order(lines, start_pos)
    if not base or not target_points:
        return base

    def dist_to_targets(pt):
        return min(math.hypot(pt[0] - t[0], pt[1] - t[1]) for t in target_points)

    candidates = [base]

    # Same order, reverse final line if it gives a better exit.
    if len(base[-1]) >= 2:
        c = [list(line) for line in base]
        c[-1] = list(reversed(c[-1]))
        candidates.append(c)

    # Reverse the underlay sequence as another cheap alternative.
    rev_lines = [list(reversed(line)) for line in reversed(base)]
    if start_pos is not None and rev_lines:
        first = rev_lines[0]
        d0 = math.hypot(first[0][0] - start_pos[0], first[0][1] - start_pos[1])
        d1 = math.hypot(first[-1][0] - start_pos[0], first[-1][1] - start_pos[1])
        if d1 < d0:
            rev_lines[0] = list(reversed(first))
    candidates.append(rev_lines)

    def score(seq):
        if not seq:
            return float("inf")
        end = seq[-1][-1]
        finish = dist_to_targets(end)
        start_cost = 0.0
        if start_pos is not None:
            start_cost = math.hypot(seq[0][0][0] - start_pos[0], seq[0][0][1] - start_pos[1])
        return finish * 3.0 + start_cost * 0.25

    return min(candidates, key=score)


def _order_satin_bars_zigzag(lines: list, start_pos: tuple | None = None) -> list:
    """
    Order satin rungs as a true ladder/zigzag path, choosing the best end of
    the whole column from the current needle position.

    This is important after satin underlay: the underlay may finish at either
    end of the column.  The visible satin should begin at the nearest practical
    rung endpoint, not jump back to the generator's original first rung.
    """
    bars = [list(line) for line in lines if line and len(line) >= 2]
    if not bars:
        return []

    def point_dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def orient_sequence(seq, entry_side=None):
        seq = [list(bar) for bar in seq]
        if not seq:
            return []

        # entry_side:
        #   0 means start at seq[0][0]
        #   1 means start at seq[0][-1]
        #   None means choose nearest to start_pos
        if entry_side == 1:
            seq[0] = list(reversed(seq[0]))
        elif entry_side is None and start_pos is not None:
            d0 = point_dist(seq[0][0], start_pos)
            d1 = point_dist(seq[0][-1], start_pos)
            if d1 < d0:
                seq[0] = list(reversed(seq[0]))

        ordered = [seq[0]]
        last_end = seq[0][-1]

        for bar in seq[1:]:
            d0 = point_dist(bar[0], last_end)
            d1 = point_dist(bar[-1], last_end)
            if d1 < d0:
                bar = list(reversed(bar))
            ordered.append(bar)
            last_end = bar[-1]

        return ordered

    candidates = []

    # Normal generated direction, both possible first-side entries.
    candidates.append(orient_sequence(bars, entry_side=0))
    candidates.append(orient_sequence(bars, entry_side=1))

    # Reversed generated direction, both possible first-side entries.
    rbars = list(reversed([list(bar) for bar in bars]))
    candidates.append(orient_sequence(rbars, entry_side=0))
    candidates.append(orient_sequence(rbars, entry_side=1))

    # Choose the candidate whose first needle drop is nearest the current point.
    # Tie-breaker: shorter final path travel between consecutive bars.
    def candidate_score(seq):
        if not seq:
            return float("inf")
        entry = seq[0][0]
        entry_cost = point_dist(entry, start_pos) if start_pos is not None else 0.0
        travel_cost = 0.0
        last = seq[0][-1]
        for bar in seq[1:]:
            travel_cost += point_dist(last, bar[0])
            last = bar[-1]
        return entry_cost * 10.0 + travel_cost * 0.05

    return min(candidates, key=candidate_score)


def _satin_bars_to_continuous_zigzag(ordered_bars: list) -> list:
    """
    Convert already-ordered satin rungs into one continuous zigzag path.

    Each bar is a rail-to-rail crossing.  We preserve the first bar's
    orientation (as chosen by _order_satin_bars_zigzag) then for each
    subsequent bar we cross to the OPPOSITE rail from wherever we are.
    This produces a true left/right alternation instead of stepping
    along one rail.
    """
    bars = [list(bar) for bar in (ordered_bars or []) if bar and len(bar) >= 2]
    if not bars:
        return []
    path = [bars[0][0], bars[0][-1]]
    last = bars[0][-1]
    for bar in bars[1:]:
        a, b = bar[0], bar[-1]
        da = math.hypot(a[0] - last[0], a[1] - last[1])
        db = math.hypot(b[0] - last[0], b[1] - last[1])
        nxt = b if da <= db else a
        if math.hypot(nxt[0] - path[-1][0], nxt[1] - path[-1][1]) > 1e-6:
            path.append(nxt)
            last = nxt
    return path if len(path) >= 2 else []


# ─────────────────────────────────────────────────────────────────────────────
# Fill angle detection
# ─────────────────────────────────────────────────────────────────────────────

def fill_angle_for_geometry(geom, default_angle: float, auto_enabled: bool = True,
                            threshold: float = 2.0) -> tuple[float, dict]:
    info = {
        "auto_used": False,
        "ratio": 1.0,
        "long_axis_angle": None,
        "chosen_angle": float(default_angle),
    }
    if not auto_enabled or geom is None or geom.is_empty:
        return float(default_angle), info
    try:
        rect = list(geom.minimum_rotated_rectangle.exterior.coords)[:-1]
        if len(rect) != 4:
            return float(default_angle), info
        edges = []
        for i in range(4):
            x1, y1 = rect[i]
            x2, y2 = rect[(i + 1) % 4]
            length = math.hypot(x2 - x1, y2 - y1)
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180.0
            edges.append((length, angle))
        edges_sorted = sorted(edges, key=lambda e: e[0], reverse=True)
        long_len, long_angle = edges_sorted[0]
        short_len = max(edges_sorted[-1][0], 1e-6)
        ratio = long_len / short_len
        info["ratio"] = float(ratio)
        info["long_axis_angle"] = float(long_angle)
        if ratio >= float(threshold):
            chosen = (long_angle + 90.0) % 180.0
            info["auto_used"] = True
            info["chosen_angle"] = float(chosen)
            return float(chosen), info
    except Exception:
        pass
    return float(default_angle), info


# ─────────────────────────────────────────────────────────────────────────────
# Design colour / pass helpers
# ─────────────────────────────────────────────────────────────────────────────

def sorted_design_colors(objects: list) -> list:
    colors = []
    seen = set()
    for obj in sorted(objects, key=lambda o: float(o.get("order", 0))):
        c = obj.get("color", "#000000")
        if c not in seen:
            seen.add(c)
            colors.append(c)
    return colors


def objects_for_pass(objects: list, assignments: dict, color: str, stitch_type: str) -> list:
    out = []
    for obj in objects:
        obj_id = str(obj.get("id"))
        if obj.get("color", "#000000") != color:
            continue
        if assignments.get(obj_id, "fill") != stitch_type:
            continue
        out.append(obj)
    return sorted(out, key=lambda o: float(o.get("order", 0)))
