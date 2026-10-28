"""Cross-platform OCR fallbacks: EasyOCR and PaddleOCR.

Used when Apple Vision isn't available (non-macOS, or for A/B comparison). Both
pull heavy deps (torch / paddle), so they are imported lazily and are commented
out of requirements.txt by default — install the one you need.
"""
from __future__ import annotations

from typing import Any

from .base import OCREngine, OCRWord


class EasyOCREngine(OCREngine):
    name = "easyocr"

    def __init__(self, langs=("en",), gpu: bool = False) -> None:
        try:
            import easyocr
        except Exception as e:  # pragma: no cover
            raise RuntimeError("EasyOCR not installed: pip install easyocr. " + str(e))
        self._reader = easyocr.Reader(list(langs), gpu=gpu)

    def _recognize(self, image: Any) -> list[OCRWord]:  # pragma: no cover - heavy dep
        import numpy as np
        arr = image if isinstance(image, np.ndarray) else np.array(image)
        out = self._reader.readtext(arr)
        words = []
        for box, text, conf in out:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x, y = min(xs), min(ys)
            words.append(OCRWord(text=text, confidence=float(conf),
                                 bbox=(x, y, max(xs) - x, max(ys) - y)))
        return words


class PaddleOCREngine(OCREngine):
    name = "paddleocr"

    def __init__(self, lang: str = "en") -> None:
        try:
            from paddleocr import PaddleOCR
        except Exception as e:  # pragma: no cover
            raise RuntimeError("PaddleOCR not installed: pip install paddleocr. " + str(e))
        self._ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)

    def _recognize(self, image: Any) -> list[OCRWord]:  # pragma: no cover - heavy dep
        import numpy as np
        arr = image if isinstance(image, np.ndarray) else np.array(image)
        result = self._ocr.ocr(arr, cls=True)
        words = []
        for line in (result[0] if result else []):
            box, (text, conf) = line
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x, y = min(xs), min(ys)
            words.append(OCRWord(text=text, confidence=float(conf),
                                 bbox=(x, y, max(xs) - x, max(ys) - y)))
        return words
