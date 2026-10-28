"""OCR engine interface + result model + factory.

An engine takes one preprocessed image (H×W×3 uint8 numpy array) and returns a
list of OCRWord (text, confidence, bbox). The pipeline runs each engine over
several fixed enhancement variants and keeps text/boxes/confidences/timing per
variant (so we can see which variant read the marking).

This module stays import-light: heavy backends (pyobjc Vision, torch) are
imported lazily inside get_engine so stage-1/stage-2 tooling and tests load fast.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OCRWord:
    text: str
    confidence: float                       # 0..1
    bbox: tuple[float, float, float, float]  # (x, y, w, h), pixels in the image given


@dataclass
class OCRResult:
    words: list[OCRWord] = field(default_factory=list)
    variant: str = ""                       # which enhancement variant produced this
    engine: str = ""
    timing_ms: float = 0.0
    image_size: tuple[int, int] = (0, 0)    # (w, h) of the image OCR'd

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def strings(self) -> list[str]:
        return [w.text for w in self.words if w.text.strip()]


class OCREngine(ABC):
    name: str = "base"

    @abstractmethod
    def _recognize(self, image: Any) -> list[OCRWord]:
        """Return recognized words for one image (H×W×3 uint8 ndarray)."""

    def recognize(self, image: Any, variant: str = "") -> OCRResult:
        t0 = time.monotonic()
        words = self._recognize(image)
        dt = (time.monotonic() - t0) * 1000.0
        h, w = (image.shape[0], image.shape[1]) if hasattr(image, "shape") else (0, 0)
        return OCRResult(words=words, variant=variant, engine=self.name,
                         timing_ms=round(dt, 2), image_size=(w, h))


class MockOCREngine(OCREngine):
    """Deterministic engine for tests/simulation: returns preset words.

    Construct with a list of (text, confidence) tuples, or a dict mapping
    variant -> list of tuples to simulate variant-dependent reads.
    """
    name = "mock"

    def __init__(self, words: Any = None, per_variant: dict | None = None):
        self._words = words or []
        self._per_variant = per_variant or {}

    def recognize(self, image: Any, variant: str = "") -> OCRResult:
        spec = self._per_variant.get(variant, self._words)
        words = [OCRWord(text=t, confidence=c, bbox=(0, 0, 10, 10)) for t, c in spec]
        return OCRResult(words=words, variant=variant, engine=self.name,
                         timing_ms=0.0, image_size=(100, 100))

    def _recognize(self, image: Any) -> list[OCRWord]:  # pragma: no cover
        return [OCRWord(text=t, confidence=c, bbox=(0, 0, 10, 10)) for t, c in self._words]


def get_engine(backend: str = "apple") -> OCREngine:
    """Factory. Lazily imports the chosen backend so missing optional deps only
    error when that backend is actually requested."""
    backend = (backend or "apple").lower()
    if backend == "mock":
        return MockOCREngine()
    if backend == "apple":
        from .apple_vision import AppleVisionEngine
        return AppleVisionEngine()
    if backend in ("easy", "easyocr"):
        from .fallback import EasyOCREngine
        return EasyOCREngine()
    if backend in ("paddle", "paddleocr"):
        from .fallback import PaddleOCREngine
        return PaddleOCREngine()
    raise ValueError(f"unknown OCR backend {backend!r} (apple|easy|paddle|mock)")
