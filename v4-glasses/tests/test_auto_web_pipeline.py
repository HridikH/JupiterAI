"""Fully-automated web fallback: read an uncatalogued part -> pipeline searches,
caches, and re-resolves to an accepted (unverified) result, with NO user action.
Network + catalog writes are mocked, so this is deterministic and offline."""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from server import pipeline as pipemod
from server import web_datasheet, config
from server.state import StateStore
from server.ws import WSHub
from server.ocr.base import MockOCREngine
from server.catalog_api import Catalog


def _jpeg():
    # textured (not flat) so it clears the blur quality gate and reaches the
    # catalog-match stage, where the MockOCR's read drives the outcome.
    arr = np.random.default_rng(0).integers(0, 255, (1944, 2592, 3)).astype("uint8")
    b = io.BytesIO(); Image.fromarray(arr).save(b, "JPEG", quality=85); return b.getvalue()


def test_pipeline_auto_resolves_uncatalogued_part(monkeypatch):
    monkeypatch.setattr(config, "WEB_AUTOSEARCH", True)
    monkeypatch.setattr(config, "WEB_AUTOCACHE", True)

    # OCR always reads an uncatalogued part number.
    eng = MockOCREngine(words=[("ATMEGA328P", 0.96)])
    empty = Catalog(parts=[])                      # starts with nothing matching

    # web search returns an exact PDF
    monkeypatch.setattr(web_datasheet, "search_datasheet", lambda part, **k: {
        "available": True, "part": part, "results": [
            {"title": f"{part} datasheet", "url": f"https://microchip.com/{part}.pdf",
             "is_pdf": True, "domain": "microchip.com", "exact": True}],
        "exact_count": 1})

    # autocache "succeeds" without touching disk/network
    import catalog.autocache as ac
    monkeypatch.setattr(ac, "cache_part", lambda part, url, **k: {"cached": True, "part": part})

    # after caching, the reloaded catalog contains the part (web-sourced, unreviewed)
    post = Catalog.from_dicts([{"part_number": "ATMEGA328P", "manufacturer": "Microchip",
                                "package": "unknown", "top_markings": ["ATMEGA328P"], "auto": True}])
    post.parts[0].reviewed = False
    monkeypatch.setattr("server.catalog_api.load_default_catalog", lambda: post)

    store, hub = StateStore(), WSHub()
    pipe = pipemod.Pipeline(store, hub, engine=eng, catalog=empty)
    rec, _ = store.start("auto_web_0001", {})
    rec.stamp("processing")
    pipe.run_sync(rec, _jpeg())

    out = store.get("auto_web_0001")
    assert out.status == "accepted"                 # hands-free, no user action
    assert out.result["part_number"] == "ATMEGA328P"
    assert out.result["verified"] is False          # tagged web-sourced
    assert out.result["source"] == "web"


def test_pipeline_recapture_when_no_web_match(monkeypatch):
    monkeypatch.setattr(config, "WEB_AUTOSEARCH", True)
    eng = MockOCREngine(words=[("ZZQQ9988XX", 0.9)])
    monkeypatch.setattr(web_datasheet, "search_datasheet",
                        lambda part, **k: {"available": False, "part": part, "results": []})
    store, hub = StateStore(), WSHub()
    pipe = pipemod.Pipeline(store, hub, engine=eng, catalog=Catalog(parts=[]))
    rec, _ = store.start("auto_web_0002", {}); rec.stamp("processing")
    pipe.run_sync(rec, _jpeg())
    out = store.get("auto_web_0002")
    assert out.status == "recapture"                # nothing found -> honest recapture
    assert out.result["reason"] == "not_in_catalog"