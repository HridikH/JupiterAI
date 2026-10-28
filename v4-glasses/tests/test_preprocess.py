"""Preprocess + thresholds tests."""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from server import preprocess
from server import thresholds as th_mod
from server.thresholds import UnfrozenConfigError


def _jpeg(size=(2592, 1944), color=(100, 100, 100)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def test_center_crop_fraction():
    img = Image.new("RGB", (1000, 1000))
    crop = preprocess.center_crop(img, frac=0.60)
    assert crop.size == (600, 600)


def test_preprocess_returns_full_variant_set():
    crop, variants, quality = preprocess.preprocess(_jpeg())
    names = [n for n, _ in variants]
    assert names == list(preprocess.VARIANT_NAMES)
    for _, arr in variants:
        assert isinstance(arr, np.ndarray) and arr.ndim == 3
    assert quality.width == crop.width


def test_quality_detects_sharp_vs_flat():
    flat = Image.new("RGB", (400, 400), (128, 128, 128))
    noisy = Image.fromarray(
        (np.random.default_rng(0).integers(0, 255, (400, 400, 3))).astype(np.uint8))
    assert preprocess.image_quality(flat).blur_var < preprocess.image_quality(noisy).blur_var


def test_glare_fraction_high_on_white():
    white = Image.new("RGB", (100, 100), (255, 255, 255))
    assert preprocess.image_quality(white).glare_frac > 0.9


# ---- thresholds -------------------------------------------------------------
def test_default_is_unfrozen():
    t = th_mod.load()
    assert t.frozen is False
    assert t.config_hash is not None


def test_require_frozen_refuses_unfrozen(tmp_path):
    p = tmp_path / "thresholds.json"
    th_mod.write_example(p)
    with pytest.raises(UnfrozenConfigError):
        th_mod.load(p, require_frozen=True)


def test_freeze_then_load_verifies_hash(tmp_path):
    p = tmp_path / "thresholds.json"
    th_mod.write_example(p)
    frozen = th_mod.freeze(p)
    assert frozen.frozen is True
    loaded = th_mod.load(p, require_frozen=True)   # must not raise
    assert loaded.config_hash == frozen.config_hash


def test_tampered_frozen_file_rejected(tmp_path):
    import json
    p = tmp_path / "thresholds.json"
    th_mod.write_example(p)
    th_mod.freeze(p)
    raw = json.loads(p.read_text())
    raw["accept_score"] = 0.01          # tamper after freezing
    p.write_text(json.dumps(raw))
    with pytest.raises(UnfrozenConfigError):
        th_mod.load(p, require_frozen=True)
