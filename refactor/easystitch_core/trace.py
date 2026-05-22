#!/usr/bin/env python3
"""
EasyStitch Core — Tracing pipeline and SVG structure parsing.

Extracted from the monolith's Stage 2 (vtracer CLI invocation) and
Stage 3 (path structure parsing / stroke candidate extraction).
"""

import os
import re
import math
import subprocess
import time
import shutil
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

from .utils import safe_stem, _neighbors8, _polyline_length, _simplify_points, _points_to_svg_path

try:
    from svgpathtools import parse_path
except Exception:
    parse_path = None

try:
    from shapely.geometry import Polygon, MultiPolygon, LineString, Point
    from shapely.ops import split as shapely_split, polygonize, unary_union
except Exception:
    Polygon = MultiPolygon = LineString = Point = None
    shapely_split = None


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: vtracer invocation
# ─────────────────────────────────────────────────────────────────────────────

def find_vtracer_cli() -> str:
    """
    Find the standalone vtracer command-line binary.

    Priority:
      1. EASYSTITCH_VTRACER_BIN environment variable
      2. vtracer on PATH
      3. ~/.cargo/bin/vtracer from cargo install vtracer --locked
    """
    candidates = []

    env_bin = os.environ.get("EASYSTITCH_VTRACER_BIN")
    if env_bin:
        candidates.append(env_bin)

    path_bin = shutil.which("vtracer")
    if path_bin:
        candidates.append(path_bin)

    candidates.append(os.path.expanduser("~/.cargo/bin/vtracer"))

    for candidate in candidates:
        if not candidate:
            continue
        expanded = os.path.expanduser(candidate)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    raise FileNotFoundError(
        "Could not find standalone vtracer CLI. Install it with: "
        "cargo install vtracer --locked, or set EASYSTITCH_VTRACER_BIN."
    )


def count_svg_paths(svg_path: str) -> int:
    try:
        return Path(svg_path).read_text(encoding="utf-8", errors="ignore").count("<path")
    except Exception:
        return 0


def trace_prepared_png(
    prepared_png: str,
    output_dir: str,
    stem: str,
    speckle: int = 8,
    mode: str = "spline",
    hierarchical: str = "cutout",
    color_precision: int = 6,
    gradient_step: int = 16,
    corner_threshold: int = 60,
    segment_length: float = 4.0,
    splice_threshold: int = 45,
    path_precision: int = 3,
) -> dict:
    """
    Trace the prepared PNG to SVG using the standalone vtracer CLI.
    """
    t0 = time.time()

    if not os.path.isfile(prepared_png):
        raise FileNotFoundError(f"Prepared PNG not found: {prepared_png}")

    vtracer_bin = find_vtracer_cli()
    out_path = Path(output_dir) / f"{safe_stem(stem)}_traced.svg"

    # Keep values in safe ranges.
    speckle = max(0, int(speckle))
    mode = mode if mode in ("spline", "polygon", "pixel") else "spline"
    hierarchical = hierarchical if hierarchical in ("cutout", "stacked") else "cutout"
    color_precision = max(1, min(8, int(color_precision)))
    gradient_step = max(0, min(128, int(gradient_step)))
    corner_threshold = max(0, min(180, int(corner_threshold)))
    segment_length = max(1.0, min(20.0, float(segment_length)))
    splice_threshold = max(0, min(180, int(splice_threshold)))
    path_precision = max(0, min(8, int(path_precision)))

    cmd = [
        vtracer_bin,
        "--input", prepared_png,
        "--output", str(out_path),
        "--colormode", "color",
        "--hierarchical", hierarchical,
        "--mode", mode,
        "--filter_speckle", str(speckle),
        "--color_precision", str(color_precision),
        "--gradient_step", str(gradient_step),
        "--corner_threshold", str(corner_threshold),
        "--segment_length", str(segment_length),
        "--splice_threshold", str(splice_threshold),
        "--path_precision", str(path_precision),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=180,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "vtracer failed with return code "
            f"{result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    if not out_path.exists() or out_path.stat().st_size < 50:
        raise RuntimeError("vtracer completed but produced no usable SVG output")

    svg_text = out_path.read_text(encoding="utf-8", errors="ignore")

    return {
        "ok": True,
        "vtracer": vtracer_bin,
        "output_path": str(out_path.resolve()),
        "svg_text": svg_text,
        "path_count": count_svg_paths(str(out_path)),
        "svg_kb": round(out_path.stat().st_size / 1024, 1),
        "time_sec": round(time.time() - t0, 3),
        "cmd": " ".join(cmd),
        "settings": {
            "speckle": speckle,
            "mode": mode,
            "hierarchical": hierarchical,
            "color_precision": color_precision,
            "gradient_step": gradient_step,
            "corner_threshold": corner_threshold,
            "segment_length": segment_length,
            "splice_threshold": splice_threshold,
            "path_precision": path_precision,
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3: path structure parsing
# ─────────────────────────────────────────────────────────────────────────────

def _extract_style_value(style_text: str, key: str, default: str = "") -> str:
    if not style_text:
        return default
    for part in style_text.split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        if k.strip().lower() == key.lower():
            return v.strip()
    return default


def _parse_translate(transform: str) -> tuple[float, float]:
    if not transform:
        return 0.0, 0.0
    m = re.search(r"translate\(\s*([-\d.]+)(?:[,\s]+([-\d.]+))?\s*\)", transform)
    if not m:
        return 0.0, 0.0
    tx = float(m.group(1))
    ty = float(m.group(2) or 0.0)
    return tx, ty


def _svg_float(value: str | None, default: float) -> float:
    if not value:
        return default
    m = re.match(r"([-\d.]+)", str(value).strip())
    return float(m.group(1)) if m else default


def _path_length_px(d: str) -> float:
    if parse_path is None:
        return 0.0
    try:
        return float(abs(parse_path(d).length(error=1e-3)))
    except Exception:
        return 0.0


def _path_bbox(d: str, tx: float, ty: float):
    if parse_path is None:
        return None
    try:
        bbox = parse_path(d).bbox()
        if not bbox:
            return None
        xmin, xmax, ymin, ymax = bbox
        return (xmin + tx, ymin + ty, xmax + tx, ymax + ty)
    except Exception:
        return None


def _path_elongation(d: str, tx: float, ty: float) -> float:
    bbox = _path_bbox(d, tx, ty)
    if not bbox:
        return 0.0
    w = max(1.0, bbox[2] - bbox[0])
    h = max(1.0, bbox[3] - bbox[1])
    area = w * h
    length = _path_length_px(d)
    if area <= 0:
        return 0.0
    return round(length / math.sqrt(area), 2)


def _make_structure_object(source_id: int, display_index: int, d: str, tx: float, ty: float,
                           color: str, part_index: int = 0, part_count: int = 1,
                           prep_note: str = "original source path",
                           render_mode: str = "fill",
                           stroke_width: float = 1.6,
                           source_kind: str = "fill_region") -> dict:
    suffix = "" if part_count <= 1 else chr(ord("a") + part_index)
    oid = f"s{source_id}" if part_count <= 1 else f"s{source_id}p{part_index+1}"
    label = f"Path {display_index}{suffix}"
    return {
        "id": oid,
        "source_id": source_id,
        "display_index": display_index,
        "label": label,
        "d": d,
        "tx": tx,
        "ty": ty,
        "color": color,
        "group_id": f"src_{source_id}",
        "part_index": part_index,
        "part_count": part_count,
        "prep_note": prep_note,
        "elongation": _path_elongation(d, tx, ty),
        "order": source_id + part_index / 100.0,
        "hidden": False,
        "render_mode": render_mode,
        "stroke_width": stroke_width,
        "source_kind": source_kind,
    }


def _subpath_bbox_world(subpath, tx: float, ty: float):
    try:
        xmin, xmax, ymin, ymax = subpath.bbox()
        return (xmin + tx, ymin + ty, xmax + tx, ymax + ty)
    except Exception:
        return None


def _bbox_contains_outer(outer, inner, margin: float = 0.5) -> bool:
    if not outer or not inner:
        return False
    return (
        outer[0] <= inner[0] + margin and
        outer[1] <= inner[1] + margin and
        outer[2] >= inner[2] - margin and
        outer[3] >= inner[3] - margin
    )


def _subpath_is_probably_closed(subpath) -> bool:
    try:
        start = subpath.point(0)
        end = subpath.point(1)
        return abs(start - end) < 0.75
    except Exception:
        return False


def split_source_path_object(src_obj: dict) -> list:
    """
    Best-guess split for Pane 3.

    Inner contours/holes must stay with their outer contour. Earlier builds
    split every continuous subpath, which turned ring holes into visible filled
    objects such as Path 2b. This version only splits genuinely separate
    subpaths and keeps nested contours together as compound paths.
    """
    if parse_path is None:
        return [_make_structure_object(
            src_obj["source_id"], src_obj["display_index"], src_obj["d"],
            src_obj["tx"], src_obj["ty"], src_obj["color"]
        )]

    try:
        parsed = parse_path(src_obj["d"])
        subpaths = parsed.continuous_subpaths()
    except Exception:
        subpaths = []

    if len(subpaths) <= 1:
        return [_make_structure_object(
            src_obj["source_id"], src_obj["display_index"], src_obj["d"],
            src_obj["tx"], src_obj["ty"], src_obj["color"]
        )]

    records = []
    for idx, sp in enumerate(subpaths):
        bbox = _subpath_bbox_world(sp, src_obj["tx"], src_obj["ty"])
        if bbox is None:
            continue
        area = max(0.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        records.append({
            "idx": idx,
            "sp": sp,
            "bbox": bbox,
            "area": area,
            "closed": _subpath_is_probably_closed(sp),
        })

    if len(records) <= 1:
        return [_make_structure_object(
            src_obj["source_id"], src_obj["display_index"], src_obj["d"],
            src_obj["tx"], src_obj["ty"], src_obj["color"]
        )]

    parent = {r["idx"]: None for r in records}
    for child in records:
        if not child["closed"]:
            continue
        containers = []
        for outer in records:
            if outer["idx"] == child["idx"] or not outer["closed"]:
                continue
            if outer["area"] > child["area"] * 1.05 and _bbox_contains_outer(outer["bbox"], child["bbox"]):
                containers.append(outer)
        if containers:
            containers.sort(key=lambda r: r["area"])
            parent[child["idx"]] = containers[0]["idx"]

    groups = []
    consumed = set()

    for rec in sorted(records, key=lambda r: r["idx"]):
        idx = rec["idx"]
        if idx in consumed:
            continue

        if parent[idx] is not None:
            continue

        group = [idx]
        for child_idx, parent_idx in parent.items():
            if parent_idx == idx:
                group.append(child_idx)

        group = sorted(group)
        groups.append(group)
        consumed.update(group)

    for rec in records:
        if rec["idx"] not in consumed:
            groups.append([rec["idx"]])

    if len(groups) <= 1:
        return [_make_structure_object(
            src_obj["source_id"], src_obj["display_index"], src_obj["d"],
            src_obj["tx"], src_obj["ty"], src_obj["color"],
            prep_note="compound path with preserved inner contour"
        )]

    out = []
    count = len(groups)
    rec_by_idx = {r["idx"]: r for r in records}

    for part_idx, group in enumerate(groups):
        combined_d = " ".join(rec_by_idx[i]["sp"].d() for i in group if i in rec_by_idx)
        out.append(_make_structure_object(
            src_obj["source_id"], src_obj["display_index"], combined_d,
            src_obj["tx"], src_obj["ty"], src_obj["color"],
            part_index=part_idx, part_count=count,
            prep_note=f"split from source path {src_obj['display_index']} ({part_idx+1}/{count})"
        ))

    return out


def parse_traced_svg_for_structure(svg_path: str) -> tuple[float, float, list, list]:
    tree = ET.parse(svg_path)
    root = tree.getroot()

    svg_w = _svg_float(root.attrib.get("width"), 500.0)
    svg_h = _svg_float(root.attrib.get("height"), 500.0)

    viewbox = root.attrib.get("viewBox")
    if viewbox:
        parts = re.split(r"[,\s]+", viewbox.strip())
        if len(parts) == 4:
            try:
                svg_w = float(parts[2])
                svg_h = float(parts[3])
            except Exception:
                pass

    path_elems = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1]
        if tag.lower() == "path":
            path_elems.append(elem)

    source_paths = []
    objects = []

    for i, elem in enumerate(path_elems):
        d = elem.attrib.get("d", "").strip()
        if not d:
            continue

        fill = elem.attrib.get("fill") or _extract_style_value(elem.attrib.get("style", ""), "fill", "#000000")
        if not fill or fill == "none":
            fill = "#000000"

        tx, ty = _parse_translate(elem.attrib.get("transform", ""))

        src = _make_structure_object(
            source_id=i,
            display_index=i + 1,
            d=d,
            tx=tx,
            ty=ty,
            color=fill,
            prep_note="original source path"
        )
        split_parts = split_source_path_object(src)

        src_export = dict(src)
        src_export["split_parts"] = split_parts

        source_paths.append(src_export)
        objects.extend(split_parts if len(split_parts) > 1 else [dict(src)])

    objects.sort(key=lambda x: x.get("order", 0.0))
    return svg_w, svg_h, source_paths, objects


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2b: stroke candidate extraction from prepared raster
# ─────────────────────────────────────────────────────────────────────────────

def connected_components_bool(mask: np.ndarray, min_area: int = 1) -> list:
    h, w = mask.shape
    visited = np.zeros((h, w), dtype=bool)
    comps = []

    for y in range(h):
        for x in range(w):
            if not mask[y, x] or visited[y, x]:
                continue

            q = deque([(y, x)])
            visited[y, x] = True
            pixels = []
            minx = maxx = x
            miny = maxy = y

            while q:
                cy, cx = q.popleft()
                pixels.append((cy, cx))
                if cx < minx: minx = cx
                if cx > maxx: maxx = cx
                if cy < miny: miny = cy
                if cy > maxy: maxy = cy

                for ny, nx in _neighbors8(cy, cx, h, w):
                    if mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        q.append((ny, nx))

            if len(pixels) < min_area:
                continue

            comp_mask = np.zeros((maxy - miny + 1, maxx - minx + 1), dtype=bool)
            for py, px in pixels:
                comp_mask[py - miny, px - minx] = True

            comps.append({
                "mask": comp_mask,
                "bbox": (minx, miny, maxx + 1, maxy + 1),
                "area": len(pixels),
                "width": maxx - minx + 1,
                "height": maxy - miny + 1,
            })

    return comps


def zhang_suen_thin(binary: np.ndarray) -> np.ndarray:
    img = binary.astype(np.uint8).copy()
    changed = True
    h, w = img.shape
    if h < 3 or w < 3:
        return img.astype(bool)

    while changed:
        changed = False
        to_remove = []

        for step in (0, 1):
            to_remove.clear()
            for y in range(1, h - 1):
                for x in range(1, w - 1):
                    if img[y, x] != 1:
                        continue

                    p2 = img[y - 1, x]
                    p3 = img[y - 1, x + 1]
                    p4 = img[y, x + 1]
                    p5 = img[y + 1, x + 1]
                    p6 = img[y + 1, x]
                    p7 = img[y + 1, x - 1]
                    p8 = img[y, x - 1]
                    p9 = img[y - 1, x - 1]

                    neighbors = [p2, p3, p4, p5, p6, p7, p8, p9]
                    B = sum(neighbors)
                    if B < 2 or B > 6:
                        continue

                    A = sum((neighbors[i] == 0 and neighbors[(i + 1) % 8] == 1) for i in range(8))
                    if A != 1:
                        continue

                    if step == 0:
                        if p2 * p4 * p6 != 0:
                            continue
                        if p4 * p6 * p8 != 0:
                            continue
                    else:
                        if p2 * p4 * p8 != 0:
                            continue
                        if p2 * p6 * p8 != 0:
                            continue

                    to_remove.append((y, x))

            if to_remove:
                changed = True
                for y, x in to_remove:
                    img[y, x] = 0

    return img.astype(bool)


def _skeleton_segments(skel: np.ndarray) -> list:
    pts = {(y, x) for y, x in zip(*np.where(skel))}
    if not pts:
        return []

    nbrs = {}
    for y, x in pts:
        nbrs[(y, x)] = [(ny, nx) for ny, nx in _neighbors8(y, x, *skel.shape) if (ny, nx) in pts]

    nodes = {p for p, ns in nbrs.items() if len(ns) != 2}
    used_edges = set()
    segments = []

    def edge_key(a, b):
        return tuple(sorted((a, b)))

    for node in list(nodes):
        for nxt in nbrs[node]:
            ek = edge_key(node, nxt)
            if ek in used_edges:
                continue

            seg = [node]
            prev = node
            cur = nxt
            used_edges.add(ek)

            while True:
                seg.append(cur)
                cur_nbrs = nbrs[cur]
                if cur in nodes and cur != node:
                    break

                choices = [n for n in cur_nbrs if n != prev]
                if not choices:
                    break

                nxt2 = choices[0]
                ek2 = edge_key(cur, nxt2)
                if ek2 in used_edges:
                    break
                used_edges.add(ek2)
                prev, cur = cur, nxt2

            if len(seg) >= 2:
                segments.append(seg)

    # Closed loops with no endpoints/junctions.
    remaining = [p for p in pts if all(edge_key(p, n) not in used_edges for n in nbrs[p])]
    visited = set()
    for start in remaining:
        if start in visited:
            continue
        loop = [start]
        visited.add(start)
        prev = None
        cur = start
        while True:
            choices = [n for n in nbrs[cur] if n != prev]
            if not choices:
                break
            nxt = choices[0]
            if nxt == start:
                loop.append(nxt)
                break
            if nxt in visited:
                break
            loop.append(nxt)
            visited.add(nxt)
            prev, cur = cur, nxt
        if len(loop) >= 3:
            segments.append(loop)

    return segments


def stroke_preview_svg(svg_w: int, svg_h: int, stroke_objects: list) -> str:
    chunks = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}">'
    ]
    for obj in stroke_objects:
        sw = max(1.1, float(obj.get("stroke_width", 1.6)) * 0.65)
        chunks.append(
            f'<path d="{obj["d"]}" fill="none" stroke="{obj["color"]}" '
            f'stroke-width="{sw:.2f}" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    chunks.append("</svg>")
    return "".join(chunks)


def extract_stroke_candidates(
    prepared_png: str,
    min_component_area: int = 24,
    max_fill_ratio: float = 0.42,
    min_aspect_ratio: float = 1.6,
    min_path_length: float = 14.0,
    ignore_near_white: bool = True,
) -> dict:
    img = Image.open(prepared_png).convert("RGB")
    arr = np.array(img)
    h, w = arr.shape[:2]

    flat = arr.reshape(-1, 3)
    colors, counts = np.unique(flat, axis=0, return_counts=True)

    stroke_objects = []
    component_count = 0

    for color_rgb, count in sorted(zip(colors, counts), key=lambda x: int(x[1]), reverse=True):
        r, g, b = [int(v) for v in color_rgb]
        if ignore_near_white and r > 245 and g > 245 and b > 245:
            continue

        color_hex = f"#{r:02X}{g:02X}{b:02X}"
        mask = np.all(arr == color_rgb, axis=2)
        comps = connected_components_bool(mask, min_area=min_component_area)

        for comp in comps:
            component_count += 1
            cw, ch = comp["width"], comp["height"]
            bbox_area = max(1, cw * ch)
            fill_ratio = comp["area"] / bbox_area
            aspect = max(cw, ch) / max(1, min(cw, ch))

            # Broad heuristic: long/thin or sparse-in-bbox components are good
            # stroke candidates. This keeps rings and outline-like shapes.
            if not (fill_ratio <= max_fill_ratio or aspect >= min_aspect_ratio):
                continue

            padded = np.pad(comp["mask"], 1, constant_values=False)
            skel = zhang_suen_thin(padded)
            segments = _skeleton_segments(skel)
            if not segments:
                continue

            total_pts = sum(len(seg) for seg in segments)
            est_width = max(1.0, min(12.0, comp["area"] / max(total_pts, 1.0) * 1.4))

            bx0, by0, _, _ = comp["bbox"]
            for seg_idx, seg in enumerate(segments):
                pts = []
                for py, px in seg:
                    gx = bx0 + (px - 1) + 0.5
                    gy = by0 + (py - 1) + 0.5
                    pts.append((gx, gy))

                pts = _simplify_points(pts, step=2)
                if len(pts) < 2:
                    continue

                plen = _polyline_length(pts)
                if plen < min_path_length:
                    continue

                d = _points_to_svg_path(pts)
                if not d:
                    continue

                stroke_objects.append({
                    "id": f"stroke_{len(stroke_objects)+1}",
                    "d": d,
                    "color": color_hex,
                    "stroke_width": round(est_width, 2),
                    "path_length": round(plen, 2),
                    "bbox": [int(v) for v in comp["bbox"]],
                    "fill_ratio": round(fill_ratio, 3),
                    "aspect_ratio": round(aspect, 3),
                    "source_kind": "stroke_candidate",
                    "prep_note": f"extracted stroke candidate from colour {color_hex}",
                })

    return {
        "svg_w": w,
        "svg_h": h,
        "stroke_objects": stroke_objects,
        "stroke_count": len(stroke_objects),
        "component_count": component_count,
        "stroke_preview_svg": stroke_preview_svg(w, h, stroke_objects),
    }


def build_structure_payload_from_trace(trace: dict) -> tuple[float, float, list, list]:
    svg_w, svg_h, source_paths, objects = parse_traced_svg_for_structure(trace["output_path"])

    # Append extracted stroke candidates as additional editable source objects.
    stroke_objs = trace.get("stroke_objects") or []
    next_source_id = (max([s["source_id"] for s in source_paths]) + 1) if source_paths else 0

    for idx, st in enumerate(stroke_objs):
        sid = next_source_id + idx
        src = _make_structure_object(
            source_id=sid,
            display_index=sid + 1,
            d=st["d"],
            tx=0.0,
            ty=0.0,
            color=st["color"],
            prep_note=st.get("prep_note", "extracted stroke candidate"),
            render_mode="stroke",
            stroke_width=float(st.get("stroke_width", 1.6)),
            source_kind="stroke_candidate",
        )
        src["split_parts"] = [dict(src)]
        source_paths.append(src)
        objects.append(dict(src))

    objects.sort(key=lambda x: x.get("order", 0.0))
    return svg_w, svg_h, source_paths, objects
