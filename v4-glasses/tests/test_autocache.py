"""Auto-cache tests: a web-found datasheet is registered once, offered-not-accepted."""
from __future__ import annotations

import types

import pytest

from catalog import autocache
from server import resolver, thresholds as th_mod
from server.catalog_api import Catalog
from server.ocr.base import OCRResult, OCRWord
from server.preprocess import ImageQuality

TH = th_mod.load()
Q = ImageQuality(blur_var=500, glare_frac=0.0, width=1000, height=800)


def _fake_pdf_get(*a, **k):
    r = types.SimpleNamespace()
    r.content = b"%PDF-1.4\n% fake datasheet\n%%EOF"
    r.raise_for_status = lambda: None
    return r


@pytest.fixture
def tmp_catalog(tmp_path, monkeypatch):
    parts = tmp_path / "parts.yaml"
    man = tmp_path / "manifest.yaml"
    parts.write_text("parts:\n  - part_number: NE555P\n    manufacturer: TI\n"
                     "    package: PDIP-8\n    top_markings: [\"NE555P\"]\n")
    man.write_text("entries:\n- orderable_number: NE555P\n  manufacturer: TI\n"
                   "  package: PDIP-8\n  url: http://x/ne555.pdf\n"
                   "  retrieval_date: '2026-01-01'\n  sha256: "
                   + "0" * 64 + "\n  cache_path: catalog/cache/ne555.pdf\n  reviewed: true\n")
    monkeypatch.setattr(autocache, "PARTS", parts)
    monkeypatch.setattr(autocache, "MANIFEST", man)
    monkeypatch.setattr(autocache, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(autocache.requests, "get", _fake_pdf_get)
    return parts, man


def test_cache_part_registers_new_part(tmp_catalog):
    parts, man = tmp_catalog
    out = autocache.cache_part("STM32F407VGT6", "https://st.com/stm32f407vgt6.pdf")
    assert out["cached"] is True and out["reviewed"] is False
    assert "STM32F407VGT6" in parts.read_text()
    assert "auto: true" in parts.read_text()
    assert "STM32F407VGT6" in man.read_text()
    assert (autocache.CACHE / "stm32f407vgt6.pdf").exists()


def test_cache_part_is_idempotent(tmp_catalog):
    autocache.cache_part("STM32F407VGT6", "https://st.com/stm32f407vgt6.pdf")
    again = autocache.cache_part("STM32F407VGT6", "https://st.com/stm32f407vgt6.pdf")
    assert again["cached"] is False and again["reason"] == "exists"


def test_cache_rejects_non_pdf(tmp_catalog):
    out = autocache.cache_part("FOO123", "https://x.com/page.html")
    assert out["cached"] is False


def test_cache_rejects_too_short(tmp_catalog):
    out = autocache.cache_part("55", "https://x.com/a.pdf")
    assert out["cached"] is False


# ---- resolver: auto-cached + unreviewed is OFFERED, never auto-accepted ----
def _ocr(text, conf=0.97):
    return [OCRResult(words=[OCRWord(text=text, confidence=conf, bbox=(400, 350, 60, 40))],
                      variant="original")]


def test_auto_unreviewed_part_is_candidate_not_accepted():
    cat = Catalog.from_dicts([
        {"part_number": "TPS7A4700RGWR", "manufacturer": "TI", "package": "VQFN",
         "top_markings": ["TPS7A4700RGWR"], "auto": True}])
    cat.parts[0].reviewed = False
    d = resolver.resolve(cat, _ocr("TPS7A4700RGWR"), TH, Q, (1000, 800))
    assert d.status == "candidate_selection"       # offered, not accepted
    assert d.result["candidates"][0]["part_number"] == "TPS7A4700RGWR"


def test_reviewed_auto_part_can_accept():
    cat = Catalog.from_dicts([
        {"part_number": "TPS7A4700RGWR", "manufacturer": "TI", "package": "VQFN",
         "top_markings": ["TPS7A4700RGWR"], "auto": True}])
    cat.parts[0].reviewed = True                    # human verified
    d = resolver.resolve(cat, _ocr("TPS7A4700RGWR"), TH, Q, (1000, 800))
    assert d.status == "accepted"
