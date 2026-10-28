"""Preprocessing: central crop + a FIXED, versioned set of enhancement variants.

No adaptive/runtime tuning — the variant set is frozen and versioned so results
are reproducible. The marking is centered by the wearer, so we crop the central
region (default 60%×60%, ~1555×1166 at full 2592×1944) then run OCR over each
variant; the resolver keeps whichever read scores best.

Also computes cheap image-quality signals (blur, glare) the resolver turns into
a specific recapture reason (U2).
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from . import config

# Bump this when the variant set changes — it participates in reproducibility.
VARIANT_SET_VERSION = "variants/1"

VARIANT_NAMES = ("original", "contrast", "denoise", "threshold", "sharpen")


@dataclass
class ImageQuality:
    blur_var: float          # variance of Laplacian — higher is sharper
    glare_frac: float        # fraction of near-saturated pixels
    width: int
    height: int


def load_rgb(image: Any) -> Image.Image:
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(bytes(image))).convert("RGB")
    if isinstance(image, np.ndarray):
        return Image.fromarray(image).convert("RGB")
    raise TypeError(f"unsupported image type {type(image)}")


def center_crop(img: Image.Image, frac: float | None = None) -> Image.Image:
    frac = config.CENTER_CROP_FRAC if frac is None else frac
    w, h = img.size
    cw, ch = int(w * frac), int(h * frac)
    left, top = (w - cw) // 2, (h - ch) // 2
    return img.crop((left, top, left + cw, top + ch))


def _laplacian_var(gray: np.ndarray) -> float:
    g = gray.astype(np.float32)
    lap = (
        -4.0 * g
        + np.roll(g, 1, 0) + np.roll(g, -1, 0)
        + np.roll(g, 1, 1) + np.roll(g, -1, 1)
    )
    # trim the wrapped borders
    return float(lap[1:-1, 1:-1].var())


def image_quality(crop: Image.Image) -> ImageQuality:
    gray = np.asarray(crop.convert("L"))
    blur_var = _laplacian_var(gray)
    glare_frac = float((gray >= 250).mean())
    return ImageQuality(blur_var=blur_var, glare_frac=glare_frac,
                        width=crop.width, height=crop.height)


def _otsu(gray: np.ndarray) -> np.ndarray:
    hist, _ = np.histogram(gray, bins=256, range=(0, 256))
    total = gray.size
    sum_total = np.dot(np.arange(256), hist)
    sum_b = w_b = 0.0
    best_t, best_var = 127, -1.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best_var:
            best_var, best_t = between, t
    return (gray > best_t).astype(np.uint8) * 255


def make_variants(crop: Image.Image) -> list[tuple[str, np.ndarray]]:
    """Fixed enhancement set. Returns (name, RGB uint8 ndarray) per variant."""
    out: list[tuple[str, np.ndarray]] = []

    # original (as cropped)
    out.append(("original", np.asarray(crop)))

    # contrast: autocontrast + a fixed contrast bump
    ac = ImageOps.autocontrast(crop, cutoff=1)
    ac = ImageEnhance.Contrast(ac).enhance(1.6)
    out.append(("contrast", np.asarray(ac)))

    # denoise: median filter to kill speckle on textured packages
    den = crop.filter(ImageFilter.MedianFilter(size=3))
    out.append(("denoise", np.asarray(den)))

    # threshold: grayscale -> Otsu binarization -> back to RGB
    gray = np.asarray(ImageOps.autocontrast(crop.convert("L"), cutoff=1))
    binimg = _otsu(gray)
    out.append(("threshold", np.stack([binimg] * 3, axis=-1)))

    # sharpen: unsharp mask
    sh = crop.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    out.append(("sharpen", np.asarray(sh)))

    return out


def preprocess(image: Any) -> tuple[Image.Image, list[tuple[str, np.ndarray]], ImageQuality]:
    """One call: load -> center crop -> quality -> variants."""
    img = load_rgb(image)
    crop = center_crop(img)
    quality = image_quality(crop)
    variants = make_variants(crop)
    return crop, variants, quality
