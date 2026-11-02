"""Resolver — deterministic catalog matching. THE safety-critical module.

Scores OCR reads against the curated catalog using confusion-aware normalized
edit distance (server/confusion.py), light OCR-confidence weighting, and
package-suffix awareness. Produces a ranked list with score + margin, then makes
exactly one decision:

  * accepted            iff top.score >= accept_score AND margin >= accept_margin
  * candidate_selection iff top.score >= candidate_score (show the plausible set)
  * recapture           otherwise, with a specific reason (U2)

No LLM is involved and no part number is ever invented. If nothing clears the
bars, the system asks for a recapture rather than guessing (core principle).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import confusion
from .catalog_api import Catalog, Part
from .ocr.base import OCRResult, OCRWord
from .preprocess import ImageQuality
from .thresholds import Thresholds

RECAPTURE_MESSAGES = {
    "blur": "Image is blurry — hold steady, let autofocus settle, then recapture.",
    "glare": "Glare on the part — tilt slightly to avoid reflections and recapture.",
    "off_center": "Marking is off-center — center the component in view and recapture.",
    "too_far": "Too far — move closer (target ~100–110 mm) and recapture.",
    "unreadable": "Couldn't read the marking — clean the part, improve lighting, and recapture.",
}

# How much OCR confidence modulates a match score (keeps valid low-conf reads
# viable while rewarding confident ones). score *= (BASE + (1-BASE)*conf).
_CONF_BASE = 0.85
_PACKAGE_BONUS = 0.05


@dataclass
class Candidate:
    part: Part
    score: float
    matched_string: str = ""     # which catalog string matched
    matched_observed: str = ""   # which OCR read matched it

    def public(self) -> dict:
        return {
            "part_number": self.part.part_number,
            "manufacturer": self.part.manufacturer,
            "package": self.part.package,
            "score": round(self.score, 4),
            "datasheet": self.part.datasheet,
        }


@dataclass
class Decision:
    status: str                       # accepted | candidate_selection | recapture
    result: dict
    ranked: list[Candidate] = field(default_factory=list)
    top_score: float = 0.0
    margin: float = 0.0


def _collect_observed(ocr_results: Iterable[OCRResult]) -> list[tuple[str, float]]:
    """Dedup observed strings across variants, keeping the max confidence.

    Includes both individual words and the per-variant concatenation (markings
    often span two lines, e.g. 'LM358' + 'P')."""
    best: dict[str, float] = {}

    def add(text: str, conf: float):
        key = confusion.normalize(text)
        if not key:
            return
        if conf > best.get(key, -1.0):
            best[key] = conf

    for res in ocr_results:
        for w in res.words:
            add(w.text, w.confidence)
        if res.words:
            joined = "".join(w.text for w in res.words)
            avg = statistics.fmean(w.confidence for w in res.words)
            add(joined, avg)
    return list(best.items())


# Shortest token (normalized) allowed to produce a match. Real orderable
# markings are >=4 chars; this stops 3-char noise (a receipt's "505" matching the
# "555" alias) from ever surfacing as a candidate.
MIN_MATCH_LEN = 4


def _score_part(part: Part, observed: list[tuple[str, float]]) -> Candidate:
    best = Candidate(part=part, score=0.0)
    pkg_norm = confusion.normalize(part.package)
    for cand_str in part.candidate_strings():
        if len(confusion.normalize(cand_str)) < MIN_MATCH_LEN:
            continue                      # skip too-short catalog markings/aliases
        for obs_text, conf in observed:
            if len(confusion.normalize(obs_text)) < MIN_MATCH_LEN:
                continue                  # skip too-short OCR tokens (noise)
            sim = confusion.similarity(obs_text, cand_str)
            score = sim * (_CONF_BASE + (1.0 - _CONF_BASE) * max(0.0, min(1.0, conf)))
            # package-suffix awareness: reading contains the package token
            if pkg_norm and pkg_norm in obs_text:
                score = min(1.0, score + _PACKAGE_BONUS)
            if score > best.score:
                best = Candidate(part=part, score=score,
                                 matched_string=cand_str, matched_observed=obs_text)
    return best


def rank(catalog: Catalog, ocr_results: Iterable[OCRResult]) -> list[Candidate]:
    observed = _collect_observed(ocr_results)
    cands = [_score_part(p, observed) for p in catalog.parts]
    cands.sort(key=lambda c: c.score, reverse=True)
    return cands


def _est_char_px(ocr_results: Iterable[OCRResult]) -> Optional[float]:
    heights = [w.bbox[3] for res in ocr_results for w in res.words if w.bbox[3] > 0]
    return statistics.median(heights) if heights else None


def _offcenter_frac(ocr_results: Iterable[OCRResult], crop_size: tuple[int, int]) -> Optional[float]:
    w, h = crop_size
    if not w or not h:
        return None
    centers = [(wd.bbox[0] + wd.bbox[2] / 2, wd.bbox[1] + wd.bbox[3] / 2)
               for res in ocr_results for wd in res.words]
    if not centers:
        return None
    cx = statistics.fmean(p[0] for p in centers)
    cy = statistics.fmean(p[1] for p in centers)
    dx, dy = cx - w / 2, cy - h / 2
    dist = (dx * dx + dy * dy) ** 0.5
    half_diag = ((w / 2) ** 2 + (h / 2) ** 2) ** 0.5
    return dist / half_diag if half_diag else None


def pick_recapture_reason(quality: ImageQuality, ocr_results: list[OCRResult],
                          crop_size: tuple[int, int], th: Thresholds) -> str:
    q = th.quality
    # image-level signals first (they also explain a bad OCR read)
    if quality.blur_var < q.get("blur_var_min", 60.0):
        return "blur"
    if quality.glare_frac > q.get("glare_frac_max", 0.12):
        return "glare"
    any_words = any(res.words for res in ocr_results)
    if not any_words:
        return "unreadable"
    char_px = _est_char_px(ocr_results)
    if char_px is not None and char_px < q.get("min_char_px", 14.0):
        return "too_far"
    off = _offcenter_frac(ocr_results, crop_size)
    if off is not None and off > q.get("offcenter_frac_max", 0.30):
        return "off_center"
    return "unreadable"


def resolve(catalog: Catalog, ocr_results: list[OCRResult], th: Thresholds,
            quality: ImageQuality, crop_size: tuple[int, int]) -> Decision:
    ranked = rank(catalog, ocr_results)
    top = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else None
    top_score = top.score if top else 0.0
    margin = top_score - (second.score if second else 0.0)

    # --- auto-accept: BOTH bars (V3) ---
    if top and top_score >= th.accept_score and margin >= th.accept_margin:
        best_text = max((r.text for r in ocr_results), key=len, default="")
        result = {
            "part_number": top.part.part_number,
            "manufacturer": top.part.manufacturer,
            "package": top.part.package,
            "score": round(top_score, 4),
            "margin": round(margin, 4),
            "ocr_text": best_text,
            "matched_string": top.matched_string,
            "datasheet": top.part.datasheet,
        }
        return Decision("accepted", result, ranked, top_score, margin)

    # --- candidate selection: plausible set ---
    if top and top_score >= th.candidate_score:
        shown = [c for c in ranked
                 if c.score >= th.candidate_score and (top_score - c.score) <= th.candidate_band]
        result = {"candidates": [c.public() for c in shown],
                  "margin": round(margin, 4)}
        return Decision("candidate_selection", result, ranked, top_score, margin)

    # --- recapture with a reason (U2) ---
    reason = pick_recapture_reason(quality, ocr_results, crop_size, th)
    result = {"reason": reason, "message": RECAPTURE_MESSAGES[reason]}
    return Decision("recapture", result, ranked, top_score, margin)
