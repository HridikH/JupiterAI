"""Voice input for hands-free datasheet Q&A — reuses the JarvisV3 Whisper stack.

WALL: this module is for asking questions about an ALREADY-ACCEPTED part's
datasheet. It is never on the identification path — no part number is ever
produced from speech. Identification stays deterministic catalog matching.

It lazily imports the parent repo's mlx-whisper Transcriber + sounddevice mic
(jarvis.stt / jarvis.audio) so the glasses venv only pays for it when voice is
actually used, and the whole server still runs if MLX isn't installed.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from .logging_setup import get

log = get("voice")

_PARENT = Path(__file__).resolve().parent.parent.parent   # the JarvisV3 repo root
_lock = threading.Lock()
_state = {"mic": None, "transcriber": None, "ready": None}


def available() -> bool:
    """True if the Whisper + mic stack can be loaded in this venv."""
    if _state["ready"] is not None:
        return _state["ready"]
    try:
        import mlx_whisper  # noqa: F401
        import sounddevice  # noqa: F401
        _state["ready"] = True
    except Exception as e:
        log.info(f"voice unavailable: {e}")
        _state["ready"] = False
    return _state["ready"]


def _ensure():
    """Load the mic + transcriber once (reusing parent modules)."""
    with _lock:
        if _state["transcriber"] is not None:
            return
        sys.path.insert(0, str(_PARENT))
        from jarvis.audio import MicStream
        from jarvis.stt import Transcriber
        t = Transcriber()
        t.warm_up()
        _state["transcriber"] = t
        _state["mic"] = MicStream  # class; opened per listen to avoid holding the device


def listen_once_text(timeout_s: float = 8.0) -> str:
    """Capture one spoken utterance from the laptop mic and transcribe it.

    Mic capture stays in Python (like the parent dashboard) — the browser is only
    a control surface. Returns '' on timeout / no speech.
    """
    if not available():
        return ""
    _ensure()
    sys.path.insert(0, str(_PARENT))
    from jarvis.stt import listen_once
    from jarvis.audio import UtteranceDetector

    MicStream = _state["mic"]
    transcriber = _state["transcriber"]
    stop = threading.Event()
    threading.Timer(timeout_s, stop.set).start()
    with MicStream() as mic:
        text = listen_once(mic, transcriber, detector=UtteranceDetector(), stop=stop)
    return (text or "").strip()


# ---- always-listening loop (voice commands + datasheet questions) -----------
_listen_thread: threading.Thread | None = None
_listen_stop = threading.Event()


def is_listening() -> bool:
    return _listen_thread is not None and _listen_thread.is_alive()


def start_always_listening(on_transcript) -> bool:
    """Continuously transcribe utterances and hand each to on_transcript(text).

    Keeps the mic open across utterances (lower latency than reopening). The mic
    stays in Python; the browser is only a control surface. Returns False if the
    voice stack is unavailable. Idempotent.

    NOTE (from the parent repo): with open speakers the VAD can hear TTS and
    self-trigger — use headphones or macOS Voice Isolation for a clean demo.
    """
    global _listen_thread
    if not available():
        return False
    if is_listening():
        return True
    _ensure()
    _listen_stop.clear()

    def loop():
        sys.path.insert(0, str(_PARENT))
        from jarvis.stt import listen_once
        from jarvis.audio import UtteranceDetector
        MicStream = _state["mic"]
        transcriber = _state["transcriber"]
        log.info("always-listening started")
        try:
            with MicStream() as mic:
                while not _listen_stop.is_set():
                    text = listen_once(mic, transcriber,
                                       detector=UtteranceDetector(), stop=_listen_stop)
                    if text and text.strip() and not _listen_stop.is_set():
                        try:
                            on_transcript(text.strip())
                        except Exception as e:  # a bad command must not kill the loop
                            log.warning(f"voice handler error: {e}")
        except Exception as e:
            log.warning(f"always-listening stopped: {e}")

    _listen_thread = threading.Thread(target=loop, daemon=True)
    _listen_thread.start()
    return True


def stop_always_listening() -> None:
    _listen_stop.set()
