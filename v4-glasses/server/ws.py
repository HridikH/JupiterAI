"""WebSocket hub — state push to the UI.

Lifted from jarvis/server.py's proven pattern: pipeline threads call emit() into
a plain queue.Queue; a daemon pump thread drains it and broadcasts to all
connected clients on the asyncio loop. This keeps the (threaded) pipeline fully
decoupled from the (async) web layer, and sidesteps the wsproto handshake quirks
already solved in the parent repo.
"""
from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any


class WSHub:
    def __init__(self) -> None:
        self._events: "queue.Queue[dict]" = queue.Queue()
        self._clients: set = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pump: threading.Thread | None = None

    # ---- producer side (any thread) -----------------------------------------
    def emit(self, type: str, **kw: Any) -> None:
        self._events.put({"type": type, **kw})

    # ---- consumer side (asyncio) --------------------------------------------
    async def _broadcast(self, ev: dict) -> None:
        for c in list(self._clients):
            try:
                await c.send_json(ev)
            except Exception:
                self._clients.discard(c)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

        def pump() -> None:
            while True:
                try:
                    ev = self._events.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    asyncio.run_coroutine_threadsafe(self._broadcast(ev), loop)
                except RuntimeError:  # loop closed — shutting down
                    return

        self._pump = threading.Thread(target=pump, daemon=True)
        self._pump.start()

    def add(self, client: Any) -> None:
        self._clients.add(client)

    def remove(self, client: Any) -> None:
        self._clients.discard(client)
