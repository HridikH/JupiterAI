"""Shared test fixtures + path setup so `from server import ...` works."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def make_jpeg(size: tuple[int, int] = (64, 48), color=(120, 120, 120)) -> bytes:
    """A small but genuinely valid JPEG for decode tests."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@pytest.fixture
def jpeg_bytes() -> bytes:
    return make_jpeg()


@pytest.fixture
def store():
    from server.state import StateStore
    return StateStore()
