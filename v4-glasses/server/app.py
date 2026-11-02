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
import re
import threading
import time
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, Form, Header, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from . import config, protocol, receiver
from .logging_setup import get
from .state import RequestRecord, StateStore
from .stream import FrameBuffer, MJPEG_MEDIA_TYPE, mjpeg_generator
from .ws import WSHub

# voice command patterns (deterministic routing — no LLM decides commands)
_PINOUT_ON_RE = re.compile(
    r"\b(show|display|bring up|turn on|enable|overlay)\b.*\b(pin ?out|pins?)\b"
    r"|\bpin ?out\b.*\b(on|up)\b", re.I)
_PINOUT_OFF_RE = re.compile(
    r"\b(hide|remove|close|turn off|disable|clear|take (down|off))\b.*\b(pin ?out|pins?)\b", re.I)

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
    frames = FrameBuffer()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.start(asyncio.get_running_loop())
        config.ensure_dirs()
        if config.VOICE_ALWAYS:
            from . import voice
            if voice.start_always_listening(_route_voice):
                log.info("always-listening voice enabled")
                hub.emit("voice_state", listening=True)
            else:
                log.info("always-listening requested but voice stack unavailable")
        log.info("glasses server up", extra={"status": "ready"})
        yield
        from . import voice
        voice.stop_always_listening()

    app = FastAPI(title="Glasses datasheet server", lifespan=lifespan)
    app.state.store = store
    app.state.hub = hub
    app.state.frames = frames

    # serve vendored UI assets (three.min.js, etc.) locally so the demo is offline
    assets_dir = config.UI_WEB_DIR / "assets"
    if assets_dir.exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

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
        frames.put(image_bytes)   # show the latest capture on the live feed too
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

    @app.get("/media/{request_id}")
    async def media(request_id: str):
        if not protocol.REQUEST_ID_RE.match(request_id):
            return JSONResponse(status_code=400, content={"error": "bad id"})
        p = config.CAPTURES_DIR / f"{request_id}.jpg"
        if not p.exists():
            return JSONResponse(status_code=404, content={"error": "no image"})
        return FileResponse(p, media_type="image/jpeg")

    @app.get("/media/{request_id}/crop")
    async def media_crop(request_id: str):
        if not protocol.REQUEST_ID_RE.match(request_id):
            return JSONResponse(status_code=400, content={"error": "bad id"})
        p = config.CAPTURES_DIR / f"{request_id}.crop.jpg"
        if not p.exists():
            return JSONResponse(status_code=404, content={"error": "no crop"})
        return FileResponse(p, media_type="image/jpeg")

    @app.get("/pdf")
    async def pdf(path: str):
        # Serve ONLY cached datasheet PDFs; block path traversal (offline, local).
        cache_root = (config.CATALOG_DIR / "cache").resolve()
        target = (config.ROOT / path).resolve()
        try:
            target.relative_to(cache_root)
        except ValueError:
            return JSONResponse(status_code=403, content={"error": "path not allowed"})
        if not target.exists():
            return JSONResponse(status_code=404, content={"error": "pdf not cached"})
        return FileResponse(target, media_type="application/pdf")

    # ---- live camera feed (option B) ----
    @app.post("/frame")
    async def ingest_frame(request: Request):
        if request.headers.get(protocol.HDR_TOKEN) != config.SHARED_TOKEN:
            return JSONResponse(status_code=401, content={"error": protocol.E_UNAUTHORIZED})
        data = await request.body()
        if not data:
            return JSONResponse(status_code=400, content={"error": "empty frame"})
        if len(data) > protocol.MAX_JPEG_BYTES:
            return JSONResponse(status_code=413, content={"error": protocol.E_TOO_LARGE})
        frames.put(data)
        return {"ok": True, "bytes": len(data)}

    @app.get("/stream")
    def stream():
        return StreamingResponse(mjpeg_generator(frames), media_type=MJPEG_MEDIA_TYPE)

    @app.get("/mobile")
    async def mobile():
        # Phone capture page (served over HTTPS). Uses the phone camera to snap a
        # photo into the real /capture protocol, and/or stream frames to /frame.
        p = config.UI_WEB_DIR / "mobile.html"
        if p.exists():
            return HTMLResponse(p.read_text())
        return HTMLResponse("<h1>mobile capture</h1><p>mobile.html missing</p>")

    @app.get("/pins")
    async def pins(part: str):
        from . import datasheet_qa
        return datasheet_qa.get_pins(part)

    @app.post("/speak")
    async def speak(request: Request):
        if not config.TTS_READOUT_ENABLED:
            return {"ok": False, "reason": "disabled"}
        from . import readout
        body = await request.json()
        ok = readout.speak(body.get("text", ""))
        return {"ok": ok}

    @app.get("/")
    async def index():
        idx = config.UI_WEB_DIR / "index.html"
        if idx.exists():
            return HTMLResponse(idx.read_text())
        return HTMLResponse("<h1>Glasses server</h1><p>UI not built yet (stage 4).</p>")

    def _answer_question(rid: str, question: str) -> None:
        """Datasheet Q&A for an ACCEPTED part. Walled off from identification."""
        from . import datasheet_qa, readout
        rec = store.get(rid)
        part = (rec.result or {}).get("part_number") if rec else None
        if not rec or rec.status != "accepted" or not part:
            hub.emit("answer", request_id=rid, question=question, available=False,
                     reason="ask after a part is accepted")
            return
        if not question.strip():
            hub.emit("answer", request_id=rid, question=question, available=False,
                     reason="empty question")
            return
        ans = datasheet_qa.answer(part, question)
        hub.emit("answer", request_id=rid, question=question, part_number=part, **ans)
        if config.TTS_READOUT_ENABLED and ans.get("available"):
            cite = ans.get("citation", {})
            readout.speak(f"{ans['answer']} Source: page {cite.get('page')}.")

    def _voice_ask(rid: str) -> None:
        from . import voice
        if not voice.available():
            hub.emit("answer", request_id=rid, available=False,
                     reason="voice stack not installed (mlx-whisper + sounddevice)")
            return
        hub.emit("listening", request_id=rid)
        text = voice.listen_once_text()
        hub.emit("heard", request_id=rid, text=text)
        _answer_question(rid, text)

    def _set_pinout(visible: bool) -> None:
        """Toggle the pinout overlay for the latest accepted part (voice/UI)."""
        if not visible:
            hub.emit("pinout", visible=False)
            return
        from . import datasheet_qa
        rec = store.latest_accepted()
        if not rec:
            hub.emit("pinout", visible=False, reason="no accepted part yet")
            return
        info = datasheet_qa.get_pins((rec.result or {}).get("part_number", ""))
        hub.emit("pinout", visible=bool(info.get("pins")), request_id=rec.request_id,
                 part=info.get("part"), page=info.get("page"), pins=info.get("pins", []),
                 datasheet=(rec.result or {}).get("datasheet"))

    def _route_voice(text: str) -> None:
        """Route an always-listening transcript to a command or a datasheet question.

        Deterministic command matching (no LLM decides actions); unmatched speech
        is treated as a datasheet question about the latest accepted part."""
        hub.emit("heard", text=text)
        if _PINOUT_OFF_RE.search(text):
            _set_pinout(False)
        elif _PINOUT_ON_RE.search(text):
            _set_pinout(True)
        else:
            rec = store.latest_accepted()
            if rec:
                _answer_question(rec.request_id, text)

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
                elif cmd == "ask":                     # text datasheet question
                    threading.Thread(target=_answer_question,
                                     args=(msg.get("request_id", ""), msg.get("question", "")),
                                     daemon=True).start()
                elif cmd == "ask_voice":               # spoken datasheet question (Whisper)
                    threading.Thread(target=_voice_ask, args=(msg.get("request_id", ""),),
                                     daemon=True).start()
                elif cmd == "pinout":                   # UI button toggle
                    threading.Thread(target=_set_pinout, args=(bool(msg.get("visible")),),
                                     daemon=True).start()
                elif cmd == "voice_always":             # start/stop always-listening
                    from . import voice
                    if msg.get("on"):
                        ok = voice.start_always_listening(_route_voice)
                        hub.emit("voice_state", listening=ok,
                                 reason=None if ok else "voice stack unavailable")
                    else:
                        voice.stop_always_listening()
                        hub.emit("voice_state", listening=False)
                elif cmd == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            hub.remove(websocket)

    return app


def main() -> None:
    import os
    import threading
    import uvicorn
    from .logging_setup import setup
    from .pipeline import Pipeline
    setup()
    store = StateStore()
    hub = WSHub()
    pipe = Pipeline(store, hub).as_fn(threaded=True)
    app = create_app(store=store, hub=hub, pipeline=pipe)

    # Optional HTTPS listener for the phone (camera needs a secure context). Runs
    # in a background thread sharing the SAME app/store/hub, with lifespan="off"
    # so hub/voice start exactly once (on the HTTP listener below).
    if os.path.exists(config.CERT_FILE) and os.path.exists(config.KEY_FILE):
        def _serve_https():
            cfg = uvicorn.Config(app, host=config.HTTPS_HOST, port=config.HTTPS_PORT,
                                 log_level="warning", ws="wsproto", lifespan="off",
                                 ssl_certfile=config.CERT_FILE, ssl_keyfile=config.KEY_FILE)
            uvicorn.Server(cfg).run()
        threading.Thread(target=_serve_https, daemon=True).start()
        log.info(f"HTTPS (phone) on {config.HTTPS_HOST}:{config.HTTPS_PORT} -> /mobile",
                 extra={"status": "listen"})
    else:
        log.info("HTTPS disabled (no cert) — run `make cert` for phone capture",
                 extra={"status": "listen"})

    log.info(f"serving on {config.HOST}:{config.PORT}", extra={"status": "listen"})
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning", ws="wsproto")


if __name__ == "__main__":
    main()
