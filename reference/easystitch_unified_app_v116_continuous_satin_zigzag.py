#!/usr/bin/env python3
"""
EasyStitch Unified App — Phase 19.0a
================================

A single local web app that will eventually hold the full EasyStitch workflow:

  Pane 1 — Image Prep        [working]
  Pane 2 — Trace             [placeholder]
  Pane 3 — Path Structure    [placeholder]
  Pane 4 — Stitch & Export   [placeholder]

Current working scope:
  - Load image from command line or upload in browser
  - Apply EXIF rotation
  - Composite alpha/palette images onto white
  - Resize to max dimension
  - Quantize to N colours using KMeans in pure-numpy Lab colourspace
  - Show original/prepared previews
  - Show final colour palette
  - Save <name>_prepared.png for the later trace pane

Dependencies:
    pip install flask pillow numpy scikit-learn

Usage:
    python easystitch_unified_app.py image.png
    python easystitch_unified_app.py --port 5001
"""

import argparse
import base64
import io
import json
import os
import re
import sys
import time
import subprocess
import shutil
import math
import xml.etree.ElementTree as ET
from collections import deque
import webbrowser
from pathlib import Path

import numpy as np
from flask import Flask, jsonify, request
from PIL import Image, ImageOps, ImageFilter, ImageEnhance
from sklearn.cluster import MiniBatchKMeans

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


class NeedSecondCutError(RuntimeError):
    pass

# ─────────────────────────────────────────────────────────────────────────────
# Pure-numpy Lab colour conversion
# ─────────────────────────────────────────────────────────────────────────────

def rgb2lab(img_float: np.ndarray) -> np.ndarray:
    """
    Convert float32 RGB image [0,1] of shape (H,W,3) to CIE L*a*b*.
    Uses D65 illuminant. Pure numpy — no scikit-image dependency.
    """
    h, w = img_float.shape[:2]
    flat = img_float.reshape(-1, 3).astype(np.float64)

    linear = np.where(
        flat > 0.04045,
        ((flat + 0.055) / 1.055) ** 2.4,
        flat / 12.92
    )

    m = np.array([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041]
    ])
    xyz = linear @ m.T
    xyz /= [0.95047, 1.00000, 1.08883]

    eps = 0.008856
    kap = 903.3
    f = np.where(
        xyz > eps,
        np.cbrt(np.clip(xyz, 0, None)),
        (kap * xyz + 16.0) / 116.0
    )

    l_val = (116.0 * f[:, 1] - 16.0).reshape(h, w)
    a_val = (500.0 * (f[:, 0] - f[:, 1])).reshape(h, w)
    b_val = (200.0 * (f[:, 1] - f[:, 2])).reshape(h, w)
    return np.stack([l_val, a_val, b_val], axis=2).astype(np.float32)


def lab2rgb(lab: np.ndarray) -> np.ndarray:
    """
    Convert L*a*b* image of shape (H,W,3) to float32 RGB [0,1].
    Pure numpy — no scikit-image dependency.
    """
    flat = lab.reshape(-1, 3).astype(np.float64)

    fy = (flat[:, 0] + 16.0) / 116.0
    fx = flat[:, 1] / 500.0 + fy
    fz = fy - flat[:, 2] / 200.0

    eps = 0.008856
    xyz = np.stack([
        np.where(fx ** 3 > eps, fx ** 3, (fx - 16.0 / 116.0) / 7.787),
        np.where(fy ** 3 > eps, fy ** 3, (fy - 16.0 / 116.0) / 7.787),
        np.where(fz ** 3 > eps, fz ** 3, (fz - 16.0 / 116.0) / 7.787),
    ], axis=1)

    xyz *= [0.95047, 1.00000, 1.08883]

    m_inv = np.array([
        [ 3.2404542, -1.5371385, -0.4985314],
        [-0.9692660,  1.8760108,  0.0415560],
        [ 0.0556434, -0.2040259,  1.0572252]
    ])
    lin = np.clip(xyz @ m_inv.T, 0.0, 1.0)

    rgb = np.where(
        lin <= 0.0031308,
        lin * 12.92,
        1.055 * lin ** (1.0 / 2.4) - 0.055
    )
    h, w = lab.shape[:2]
    return np.clip(rgb, 0.0, 1.0).reshape(h, w, 3).astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# Image prep
# ─────────────────────────────────────────────────────────────────────────────

def safe_stem(name: str) -> str:
    stem = Path(name).stem or "image"
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)
    return stem.strip("._") or "image"


def normalise_image(img: Image.Image, max_dimension: int) -> tuple[Image.Image, dict]:
    """
    Apply EXIF rotation, composite transparency/palette to white, convert to RGB,
    and resize so longest side <= max_dimension.
    """
    info = {
        "original_size": img.size,
        "original_mode": img.mode,
        "resized": False,
        "processed_size": img.size,
    }

    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode == "P":
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    if max(w, h) > max_dimension:
        scale = max_dimension / max(w, h)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        info["resized"] = True
        info["processed_size"] = img.size
    else:
        info["processed_size"] = img.size

    return img, info


def apply_simplify_filter(img: Image.Image, preset: str = "none",
                          smoothing: int = 0,
                          posterize_bits: int = 0,
                          color_boost: float = 1.0,
                          contrast_boost: float = 1.0) -> Image.Image:
    """
    Apply lightweight pre-quantization simplification filters.

    These are intentionally basic and dependency-light. They are not intended
    to replace tracing controls later, but they help photo/stylized images
    become more embroidery-friendly before palette reduction.
    """
    img = img.convert("RGB")
    preset = (preset or "none").lower()

    # Presets set sane defaults, then explicit sliders can add more.
    if preset == "soft":
        img = img.filter(ImageFilter.MedianFilter(size=3))
        img = img.filter(ImageFilter.SMOOTH_MORE)
    elif preset == "cartoon":
        img = img.filter(ImageFilter.MedianFilter(size=3))
        img = img.filter(ImageFilter.ModeFilter(size=3))
        img = ImageEnhance.Color(img).enhance(1.18)
        img = ImageEnhance.Contrast(img).enhance(1.08)
    elif preset == "strong":
        img = img.filter(ImageFilter.MedianFilter(size=5))
        img = img.filter(ImageFilter.ModeFilter(size=5))
        img = img.filter(ImageFilter.SMOOTH_MORE)
        img = ImageEnhance.Color(img).enhance(1.25)
        img = ImageEnhance.Contrast(img).enhance(1.12)

    # Manual smoothing pass. Values above 3 tend to over-blur small artwork.
    smoothing = max(0, min(5, int(smoothing)))
    for _ in range(smoothing):
        img = img.filter(ImageFilter.SMOOTH_MORE)

    # Optional posterize before KMeans. Lower bits = more aggressive flattening.
    posterize_bits = int(posterize_bits or 0)
    if 1 <= posterize_bits <= 7:
        img = ImageOps.posterize(img, posterize_bits)

    if abs(float(color_boost) - 1.0) > 0.001:
        img = ImageEnhance.Color(img).enhance(float(color_boost))

    if abs(float(contrast_boost) - 1.0) > 0.001:
        img = ImageEnhance.Contrast(img).enhance(float(contrast_boost))

    return img

def quantize_image(img: Image.Image, n_colors: int) -> tuple[Image.Image, list[dict]]:
    """
    Flatten image to n_colors using MiniBatchKMeans in Lab colourspace.
    Returns (quantized PIL image, palette list).
    """
    arr = np.array(img, dtype=np.float32) / 255.0
    h, w = arr.shape[:2]
    lab = rgb2lab(arr)
    flat = lab.reshape(-1, 3)

    n_colors = max(2, min(64, int(n_colors)))
    km = MiniBatchKMeans(
        n_clusters=n_colors,
        n_init=3,
        batch_size=min(8192, w * h),
        random_state=42,
        max_iter=300,
    )

    labels = km.fit_predict(flat)
    centres_lab = km.cluster_centers_

    quant_lab = centres_lab[labels].reshape(h, w, 3).astype(np.float32)
    quant_rgb = np.clip(lab2rgb(quant_lab), 0.0, 1.0)
    quant_u8 = (quant_rgb * 255).astype(np.uint8)

    palette = []
    counts = np.bincount(labels, minlength=n_colors)
    for idx, c in enumerate(centres_lab):
        c_rgb = lab2rgb(np.array([[[c[0], c[1], c[2]]]], dtype=np.float32))
        r, g, b = np.clip(c_rgb[0, 0] * 255, 0, 255).astype(int)
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        palette.append({
            "hex": f"#{int(r):02X}{int(g):02X}{int(b):02X}",
            "r": int(r),
            "g": int(g),
            "b": int(b),
            "pixels": int(counts[idx]),
            "percent": float(counts[idx]) / float(w * h) * 100.0,
            "luminance": float(lum),
        })

    palette.sort(key=lambda p: p["luminance"])
    return Image.fromarray(quant_u8, mode="RGB"), palette


def image_to_data_uri(img: Image.Image, max_preview: int = 900) -> str:
    """
    Convert PIL image to PNG data URI for browser preview.
    """
    preview = img.copy()
    w, h = preview.size
    if max(w, h) > max_preview:
        scale = max_preview / max(w, h)
        preview = preview.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.LANCZOS
        )

    buf = io.BytesIO()
    preview.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def load_image_from_path(path: str) -> Image.Image:
    with Image.open(path) as img:
        return img.copy()


def run_image_prep(input_path: str, output_dir: str, max_size: int, colors: int,
                   simplify_preset: str = 'none', smoothing: int = 0,
                   posterize_bits: int = 0, color_boost: float = 1.0,
                   contrast_boost: float = 1.0) -> dict:
    t0 = time.time()
    img = load_image_from_path(input_path)
    original_preview_img, _ = normalise_image(img.copy(), max_dimension=max_size)
    normalised, info = normalise_image(img, max_dimension=max_size)

    simplified = apply_simplify_filter(
        normalised,
        preset=simplify_preset,
        smoothing=smoothing,
        posterize_bits=posterize_bits,
        color_boost=color_boost,
        contrast_boost=contrast_boost,
    )

    quantized, palette = quantize_image(simplified, colors)

    stem = safe_stem(Path(input_path).name)
    out_path = Path(output_dir) / f"{stem}_prepared.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    quantized.save(out_path, format="PNG", optimize=False)

    return {
        "ok": True,
        "input_path": str(Path(input_path).resolve()),
        "output_path": str(out_path.resolve()),
        "stem": stem,
        "original_mode": info["original_mode"],
        "original_width": info["original_size"][0],
        "original_height": info["original_size"][1],
        "processed_width": normalised.width,
        "processed_height": normalised.height,
        "resized": info["resized"],
        "colors_requested": colors,
        "simplify_preset": simplify_preset,
        "smoothing": smoothing,
        "posterize_bits": posterize_bits,
        "color_boost": color_boost,
        "contrast_boost": contrast_boost,
        "palette": palette,
        "time_sec": round(time.time() - t0, 3),
        "original_preview": image_to_data_uri(original_preview_img),
        "prepared_preview": image_to_data_uri(quantized),
    }



# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: SVG tracing via standalone vtracer CLI
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

def _neighbors8(y: int, x: int, h: int, w: int):
    for ny in range(max(0, y - 1), min(h, y + 2)):
        for nx in range(max(0, x - 1), min(w, x + 2)):
            if ny == y and nx == x:
                continue
            yield ny, nx


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


def _polyline_length(points: list) -> float:
    total = 0.0
    for i in range(1, len(points)):
        total += math.hypot(points[i][0] - points[i-1][0], points[i][1] - points[i-1][1])
    return total


def _simplify_points(points: list, step: int = 2) -> list:
    if len(points) <= 4:
        return points
    out = [points[0]]
    for i in range(step, len(points) - 1, step):
        out.append(points[i])
    out.append(points[-1])
    return out


def _points_to_svg_path(points: list) -> str:
    if len(points) < 2:
        return ""
    parts = [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]
    for x, y in points[1:]:
        parts.append(f"L {x:.2f} {y:.2f}")
    return " ".join(parts)


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


# ─────────────────────────────────────────────────────────────────────────────
# Pane 3 manual splitting helpers
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
# Pane 4 stitch preview / underlay helpers
# ─────────────────────────────────────────────────────────────────────────────

def mm_to_px(mm: float, dpi: float = 96.0) -> float:
    return float(mm) * float(dpi) / 25.4


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


def _rotate_xy(x: float, y: float, angle_rad: float):
    ca, sa = math.cos(angle_rad), math.sin(angle_rad)
    return x * ca - y * sa, x * sa + y * ca


def _rotate_geom(geom, angle_rad: float):
    from shapely.ops import transform
    return transform(lambda x, y, z=None: _rotate_xy(x, y, angle_rad), geom)


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
    from shapely.ops import transform
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


def _arc_coords_between(coords: list, start_idx: int, end_idx: int) -> list:
    """
    Return circular coordinate arc from start_idx to end_idx inclusive.
    """
    if not coords:
        return []
    n = len(coords)
    out = []
    i = start_idx % n
    while True:
        out.append(coords[i])
        if i == end_idx % n:
            break
        i = (i + 1) % n
        if len(out) > n + 1:
            break
    return out




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


def _svg_debug_polyline(points, color: str, width: float = 2.2,
                        opacity: float = 0.95, dash: str = "") -> str:
    if not points or len(points) < 2:
        return ""
    pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<polyline points="{pts}" fill="none" stroke="{color}" '
        f'stroke-width="{width:.2f}" stroke-opacity="{opacity:.3f}" '
        f'stroke-linecap="round" stroke-linejoin="round" '
        f'vector-effect="non-scaling-stroke"{dash_attr}/>'
    )


def _svg_debug_dot(x: float, y: float, color: str, r: float = 3.0) -> str:
    return (
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="{color}" '
        f'stroke="#111" stroke-width="0.8" vector-effect="non-scaling-stroke"/>'
    )


def _svg_debug_text(x: float, y: float, label: str, color: str = "#00ff66") -> str:
    safe = str(label).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" fill="{color}" font-size="10" '
        f'font-family="monospace" stroke="#111" stroke-width="0.35" '
        f'paint-order="stroke" vector-effect="non-scaling-stroke">{safe}</text>'
    )


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


def _svg_polyline(points, color: str, width: float, opacity: float, dash: str = "") -> str:
    if not points or len(points) < 2:
        return ""
    pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<polyline points="{pts}" fill="none" stroke="{color}" '
        f'stroke-width="{width:.2f}" stroke-opacity="{opacity:.3f}" '
        f'stroke-linecap="round" stroke-linejoin="round"{dash_attr}/>'
    )




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
    Convert rail-to-rail satin bars into a true continuous satin zigzag path.

    Each generated bar represents the ideal rail-to-rail stitch direction at a
    sample station.  The machine should not stitch a short step along one rail
    and then stitch across the same station.  Instead it should travel from the
    previous rail endpoint directly to the opposite rail endpoint of the next
    station, creating the classic tight zigzag/ladder.
    """
    bars = [list(bar) for bar in (ordered_bars or []) if bar and len(bar) >= 2]
    if not bars:
        return []
    path = [bars[0][0], bars[0][-1]]
    for bar in bars[1:]:
        path.append(bar[-1])
    # Remove exact duplicate consecutive points which can occur at endpoints.
    clean = []
    for p in path:
        if not clean or math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 1e-6:
            clean.append(p)
    return clean if len(clean) >= 2 else []


def _object_sort_key(obj: dict):
    return (str(obj.get("color", "#000000")).lower(), float(obj.get("order", 0)))



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




def _hex_color_to_rgb(color: str) -> tuple[int, int, int]:
    try:
        c = str(color or "#000000").strip()
        if c.startswith("#"):
            c = c[1:]
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) >= 6:
            return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except Exception:
        pass
    return 0, 0, 0


def color_luminance(color: str) -> float:
    r, g, b = _hex_color_to_rgb(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


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
            target_points = _satin_entry_candidates(top_lines)
            for line in _order_underlay_to_finish_near(satin_underlay_lines, current_pos, target_points):
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

        if enable_underlay:
            edge_lines = generate_edge_walk_preview(geom, underlay_inset_px, stitch_len_px)
            for line in edge_lines:
                chunks.append(_svg_polyline(line, color, 0.75, 0.55, "3 2"))
                add_preview_line("underlay", line, color, 0.85, 0.65, "3 2")
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

            ordered_satin_preview_bars = _order_satin_bars_zigzag(satin_lines, None)
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


# ─────────────────────────────────────────────────────────────────────────────
# Web UI
# ─────────────────────────────────────────────────────────────────────────────

HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EasyStitch Unified App</title>
<style>
*{box-sizing:border-box}
html,body{height:100%;margin:0;font-family:Inter,Segoe UI,Arial,sans-serif;background:#101322;color:#e7eaf5}
body{display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:14px;padding:12px 18px;background:#16213e;border-bottom:1px solid #0f3460}
h1{font-size:1rem;margin:0;color:#e94560;letter-spacing:1px}
.sub{font-size:.78rem;color:#8f96b3}
.status{margin-left:auto;font-size:.76rem;color:#b8bfd6}
.app{flex:1;display:flex;min-height:0}
.nav{width:240px;background:#16213e;border-right:1px solid #0f3460;display:flex;flex-direction:column}
.step{padding:14px 16px;border-bottom:1px solid #0f3460;cursor:pointer;display:flex;gap:10px;align-items:flex-start}
.step:hover{background:#14264a}
.step.active{background:#0f3460;border-left:4px solid #e94560;padding-left:12px}
.step.disabled{opacity:.42;cursor:not-allowed}
.num{width:24px;height:24px;border-radius:999px;background:#25395d;color:#dbe3fb;font-size:.78rem;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-weight:700}
.step.active .num{background:#e94560;color:#fff}
.stitle{font-size:.84rem;font-weight:700}
.sdesc{font-size:.7rem;color:#8f96b3;margin-top:2px;line-height:1.35}
.content{flex:1;display:flex;flex-direction:column;min-width:0}
.panel{display:none;flex:1;min-height:0}
.panel.active{display:flex}
.left{width:340px;background:#121a30;border-right:1px solid #0f3460;padding:16px;overflow:auto}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.card{background:#16213e;border:1px solid #0f3460;border-radius:10px;padding:14px;margin-bottom:14px}
.card h2{font-size:.95rem;margin:0 0 10px;color:#f1f3fa}
.card p{font-size:.78rem;color:#9da7c4;line-height:1.45;margin:0 0 10px}
.formrow{display:flex;flex-direction:column;gap:6px;margin-bottom:12px}
label{font-size:.75rem;color:#b8bfd6;font-weight:700}
input[type=file],input[type=number],input[type=text]{background:#0f1628;color:#e7eaf5;border:1px solid #30476d;border-radius:6px;padding:8px}
input[type=range]{accent-color:#e94560}
.range-row{display:flex;align-items:center;gap:10px}
.range-row input{flex:1}
.val{font-size:.78rem;color:#fff;width:54px;text-align:right}
.btn{padding:9px 13px;border:1px solid #30476d;background:#111a31;color:#dbe3fb;border-radius:7px;cursor:pointer;font-size:.8rem;font-weight:700}
.btn:hover{background:#1a2748}
.btn.primary{background:#e94560;border-color:#e94560;color:white}
.btn.primary:hover{background:#c93652}
.btn:disabled{opacity:.5;cursor:not-allowed}
.hover-tooltip{position:fixed;z-index:9999;max-width:340px;background:#0b1020;color:#e7eaf5;border:1px solid #e94560;border-radius:8px;padding:9px 11px;font-size:.76rem;line-height:1.35;box-shadow:0 10px 35px rgba(0,0,0,.45);pointer-events:none;opacity:0;transform:translateY(4px);transition:opacity .12s ease,transform .12s ease}
.hover-tooltip.show{opacity:1;transform:translateY(0)}
.tool-card .btn{display:block;width:100%;box-sizing:border-box;text-align:left}
.preview-area{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px;overflow:auto;background:#12122a}
.preview-box{background:#e8e4df;background-image:repeating-conic-gradient(#fff 0% 25%,#ccc 0% 50%);background-size:16px 16px;border-radius:8px;min-height:260px;display:flex;flex-direction:column;overflow:hidden;border:1px solid rgba(255,255,255,.08)}
.preview-title{background:rgba(15,22,40,.92);color:#dbe3fb;padding:8px 10px;font-size:.75rem;font-weight:700;display:flex;justify-content:space-between}
.preview-img-wrap{flex:1;display:flex;align-items:center;justify-content:center;padding:12px;position:relative;overflow:auto}
.hoop-ruler line{stroke:#f6c177;stroke-opacity:.72;vector-effect:non-scaling-stroke}
.hoop-ruler text{fill:#f6c177;font-family:system-ui,sans-serif;font-weight:700;paint-order:stroke;stroke:#0b1020;stroke-width:3px;stroke-linejoin:round}
.hoop-ruler .ruler-base{stroke:#f6c177;stroke-opacity:.9;stroke-width:1.25;vector-effect:non-scaling-stroke}
.hoop-frame{fill:none;stroke:#7dd3fc;stroke-width:1.6;stroke-dasharray:8 5;stroke-opacity:.9;vector-effect:non-scaling-stroke}
.hoop-frame-bg{fill:rgba(125,211,252,.035);stroke:none}
.preview-img-wrap img{max-width:100%;max-height:70vh;object-fit:contain;transform-origin:center center;transition:transform .08s ease-out}
.palette{display:flex;flex-direction:column;gap:7px}
.swatch-row{display:grid;grid-template-columns:26px 82px 1fr 48px;align-items:center;gap:8px;font-size:.74rem;color:#cbd3e9}
.swatch{width:24px;height:24px;border-radius:4px;border:1px solid rgba(255,255,255,.25)}
.bar{height:8px;background:#0f1628;border-radius:999px;overflow:hidden}
.bar div{height:100%;background:#e94560}
.meta{font-size:.75rem;color:#9da7c4;line-height:1.5}
.meta b{color:#e7eaf5}
.placeholder{flex:1;display:flex;align-items:center;justify-content:center;text-align:center;padding:30px;color:#8f96b3;background:#12122a}
.placeholder .box{max-width:520px;background:#16213e;border:1px solid #0f3460;border-radius:12px;padding:24px}
.footer{padding:9px 16px;background:#0f1628;border-top:1px solid #0f3460;font-size:.74rem;color:#8f96b3;display:flex;gap:16px;align-items:center}
.obj-row{display:flex;align-items:flex-start;gap:8px;padding:9px 12px;border-bottom:1px solid #0f3460;cursor:pointer}
.obj-row:hover{background:#133057}
.obj-row.sel{background:#0f3460;border-left:3px solid #e94560;padding-left:9px}
.obj-swatch{width:16px;height:16px;border-radius:3px;border:1px solid rgba(255,255,255,.2);flex-shrink:0}
.obj-info{flex:1;min-width:0}
.obj-name{font-size:.82rem;font-weight:600}
.obj-meta{font-size:.68rem;color:#8f96b3;margin-top:2px;line-height:1.35}
.obj-badge{font-size:.64rem;padding:2px 6px;border-radius:10px;font-weight:700;white-space:nowrap;background:#233c63;color:#9ac2ff}
.obj-parent{background:#182845;border-bottom:1px solid #0f3460;font-weight:700}
.obj-parent.sel{background:#0f3460;border-left:3px solid #e94560;padding-left:9px}
.obj-child{padding-left:34px;background:#101a30}
.obj-child .obj-name::before{content:'↳ ';color:#8f96b3}
.obj-group-count{font-size:.64rem;padding:2px 6px;border-radius:10px;background:#2f456b;color:#dbe8ff;white-space:nowrap}
.collapse-toggle{width:22px;height:22px;border-radius:6px;border:1px solid #35507d;background:#16253f;color:#dbe8ff;display:inline-flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:900;cursor:pointer;flex:0 0 auto}
.collapse-toggle:hover{background:#1d3153}
.obj-row.hidden-child{display:none}
.stitch-badge{font-size:.64rem;padding:2px 7px;border-radius:10px;font-weight:800;white-space:nowrap;text-transform:uppercase}
.stitch-fill{background:#244b36;color:#b8ffd2}
.stitch-satin{background:#5b3a1d;color:#ffd39a}
.stitch-skip{background:#4b2530;color:#ffb8c6}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#2d7a52;color:#fff;padding:10px 18px;border-radius:8px;font-size:.82rem;font-weight:700;opacity:0;transition:opacity .25s;pointer-events:none}
.toast.show{opacity:1}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid #405273;border-top-color:#e94560;border-radius:50%;animation:spin .8s linear infinite;vertical-align:-2px;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
.work-preview.preview-dark{background:#171923!important;background-image:linear-gradient(45deg,#24283a 25%,transparent 25%),linear-gradient(-45deg,#24283a 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#24283a 75%),linear-gradient(-45deg,transparent 75%,#24283a 75%)!important;background-size:24px 24px!important;background-position:0 0,0 12px,12px -12px,-12px 0!important}


.work-card{position:relative!important}


/* Preview background toggle */
.work-preview.preview-dark,
.work-preview.preview-dark svg,
.work-preview.preview-dark > svg {
  background-color:#111827!important;
  background-image:
    linear-gradient(45deg,#1f2937 25%,transparent 25%),
    linear-gradient(-45deg,#1f2937 25%,transparent 25%),
    linear-gradient(45deg,transparent 75%,#1f2937 75%),
    linear-gradient(-45deg,transparent 75%,#1f2937 75%)!important;
  background-size:24px 24px!important;
  background-position:0 0,0 12px,12px -12px,-12px 0!important;
}
.work-preview.preview-light,
.work-preview.preview-light svg,
.work-preview.preview-light > svg {
  background-color:#f5f7fb!important;
}
.preview-bg-toggle{
  position:absolute;
  top:48px;
  right:14px;
  z-index:200;
  border:1px solid #3d4c72;
  background:#111827;
  color:#dbe8ff;
  border-radius:999px;
  padding:5px 10px;
  font-size:.72rem;
  cursor:pointer;
  box-shadow:0 3px 10px rgba(0,0,0,.35);
}
.preview-bg-toggle:hover{background:#1d3153}
.work-card{position:relative!important}

</style>
</head>
<body>
<header>
  <h1>EasyStitch</h1>
  <span class="sub">Unified workflow prototype — v69 plus preserved assignments on structure changes</span>
  <span class="status" id="status">No image loaded</span>
</header>
<div class="app">
  <nav class="nav">
    <div class="step active" onclick="showPane(1)">
      <div class="num">1</div><div><div class="stitle">Image Prep</div><div class="sdesc">Load, resize, quantize and create a solid image for tracing.</div></div>
    </div>
    <div class="step" onclick="showPane(2)">
      <div class="num">2</div><div><div class="stitle">Trace</div><div class="sdesc">Generate SVG paths from the prepared image and tune trace settings.</div></div>
    </div>
    <div class="step" onclick="showPane(3)">
      <div class="num">3</div><div><div class="stitle">Path Structure</div><div class="sdesc">Review traced SVG objects, split, and group objects into nested parent/child path sets.</div></div>
    </div>
    <div class="step" onclick="showPane(4)">
      <div class="num">4</div><div><div class="stitle">Stitch Tuning & Export</div><div class="sdesc">Preview stitches, tune generation, and export machine files.</div></div>
    </div>
  </nav>

  <section class="content">
    <div class="panel active" id="pane1">
      <aside class="left">
        <div class="card" data-tooltip="Load an image, then generate a cleaned, colour-reduced PNG that feeds the trace pane.">
          <h2>1. Source Image</h2>
          <div class="formrow">
            <label>Upload image</label>
            <input type="file" id="file-input" accept="image/png,image/jpeg,image/webp,image/bmp">
          </div>
          <button class="btn" onclick="uploadImage()">Load Uploaded Image</button>
        </div>

        <div class="card">
          <h2>2. Prep Settings</h2>
          <div class="formrow">
            <label>Colour count</label>
            <div class="range-row">
              <input type="range" id="colors" min="2" max="20" step="1" value="12" oninput="syncVal('colors','colors-val','')">
              <span class="val" id="colors-val">12</span>
            </div>
          </div>
          <div class="formrow">
            <label>Max image size</label>
            <div class="range-row">
              <input type="range" id="max-size" min="100" max="2000" step="50" value="1000" oninput="syncVal('max-size','max-size-val','px')">
              <span class="val" id="max-size-val">1000px</span>
            </div>
          </div>

          <div class="formrow">
            <label>Simplify preset</label>
            <select id="simplify-preset" style="background:#0f1628;color:#e7eaf5;border:1px solid #30476d;border-radius:6px;padding:8px">
              <option value="none">None / artwork already clean</option>
              <option value="soft">Soft cleanup</option>
              <option value="cartoon">Cartoon flatten</option>
              <option value="strong">Strong photo simplify</option>
            </select>
          </div>

          <div class="formrow">
            <label>Smoothing passes</label>
            <div class="range-row">
              <input type="range" id="smoothing" min="0" max="5" step="1" value="0" oninput="syncVal('smoothing','smoothing-val','')">
              <span class="val" id="smoothing-val">0</span>
            </div>
          </div>

          <div class="formrow">
            <label>Colour boost</label>
            <div class="range-row">
              <input type="range" id="color-boost" min="0.6" max="1.6" step="0.05" value="1.0" oninput="syncFloatVal('color-boost','color-boost-val','×')">
              <span class="val" id="color-boost-val">1.0×</span>
            </div>
          </div>

          <div class="formrow">
            <label>Contrast boost</label>
            <div class="range-row">
              <input type="range" id="contrast-boost" min="0.6" max="1.6" step="0.05" value="1.0" oninput="syncFloatVal('contrast-boost','contrast-boost-val','×')">
              <span class="val" id="contrast-boost-val">1.0×</span>
            </div>
          </div>

          <button class="btn primary" id="prep-btn" onclick="runPrep()">Run Image Prep</button>
        </div>

        <div class="card">
          <h2>3. Output</h2>
          <div class="meta" id="meta">No prepared image yet.</div>
        </div>

        <div class="card">
          <h2>Palette</h2>
          <div class="palette" id="palette"></div>
        </div>
      </aside>

      <main class="main">
        <div class="preview-area">
          <div class="preview-box">
            <div class="preview-title"><span>Source / Normalised</span><span id="orig-size"></span></div>
            <div class="preview-img-wrap" id="orig-preview"><span style="color:#555">No image loaded</span></div>
          </div>
          <div class="preview-box">
            <div class="preview-title"><span>Prepared / Quantized</span><span id="prep-size"></span></div>
            <div class="preview-img-wrap" id="prep-preview"><span style="color:#555">Run prep to preview</span></div>
          </div>
        </div>
        <div class="footer">
          <span id="footer-msg">Pane 1 is intentionally the only active pane for this build.</span>
        </div>
      </main>
    </div>

    <div class="panel" id="pane2">
      <aside class="left">
        <div class="card" data-tooltip="Generate editable fill-region SVG paths from the prepared image.">
          <h2>2. Trace</h2>
          <button class="btn primary" id="trace-btn" onclick="runTrace()">Run Trace</button>
        </div>

        <div class="card">
          <h2>Fill-region tracing (vtracer)</h2>

          <div class="formrow">
            <label>Speckle filter</label>
            <div class="range-row">
              <input type="range" id="trace-speckle" min="0" max="16" step="1" value="8" oninput="syncVal('trace-speckle','trace-speckle-val','px')">
              <span class="val" id="trace-speckle-val">8px</span>
            </div>
          </div>

          <div class="formrow">
            <label>Trace mode</label>
            <select id="trace-mode" style="background:#0f1628;color:#e7eaf5;border:1px solid #30476d;border-radius:6px;padding:8px">
              <option value="spline">Spline / smooth curves</option>
              <option value="polygon" selected>Polygon / simpler edges</option>
              <option value="pixel">Pixel / blocky exactness</option>
            </select>
          </div>

          <div class="formrow">
            <label>Layering</label>
            <select id="trace-hierarchical" style="background:#0f1628;color:#e7eaf5;border:1px solid #30476d;border-radius:6px;padding:8px">
              <option value="cutout">Cutout / non-overlapping regions</option>
              <option value="stacked">Stacked / layered regions</option>
            </select>
          </div>

          <div class="formrow">
            <label>Combine / colour step</label>
            <div class="range-row">
              <input type="range" id="trace-gradient-step" min="0" max="64" step="1" value="16" oninput="syncVal('trace-gradient-step','trace-gradient-step-val','')">
              <span class="val" id="trace-gradient-step-val">16</span>
            </div>
          </div>

          <div class="formrow">
            <label>Segment length</label>
            <div class="range-row">
              <input type="range" id="trace-segment-length" min="3.5" max="10" step="0.5" value="3.5" oninput="syncFloatVal('trace-segment-length','trace-segment-length-val','px')">
              <span class="val" id="trace-segment-length-val">3.5px</span>
            </div>
          </div>

          <details style="margin-top:12px">
            <summary style="cursor:pointer;font-size:.78rem;color:#b8bfd6;font-weight:700">Advanced</summary>
            <div style="margin-top:12px">
              <div class="formrow">
                <label>Colour precision</label>
                <div class="range-row">
                  <input type="range" id="trace-color-precision" min="2" max="8" step="1" value="6" oninput="syncVal('trace-color-precision','trace-color-precision-val','')">
                  <span class="val" id="trace-color-precision-val">6</span>
                </div>
              </div>
              <div class="formrow">
                <label>Corner threshold</label>
                <div class="range-row">
                  <input type="range" id="trace-corner-threshold" min="0" max="120" step="5" value="60" oninput="syncVal('trace-corner-threshold','trace-corner-threshold-val','')">
                  <span class="val" id="trace-corner-threshold-val">60</span>
                </div>
              </div>
              <div class="formrow">
                <label>Splice threshold</label>
                <div class="range-row">
                  <input type="range" id="trace-splice-threshold" min="0" max="120" step="5" value="45" oninput="syncVal('trace-splice-threshold','trace-splice-threshold-val','')">
                  <span class="val" id="trace-splice-threshold-val">45</span>
                </div>
              </div>
              <div class="formrow">
                <label>Path precision</label>
                <div class="range-row">
                  <input type="range" id="trace-path-precision" min="0" max="6" step="1" value="3" oninput="syncVal('trace-path-precision','trace-path-precision-val','')">
                  <span class="val" id="trace-path-precision-val">3</span>
                </div>
              </div>
            </div>
          </details>
        </div>

        <div class="card">
          <h2>Trace Output</h2>
          <div class="meta" id="trace-meta">Run Image Prep first, then Trace.</div>
        </div>
      </aside>

      <main class="main">
        <div class="preview-area" style="grid-template-columns:1fr">
          <div class="preview-box">
            <div class="preview-title"><span>Fill-region SVG Preview</span><span id="trace-count"></span></div>
            <div class="preview-img-wrap" id="trace-preview"><span style="color:#555">No SVG trace yet</span></div>
          </div>
        </div>
        <div class="footer">
          <span id="trace-footer-msg">Pane 2 produces both fill regions and stroke candidates for the Path Structure pane.</span>
        </div>
      </main>
    </div>
<div class="panel" id="pane3">
      <aside class="left">
        <div class="card" data-tooltip="Organise the traced SVG before stitch logic begins. Select/cut paths, then assign Fill, Satin, or Skip.">
          <h2>3. Path Structure</h2>
          <button class="btn" onclick="loadStructure()">Load current trace</button>
        </div>

        <div class="card tool-card" data-tooltip="Manual split: first click chooses the target path, next two clicks place the cut. Junction cut: choose centre, then branch points, then apply.">
          <h2>Structure Tools</h2>
          <button class="btn" onclick="setStructureTool('select')" style="margin-top:8px" id="tool-select-btn">Select / cycle tool</button>
                                        <button class="btn" onclick="setStructureTool('manual_split')" style="margin-top:8px" id="manual-split-btn">Manual split tool</button>
          <button class="btn" onclick="setStructureTool('junction')" style="margin-top:8px" id="junction-cut-btn">Junction cut tool</button>
          <button class="btn" onclick="applyJunctionCut()" style="margin-top:8px" id="junction-apply-btn">Apply junction cut</button>
          <button class="btn" onclick="cancelManualSplit()" style="margin-top:8px">Clear tool points</button>
          <button class="btn" onclick="saveStructureJson()" style="margin-top:8px">Save prep JSON</button>
        </div>

        <div class="card tool-card" data-tooltip="Assign stitch types while cutting/grouping paths. Fill shows a hatch overlay, satin remains fully visible, and skip fades.">
          <h2>Stitch Type Assignment</h2>
          <button class="btn" id="assign-fill-tool-btn" onclick="setStructureTool('assign_fill')">Fill tool</button>
          <button class="btn" id="assign-satin-tool-btn" onclick="setStructureTool('assign_satin')" style="margin-top:8px">Satin tool</button>
          <button class="btn" id="assign-skip-tool-btn" onclick="setStructureTool('assign_skip')" style="margin-top:8px">Skip tool</button>

          <button class="btn" onclick="assignColourStitch('fill')" style="margin-top:12px">All this colour → Fill</button>
          <button class="btn" onclick="assignColourStitch('satin')" style="margin-top:8px">All this colour → Satin</button>
          <button class="btn" onclick="assignColourStitch('skip')" style="margin-top:8px">All this colour → Skip</button>

          <button class="btn" onclick="autoAssignStitches()" style="margin-top:12px" data-tooltip="Auto guess runs in Pane 3: image-edge paths become Skip; likely columns become Satin; enclosed regions that fill at least the selected percentage of their satin container become Fill, using a small raster pixel-area comparison.">Auto guess all</button>
          <div class="formrow" data-tooltip="Auto Guess enclosed-fill threshold. Lower values convert more enclosed column-like regions back to Fill. Higher values are stricter. Uses raster pixel-area comparison on a small offscreen canvas.">
            <label>Auto enclosed Fill threshold</label>
            <div class="range-row">
              <input type="range" id="auto-enclosed-fill-pct" min="50" max="90" step="5" value="70" oninput="syncVal('auto-enclosed-fill-pct','auto-enclosed-fill-pct-val','%')">
              <span class="val" id="auto-enclosed-fill-pct-val">70%</span>
            </div>
          </div>
          <button class="btn" onclick="saveStitchJson()" style="margin-top:8px">Save stitch JSON</button>
          <div class="meta" id="structure-stitch-summary" style="margin-top:8px">No stitch assignments yet.</div>
        </div>

        <div class="card">
          <h2>Object Detail</h2>
          <div class="meta" id="structure-detail">Load a traced SVG to begin.</div>
        </div>
      </aside>

      <main class="main" style="display:flex;flex-direction:row;min-width:0">
        <div id="structure-object-panel" style="width:42px;background:#121a30;border-right:1px solid #0f3460;display:flex;flex-direction:column;min-height:0;transition:width .18s ease">
          <div style="padding:10px 8px;border-bottom:1px solid #0f3460;font-size:.75rem;color:#8f96b3;display:flex;gap:8px;align-items:center;justify-content:space-between">
            <button class="collapse-toggle" id="structure-object-panel-toggle" title="Show/hide embroidery object list" onclick="toggleStructureObjectPanel()">☰</button>
            <span class="structure-list-expanded-only" style="display:none">Embroidery Objects</span><span class="structure-list-expanded-only" id="structure-count" style="display:none">0</span>
          </div>
          <div class="structure-list-expanded-only" id="structure-list" style="overflow:auto;flex:1;display:none"></div>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;min-width:0">
          <div class="preview-area" id="structure-preview-area" style="grid-template-columns:1fr">
            <div class="preview-box">
              <div class="preview-title"><span>Path Structure Preview</span><span id="structure-preview-meta"></span></div>
              <div class="preview-img-wrap" id="structure-preview"><span style="color:#555">No traced SVG loaded</span></div>
            </div>
          </div>
          <div class="footer">
            <span id="structure-footer-msg">Use Shift + click or checkboxes to multi-select. Grouping is logical, not destructive geometry merging.</span>
          </div>
        </div>
      </main>
    </div>
    <div class="panel" id="pane4">
      <aside class="left">
        <div class="card" data-tooltip="Load the prepared path map from Pane 3, choose hoop/design size, preview stitches, tune generation, and export machine files.">
          <h2>4. Stitch Tuning & Export</h2>
          <button class="btn" onclick="loadStitchPane()" data-tooltip="Loads the Pane 3 path map exactly as assigned. Pane 4 does not change Fill/Satin/Skip assignments.">Load prepared structure</button>
          <div class="formrow" style="margin-top:10px">
            <label>Hoop size</label>
            <select id="hoop-size" onchange="onHoopSizeChanged()" data-tooltip="Target embroidery hoop/design area. The size panel below shows the current traced size and selected design size.">
              <option value="120x120" selected>120 × 120 mm</option>
              <option value="260x200">260 × 200 mm (V × H)</option>
              <option value="360x200">360 × 200 mm (V × H)</option>
            </select>
          </div>

          <div class="formrow" data-tooltip="Set the actual stitched design size by longest side. This sets the actual stitched design size used for preview, stitch generation, and export.">
            <label>Design longest side</label>
            <div class="range-row">
              <input type="range" id="design-longest-side" min="5" max="120" step="1" value="120" oninput="onDesignLongestSideSlider()">
              <input type="number" id="design-longest-side-input" min="5" max="120" step="0.1" value="120" style="width:74px" onchange="onDesignLongestSideInput()" onkeydown="if(event.key==='Enter'){event.preventDefault();onDesignLongestSideInput();previewStitches();}">
              <span class="val">mm</span>
            </div>
          </div>

          <div class="meta" id="design-size-meta" style="margin-top:8px">Load prepared structure to calculate design size.</div>
          <div class="meta" id="stitch-sort-meta" style="margin-top:8px;display:none">Sort mode: number</div>
        </div>

        <div class="card" data-tooltip="Tune stitch generation, preview layers, manual rungs, stitch plan generation, and export/debug options.">
          <h2>Stitch Settings</h2>

          <details style="margin-top:12px">
            <summary style="cursor:pointer;font-size:.78rem;color:#b8bfd6;font-weight:700">Stitch generation settings</summary>
            <div style="margin-top:12px">
          <div class="formrow">
            <label>Fill angle</label>
            <div class="range-row">
              <input type="range" id="stitch-fill-angle" min="0" max="180" step="5" value="45" oninput="syncVal('stitch-fill-angle','stitch-fill-angle-val','°')">
              <span class="val" id="stitch-fill-angle-val">45°</span>
            </div>
          </div>

          <div class="formrow">
            <label><input type="checkbox" id="auto-fill-direction-enable" checked> Auto fill direction for elongated shapes</label>
          </div>

          <div class="formrow">
            <label>Auto fill elongation threshold</label>
            <div class="range-row">
              <input type="range" id="auto-fill-threshold" min="1.2" max="5.0" step="0.1" value="2.0" oninput="syncFloatVal('auto-fill-threshold','auto-fill-threshold-val','×')">
              <span class="val" id="auto-fill-threshold-val">2.0×</span>
            </div>
          </div>

          <div class="formrow">
            <label>Stitch order mode</label>
            <select id="stitch-order-mode">
              <option value="quality">Quality: all fills, then satin outlines</option>
              <option value="color_min">Minimum colour changes: fill then satin per colour</option>
            </select>
          </div>

          <div class="formrow">
            <label><input type="checkbox" id="avoid-top-fill-overlap-enable" checked> Top fill avoids different-colour objects</label>
          </div>

          <div class="formrow">
            <label><input type="checkbox" id="underlay-protect-lighter-enable" checked> Underlay protects lighter objects</label>
          </div>

          <div class="formrow">
            <label>Light-object protection threshold</label>
            <div class="range-row">
              <input type="range" id="underlay-light-threshold" min="10" max="100" step="5" value="45" oninput="syncVal('underlay-light-threshold','underlay-light-threshold-val','')">
              <span class="val" id="underlay-light-threshold-val">45</span>
            </div>
          </div>

          <div class="formrow">
            <label>Underlay long-jump trim threshold</label>
            <div class="range-row">
              <input type="range" id="underlay-jump-trim-threshold-mm" min="3" max="30" step="1" value="5" oninput="syncFloatVal('underlay-jump-trim-threshold-mm','underlay-jump-trim-threshold-val','mm')">
              <span class="val" id="underlay-jump-trim-threshold-val">5.0mm</span>
            </div>
          </div>

          <div class="formrow">
            <label>Fill row spacing</label>
            <div class="range-row">
              <input type="range" id="stitch-row-spacing" min="0.2" max="1.2" step="0.05" value="0.4" oninput="syncFloatVal('stitch-row-spacing','stitch-row-spacing-val','mm')">
              <span class="val" id="stitch-row-spacing-val">0.4mm</span>
            </div>
          </div>

          <div class="formrow">
            <label>Running stitch length</label>
            <div class="range-row">
              <input type="range" id="stitch-length-mm" min="1.0" max="5.0" step="0.25" value="2.5" oninput="syncFloatVal('stitch-length-mm','stitch-length-val','mm')">
              <span class="val" id="stitch-length-val">2.5mm</span>
            </div>
          </div>

          <div class="formrow">
            <label>Jump/trim threshold</label>
            <div class="range-row">
              <input type="range" id="jump-trim-threshold-mm" min="1.0" max="8.0" step="0.5" value="3.0" oninput="syncFloatVal('jump-trim-threshold-mm','jump-trim-threshold-val','mm')">
              <span class="val" id="jump-trim-threshold-val">3.0mm</span>
            </div>
          </div>

          <div class="formrow">
            <label>Satin spacing</label>
            <div class="range-row">
              <input type="range" id="satin-spacing-mm" min="0.25" max="1.2" step="0.05" value="0.45" oninput="syncFloatVal('satin-spacing-mm','satin-spacing-val','mm')">
              <span class="val" id="satin-spacing-val">0.45mm</span>
            </div>
          </div>

          <div class="formrow">
            <label>Satin max width</label>
            <div class="range-row">
              <input type="range" id="satin-max-width-mm" min="2.0" max="12.0" step="0.5" value="7.0" oninput="syncFloatVal('satin-max-width-mm','satin-max-width-val','mm')">
              <span class="val" id="satin-max-width-val">7.0mm</span>
            </div>
          </div>

          <div class="formrow">
            <label>Extra end rungs</label>
            <div class="range-row">
              <input type="range" id="satin-end-extra-rungs" min="0" max="6" step="1" value="0" oninput="syncVal('satin-end-extra-rungs','satin-end-extra-rungs-val','')">
              <span class="val" id="satin-end-extra-rungs-val">0</span>
            </div>
          </div>

          <div class="formrow">
            <label><input type="checkbox" id="satin-guide-helper-enable"> Use guide-rail helper if rail coverage is sparse</label>
          </div>

          <div class="formrow">
            <label><input type="checkbox" id="satin-debug-rails-enable"> Show satin rail debug overlay</label>
          </div>

          <div class="formrow">
            <label><input type="checkbox" id="underlay-enable" checked onchange="updateUnderlayUi()"> Enable / show underlay</label>
          </div>

          <div class="formrow">
            <label>Preview layer view</label>
            <select id="preview-layer-mode" onchange="setPreviewLayerMode(this.value)">
              <option value="both">Both top + underlay</option>
              <option value="top">Top stitches only</option>
              <option value="underlay">Underlay only</option>
            </select>
          </div>

          <div id="underlay-settings">
            <div class="formrow">
              <label>Underlay edge inset</label>
              <div class="range-row">
                <input type="range" id="underlay-inset-mm" min="0.2" max="2.0" step="0.1" value="0.8" oninput="syncFloatVal('underlay-inset-mm','underlay-inset-val','mm')">
                <span class="val" id="underlay-inset-val">0.8mm</span>
              </div>
            </div>

            <div class="formrow">
              <label>Underlay row spacing</label>
              <div class="range-row">
                <input type="range" id="underlay-row-mm" min="0.8" max="4.0" step="0.1" value="1.6" oninput="syncFloatVal('underlay-row-mm','underlay-row-val','mm')">
                <span class="val" id="underlay-row-val">1.6mm</span>
              </div>
            </div>
          </div>

            </div>
          </details>

          <button class="btn primary" onclick="previewStitches()" style="margin-top:8px">Preview stitches</button>
          <button class="btn" onclick="generateStitchPlan()" style="margin-top:8px">Generate stitch plan</button>
          <button class="btn" onclick="viewFullStitchPlan()" style="margin-top:8px">View stitch plan</button>
          <button class="btn" id="manual-rung-mode-btn" onclick="toggleManualRungMode()" style="margin-top:8px" data-tooltip="Manual rung mode: first click chooses the satin path under the cursor, second click finishes the guide rung. Drag endpoints to adjust.">Manual rung mode: off</button>
          <button class="btn" onclick="clearSelectedManualRungs()" style="margin-top:8px">Clear selected object rungs</button>
          <button class="btn" onclick="clearAllManualRungs()" style="margin-top:8px">Clear all manual rungs</button>
          <div class="meta" id="manual-rung-status" style="margin-top:8px">Manual rung target: none</div>
        </div>

        <div class="card">
          <h2>Stitch Plan Stats</h2>
          <details style="margin-top:8px">
            <summary style="cursor:pointer;font-size:.78rem;color:#b8bfd6;font-weight:700">Status information</summary>
            <div class="meta" id="stitch-plan-stats" style="margin-top:8px">No stitch plan generated yet.</div>
          </details>
          <div class="formrow" style="margin-top:10px">
            <label><input type="checkbox" id="plan-show-stitches" checked> Show stitches</label>
          </div>
          <div class="formrow">
            <label><input type="checkbox" id="show-stitch-dots" onchange="renderCachedStitchPreview(); viewStitchPlan(stitchPlanPlayIndex)" data-tooltip="Show individual stitch points as dots. Useful at small design sizes, but can look busy on larger designs."> Show stitch dots</label>
          </div>
          <div class="formrow">
            <label><input type="checkbox" id="plan-show-jumps" checked> Show jumps</label>
          </div>
          <div class="formrow">
            <label><input type="checkbox" id="plan-show-trims" checked> Show trims</label>
          </div>
          <div class="formrow">
            <label>Max displayed stitch events</label>
            <div class="range-row">
              <input type="range" id="plan-max-events" min="100" max="15000" step="100" value="1000" oninput="syncVal('plan-max-events','plan-max-events-val','')">
              <span class="val" id="plan-max-events-val">1000</span>
            </div>
          </div>

          <div class="formrow">
            <label>Playback position</label>
            <div class="range-row">
              <input type="range" id="plan-playhead" min="0" max="0" step="1" value="0" oninput="seekStitchPlan(parseInt(this.value || '0', 10))">
              <span class="val" id="plan-playhead-val">0</span>
            </div>
          </div>

          <button class="btn" onclick="playStitchPlan()" style="margin-top:8px">Play</button>
          <button class="btn" onclick="pauseStitchPlan()" style="margin-top:8px">Pause</button>
          <button class="btn" onclick="seekStitchPlan(0)" style="margin-top:8px">Restart</button>
        </div>

        <div class="card" data-tooltip="Export machine files and save debugging artifacts for checking generated stitch output.">
          <h2>Export</h2>
          <button class="btn primary" onclick="exportStitchPlanDst()" style="margin-top:8px">Export DST</button>
          <button class="btn primary" onclick="exportStitchPlanJef()" style="margin-top:8px" data-tooltip="JEF export via pyembroidery. Good second test format for machines that support Janome/JEF files.">Export JEF</button>
          <button class="btn primary" onclick="exportStitchPlanVp3()" style="margin-top:8px" data-tooltip="Experimental VP3 export via pyembroidery. DST remains the validation baseline while VP3 trim handling is tested.">Export VP3 Beta</button>
          <button class="btn" onclick="saveStitchPlanJson()" style="margin-top:8px">Save stitch plan JSON</button>
          <button class="btn" onclick="saveExportDebugJson()" style="margin-top:8px">Save export debug JSON</button>
          <div class="meta" id="export-stats" style="margin-top:8px">No machine export yet.</div>
        </div>
      </aside>

      <main class="main" style="display:flex;flex-direction:row;min-width:0">
        <div id="stitch-object-panel" style="width:42px;background:#121a30;border-right:1px solid #0f3460;display:flex;flex-direction:column;min-height:0;transition:width .18s ease">
          <div style="padding:10px 8px;border-bottom:1px solid #0f3460;font-size:.75rem;color:#8f96b3;display:flex;gap:8px;align-items:center;justify-content:space-between">
            <button class="collapse-toggle" id="stitch-object-panel-toggle" title="Show/hide stitch object list" onclick="toggleStitchObjectPanel()">☰</button>
            <span class="stitch-list-expanded-only" style="display:none">Stitch Objects</span><span class="stitch-list-expanded-only" id="stitch-count" style="display:none">0</span>
          </div>
          <div class="stitch-list-expanded-only" id="stitch-list" style="overflow:auto;flex:1;display:none"></div>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;min-width:0">
          <div class="preview-area" id="stitch-preview-area" style="grid-template-columns:1fr">
            <div class="preview-box">
              <div class="preview-title"><span>Stitch Assignment Preview</span><span id="stitch-preview-meta"></span></div>
              <div class="preview-img-wrap" id="stitch-preview"><span style="color:#555">No stitch assignments loaded</span></div>
            </div>
          </div>
          <div class="footer">
            <span id="stitch-footer-msg">Fill = normal colour region, Satin = highlighted outline/column candidate, Skip = faded object.</span>
          </div>
        </div>
      </main>
    </div>
  </section>
</div>
<div class="toast" id="toast"></div>

<script>
let currentImageLoaded = false;
let lastPrep = null;
let lastTrace = null;
let structureLoaded = false;
let structureSvgW = 500;
let structureSvgH = 500;
let structureSourcePaths = [];
let structureObjects = [];
let structureSelectedId = null;
let structureCheckedIds = new Set();
let structureGroupCounter = 1;
let structureSplitMode = false;
let structureJunctionMode = false;
let structureActiveTool = 'select';
let structureObjectListCollapsed = true;
let structureSplitTargetReady = false;
let structureCutPoints = [];
let structureJunctionPoints = [];
let structureHoverPoint = null;
let structureNeedSecondCut = false;
let stitchLoaded = false;
let stitchObjects = [];
let stitchAssignments = {};
let stitchSelectedId = null;
let stitchCheckedIds = new Set();
let structureCollapsedGroups = new Set();
let stitchCollapsedGroups = new Set();
let stitchSortMode = 'number';
let stitchObjectListCollapsed = true;
let stitchManualRungs = {};
let currentStitchPlan = null;
let currentExportDebug = null;
let currentStitchPreview = null;
let stitchPlanPlayIndex = 0;
let stitchPlanPlayTimer = null;
let previewLayerMode = 'both';
let manualRungMode = false;
let pendingManualRungPoint = null;
let draggingManualRung = null;
let workZoom = 1.0;
let designTargetLongestMm = null;
let traceAutoRunOnce = false;


let tooltipTimer = null;
let tooltipEl = null;
let tooltipTarget = null;

function hideHoverTooltip() {
  if (tooltipTimer) {
    clearTimeout(tooltipTimer);
    tooltipTimer = null;
  }
  tooltipTarget = null;
  if (tooltipEl) {
    tooltipEl.classList.remove('show');
    tooltipEl.remove();
    tooltipEl = null;
  }
}

function showHoverTooltip(target, ev) {
  const msg = target?.getAttribute('data-tooltip');
  if (!msg) return;

  hideHoverTooltip();
  tooltipTarget = target;
  tooltipTimer = setTimeout(() => {
    if (tooltipTarget !== target) return;
    tooltipEl = document.createElement('div');
    tooltipEl.className = 'hover-tooltip';
    tooltipEl.textContent = msg;
    document.body.appendChild(tooltipEl);

    const margin = 14;
    const rect = tooltipEl.getBoundingClientRect();
    let x = (ev?.clientX || window.innerWidth / 2) + 14;
    let y = (ev?.clientY || window.innerHeight / 2) + 14;
    if (x + rect.width + margin > window.innerWidth) x = window.innerWidth - rect.width - margin;
    if (y + rect.height + margin > window.innerHeight) y = window.innerHeight - rect.height - margin;
    if (x < margin) x = margin;
    if (y < margin) y = margin;

    tooltipEl.style.left = x + 'px';
    tooltipEl.style.top = y + 'px';
    requestAnimationFrame(() => tooltipEl && tooltipEl.classList.add('show'));
  }, 1500);
}

function initHoverTooltips() {
  document.addEventListener('mouseover', ev => {
    const target = ev.target.closest?.('[data-tooltip]');
    if (target) showHoverTooltip(target, ev);
  });
  document.addEventListener('mouseout', ev => {
    if (ev.target.closest?.('[data-tooltip]')) hideHoverTooltip();
  });
  document.addEventListener('click', hideHoverTooltip, true);
  document.addEventListener('keydown', hideHoverTooltip, true);
}


async function init() {
  initHoverTooltips();
  const res = await fetch('/api/state');
  const data = await res.json();
  if (data.has_image) {
    currentImageLoaded = true;
    document.getElementById('status').textContent = data.input_name;
    toast('Image loaded from command line');
  }
}

function showPane(n) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('pane' + n).classList.add('active');
  document.querySelectorAll('.step').forEach((s, i) => s.classList.toggle('active', i === n - 1));

  if (n === 2 && lastPrep && !lastTrace && !traceAutoRunOnce) {
    traceAutoRunOnce = true;
    setTimeout(() => runTrace(), 80);
  }
  if (n === 3 && !structureLoaded && lastTrace) {
    loadStructure();
  }
  if (n === 4 && !stitchLoaded && structureObjects.length) {
    loadStitchPane();
  }
  setWorkZoom(workZoom);
}
function showDisabled() {
  toast('This pane is a placeholder in this build');
}

function syncVal(inputId, valId, suffix) {
  const v = document.getElementById(inputId).value;
  document.getElementById(valId).textContent = v + suffix;
}

function syncFloatVal(inputId, valId, suffix) {
  const v = parseFloat(document.getElementById(inputId).value);
  document.getElementById(valId).textContent = v.toFixed(2).replace(/0$/, '').replace(/\.0$/, '.0') + suffix;
}

function syncPosterizeVal() {
  const v = parseInt(document.getElementById('posterize-bits').value, 10);
  document.getElementById('posterize-val').textContent = v === 0 ? 'off' : v + ' bit';
}

let previewDarkBackgrounds = new Set();

function applyPreviewBgState(id) {
  const el = document.getElementById(id);
  if (!el) return;

  const dark = previewDarkBackgrounds.has(id);
  el.classList.toggle('preview-dark', dark);
  el.classList.toggle('preview-light', !dark);

  // Force inline styles too because some preview SVGs and checkerboard
  // backgrounds are created dynamically and can override the class.
  const bg = dark ? '#111827' : '';
  el.style.backgroundColor = bg;

  const svgs = el.querySelectorAll('svg');
  svgs.forEach(svg => {
    svg.style.backgroundColor = dark ? '#111827' : '';
    svg.style.backgroundImage = dark
      ? 'linear-gradient(45deg,#1f2937 25%,transparent 25%),linear-gradient(-45deg,#1f2937 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#1f2937 75%),linear-gradient(-45deg,transparent 75%,#1f2937 75%)'
      : '';
    svg.style.backgroundSize = dark ? '24px 24px' : '';
    svg.style.backgroundPosition = dark ? '0 0,0 12px,12px -12px,-12px 0' : '';
  });

  const parent = el.parentElement;
  if (parent) parent.style.position = 'relative';

  const btn = parent ? parent.querySelector(`.preview-bg-toggle[data-target="${id}"]`) : null;
  if (btn) btn.textContent = dark ? 'Light BG' : 'Dark BG';
}

function togglePreviewBg(id) {
  const el = document.getElementById(id);
  if (!el) return;
  if (previewDarkBackgrounds.has(id)) previewDarkBackgrounds.delete(id);
  else previewDarkBackgrounds.add(id);
  applyPreviewBgState(id);
}

function restorePreviewBg(id) {
  applyPreviewBgState(id);
}

function ensurePreviewBgButtons() {
  const ids = ['prep-preview', 'trace-preview', 'structure-preview', 'stitch-preview'];
  ids.forEach((id) => {
    const preview = document.getElementById(id);
    if (!preview) return;

    const parent = preview.parentElement || preview;
    parent.classList.add('work-card');
    parent.style.position = 'relative';

    const existing = parent.querySelector(`.preview-bg-toggle[data-target="${id}"]`);
    if (existing) return;

    const btn = document.createElement('button');
    btn.className = 'preview-bg-toggle';
    btn.dataset.target = id;
    btn.type = 'button';
    btn.textContent = previewDarkBackgrounds.has(id) ? 'Light BG' : 'Dark BG';
    btn.title = 'Toggle preview background';
    btn.style.top = '48px';
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      togglePreviewBg(id);
    });

    parent.appendChild(btn);
    restorePreviewBg(id);
  });
}



function setWorkZoom(z) {
  workZoom = Math.max(0.25, Math.min(5.0, z));
  document.querySelectorAll('.preview-img-wrap img, .preview-img-wrap svg').forEach(el => {
    el.style.transform = 'scale(' + workZoom + ')';
    el.style.transformOrigin = 'center center';
  });
  const msg = 'Work-area zoom: ' + Math.round(workZoom * 100) + '%. Shift + scroll over the preview to zoom.';
  const f1 = document.getElementById('footer-msg');
  if (f1) f1.textContent = msg;
  const f2 = document.getElementById('trace-footer-msg');
  if (f2 && lastTrace) f2.textContent = 'Trace complete. ' + msg;
  const f3 = document.getElementById('structure-footer-msg');
  if (f3) f3.textContent = 'Tool workflow: Select cycles assignment on second click; Fill/Satin/Skip tools paint paths; Manual split stays active until another tool is selected. ' + msg;
  const f4 = document.getElementById('stitch-footer-msg');
  if (f4) f4.textContent = 'Fill = normal colour region, Satin = highlighted column candidate, Skip = faded object. ' + msg;
  updateDesignSizeInfo();
}

async function uploadImage() {
  const input = document.getElementById('file-input');
  if (!input.files.length) {
    toast('Choose an image first');
    return;
  }
  const fd = new FormData();
  fd.append('image', input.files[0]);
  const res = await fetch('/api/upload', {method:'POST', body:fd});
  const data = await res.json();
  if (data.ok) {
    currentImageLoaded = true;
    document.getElementById('status').textContent = data.name;
    document.getElementById('meta').innerHTML = 'Loaded: <b>' + data.name + '</b><br>Now run Image Prep.';
    document.getElementById('palette').innerHTML = '';
    document.getElementById('orig-preview').innerHTML = '<span style="color:#555">Run prep to preview</span>';
    document.getElementById('prep-preview').innerHTML = '<span style="color:#555">Run prep to preview</span>';
    toast('Image uploaded');
  } else {
    toast('Upload failed: ' + (data.error || 'unknown'), 5000);
  }
}

async function runPrep() {
  if (!currentImageLoaded) {
    toast('Load an image first');
    return;
  }
  const btn = document.getElementById('prep-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Preparing…';

  const body = {
    colors: parseInt(document.getElementById('colors').value, 10),
    max_size: parseInt(document.getElementById('max-size').value, 10),
    simplify_preset: document.getElementById('simplify-preset').value,
    smoothing: parseInt(document.getElementById('smoothing').value, 10),
    posterize_bits: parseInt(document.getElementById('posterize-bits')?.value || '0', 10),
    color_boost: parseFloat(document.getElementById('color-boost').value),
    contrast_boost: parseFloat(document.getElementById('contrast-boost').value)
  };

  try {
    const res = await fetch('/api/prep', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!data.ok) {
      toast('Prep failed: ' + (data.error || 'unknown'), 6000);
      console.error(data.trace);
    } else {
      lastPrep = data;
      lastTrace = null;
      traceAutoRunOnce = false;
      renderPrep(data);
      toast('Prepared image saved');
    }
  } catch(e) {
    toast('Prep error: ' + e, 6000);
  }
  btn.disabled = false;
  btn.textContent = 'Run Image Prep';
}

function renderPrep(data) {
  document.getElementById('status').textContent = data.stem + '_prepared.png';
  document.getElementById('orig-size').textContent = data.original_width + '×' + data.original_height;
  document.getElementById('prep-size').textContent = data.processed_width + '×' + data.processed_height;

  document.getElementById('orig-preview').innerHTML =
    '<img src="' + data.original_preview + '" alt="original preview">';
  document.getElementById('prep-preview').innerHTML =
    '<img src="' + data.prepared_preview + '" alt="prepared preview">';

  document.getElementById('meta').innerHTML = `
    Source: <b>${data.input_path}</b><br>
    Prepared: <b>${data.output_path}</b><br>
    Original: <b>${data.original_width}×${data.original_height}</b> ${data.original_mode}<br>
    Processed: <b>${data.processed_width}×${data.processed_height}</b> ${data.resized ? '(resized)' : '(not resized)'}<br>
    Colours: <b>${data.colors_requested}</b><br>
    Simplify: <b>${data.simplify_preset}</b>, smoothing <b>${data.smoothing}</b>, posterize <b>${data.posterize_bits || 'off'}</b><br>
    Colour/contrast: <b>${data.color_boost}×</b> / <b>${data.contrast_boost}×</b><br>
    Time: <b>${data.time_sec}s</b>
  `;

  const pal = document.getElementById('palette');
  pal.innerHTML = '';
  data.palette.forEach((p, i) => {
    const row = document.createElement('div');
    row.className = 'swatch-row';
    row.innerHTML = `
      <div class="swatch" style="background:${p.hex}"></div>
      <div>${i + 1}. ${p.hex}</div>
      <div class="bar"><div style="width:${Math.max(2, p.percent)}%"></div></div>
      <div>${p.percent.toFixed(1)}%</div>
    `;
    pal.appendChild(row);
  });

  document.getElementById('footer-msg').textContent =
    'Prepared PNG is ready. Move to Pane 2 — Trace.';
  document.getElementById('trace-meta').innerHTML = 'Prepared source: <b>' + data.output_path + '</b><br>Ready to trace into fill regions and stroke candidates.';
}

async function runTrace() {
  if (!lastPrep || !lastPrep.output_path) {
    toast('Run Image Prep first');
    showPane(1);
    return;
  }

  const btn = document.getElementById('trace-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Tracing…';

  const body = {
    speckle: parseInt(document.getElementById('trace-speckle').value, 10),
    mode: document.getElementById('trace-mode').value,
    hierarchical: document.getElementById('trace-hierarchical').value,
    gradient_step: parseInt(document.getElementById('trace-gradient-step').value, 10),
    segment_length: parseFloat(document.getElementById('trace-segment-length').value),
    color_precision: parseInt(document.getElementById('trace-color-precision').value, 10),
    corner_threshold: parseInt(document.getElementById('trace-corner-threshold').value, 10),
    splice_threshold: parseInt(document.getElementById('trace-splice-threshold').value, 10),
    path_precision: parseInt(document.getElementById('trace-path-precision').value, 10)
  };

  try {
    const res = await fetch('/api/trace', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!data.ok) {
      toast('Trace failed: ' + (data.error || 'unknown'), 7000);
      console.error(data.trace);
    } else {
      lastTrace = data;
      renderTrace(data);
      toast('SVG trace generated');
    }
  } catch(e) {
    toast('Trace error: ' + e, 7000);
  }

  btn.disabled = false;
  btn.textContent = 'Run Trace';
}

function renderTrace(data) {
  const preview = document.getElementById('trace-preview');
  preview.innerHTML = data.svg_text;
  const svg = preview.querySelector('svg');
  if (svg) {
    svg.style.maxWidth = '100%';
    svg.style.maxHeight = '75vh';
    svg.style.width = 'auto';
    svg.style.height = 'auto';
    svg.style.background = 'transparent';
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.style.transformOrigin = 'center center';
    svg.querySelectorAll('path').forEach(p => {
      p.setAttribute('fill-rule', 'evenodd');
      p.setAttribute('clip-rule', 'evenodd');
    });
  }

  setWorkZoom(workZoom);

  document.getElementById('trace-count').textContent = data.path_count + ' fill paths';
  document.getElementById('trace-meta').innerHTML = `
    SVG: <b>${data.output_path}</b><br>
    Fill-region paths: <b>${data.path_count}</b><br>
    SVG size: <b>${data.svg_kb} KB</b><br>
    Trace time: <b>${data.time_sec}s</b><br>
    Mode: <b>${data.settings.mode}</b>, layering <b>${data.settings.hierarchical}</b><br>
    Speckle: <b>${data.settings.speckle}px</b>, segment length <b>${data.settings.segment_length}px</b><br>
    vtracer: <b>${data.vtracer}</b>
  `;
  document.getElementById('trace-footer-msg').textContent =
    'Trace complete. Pane 2 created fill-region SVG paths ready for Pane 3.';
}

function invalidateStitchPane() {
  stitchLoaded = false;
  stitchObjects = [];
  // Pane 3 now owns stitchAssignments. Do not clear them when structure changes;
  // this function only invalidates Pane 4's derived object list/preview/plan.
  stitchSelectedId = null;
  stitchCheckedIds = new Set();
  stitchCollapsedGroups = new Set();
  stitchManualRungs = {};
  currentStitchPlan = null;
  currentStitchPreview = null;
  stitchPlanPlayIndex = 0;
  if (stitchPlanPlayTimer) { clearInterval(stitchPlanPlayTimer); stitchPlanPlayTimer = null; }
  previewLayerMode = 'both';
  manualRungMode = false;
  pendingManualRungPoint = null;
  draggingManualRung = null;
  const count = document.getElementById('stitch-count');
  const list = document.getElementById('stitch-list');
  const prev = document.getElementById('stitch-preview');
  const detail = document.getElementById('stitch-detail');
  const summary = document.getElementById('stitch-summary');
  if (count) count.textContent = '0';
  if (list) list.innerHTML = '';
  if (prev) prev.innerHTML = '<span style="color:#555">Prepared structure changed; reload Pane 4</span>';
  if (detail) detail.innerHTML = 'Load prepared structure to begin.';
  if (summary) summary.innerHTML = 'No assignments yet.';
}


function initStructureAssignments(reset=false) {
  if (!structureObjects.length) return;
  if (reset) stitchAssignments = {};
  structureObjects.forEach(o => {
    if (!stitchAssignments[o.id]) stitchAssignments[o.id] = defaultStitchType(o);
  });
  Object.keys(stitchAssignments).forEach(id => {
    if (!structureObjects.some(o => o.id === id) && !stitchObjects.some(o => o.id === id)) {
      delete stitchAssignments[id];
    }
  });
  updateStructureStitchSummary();
}

function currentAssignmentObjects() {
  const pane3 = document.getElementById('pane3')?.classList.contains('active');
  if (pane3 && structureLoaded) return structureSelectedObjects();
  if (stitchLoaded) return stitchSelectedObjects();
  if (structureLoaded) return structureSelectedObjects();
  return [];
}

function currentAssignmentObjectList() {
  const pane3 = document.getElementById('pane3')?.classList.contains('active');
  if (pane3 && structureLoaded) return structureObjects;
  if (stitchLoaded) return stitchObjects;
  if (structureLoaded) return structureObjects;
  return [];
}

function currentAssignmentSelectedColour() {
  const objs = currentAssignmentObjects();
  return objs.length ? objs[0].color : null;
}

function refreshAssignmentViews() {
  currentStitchPlan = null;
  currentStitchPreview = null;
  currentExportDebug = null;
  if (structureLoaded) {
    renderStructureList();
    renderStructurePreview();
    updateStructureDetail();
    updateStructureStitchSummary();
  }
  if (stitchLoaded) {
    renderStitchList();
    renderStitchPreview();
    updateStitchDetail();
    updateStitchSummary();
  }
}

function updateStructureStitchSummary() {
  const summary = document.getElementById('structure-stitch-summary');
  if (!summary) return;
  if (!structureObjects.length) {
    summary.innerHTML = 'No stitch assignments yet.';
    return;
  }
  const counts = {fill:0, satin:0, skip:0};
  structureObjects.forEach(o => {
    const st = stitchAssignments[o.id] || defaultStitchType(o);
    counts[st] = (counts[st] || 0) + 1;
  });
  summary.innerHTML = `Fill: <b>${counts.fill}</b> · Satin: <b>${counts.satin}</b> · Skip: <b>${counts.skip}</b><br>Total objects: <b>${structureObjects.length}</b>`;
}

function assignmentLuminance(hex) {
  const h = (hex || '#000000').replace('#', '');
  if (h.length !== 6) return 0;
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function appendAssignmentDefs(svg) {
  if (!svg || svg.querySelector('#structureFillHatchDark')) return;
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  defs.innerHTML = `
    <pattern id="structureFillHatchDark" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="8" stroke="rgba(255,255,255,0.65)" stroke-width="1"/>
    </pattern>
    <pattern id="structureFillHatchLight" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="8" stroke="rgba(0,0,0,0.55)" stroke-width="1"/>
    </pattern>
  `;
  svg.appendChild(defs);
}

function appendStructureAssignmentHatch(svg, obj, st) {
  if (st !== 'fill') return;
  const hatch = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  hatch.setAttribute('d', obj.d);
  if (obj.tx || obj.ty) hatch.setAttribute('transform', `translate(${obj.tx},${obj.ty})`);
  hatch.setAttribute('fill-rule', 'evenodd');
  hatch.setAttribute('clip-rule', 'evenodd');
  hatch.setAttribute('fill', assignmentLuminance(obj.color) > 160 ? 'url(#structureFillHatchLight)' : 'url(#structureFillHatchDark)');
  hatch.setAttribute('pointer-events', 'none');
  svg.appendChild(hatch);
}


function applyStructureObjectPanelState() {
  const panel = document.getElementById('structure-object-panel');
  if (!panel) return;
  const expanded = !structureObjectListCollapsed;
  panel.style.width = expanded ? '340px' : '42px';
  document.querySelectorAll('.structure-list-expanded-only').forEach(el => {
    el.style.display = expanded ? '' : 'none';
  });
  const btn = document.getElementById('structure-object-panel-toggle');
  if (btn) {
    btn.textContent = expanded ? '×' : '☰';
    btn.title = expanded ? 'Collapse embroidery object list' : 'Expand embroidery object list';
  }
}

function toggleStructureObjectPanel() {
  structureObjectListCollapsed = !structureObjectListCollapsed;
  applyStructureObjectPanelState();
  setWorkZoom(workZoom);
}

function setToolButtonActive(id, on) {
  const btn = document.getElementById(id);
  if (!btn) return;
  btn.classList.toggle('primary', !!on);
}

function updateStructureToolButtons() {
  setToolButtonActive('tool-select-btn', structureActiveTool === 'select');
  setToolButtonActive('manual-split-btn', structureActiveTool === 'manual_split');
  setToolButtonActive('junction-cut-btn', structureActiveTool === 'junction');
  setToolButtonActive('assign-fill-tool-btn', structureActiveTool === 'assign_fill');
  setToolButtonActive('assign-satin-tool-btn', structureActiveTool === 'assign_satin');
  setToolButtonActive('assign-skip-tool-btn', structureActiveTool === 'assign_skip');

  const splitBtn = document.getElementById('manual-split-btn');
  if (splitBtn) splitBtn.textContent = structureActiveTool === 'manual_split' ? 'Manual split: active' : 'Manual split tool';
  const junctionBtn = document.getElementById('junction-cut-btn');
  if (junctionBtn) junctionBtn.textContent = structureActiveTool === 'junction' ? 'Junction cut: active' : 'Junction cut tool';
}

function setStructureTool(tool) {
  if (!structureLoaded && tool !== 'select') {
    toast('Load a traced SVG first');
    return;
  }
  if ((tool === 'manual_split' || tool === 'junction') && structureSelectedObjects().length > 1) {
    toast('Cut tools work on one object at a time. Clear multi-selection/checkmarks first.');
    return;
  }

  structureActiveTool = tool || 'select';
  structureSplitMode = structureActiveTool === 'manual_split';
  structureJunctionMode = structureActiveTool === 'junction';
  structureSplitTargetReady = false;
  structureCutPoints = [];
  structureJunctionPoints = [];
  structureHoverPoint = null;
  structureNeedSecondCut = false;

  updateStructureToolButtons();
  renderStructurePreview();
  updateStructureDetail();

  const labels = {
    select: 'Select tool: click an already selected object again to cycle Fill → Satin → Skip',
    manual_split: 'Manual split tool active: first click chooses the path, next two clicks place the cut. Tool stays active after each cut.',
    junction: 'Junction cut tool active: click centre, click each branch, then double-click or Apply.',
    assign_fill: 'Fill tool active: click paths to mark Fill',
    assign_satin: 'Satin tool active: click paths to mark Satin',
    assign_skip: 'Skip tool active: click paths to mark Skip'
  };
  toast(labels[structureActiveTool] || 'Tool selected');
}

function currentAssignToolType() {
  if (structureActiveTool === 'assign_fill') return 'fill';
  if (structureActiveTool === 'assign_satin') return 'satin';
  if (structureActiveTool === 'assign_skip') return 'skip';
  return null;
}

function applyStructureAssignmentClick(obj, shiftKey=false) {
  if (!obj) return;
  if (shiftKey) {
    toggleStructureChecked(obj.id);
    structureSelectedId = obj.id;
    return;
  }

  const toolType = currentAssignToolType();
  if (toolType) {
    stitchAssignments[obj.id] = toolType;
    structureSelectedId = obj.id;
    structureCheckedIds.clear();
    refreshAssignmentViews();
    toast('Assigned ' + toolType + ' to ' + (obj.label || obj.id));
    return;
  }

  const noMulti = structureCheckedIds.size === 0;
  if (structureActiveTool === 'select' && obj.id === structureSelectedId && noMulti) {
    stitchAssignments[obj.id] = cycleStitchType(stitchAssignments[obj.id] || defaultStitchType(obj));
    refreshAssignmentViews();
    toast('Changed selected object to ' + (stitchAssignments[obj.id] || 'fill'));
    return;
  }

  selectStructureObject(obj.id);
}

async function loadStructure() {
  if (!lastTrace || !lastTrace.output_path) {
    toast('Run Trace first');
    showPane(2);
    return;
  }
  try {
    const res = await fetch('/api/structure/load');
    const data = await res.json();
    if (!data.ok) {
      toast('Structure load failed: ' + (data.error || 'unknown'), 7000);
      console.error(data.trace);
      return;
    }
    structureLoaded = true;
    structureSvgW = data.svg_w;
    structureSvgH = data.svg_h;
    structureSourcePaths = data.source_paths || [];
    structureObjects = (data.objects || []).sort((a,b) => (a.order||0) - (b.order||0));
    structureSelectedId = structureObjects.length ? structureObjects[0].id : null;
    structureCheckedIds = new Set();

    // Pane 3 owns Fill/Satin/Skip assignment. On the first load/current-trace
    // load, run Auto Guess once. Returning from Pane 4 does not reload Pane 3,
    // so manual edits are preserved.
    stitchAssignments = {};
    const structureAutoResult = autoAssignImproved(structureObjects);
    Object.keys(structureAutoResult.assignments || {}).forEach(id => {
      stitchAssignments[id] = structureAutoResult.assignments[id];
    });

    updateStructureToolButtons();
    renderStructureList();
    renderStructurePreview();
  ensurePreviewBgButtons();
    updateStructureDetail();
    invalidateStitchPane();
    toast(
      'Path structure loaded' +
      (structureAutoResult.edgeSkipped ? ` · edge/background skipped ${structureAutoResult.edgeSkipped}` : '') +
      (structureAutoResult.columnsConverted ? ` · columns to satin ${structureAutoResult.columnsConverted}` : '') +
      (structureAutoResult.enclosedLargeRegionsForcedFill ? ` · enclosed large regions to fill ${structureAutoResult.enclosedLargeRegionsForcedFill} @ ${Math.round(autoAssignEnclosedFillThreshold() * 100)}%` : '')
    );
  } catch (e) {
    toast('Structure error: ' + e, 7000);
  }
}


function objectGroupKey(obj) {
  return obj.group_id || ('src_' + obj.source_id);
}

function buildGroupedObjectRows(objects) {
  const groups = new Map();
  objects.slice().sort((a,b) => (a.order||0) - (b.order||0)).forEach(obj => {
    const key = objectGroupKey(obj);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(obj);
  });
  return Array.from(groups.entries()).map(([groupId, members]) => {
    members.sort((a,b) => (a.order||0) - (b.order||0));
    return {groupId, members, first: members[0]};
  }).sort((a,b) => (a.first.order||0) - (b.first.order||0));
}

function groupParentLabel(members) {
  if (!members.length) return 'Group';
  const sourceSet = [...new Set(members.map(o => o.display_index))].sort((a,b) => a - b);
  if (sourceSet.length === 1) return 'Path ' + sourceSet[0];
  return 'Group: Paths ' + sourceSet.join(', ');
}

function childLetter(index) {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz';
  if (index < alphabet.length) return alphabet[index];
  return 'p' + (index + 1);
}

function childLabel(parentLabel, index) {
  return parentLabel + childLetter(index);
}

function groupCheckedState(members, checkedSet) {
  const checked = members.filter(o => checkedSet.has(o.id)).length;
  if (checked === 0) return '';
  if (checked === members.length) return 'checked';
  return 'indeterminate';
}

function setCheckboxIndeterminate(input, state) {
  if (state === 'indeterminate') input.indeterminate = true;
}

function structureSelectedObjects() {
  const ids = structureCheckedIds.size ? Array.from(structureCheckedIds) : (structureSelectedId ? [structureSelectedId] : []);
  return ids.map(id => structureObjects.find(o => o.id === id)).filter(Boolean);
}

function renderStructureList() {
  const list = document.getElementById('structure-list');
  const meta = document.getElementById('structure-count');
  applyStructureObjectPanelState();
  list.innerHTML = '';
  meta.textContent = structureObjects.length + ' object' + (structureObjects.length === 1 ? '' : 's');

  buildGroupedObjectRows(structureObjects).forEach(group => {
    const members = group.members;
    const parentLabel = groupParentLabel(members);
    const isGrouped = members.length > 1;
    const groupState = groupCheckedState(members, structureCheckedIds);
    const groupSelected = members.some(o => o.id === structureSelectedId || structureCheckedIds.has(o.id));
    const collapsed = structureCollapsedGroups.has(group.groupId);

    if (isGrouped) {
      const parent = document.createElement('div');
      parent.className = 'obj-row obj-parent' + (groupSelected ? ' sel' : '');
      parent.onclick = (ev) => {
        if (ev.shiftKey) toggleStructureGroup(group.groupId);
        else selectStructureGroup(group.groupId);
      };
      parent.innerHTML = `
        <button class="collapse-toggle" title="${collapsed ? 'Expand group' : 'Collapse group'}" onclick="event.stopPropagation();toggleStructureGroupCollapse('${group.groupId}')">${collapsed ? '+' : '−'}</button>
        <input type="checkbox" ${groupState === 'checked' ? 'checked' : ''} onclick="event.stopPropagation();toggleStructureGroup('${group.groupId}')">
        <div class="obj-swatch" style="background:${members[0].color}"></div>
        <div class="obj-info">
          <div class="obj-name">${parentLabel}</div>
          <div class="obj-meta">${members.length} grouped child paths · group ${group.groupId}</div>
        </div>
        <span class="obj-group-count">${members.length} parts</span>
      `;
      const cb = parent.querySelector('input[type=checkbox]');
      setCheckboxIndeterminate(cb, groupState);
      list.appendChild(parent);
    }

    members.forEach((obj, idx) => {
      const row = document.createElement('div');
      row.className = 'obj-row' + (isGrouped ? ' obj-child' : '') + (obj.id === structureSelectedId ? ' sel' : '') + ((isGrouped && collapsed) ? ' hidden-child' : '');
      row.onclick = (ev) => {
        if (ev.shiftKey) {
          toggleStructureChecked(obj.id);
          structureSelectedId = obj.id;
        } else {
          selectStructureObject(obj.id);
        }
      };
      const displayName = isGrouped ? childLabel(parentLabel, idx) : obj.label;
      row.innerHTML = `
        ${!isGrouped ? '<span style="width:22px;display:inline-block"></span>' : ''}
        <input type="checkbox" ${structureCheckedIds.has(obj.id) ? 'checked' : ''} onclick="event.stopPropagation();toggleStructureChecked('${obj.id}')">
        <div class="obj-swatch" style="background:${obj.color}"></div>
        <div class="obj-info">
          <div class="obj-name">${displayName} <span style="color:#8f96b3;font-size:.72rem">${obj.color}</span></div>
          <div class="obj-meta">source path ${obj.display_index} · ${obj.source_kind || 'object'} · group ${obj.group_id} · ratio ${obj.elongation}<br>${obj.prep_note || 'working object'}</div>
        </div>
        <span class="obj-badge">${isGrouped ? childLetter(idx) : obj.group_id}</span>
      `;
      list.appendChild(row);
    });
  });
}

function selectStructureObject(id) {
  structureSelectedId = id;
  structureCheckedIds.clear();
  structureSplitTargetReady = structureSplitMode;
  structureCutPoints = [];
  structureHoverPoint = null;
  structureNeedSecondCut = false;
  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
  if (structureSplitMode) toast('Manual split target selected: now place first cut point');
}

function toggleStructureChecked(id) {
  if (structureCheckedIds.has(id)) structureCheckedIds.delete(id);
  else structureCheckedIds.add(id);
  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
}

function selectStructureGroup(groupId) {
  const members = structureObjects.filter(o => objectGroupKey(o) === groupId);
  if (!members.length) return;
  structureSelectedId = members[0].id;
  structureCheckedIds = new Set(members.map(o => o.id));
  structureCutPoints = [];
  structureHoverPoint = null;
  structureNeedSecondCut = false;
  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
}

function toggleStructureGroup(groupId) {
  const members = structureObjects.filter(o => objectGroupKey(o) === groupId);
  if (!members.length) return;
  const allChecked = members.every(o => structureCheckedIds.has(o.id));
  if (allChecked) members.forEach(o => structureCheckedIds.delete(o.id));
  else members.forEach(o => structureCheckedIds.add(o.id));
  structureSelectedId = members[0].id;
  structureCutPoints = [];
  structureHoverPoint = null;
  structureNeedSecondCut = false;
  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
}

function toggleStructureGroupCollapse(groupId) {
  if (structureCollapsedGroups.has(groupId)) structureCollapsedGroups.delete(groupId);
  else structureCollapsedGroups.add(groupId);
  renderStructureList();
}

function structureSvgMarkup() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${structureSvgW}" height="${structureSvgH}" viewBox="0 0 ${structureSvgW} ${structureSvgH}"></svg>`;
}

function renderStructurePreview() {
  const wrap = document.getElementById('structure-preview');
  if (!structureObjects.length) {
    wrap.innerHTML = '<span style="color:#555">No traced SVG loaded</span>';
    return;
  }
  wrap.innerHTML = structureSvgMarkup();
  const svg = wrap.querySelector('svg');
  svg.style.maxWidth = '100%';
  svg.style.maxHeight = '75vh';
  svg.style.width = 'auto';
  svg.style.height = 'auto';

  const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  bg.setAttribute('x', '0');
  bg.setAttribute('y', '0');
  bg.setAttribute('width', String(structureSvgW));
  bg.setAttribute('height', String(structureSvgH));
  bg.setAttribute('fill', 'transparent');
  svg.appendChild(bg);
  appendAssignmentDefs(svg);

  const selectedGroupIds = new Set();
  if (structureSelectedId) {
    const s = structureObjects.find(o => o.id === structureSelectedId);
    if (s && s.group_id) selectedGroupIds.add(s.group_id);
  }
  structureCheckedIds.forEach(id => {
    const o = structureObjects.find(x => x.id === id);
    if (o && o.group_id) selectedGroupIds.add(o.group_id);
  });

  const clickHandler = (ev) => {
    if (!structureSplitMode && !structureJunctionMode) return;
    ev.stopPropagation();
    const p = svgPointFromMouse(svg, ev);

    if (structureSplitMode && !structureSplitTargetReady) {
      toast('Manual split: click the path you want to cut first');
      return;
    }

    if (structureJunctionMode) {
      structureJunctionPoints.push([p.x, p.y]);
      structureHoverPoint = null;
      renderStructurePreview();
      updateStructureDetail();

      if (structureJunctionPoints.length === 1) {
        toast('Junction centre set. Click each branch direction, then double-click or Apply junction cut.');
      } else {
        toast((structureJunctionPoints.length - 1) + ' branch point(s). Need at least 3; double-click or Apply to finish.');
      }
      return;
    }

    structureCutPoints.push([p.x, p.y]);

    if (structureCutPoints.length === 1) {
      structureHoverPoint = [p.x, p.y];
      renderStructurePreview();
      updateStructureDetail();
      toast('Pick second point for the first cut');
    } else if (structureCutPoints.length === 2) {
      structureHoverPoint = null;
      renderStructurePreview();
      updateStructureDetail();
      applyManualSplit();
    } else if (structureCutPoints.length === 3) {
      structureHoverPoint = [p.x, p.y];
      renderStructurePreview();
      updateStructureDetail();
      toast('Pick fourth point for the second cut');
    } else if (structureCutPoints.length === 4) {
      structureHoverPoint = null;
      renderStructurePreview();
      updateStructureDetail();
      applyManualSplit();
    } else {
      structureCutPoints = [];
      structureHoverPoint = null;
      structureNeedSecondCut = false;
      renderStructurePreview();
    }
  };

  const moveHandler = (ev) => {
    if (!structureSplitMode && !structureJunctionMode) return;
    if (structureSplitMode && (!structureSplitTargetReady || ![1, 3].includes(structureCutPoints.length))) return;
    if (structureJunctionMode && structureJunctionPoints.length < 1) return;
    const p = svgPointFromMouse(svg, ev);
    structureHoverPoint = [p.x, p.y];
    renderStructurePreview();
  };

  bg.addEventListener('click', clickHandler);
  bg.addEventListener('dblclick', (ev) => {
    if (structureJunctionMode) {
      ev.stopPropagation();
      ev.preventDefault();
      applyJunctionCut();
    }
  });
  bg.addEventListener('mousemove', moveHandler);

  structureObjects.sort((a,b) => (a.order||0) - (b.order||0)).forEach(obj => {
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', obj.d);
    if (obj.tx || obj.ty) p.setAttribute('transform', `translate(${obj.tx},${obj.ty})`);
    p.style.cursor = (structureSplitMode || structureJunctionMode || currentAssignToolType()) ? 'crosshair' : 'pointer';

    const st = stitchAssignments[obj.id] || defaultStitchType(obj);
    if ((obj.render_mode || 'fill') === 'stroke') {
      p.setAttribute('fill', 'none');
      p.setAttribute('stroke', obj.color);
      p.setAttribute('stroke-width', String(obj.stroke_width || 1.6));
      p.setAttribute('stroke-linecap', 'round');
      p.setAttribute('stroke-linejoin', 'round');
      if (st === 'skip') p.setAttribute('stroke-opacity', '0.22');
    } else {
      p.setAttribute('fill', obj.color);
      p.setAttribute('fill-rule', 'evenodd');
      p.setAttribute('clip-rule', 'evenodd');
      if (st === 'skip') {
        p.setAttribute('fill-opacity', '0.18');
        p.setAttribute('stroke', obj.color);
        p.setAttribute('stroke-opacity', '0.25');
        p.setAttribute('stroke-width', '0.8');
      } else if (st === 'satin') {
        p.setAttribute('fill-opacity', '1');
        p.setAttribute('stroke', obj.color);
        p.setAttribute('stroke-width', '0.6');
        p.setAttribute('stroke-opacity', '0.9');
      }
    }

    if (selectedGroupIds.has(obj.group_id)) {
      if ((obj.render_mode || 'fill') === 'stroke') {
        p.setAttribute('stroke', '#ff5f7c');
        p.setAttribute('stroke-width', String((obj.stroke_width || 1.6) + (obj.id === structureSelectedId ? 1.8 : 1.0)));
        p.setAttribute('stroke-opacity', obj.id === structureSelectedId ? '0.95' : '0.72');
      } else {
        p.setAttribute('stroke', '#e94560');
        p.setAttribute('stroke-width', obj.id === structureSelectedId ? '2' : '1.4');
        p.setAttribute('stroke-opacity', obj.id === structureSelectedId ? '0.95' : '0.65');
      }
    } else if (structureCheckedIds.has(obj.id)) {
      p.setAttribute('stroke', '#78a6ff');
      p.setAttribute('stroke-width', (obj.render_mode || 'fill') === 'stroke' ? String((obj.stroke_width || 1.6) + 0.8) : '1');
      p.setAttribute('stroke-opacity', '0.7');
    }

    p.addEventListener('mousemove', moveHandler);
    p.addEventListener('dblclick', (ev) => {
      if (structureJunctionMode) {
        ev.stopPropagation();
        ev.preventDefault();
        applyJunctionCut();
      }
    });
    p.addEventListener('click', (ev) => {
      if (structureSplitMode) {
        ev.stopPropagation();

        // First click after choosing Manual Split selects the target only.
        // After that, every click anywhere in the preview, including over an
        // adjacent path or inside the target path, is a cut point.
        if (!structureSplitTargetReady) {
          structureSelectedId = obj.id;
          structureCheckedIds.clear();
          structureSplitTargetReady = true;
          structureCutPoints = [];
          structureHoverPoint = null;
          structureNeedSecondCut = false;
          renderStructureList();
          renderStructurePreview();
          updateStructureDetail();
          toast('Manual split target selected: now place first cut point');
          return;
        }

        clickHandler(ev);
        return;
      }

      if (structureJunctionMode) {
        if (structureJunctionPoints.length === 0) {
          structureSelectedId = obj.id;
          structureCheckedIds.clear();
        }
        clickHandler(ev);
        return;
      }

      ev.stopPropagation();
      applyStructureAssignmentClick(obj, ev.shiftKey);
    });
    svg.appendChild(p);
    appendStructureAssignmentHatch(svg, obj, st);
  });

  const drawPoint = (pt, fill) => {
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('cx', String(pt[0]));
    c.setAttribute('cy', String(pt[1]));
    c.setAttribute('r', structureJunctionMode ? '3.2' : '2');
    c.setAttribute('fill', fill);
    c.setAttribute('stroke', '#111');
    c.setAttribute('stroke-width', '0.6');
    svg.appendChild(c);
  };

  const drawLine = (a, b, color) => {
    const ln = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    ln.setAttribute('x1', String(a[0]));
    ln.setAttribute('y1', String(a[1]));
    ln.setAttribute('x2', String(b[0]));
    ln.setAttribute('y2', String(b[1]));
    ln.setAttribute('stroke', color);
    ln.setAttribute('stroke-width', '2');
    ln.setAttribute('stroke-dasharray', '6 4');
    svg.appendChild(ln);
  };

  if (structureCutPoints.length >= 1) drawPoint(structureCutPoints[0], '#ffd166');
  if (structureCutPoints.length >= 2) {
    drawPoint(structureCutPoints[1], '#06d6a0');
    drawLine(structureCutPoints[0], structureCutPoints[1], '#ffd166');
  }
  if (structureCutPoints.length >= 3) drawPoint(structureCutPoints[2], '#7bdff2');
  if (structureCutPoints.length >= 4) {
    drawPoint(structureCutPoints[3], '#f15bb5');
    drawLine(structureCutPoints[2], structureCutPoints[3], '#7bdff2');
  }

  if (structureJunctionPoints.length >= 1) {
    const centre = structureJunctionPoints[0];
    drawPoint(centre, '#ff00ff');
    for (let i = 1; i < structureJunctionPoints.length; i++) {
      drawPoint(structureJunctionPoints[i], '#00e5ff');
      drawLine(centre, structureJunctionPoints[i], '#00e5ff');
    }
    if (structureJunctionMode && structureHoverPoint) {
      drawLine(centre, structureHoverPoint, '#ff00ff');
    }
  }

  if (structureSplitMode && structureHoverPoint) {
    if (structureCutPoints.length === 1) {
      drawLine(structureCutPoints[0], structureHoverPoint, '#ffd166');
    } else if (structureCutPoints.length === 3) {
      drawLine(structureCutPoints[2], structureHoverPoint, '#7bdff2');
    }
  }

  setWorkZoom(workZoom);
  document.getElementById('structure-preview-meta').textContent =
    structureObjects.length + ' objects' +
    (structureJunctionMode ? (' · junction points ' + structureJunctionPoints.length) : '');
}
function updateStructureDetail() {
  const detail = document.getElementById('structure-detail');
  const objs = structureSelectedObjects();
  if (!objs.length) {
    detail.innerHTML = 'Load a traced SVG to begin.';
    return;
  }
  if (objs.length > 1) {
    detail.innerHTML = `
      Selected objects: <b>${objs.length}</b><br>
      Sources: <b>${[...new Set(objs.map(o => o.display_index))].join(', ')}</b><br>
      Assignments: <b>${[...new Set(objs.map(o => stitchAssignments[o.id] || defaultStitchType(o)))].join(', ')}</b><br>
      Groups: <b>${[...new Set(objs.map(o => o.group_id))].join(', ')}</b>
    `;
    return;
  }
  const o = objs[0];
  detail.innerHTML = `
    Object: <b>${o.label}</b><br>
    Source path: <b>${o.display_index}</b><br>
    Colour: <b>${o.color}</b><br>
    Assignment: <b>${stitchAssignments[o.id] || defaultStitchType(o)}</b><br>
    Group: <b>${o.group_id}</b><br>
    Ratio: <b>${o.elongation}</b><br>
    Kind: <b>${o.source_kind || 'object'}</b><br>
    Mode: <b>${o.render_mode || 'fill'}</b><br>
    Note: <b>${o.prep_note}</b><br>
    ${structureSplitMode ? `Manual split active · ${structureSplitTargetReady ? 'cut target selected' : 'choose target path'} · points placed: <b>${structureCutPoints.length}</b>${structureNeedSecondCut ? '/4' : '/2'}<br>Selected object: <b>${o.label}</b><br>${!structureSplitTargetReady ? 'Click the path once to choose it as the cut target.' : (structureNeedSecondCut ? 'First cut is registered. Place two more points for the second cut.' : 'Place both cut points outside this selected shape on opposite sides.')}` : ''}
  `;
}

function replaceSourceObjects(sourceId, replacementObjects) {
  structureObjects = structureObjects.filter(o => o.source_id !== sourceId);
  replacementObjects.forEach(o => {
    if (!o.group_id) o.group_id = 'src_' + sourceId;
  });
  structureObjects = structureObjects.concat(JSON.parse(JSON.stringify(replacementObjects)));
  structureObjects.sort((a,b) => (a.order||0) - (b.order||0));
}

function splitSelectedSource() {
  if (!structureLoaded) { toast('Load a traced SVG first'); return; }
  const selected = structureSelectedObjects();
  if (!selected.length) { toast('Select an object first'); return; }

  const sourceIds = [...new Set(selected.map(o => o.source_id))];
  let changed = 0;
  sourceIds.forEach(sid => {
    const src = structureSourcePaths.find(s => s.source_id === sid);
    if (src && src.split_parts && src.split_parts.length > 1) {
      replaceSourceObjects(sid, src.split_parts);
      changed++;
    }
  });

  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
  if (changed) {
    toast('Best-guess source split applied');
  } else {
    toast('No safe split found. This is likely one fused path or a preserved ring/hole compound path.');
  }
}

function restoreSelectedSource() {
  if (!structureLoaded) { toast('Load a traced SVG first'); return; }
  const selected = structureSelectedObjects();
  if (!selected.length) { toast('Select an object first'); return; }

  const sourceIds = [...new Set(selected.map(o => o.source_id))];
  sourceIds.forEach(sid => {
    const src = structureSourcePaths.find(s => s.source_id === sid);
    if (src) {
      const restoreObj = {
        id: 's' + src.source_id,
        source_id: src.source_id,
        display_index: src.display_index,
        label: 'Path ' + src.display_index,
        d: src.d,
        tx: src.tx,
        ty: src.ty,
        color: src.color,
        group_id: 'src_' + src.source_id,
        part_index: 0,
        part_count: 1,
        prep_note: src.prep_note || 'original source path',
        elongation: src.elongation,
        order: src.order || src.source_id,
        hidden: false,
        render_mode: src.render_mode || 'fill',
        stroke_width: src.stroke_width || 1.6,
        source_kind: src.source_kind || 'fill_region'
      };
      replaceSourceObjects(sid, [restoreObj]);
    }
  });

  structureCheckedIds.clear();
  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
  invalidateStitchPane();
  toast('Selected source restored');
}

function groupSelectedObjects() {
  if (!structureLoaded) { toast('Load a traced SVG first'); return; }
  const selected = structureSelectedObjects();
  if (selected.length < 2) { toast('Select at least 2 objects to group'); return; }

  const gid = 'grp_' + (structureGroupCounter++);
  const ids = new Set(selected.map(o => o.id));
  structureObjects.forEach(o => { if (ids.has(o.id)) o.group_id = gid; });

  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
  invalidateStitchPane();
  toast('Grouped ' + selected.length + ' objects');
}

function ungroupSelectedObjects() {
  if (!structureLoaded) { toast('Load a traced SVG first'); return; }
  const selected = structureSelectedObjects();
  if (!selected.length) { toast('Select object(s) first'); return; }

  const ids = new Set(selected.map(o => o.id));
  structureObjects.forEach(o => { if (ids.has(o.id)) o.group_id = 'src_' + o.source_id; });

  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
  invalidateStitchPane();
  toast('Ungrouped selected objects');
}


function toggleManualSplitMode() {
  setStructureTool(structureActiveTool === 'manual_split' ? 'select' : 'manual_split');
}

function toggleJunctionCutMode() {
  setStructureTool(structureActiveTool === 'junction' ? 'select' : 'junction');
}

async function applyJunctionCut() {
  const objs = structureSelectedObjects();
  if (objs.length !== 1) {
    toast('Select exactly one object for junction cut');
    cancelManualSplit();
    return;
  }
  if (structureJunctionPoints.length < 4) {
    toast('Junction cut needs a centre point plus at least three branch points');
    return;
  }

  try {
    const center = structureJunctionPoints[0];
    const branch_points = structureJunctionPoints.slice(1);
    const res = await fetch('/api/structure/junction_split', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({object: objs[0], center, branch_points})
    });
    const data = await res.json();

    if (!data.ok) {
      toast('Junction cut failed: ' + (data.error || 'unknown'), 9000);
      console.error(data.trace);
      return;
    }

    const selected = objs[0];
    const selectedAssignment = stitchAssignments[selected.id] || defaultStitchType(selected);
    structureObjects = structureObjects.filter(o => o.id !== selected.id);
    delete stitchAssignments[selected.id];
    data.objects.forEach((o, i) => {
      if (!o.id) o.id = selected.id + '_jcut' + (i + 1);
      o.group_id = selected.group_id || ('src_' + selected.source_id);
      o.source_id = selected.source_id;
      o.display_index = selected.display_index;
      o.color = o.color || selected.color;
      o.render_mode = o.render_mode || selected.render_mode || 'fill';
      o.stroke_width = o.stroke_width || selected.stroke_width || 1.6;
      o.source_kind = o.source_kind || selected.source_kind || 'fill_region';
      o.cut_guide_rungs = o.cut_guide_rungs || [];
      stitchAssignments[o.id] = selectedAssignment;
    });

    structureObjects = structureObjects.concat(data.objects).sort((a,b) => (a.order||0) - (b.order||0));
    structureSelectedId = data.objects[0].id;
    structureCheckedIds.clear();
    structureJunctionPoints = [];
    structureHoverPoint = null;
    updateStructureToolButtons();
    renderStructureList();
    renderStructurePreview();
    updateStructureDetail();
    invalidateStitchPane();
    toast('Junction cut created ' + data.objects.length + ' object(s)' +
      ((data.cut_guide_rungs || 0) ? (' with ' + data.cut_guide_rungs + ' cut guide rung(s)') : ''));
  } catch (e) {
    toast('Junction cut error: ' + e, 9000);
  }
}

function cancelManualSplit() {
  structureSplitTargetReady = false;
  structureCutPoints = [];
  structureJunctionPoints = [];
  structureHoverPoint = null;
  structureNeedSecondCut = false;
  updateStructureToolButtons();
  renderStructurePreview();
  updateStructureDetail();
}
function svgPointFromMouse(svg, evt) {
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX;
  pt.y = evt.clientY;
  const p = pt.matrixTransform(svg.getScreenCTM().inverse());
  return {x: p.x, y: p.y};
}

async function applyManualSplit() {
  const objs = structureSelectedObjects();
  if (objs.length !== 1) {
    toast('Select exactly one object for manual split');
    cancelManualSplit();
    return;
  }
  if (![2, 4].includes(structureCutPoints.length)) {
    toast(structureNeedSecondCut ? 'Pick two more cut points for the second cut' : 'Pick two cut points');
    return;
  }
  try {
    const res = await fetch('/api/structure/manual_split', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({object: objs[0], cut_points: structureCutPoints})
    });
    const data = await res.json();

    if (!data.ok && data.needs_second_cut) {
      structureSplitTargetReady = true;
      structureNeedSecondCut = true;
      structureHoverPoint = null;
      renderStructurePreview();
      updateStructureDetail();
      toast('First cut registered. This shape needs a second cut: place two more points for the second cut.', 9000);
      return;
    }

    if (!data.ok) {
      structureSplitTargetReady = false;
      structureCutPoints = [];
      structureHoverPoint = null;
      structureNeedSecondCut = false;
      renderStructurePreview();
      updateStructureDetail();
      toast('Manual split failed: ' + (data.error || 'unknown') + '  Try again on the selected object.', 9000);
      console.error(data.trace);
      return;
    }

    const selected = objs[0];
    const selectedAssignment = stitchAssignments[selected.id] || defaultStitchType(selected);
    structureObjects = structureObjects.filter(o => o.id !== selected.id);
    delete stitchAssignments[selected.id];
    data.objects.forEach((o, i) => {
      if (!o.id) o.id = selected.id + '_cut' + (i + 1);
      o.group_id = selected.group_id || ('src_' + selected.source_id);
      o.source_id = selected.source_id;
      o.display_index = selected.display_index;
      o.color = o.color || selected.color;
      o.render_mode = o.render_mode || selected.render_mode || 'fill';
      o.stroke_width = o.stroke_width || selected.stroke_width || 1.6;
      o.source_kind = o.source_kind || selected.source_kind || 'fill_region';
      o.cut_guide_rungs = o.cut_guide_rungs || [];
      stitchAssignments[o.id] = selectedAssignment;
    });
    structureObjects = structureObjects.concat(data.objects).sort((a,b) => (a.order||0) - (b.order||0));
    structureSelectedId = data.objects[0].id;
    structureCheckedIds.clear();
    structureSplitTargetReady = false;
    structureCutPoints = [];
    structureHoverPoint = null;
    structureNeedSecondCut = false;
    updateStructureToolButtons();
    renderStructureList();
    renderStructurePreview();
    updateStructureDetail();
    invalidateStitchPane();
    toast('Manual split created ' + data.objects.length + ' object(s)' +
      ((data.cut_guide_rungs || 0) ? (' with ' + data.cut_guide_rungs + ' cut guide rung(s)') : '') +
      '. Choose the next path to cut.');
  } catch (e) {
    structureSplitTargetReady = false;
    structureCutPoints = [];
    structureHoverPoint = null;
    structureNeedSecondCut = false;
    renderStructurePreview();
    updateStructureDetail();
    toast('Manual split error: ' + e + '  Cut points cleared; try again.', 9000);
  }
}
function saveStructureJson() {
  if (!structureLoaded) { toast('Load a traced SVG first'); return; }
  const payload = {
    version: 1,
    source_svg: lastTrace ? lastTrace.output_path : null,
    svg_w: structureSvgW,
    svg_h: structureSvgH,
    source_paths: structureSourcePaths,
    objects: structureObjects
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'easystitch_structure.json';
  a.click();
  URL.revokeObjectURL(a.href);
}




let autoAssignBBoxSvg = null;

function autoAssignObjectBBox(obj) {
  if (!obj || !obj.d) return null;
  try {
    if (!autoAssignBBoxSvg) {
      autoAssignBBoxSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      autoAssignBBoxSvg.setAttribute('width', '0');
      autoAssignBBoxSvg.setAttribute('height', '0');
      autoAssignBBoxSvg.style.position = 'fixed';
      autoAssignBBoxSvg.style.left = '-10000px';
      autoAssignBBoxSvg.style.top = '-10000px';
      autoAssignBBoxSvg.style.visibility = 'hidden';
      document.body.appendChild(autoAssignBBoxSvg);
    }

    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', obj.d);
    autoAssignBBoxSvg.appendChild(p);
    const b = p.getBBox();

    let len = 0;
    try { len = p.getTotalLength(); } catch (e) { len = 0; }
    p.remove();

    const tx = Number(obj.tx || 0);
    const ty = Number(obj.ty || 0);
    const area = Math.max(0, b.width * b.height);
    const minDim = Math.min(Math.max(0, b.width), Math.max(0, b.height));
    const maxDim = Math.max(Math.max(0, b.width), Math.max(0, b.height));
    const areaPerLength = len > 0 ? area / len : area;

    return {
      x1: b.x + tx,
      y1: b.y + ty,
      x2: b.x + b.width + tx,
      y2: b.y + b.height + ty,
      w: b.width,
      h: b.height,
      area,
      minDim,
      maxDim,
      aspect: minDim > 0 ? maxDim / minDim : 999,
      length: len,
      areaPerLength
    };
  } catch (e) {
    return null;
  }
}

function autoAssignTouchesImageEdge(b, eps=1.5) {
  if (!b) return false;
  return (
    b.x1 <= eps ||
    b.y1 <= eps ||
    b.x2 >= (structureSvgW - eps) ||
    b.y2 >= (structureSvgH - eps)
  );
}

function autoAssignBBoxContains(outer, inner, margin=1.0) {
  if (!outer || !inner) return false;
  return (
    outer.x1 <= inner.x1 + margin &&
    outer.y1 <= inner.y1 + margin &&
    outer.x2 >= inner.x2 - margin &&
    outer.y2 >= inner.y2 - margin
  );
}

function autoAssignIsLikelyColumn(obj, bbox) {
  if (!obj || !bbox) return false;
  if ((obj.render_mode || 'fill') === 'stroke') return true;

  // Use the earlier working elongation signal again, but let the full-list
  // pass below correct large enclosed fills such as the HappySun face back to
  // Fill when they contain satin details.
  if (bbox.area < 12) return false;

  const ratio = parseFloat(obj.elongation || 0);
  const oldElongationColumn = ratio >= 3.0;
  const thinByWidth = bbox.minDim <= 16 && bbox.aspect >= 1.8;
  const thinByAreaLength = bbox.areaPerLength <= 9.0 && bbox.aspect >= 1.2;
  const veryLongNarrow = bbox.aspect >= 3.5 && bbox.minDim <= 24;

  return oldElongationColumn || thinByWidth || thinByAreaLength || veryLongNarrow;
}

function autoAssignBaseType(obj, bbox) {
  // Default is Fill. Then only explicit rules override it.
  if (autoAssignTouchesImageEdge(bbox)) return 'skip';
  if (obj.hidden) return 'skip';
  if (autoAssignIsLikelyColumn(obj, bbox)) return 'satin';
  return 'fill';
}

function autoAssignEnclosedFillThreshold() {
  const el = document.getElementById('auto-enclosed-fill-pct');
  const pct = parseFloat(el?.value || '70');
  return Math.max(0.50, Math.min(0.90, pct / 100.0));
}

let autoAssignAreaCanvas = null;
let autoAssignAreaCtx = null;

function autoAssignEnsureAreaCanvas() {
  if (!autoAssignAreaCanvas) {
    autoAssignAreaCanvas = document.createElement('canvas');
    autoAssignAreaCanvas.width = 128;
    autoAssignAreaCanvas.height = 128;
    autoAssignAreaCtx = autoAssignAreaCanvas.getContext('2d', { willReadFrequently: true });
  }
  return autoAssignAreaCtx;
}

function autoAssignCountFilledPixelsForObject(obj, bounds) {
  if (!obj || !obj.d || !bounds) return 0;
  const ctx = autoAssignEnsureAreaCanvas();
  if (!ctx) return 0;

  const canvas = ctx.canvas;
  const margin = 2;
  const bw = Math.max(1, bounds.x2 - bounds.x1);
  const bh = Math.max(1, bounds.y2 - bounds.y1);
  const usable = Math.max(1, Math.min(canvas.width, canvas.height) - margin * 2);
  const scale = usable / Math.max(bw, bh);

  ctx.save();
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#ffffff';

  try {
    const path = new Path2D(obj.d);
    ctx.translate(margin + ((usable - bw * scale) * 0.5), margin + ((usable - bh * scale) * 0.5));
    ctx.scale(scale, scale);
    ctx.translate(-bounds.x1, -bounds.y1);
    ctx.translate(Number(obj.tx || 0), Number(obj.ty || 0));
    ctx.fill(path, 'nonzero');
  } catch (e) {
    ctx.restore();
    return 0;
  }

  ctx.restore();

  const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  let count = 0;
  for (let i = 3; i < data.length; i += 4) {
    if (data[i] > 0) count += 1;
  }
  return count;
}

function autoAssignRasterAreaRatio(innerObj, outerObj, innerBBox, outerBBox) {
  if (!innerObj || !outerObj || !innerBBox || !outerBBox) return 0;
  const bounds = {
    x1: Math.min(innerBBox.x1, outerBBox.x1),
    y1: Math.min(innerBBox.y1, outerBBox.y1),
    x2: Math.max(innerBBox.x2, outerBBox.x2),
    y2: Math.max(innerBBox.y2, outerBBox.y2),
  };
  const outerPixels = autoAssignCountFilledPixelsForObject(outerObj, bounds);
  if (!outerPixels) return 0;
  const innerPixels = autoAssignCountFilledPixelsForObject(innerObj, bounds);
  return innerPixels / outerPixels;
}

function autoAssignImproved(list) {
  const boxes = new Map();
  list.forEach(o => boxes.set(o.id, autoAssignObjectBBox(o)));

  const assignments = {};
  let edgeSkipped = 0;
  let columnsConverted = 0;
  let enclosedLargeRegionsForcedFill = 0;

  // Pass 1: everything starts as Fill, then hard overrides run.
  list.forEach(o => {
    const b = boxes.get(o.id);
    if (autoAssignTouchesImageEdge(b)) {
      assignments[o.id] = 'skip';
      edgeSkipped += 1;
    } else if (autoAssignIsLikelyColumn(o, b)) {
      assignments[o.id] = 'satin';
      columnsConverted += 1;
    } else {
      assignments[o.id] = 'fill';
    }
  });

  // Pass 2: if an object was changed to Satin by the column detector, but it
  // substantially fills the inside of another satin outline/column, switch it
  // back to Fill.
  //
  // This uses a simple "caveman stone counting" raster estimate:
  // rasterise the enclosing satin object and the inner candidate into a small
  // offscreen canvas (longest side 128 px), count filled pixels for each, then
  // compare the ratio. This behaves much better than bbox percentage on angled
  // shapes such as the HappySun spikes.
  const satinContainers = list.filter(o => assignments[o.id] === 'satin')
    .map(o => ({obj: o, bbox: boxes.get(o.id)}))
    .filter(x => x.bbox && x.bbox.area > 0);

  list.forEach(o => {
    const b = boxes.get(o.id);
    if (!b || assignments[o.id] !== 'satin') return;
    if (autoAssignTouchesImageEdge(b)) return;

    const enclosedAndLarge = satinContainers.some(c => {
      if (!c.obj || c.obj.id === o.id) return false;
      if (!autoAssignBBoxContains(c.bbox, b, 2.0)) return false;
      if (b.area < 16) return false;

      const fillRatio = autoAssignRasterAreaRatio(o, c.obj, b, c.bbox);
      return fillRatio >= autoAssignEnclosedFillThreshold() && fillRatio < 0.985;
    });

    if (enclosedAndLarge) {
      assignments[o.id] = 'fill';
      columnsConverted -= 1;
      enclosedLargeRegionsForcedFill += 1;
    }
  });

  return {assignments, edgeSkipped, columnsConverted, enclosedLargeRegionsForcedFill};
}


function defaultStitchType(obj) {
  return autoAssignBaseType(obj, autoAssignObjectBBox(obj));
}

function loadStitchPane() {
  if (!structureObjects.length) {
    toast('Prepare paths in Pane 3 first');
    showPane(3);
    return;
  }
  stitchObjects = JSON.parse(JSON.stringify(structureObjects)).sort((a,b) => (a.order||0) - (b.order||0));
  // Pane 4 is read-only for Fill/Satin/Skip assignments. It never auto-assigns
  // or changes stitchAssignments; it only previews, tunes, and exports.
  stitchSelectedId = stitchObjects.length ? stitchObjects[0].id : null;
  stitchCheckedIds = new Set();
  stitchCollapsedGroups = new Set();
  stitchManualRungs = {};
  currentStitchPlan = null;
  currentStitchPreview = null;
  stitchPlanPlayIndex = 0;
  if (stitchPlanPlayTimer) { clearInterval(stitchPlanPlayTimer); stitchPlanPlayTimer = null; }
  previewLayerMode = 'both';
  manualRungMode = false;
  pendingManualRungPoint = null;
  draggingManualRung = null;
  stitchSortMode = 'number';
  stitchLoaded = true;
  renderStitchList();
  renderStitchPreview();
  ensurePreviewBgButtons();
  updateStitchDetail();
  updateStitchSummary();
  updateDesignSizeInfo();
  toast('Prepared stitch map loaded from Pane 3 assignments');
  setTimeout(() => previewStitches(), 80);
}

function stitchSelectedObjects() {
  const ids = stitchCheckedIds.size ? Array.from(stitchCheckedIds) : (stitchSelectedId ? [stitchSelectedId] : []);
  return ids.map(id => stitchObjects.find(o => o.id === id)).filter(Boolean);
}

function selectStitchObject(id) {
  stitchSelectedId = id;
  stitchCheckedIds.clear();
  pendingManualRungPoint = null;
  draggingManualRung = null;
  renderStitchList();
  renderStitchPreview();
  updateStitchDetail();
  updateManualRungStatus();
}

function toggleStitchChecked(id) {
  if (stitchCheckedIds.has(id)) stitchCheckedIds.delete(id);
  else stitchCheckedIds.add(id);
  renderStitchList();
  renderStitchPreview();
  updateStitchDetail();
}

function selectStitchGroup(groupId) {
  const members = stitchObjects.filter(o => objectGroupKey(o) === groupId);
  if (!members.length) return;
  stitchSelectedId = members[0].id;
  stitchCheckedIds = new Set(members.map(o => o.id));
  renderStitchList();
  renderStitchPreview();
  updateStitchDetail();
}

function toggleStitchGroup(groupId) {
  const members = stitchObjects.filter(o => objectGroupKey(o) === groupId);
  if (!members.length) return;
  const allChecked = members.every(o => stitchCheckedIds.has(o.id));
  if (allChecked) members.forEach(o => stitchCheckedIds.delete(o.id));
  else members.forEach(o => stitchCheckedIds.add(o.id));
  stitchSelectedId = members[0].id;
  renderStitchList();
  renderStitchPreview();
  updateStitchDetail();
}

function toggleStitchGroupCollapse(groupId) {
  if (stitchCollapsedGroups.has(groupId)) stitchCollapsedGroups.delete(groupId);
  else stitchCollapsedGroups.add(groupId);
  renderStitchList();
}

function groupAssignmentSummary(members) {
  const vals = [...new Set(members.map(o => stitchAssignments[o.id] || 'fill'))];
  return vals.length === 1 ? vals[0] : 'mixed';
}


function cycleStitchType(type) {
  if (type === 'fill') return 'satin';
  if (type === 'satin') return 'skip';
  return 'fill';
}

function applyStitchSelectionAction(id, shiftKey) {
  if (shiftKey) {
    toggleStitchChecked(id);
    stitchSelectedId = id;
    renderStitchList();
    renderStitchPreview();
    updateStitchDetail();
    return;
  }

  const noMulti = stitchCheckedIds.size === 0;
  if (id === stitchSelectedId && noMulti) {
    stitchAssignments[id] = cycleStitchType(stitchAssignments[id] || 'fill');
    refreshAssignmentViews();
    toast('Changed selected object to ' + (stitchAssignments[id] || 'fill'));
    return;
  }

  selectStitchObject(id);
}

function selectedStitchColour() {
  return currentAssignmentSelectedColour();
}

function assignColourStitch(type) {
  const colour = currentAssignmentSelectedColour();
  const list = currentAssignmentObjectList();
  if (!colour || !list.length) {
    toast('Select an object first');
    return;
  }
  let changed = 0;
  list.forEach(o => {
    if (o.color === colour) {
      stitchAssignments[o.id] = type;
      changed += 1;
    }
  });
  refreshAssignmentViews();
  toast('Assigned ' + type + ' to ' + changed + ' object(s) of colour ' + colour);
}

function orderedStitchGroups() {
  const groups = buildGroupedObjectRows(stitchObjects);
  if (stitchSortMode === 'colour') {
    groups.sort((a, b) => {
      const ac = (a.first.color || '').toLowerCase();
      const bc = (b.first.color || '').toLowerCase();
      if (ac < bc) return -1;
      if (ac > bc) return 1;
      return (a.first.order || 0) - (b.first.order || 0);
    });
  } else {
    groups.sort((a, b) => (a.first.order || 0) - (b.first.order || 0));
  }
  return groups;
}

function toggleStitchSortMode() {
  stitchSortMode = stitchSortMode === 'number' ? 'colour' : 'number';
  const meta = document.getElementById('stitch-sort-meta');
  if (meta) meta.textContent = 'Sort mode: ' + stitchSortMode;
  renderStitchList();
  toast('Stitch list sort: ' + stitchSortMode);
}

function stitchBadgeClass(st) {
  if (st === 'satin') return 'stitch-badge stitch-satin';
  if (st === 'skip') return 'stitch-badge stitch-skip';
  return 'stitch-badge stitch-fill';
}


function applyStitchObjectPanelState() {
  const panel = document.getElementById('stitch-object-panel');
  if (!panel) return;
  const expanded = !stitchObjectListCollapsed;
  panel.style.width = expanded ? '360px' : '42px';
  document.querySelectorAll('.stitch-list-expanded-only').forEach(el => {
    el.style.display = expanded ? '' : 'none';
  });
  const btn = document.getElementById('stitch-object-panel-toggle');
  if (btn) {
    btn.textContent = expanded ? '×' : '☰';
    btn.title = expanded ? 'Collapse stitch object list' : 'Expand stitch object list';
  }
}

function toggleStitchObjectPanel() {
  stitchObjectListCollapsed = !stitchObjectListCollapsed;
  applyStitchObjectPanelState();
  setWorkZoom(workZoom);
}

function renderStitchList() {
  const list = document.getElementById('stitch-list');
  const meta = document.getElementById('stitch-count');
  const sortMeta = document.getElementById('stitch-sort-meta');
  if (!list || !meta) return;
  list.innerHTML = '';
  meta.textContent = stitchObjects.length + ' object' + (stitchObjects.length === 1 ? '' : 's');
  if (sortMeta) sortMeta.textContent = 'Sort mode: ' + stitchSortMode;
  applyStitchObjectPanelState();

  orderedStitchGroups().forEach(group => {
    const members = group.members;
    const parentLabel = groupParentLabel(members);
    const isGrouped = members.length > 1;
    const groupState = groupCheckedState(members, stitchCheckedIds);
    const groupSelected = members.some(o => o.id === stitchSelectedId || stitchCheckedIds.has(o.id));
    const collapsed = stitchCollapsedGroups.has(group.groupId);

    if (isGrouped) {
      const stSummary = groupAssignmentSummary(members);
      const parent = document.createElement('div');
      parent.className = 'obj-row obj-parent' + (groupSelected ? ' sel' : '');
      parent.onclick = (ev) => {
        if (ev.shiftKey) toggleStitchGroup(group.groupId);
        else selectStitchGroup(group.groupId);
      };
      parent.innerHTML = `
        <button class="collapse-toggle" title="${collapsed ? 'Expand group' : 'Collapse group'}" onclick="event.stopPropagation();toggleStitchGroupCollapse('${group.groupId}')">${collapsed ? '+' : '−'}</button>
        <input type="checkbox" ${groupState === 'checked' ? 'checked' : ''} onclick="event.stopPropagation();toggleStitchGroup('${group.groupId}')">
        <div class="obj-swatch" style="background:${members[0].color}"></div>
        <div class="obj-info">
          <div class="obj-name">${parentLabel}</div>
          <div class="obj-meta">${members.length} grouped child paths · group ${group.groupId}</div>
        </div>
        <span class="${stSummary === 'mixed' ? 'obj-group-count' : stitchBadgeClass(stSummary)}">${stSummary}</span>
      `;
      const cb = parent.querySelector('input[type=checkbox]');
      setCheckboxIndeterminate(cb, groupState);
      list.appendChild(parent);
    }

    members.forEach((obj, idx) => {
      const st = stitchAssignments[obj.id] || 'fill';
      const row = document.createElement('div');
      row.className = 'obj-row' + (isGrouped ? ' obj-child' : '') + (obj.id === stitchSelectedId ? ' sel' : '') + ((isGrouped && collapsed) ? ' hidden-child' : '');
      row.onclick = (ev) => applyStitchSelectionAction(obj.id, ev.shiftKey);
      const displayName = isGrouped ? childLabel(parentLabel, idx) : obj.label;
      row.innerHTML = `
        ${!isGrouped ? '<span style="width:22px;display:inline-block"></span>' : ''}
        <input type="checkbox" ${stitchCheckedIds.has(obj.id) ? 'checked' : ''} onclick="event.stopPropagation();toggleStitchChecked('${obj.id}')">
        <div class="obj-swatch" style="background:${obj.color}"></div>
        <div class="obj-info">
          <div class="obj-name">${displayName} <span style="color:#8f96b3;font-size:.72rem">${obj.color}</span></div>
          <div class="obj-meta">source path ${obj.display_index} · group ${obj.group_id} · ratio ${obj.elongation}<br>${obj.prep_note || 'working object'}</div>
        </div>
        <span class="${stitchBadgeClass(st)}">${st}</span>
      `;
      list.appendChild(row);
    });
  });
}

function stitchSvgMarkup() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${structureSvgW}" height="${structureSvgH}" viewBox="0 0 ${structureSvgW} ${structureSvgH}"></svg>`;
}

function renderStitchPreview() {
  const wrap = document.getElementById('stitch-preview');
  if (!wrap) return;
  if (!stitchObjects.length) {
    wrap.innerHTML = '<span style="color:#555">No stitch assignments loaded</span>'; restorePreviewBg('stitch-preview');
    return;
  }

  wrap.innerHTML = stitchSvgMarkup(); restorePreviewBg('stitch-preview');
  const svg = wrap.querySelector('svg');
  svg.style.maxWidth = '100%';
  svg.style.maxHeight = '75vh';
  svg.style.width = 'auto';
  svg.style.height = 'auto';

  renderHoopRulers(svg);
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  defs.innerHTML = `
    <pattern id="fillHatchDark" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="8" stroke="rgba(255,255,255,0.65)" stroke-width="1"/>
    </pattern>
    <pattern id="fillHatchLight" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="8" stroke="rgba(0,0,0,0.55)" stroke-width="1"/>
    </pattern>
  `;
  svg.appendChild(defs);

  const selectedGroupIds = new Set();
  if (stitchSelectedId) {
    const s = stitchObjects.find(o => o.id === stitchSelectedId);
    if (s && s.group_id) selectedGroupIds.add(s.group_id);
  }
  stitchCheckedIds.forEach(id => {
    const o = stitchObjects.find(x => x.id === id);
    if (o && o.group_id) selectedGroupIds.add(o.group_id);
  });

  function luminance(hex) {
    const h = (hex || '#000000').replace('#', '');
    if (h.length !== 6) return 0;
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  stitchObjects.slice().sort((a,b) => (a.order||0) - (b.order||0)).forEach(obj => {
    const st = stitchAssignments[obj.id] || 'fill';
    const isHighlighted = selectedGroupIds.has(obj.group_id) || stitchCheckedIds.has(obj.id) || obj.id === stitchSelectedId;

    const base = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    base.setAttribute('d', obj.d);
    if (obj.tx || obj.ty) base.setAttribute('transform', `translate(${obj.tx},${obj.ty})`);
    base.style.cursor = 'pointer';
    base.setAttribute('fill-rule', 'evenodd');
    base.setAttribute('clip-rule', 'evenodd');

    if (st === 'skip') {
      base.setAttribute('fill', obj.color);
      base.setAttribute('fill-opacity', '0.18');
      base.setAttribute('stroke', obj.color);
      base.setAttribute('stroke-opacity', '0.25');
      base.setAttribute('stroke-width', '0.8');
    } else if (st === 'satin') {
      base.setAttribute('fill', obj.color);
      base.setAttribute('fill-opacity', '1');
      base.setAttribute('stroke', obj.color);
      base.setAttribute('stroke-width', '0.6');
      base.setAttribute('stroke-opacity', '0.9');
    } else {
      base.setAttribute('fill', obj.color);
      base.setAttribute('fill-opacity', '1');
      base.setAttribute('stroke', 'rgba(255,255,255,0.25)');
      base.setAttribute('stroke-width', '0.4');
    }

    if (isHighlighted) {
      base.setAttribute('stroke', '#e94560');
      base.setAttribute('stroke-width', obj.id === stitchSelectedId ? '2.2' : '1.4');
      base.setAttribute('stroke-opacity', obj.id === stitchSelectedId ? '1' : '0.78');
    }

    base.addEventListener('click', (ev) => {
      ev.stopPropagation();
      applyStitchSelectionAction(obj.id, ev.shiftKey);
    });
    svg.appendChild(base);

    if (st === 'fill') {
      const hatch = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      hatch.setAttribute('d', obj.d);
      if (obj.tx || obj.ty) hatch.setAttribute('transform', `translate(${obj.tx},${obj.ty})`);
      hatch.setAttribute('fill-rule', 'evenodd');
      hatch.setAttribute('clip-rule', 'evenodd');
      hatch.setAttribute('fill', luminance(obj.color) > 160 ? 'url(#fillHatchLight)' : 'url(#fillHatchDark)');
      hatch.setAttribute('pointer-events', 'none');
      svg.appendChild(hatch);
    }
  });

  setWorkZoom(workZoom);
  renderManualRungOverlay();
  document.getElementById('stitch-preview-meta').textContent = stitchObjects.length + ' objects';
}

function updateStitchDetail() {
  const detail = document.getElementById('stitch-detail');
  if (!detail) return;
  const objs = stitchSelectedObjects();
  if (!objs.length) {
    detail.innerHTML = 'Load prepared structure to begin.';
    return;
  }
  if (objs.length > 1) {
    detail.innerHTML = `
      Selected objects: <b>${objs.length}</b><br>
      Assignments: <b>${[...new Set(objs.map(o => stitchAssignments[o.id] || 'fill'))].join(', ')}</b><br>
      Groups: <b>${[...new Set(objs.map(o => o.group_id))].join(', ')}</b>
    `;
    return;
  }
  const o = objs[0];
  const st = stitchAssignments[o.id] || 'fill';
  detail.innerHTML = `
    Object: <b>${o.label}</b><br>
    Assignment: <b>${st}</b><br>
    Source path: <b>${o.display_index}</b><br>
    Colour: <b>${o.color}</b><br>
    Group: <b>${o.group_id}</b><br>
    Ratio: <b>${o.elongation}</b><br>
    Note: <b>${o.prep_note}</b>
  `;
}

function updateStitchSummary() {
  const summary = document.getElementById('stitch-summary');
  if (!summary) return;
  const counts = {fill:0, satin:0, skip:0};
  stitchObjects.forEach(o => {
    const st = stitchAssignments[o.id] || 'fill';
    counts[st] = (counts[st] || 0) + 1;
  });
  summary.innerHTML = `
    Fill: <b>${counts.fill}</b><br>
    Satin: <b>${counts.satin}</b><br>
    Skip: <b>${counts.skip}</b><br>
    Total objects: <b>${stitchObjects.length}</b>
  `;
}

function assignSelectedStitch(type) {
  const objs = currentAssignmentObjects();
  if (!objs.length) { toast('Select object(s) first'); return; }
  objs.forEach(o => { stitchAssignments[o.id] = type; });
  refreshAssignmentViews();
  toast('Assigned ' + type + ' to ' + objs.length + ' object(s)');
}

function autoAssignStitches() {
  const list = currentAssignmentObjectList();
  if (!list.length) { toast('Load structure first'); return; }

  const result = autoAssignImproved(list);
  Object.keys(result.assignments).forEach(id => {
    stitchAssignments[id] = result.assignments[id];
  });

  refreshAssignmentViews();
  toast(
    'Auto assignment refreshed' +
    (result.edgeSkipped ? ` · edge/background skipped ${result.edgeSkipped}` : '') +
    (result.columnsConverted ? ` · columns to satin ${result.columnsConverted}` : '') +
    (result.enclosedLargeRegionsForcedFill ? ` · enclosed large regions to fill ${result.enclosedLargeRegionsForcedFill} @ ${Math.round(autoAssignEnclosedFillThreshold() * 100)}%` : '')
  );
}


function setPreviewLayerMode(mode) {
  previewLayerMode = mode || 'both';

  const underlayBox = document.getElementById('underlay-enable');
  if (underlayBox) {
    if (previewLayerMode === 'top') underlayBox.checked = false;
    if (previewLayerMode === 'underlay') underlayBox.checked = true;
  }

  updateUnderlayUi(false);
  renderCachedStitchPreview();
  ensurePreviewBgButtons();
}

function getSelectedHoop() {
  const value = document.getElementById('hoop-size')?.value || '120x120';
  const parts = value.split('x').map(v => parseFloat(v));

  // Hoop options are named as Vertical × Horizontal, matching the machine/hoop
  // convention. Internally width is horizontal and height is vertical.
  const verticalMm = parts[0] || 120;
  const horizontalMm = parts[1] || 120;

  return {
    id: value,
    width_mm: horizontalMm,
    height_mm: verticalMm,
    vertical_mm: verticalMm,
    horizontal_mm: horizontalMm,
    label: `${verticalMm} × ${horizontalMm} mm (V × H)`
  };
}

function designSizeSourceObjects() {
  const source = stitchObjects.length ? stitchObjects : structureObjects;
  if (!source.length) return [];
  const stitched = source.filter(o => (stitchAssignments[o.id] || 'fill') !== 'skip');
  return stitched.length ? stitched : source;
}

let designSizingBBoxSvg = null;

function designSizingObjectBBox(obj) {
  if (!obj || !obj.d) return null;
  try {
    if (!designSizingBBoxSvg) {
      designSizingBBoxSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      designSizingBBoxSvg.setAttribute('width', '0');
      designSizingBBoxSvg.setAttribute('height', '0');
      designSizingBBoxSvg.style.position = 'fixed';
      designSizingBBoxSvg.style.left = '-10000px';
      designSizingBBoxSvg.style.top = '-10000px';
      designSizingBBoxSvg.style.visibility = 'hidden';
      document.body.appendChild(designSizingBBoxSvg);
    }

    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', obj.d);
    designSizingBBoxSvg.appendChild(p);
    const b = p.getBBox();
    p.remove();

    const tx = Number(obj.tx || 0);
    const ty = Number(obj.ty || 0);
    return {
      x1: b.x + tx,
      y1: b.y + ty,
      x2: b.x + b.width + tx,
      y2: b.y + b.height + ty,
      w: b.width,
      h: b.height
    };
  } catch (e) {
    return null;
  }
}

function getDesignBoundsSvgUnits() {
  const objects = designSizeSourceObjects();
  let bounds = null;

  objects.forEach(o => {
    const b = designSizingObjectBBox(o);
    if (!b) return;
    if (!bounds) bounds = {x1:b.x1, y1:b.y1, x2:b.x2, y2:b.y2};
    else {
      bounds.x1 = Math.min(bounds.x1, b.x1);
      bounds.y1 = Math.min(bounds.y1, b.y1);
      bounds.x2 = Math.max(bounds.x2, b.x2);
      bounds.y2 = Math.max(bounds.y2, b.y2);
    }
  });

  if (!bounds) return null;
  bounds.w = Math.max(0, bounds.x2 - bounds.x1);
  bounds.h = Math.max(0, bounds.y2 - bounds.y1);
  return bounds;
}

function formatMm(v) {
  if (!Number.isFinite(v)) return '0.0';
  return v.toFixed(1);
}

function getFitLongestSideForHoop(bounds, hoop) {
  if (!bounds || bounds.w <= 0 || bounds.h <= 0) return Math.min(hoop.width_mm, hoop.height_mm);
  const longestSvg = Math.max(bounds.w, bounds.h);
  const widthAtLongestOne = bounds.w / longestSvg;
  const heightAtLongestOne = bounds.h / longestSvg;
  return Math.min(
    hoop.width_mm / Math.max(0.0001, widthAtLongestOne),
    hoop.height_mm / Math.max(0.0001, heightAtLongestOne)
  );
}

function clampDesignTargetLongest(value, bounds=null, hoop=null) {
  hoop = hoop || getSelectedHoop();
  bounds = bounds || getDesignBoundsSvgUnits();
  const fitMax = getFitLongestSideForHoop(bounds, hoop);
  const maxVal = Math.max(5, fitMax);
  const v = Number.isFinite(value) ? value : maxVal;
  return Math.max(5, Math.min(maxVal, v));
}

function syncDesignTargetControls(bounds=null) {
  const hoop = getSelectedHoop();
  bounds = bounds || getDesignBoundsSvgUnits();
  const fitMax = getFitLongestSideForHoop(bounds, hoop);
  const slider = document.getElementById('design-longest-side');
  const input = document.getElementById('design-longest-side-input');

  if (!bounds || bounds.w <= 0 || bounds.h <= 0) {
    if (slider) {
      slider.max = String(Math.round(fitMax));
      slider.value = String(Math.round(fitMax));
      slider.disabled = true;
    }
    if (input) {
      input.max = String(formatMm(fitMax));
      input.value = String(formatMm(fitMax));
      input.disabled = true;
    }
    return;
  }

  if (designTargetLongestMm === null) {
    designTargetLongestMm = fitMax;
  }
  designTargetLongestMm = clampDesignTargetLongest(designTargetLongestMm, bounds, hoop);

  const maxRounded = Math.max(5, Math.ceil(fitMax * 10) / 10);
  if (slider) {
    slider.disabled = false;
    slider.max = String(Math.ceil(maxRounded));
    slider.value = String(Math.round(designTargetLongestMm));
  }
  if (input) {
    input.disabled = false;
    input.max = String(maxRounded.toFixed(1));
    input.value = String(formatMm(designTargetLongestMm));
  }
}

function setDesignTargetToFit() {
  const bounds = getDesignBoundsSvgUnits();
  const hoop = getSelectedHoop();
  designTargetLongestMm = getFitLongestSideForHoop(bounds, hoop);
  syncDesignTargetControls(bounds);
}

function onHoopSizeChanged() {
  // On hoop change, default back to "fit selected hoop" because the available
  // maximum has changed.
  setDesignTargetToFit();
  updateDesignSizeInfo();
  renderStitchPreview();
  renderCachedStitchPreview();
}

function onDesignLongestSideSlider() {
  const slider = document.getElementById('design-longest-side');
  const bounds = getDesignBoundsSvgUnits();
  designTargetLongestMm = clampDesignTargetLongest(parseFloat(slider?.value || '0'), bounds);
  syncDesignTargetControls(bounds);
  updateDesignSizeInfo();
  renderStitchPreview();
  renderCachedStitchPreview();
}

function onDesignLongestSideInput() {
  const input = document.getElementById('design-longest-side-input');
  const bounds = getDesignBoundsSvgUnits();
  designTargetLongestMm = clampDesignTargetLongest(parseFloat(input?.value || '0'), bounds);
  syncDesignTargetControls(bounds);
  updateDesignSizeInfo();
  renderStitchPreview();
  renderCachedStitchPreview();
}

function getDesignScaleInfo() {
  const hoop = getSelectedHoop();
  const bounds = getDesignBoundsSvgUnits();
  if (!bounds || bounds.w <= 0 || bounds.h <= 0) return {hoop, bounds: null};

  const nativeMmPerSvg = 25.4 / 96.0;
  const nativeW = bounds.w * nativeMmPerSvg;
  const nativeH = bounds.h * nativeMmPerSvg;
  const fitLongestMm = getFitLongestSideForHoop(bounds, hoop);

  if (designTargetLongestMm === null) designTargetLongestMm = fitLongestMm;
  designTargetLongestMm = clampDesignTargetLongest(designTargetLongestMm, bounds, hoop);

  const longestSvg = Math.max(bounds.w, bounds.h);
  const designMmPerSvg = designTargetLongestMm / Math.max(0.0001, longestSvg);
  const designW = bounds.w * designMmPerSvg;
  const designH = bounds.h * designMmPerSvg;
  const fitPercentOfNative = (designMmPerSvg / nativeMmPerSvg) * 100.0;
  const exceedsHoop = designW > hoop.width_mm + 0.01 || designH > hoop.height_mm + 0.01;

  return {
    hoop,
    bounds,
    nativeMmPerSvg,
    nativeW,
    nativeH,
    fitMmPerSvg: designMmPerSvg,
    fitW: designW,
    fitH: designH,
    fitPercentOfNative,
    fitLongestMm,
    targetLongestMm: designTargetLongestMm,
    exceedsNative: exceedsHoop,
    exceedsHoop
  };
}

function updateDesignSizeInfo() {
  const el = document.getElementById('design-size-meta');
  if (!el) return;

  const info = getDesignScaleInfo();
  const hoop = info.hoop;
  if (!info.bounds) {
    syncDesignTargetControls(null);
    el.innerHTML = `Selected hoop: <b>${hoop.label}</b><br>Load prepared structure to calculate design size.`;
    return;
  }

  syncDesignTargetControls(info.bounds);

  const warning = info.exceedsHoop
    ? `<br><span style="color:#f6c177">Selected design size exceeds selected hoop.</span>`
    : `<br><span style="color:#9da7c4">Selected design size fits selected hoop. This size is now used for stitch generation and export.</span>`;

  el.innerHTML =
    `Selected hoop: <b>${hoop.label}</b><br>` +
    `Current legacy size: <b>${formatMm(info.nativeW)} × ${formatMm(info.nativeH)} mm</b><br>` +
    `Selected design size: <b>${formatMm(info.fitW)} × ${formatMm(info.fitH)} mm</b><br>` +
    `Fit-to-hoop max longest side: <b>${formatMm(info.fitLongestMm)} mm</b><br>` +
    `Scale estimate: <b>${info.fitPercentOfNative.toFixed(1)}%</b> of current size` +
    warning;
}

function clearHoopRulers(svg) {
  if (!svg) return;
  svg.querySelectorAll('.hoop-ruler').forEach(g => g.remove());
}

function niceRulerStep(maxMm) {
  if (maxMm <= 60) return 5;
  if (maxMm <= 180) return 10;
  if (maxMm <= 300) return 20;
  return 50;
}

function renderHoopRulers(svg) {
  if (!svg) return;
  clearHoopRulers(svg);

  const info = getDesignScaleInfo();
  if (!info.bounds || !info.fitMmPerSvg) return;

  const b = info.bounds;
  const mmPerSvg = info.fitMmPerSvg;
  const widthMm = b.w * mmPerSvg;
  const heightMm = b.h * mmPerSvg;
  const maxMm = Math.max(widthMm, heightMm);
  const stepMm = niceRulerStep(maxMm);
  const stepSvg = stepMm / mmPerSvg;
  const minorStepSvg = stepSvg / 2;

  const hoopWsvg = info.hoop.width_mm / mmPerSvg;
  const hoopHsvg = info.hoop.height_mm / mmPerSvg;

  // For now the design is centred inside the selected hoop. Later, when we add
  // placement controls, this centre can become user-adjustable without changing
  // the scale/ruler maths.
  const cx = b.x1 + b.w / 2;
  const cy = b.y1 + b.h / 2;
  const hoopRect = {
    x: cx - hoopWsvg / 2,
    y: cy - hoopHsvg / 2,
    w: hoopWsvg,
    h: hoopHsvg
  };

  // Expand the SVG viewBox to include the whole hoop frame, otherwise a small
  // design inside a larger hoop would clip the frame at the preview edge.
  const pad = Math.max(8, Math.max(hoopWsvg, hoopHsvg) * 0.035);
  const vbX = Math.min(0, hoopRect.x - pad, b.x1 - pad);
  const vbY = Math.min(0, hoopRect.y - pad, b.y1 - pad);
  const vbX2 = Math.max(structureSvgW, hoopRect.x + hoopRect.w + pad, b.x2 + pad);
  const vbY2 = Math.max(structureSvgH, hoopRect.y + hoopRect.h + pad, b.y2 + pad);
  svg.setAttribute('viewBox', `${vbX.toFixed(2)} ${vbY.toFixed(2)} ${(vbX2 - vbX).toFixed(2)} ${(vbY2 - vbY).toFixed(2)}`);

  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('class', 'hoop-ruler');
  g.setAttribute('pointer-events', 'none');

  const sizeBase = Math.max(hoopRect.w, hoopRect.h, b.w, b.h);
  const fontSize = Math.max(7, Math.min(18, sizeBase * 0.018));
  const tickMajor = Math.max(4, Math.min(18, sizeBase * 0.014));
  const tickMinor = tickMajor * 0.55;

  const addLine = (x1, y1, x2, y2, cls='') => {
    const l = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    l.setAttribute('x1', x1.toFixed(2));
    l.setAttribute('y1', y1.toFixed(2));
    l.setAttribute('x2', x2.toFixed(2));
    l.setAttribute('y2', y2.toFixed(2));
    if (cls) l.setAttribute('class', cls);
    g.appendChild(l);
  };

  const addText = (x, y, txt, anchor='middle', rotate=false) => {
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    t.setAttribute('x', x.toFixed(2));
    t.setAttribute('y', y.toFixed(2));
    t.setAttribute('font-size', fontSize.toFixed(2));
    t.setAttribute('text-anchor', anchor);
    if (rotate) t.setAttribute('transform', `rotate(-90 ${x.toFixed(2)} ${y.toFixed(2)})`);
    t.textContent = txt;
    g.appendChild(t);
  };

  // Full selected hoop/frame overlay.
  const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  bg.setAttribute('class', 'hoop-frame-bg');
  bg.setAttribute('x', hoopRect.x.toFixed(2));
  bg.setAttribute('y', hoopRect.y.toFixed(2));
  bg.setAttribute('width', hoopRect.w.toFixed(2));
  bg.setAttribute('height', hoopRect.h.toFixed(2));
  g.appendChild(bg);

  const frame = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  frame.setAttribute('class', 'hoop-frame');
  frame.setAttribute('x', hoopRect.x.toFixed(2));
  frame.setAttribute('y', hoopRect.y.toFixed(2));
  frame.setAttribute('width', hoopRect.w.toFixed(2));
  frame.setAttribute('height', hoopRect.h.toFixed(2));
  g.appendChild(frame);

  // Rulers remain tied to the selected design size, not to screen zoom.
  addLine(b.x1, b.y1, b.x2, b.y1, 'ruler-base');
  addLine(b.x1, b.y1, b.x1, b.y2, 'ruler-base');

  for (let x = b.x1, mm = 0; x <= b.x2 + 0.001; x += minorStepSvg, mm += stepMm / 2) {
    const isMajor = Math.abs(mm / stepMm - Math.round(mm / stepMm)) < 0.001;
    const tick = isMajor ? tickMajor : tickMinor;
    addLine(x, b.y1, x, b.y1 + tick);
    if (isMajor && mm > 0 && mm <= widthMm + 0.5) {
      addText(x, b.y1 + tick + fontSize + 1, String(Math.round(mm)));
    }
  }

  for (let y = b.y1, mm = 0; y <= b.y2 + 0.001; y += minorStepSvg, mm += stepMm / 2) {
    const isMajor = Math.abs(mm / stepMm - Math.round(mm / stepMm)) < 0.001;
    const tick = isMajor ? tickMajor : tickMinor;
    addLine(b.x1, y, b.x1 + tick, y);
    if (isMajor && mm > 0 && mm <= heightMm + 0.5) {
      addText(b.x1 + tick + fontSize + 1, y + fontSize * 0.35, String(Math.round(mm)), 'middle', true);
    }
  }

  svg.appendChild(g);
}


function previewSvgShell() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${structureSvgW}" height="${structureSvgH}" viewBox="0 0 ${structureSvgW} ${structureSvgH}"></svg>`;
}

function svgAddPolyline(svg, points, color, width, opacity, dash='') {
  if (!points || points.length < 2) return;
  const pl = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
  pl.setAttribute('points', points.map(p => `${Number(p[0]).toFixed(2)},${Number(p[1]).toFixed(2)}`).join(' '));
  pl.setAttribute('fill', 'none');
  pl.setAttribute('stroke', color || '#000000');
  pl.setAttribute('stroke-width', String(width));
  pl.setAttribute('stroke-opacity', String(opacity));
  pl.setAttribute('stroke-linecap', 'round');
  pl.setAttribute('stroke-linejoin', 'round');
  pl.setAttribute('vector-effect', 'non-scaling-stroke');
  if (dash) pl.setAttribute('stroke-dasharray', dash);
  svg.appendChild(pl);
}

function shouldShowStitchDots() {
  return document.getElementById('show-stitch-dots')?.checked === true;
}

function svgDotColorForLine(color) {
  const h = (color || '#000000').replace('#', '');
  if (h.length !== 6) return '#111111';
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum < 70 ? '#f4f4f4' : '#111111';
}

function svgAddStitchDots(svg, points, color, radius=1.15, opacity=0.75, maxDots=25000) {
  if (!svg || !points || !points.length) return 0;
  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('pointer-events', 'none');
  g.setAttribute('class', 'stitch-point-dots');
  const fill = svgDotColorForLine(color);
  let count = 0;

  // The preview polylines already contain generated stitch vertices, so dots
  // at those vertices make the actual stitch length/density visible.
  for (const p of points) {
    if (count >= maxDots) break;
    if (!p || p.length < 2) continue;
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('cx', Number(p[0]).toFixed(2));
    c.setAttribute('cy', Number(p[1]).toFixed(2));
    c.setAttribute('r', String(radius));
    c.setAttribute('fill', fill);
    c.setAttribute('fill-opacity', String(opacity));
    c.setAttribute('stroke', color || '#000000');
    c.setAttribute('stroke-opacity', '0.45');
    c.setAttribute('stroke-width', '0.35');
    g.appendChild(c);
    count += 1;
  }

  if (count) svg.appendChild(g);
  return count;
}

function renderCachedStitchPreview() {
  if (!currentStitchPreview) return false;

  const wrap = document.getElementById('stitch-preview');
  if (!wrap) return false;

  wrap.innerHTML = previewSvgShell(); restorePreviewBg('stitch-preview');
  const svg = wrap.querySelector('svg');
  svg.style.maxWidth = '100%';
  svg.style.maxHeight = '75vh';
  svg.style.width = 'auto';
  svg.style.height = 'auto';

  renderHoopRulers(svg);
  const mode = previewLayerMode || 'both';
  const showUnderlay = mode === 'both' || mode === 'underlay';
  const showTop = mode === 'both' || mode === 'top';

  const layers = currentStitchPreview.layers || {};
  let count = 0;

  if (showUnderlay) {
    for (const line of layers.underlay || []) {
      svgAddPolyline(svg, line.points, line.color, line.width || 0.75, line.opacity || 0.55, line.dash || '');
      if (shouldShowStitchDots()) svgAddStitchDots(svg, line.points, line.color, 0.95, 0.45);
      count += 1;
    }
  }

  if (showTop) {
    for (const line of layers.top || []) {
      svgAddPolyline(svg, line.points, line.color, Math.max(line.width || 0.85, 1.05), Math.max(line.opacity || 0.9, 0.98), line.dash || '');
      if (shouldShowStitchDots()) svgAddStitchDots(svg, line.points, line.color, 1.15, 0.78);
      count += 1;
    }
    if (currentStitchPreview.debug_svg) {
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g.innerHTML = currentStitchPreview.debug_svg;
      svg.appendChild(g);
    }
  }

  setWorkZoom(workZoom);
  renderManualRungOverlay();

  const meta = document.getElementById('stitch-preview-meta');
  if (meta) {
    const c = currentStitchPreview.counts || {};
    meta.textContent =
      `Preview ${mode}: ${count} line(s) · ` +
      `${c.underlay_edge_lines || 0} edge underlay · ` +
      `${c.underlay_fill_lines || 0} underlay fill · ` +
      `${c.top_fill_lines || 0} top fill · ` +
      `${c.satin_bars || 0} satin bars` +
      (c.design_scale_applied ? ` · scaled ${(c.target_width_mm || 0).toFixed(1)}×${(c.target_height_mm || 0).toFixed(1)}mm` : '') +
      ((c.cut_guide_rungs || 0) ? ` · cut rungs ${c.cut_guide_rungs}` : '');
  }

  return true;
}

function updateUnderlayUi(render=true) {
  const on = document.getElementById('underlay-enable')?.checked ?? true;
  const box = document.getElementById('underlay-settings');
  if (box) {
    box.style.opacity = on ? '1' : '0.35';
    box.style.pointerEvents = on ? '' : 'none';
  }

  const layerSelect = document.getElementById('preview-layer-mode');
  if (layerSelect) {
    if (!on && previewLayerMode !== 'top') {
      previewLayerMode = 'top';
      layerSelect.value = 'top';
    } else if (on && previewLayerMode === 'top') {
      previewLayerMode = 'underlay';
      layerSelect.value = 'underlay';
    } else {
      layerSelect.value = previewLayerMode || 'both';
    }
  }

  if (render) renderCachedStitchPreview();
}

function cleanManualRungsPayload() {
  const existing = new Set(stitchObjects.map(o => o.id));
  const out = {};
  Object.entries(stitchManualRungs).forEach(([id, rungs]) => {
    if (!existing.has(id)) return;
    const clean = (rungs || []).filter(r => r && r.a && r.b && r.a.length === 2 && r.b.length === 2);
    if (clean.length) out[id] = clean;
  });
  return out;
}

function currentStitchSettings() {
  const scaleInfo = getDesignScaleInfo();
  const svgToMm = (scaleInfo && scaleInfo.fitMmPerSvg) ? scaleInfo.fitMmPerSvg : (25.4 / 96.0);
  const effectiveDpi = 25.4 / Math.max(0.000001, svgToMm);

  const designScale = scaleInfo && scaleInfo.bounds ? {
    svg_to_mm: svgToMm,
    effective_dpi: effectiveDpi,
    target_width_mm: scaleInfo.fitW,
    target_height_mm: scaleInfo.fitH,
    target_longest_mm: scaleInfo.targetLongestMm,
    hoop_width_mm: scaleInfo.hoop.width_mm,
    hoop_height_mm: scaleInfo.hoop.height_mm,
    hoop_label: scaleInfo.hoop.label,
    bounds_svg: scaleInfo.bounds,
    scaling_applied: true
  } : {
    svg_to_mm: 25.4 / 96.0,
    effective_dpi: 96.0,
    scaling_applied: false
  };

  return {
    fill_angle: parseFloat(document.getElementById('stitch-fill-angle')?.value || '45'),
    auto_fill_direction: document.getElementById('auto-fill-direction-enable')?.checked ?? true,
    auto_fill_threshold: parseFloat(document.getElementById('auto-fill-threshold')?.value || '2.0'),
    stitch_order_mode: document.getElementById('stitch-order-mode')?.value || 'quality',
    avoid_top_fill_overlap: document.getElementById('avoid-top-fill-overlap-enable')?.checked ?? true,
    underlay_protect_lighter: document.getElementById('underlay-protect-lighter-enable')?.checked ?? true,
    underlay_light_threshold: parseFloat(document.getElementById('underlay-light-threshold')?.value || '45'),
    underlay_jump_trim_threshold_mm: parseFloat(document.getElementById('underlay-jump-trim-threshold-mm')?.value || '5'),
    row_spacing_mm: parseFloat(document.getElementById('stitch-row-spacing')?.value || '0.4'),
    stitch_length_mm: parseFloat(document.getElementById('stitch-length-mm')?.value || '2.5'),
    jump_trim_threshold_mm: parseFloat(document.getElementById('jump-trim-threshold-mm')?.value || '3.0'),
    satin_spacing_mm: parseFloat(document.getElementById('satin-spacing-mm')?.value || '0.45'),
    satin_max_width_mm: parseFloat(document.getElementById('satin-max-width-mm')?.value || '7.0'),
    satin_end_extra_rungs: parseInt(document.getElementById('satin-end-extra-rungs')?.value || '2', 10),
    satin_use_guide_helper: document.getElementById('satin-guide-helper-enable')?.checked ?? false,
    satin_debug_rails: document.getElementById('satin-debug-rails-enable')?.checked ?? false,
    enable_underlay: document.getElementById('underlay-enable')?.checked ?? true,
    underlay_inset_mm: parseFloat(document.getElementById('underlay-inset-mm')?.value || '0.8'),
    underlay_row_mm: parseFloat(document.getElementById('underlay-row-mm')?.value || '1.6'),

    // Effective DPI is the bridge between SVG units and real embroidery mm.
    // Geometry remains in SVG coordinates, but spacing and export conversion
    // now use the selected Pane 4 design size.
    dpi: effectiveDpi,
    design_scale: designScale
  };
}


function selectedStitchObject() {
  return stitchObjects.find(o => o.id === stitchSelectedId) || null;
}

function selectedManualRungTarget() {
  const obj = selectedStitchObject();
  if (!obj) return null;
  if ((stitchAssignments[obj.id] || 'fill') !== 'satin') return null;
  return obj;
}

function updateManualRungStatus() {
  const el = document.getElementById('manual-rung-status');
  if (!el) return;
  const obj = selectedManualRungTarget();
  if (!obj) {
    el.textContent = 'Manual rung target: none';
    return;
  }
  const count = (stitchManualRungs[obj.id] || []).length;
  el.textContent = `Manual rung target: ${obj.label || obj.id} · ${count} guide rung(s)`;
}

function toggleManualRungMode() {
  if (!stitchLoaded) {
    loadStitchPane();
    if (!stitchLoaded) return;
  }
  manualRungMode = !manualRungMode;
  pendingManualRungPoint = null;
  draggingManualRung = null;
  const btn = document.getElementById('manual-rung-mode-btn');
  if (btn) btn.textContent = manualRungMode ? 'Manual rung mode: on' : 'Manual rung mode: off';
  renderManualRungOverlay();
  updateManualRungStatus();
  toast(manualRungMode ? 'Manual rung mode on: first click chooses the satin path, second click finishes the guide rung' : 'Manual rung mode off');
}

function clearSelectedManualRungs() {
  const obj = selectedManualRungTarget();
  if (!obj) {
    toast('Select a satin object first');
    return;
  }
  delete stitchManualRungs[obj.id];
  pendingManualRungPoint = null;
  draggingManualRung = null;
  renderManualRungOverlay();
  updateManualRungStatus();
  toast('Cleared manual rungs for selected satin object');
}

function clearAllManualRungs() {
  stitchManualRungs = {};
  pendingManualRungPoint = null;
  draggingManualRung = null;
  renderManualRungOverlay();
  updateManualRungStatus();
  toast('Cleared all manual rungs');
}

function getPreviewSvg() {
  const wrap = document.getElementById('stitch-preview');
  if (!wrap) return null;
  return wrap.querySelector('svg');
}

function stitchPreviewPoint(evt) {
  const svg = getPreviewSvg();
  if (!svg) return null;
  return svgPointFromMouse(svg, evt);
}

function ensureManualRungs(id) {
  if (!stitchManualRungs[id]) stitchManualRungs[id] = [];
  return stitchManualRungs[id];
}

function objectTransformString(obj) {
  if (obj.tx || obj.ty) return `translate(${obj.tx || 0},${obj.ty || 0})`;
  return '';
}

function findSatinObjectAtPoint(pt) {
  const svg = getPreviewSvg();
  if (!svg || !pt) return null;

  const ordered = stitchObjects.slice().sort((a, b) => (b.order || 0) - (a.order || 0));
  const selected = selectedManualRungTarget();
  if (selected) {
    const idx = ordered.findIndex(o => o.id === selected.id);
    if (idx >= 0) {
      ordered.splice(idx, 1);
      ordered.unshift(selected);
    }
  }

  for (const obj of ordered) {
    if ((stitchAssignments[obj.id] || 'fill') !== 'satin') continue;
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', obj.d);
    const tr = objectTransformString(obj);
    if (tr) p.setAttribute('transform', tr);
    p.setAttribute('fill-rule', 'evenodd');
    p.setAttribute('clip-rule', 'evenodd');

    try {
      const local = new DOMPoint(pt.x, pt.y);
      if (p.isPointInFill && p.isPointInFill(local)) return obj;
    } catch (e) {}

    try {
      p.setAttribute('fill', 'transparent');
      p.setAttribute('stroke', 'none');
      svg.appendChild(p);
      const bb = p.getBBox();
      p.remove();
      if (pt.x >= bb.x && pt.x <= bb.x + bb.width && pt.y >= bb.y && pt.y <= bb.y + bb.height) {
        return obj;
      }
    } catch (e) {
      try { p.remove(); } catch (_) {}
    }
  }
  return selected || null;
}

function renderManualRungOverlay() {
  const svg = getPreviewSvg();
  if (!svg) return;

  const existing = svg.querySelector('#manual-rung-overlay');
  if (existing) existing.remove();

  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('id', 'manual-rung-overlay');
  g.setAttribute('pointer-events', 'all');

  const obj = selectedManualRungTarget();
  const selectedId = obj ? obj.id : null;

  Object.entries(stitchManualRungs).forEach(([objId, rungs]) => {
    const isSel = objId === selectedId;
    const objForLabel = stitchObjects.find(o => o.id === objId);
    rungs.forEach((rung, idx) => {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', rung.a[0]);
      line.setAttribute('y1', rung.a[1]);
      line.setAttribute('x2', rung.b[0]);
      line.setAttribute('y2', rung.b[1]);
      line.setAttribute('stroke', isSel ? '#ff2bd6' : '#b46cff');
      line.setAttribute('stroke-width', isSel ? '2.2' : '1.2');
      line.setAttribute('stroke-opacity', isSel ? '0.95' : '0.45');
      line.setAttribute('stroke-linecap', 'round');
      line.setAttribute('vector-effect', 'non-scaling-stroke');
      g.appendChild(line);

      const midx = (rung.a[0] + rung.b[0]) / 2;
      const midy = (rung.a[1] + rung.b[1]) / 2;
      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('x', midx + 4);
      label.setAttribute('y', midy - 4);
      label.setAttribute('fill', isSel ? '#ff2bd6' : '#b46cff');
      label.setAttribute('font-size', '9');
      label.setAttribute('font-family', 'monospace');
      label.setAttribute('stroke', '#111');
      label.setAttribute('stroke-width', '0.25');
      label.setAttribute('paint-order', 'stroke');
      label.setAttribute('vector-effect', 'non-scaling-stroke');
      label.textContent = objForLabel ? (objForLabel.label || objId) : objId;
      g.appendChild(label);

      ['a', 'b'].forEach((endKey) => {
        const pt = rung[endKey];
        const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        c.setAttribute('cx', pt[0]);
        c.setAttribute('cy', pt[1]);
        c.setAttribute('r', isSel ? '3.2' : '2.2');
        c.setAttribute('fill', endKey === 'a' ? '#ffea00' : '#ff2bd6');
        c.setAttribute('stroke', '#111');
        c.setAttribute('stroke-width', '0.8');
        c.setAttribute('vector-effect', 'non-scaling-stroke');
        c.style.cursor = isSel ? 'grab' : 'default';
        if (isSel) {
          c.addEventListener('mousedown', (ev) => {
            ev.stopPropagation();
            draggingManualRung = {objId, idx, endKey};
            c.style.cursor = 'grabbing';
          });
        }
        g.appendChild(c);
      });
    });
  });

  if (pendingManualRungPoint) {
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('cx', pendingManualRungPoint[0]);
    c.setAttribute('cy', pendingManualRungPoint[1]);
    c.setAttribute('r', '3.5');
    c.setAttribute('fill', '#ffea00');
    c.setAttribute('stroke', '#111');
    c.setAttribute('stroke-width', '0.8');
    c.setAttribute('vector-effect', 'non-scaling-stroke');
    g.appendChild(c);
  }

  svg.appendChild(g);

  svg.onmousemove = (ev) => {
    if (!draggingManualRung) return;
    const p = stitchPreviewPoint(ev);
    if (!p) return;
    const rungs = stitchManualRungs[draggingManualRung.objId] || [];
    const rung = rungs[draggingManualRung.idx];
    if (!rung) return;
    rung[draggingManualRung.endKey] = [p.x, p.y];
    renderManualRungOverlay();
  };

  svg.onmouseup = () => {
    draggingManualRung = null;
    renderManualRungOverlay();
  };

  svg.onmouseleave = () => {
    draggingManualRung = null;
    renderManualRungOverlay();
  };

  svg.onclick = (ev) => {
    if (!manualRungMode) return;
    if (draggingManualRung) return;

    const p = stitchPreviewPoint(ev);
    if (!p) return;
    ev.stopPropagation();

    if (!pendingManualRungPoint) {
      const hitObj = findSatinObjectAtPoint(p);
      if (!hitObj) {
        toast('Click inside a satin object to start a manual rung');
        return;
      }
      stitchSelectedId = hitObj.id;
      stitchCheckedIds.clear();
      pendingManualRungPoint = [p.x, p.y];
      renderStitchList();
      updateStitchDetail();
      updateManualRungStatus();
      renderManualRungOverlay();
      toast('Manual rung target: ' + (hitObj.label || hitObj.id) + '. Pick the opposite side.');
      return;
    }

    const objNow = selectedManualRungTarget();
    if (!objNow) {
      pendingManualRungPoint = null;
      toast('Select a satin object first');
      renderManualRungOverlay();
      return;
    }

    const rungs = ensureManualRungs(objNow.id);
    rungs.push({a: pendingManualRungPoint, b: [p.x, p.y]});
    pendingManualRungPoint = null;
    updateManualRungStatus();
    renderManualRungOverlay();
    toast('Manual guide rung added to ' + (objNow.label || objNow.id) + '. Preview stitches to apply.');
  };

  updateManualRungStatus();
}

async function previewStitches() {
  if (!stitchLoaded) {
    loadStitchPane();
    if (!stitchLoaded) return;
  }

  const wrap = document.getElementById('stitch-preview');
  const meta = document.getElementById('stitch-preview-meta');
  if (wrap) wrap.innerHTML = '<span style="color:#555">Generating stitch preview…</span>';

  try {
    const res = await fetch('/api/stitches/preview', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        svg_w: structureSvgW,
        svg_h: structureSvgH,
        objects: stitchObjects,
        assignments: stitchAssignments,
        manual_rungs: cleanManualRungsPayload(),
        settings: currentStitchSettings()
      })
    });
    const data = await res.json();
    if (!data.ok) {
      toast('Stitch preview failed: ' + (data.error || 'unknown'), 9000);
      console.error(data.trace);
      renderStitchPreview();
      return;
    }

    currentStitchPreview = {
      svg: data.svg,
      counts: data.counts || {},
      layers: data.layers || {},
      debug_svg: data.debug_svg || ''
    };
    renderCachedStitchPreview();
    toast('Stitch preview generated');
  } catch (e) {
    toast('Stitch preview error: ' + e, 9000);
    renderStitchPreview();
  }
}


function safeBaseFileName() {
  // Prefer the browser-selected input file name.
  try {
    const fileInput = document.getElementById('file-input') || document.querySelector('input[type="file"]');
    if (fileInput && fileInput.files && fileInput.files.length && fileInput.files[0].name) {
      return fileInput.files[0].name.replace(/\.[^.]+$/, '');
    }
  } catch (e) {}

  // Fall back to globals used by some older EasyStitch versions, if present.
  try {
    if (typeof currentFileName !== 'undefined' && currentFileName) {
      return String(currentFileName).replace(/\.[^.]+$/, '');
    }
  } catch (e) {}

  try {
    if (typeof loadedFileName !== 'undefined' && loadedFileName) {
      return String(loadedFileName).replace(/\.[^.]+$/, '');
    }
  } catch (e) {}

  return 'easystitch';
}

function downloadText(filename, text) {
  const blob = new Blob([text], {type: 'application/json;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadBase64Binary(filename, b64, mime='application/octet-stream') {
  const raw = atob(b64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  const blob = new Blob([bytes], {type: mime});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function updateExportStats(stats) {
  const el = document.getElementById('export-stats');
  if (!el) return;
  if (!stats) {
    el.innerHTML = 'No machine export yet.';
    return;
  }
  el.innerHTML =
    `<b>${stats.format || 'Export'}</b><br>` +
    `Records: ${stats.records || 0}<br>` +
    `Stitches / jumps / trims: ${stats.stitches || 0} / ${stats.jumps || 0} / ${stats.trims || 0}<br>` +
    `Colour changes: ${stats.color_changes || 0}<br>` +
    `Size: ${stats.width_mm || 0} × ${stats.height_mm || 0} mm<br>` +
    `Bounds 0.1mm: X ${stats.min_x_01mm || 0}..${stats.max_x_01mm || 0}, Y ${stats.min_y_01mm || 0}..${stats.max_y_01mm || 0}<br>` +
    `<span style="color:#9da7c4">${stats.note || ''}</span>`;
}

async function requestDstExport() {
  if (!currentStitchPlan) {
    await generateStitchPlan();
    if (!currentStitchPlan) {
      toast('No stitch plan available to export', 6000);
      return null;
    }
  }

  const filename = safeBaseFileName() + '.dst';
  const res = await fetch('/api/stitches/export_dst', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      filename,
      plan: currentStitchPlan,
      settings: currentStitchSettings()
    })
  });
  const data = await res.json();
  if (!data.ok) {
    toast('DST export failed: ' + (data.error || 'unknown'), 9000);
    console.error(data.trace);
    return null;
  }
  currentExportDebug = data.debug || null;
  updateExportStats(data.stats);
  return data;
}

async function requestJefExport() {
  if (!currentStitchPlan) {
    await generateStitchPlan();
    if (!currentStitchPlan) {
      toast('No stitch plan available to export', 6000);
      return null;
    }
  }

  const filename = safeBaseFileName() + '.jef';
  const res = await fetch('/api/stitches/export_jef', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      filename,
      plan: currentStitchPlan,
      settings: currentStitchSettings()
    })
  });
  const data = await res.json();
  if (!data.ok) {
    toast('JEF export failed: ' + (data.error || 'unknown'), 9000);
    console.error(data.trace || data);
    return null;
  }
  currentExportDebug = data.debug || null;
  updateExportStats(data.stats);
  return data;
}

async function exportStitchPlanJef() {
  try {
    const data = await requestJefExport();
    if (!data) return;
    downloadBase64Binary(data.filename || (safeBaseFileName() + '.jef'), data.jef_base64, 'application/octet-stream');
    toast('JEF exported: ' + (data.stats ? (data.stats.records + ' records') : data.filename));
  } catch (e) {
    console.error(e);
    toast('JEF export error: ' + e, 9000);
  }
}

async function requestVp3Export() {
  if (!currentStitchPlan) {
    await generateStitchPlan();
    if (!currentStitchPlan) {
      toast('No stitch plan available to export', 6000);
      return null;
    }
  }

  const filename = safeBaseFileName() + '.vp3';
  const res = await fetch('/api/stitches/export_vp3', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      filename,
      plan: currentStitchPlan,
      settings: currentStitchSettings()
    })
  });
  const data = await res.json();
  if (!data.ok) {
    toast('VP3 export failed: ' + (data.error || 'unknown'), 9000);
    console.error(data.trace || data);
    return null;
  }
  currentExportDebug = data.debug || null;
  updateExportStats(data.stats);
  return data;
}

async function exportStitchPlanVp3() {
  try {
    const data = await requestVp3Export();
    if (!data) return;
    downloadBase64Binary(data.filename || (safeBaseFileName() + '.vp3'), data.vp3_base64, 'application/octet-stream');
    toast('VP3 exported: ' + (data.stats ? (data.stats.records + ' records') : data.filename));
  } catch (e) {
    console.error(e);
    toast('VP3 export error: ' + e, 9000);
  }
}

async function exportStitchPlanDst() {
  try {
    const data = await requestDstExport();
    if (!data) return;
    downloadBase64Binary(data.filename || (safeBaseFileName() + '.dst'), data.dst_base64, 'application/octet-stream');
    toast('DST exported: ' + (data.stats ? (data.stats.records + ' records') : data.filename));
  } catch (e) {
    console.error(e);
    toast('DST export error: ' + e, 9000);
  }
}

async function saveExportDebugJson() {
  try {
    let debug = currentExportDebug;
    if (!debug) {
      const data = await requestDstExport();
      if (!data) return;
      debug = data.debug;
    }
    if (!debug) {
      toast('No export debug data available', 6000);
      return;
    }
    downloadText(
      safeBaseFileName() + '_export_debug.json',
      JSON.stringify(debug, null, 2)
    );
    toast('Export debug JSON downloaded');
  } catch (e) {
    console.error(e);
    toast('Save export debug failed: ' + e, 9000);
  }
}

function updateStitchPlanStats(stats) {
  const el = document.getElementById('stitch-plan-stats');
  if (!el) return;
  if (!stats) {
    el.textContent = 'No stitch plan generated yet.';
    return;
  }
  el.innerHTML = `
    Objects used: ${stats.objects_used || 0}<br>
    Fill / Satin objects: ${stats.fill_objects || 0} / ${stats.satin_objects || 0}<br>
    Total stitch events: ${stats.stitches || 0}<br>
    Underlay / Top stitches: ${stats.underlay_stitches || 0} / ${stats.top_stitches || 0}<br>
    Jumps / Trims / Colour changes: ${stats.jumps || 0} / ${stats.trims || 0} / ${stats.color_changes || 0}<br>
    Jump/trim threshold: ${(stats.jump_threshold_mm || 0).toFixed(1)}mm<br>
    Underlay long-jump trim: ${(stats.underlay_jump_trim_threshold_mm || 0).toFixed(1)}mm<br>
    Underlay protects lighter: ${stats.underlay_protect_lighter ? 'on' : 'off'} · threshold ${stats.underlay_light_threshold || 0}<br>
    Small gap fill: ${(stats.small_gap_fill_mm || 0).toFixed(1)}mm<br>
    Satin underlay: ${stats.satin_underlay_mode || 'contour_centerline'}<br>
    Satin top order: ${stats.satin_top_order || 'zigzag_ladder'}<br>
    Top fill order: ${stats.top_fill_order || 'lane_serpentine'}<br>
    Long jump connector: ${stats.long_jump_connector_policy || 'hidden-if-safe'}<br>
    Manual/cut guide rungs: ${stats.manual_rungs || 0} · from cuts ${stats.cut_guide_rungs || 0}<br>
    Auto fill direction objects: ${stats.auto_fill_direction_objects || 0}<br>
    Avoid top fill overlap: ${stats.avoid_top_fill_overlap ? 'on' : 'off'}<br>
    Estimated size: ${(stats.estimated_width_mm || 0).toFixed(1)}mm × ${(stats.estimated_height_mm || 0).toFixed(1)}mm<br>
    Scale applied: ${stats.design_scale_applied ? 'yes' : 'no'}${stats.effective_dpi ? ' · effective DPI ' + stats.effective_dpi.toFixed(1) : ''}
  `;
}

function stitchPlanSvgMarkup() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${structureSvgW}" height="${structureSvgH}" viewBox="0 0 ${structureSvgW} ${structureSvgH}"></svg>`;
}

function appendSvgLine(svg, a, b, color, width, opacity, dash='') {
  const ln = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  ln.setAttribute('x1', a.x);
  ln.setAttribute('y1', a.y);
  ln.setAttribute('x2', b.x);
  ln.setAttribute('y2', b.y);
  ln.setAttribute('stroke', color);
  ln.setAttribute('stroke-width', String(width));
  ln.setAttribute('stroke-opacity', String(opacity));
  ln.setAttribute('stroke-linecap', 'round');
  ln.setAttribute('vector-effect', 'non-scaling-stroke');
  if (dash) ln.setAttribute('stroke-dasharray', dash);
  svg.appendChild(ln);
}

function appendSvgDot(svg, p, color, r, label='') {
  const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  c.setAttribute('cx', p.x);
  c.setAttribute('cy', p.y);
  c.setAttribute('r', String(r));
  c.setAttribute('fill', color);
  c.setAttribute('stroke', '#111');
  c.setAttribute('stroke-width', '0.6');
  c.setAttribute('vector-effect', 'non-scaling-stroke');
  svg.appendChild(c);
  if (label) {
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    t.setAttribute('x', p.x + 4);
    t.setAttribute('y', p.y - 4);
    t.setAttribute('fill', color);
    t.setAttribute('font-size', '9');
    t.setAttribute('font-family', 'monospace');
    t.setAttribute('stroke', '#111');
    t.setAttribute('stroke-width', '0.25');
    t.setAttribute('paint-order', 'stroke');
    t.setAttribute('vector-effect', 'non-scaling-stroke');
    t.textContent = label;
    svg.appendChild(t);
  }
}

function eventPoint(ev) {
  if (typeof ev.x !== 'number' || typeof ev.y !== 'number') return null;
  return {x: ev.x, y: ev.y};
}

function setupPlanPlayhead() {
  const slider = document.getElementById('plan-playhead');
  const val = document.getElementById('plan-playhead-val');
  const total = currentStitchPlan ? ((currentStitchPlan.events || []).length) : 0;
  if (slider) {
    slider.max = String(Math.max(0, total - 1));
    slider.value = String(Math.min(stitchPlanPlayIndex, Math.max(0, total - 1)));
  }
  if (val) val.textContent = `${Math.min(stitchPlanPlayIndex, Math.max(0, total - 1))}/${Math.max(0, total - 1)}`;
}

function viewFullStitchPlan() {
  if (!currentStitchPlan) {
    toast('Generate a stitch plan first');
    return;
  }
  const events = currentStitchPlan.events || [];
  const maxIndex = Math.max(0, events.length - 1);
  stitchPlanPlayIndex = maxIndex;
  viewStitchPlan(maxIndex);
}

function viewStitchPlan(limitIndex=null) {
  if (!currentStitchPlan) {
    toast('Generate a stitch plan first');
    return;
  }

  const events = currentStitchPlan.events || [];
  const maxEventsControl = parseInt(document.getElementById('plan-max-events')?.value || '15000', 10);
  const playLimit = limitIndex === null ? maxEventsControl : Math.max(0, Math.min(limitIndex, events.length - 1));

  const wrap = document.getElementById('stitch-preview');
  if (!wrap) return;

  wrap.innerHTML = stitchPlanSvgMarkup();
  restorePreviewBg('stitch-preview');
  const svg = wrap.querySelector('svg');
  svg.style.maxWidth = '100%';
  svg.style.maxHeight = '75vh';
  svg.style.width = 'auto';
  svg.style.height = 'auto';

  renderHoopRulers(svg);
  const showStitches = document.getElementById('plan-show-stitches')?.checked ?? true;
  const showJumps = document.getElementById('plan-show-jumps')?.checked ?? true;
  const showTrims = document.getElementById('plan-show-trims')?.checked ?? true;

  let last = null;
  let drawnStitches = 0;
  let drawnJumps = 0;
  let drawnTrims = 0;
  let processed = 0;
  let currentColor = '#000000';

  for (let idx = 0; idx < events.length; idx++) {
    if (idx > playLimit) break;
    const ev = events[idx];
    processed += 1;

    if (ev.type === 'color_change') {
      currentColor = ev.color || currentColor;
      last = null;
      continue;
    }

    if (ev.type === 'move') {
      last = eventPoint(ev);
      continue;
    }

    if (ev.type === 'jump') {
      const p = eventPoint(ev);
      if (p && last && showJumps) {
        appendSvgLine(svg, last, p, '#ff4040', 1.25, 0.9, '5 4');
        drawnJumps += 1;
      }
      if (p) last = p;
      continue;
    }

    if (ev.type === 'trim') {
      if (last && showTrims) {
        appendSvgDot(svg, last, '#ffea00', 3.6, 'trim');
        drawnTrims += 1;
      }
      last = null;
      continue;
    }

    if (ev.type === 'stitch') {
      const p = eventPoint(ev);
      if (p && last && showStitches) {
        const layer = ev.layer || '';
        const width = layer.includes('underlay') ? 0.65 : 1.0;
        const opacity = layer.includes('underlay') ? 0.55 : 0.95;
        appendSvgLine(svg, last, p, ev.color || currentColor, width, opacity);
        if (shouldShowStitchDots()) {
          const dotColor = svgDotColorForLine(ev.color || currentColor);
          appendSvgDot(svg, p, dotColor, layer.includes('underlay') ? 1.05 : 1.25, '');
        }
        drawnStitches += 1;
      }
      if (p) last = p;
      continue;
    }
  }

  // Current needle marker.
  const currentEv = events[Math.min(playLimit, events.length - 1)];
  const currentPt = currentEv ? eventPoint(currentEv) : null;
  if (currentPt) appendSvgDot(svg, currentPt, '#00ff66', 4.2, 'needle');

  const meta = document.getElementById('stitch-preview-meta');
  if (meta) {
    meta.textContent = `Plan view: ${drawnStitches} stitches · ${drawnJumps} jumps · ${drawnTrims} trims · processed ${processed}/${events.length}`;
  }

  stitchPlanPlayIndex = Math.min(playLimit, Math.max(0, events.length - 1));
  setupPlanPlayhead();
  setWorkZoom(workZoom);
}

function seekStitchPlan(index) {
  pauseStitchPlan(false);
  if (!currentStitchPlan) {
    toast('Generate a stitch plan first');
    return;
  }
  stitchPlanPlayIndex = Math.max(0, Math.min(index || 0, (currentStitchPlan.events || []).length - 1));
  viewStitchPlan(stitchPlanPlayIndex);
}

function playStitchPlan() {
  if (!currentStitchPlan) {
    toast('Generate a stitch plan first');
    return;
  }
  if (stitchPlanPlayTimer) clearInterval(stitchPlanPlayTimer);

  const events = currentStitchPlan.events || [];
  const total = events.length;
  if (!total) return;

  // Step size scales with plan size so playback is usable even for 50k+ events.
  const step = Math.max(25, Math.ceil(total / 700));
  stitchPlanPlayTimer = setInterval(() => {
    stitchPlanPlayIndex += step;
    if (stitchPlanPlayIndex >= total - 1) {
      stitchPlanPlayIndex = total - 1;
      pauseStitchPlan(false);
    }
    viewStitchPlan(stitchPlanPlayIndex);
  }, 80);

  toast('Playing stitch plan');
}

function pauseStitchPlan(showToast=true) {
  if (stitchPlanPlayTimer) {
    clearInterval(stitchPlanPlayTimer);
    stitchPlanPlayTimer = null;
    if (showToast) toast('Playback paused');
  }
}

async function generateStitchPlan() {
  if (!stitchLoaded) {
    loadStitchPane();
    if (!stitchLoaded) return;
  }

  try {
    const res = await fetch('/api/stitches/plan', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        svg_w: structureSvgW,
        svg_h: structureSvgH,
        objects: stitchObjects,
        assignments: stitchAssignments,
        manual_rungs: cleanManualRungsPayload(),
        settings: currentStitchSettings()
      })
    });
    const data = await res.json();
    if (!data.ok) {
      toast('Stitch plan failed: ' + (data.error || 'unknown'), 9000);
      console.error(data.trace);
      return;
    }
    currentStitchPlan = data.plan;
    stitchPlanPlayIndex = 0;
    pauseStitchPlan(false);
    updateStitchPlanStats(currentStitchPlan.stats);
    setupPlanPlayhead();
    viewFullStitchPlan();
    toast('Stitch plan generated: ' + (currentStitchPlan.stats.stitches || 0) + ' stitch events');
  } catch (e) {
    toast('Stitch plan error: ' + e, 9000);
  }
}

async function saveStitchPlanJson() {
  try {
    if (!currentStitchPlan) {
      await generateStitchPlan();
      if (!currentStitchPlan) {
        toast('No stitch plan available to save', 6000);
        return;
      }
    }
    downloadText(
      safeBaseFileName() + '_stitch_plan.json',
      JSON.stringify(currentStitchPlan, null, 2)
    );
    toast('Stitch plan JSON downloaded');
  } catch (e) {
    console.error(e);
    toast('Save stitch plan failed: ' + e, 9000);
  }
}

function saveStitchJson() {
  if (!structureLoaded && !stitchLoaded) { toast('Load prepared structure first'); return; }
  const objectsForSave = stitchLoaded ? stitchObjects : structureObjects;
  const payload = {
    version: 1,
    source_svg: lastTrace ? lastTrace.output_path : null,
    svg_w: structureSvgW,
    svg_h: structureSvgH,
    objects: objectsForSave,
    assignments: stitchAssignments,
    manual_rungs: cleanManualRungsPayload(),
    settings: currentStitchSettings(),
    note: 'EasyStitch stitch assignment map with stitch settings. Final satin/export is the next stage.'
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'easystitch_stitch_assignments.json';
  a.click();
  URL.revokeObjectURL(a.href);
}

function toast(msg, ms=3200) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), ms);
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.preview-area').forEach(area => {
    area.addEventListener('wheel', e => {
      if (e.shiftKey) {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.08 : 0.08;
        setWorkZoom(workZoom + delta);
      }
    }, {passive:false});
  });
});

init();

setTimeout(ensurePreviewBgButtons, 50);
setTimeout(updateStructureToolButtons, 60);
setTimeout(updateDesignSizeInfo, 70);

setTimeout(ensurePreviewBgButtons, 100);
setTimeout(() => {
  ['prep-preview','trace-preview','structure-preview','stitch-preview'].forEach(restorePreviewBg);
}, 150);

</script>
</body>
</html>
"""



# ─────────────────────────────────────────────────────────────────────────────
# DST export helpers
# ─────────────────────────────────────────────────────────────────────────────

def _dst_encode_record(dx: int, dy: int, command: str = "stitch") -> bytes:
    """
    Encode one Tajima DST record using the same bit layout used by
    pyembroidery/libembroidery.

    dx/dy are in 0.1 mm units, each within [-121, 121].
    command: stitch, jump, color, stop, end.

    Important: DST's y-axis is inverted at the record-encoding level.
    """
    dx = int(round(dx))
    dy = int(round(dy))

    if command == "end":
        return bytes([0x00, 0x00, 0xF3])
    if command in ("color", "stop"):
        return bytes([0x00, 0x00, 0xC3])

    if dx < -121 or dx > 121 or dy < -121 or dy > 121:
        raise ValueError(f"DST movement out of range: dx={dx}, dy={dy}")

    # Pyembroidery flips the coordinate y space here.
    x = dx
    y = -dy

    b0 = 0
    b1 = 0
    b2 = 0

    if command == "jump":
        b2 |= 1 << 7

    # Stitch/jump low marker bits.
    b2 |= 1 << 0
    b2 |= 1 << 1

    # X: +81/-81, +27/-27, +9/-9, +3/-3, +1/-1
    if x > 40:
        b2 |= 1 << 2
        x -= 81
    if x < -40:
        b2 |= 1 << 3
        x += 81
    if x > 13:
        b1 |= 1 << 2
        x -= 27
    if x < -13:
        b1 |= 1 << 3
        x += 27
    if x > 4:
        b0 |= 1 << 2
        x -= 9
    if x < -4:
        b0 |= 1 << 3
        x += 9
    if x > 1:
        b1 |= 1 << 0
        x -= 3
    if x < -1:
        b1 |= 1 << 1
        x += 3
    if x > 0:
        b0 |= 1 << 0
        x -= 1
    if x < 0:
        b0 |= 1 << 1
        x += 1
    if x != 0:
        raise ValueError(f"Could not encode DST dx component: {dx}")

    # Y: +81/-81, +27/-27, +9/-9, +3/-3, +1/-1
    if y > 40:
        b2 |= 1 << 5
        y -= 81
    if y < -40:
        b2 |= 1 << 4
        y += 81
    if y > 13:
        b1 |= 1 << 5
        y -= 27
    if y < -13:
        b1 |= 1 << 4
        y += 27
    if y > 4:
        b0 |= 1 << 5
        y -= 9
    if y < -4:
        b0 |= 1 << 4
        y += 9
    if y > 1:
        b1 |= 1 << 7
        y -= 3
    if y < -1:
        b1 |= 1 << 6
        y += 3
    if y > 0:
        b0 |= 1 << 7
        y -= 1
    if y < 0:
        b0 |= 1 << 6
        y += 1
    if y != 0:
        raise ValueError(f"Could not encode DST dy component: {dy}")

    return bytes([b0, b1, b2])


def _split_dst_delta(dx: int, dy: int):
    """
    Split a large relative move into DST-encodable chunks.
    """
    dx = int(round(dx))
    dy = int(round(dy))
    chunks = []
    while dx != 0 or dy != 0:
        sx = max(-121, min(121, dx))
        sy = max(-121, min(121, dy))
        chunks.append((sx, sy))
        dx -= sx
        dy -= sy
    if not chunks:
        chunks.append((0, 0))
    return chunks


def _dst_header(label: str, record_count: int, color_changes: int,
                min_x: int, max_x: int, min_y: int, max_y: int) -> bytes:
    """
    Build 512 byte DST ASCII header.
    Extents are in 0.1mm units.
    """
    label = re.sub(r"[^A-Za-z0-9_ -]", "_", str(label or "EASYSTITCH"))[:16]
    lines = [
        f"LA:{label}\\r",
        f"ST:{int(record_count):7d}\\r",
        f"CO:{int(color_changes):3d}\\r",
        f"+X:{max(0, max_x):5d}\\r",
        f"-X:{abs(min(0, min_x)):5d}\\r",
        f"+Y:{max(0, max_y):5d}\\r",
        f"-Y:{abs(min(0, min_y)):5d}\\r",
        "AX:+00000\\r",
        "AY:+00000\\r",
        "MX:+00000\\r",
        "MY:+00000\\r",
        "PD:******\\r",
    ]
    data = "".join(lines).encode("ascii", "replace")
    if len(data) > 512:
        data = data[:512]
    return data + b" " * (512 - len(data))


def export_stitch_plan_to_dst(plan: dict, filename: str = "easystitch.dst",
                              settings: dict | None = None) -> tuple[bytes, dict]:
    """
    Convert EasyStitch internal stitch plan to a simple DST binary.

    Coordinates:
      SVG px -> mm using plan/settings dpi
      mm -> DST 0.1mm units
      centred around the SVG viewBox centre
      Y is flipped so embroidery positive Y is upward
    """
    if not plan or not isinstance(plan, dict):
        raise ValueError("No stitch plan supplied.")

    settings = settings or {}
    dpi = float(settings.get("dpi") or plan.get("settings", {}).get("dpi") or 96.0)
    svg_w = float(plan.get("svg_w") or plan.get("stats", {}).get("estimated_width_px") or 500)
    svg_h = float(plan.get("svg_h") or plan.get("stats", {}).get("estimated_height_px") or 500)
    px_to_dst = (25.4 / dpi) * 10.0

    events = plan.get("events") or []
    if not events:
        raise ValueError("Stitch plan has no events.")

    current = (0, 0)
    records = []
    positions = [(0, 0)]
    debug_records = []
    debug_trims = []
    debug_color_changes = []
    debug_long_moves = []
    color_changes = 0
    trims = 0
    jumps = 0
    stitches = 0

    def to_dst_point(ev):
        x = float(ev.get("x", 0.0))
        y = float(ev.get("y", 0.0))
        # Centre coordinates.  The DST record encoder performs the required
        # y-axis inversion, matching pyembroidery's writer.
        dxu = int(round((x - svg_w / 2.0) * px_to_dst))
        dyu = int(round((y - svg_h / 2.0) * px_to_dst))
        return dxu, dyu

    def emit_move_to(target, command, source_event=None):
        nonlocal current, jumps, stitches
        start = current
        total_dx = target[0] - current[0]
        total_dy = target[1] - current[1]
        move_len = math.hypot(total_dx, total_dy)
        chunks = _split_dst_delta(total_dx, total_dy)

        if move_len > 30:  # >3mm, useful for debug even when valid
            debug_long_moves.append({
                "from_01mm": list(start),
                "to_01mm": list(target),
                "dx_01mm": total_dx,
                "dy_01mm": total_dy,
                "length_mm": round(move_len / 10.0, 3),
                "command": command,
                "source_type": (source_event or {}).get("type"),
                "source_layer": (source_event or {}).get("layer"),
                "object_id": (source_event or {}).get("object_id"),
            })

        for i, (sx, sy) in enumerate(chunks):
            rec_cmd = command
            records.append(_dst_encode_record(sx, sy, rec_cmd))
            current = (current[0] + sx, current[1] + sy)
            positions.append(current)
            debug_records.append({
                "record_index": len(records),
                "command": rec_cmd,
                "dx_01mm": sx,
                "dy_01mm": sy,
                "x_01mm": current[0],
                "y_01mm": current[1],
                "source_type": (source_event or {}).get("type"),
                "source_layer": (source_event or {}).get("layer"),
                "object_id": (source_event or {}).get("object_id"),
            })
            if rec_cmd == "jump":
                jumps += 1
            elif rec_cmd == "stitch":
                stitches += 1

    for ev in events:
        et = ev.get("type")
        if et == "color_change":
            records.append(_dst_encode_record(0, 0, "color"))
            color_changes += 1
            debug_color_changes.append({
                "record_index": len(records),
                "color": ev.get("color"),
                "object_id": ev.get("object_id"),
                "layer": ev.get("layer"),
            })
            debug_records.append({
                "record_index": len(records),
                "command": "color",
                "dx_01mm": 0,
                "dy_01mm": 0,
                "x_01mm": current[0],
                "y_01mm": current[1],
                "source_type": et,
                "source_layer": ev.get("layer"),
                "object_id": ev.get("object_id"),
            })
            continue

        if et == "trim":
            # DST has no universal explicit trim.  Three zero-length jumps are
            # commonly used as a trim hint; many machines/software trim on
            # subsequent jumps according to machine settings.
            trim_record_start = len(records) + 1
            for _ in range(3):
                records.append(_dst_encode_record(0, 0, "jump"))
                debug_records.append({
                    "record_index": len(records),
                    "command": "trim_hint_jump",
                    "dx_01mm": 0,
                    "dy_01mm": 0,
                    "x_01mm": current[0],
                    "y_01mm": current[1],
                    "source_type": et,
                    "source_layer": ev.get("layer"),
                    "object_id": ev.get("object_id"),
                    "reason": ev.get("reason"),
                })
            debug_trims.append({
                "record_index": trim_record_start,
                "x_01mm": current[0],
                "y_01mm": current[1],
                "object_id": ev.get("object_id"),
                "color": ev.get("color"),
                "reason": ev.get("reason"),
                "distance_px": ev.get("distance_px"),
            })
            trims += 1
            jumps += 3
            continue

        if et not in ("move", "jump", "stitch"):
            continue

        target = to_dst_point(ev)
        if et == "stitch":
            emit_move_to(target, "stitch", ev)
        else:
            emit_move_to(target, "jump", ev)

    records.append(_dst_encode_record(0, 0, "end"))

    if positions:
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    else:
        min_x = max_x = min_y = max_y = 0

    label = Path(filename or "easystitch.dst").stem
    header = _dst_header(label, len(records), color_changes, min_x, max_x, min_y, max_y)
    dst_bytes = header + b"".join(records)

    stats = {
        "records": len(records),
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
        "format": "DST",
        "note": "DST has limited colour metadata and no guaranteed explicit trim command; trim events are encoded as jump hints. DST bit encoding follows pyembroidery/libembroidery layout."
    }
    debug = {
        "version": "easystitch-export-debug-v1",
        "format": "DST",
        "filename": str(filename or "easystitch.dst"),
        "stats": stats,
        "source_plan_stats": plan.get("stats", {}),
        "coordinate_scale": {
            "dpi": dpi,
            "px_to_01mm": px_to_dst,
            "svg_w": svg_w,
            "svg_h": svg_h,
            "centered": True,
            "design_scale": plan.get("settings", {}).get("design_scale", {}),
        },
        "trim_hints": debug_trims,
        "color_changes": debug_color_changes,
        "long_moves_over_3mm": debug_long_moves,
        # Keep the per-record list capped to avoid huge browser JSON in large
        # designs.  The counts/stats above remain complete.
        "records_sample_first_5000": debug_records[:5000],
        "records_total": len(debug_records),
        "notes": [
            "DST stores colour changes/stops but not rich thread palette metadata.",
            "DST trim events are encoded as three zero-length jump records as trim hints.",
            "Use VP3 or another richer format later for stronger trim/color metadata on compatible machines."
        ],
    }

    return dst_bytes, stats, debug


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

    import tempfile
    import os as _os

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
                _os.remove(tmp_path)
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
        "note": f"{fmt.upper()} written through pyembroidery. Exact trim/color behaviour depends on the reader/machine."
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



# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────

def create_app(initial_input: str | None, output_dir: str) -> Flask:
    app = Flask(__name__)
    app.config["CURRENT_INPUT"] = os.path.abspath(initial_input) if initial_input else None
    app.config["OUTPUT_DIR"] = os.path.abspath(output_dir)
    app.config["UPLOAD_DIR"] = os.path.join(app.config["OUTPUT_DIR"], "_uploads")
    app.config["LAST_PREP"] = None
    app.config["LAST_TRACE"] = None
    app.config["LAST_STRUCTURE"] = None
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)

    @app.route("/")
    def index():
        return HTML

    @app.route("/favicon.ico")
    def favicon():
        return ("", 204)

    @app.route("/api/state")
    def api_state():
        current = app.config.get("CURRENT_INPUT")
        return jsonify({
            "has_image": bool(current),
            "input_path": current,
            "input_name": os.path.basename(current) if current else None,
            "output_dir": app.config["OUTPUT_DIR"],
        })

    @app.route("/api/upload", methods=["POST"])
    def api_upload():
        try:
            if "image" not in request.files:
                return jsonify({"ok": False, "error": "No image file uploaded"})
            f = request.files["image"]
            if not f.filename:
                return jsonify({"ok": False, "error": "Empty filename"})
            name = safe_stem(f.filename) + Path(f.filename).suffix.lower()
            save_path = os.path.join(app.config["UPLOAD_DIR"], name)
            f.save(save_path)

            # Validate that PIL can open it.
            with Image.open(save_path) as img:
                img.verify()

            app.config["CURRENT_INPUT"] = os.path.abspath(save_path)
            return jsonify({"ok": True, "path": app.config["CURRENT_INPUT"], "name": os.path.basename(save_path)})
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})

    @app.route("/api/prep", methods=["POST"])
    def api_prep():
        try:
            current = app.config.get("CURRENT_INPUT")
            if not current:
                return jsonify({"ok": False, "error": "No image loaded"})
            body = request.get_json() or {}
            colors = int(body.get("colors", 12))
            max_size = int(body.get("max_size", 1000))
            result = run_image_prep(
                current,
                app.config["OUTPUT_DIR"],
                max_size=max_size,
                colors=colors,
                simplify_preset=str(body.get("simplify_preset", "none")),
                smoothing=int(body.get("smoothing", 0)),
                posterize_bits=int(body.get("posterize_bits", 0)),
                color_boost=float(body.get("color_boost", 1.0)),
                contrast_boost=float(body.get("contrast_boost", 1.0)),
            )
            app.config["LAST_PREP"] = result
            app.config["LAST_TRACE"] = None
            app.config["LAST_STRUCTURE"] = None
            return jsonify(result)
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})

    @app.route("/api/trace", methods=["POST"])
    def api_trace():
        try:
            prep = app.config.get("LAST_PREP")
            if not prep or not prep.get("output_path"):
                return jsonify({"ok": False, "error": "No prepared PNG available. Run Image Prep first."})

            body = request.get_json() or {}
            result = trace_prepared_png(
                prep["output_path"],
                app.config["OUTPUT_DIR"],
                stem=prep.get("stem", "image"),
                speckle=int(body.get("speckle", 8)),
                mode=str(body.get("mode", "spline")),
                hierarchical=str(body.get("hierarchical", "cutout")),
                color_precision=int(body.get("color_precision", 6)),
                gradient_step=int(body.get("gradient_step", 16)),
                corner_threshold=int(body.get("corner_threshold", 60)),
                segment_length=float(body.get("segment_length", 4.0)),
                splice_threshold=int(body.get("splice_threshold", 45)),
                path_precision=int(body.get("path_precision", 3)),
            )

            extraction_enabled = False
            if extraction_enabled:
                strokes = extract_stroke_candidates(
                    prep["output_path"],
                    min_component_area=int(body.get("stroke_min_area", 24)),
                    max_fill_ratio=float(body.get("stroke_max_fill_ratio", 0.42)),
                    min_aspect_ratio=float(body.get("stroke_min_aspect", 1.6)),
                    min_path_length=float(body.get("stroke_min_length", 14.0)),
                    ignore_near_white=bool(body.get("stroke_ignore_white", True)),
                )
                result.update(strokes)
            else:
                result.update({
                    "svg_w": prep.get("processed_width"),
                    "svg_h": prep.get("processed_height"),
                    "stroke_objects": [],
                    "stroke_count": 0,
                    "component_count": 0,
                    "stroke_preview_svg": "",
                })
            result["extraction_enabled"] = extraction_enabled

            app.config["LAST_TRACE"] = result
            app.config["LAST_STRUCTURE"] = None
            return jsonify(result)
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/structure/load")
    def api_structure_load():
        try:
            trace = app.config.get("LAST_TRACE")
            if not trace or not trace.get("output_path"):
                return jsonify({"ok": False, "error": "No traced SVG available. Run Trace first."})
            svg_w, svg_h, source_paths, objects = parse_traced_svg_for_structure(trace["output_path"])
            payload = {
                "ok": True,
                "svg_w": svg_w,
                "svg_h": svg_h,
                "source_paths": source_paths,
                "objects": objects,
            }
            app.config["LAST_STRUCTURE"] = payload
            return jsonify(payload)
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/structure/manual_split", methods=["POST"])
    def api_structure_manual_split():
        try:
            body = request.get_json() or {}
            obj = body.get("object")
            cut_points = body.get("cut_points") or []
            if not obj:
                return jsonify({"ok": False, "error": "No structure object supplied."})
            if len(cut_points) < 2:
                return jsonify({"ok": False, "error": "Two cut points are required."})
            out_objects = manual_split_object(obj, cut_points)
            cut_rung_count = sum(len(o.get("cut_guide_rungs") or []) for o in out_objects)
            return jsonify({"ok": True, "objects": out_objects, "cut_guide_rungs": cut_rung_count})
        except NeedSecondCutError as e:
            return jsonify({"ok": False, "needs_second_cut": True, "error": str(e)})
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/structure/junction_split", methods=["POST"])
    def api_structure_junction_split():
        try:
            body = request.get_json() or {}
            obj = body.get("object")
            center = body.get("center")
            branch_points = body.get("branch_points") or []
            if not obj:
                return jsonify({"ok": False, "error": "No structure object supplied."})
            if not center or len(branch_points) < 3:
                return jsonify({"ok": False, "error": "Junction split needs a centre and at least three branch points."})
            if (obj.get("render_mode") or "fill") == "stroke":
                return jsonify({"ok": False, "error": "Junction split currently works on fill/column shapes, not stroke paths."})
            out_objects = split_fill_object_by_junction(obj, center, branch_points)
            cut_rung_count = sum(len(o.get("cut_guide_rungs") or []) for o in out_objects)
            return jsonify({"ok": True, "objects": out_objects, "cut_guide_rungs": cut_rung_count})
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/stitches/preview", methods=["POST"])
    def api_stitches_preview():
        try:
            payload = request.get_json() or {}
            result = build_stitch_preview_svg(payload)
            return jsonify({
                "ok": True,
                "svg": result["svg"],
                "counts": result["counts"],
                "layers": result.get("layers", {}),
                "debug_svg": result.get("debug_svg", "")
            })
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/stitches/plan", methods=["POST"])
    def api_stitches_plan():
        try:
            payload = request.get_json() or {}
            plan = build_stitch_plan(payload)
            return jsonify({"ok": True, "plan": plan})
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/stitches/export_dst", methods=["POST"])
    def api_stitches_export_dst():
        try:
            body = request.get_json() or {}
            plan = body.get("plan")
            filename = body.get("filename") or "easystitch.dst"
            settings = body.get("settings") or {}
            dst_bytes, stats, debug = export_stitch_plan_to_dst(plan, filename=filename, settings=settings)
            return jsonify({
                "ok": True,
                "filename": filename if str(filename).lower().endswith(".dst") else str(filename) + ".dst",
                "dst_base64": base64.b64encode(dst_bytes).decode("ascii"),
                "stats": stats,
                "debug": debug
            })
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/stitches/export_jef", methods=["POST"])
    def api_stitches_export_jef():
        try:
            body = request.get_json() or {}
            plan = body.get("plan")
            filename = body.get("filename") or "easystitch.jef"
            settings = body.get("settings") or {}
            jef_bytes, stats, debug = export_stitch_plan_to_jef(plan, filename=filename, settings=settings)
            return jsonify({
                "ok": True,
                "filename": filename if str(filename).lower().endswith(".jef") else str(filename) + ".jef",
                "jef_base64": base64.b64encode(jef_bytes).decode("ascii"),
                "stats": stats,
                "debug": debug
            })
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/stitches/export_vp3", methods=["POST"])
    def api_stitches_export_vp3():
        try:
            body = request.get_json() or {}
            plan = body.get("plan")
            filename = body.get("filename") or "easystitch.vp3"
            settings = body.get("settings") or {}
            vp3_bytes, stats, debug = export_stitch_plan_to_vp3(plan, filename=filename, settings=settings)
            return jsonify({
                "ok": True,
                "filename": filename if str(filename).lower().endswith(".vp3") else str(filename) + ".vp3",
                "vp3_base64": base64.b64encode(vp3_bytes).decode("ascii"),
                "stats": stats,
                "debug": debug
            })
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    return app


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="EasyStitch unified app prototype")
    p.add_argument("input", nargs="?", help="Optional image to load on startup")
    p.add_argument("--output-dir", default=None, help="Output directory, default: input folder or cwd")
    p.add_argument("--port", type=int, default=5001)
    p.add_argument("--no-browser", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()

    if args.input and not os.path.isfile(args.input):
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    if args.output_dir:
        output_dir = os.path.abspath(args.output_dir)
    elif args.input:
        output_dir = os.path.dirname(os.path.abspath(args.input))
    else:
        output_dir = os.getcwd()

    app = create_app(args.input, output_dir)

    url = f"http://127.0.0.1:{args.port}"
    print("\n" + "=" * 58)
    print("  EasyStitch Unified App — Phase 19.0a")
    print("=" * 58)
    print(f"  URL       : {url}")
    print(f"  Output dir: {output_dir}")
    if args.input:
        print(f"  Input     : {os.path.abspath(args.input)}")
    else:
        print("  Input     : use browser upload")
    print("=" * 58 + "\n")

    if not args.no_browser:
        webbrowser.open(url)

    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()