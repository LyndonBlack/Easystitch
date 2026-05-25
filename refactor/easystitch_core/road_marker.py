"""Satin V2 road-graph mask preparation.

This module is intentionally limited to checklist Step 1:
- collect_satin_objects
- render_satin_mask
- clean_binary_mask

Do not add AutoTrace, graph parsing, UI, or stitch-generation logic here until
those checklist steps are explicitly started.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from io import BytesIO
from typing import Any, cast
from xml.sax.saxutils import escape

import cairosvg
from PIL import Image, ImageFilter
from svgpathtools import Line, parse_path


SATIN_ASSIGNMENT = "satin"


def _object_id(obj: dict[str, Any]) -> str | None:
    """Return the Pane 3 object id, accepting only generic id field names."""
    value = obj.get("id")
    if value is None:
        return None
    return str(value)


def _is_satin_assignment(value: Any) -> bool:
    """Case-insensitive Satin assignment check."""
    return isinstance(value, str) and value.strip().lower() == SATIN_ASSIGNMENT


def collect_satin_objects(objects: list[dict[str, Any]], assignments: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Return only objects currently assigned as Satin.

    Rules from the checklist:
    - include only assignment == "satin" case-insensitively
    - exclude fill, skip, background, unset, and missing assignments
    - use the current Pane 3 object list after manual splits
    - preserve object fields such as id, d, color, tx, and ty
    """
    satin_objects: list[dict[str, Any]] = []

    for obj in objects or []:
        obj_id = _object_id(obj)
        if obj_id is None:
            continue
        if _is_satin_assignment(assignments.get(obj_id)):
            satin_objects.append(dict(obj))

    return satin_objects


def _format_number(value: Any, default: float = 0.0) -> str:
    """Format a numeric SVG attribute defensively."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return f"{number:g}"


def _build_compositing_mask_svg(
    objects: list[dict[str, Any]],
    assignments: dict[str, Any],
    svg_w: float,
    svg_h: float,
) -> str:
    """Build a temporary SVG with all objects, recoloured by assignment.

    Satin objects render as #000000 (black).
    Fill/Skip/Background/unset objects render as #ffffff (white).
    Object order is preserved exactly — a later Fill object can knock out an
    earlier Satin backing shape, matching the visual stacking of the traced SVG.
    """
    width = _format_number(svg_w)
    height = _format_number(svg_h)

    path_elements: list[str] = []
    for obj in objects or []:
        d = obj.get("d")
        if not d:
            continue

        obj_id = obj.get("id")
        fill_color = "#ffffff"  # default: white (background/unset)
        if obj_id is not None and _is_satin_assignment(assignments.get(str(obj_id))):
            fill_color = "#000000"

        tx = float(obj.get("tx") or 0.0)
        ty = float(obj.get("ty") or 0.0)
        transform = ""
        if tx or ty:
            transform = f' transform="translate({_format_number(tx)} {_format_number(ty)})"'

        path_elements.append(
            f'<path d="{escape(str(d))}" fill="{fill_color}" stroke="none"{transform}/>'
        )

    paths = "\n  ".join(path_elements)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>
  {paths}
</svg>
'''


def render_satin_mask(
    objects: list[dict[str, Any]],
    assignments: dict[str, Any],
    svg_w: float,
    svg_h: float,
    scale: int = 4,
    antialias: bool = False,
) -> dict[str, Any]:
    """Render a visible Satin compositing mask.

    All current Pane 3 objects are rendered into a temporary SVG in order.
    Satin objects are black (#000000).
    Fill/Skip/Background/unset objects are white (#ffffff) and can knock out
    earlier black Satin objects, matching the visual stacking of the traced SVG.

    Returns:
        dict with image (PIL.Image), dimensions, scale, satin_object_ids,
        and excluded_object_ids.
    """
    if scale <= 0:
        raise ValueError("scale must be greater than zero")
    if svg_w <= 0 or svg_h <= 0:
        raise ValueError("svg_w and svg_h must be greater than zero")

    # ── Satin-only IDs for reporting ────────────────────────────────────
    satin_objects = collect_satin_objects(objects, assignments)
    satin_ids = [_object_id(obj) for obj in satin_objects if _object_id(obj) is not None]
    satin_id_set = set(satin_ids)

    all_ids = [_object_id(obj) for obj in (objects or [])]
    excluded_ids = [obj_id for obj_id in all_ids if obj_id is not None and obj_id not in satin_id_set]

    # ── Compositing mask SVG (all objects, coloured by assignment) ──────
    width_px = int(round(float(svg_w) * scale))
    height_px = int(round(float(svg_h) * scale))
    svg_text = _build_compositing_mask_svg(objects, assignments, svg_w, svg_h)

    png_data = cairosvg.svg2png(
        bytestring=svg_text.encode("utf-8"),
        output_width=width_px,
        output_height=height_px,
    )
    if png_data is None:
        raise RuntimeError("CairoSVG did not return PNG data")
    image = Image.open(BytesIO(cast(bytes, png_data))).convert("L")

    if not antialias:
        image = clean_binary_mask(image, median_filter=False, threshold=128)

    return {
        "image": image,
        "width_px": width_px,
        "height_px": height_px,
        "scale": float(scale),
        "satin_object_ids": satin_ids,
        "excluded_object_ids": excluded_ids,
    }


def clean_binary_mask(image: Image.Image, median_filter: bool = True, threshold: int = 128) -> Image.Image:
    """
    Convert a mask image to pure black/white L-mode.

    Rules from the checklist:
    - black = satin
    - white = background
    - no grey pixels in final mask
    - optional 3x3 median filter
    """
    if image is None:
        raise ValueError("image is required")

    threshold = max(0, min(255, int(threshold)))
    result = image.convert("L")

    if median_filter:
        result = result.filter(ImageFilter.MedianFilter(size=3))

    lookup = [0 if pixel < threshold else 255 for pixel in range(256)]
    return result.point(lookup, mode="L")


def _polyline_length(points: list[list[float]]) -> float:
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
    return total


def _normalise_point(point: Any) -> list[float]:
    return [float(point[0]), float(point[1])]


def _make_polyline(polyline_id: str, points: list[list[float]]) -> dict[str, Any] | None:
    cleaned: list[list[float]] = []
    for point in points:
        p = _normalise_point(point)
        if not cleaned or math.hypot(p[0] - cleaned[-1][0], p[1] - cleaned[-1][1]) > 1e-9:
            cleaned.append(p)
    if len(cleaned) < 2:
        return None
    return {"id": polyline_id, "points": cleaned, "length": _polyline_length(cleaned)}


def run_autotrace_centerline(
    mask_image: Image.Image,
    autotrace_path: str = "autotrace",
    despeckle_level: int = 8,
    filter_iterations: int = 4,
    error_threshold: float = 2.0,
) -> str:
    """Save mask to a temporary PNG, run AutoTrace --centerline, return SVG text."""
    resolved = shutil.which(autotrace_path) if os.path.basename(autotrace_path) == autotrace_path else autotrace_path
    if not resolved or not os.path.exists(resolved):
        raise RuntimeError("AutoTrace not found. Install autotrace and ensure it is on PATH.")

    if mask_image is None:
        raise ValueError("mask_image is required")

    with tempfile.TemporaryDirectory(prefix="easystitch_autotrace_") as tmp_dir:
        mask_path = os.path.join(tmp_dir, "road_mask.png")
        svg_path = os.path.join(tmp_dir, "road_centerline.svg")
        clean_binary_mask(mask_image, median_filter=False, threshold=128).save(mask_path)

        full_cmd = [
            resolved,
            "--centerline",
            "--background-color", "FFFFFF",
            "--output-format", "svg",
            "--despeckle-level", str(int(despeckle_level)),
            "--filter-iterations", str(int(filter_iterations)),
            "--error-threshold", str(float(error_threshold)),
            "--output-file", svg_path,
            mask_path,
        ]
        minimal_cmd = [
            resolved,
            "--centerline",
            "--background-color", "FFFFFF",
            "--output-format", "svg",
            "--output-file", svg_path,
            mask_path,
        ]

        first = subprocess.run(full_cmd, capture_output=True, text=True, timeout=60, check=False)
        if first.returncode != 0:
            second = subprocess.run(minimal_cmd, capture_output=True, text=True, timeout=60, check=False)
            if second.returncode != 0:
                message = (second.stderr or second.stdout or first.stderr or first.stdout or "AutoTrace failed").strip()
                raise RuntimeError(f"AutoTrace failed: {message}")

        if not os.path.exists(svg_path):
            raise RuntimeError("AutoTrace failed: output SVG was not created")
        with open(svg_path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()


def _element_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _parse_points_attr(points_text: str, scale: float) -> list[list[float]]:
    numbers = [float(value) for value in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", points_text or "")]
    points: list[list[float]] = []
    for i in range(0, len(numbers) - 1, 2):
        points.append([numbers[i] / scale, numbers[i + 1] / scale])
    return points


def _flatten_svg_path_subpaths(d: str, scale: float) -> list[list[list[float]]]:
    path = parse_path(d)
    subpaths: list[list[list[float]]] = []
    current: list[list[float]] = []
    previous_end: complex | None = None

    def append_point(point_value: complex) -> None:
        point = [float(point_value.real) / scale, float(point_value.imag) / scale]
        if not current or math.hypot(point[0] - current[-1][0], point[1] - current[-1][1]) > 1e-9:
            current.append(point)

    for segment in path:
        if previous_end is None or abs(segment.start - previous_end) > 1e-7:
            if len(current) >= 2:
                subpaths.append(current)
            current = []
            append_point(segment.start)

        if isinstance(segment, Line):
            samples = [segment.end]
        else:
            try:
                seg_len = float(segment.length(error=1e-3))
            except Exception:
                seg_len = 20.0
            steps = max(4, min(80, int(math.ceil(seg_len / max(scale, 1.0) / 2.0))))
            samples = [segment.point(i / steps) for i in range(1, steps + 1)]
        for sample in samples:
            append_point(sample)
        previous_end = segment.end

    if len(current) >= 2:
        subpaths.append(current)
    return subpaths


def parse_centerline_svg_to_polylines(svg_text: str, scale: float) -> list[dict[str, Any]]:
    """Parse AutoTrace SVG line/polyline/path elements into original-SVG-coordinate polylines."""
    if not svg_text or not svg_text.strip():
        return []
    if scale <= 0:
        raise ValueError("scale must be greater than zero")

    root = ET.fromstring(svg_text)
    polylines: list[dict[str, Any]] = []
    counter = 1

    for element in root.iter():
        name = _element_local_name(element.tag)
        points: list[list[float]] = []
        if name == "polyline" or name == "polygon":
            points = _parse_points_attr(element.attrib.get("points", ""), scale)
            if name == "polygon" and len(points) > 2 and points[0] != points[-1]:
                points.append(list(points[0]))
        elif name == "line":
            try:
                points = [
                    [float(element.attrib.get("x1", "0")) / scale, float(element.attrib.get("y1", "0")) / scale],
                    [float(element.attrib.get("x2", "0")) / scale, float(element.attrib.get("y2", "0")) / scale],
                ]
            except ValueError:
                points = []
        elif name == "path" and element.attrib.get("d"):
            try:
                subpaths = _flatten_svg_path_subpaths(element.attrib["d"], scale)
            except Exception:
                subpaths = []
            for subpath in subpaths:
                polyline = _make_polyline(f"cline_{counter}", subpath)
                if polyline is not None:
                    polylines.append(polyline)
                    counter += 1
            continue

        polyline = _make_polyline(f"cline_{counter}", points)
        if polyline is not None:
            polylines.append(polyline)
            counter += 1

    return polylines


def _douglas_peucker(points: list[list[float]], tolerance: float) -> list[list[float]]:
    if len(points) <= 2 or tolerance <= 0:
        return [list(p) for p in points]

    start = points[0]
    end = points[-1]
    sx, sy = start
    ex, ey = end
    line_len = math.hypot(ex - sx, ey - sy)
    max_dist = -1.0
    index = 0

    for i in range(1, len(points) - 1):
        px, py = points[i]
        if line_len == 0:
            dist = math.hypot(px - sx, py - sy)
        else:
            dist = abs((ey - sy) * px - (ex - sx) * py + ex * sy - ey * sx) / line_len
        if dist > max_dist:
            max_dist = dist
            index = i

    if max_dist > tolerance:
        left = _douglas_peucker(points[: index + 1], tolerance)
        right = _douglas_peucker(points[index:], tolerance)
        return left[:-1] + right
    return [list(start), list(end)]


def _point_distance(a: list[float], b: list[float]) -> float:
    return math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))


def _dedupe_consecutive_points(points: list[list[float]]) -> list[list[float]]:
    deduped: list[list[float]] = []
    for point in points:
        p = _normalise_point(point)
        if not deduped or _point_distance(p, deduped[-1]) > 1e-9:
            deduped.append(p)
    return deduped


def _is_closed_polyline(points: list[list[float]], tolerance: float = 1.5) -> bool:
    return len(points) >= 4 and _point_distance(points[0], points[-1]) <= tolerance


def _closed_polyline_perimeter(points: list[list[float]]) -> float:
    if len(points) < 2:
        return 0.0
    total = _polyline_length(points)
    if _point_distance(points[0], points[-1]) > 1e-9:
        total += _point_distance(points[-1], points[0])
    return total


def _make_closed_polyline(polyline_id: str, points: list[list[float]]) -> dict[str, Any] | None:
    deduped = _dedupe_consecutive_points(points)
    if len(deduped) >= 2 and _point_distance(deduped[0], deduped[-1]) <= 1.5:
        deduped = deduped[:-1]
    if len(deduped) < 4:
        return None
    deduped.append(list(deduped[0]))
    return {"id": polyline_id, "points": deduped, "length": _closed_polyline_perimeter(deduped)}


def clean_centerline_polylines(
    polylines: list[dict[str, Any]],
    min_length_px: float = 5.0,
    simplify_tolerance: float = 1.0,
) -> list[dict[str, Any]]:
    """Remove tiny centerline paths and simplify jitter while preserving closed loops."""
    cleaned: list[dict[str, Any]] = []
    for polyline in polylines or []:
        points = _dedupe_consecutive_points([list(map(float, point)) for point in polyline.get("points", [])])
        if len(points) < 2:
            continue

        polyline_id = str(polyline.get("id") or f"cline_{len(cleaned) + 1}")
        if _is_closed_polyline(points):
            length = _closed_polyline_perimeter(points)
            meaningful_points = points[:-1] if _point_distance(points[0], points[-1]) <= 1.5 else points
            if length < min_length_px or len(meaningful_points) < 4:
                continue
            # Closed loops are common for small cheeks/eyes. Do not run the
            # open-line Douglas-Peucker simplifier on them: identical endpoints
            # collapse the loop to a degenerate two-point path.
            made = _make_closed_polyline(polyline_id, points)
        else:
            length = float(polyline.get("length") or _polyline_length(points))
            if length < min_length_px:
                continue
            simplified = _douglas_peucker(points, simplify_tolerance)
            made = _make_polyline(polyline_id, simplified)

        if made is not None:
            cleaned.append(made)
    return cleaned


def _render_object_label_map(
    objects: list[dict[str, Any]],
    assignments: dict[str, Any],
    svg_w: float,
    svg_h: float,
    scale: int = 4,
) -> tuple[Image.Image | None, list[dict[str, Any]], dict[str, int]]:
    """Render each Satin object with a unique grayscale fill.

    Returns:
        (label_map_image, satin_objects, object_id_to_index)
        - label_map_image: L-mode PIL Image (mask_w x mask_h) where pixel value =
          satin_object_index + 1, or 0 for background / non-Satin areas.
        - satin_objects: list of Satin objects in render order.
        - object_id_to_index: dict mapping object id -> index in satin_objects.
    """
    satin_objects = [obj for obj in (objects or [])
                     if _object_id(obj) is not None
                     and _is_satin_assignment(assignments.get(str(_object_id(obj))))]

    if not satin_objects:
        return None, [], {}

    width = _format_number(svg_w)
    height = _format_number(svg_h)
    width_px = int(round(float(svg_w) * scale))
    height_px = int(round(float(svg_h) * scale))

    object_id_to_index: dict[str, int] = {}
    path_elements: list[str] = []
    for idx, obj in enumerate(satin_objects):
        d = obj.get("d")
        if not d:
            continue
        obj_id = str(_object_id(obj))
        label = idx + 1  # 1-based so 0 = background
        fill_color = f"#{label:02x}{label:02x}{label:02x}"
        object_id_to_index[obj_id] = idx
        tx = float(obj.get("tx") or 0.0)
        ty = float(obj.get("ty") or 0.0)
        transform = ""
        if tx or ty:
            transform = f' transform="translate({_format_number(tx)} {_format_number(ty)})"'
        path_elements.append(
            f'<path d="{escape(str(d))}" fill="{fill_color}" stroke="none"{transform}/>'
        )

    if not path_elements:
        return None, [], {}

    paths = "\n  ".join(path_elements)
    svg_text = (
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' width="{width}" height="{height}"'
        f' viewBox="0 0 {width} {height}">\n'
        f'  <rect x="0" y="0" width="{width}" height="{height}" fill="#000000"/>\n'
        f'  {paths}\n'
        f'</svg>\n'
    )

    try:
        png_data = cairosvg.svg2png(
            bytestring=svg_text.encode("utf-8"),
            output_width=width_px,
            output_height=height_px,
        )
    except Exception:
        return None, [], {}

    if png_data is None:
        return None, [], {}

    img = Image.open(BytesIO(png_data)).convert("L")
    return img, satin_objects, object_id_to_index


def split_polylines_at_object_boundaries(
    polylines: list[dict[str, Any]],
    objects: list[dict[str, Any]],
    assignments: dict[str, Any],
    svg_w: float,
    svg_h: float,
    scale: int = 4,
    object_label_map: Image.Image | None = None,
    satin_objects: list[dict[str, Any]] | None = None,
    object_id_to_index: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Split polylines wherever the centreline crosses a boundary between two Satin objects.

    Uses a label map (rendered with _render_object_label_map) to determine which
    Satin object owns each point on the polyline. When consecutive points belong
    to different Satin objects, a split node is inserted at the midpoint and the
    polyline is broken into two (or more) separate polylines.

    Each resulting polyline carries a ``source_object_ids`` list populated with
    the Pane 3 object id(s) that contributed to it.

    If label rendering fails or no boundaries are found, the original polylines
    are returned unchanged (with empty source_object_ids).
    """
    if not polylines:
        return []

    # Build label map if not provided
    label_map: Image.Image | None = object_label_map
    _satin_objects: list[dict[str, Any]] = list(satin_objects or [])
    _oid_to_idx: dict[str, int] = dict(object_id_to_index or {})

    if label_map is None:
        label_map, _satin_objects, _oid_to_idx = _render_object_label_map(
            objects, assignments, svg_w, svg_h, scale
        )

    if label_map is None:
        # No Satin objects — return original polylines with empty source_object_ids
        return [dict(p, source_object_ids=[]) for p in polylines]

    # Build reverse lookup: label_value -> object_id  (label = satin_index + 1)
    idx_to_obj_id: dict[int, str] = {}
    for obj_id, idx in _oid_to_idx.items():
        idx_to_obj_id[idx + 1] = obj_id

    result: list[dict[str, Any]] = []
    split_count = 0

    for polyline in polylines or []:
        points = polyline.get("points", [])
        if len(points) < 2:
            result.append(dict(polyline, source_object_ids=[]))
            continue

        # Sample label at each point (in SVG coordinates, label map is at mask scale)
        point_labels: list[int | None] = []
        for px, py in points:
            sx = int(round(float(px) * scale))
            sy = int(round(float(py) * scale))
            if 0 <= sx < label_map.width and 0 <= sy < label_map.height:
                pixel = label_map.getpixel((sx, sy))
                label: int = int(pixel) if isinstance(pixel, (int, float)) else 0
                point_labels.append(label if label > 0 else None)
            else:
                point_labels.append(None)

        # If every point has the same label (or all None), no split needed
        unique_labels = {l for l in point_labels if l is not None}
        if len(unique_labels) <= 1:
            # All same object (or all background/None) — no split
            source_ids: list[str] = []
            lab = next((l for l in point_labels if l is not None), None)
            if lab is not None:
                oid = idx_to_obj_id.get(lab)
                if oid is not None:
                    source_ids = [oid]
            result.append(dict(polyline, source_object_ids=source_ids))
            continue

        # Walk the points, split where label changes
        segments: list[dict[str, Any]] = []
        current_points: list[list[float]] = [list(points[0])]
        current_label: int | None = point_labels[0]

        for i in range(1, len(points)):
            this_label = point_labels[i]

            if (this_label is not None and current_label is not None
                    and this_label != current_label):
                # Boundary detected — insert split at midpoint between points[i-1] and points[i]
                mid_x = (float(points[i - 1][0]) + float(points[i][0])) / 2.0
                mid_y = (float(points[i - 1][1]) + float(points[i][1])) / 2.0
                current_points.append([mid_x, mid_y])

                if len(current_points) >= 2:
                    src_id = idx_to_obj_id.get(current_label, "")
                    segments.append({
                        "points": [list(p) for p in current_points],
                        "source_object_ids": [src_id] if src_id else [],
                    })
                split_count += 1

                # Start new segment from split point
                current_points = [[mid_x, mid_y], [float(points[i][0]), float(points[i][1])]]
                current_label = this_label
            else:
                current_points.append([float(points[i][0]), float(points[i][1])])
                current_label = current_label if current_label is not None else this_label

        # Final segment
        if len(current_points) >= 2:
            src_id = idx_to_obj_id.get(current_label, "") if current_label is not None else ""
            segments.append({
                "points": [list(p) for p in current_points],
                "source_object_ids": [src_id] if src_id else [],
            })

        # Turn segments into polylines — merged back if only one (no actual split)
        base_id = polyline.get("id", "cline")
        for seg_idx, seg in enumerate(segments):
            seg_points = _dedupe_consecutive_points(seg["points"])
            if len(seg_points) < 2:
                continue
            poly_id = f"{base_id}_b{seg_idx}" if len(segments) > 1 else f"{base_id}"
            made = _make_polyline(poly_id, seg_points)
            if made is not None:
                made["source_object_ids"] = seg["source_object_ids"]
                result.append(made)

    return result


def build_centerline_graph(polylines: list[dict[str, Any]], snap_distance: float = 3.0) -> dict[str, Any]:
    """Build a simple endpoint-snapped graph from centerline polylines."""
    node_points: list[list[float]] = []
    edge_specs: list[tuple[str, int, int, list[list[float]], float, list[str]]] = []

    def node_for(point: list[float]) -> int:
        for idx, existing in enumerate(node_points):
            if math.hypot(point[0] - existing[0], point[1] - existing[1]) <= snap_distance:
                existing[0] = (existing[0] + point[0]) / 2.0
                existing[1] = (existing[1] + point[1]) / 2.0
                return idx
        node_points.append([float(point[0]), float(point[1])])
        return len(node_points) - 1

    for edge_index, polyline in enumerate(polylines or [], start=1):
        points = [list(map(float, point)) for point in polyline.get("points", [])]
        if len(points) < 2:
            continue
        source_idx = node_for(points[0])
        target_idx = node_for(points[-1])
        edge_specs.append((f"edge_{edge_index}", source_idx, target_idx, points, _polyline_length(points),
                           polyline.get("source_object_ids", [])))

    degrees = [0 for _ in node_points]
    for spec in edge_specs:
        _, source_idx, target_idx, _, _, _ = spec
        degrees[source_idx] += 1
        degrees[target_idx] += 1

    nodes: list[dict[str, Any]] = []
    for idx, point in enumerate(node_points):
        degree = degrees[idx]
        node_type = "junction" if degree >= 3 else ("endpoint" if degree == 1 else "pass_through")
        nodes.append({
            "id": f"node_{idx + 1}",
            "x": point[0],
            "y": point[1],
            "type": node_type,
            "degree": degree,
        })

    edges: list[dict[str, Any]] = []
    for edge_id, source_idx, target_idx, points, length, source_ids in edge_specs:
        edges.append({
            "id": edge_id,
            "source": f"node_{source_idx + 1}",
            "target": f"node_{target_idx + 1}",
            "points": points,
            "length": length,
            "source_object_ids": list(source_ids),
            "priority": None,
            "assignment": "unmarked",
        })

    return {"nodes": nodes, "edges": edges}


def tag_split_boundary_nodes(graph: dict[str, Any]) -> dict[str, Any]:
    """Walk graph nodes and tag any node that connects edges with different source_object_ids.

    Returns the graph with node types updated — nodes that sit between edges
    belonging to different Pane 3 Satin objects are typed ``manual_split_boundary``.
    """
    nodes = list(graph.get("nodes", []) or [])
    edges = list(graph.get("edges", []) or [])

    if not nodes or not edges:
        return graph

    # Build map: node_id -> set of source_object_ids from connected edges
    node_source_ids: dict[str, set[str]] = {}
    for edge in edges:
        src = str(edge.get("source", ""))
        tgt = str(edge.get("target", ""))
        obj_ids = set(str(s) for s in (edge.get("source_object_ids") or []))
        for nid in (src, tgt):
            if nid:
                if nid not in node_source_ids:
                    node_source_ids[nid] = set()
                node_source_ids[nid].update(obj_ids)

    # Tag nodes that have >1 distinct satin object id
    for node in nodes:
        nid = str(node.get("id", ""))
        obj_ids = node_source_ids.get(nid, set())
        # Filter out empty ids
        real_ids = {oid for oid in obj_ids if oid}
        if len(real_ids) >= 2:
            node["type"] = "manual_split_boundary"

    return {"nodes": nodes, "edges": edges}


def _project_point_to_polyline(point: list[float], points: list[list[float]]) -> dict[str, Any] | None:
    if not point or not points:
        return None
    if len(points) == 1:
        return {"distance": 0.0, "offset": _point_distance(point, points[0]), "point": list(points[0])}

    best: dict[str, Any] | None = None
    walked = 0.0
    for a, b in zip(points, points[1:]):
        ax, ay = float(a[0]), float(a[1])
        bx, by = float(b[0]), float(b[1])
        vx = bx - ax
        vy = by - ay
        seg_len_sq = vx * vx + vy * vy
        seg_len = math.sqrt(seg_len_sq)
        if seg_len <= 1e-9:
            continue
        raw_t = ((float(point[0]) - ax) * vx + (float(point[1]) - ay) * vy) / seg_len_sq
        t = max(0.0, min(1.0, raw_t))
        projected = [ax + vx * t, ay + vy * t]
        offset = _point_distance(point, projected)
        along = walked + seg_len * t
        if best is None or offset < best["offset"] or (abs(offset - best["offset"]) <= 1e-9 and along < best["distance"]):
            best = {"distance": along, "offset": offset, "point": projected}
        walked += seg_len
    return best


def _point_at_distance(points: list[list[float]], target_distance: float) -> list[float]:
    if not points:
        return [0.0, 0.0]
    if target_distance <= 0:
        return list(points[0])
    walked = 0.0
    for a, b in zip(points, points[1:]):
        seg_len = _point_distance(a, b)
        if seg_len <= 1e-9:
            continue
        if walked + seg_len >= target_distance:
            t = (target_distance - walked) / seg_len
            return [float(a[0]) + (float(b[0]) - float(a[0])) * t,
                    float(a[1]) + (float(b[1]) - float(a[1])) * t]
        walked += seg_len
    return list(points[-1])


def _push_unique_point(out: list[list[float]], point: list[float]) -> None:
    if not out or _point_distance(out[-1], point) > 1e-9:
        out.append([float(point[0]), float(point[1])])


def _slice_polyline_by_distance(points: list[list[float]], start_distance: float, end_distance: float) -> list[list[float]]:
    if not points:
        return []
    total = _polyline_length(points)
    start = max(0.0, min(total, start_distance))
    end = max(0.0, min(total, end_distance))
    if end < start:
        return _slice_polyline_by_distance(points, end, start)
    if abs(end - start) <= 1e-9:
        return [_point_at_distance(points, start)]

    out: list[list[float]] = []
    _push_unique_point(out, _point_at_distance(points, start))
    walked = 0.0
    for point_a, point_b in zip(points, points[1:]):
        seg_len = _point_distance(point_a, point_b)
        vertex_distance = walked + seg_len
        if vertex_distance > start + 1e-9 and vertex_distance < end - 1e-9:
            _push_unique_point(out, point_b)
        walked = vertex_distance
    _push_unique_point(out, _point_at_distance(points, end))
    return out


def _edge_source_id_set(edge: dict[str, Any]) -> set[str]:
    return {str(value) for value in (edge.get("source_object_ids") or []) if str(value)}


def _node_connected_source_ids(node_id: str, edges: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for edge in edges:
        if edge.get("source") == node_id or edge.get("target") == node_id:
            ids.update(_edge_source_id_set(edge))
    return ids


def _segment_intersection(a0: list[float], a1: list[float],
                          b0: list[float], b1: list[float]) -> list[float] | None:
    """Return the intersection point of two line segments, or None."""
    ax, ay = a0
    bx, by = a1
    cx, cy = b0
    dx, dy = b1
    r = [bx - ax, by - ay]
    s = [dx - cx, dy - cy]
    cross = r[0] * s[1] - r[1] * s[0]
    if abs(cross) <= 1e-9:
        return None  # collinear or parallel
    ca = [cx - ax, cy - ay]
    t = (ca[0] * s[1] - ca[1] * s[0]) / cross
    u = (ca[0] * r[1] - ca[1] * r[0]) / cross
    if t < -1e-9 or t > 1 + 1e-9 or u < -1e-9 or u > 1 + 1e-9:
        return None  # outside segment bounds
    return [ax + r[0] * max(0.0, min(1.0, t)),
            ay + r[1] * max(0.0, min(1.0, t))]


def _node_edge_direction(node_id: str, edges: list[dict[str, Any]]) -> list[float] | None:
    """Get the direction vector of the edge connected to this node at the node end."""
    for edge in edges:
        points = edge.get("points") or []
        if len(points) < 2:
            continue
        if edge.get("source") == node_id:
            p0, p1 = points[0], points[1]
            return [float(p1[0]) - float(p0[0]), float(p1[1]) - float(p0[1])]
        if edge.get("target") == node_id:
            p0, p1 = points[-2], points[-1]
            return [float(p1[0]) - float(p0[0]), float(p1[1]) - float(p0[1])]
    return None


def _vector_angle_degrees(v1: list[float], v2: list[float]) -> float:
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    mag1 = math.hypot(v1[0], v1[1])
    mag2 = math.hypot(v2[0], v2[1])
    if mag1 <= 1e-9 or mag2 <= 1e-9:
        return 0.0
    cos_angle = max(-1.0, min(1.0, dot / (mag1 * mag2)))
    return math.degrees(math.acos(cos_angle))


def _segment_direction_at_distance(points: list[list[float]], distance: float) -> list[float]:
    """Return the direction of the polyline segment at a given distance along it."""
    walked = 0.0
    for a, b in zip(points, points[1:]):
        seg_len = _point_distance(a, b)
        if seg_len <= 1e-9:
            continue
        if walked + seg_len >= distance:
            return [float(b[0]) - float(a[0]), float(b[1]) - float(a[1])]
        walked += seg_len
    if len(points) >= 2:
        return [float(points[-1][0]) - float(points[-2][0]),
                float(points[-1][1]) - float(points[-2][1])]
    return [1.0, 0.0]


def _add_edge_intersection_nodes(graph: dict[str, Any], snap_tolerance: float) -> int:
    """Detect edge/edge crossings and add generated_junction nodes.

    For every pair of edges whose polylines geometrically cross without an
    existing node at the intersection, a node is created.
    Returns the number of new nodes added.
    """
    edges = graph.get("edges") or []
    nodes = graph.get("nodes") or []
    added = 0
    existing_node_ids: set[str] = set()
    for node in nodes:
        nid = node.get("id")
        if nid:
            existing_node_ids.add(str(nid))

    edge_entries: list[dict[str, Any]] = []
    for edge in edges:
        points = [list(map(float, p)) for p in (edge.get("points") or [])]
        if len(points) >= 2:
            edge_entries.append({"edge": edge, "points": points})

    for i in range(len(edge_entries)):
        for j in range(i + 1, len(edge_entries)):
            a_entry = edge_entries[i]
            b_entry = edge_entries[j]
            a_edge = a_entry["edge"]
            b_edge = b_entry["edge"]
            # Skip if the edges already share a node endpoint
            a_src = a_edge.get("source")
            a_tgt = a_edge.get("target")
            b_src = b_edge.get("source")
            b_tgt = b_edge.get("target")
            if a_src in (b_src, b_tgt) or a_tgt in (b_src, b_tgt):
                continue
            a_points = a_entry["points"]
            b_points = b_entry["points"]

            for ai in range(1, len(a_points)):
                a0 = a_points[ai - 1]
                a1 = a_points[ai]
                for bi in range(1, len(b_points)):
                    b0 = b_points[bi - 1]
                    b1 = b_points[bi]
                    hit = _segment_intersection(a0, a1, b0, b1)
                    if hit is None:
                        continue
                    # Don't create a node if one already exists at this point
                    already = False
                    for node in nodes:
                        nx = float(node.get("x", 0))
                        ny = float(node.get("y", 0))
                        if _point_distance(hit, [nx, ny]) <= snap_tolerance:
                            already = True
                            break
                    if already:
                        continue
                    # Generate a unique id
                    counter = 1
                    while f"gen_junction_{counter}" in existing_node_ids:
                        counter += 1
                    nid = f"gen_junction_{counter}"
                    existing_node_ids.add(nid)
                    new_node = {
                        "id": nid,
                        "x": round(hit[0], 6),
                        "y": round(hit[1], 6),
                        "type": "generated_junction",
                        "degree": 0,
                    }
                    nodes.append(new_node)
                    added += 1
                    break  # one intersection per edge pair is enough
                if added > 0:
                    break
    return added


def _node_allowed_to_split_edge(
    node: dict[str, Any],
    edge: dict[str, Any],
    all_edges: list[dict[str, Any]],
    projection_offset: float,
    strict_snap_tolerance: float,
    node_edge_direction: list[float] | None = None,
    target_edge_direction: list[float] | None = None,
) -> bool:
    node_type = str(node.get("type") or "")
    if node_type not in {"endpoint", "junction", "manual_split_boundary", "generated_junction", "pass_through"}:
        return False
    # Always split at junctions, boundaries, or generated intersections
    if node_type in {"junction", "manual_split_boundary", "generated_junction"}:
        return True

    # For endpoints and pass_through: check source object overlap first
    target_ids = _edge_source_id_set(edge)
    node_ids = _node_connected_source_ids(str(node.get("id", "")), all_edges)
    if target_ids and node_ids and target_ids.intersection(node_ids):
        return True

    # Use angle-based geometric rejection for different-object connections.
    # If the node's own edge approaches the target edge at >30°, it's a
    # genuine T-junction — allow at full snap tolerance.
    # If the angle is small, the paths run parallel — only allow at strict tolerance.
    if node_edge_direction is not None and target_edge_direction is not None:
        angle = _vector_angle_degrees(node_edge_direction, target_edge_direction)
        if angle > 30.0:
            return True  # genuine T-junction

    # Parallel/nearby: only allow if very close
    return projection_offset <= strict_snap_tolerance


def _renumber_edge_id(base_id: str, index: int, total: int) -> str:
    if total <= 1:
        return base_id
    return f"{base_id}_n{index}"


def _split_edge_at_nodes(
    edge: dict[str, Any],
    candidates: list[dict[str, Any]],
    all_edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
    snap_tolerance: float,
    strict_snap_tolerance: float,
    endpoint_margin: float,
    split_edges: list[dict[str, Any]],
) -> None:
    """Project candidate nodes onto an edge's polyline and split if found.

    Appends to split_edges in-place. If no interior split is found the
    original edge is appended unchanged.
    """
    points = [list(map(float, point)) for point in (edge.get("points") or [])]
    if len(points) < 2:
        return
    edge_length = _polyline_length(points)
    boundaries: list[dict[str, Any]] = [
        {"node_id": str(edge.get("source")), "distance": 0.0},
        {"node_id": str(edge.get("target")), "distance": edge_length},
    ]

    for node in candidates:
        node_id = str(node.get("id", ""))
        if not node_id or node_id == edge.get("source") or node_id == edge.get("target"):
            continue
        node_point = [float(node.get("x", 0.0)), float(node.get("y", 0.0))]
        projection = _project_point_to_polyline(node_point, points)
        if projection is None:
            continue
        if float(projection["offset"]) > float(snap_tolerance):
            continue
        distance_along = float(projection["distance"])
        near_start = distance_along <= endpoint_margin
        near_end = edge_length - distance_along <= endpoint_margin
        if near_start or near_end:
            # Reject only if the node is at the SAME physical location as
            # the edge's source or target that sits at this polyline endpoint.
            # If the node is a DIFFERENT one that happens to project near
            # the split boundary, allow it (genuine topology connection).
            src_node = node_by_id.get(str(edge.get("source")))
            tgt_node = node_by_id.get(str(edge.get("target")))
            src_xy = [float(src_node["x"]), float(src_node["y"])] if src_node else None
            tgt_xy = [float(tgt_node["x"]), float(tgt_node["y"])] if tgt_node else None
            node_xy = [float(node.get("x", 0.0)), float(node.get("y", 0.0))]
            matches_source = src_xy is not None and _point_distance(node_xy, src_xy) <= endpoint_margin
            matches_target = tgt_xy is not None and _point_distance(node_xy, tgt_xy) <= endpoint_margin
            if matches_source or matches_target:
                continue  # same physical node as source/target — skip duplicate
        node_dir = _node_edge_direction(node_id, all_edges)
        target_dir = _segment_direction_at_distance(points, distance_along)
        if not _node_allowed_to_split_edge(
            node, edge, all_edges, float(projection["offset"]),
            strict_snap_tolerance, node_dir, target_dir,
        ):
            continue
        boundaries.append({"node_id": node_id, "distance": distance_along})

    boundaries.sort(key=lambda item: float(item["distance"]))
    deduped: list[dict[str, Any]] = []
    for boundary in boundaries:
        previous = deduped[-1] if deduped else None
        if previous and abs(float(previous["distance"]) - float(boundary["distance"])) <= 1e-6:
            if boundary["node_id"] not in {edge.get("source"), edge.get("target")}:
                previous["node_id"] = boundary["node_id"]
            continue
        deduped.append(boundary)

    if len(deduped) <= 2:
        preserved = dict(edge)
        preserved["points"] = points
        preserved["length"] = _polyline_length(points)
        split_edges.append(preserved)
        return

    source_edge_id = str(edge.get("source_edge_id") or edge.get("id"))
    child_count = len(deduped) - 1
    for idx in range(1, len(deduped)):
        start = deduped[idx - 1]
        end = deduped[idx]
        if float(end["distance"]) - float(start["distance"]) <= 1e-9:
            continue
        child_points = _slice_polyline_by_distance(points, float(start["distance"]), float(end["distance"]))
        if len(child_points) < 2:
            continue
        child = dict(edge)
        child["id"] = _renumber_edge_id(str(edge.get("id")), idx, child_count)
        child["source_edge_id"] = source_edge_id
        child["source"] = str(start["node_id"])
        child["target"] = str(end["node_id"])
        child["points"] = child_points
        child["length"] = _polyline_length(child_points)
        child["source_object_ids"] = list(edge.get("source_object_ids") or [])
        split_edges.append(child)


def normalize_graph_topology(graph: dict[str, Any], snap_tolerance: float = 12.0) -> dict[str, Any]:
    """Split graph edges at nearby graph nodes so visible nodes become hard boundaries.

    This is topology normalization: if a node lies near the interior of another
    edge, the edge is sliced into node-to-node child spans before the frontend
    builds selectable roadSegments. It keeps the snap tolerance separate from the
    smaller frontend atomic projection tolerance, and applies safety checks so
    close parallel paths do not create false junctions just because they run near
    each other.
    """
    nodes = [dict(node) for node in (graph.get("nodes", []) or [])]
    edges = [dict(edge) for edge in (graph.get("edges", []) or [])]
    if not nodes or not edges:
        return {"nodes": nodes, "edges": edges}

    # Phase C.9a: detect edge/edge crossings and create generated_junction nodes
    _add_edge_intersection_nodes(graph, snap_tolerance)
    nodes = list(graph.get("nodes", []) or [])
    edges = list(graph.get("edges", []) or [])

    node_by_id = {str(node.get("id")): node for node in nodes if node.get("id") is not None}
    original_node_types: dict[str, str] = {
        str(node.get("id")): str(node.get("type") or "")
        for node in nodes if node.get("id")
    }
    strict_snap_tolerance = min(2.0, float(snap_tolerance) / 4.0)
    endpoint_margin = max(1.0, min(4.0, float(snap_tolerance) / 2.0))
    split_edges: list[dict[str, Any]] = []

    for edge in edges:
        _split_edge_at_nodes(edge, nodes, edges, node_by_id, snap_tolerance,
                             strict_snap_tolerance, endpoint_margin, split_edges)

    # Compute degrees and identify promoted junction nodes
    degrees: dict[str, int] = {node_id: 0 for node_id in node_by_id}
    for edge in split_edges:
        for node_id in (str(edge.get("source", "")), str(edge.get("target", ""))):
            if node_id:
                degrees[node_id] = degrees.get(node_id, 0) + 1

    promoted_junction_ids: set[str] = set()
    for node in nodes:
        nid = str(node.get("id", ""))
        orig_type = original_node_types.get(nid, "")
        new_degree = degrees.get(nid, 0)
        if new_degree >= 3 and orig_type in {"endpoint", "pass_through"}:
            promoted_junction_ids.add(nid)

    # SECOND PASS: promoted junctions in newly computed degrees re-check
    # unsplit edges at double tolerance. These are genuine intersections that
    # barely missed the first pass.
    if promoted_junction_ids:
        junction_tolerance = float(snap_tolerance) * 2.0
        for edge in edges:
            eid = str(edge.get("id"))
            was_split = any(
                e.get("id") != eid and (str(e.get("source_edge_id") or e.get("id")) == eid)
                for e in split_edges
            )
            if was_split:
                continue
            # Build the promoted-junction-only node subset for this pass
            promo_nodes = [n for n in nodes if n.get("id") in promoted_junction_ids]
            _split_edge_at_nodes(edge, promo_nodes, edges, node_by_id,
                                 junction_tolerance, strict_snap_tolerance,
                                 endpoint_margin, split_edges)

    normalized_nodes: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(node.get("id", ""))
        degree = degrees.get(node_id, 0)
        original_type = str(node.get("type") or "")
        normalized = dict(node)
        normalized["degree"] = degree
        if original_type in {"manual_split_boundary", "generated_junction"}:
            normalized["type"] = original_type
        elif degree >= 3:
            normalized["type"] = "junction"
        elif degree == 1:
            normalized["type"] = "endpoint"
        else:
            normalized["type"] = "pass_through"
        normalized_nodes.append(normalized)

    return {"nodes": normalized_nodes, "edges": split_edges}


def build_road_graph_overlay_svg(
    svg_w: float,
    svg_h: float,
    satin_objects: list[dict[str, Any]],
    graph: dict[str, Any],
) -> str:
    """Build a debug SVG showing faint Satin paths, centerline edges, and graph nodes."""
    width = _format_number(svg_w)
    height = _format_number(svg_h)
    elements: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'  <rect x="0" y="0" width="{width}" height="{height}" fill="#10131c"/>',
    ]

    for obj in satin_objects or []:
        d = obj.get("d")
        if not d:
            continue
        tx = float(obj.get("tx") or 0.0)
        ty = float(obj.get("ty") or 0.0)
        transform = f' transform="translate({_format_number(tx)} {_format_number(ty)})"' if (tx or ty) else ""
        obj_id = escape(str(obj.get("id", "")))
        elements.append(
            f'  <path data-satin-id="{obj_id}" d="{escape(str(d))}" fill="#000000" fill-opacity="0.15" stroke="#ffffff" stroke-opacity="0.18" stroke-width="0.5"{transform}/>'
        )

    for edge in graph.get("edges", []) or []:
        points = edge.get("points") or []
        if len(points) < 2:
            continue
        point_text = " ".join(f"{_format_number(p[0])},{_format_number(p[1])}" for p in points)
        edge_id = escape(str(edge.get("id", "")))
        elements.append(
            f'  <polyline data-edge-id="{edge_id}" points="{point_text}" fill="none" stroke="#00d5ff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
        )

    for node in graph.get("nodes", []) or []:
        node_id = escape(str(node.get("id", "")))
        node_type = str(node.get("type", ""))
        if node_type == "manual_split_boundary":
            fill = "#ff00ff"  # magenta for split boundaries
            radius = "3.6"
        elif node_type == "junction":
            fill = "#ff7a00"
            radius = "3.2"
        elif node_type == "endpoint":
            fill = "#30e37a"
            radius = "2.2"
        else:
            fill = "#d9d55a"
            radius = "2.2"
        elements.append(
            f'  <circle data-node-id="{node_id}" cx="{_format_number(node.get("x"))}" cy="{_format_number(node.get("y"))}" r="{radius}" fill="{fill}" stroke="#ffffff" stroke-width="0.8"/>'
        )

    elements.append("</svg>")
    return "\n".join(elements)
