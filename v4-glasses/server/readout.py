"""OPTIONAL Kokoro TTS readout of a result — OFF by default.

Reuses the parent repo's Kokoro streamer if its MLX-audio stack is importable in
this venv. Purely cosmetic: it speaks a short summary of the outcome. It plays no
role in identification and is disabled unless GLASSES_TTS_READOUT=1.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from .logging_setup import get

log = get("readout")

_tts = None
_lock = threading.Lock()


def _get_tts():
    global _tts
    with _lock:
        if _tts is not None:
            return _tts
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
            from jarvis.audio import AudioPlayer
            from jarvis.tts import KokoroStreamer
            _tts = KokoroStreamer(AudioPlayer())
        except Exception as e:  # MLX-audio not installed — readout unavailable
            log.info(f"TTS readout unavailable: {e}")
            _tts = False
        return _tts


def speak(text: str) -> bool:
    """Speak text on a background thread. Returns False if TTS is unavailable."""
    tts = _get_tts()
    if not tts:
        return False
    threading.Thread(target=lambda: tts.say(text), daemon=True).start()
    return True


def summarize(status: str, result: dict) -> str:
    if status == "accepted":
        return f"Accepted. {result.get('part_number', '')} by {result.get('manufacturer', '')}."
    if status == "candidate_selection":
        n = len(result.get("candidates", []))
        return f"{n} possible matches. Please choose one."
    if status == "recapture":
        return result.get("message", "Please recapture.")
    if status == "error":
        return "An error occurred."
    return ""
