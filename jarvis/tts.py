"""Kokoro TTS (MLX port via mlx-audio), streaming as tokens arrive.

Strategy: buffer LLM deltas until a sentence boundary (or TTS_MIN_CHUNK_CHARS
mid-clause), synthesize that chunk, push PCM to the player queue, keep going.
First audio typically starts before the LLM finishes its first sentence.

Synthesis runs inline on the caller's thread — which is the LLM streaming
thread. That serializes Kokoro against generation on the Metal device, but
Kokoro is 82M params (~tens of ms per chunk) and MLX streams fill the player
queue faster than real-time playback drains it, so the audio never starves.
Keeping it single-threaded also avoids two threads issuing Metal work against
a memory pool we're managing tightly on 16GB.

NOTE: mlx-audio's API has drifted across versions; this targets the
`load_model(...).generate(...)` interface (>=0.1). If your version differs,
`pip install -U mlx-audio` or adapt `_synthesize`.
"""
from __future__ import annotations

import re
import threading
from typing import Iterable

import numpy as np

from . import config
from .audio import AudioPlayer

_SENTENCE_END = re.compile(r"[.!?…]['\")\]]?\s")


class KokoroStreamer:
    def __init__(self, player: AudioPlayer, model: str = config.KOKORO_MODEL) -> None:
        from mlx_audio.tts.utils import load_model
        self.model = load_model(model)
        self.player = player

    def _synthesize(self, text: str) -> np.ndarray | None:
        text = text.strip()
        if not text:
            return None
        chunks = []
        for result in self.model.generate(
            text=text, voice=config.KOKORO_VOICE, speed=config.KOKORO_SPEED
        ):
            audio = np.asarray(result.audio, dtype=np.float32).reshape(-1)
            chunks.append(audio)
        return np.concatenate(chunks) if chunks else None

    def say(self, text: str, interrupt: threading.Event | None = None) -> None:
        """Speak a complete string (used for canned/trivial replies)."""
        self.speak_stream(iter([text]), interrupt)

    def speak_stream(
        self,
        deltas: Iterable[str],
        interrupt: threading.Event | None = None,
    ) -> str:
        """Consume LLM text deltas, speak sentence-by-sentence.
        Returns the full text actually spoken (for the resume buffer)."""
        buf, spoken = "", []
        for delta in deltas:
            if interrupt is not None and interrupt.is_set():
                return "".join(spoken)
            buf += delta
            while True:
                m = _SENTENCE_END.search(buf)
                if m:
                    cut = m.end()
                elif len(buf) >= config.TTS_MIN_CHUNK_CHARS * 3:
                    # runaway clause with no punctuation — cut at last space
                    cut = buf.rfind(" ", 0, config.TTS_MIN_CHUNK_CHARS * 3)
                    if cut <= 0:
                        break
                else:
                    break
                chunk, buf = buf[:cut], buf[cut:]
                self._emit(chunk, interrupt)
                spoken.append(chunk)
                if interrupt is not None and interrupt.is_set():
                    return "".join(spoken)
        if buf.strip():
            self._emit(buf, interrupt)
            spoken.append(buf)
        return "".join(spoken)

    def _emit(self, chunk: str, interrupt: threading.Event | None) -> None:
        if interrupt is not None and interrupt.is_set():
            return
        audio = self._synthesize(chunk)
        if audio is not None:
            self.player.play(audio)
