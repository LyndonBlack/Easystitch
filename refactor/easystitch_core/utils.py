#!/usr/bin/env python3
"""
EasyStitch Core — Utility functions extracted from the monolith.

Contains colour-space conversion, math helpers, image utilities,
and SVG drawing helpers used throughout EasyStitch.
"""

import base64
import io
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image


# ─────────────────────────────────────────────────────────────────────────────
# Exception classes
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Misc helpers / underlay
# ─────────────────────────────────────────────────────────────────────────────

def mm_to_px(mm: float, dpi: float = 96.0) -> float:
    return float(mm) * float(dpi) / 25.4


# ─────────────────────────────────────────────────────────────────────────────
# Hex colour helpers
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2b: stroke candidate extraction from prepared raster
# ─────────────────────────────────────────────────────────────────────────────

def _neighbors8(y: int, x: int, h: int, w: int):
    for ny in range(max(0, y - 1), min(h, y + 2)):
        for nx in range(max(0, x - 1), min(w, x + 2)):
            if ny == y and nx == x:
                continue
            yield ny, nx


# ─────────────────────────────────────────────────────────────────────────────
# Polyline utilities
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Pane 4 stitch preview / underlay helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rotate_xy(x: float, y: float, angle_rad: float):
    ca, sa = math.cos(angle_rad), math.sin(angle_rad)
    return x * ca - y * sa, x * sa + y * ca


def _rotate_geom(geom, angle_rad: float):
    from shapely.ops import transform
    return transform(lambda x, y, z=None: _rotate_xy(x, y, angle_rad), geom)


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


# ─────────────────────────────────────────────────────────────────────────────
# SVG drawing helpers
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Image/data URI helpers
# ─────────────────────────────────────────────────────────────────────────────

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
