"""Web dashboard server — the glowing-orb control surface for all 5 capabilities.

Run:  python -m jarvis.server          (opens http://127.0.0.1:7777)

Design:
- The browser is a CONTROL SURFACE, not an audio device. Mic capture and
  speaker playback stay in Python (sounddevice), reusing every existing
  module — the page just shows state and sends commands over one WebSocket.
- Models load LAZILY per mode, because on smaller machines you don't want
  whisper + 7B + Kokoro resident just to use the TTS tab:
      transcribe -> whisper          speak -> kokoro
      tools      -> kokoro (speech)  coder -> coder 7B (on demand)
      chat       -> everything (same residency policy as jarvis.main)
- One worker thread runs the active mode's loop. Switching modes sets a stop
  Event (listen_once honors it), joins the thread, starts the next.
- Voice task-select: on the menu, once whisper is loaded, we listen for
  option words ("chat", "transcribe", ...) and select the matching card.

Events out: state, mode, loading, partial, heard, delta, done, route,
            tool_result, log, error, menu_hint
Commands in: select_mode, text, tool, stop_speaking
"""
from __future__ import annotations

import asyncio
import json
import queue
import re
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

# NOTE: these imports MUST be module-level. This file uses
# `from __future__ import annotations` (stringified type hints), and FastAPI
# resolves the `websocket: WebSocket` annotation against module globals —
# with the import hidden inside create_app it silently degrades to a required
# query parameter and every handshake dies with 403/1008 "Field required".
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

import numpy as np

from . import config, prompts, tools
from .agent import stream_turn
from .audio import AudioPlayer, MicStream, UtteranceDetector
from .bargein import BargeInMonitor, ResumeBuffer
from .coder import code_task
from .llm import ModelManager
from .memory import Memory
from .refine import CONTINUE_RE, AnyEvent, clean_transcript, refine
from .router import route
from .stt import Transcriber, listen_once
from .tts import KokoroStreamer
from .wake import ClapDetector, build_wake_audio

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
HOST, PORT = "127.0.0.1", 7777

_EVENTS: queue.Queue[dict] = queue.Queue()


def emit(type: str, **kw) -> None:
    _EVENTS.put({"type": type, **kw})


def _whisper_cached() -> bool:
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(config.WHISPER_MODEL, local_files_only=True)
        return True
    except Exception:
        return False


_WEATHER_RE = re.compile(r"\b(weather|temperature|forecast|rain\w*|snow\w*|sunny|humid\w*)\b", re.I)
_STOCK_RE = re.compile(r"\b(stocks?|shares?|ticker|nasdaq|dow)\b|\bprice of\b", re.I)


def _parse_tool_speech(text: str):
    """Map a spoken request to (tool, arg) for tools mode."""
    from .agent import _extract_location, _extract_ticker
    if _WEATHER_RE.search(text):
        return "get_weather", _extract_location(text)
    if _STOCK_RE.search(text):
        return "get_stock", _extract_ticker(text) or text
    return "web_search", text


_MENU_WORDS = [  # (regex, mode) — checked in order against the heard phrase
    (re.compile(r"\b(chat|talk|conversation|assistant|jarvis|jupiter)\b", re.I), "chat"),
    (re.compile(r"\b(transcri|dictat|listen)\w*", re.I), "transcribe"),
    (re.compile(r"\b(speak|say|read|voice out|tts)\b", re.I), "speak"),
    (re.compile(r"\b(tool|weather|stock|search)\w*", re.I), "tools"),
    (re.compile(r"\b(code|coder|coding|build|program)\w*", re.I), "coder"),
]


class Backend:
    """Owns models + audio; runs one mode loop at a time on a worker thread."""

    def __init__(self) -> None:
        self.models: ModelManager | None = None
        self.transcriber: Transcriber | None = None
        self.player: AudioPlayer | None = None
        self.tts: KokoroStreamer | None = None
        self.history = [{"role": "system", "content": prompts.MAIN_SYSTEM_PROMPT}]
        self.resume = ResumeBuffer()
        self.memory = Memory()
        self._mode = "menu"
        self._woken = False   # first-boot clap wake happens exactly once
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._inbox: queue.Queue = queue.Queue()   # typed text / tool commands
        self._lock = threading.Lock()              # serializes mode switches
        # Per-resource load locks: a stale mode thread may still be downloading
        # when the user switches; the new mode must wait for THAT resource
        # (no duplicate download) — but unrelated loads must not queue behind
        # it (clicking Speak during the whisper download must load Kokoro now).
        self._load_locks = {"stt": threading.Lock(), "tts": threading.Lock(),
                            "llm": threading.Lock()}

    # ---- lazy loaders --------------------------------------------------------
    def ensure_stt(self) -> Transcriber:
        with self._load_locks["stt"]:
            if self.transcriber is None:
                emit("loading", what="whisper (first run downloads ~1.5GB)")
                t = Transcriber()
                t.warm_up()
                self.transcriber = t
                emit("loaded", what="whisper")
        return self.transcriber

    def ensure_tts(self) -> KokoroStreamer:
        with self._load_locks["tts"]:
            if self.tts is None:
                emit("loading", what="kokoro")
                self.player = self.player or AudioPlayer()
                self.tts = KokoroStreamer(self.player)
                emit("loaded", what="kokoro")
        return self.tts

    def ensure_llm(self) -> ModelManager:
        with self._load_locks["llm"]:
            if self.models is None:
                emit("loading", what="router + main LLM")
                self.models = ModelManager()
                emit("loaded", what="router + main LLM")
            elif not self.models.resident:
                # a coder-only session left behind an empty shell — fill it
                emit("loading", what="router + main LLM")
                self.models.load_resident()
                emit("loaded", what="router + main LLM")
        return self.models

    def ensure_llm_shell(self) -> ModelManager:
        """ModelManager without resident models — enough for the coder swap."""
        with self._load_locks["llm"]:
            if self.models is None:
                self.models = ModelManager(load_now=False)
        return self.models

    # ---- mode control --------------------------------------------------------
    def switch(self, mode: str) -> None:
        with self._lock:
            if mode == self._mode and self._thread and self._thread.is_alive():
                return
            self._stop.set()
            if self.player:
                self.player.stop()
            if self._thread and self._thread.is_alive():
                # Short join only: the old thread may be blocked on a model
                # download and can't be waited for. It exits on its own at the
                # next stop-check; audio/model conflicts are prevented by the
                # stop-check after ensure_*() and the load lock, not by joining.
                self._thread.join(timeout=0.5)
            self._stop = threading.Event()
            self._inbox = queue.Queue()
            self._mode = mode
            if mode != "standby":
                self._woken = True  # any user-driven mode entry counts as waking
            emit("mode", value=mode)
            target = {
                "standby": self._run_standby,
                "menu": self._run_menu,
                "chat": self._run_chat,
                "transcribe": self._run_transcribe,
                "speak": self._run_speak,
                "tools": self._run_tools,
                "coder": self._run_coder,
            }.get(mode)
            if target is None:
                emit("error", text=f"unknown mode '{mode}'")
                return
            self._thread = threading.Thread(
                target=self._guard, args=(target, self._stop), daemon=True)
            self._thread.start()

    def _guard(self, target, stop: threading.Event) -> None:
        try:
            target(stop)
        except Exception as e:  # surface crashes in the UI instead of dying silently
            emit("error", text=f"{type(e).__name__}: {e}")
            emit("state", value="idle")

    def submit(self, item: dict) -> None:
        self._inbox.put(item)

    def stop_speaking(self) -> None:
        if self.player:
            self.player.stop()

    def _level_cb(self):
        """Throttled mic-level emitter (~14/s) driving the orb in the UI."""
        last = [0.0]

        def cb(v: float) -> None:
            now = time.monotonic()
            if now - last[0] >= 0.07:
                last[0] = now
                emit("level", value=round(min(1.0, v * 6), 3))
        return cb

    # ---- blabber handling ----------------------------------------------------
    def _listen_window(self, mic, stop: threading.Event, lvl, window: float) -> str:
        """Listen for one more utterance, but give up after `window` seconds of
        continued silence. Never cuts off speech already in progress."""
        det = UtteranceDetector()
        expired = threading.Event()

        def watchdog() -> None:
            deadline = time.monotonic() + window
            while (time.monotonic() < deadline or det.active) and not stop.is_set():
                time.sleep(0.08)
            expired.set()

        threading.Thread(target=watchdog, daemon=True).start()
        return listen_once(mic, self.transcriber, detector=det,
                           stop=AnyEvent(stop, expired), on_level=lvl)

    def _capture_continuations(self, mic, text: str, stop: threading.Event,
                               lvl, max_ext: int = 2) -> str:
        """The utterance trailed off mid-thought ('…and, um') — wait briefly
        for the rest instead of responding to half a sentence."""
        for _ in range(max_ext):
            if stop.is_set() or not CONTINUE_RE.search(text):
                break
            emit("partial", text=text + " …")
            more = self._listen_window(mic, stop, lvl, config.CONTINUE_WINDOW_S)
            if not more:
                break
            text = f"{text} {more}"
        return text

    def _refine_speech(self, query: str) -> str:
        """Regex + (if resident) 0.6B cleanup of a spoken utterance."""
        if not config.BLABBER_REFINE:
            return query
        models = self.models if (self.models is not None and self.models.resident) else None
        refined = refine(models, query)
        if refined and refined.strip().lower() != query.strip().lower():
            emit("log", text=f"raw: “{query}”")
        return refined or query

    def _next_item(self, stop: threading.Event, timeout: float = 0.25):
        """Poll the inbox without blocking mode shutdown."""
        while not stop.is_set():
            try:
                return self._inbox.get(timeout=timeout)
            except queue.Empty:
                continue
        return None

    # ---- modes ---------------------------------------------------------------
    def _run_standby(self, stop: threading.Event) -> None:
        """First-boot dormant state: wait for a clap/snap, play the wake sound
        with the wake line overlaid, then hand off to the normal menu."""
        emit("state", value="standby")
        emit("menu_hint", text="👏 clap or snap to wake Jupiter")
        detector = ClapDetector()
        lvl = self._level_cb()
        woke = False
        with MicStream() as mic:
            while not stop.is_set():
                frame = mic.get(timeout=0.5)
                if frame is None:
                    continue
                rms = float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2)))
                lvl(rms)
                if detector.feed(rms):
                    woke = True
                    break
        if not woke or stop.is_set():
            return  # user clicked a card instead — switch() already marked woken
        self._woken = True
        emit("log", text="wake detected")
        emit("state", value="speaking")
        try:
            self.ensure_tts()
            audio = build_wake_audio(self.tts)
            if audio is not None:
                self.player.play(audio)
                self.player.wait_done()
        except Exception as e:
            emit("log", text=f"wake sound unavailable: {e}")
        time.sleep(config.POST_SPEECH_COOLDOWN_S)  # let the song's tail die before
        threading.Thread(target=self.switch, args=("menu",), daemon=True).start()

    def _run_menu(self, stop: threading.Event) -> None:
        emit("state", value="menu")
        # Voice select needs whisper. Cards must stay instantly clickable, so
        # this thread may block on the download — switch() won't wait for it.
        if self.transcriber is None and not _whisper_cached():
            emit("log", text="first run: downloading whisper for voice select — "
                             "cards work now; prefetch everything with "
                             "`python scripts/fetch_models.py`")
        try:
            self.ensure_stt()
        except Exception as e:
            emit("log", text=f"voice select unavailable: {e}")
            return
        if stop.is_set():
            return
        emit("menu_hint", text="Say an option: chat, transcribe, speak, tools, or coder")
        lvl = self._level_cb()
        with MicStream() as mic:
            while not stop.is_set():
                if self.player is not None and self.player.is_playing:
                    mic.drain()   # never transcribe our own audio (e.g. wake tail)
                    time.sleep(0.1)
                    continue
                heard = listen_once(mic, self.transcriber, stop=stop,
                                    on_level=lvl)
                if stop.is_set() or not heard:
                    continue
                emit("heard", text=heard)
                for rx, mode in _MENU_WORDS:
                    if rx.search(heard):
                        # switch() joins this thread — do it from outside
                        threading.Thread(
                            target=self.switch, args=(mode,), daemon=True).start()
                        return

    def _run_transcribe(self, stop: threading.Event) -> None:
        self.ensure_stt()
        emit("state", value="listening")
        lvl = self._level_cb()
        with MicStream() as mic:
            while not stop.is_set():
                final = listen_once(
                    mic, self.transcriber,
                    on_partial=lambda t: emit("partial", text=t), stop=stop,
                    on_level=lvl)
                if final:
                    # transcribe mode: regex cleanup only (no LLM rewriting of
                    # dictation — the user's words are the product here)
                    cleaned = clean_transcript(final) if config.BLABBER_REFINE else final
                    if cleaned and cleaned.lower() != final.lower():
                        emit("log", text=f"raw: “{final}”")
                    emit("heard", text=cleaned or final)

    def _run_speak(self, stop: threading.Event) -> None:
        self.ensure_tts()
        emit("state", value="idle")
        while not stop.is_set():
            item = self._next_item(stop)
            if not item:
                continue
            text = item.get("text", "").strip()
            if not text:
                continue
            emit("state", value="speaking")
            emit("delta", text=text)
            emit("done")
            self.tts.say(text)
            self.player.wait_done()
            emit("state", value="idle")

    def _run_tools(self, stop: threading.Event) -> None:
        self.ensure_stt()   # voice-first: "what's the weather in Tokyo" just works
        emit("state", value="listening")
        lvl = self._level_cb()
        with MicStream() as mic:
            while not stop.is_set():
                item = None
                try:
                    item = self._inbox.get_nowait()   # typed input wins if present
                except queue.Empty:
                    if self.player is not None and self.player.is_playing:
                        mic.drain()
                        time.sleep(0.1)
                        continue
                    heard = self._listen_window(mic, stop, lvl, 1.0)
                    if heard:
                        heard = self._refine_speech(heard)
                        tool, arg = _parse_tool_speech(heard)
                        emit("heard", text=heard)
                        item = {"tool": tool, "arg": arg}
                if not item or stop.is_set():
                    continue
                self._do_tool(item)
                time.sleep(config.POST_SPEECH_COOLDOWN_S)
                mic.drain()
                emit("state", value="listening")

    def _do_tool(self, item: dict) -> None:
        tool = item.get("tool", "")
        arg = item.get("arg", "") or item.get("text", "")
        emit("state", value="thinking")
        try:
            if tool == "get_weather":
                result = tools.get_weather(arg)
                spoken = tools.speak_weather(result)
            elif tool == "get_stock":
                result = tools.get_stock(arg)
                spoken = tools.speak_stock(result)
            elif tool == "web_search":
                result = tools.web_search(arg)
                top = result[0]["title"] if result else "nothing"
                spoken = f"Top result: {top}." if result else "Search came up empty."
            else:
                emit("error", text=f"unknown tool '{tool}'")
                return
        except Exception as e:
            emit("error", text=f"{tool} failed: {e}")
            return
        emit("tool_result", tool=tool, arg=arg, data=result, spoken=spoken)
        try:
            self.ensure_tts()
            emit("state", value="speaking")
            self.tts.say(spoken)
            self.player.wait_done()
        except Exception as e:
            emit("log", text=f"tts unavailable: {e}")

    def _run_coder(self, stop: threading.Event) -> None:
        models = self.ensure_llm_shell()
        self.ensure_stt()   # describe the build out loud
        emit("state", value="listening")
        lvl = self._level_cb()
        with MicStream() as mic:
            while not stop.is_set():
                task = None
                try:
                    task = self._inbox.get_nowait().get("text", "").strip()
                except queue.Empty:
                    if self.player is not None and self.player.is_playing:
                        mic.drain()
                        time.sleep(0.1)
                        continue
                    heard = self._listen_window(mic, stop, lvl, 1.0)
                    if heard:
                        heard = self._capture_continuations(mic, heard, stop, lvl)
                        heard = self._refine_speech(heard)
                        if len(heard.split()) < 4:
                            # stray noise/backchannel shouldn't launch a build
                            emit("log", text=f"too short for a build task: “{heard}”")
                            continue
                        emit("heard", text=heard)
                        task = heard
                if not task or stop.is_set():
                    continue
                emit("state", value="coding")
                interrupt = threading.Event()
                watcher = threading.Thread(  # propagate mode-switch into generation
                    target=lambda: (stop.wait(), interrupt.set()), daemon=True)
                watcher.start()
                for status in code_task(models, task, interrupt=interrupt):
                    emit("delta", text=status + "\n")
                    try:
                        self.ensure_tts()
                        self.tts.say(status)
                    except Exception:
                        pass
                emit("done")
                if self.player is not None:
                    self.player.wait_done()
                time.sleep(config.POST_SPEECH_COOLDOWN_S)
                mic.drain()   # a long build queued minutes of mic audio — discard
                emit("state", value="listening")

    def _run_chat(self, stop: threading.Event) -> None:
        models = self.ensure_llm()
        self.ensure_stt()
        self.ensure_tts()
        emit("state", value="listening")
        lvl = self._level_cb()
        with MicStream() as mic:
            monitor = BargeInMonitor(mic, self.player)
            monitor.start()
            preroll = None
            try:
                while not stop.is_set():
                    # typed input takes priority if the user sent one
                    typed = None
                    try:
                        typed = self._inbox.get_nowait().get("text", "").strip()
                    except queue.Empty:
                        pass
                    if typed:
                        query = typed
                    else:
                        emit("state", value="listening")
                        query = listen_once(
                            mic, self.transcriber,
                            on_partial=lambda t: emit("partial", text=t),
                            preroll=preroll, stop=stop, on_level=lvl)
                        if query:
                            query = self._capture_continuations(mic, query, stop, lvl)
                            query = self._refine_speech(query)
                    preroll = None
                    if stop.is_set() or not query:
                        continue
                    emit("heard", text=query)
                    interrupted = self._chat_respond(models, query, monitor, stop)
                    if interrupted:
                        preroll = monitor.leadin
                    else:
                        # our own voice + room tail is sitting in the mic queue —
                        # flush it so it can't become the next "user utterance"
                        time.sleep(config.POST_SPEECH_COOLDOWN_S)
                        mic.drain()
            finally:
                monitor.stop()

    def _chat_respond(self, models: ModelManager, query: str,
                      monitor: BargeInMonitor, stop: threading.Event) -> bool:
        t0 = time.monotonic()
        emit("state", value="thinking")
        r = route(models, query)
        emit("route", label=r.label, kind=r.kind,
             ms=round((time.monotonic() - t0) * 1000))
        monitor.arm()
        interrupt = monitor.interrupt

        if r.kind in ("fast_path", "trivial"):
            deltas = iter([r.text])
        elif r.kind == "resume":
            rest = self.resume.remainder()
            deltas = iter([rest]) if rest else iter(["I'd already lost that one. Ask me again."])
            self.resume.clear()
        elif r.kind == "coder":
            emit("state", value="coding")
            deltas = code_task(models, query, interrupt=interrupt)
        else:
            # Recalled memory rides on the user turn (not the system prompt) so
            # the main model's KV prefix cache survives across turns.
            q = query
            if r.kind in ("main", "cloud"):
                mem = self.memory.context(query)
                if mem:
                    emit("log", text=f"recalled {mem.count(chr(10))} memory fact(s)")
                    q = f"{query}\n\n{mem}"
            deltas = stream_turn(models, self.history, q,
                                 r.kind, r.tool, interrupt=interrupt)

        emit("state", value="speaking")
        full_parts: list[str] = []

        def tee():
            for d in deltas:
                if stop.is_set():
                    interrupt.set()
                    return
                full_parts.append(d)
                emit("delta", text=d)
                yield d

        spoken = self.tts.speak_stream(tee(), interrupt=interrupt)
        if not interrupt.is_set():
            self.player.wait_done()
        emit("done", secs=round(time.monotonic() - t0, 2))

        # Background fact extraction on the 0.6B — never blocks the turn.
        if r.kind in ("main", "cloud", "trivial") and full_parts:
            self.memory.extract_async(models, query, "".join(full_parts))

        if interrupt.is_set() and not stop.is_set():
            self.resume.store("".join(full_parts), spoken)
            emit("log", text="barge-in: reply interrupted, say 'finish that' to resume")
            return True
        return False


# ---- FastAPI app -------------------------------------------------------------
def create_app(backend: Backend):
    clients: set = set()

    async def broadcast(ev: dict) -> None:
        for c in list(clients):
            try:
                await c.send_json(ev)
            except Exception:
                clients.discard(c)

    @asynccontextmanager
    async def lifespan(app):
        loop = asyncio.get_running_loop()

        def pump() -> None:
            # Daemon thread + timeout poll: never blocks interpreter shutdown,
            # exits on its own once the loop is gone (Ctrl-C works first try).
            while True:
                try:
                    ev = _EVENTS.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    asyncio.run_coroutine_threadsafe(broadcast(ev), loop)
                except RuntimeError:  # loop closed — server is shutting down
                    return

        threading.Thread(target=pump, daemon=True).start()
        yield

    app = FastAPI(title="JARVIS dashboard", lifespan=lifespan)

    @app.get("/")
    async def index():
        return HTMLResponse((WEB_DIR / "index.html").read_text())

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        clients.add(websocket)
        await websocket.send_json({"type": "mode", "value": backend._mode})
        try:
            while True:
                msg = json.loads(await websocket.receive_text())
                cmd = msg.get("type")
                if cmd == "select_mode":
                    threading.Thread(target=backend.switch,
                                     args=(msg.get("mode", "menu"),),
                                     daemon=True).start()
                elif cmd == "text":
                    backend.submit({"text": msg.get("text", "")})
                elif cmd == "tool":
                    backend.submit({"tool": msg.get("tool", ""),
                                    "arg": msg.get("arg", "")})
                elif cmd == "stop_speaking":
                    backend.stop_speaking()
        except WebSocketDisconnect:
            clients.discard(websocket)

    return app


def main() -> None:
    import uvicorn
    backend = Backend()
    app = create_app(backend)
    backend.switch("standby" if config.WAKE_ON_CLAP and config.WAKE_SOUND.exists()
                   else "menu")
    if "--no-browser" not in sys.argv:  # Electron passes this and opens its own window
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    print(f"JARVIS dashboard -> http://{HOST}:{PORT}")
    # ws="wsproto": uvicorn's websockets-sansio backend (the default with
    # websockets>=14) rejected handshakes with 403 on macOS in testing;
    # wsproto is the older, boring implementation that just works.
    uvicorn.run(app, host=HOST, port=PORT, log_level="info", ws="wsproto")


if __name__ == "__main__":
    main()
