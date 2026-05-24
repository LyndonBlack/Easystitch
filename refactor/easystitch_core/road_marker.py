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
