"""OCR subpackage: pluggable text-recognition engines."""
from .base import OCRResult, OCRWord, OCREngine, MockOCREngine, get_engine  # noqa: F401
