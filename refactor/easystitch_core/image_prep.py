#!/usr/bin/env python3
"""
EasyStitch Core — Image preparation functions.

Handles image normalisation (EXIF rotation, alpha compositing, resize),
simplification filters (smoothing, posterize, color/contrast enhancement),
quantization (KMeans in Lab colourspace), and the full image-prep pipeline.
"""

import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, ImageFilter, ImageEnhance
from sklearn.cluster import MiniBatchKMeans

from .utils import rgb2lab, lab2rgb, safe_stem, image_to_data_uri


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
