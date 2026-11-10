"""Live camera feed (MJPEG) — option B.

The glasses stream JPEG frames to POST /frame (or the firmware runs its own MJPEG
server); the laptop re-serves them to the UI as multipart/x-mixed-replace so the
console shows a live feed. The pinout overlay is drawn on top in the browser.

A single latest-frame buffer with a Condition keeps memory bounded (we only ever
hold the most recent frame) and lets each stream client wake on a new frame.
"""
from __future__ import annotations

import threading
from typing import Optional


class FrameBuffer:
    def __init__(self) -> None:
        self._frame: Optional[bytes] = None
        self._seq = 0
        self._cond = threading.Condition()

    def put(self, data: bytes) -> None:
        with self._cond:
            self._frame = data
            self._seq += 1
            self._cond.notify_all()

    def wait_next(self, last_seq: int, timeout: float = 1.0):
        """Block until a frame newer than last_seq, or timeout. Returns (frame, seq)."""
        with self._cond:
            if self._seq == last_seq:
                self._cond.wait(timeout)
            return self._frame, self._seq


def mjpeg_generator(fb: FrameBuffer):
    """Sync generator for StreamingResponse. One threadpool thread per client."""
    last = -1
    while True:
        frame, seq = fb.wait_next(last)
        if seq == last or frame is None:
            continue  # timeout with no new frame — loop (keeps the response alive)
        last = seq
        yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
               + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")


MJPEG_MEDIA_TYPE = "multipart/x-mixed-replace; boundary=frame"


def pull_mjpeg(url: str, fb: FrameBuffer, stop: "threading.Event",
               timeout: float = 5.0, retry_s: float = 2.0) -> None:
    """Pull a device's multipart MJPEG stream into ``fb`` until ``stop`` is set.

    Splits on JPEG SOI/EOI markers rather than trusting part headers, so it works
    with esp32-camera's stream handler and any standard MJPEG server. Reconnects
    forever (the glasses drop off Wi-Fi when they sleep)."""
    import logging
    import requests

    log = logging.getLogger("glasses.stream")
    while not stop.is_set():
        try:
            with requests.get(url, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                log.info(f"pulling device stream {url}")
                buf = b""
                for chunk in r.iter_content(chunk_size=16384):
                    if stop.is_set():
                        return
                    buf += chunk
                    while True:
                        a = buf.find(b"\xff\xd8")
                        if a < 0:
                            buf = buf[-1:]
                            break
                        b = buf.find(b"\xff\xd9", a + 2)
                        if b < 0:
                            buf = buf[a:]
                            if len(buf) > 8 * 1024 * 1024:   # runaway frame -> resync
                                buf = b""
                            break
                        fb.put(buf[a:b + 2])
                        buf = buf[b + 2:]
        except Exception as e:
            log.info(f"device stream unavailable ({e}); retrying in {retry_s}s")
        stop.wait(retry_s)
