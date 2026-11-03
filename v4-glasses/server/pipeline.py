"""Identification pipeline: preprocess -> OCR (all variants) -> resolve.

Runs on a worker thread so /capture can ACK immediately while identification
proceeds. Updates the StateStore with the outcome and pushes state + result over
the WebSocket. Every result is stamped with the frozen-threshold version + hash.

The pipeline is deterministic and LLM-free. An exception anywhere lands the
request in 'error' — never in 'accepted'.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from . import config, preprocess, protocol, resolver, thresholds as th_mod
from .catalog_api import Catalog
from .logging_setup import get
from .ocr.base import OCREngine, get_engine
from .state import RequestRecord, StateStore
from .ws import WSHub

log = get("pipeline")


class Pipeline:
    def __init__(self, store: StateStore, hub: WSHub, *,
                 catalog: Optional[Catalog] = None,
                 engine: Optional[OCREngine] = None,
                 thresholds: Optional[th_mod.Thresholds] = None,
                 require_frozen: bool = False):
        self.store = store
        self.hub = hub
        self._catalog = catalog
        self._catalog_injected = catalog is not None   # don't hot-reload a test catalog
        self._catalog_mtime = None
        self._engine = engine
        self._require_frozen = require_frozen
        self._thresholds = thresholds
        self._engine_lock = threading.Lock()

    # lazy so tests/stage-2 don't need macOS Vision or a real catalog
    def catalog(self) -> Catalog:
        # Reload when parts.yaml changes (e.g. a web datasheet was auto-cached), so
        # a newly seen part matches offline on the very next capture. An explicitly
        # injected catalog (tests) is never auto-reloaded.
        from .catalog_api import load_default_catalog
        from . import config as _cfg
        pf = _cfg.CATALOG_DIR / "parts.yaml"
        try:
            mtime = pf.stat().st_mtime
        except OSError:
            mtime = None
        if self._catalog is None or (self._catalog_injected is False and mtime != self._catalog_mtime):
            self._catalog = load_default_catalog()
            self._catalog_mtime = mtime
        return self._catalog

    def engine(self) -> OCREngine:
        with self._engine_lock:
            if self._engine is None:
                self._engine = get_engine(config.OCR_BACKEND)
        return self._engine

    def thresholds(self) -> th_mod.Thresholds:
        if self._thresholds is None:
            self._thresholds = th_mod.load(require_frozen=self._require_frozen)
        return self._thresholds

    def identify(self, image_bytes: bytes, store_crop_for: str | None = None):
        """Run preprocess -> OCR -> resolve and return the resolver Decision.

        Pure identification (no state mutation / WS), so eval harnesses can reuse
        the exact runtime path in-process. Deterministic and LLM-free."""
        th = self.thresholds()
        crop, variants, quality = preprocess.preprocess(image_bytes)
        if store_crop_for:
            self._maybe_store_crop(store_crop_for, crop)
        engine = self.engine()
        ocr_results = [engine.recognize(arr, variant=name) for name, arr in variants]
        return resolver.resolve(self.catalog(), ocr_results, th, quality,
                                (crop.width, crop.height))

    def _auto_web_resolve(self, rid, hint, image_bytes, th):
        """Automatically web-search + cache a read-but-uncatalogued part, then
        re-resolve. Returns a new Decision, or None to keep the recapture.

        The part number came from OCR (deterministic), not a guess; we fetch the
        EXACT datasheet for it. A result is only accepted off an EXACT web match,
        and is tagged source='web', verified=False by the resolver."""
        if not hint:
            return None
        from . import web_datasheet as wd
        self.hub.emit("state", request_id=rid, status="searching_web", query=hint)
        log.info(f"auto web search for uncatalogued part '{hint}'",
                 extra={"request_id": rid})
        res = wd.search_datasheet(hint)
        if not res.get("available"):
            return None
        exact = next((r for r in res["results"] if r.get("exact") and r.get("is_pdf")), None)
        if not exact or not config.WEB_AUTOCACHE:
            return None
        try:
            from catalog import autocache
            cached = autocache.cache_part(hint, exact["url"])
        except Exception as e:
            log.warning(f"auto-cache failed: {e}", extra={"request_id": rid})
            return None
        if not (cached.get("cached") or cached.get("reason") == "exists"):
            return None
        # reload catalog (new entry) and re-resolve against it
        self._catalog = None
        crop, variants, quality = preprocess.preprocess(image_bytes)
        engine = self.engine()
        ocr_results = [engine.recognize(arr, variant=name) for name, arr in variants]
        decision = resolver.resolve(self.catalog(), ocr_results, th, quality,
                                    (crop.width, crop.height))
        # attach provenance for the UI
        decision.result["source"] = "web"
        decision.result.setdefault("verified", False)
        decision.result["web_results"] = res["results"][:5]
        log.info(f"auto-resolved '{hint}' via web -> {decision.status}",
                 extra={"request_id": rid, "status": decision.status})
        return decision

    def run_sync(self, rec: RequestRecord, image_bytes: bytes) -> None:
        rid = rec.request_id
        th = self.thresholds()
        try:
            decision = self.identify(image_bytes, store_crop_for=rid)

            # Fully-automated fallback: read a part not in the catalog -> the
            # pipeline itself searches the web, caches the exact datasheet, and
            # re-resolves. No user action (the hands-free core of the product).
            if (decision.status == "recapture"
                    and decision.result.get("reason") == "not_in_catalog"
                    and config.WEB_AUTOSEARCH):
                hint = decision.result.get("search_hint", "")
                new = self._auto_web_resolve(rid, hint, image_bytes, th)
                if new is not None:
                    decision = new

            self.store.set_result(rid, decision.status, decision.result,
                                  config_version=th.version, config_hash=th.config_hash)
            log.info("resolved", extra={"request_id": rid, "status": decision.status,
                                        "config_version": th.version,
                                        "config_hash": th.config_hash})
            self.hub.emit("result", request_id=rid, status=decision.status,
                          result=decision.result, config_version=th.version,
                          config_hash=th.config_hash)
        except Exception as e:
            self.store.set_result(rid, "error",
                                  {"code": protocol.E_INTERNAL, "message": str(e)},
                                  config_version=th.version, config_hash=th.config_hash)
            log.exception("pipeline error", extra={"request_id": rid,
                                                   "error": protocol.E_INTERNAL})
            self.hub.emit("result", request_id=rid, status="error",
                          result={"code": protocol.E_INTERNAL, "message": str(e)})

    def _maybe_store_crop(self, rid: str, crop) -> None:
        if not config.STORE_IMAGES:
            return
        try:
            config.ensure_dirs()
            crop.save(config.CAPTURES_DIR / f"{rid}.crop.jpg", format="JPEG", quality=85)
        except Exception as e:  # never break identification on a storage error
            log.warning(f"failed to store crop: {e}", extra={"request_id": rid})

    def as_fn(self, threaded: bool = True) -> Callable[[RequestRecord, bytes], None]:
        def _fn(rec: RequestRecord, image_bytes: bytes) -> None:
            if threaded:
                threading.Thread(target=self.run_sync, args=(rec, image_bytes),
                                 daemon=True).start()
            else:
                self.run_sync(rec, image_bytes)
        return _fn
