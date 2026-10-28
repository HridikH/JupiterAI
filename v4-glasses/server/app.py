"""FastAPI app — HTTP adapter over receiver + state + ws (docs/protocol.md §3).

The HTTP layer is thin: it extracts token/headers/parts, calls
receiver.validate_capture (all V1 checks live there), stores captures locally if
enabled, pushes state over WebSocket, and serves the UI. The identification
pipeline is INJECTED (default = stage-1 stub) so this file is stable across
stages — stage 2 swaps in the real pipeline without touching the HTTP layer.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, Form, Header, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from . import config, protocol, receiver
from .logging_setup import get
from .state import RequestRecord, StateStore
from .ws import WSHub

log = get("app")

# A pipeline callback: (record, image_bytes) -> None. It is expected to run the
# identification work (possibly on a worker thread), update the StateStore via
# set_result(), and emit WS events. Stage 1 ships a stub.
PipelineFn = Callable[[RequestRecord, bytes], None]


def stage1_stub_pipeline(hub: WSHub, store: StateStore) -> PipelineFn:
    """Placeholder until stage 2 installs OCR + resolver.

    Leaves the request in 'processing' and emits a state event. It never
    fabricates an 'accepted' outcome — the safety invariant holds even for the
    stub: no identification means no accept.
    """
    def _run(rec: RequestRecord, image_bytes: bytes) -> None:
        hub.emit("state", request_id=rec.request_id, status=rec.status,
                 timestamps=rec.timestamps)
        log.info("stage-1 stub: pipeline not installed; left in processing",
                 extra={"request_id": rec.request_id, "status": "processing"})
    return _run


def _store_capture(rec: RequestRecord, image_bytes: bytes, meta_raw: bytes) -> None:
    if not config.STORE_IMAGES:
        return
    try:
        config.ensure_dirs()
        base = config.CAPTURES_DIR / rec.request_id
        base.with_suffix(".jpg").write_bytes(image_bytes)
        base.with_suffix(".meta.json").write_bytes(meta_raw)
    except Exception as e:  # storage must never break the response
        log.warning(f"failed to store capture: {e}", extra={"request_id": rec.request_id})


def create_app(store: Optional[StateStore] = None,
               hub: Optional[WSHub] = None,
               pipeline: Optional[PipelineFn] = None) -> FastAPI:
    store = store or StateStore()
    hub = hub or WSHub()
    pipeline = pipeline or stage1_stub_pipeline(hub, store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.start(asyncio.get_running_loop())
        config.ensure_dirs()
        log.info("glasses server up", extra={"status": "ready"})
        yield

    app = FastAPI(title="Glasses datasheet server", lifespan=lifespan)
    app.state.store = store
    app.state.hub = hub

    def _err(exc: protocol.ProtocolError, request_id: str = "") -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"ok": False, "request_id": request_id,
                     "error": exc.code, "detail": exc.detail},
        )

    @app.get(protocol.EP_HEALTH)
    async def healthz():
        return {"ok": True, "service": "glasses-server", "proto": protocol.PROTO_VERSION}

    @app.post(protocol.EP_CAPTURE)
    async def capture(
        request: Request,
        meta: str = Form(...),
        image: UploadFile | None = None,
    ):
        token = request.headers.get(protocol.HDR_TOKEN)
        rid_hdr = request.headers.get(protocol.HDR_REQUEST_ID)
        meta_raw = meta.encode("utf-8")
        image_bytes = await image.read() if image is not None else b""

        try:
            rec, created, parsed = receiver.validate_capture(
                token=token,
                request_id_header=rid_hdr,
                meta_raw=meta_raw,
                image_bytes=image_bytes,
                store=store,
                expected_token=config.SHARED_TOKEN,
                on_valid=lambda r, img: _on_valid(r, img, meta_raw),
            )
        except protocol.ProtocolError as e:
            log.info("capture rejected",
                     extra={"request_id": rid_hdr or "", "error": e.code})
            return _err(e, rid_hdr or "")

        ack = {"request_id": rec.request_id, "received": True,
               "proto": protocol.PROTO_VERSION, "server_time": time.time()}
        return JSONResponse(
            status_code=202,
            content={"ok": True, "request_id": rec.request_id,
                     "status": rec.status, "duplicate": not created, "ack": ack},
        )

    def _on_valid(rec: RequestRecord, image_bytes: bytes, meta_raw: bytes) -> None:
        _store_capture(rec, image_bytes, meta_raw)
        hub.emit("state", request_id=rec.request_id, status="processing")
        pipeline(rec, image_bytes)

    @app.get(protocol.EP_RESULT + "/{request_id}")
    async def result(request_id: str):
        rec = store.get(request_id)
        if rec is None:
            return JSONResponse(status_code=404,
                                content={"ok": False, "request_id": request_id,
                                         "error": "not_found"})
        return rec.to_public()

    @app.get("/")
    async def index():
        idx = config.UI_WEB_DIR / "index.html"
        if idx.exists():
            return HTMLResponse(idx.read_text())
        return HTMLResponse("<h1>Glasses server</h1><p>UI not built yet (stage 4).</p>")

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        hub.add(websocket)
        try:
            while True:
                msg = json.loads(await websocket.receive_text())
                cmd = msg.get("type")
                if cmd == "user_decision":
                    # U3: UI reports operator decision time on the candidate screen
                    store.set_user_decision(msg.get("request_id", ""),
                                            float(msg.get("ms", 0.0)))
                elif cmd == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            hub.remove(websocket)

    return app


def main() -> None:
    import uvicorn
    from .logging_setup import setup
    from .pipeline import Pipeline
    setup()
    store = StateStore()
    hub = WSHub()
    pipe = Pipeline(store, hub).as_fn(threaded=True)
    app = create_app(store=store, hub=hub, pipeline=pipe)
    log.info(f"serving on {config.HOST}:{config.PORT}", extra={"status": "listen"})
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning", ws="wsproto")


if __name__ == "__main__":
    main()
