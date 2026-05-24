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


def _build_satin_only_svg(
    satin_objects: list[dict[str, Any]],
    svg_w: float,
    svg_h: float,
) -> str:
    """Build a temporary SVG containing only black-filled Satin paths."""
    width = _format_number(svg_w)
    height = _format_number(svg_h)

    path_elements: list[str] = []
    for obj in satin_objects:
        d = obj.get("d")
        if not d:
            continue

        tx = float(obj.get("tx") or 0.0)
        ty = float(obj.get("ty") or 0.0)
        transform = ""
        if tx or ty:
            transform = f' transform="translate({_format_number(tx)} {_format_number(ty)})"'

        path_elements.append(
            f'<path d="{escape(str(d))}" fill="#000000" stroke="none"{transform}/>'
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
    """
    Render only Satin-assigned SVG paths to a black/white PNG mask image.

    Output dict:
    {
        "image": PIL.Image,
        "width_px": int,
        "height_px": int,
        "scale": float,
        "satin_object_ids": list[str],
        "excluded_object_ids": list[str],
    }

    The renderer uses a temporary SVG containing only Satin paths, rendered by
    CairoSVG at the requested scale. The returned image is L-mode; call
    clean_binary_mask() to threshold it to pure black/white.
    """
    if scale <= 0:
        raise ValueError("scale must be greater than zero")
    if svg_w <= 0 or svg_h <= 0:
        raise ValueError("svg_w and svg_h must be greater than zero")

    satin_objects = collect_satin_objects(objects, assignments)
    satin_ids = [_object_id(obj) for obj in satin_objects if _object_id(obj) is not None]
    satin_id_set = set(satin_ids)

    all_ids = [_object_id(obj) for obj in (objects or [])]
    excluded_ids = [obj_id for obj_id in all_ids if obj_id is not None and obj_id not in satin_id_set]

    width_px = int(round(float(svg_w) * scale))
    height_px = int(round(float(svg_h) * scale))
    svg_text = _build_satin_only_svg(satin_objects, svg_w, svg_h)

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


def clean_centerline_polylines(
    polylines: list[dict[str, Any]],
    min_length_px: float = 5.0,
    simplify_tolerance: float = 1.0,
) -> list[dict[str, Any]]:
    """Remove tiny centerline paths and simplify jitter while preserving endpoints."""
    cleaned: list[dict[str, Any]] = []
    for polyline in polylines or []:
        points = [list(map(float, point)) for point in polyline.get("points", [])]
        if len(points) < 2:
            continue
        length = float(polyline.get("length") or _polyline_length(points))
        if length < min_length_px:
            continue
        simplified = _douglas_peucker(points, simplify_tolerance)
        made = _make_polyline(str(polyline.get("id") or f"cline_{len(cleaned) + 1}"), simplified)
        if made is not None:
            cleaned.append(made)
    return cleaned


def build_centerline_graph(polylines: list[dict[str, Any]], snap_distance: float = 3.0) -> dict[str, Any]:
    """Build a simple endpoint-snapped graph from centerline polylines."""
    node_points: list[list[float]] = []
    edge_specs: list[tuple[str, int, int, list[list[float]], float]] = []

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
        edge_specs.append((f"edge_{edge_index}", source_idx, target_idx, points, _polyline_length(points)))

    degrees = [0 for _ in node_points]
    for _, source_idx, target_idx, _, _ in edge_specs:
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
    for edge_id, source_idx, target_idx, points, length in edge_specs:
        edges.append({
            "id": edge_id,
            "source": f"node_{source_idx + 1}",
            "target": f"node_{target_idx + 1}",
            "points": points,
            "length": length,
            "source_object_ids": [],
            "priority": None,
            "assignment": "unmarked",
        })

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
        fill = "#ff7a00" if node_type == "junction" else ("#30e37a" if node_type == "endpoint" else "#d9d55a")
        radius = "3.2" if node_type == "junction" else "2.2"
        elements.append(
            f'  <circle data-node-id="{node_id}" cx="{_format_number(node.get("x"))}" cy="{_format_number(node.get("y"))}" r="{radius}" fill="{fill}" stroke="#ffffff" stroke-width="0.8"/>'
        )

    elements.append("</svg>")
    return "\n".join(elements)
