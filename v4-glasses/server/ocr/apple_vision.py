"""Apple Vision OCR backend (macOS, via pyobjc) — the default engine.

Fast, on-device, no model download, excellent on printed text. Uses
VNRecognizeTextRequest at accurate level. Requires pyobjc-framework-Vision +
pyobjc-framework-Quartz (see server/requirements.txt), macOS only.
"""
from __future__ import annotations

from typing import Any

from .base import OCREngine, OCRWord


class AppleVisionEngine(OCREngine):
    name = "apple_vision"

    def __init__(self) -> None:
        try:
            import Vision  # noqa: F401
            import Quartz   # noqa: F401
        except Exception as e:  # pragma: no cover - platform dependent
            raise RuntimeError(
                "Apple Vision backend needs pyobjc-framework-Vision + "
                "pyobjc-framework-Quartz (macOS only). Install them or set "
                "GLASSES_OCR_BACKEND=easy. Underlying error: " + str(e)
            )

    def _recognize(self, image: Any) -> list[OCRWord]:  # pragma: no cover - macOS runtime
        import Quartz
        import Vision
        from Foundation import NSData

        import io
        import numpy as np
        from PIL import Image

        if isinstance(image, np.ndarray):
            pil = Image.fromarray(image)
        else:
            pil = image
        w, h = pil.size
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
        src = Quartz.CGImageSourceCreateWithData(data, None)
        cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(False)   # part markings aren't words

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
        ok = handler.performRequests_error_([request], None)
        words: list[OCRWord] = []
        if not ok:
            return words
        for obs in request.results() or []:
            cand = obs.topCandidates_(1)
            if not cand:
                continue
            best = cand[0]
            text = best.string()
            conf = float(best.confidence())
            bb = obs.boundingBox()   # normalized, origin bottom-left
            x = bb.origin.x * w
            bw = bb.size.width * w
            bh = bb.size.height * h
            y = (1.0 - bb.origin.y - bb.size.height) * h  # flip to top-left origin
            words.append(OCRWord(text=text, confidence=conf, bbox=(x, y, bw, bh)))
        return words
