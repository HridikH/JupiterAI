"""Resolver + confusion tests (V3 decision logic, U2 recapture reasons).

The resolver is the safety-critical module, so these tests pin the decision
boundaries and, crucially, that nothing uncertain is ever auto-accepted.
"""
from __future__ import annotations

import pytest

from server import confusion, resolver
from server.catalog_api import Catalog
from server.ocr.base import OCRResult, OCRWord
from server.preprocess import ImageQuality
from server import thresholds as th_mod

TH = th_mod.load()  # dev defaults (unfrozen is fine for resolver logic tests)
GOOD_Q = ImageQuality(blur_var=500.0, glare_frac=0.0, width=1000, height=800)
CROP = (1000, 800)


def ocr(words, variant="original"):
    """words: list of (text, conf) or (text, conf, bbox)."""
    out = []
    for w in words:
        if len(w) == 3:
            out.append(OCRWord(text=w[0], confidence=w[1], bbox=w[2]))
        else:
            out.append(OCRWord(text=w[0], confidence=w[1], bbox=(400, 350, 60, 40)))
    return [OCRResult(words=out, variant=variant, engine="mock")]


def catalog():
    # CD4011B vs CD40118 differ only by a confusable last glyph (B<->8), so a
    # read of one is near-equidistant from both -> a genuine tight-margin case.
    return Catalog.from_dicts([
        {"part_number": "NE555P", "manufacturer": "TI", "package": "PDIP-8",
         "top_markings": ["NE555P", "NE555"], "aliases": ["555"]},
        {"part_number": "CD4011B", "manufacturer": "TI", "package": "PDIP-14",
         "top_markings": ["CD4011B"]},
        {"part_number": "CD40118", "manufacturer": "TI", "package": "PDIP-14",
         "top_markings": ["CD40118"]},
    ])


# ---- confusion map ----------------------------------------------------------
def test_identical_is_one():
    assert confusion.similarity("NE555", "NE555") == 1.0


def test_confusable_scores_high():
    # 5<->S, 8<->B are confusable -> should stay close to 1
    assert confusion.similarity("NE5S5", "NE555") > 0.9
    assert confusion.similarity("LM35B", "LM358") > 0.9


def test_unrelated_scores_low():
    assert confusion.similarity("NE555", "LM741") < 0.6


# ---- accept (V3: BOTH bars) -------------------------------------------------
def test_clean_read_auto_accepts():
    d = resolver.resolve(catalog(), ocr([("NE555P", 0.96)]), TH, GOOD_Q, CROP)
    assert d.status == "accepted"
    assert d.result["part_number"] == "NE555P"
    assert d.margin >= TH.accept_margin


def test_high_score_but_low_margin_is_candidate_not_accept():
    # 'CD4011B' matches CD4011B exactly AND CD40118 via B<->8 confusion -> tight margin
    d = resolver.resolve(catalog(), ocr([("CD4011B", 0.9)]), TH, GOOD_Q, CROP)
    assert d.status == "candidate_selection"
    nums = {c["part_number"] for c in d.result["candidates"]}
    assert {"CD4011B", "CD40118"} <= nums
    # safety: margin really is under the accept bar
    assert d.margin < TH.accept_margin


def test_candidate_set_excludes_far_parts():
    d = resolver.resolve(catalog(), ocr([("CD4011B", 0.9)]), TH, GOOD_Q, CROP)
    nums = {c["part_number"] for c in d.result["candidates"]}
    assert "NE555P" not in nums  # unrelated part not shown


# ---- recapture reasons (U2) -------------------------------------------------
# These use non-matching reads so the decision falls through to recapture, where
# the reason is derived from image/OCR quality. (A confident catalog match is the
# authority and auto-accepts regardless of blur metrics, per the deterministic
# principle — see test_clean_read_auto_accepts.)
def test_blur_takes_precedence():
    q = ImageQuality(blur_var=10.0, glare_frac=0.0, width=1000, height=800)
    d = resolver.resolve(catalog(), ocr([("QWXYZ", 0.3)]), TH, q, CROP)
    assert d.status == "recapture" and d.result["reason"] == "blur"


def test_glare_reason():
    q = ImageQuality(blur_var=500.0, glare_frac=0.9, width=1000, height=800)
    d = resolver.resolve(catalog(), ocr([("QWXYZ", 0.3)]), TH, q, CROP)
    assert d.status == "recapture" and d.result["reason"] == "glare"


def test_unreadable_when_no_text():
    d = resolver.resolve(catalog(), [OCRResult(words=[], variant="original")],
                         TH, GOOD_Q, CROP)
    assert d.status == "recapture" and d.result["reason"] == "unreadable"


def test_too_far_when_chars_tiny():
    # present but unmatched text, tiny char height -> too_far
    words = ocr([("ZZZZZZ", 0.4, (400, 350, 50, 8))])
    d = resolver.resolve(catalog(), words, TH, GOOD_Q, CROP)
    assert d.status == "recapture" and d.result["reason"] == "too_far"


def test_off_center_when_text_in_corner():
    # tall enough chars (not too_far), unmatched, centroid in far corner
    words = ocr([("ZZZZZZ", 0.4, (980, 780, 40, 30))])
    d = resolver.resolve(catalog(), words, TH, GOOD_Q, CROP)
    assert d.status == "recapture" and d.result["reason"] == "off_center"


# ---- core safety invariant --------------------------------------------------
def test_empty_catalog_never_accepts():
    d = resolver.resolve(Catalog(parts=[]), ocr([("NE555P", 0.99)]), TH, GOOD_Q, CROP)
    assert d.status == "recapture"       # no catalog => cannot be accepted


def test_near_miss_does_not_accept():
    # a read that's close to NE555P but below accept_score must not auto-accept
    d = resolver.resolve(catalog(), ocr([("NE5XX", 0.5)]), TH, GOOD_Q, CROP)
    assert d.status != "accepted"
