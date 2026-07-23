"""Streaming STT on mlx-whisper (medium).

mlx-whisper has no native incremental API, so "streaming partials" is done the
standard way: keep the growing utterance buffer and re-transcribe it every
PARTIAL_INTERVAL_S. Whisper medium transcribes a few seconds of audio in
~200-400ms on M-series, so the cadence holds.

16GB callouts:
- medium (~1.5GB) instead of large-v3-turbo (~3.1GB): turbo's extra ~1.6GB is
  exactly the headroom the main model's KV cache needs. medium's accuracy is
  fine for near-field voice commands.
- Re-transcription runs on the same Metal device as the LLMs. During partials
  the LLM is idle (user still talking) so there's no real contention; the one
  place it matters is barge-in during coder generation — partials are simply
  skipped there (see main.py) and only the final pass runs.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

from . import config
from .audio import FRAME_SAMPLES, MicStream, UtteranceDetector

_PRE_ROLL_FRAMES = 10  # ~300ms kept from before VAD trigger so first word isn't clipped
_MAX_BACKLOG_FRAMES = 3  # skip partials unless we're caught up with the mic (prevents
                         # a death spiral where slow transcribes back up the frame
                         # queue and the end-of-utterance silence never gets processed)
_PARTIAL_WINDOW_S = 8    # partials re-transcribe only the last N seconds; the final
                         # pass still uses the full utterance


class Transcriber:
    def __init__(self, model: str = config.WHISPER_MODEL) -> None:
        self.model = model
        # warm-up: first mlx_whisper call JIT-compiles kernels; do it off the hot path
        import mlx_whisper  # noqa: F401 (import cost is the warmup)
        self._mlx_whisper = mlx_whisper

    def transcribe(self, audio_i16: np.ndarray) -> str:
        audio = audio_i16.astype(np.float32) / 32768.0
        out = self._mlx_whisper.transcribe(
            audio, path_or_hf_repo=self.model, language="en", fp16=True
        )
        return out["text"].strip()

    def warm_up(self) -> None:
        self.transcribe(np.zeros(config.SAMPLE_RATE, dtype=np.int16))


def listen_once(
    mic: MicStream,
    transcriber: Transcriber,
    on_partial: Callable[[str], None] | None = None,
    detector: UtteranceDetector | None = None,
    preroll: list[np.ndarray] | None = None,
    stop: threading.Event | None = None,
    on_level: Callable[[float], None] | None = None,
) -> str:
    """Block until one utterance is captured; stream partials along the way.

    preroll: frames captured by the barge-in monitor before it fired — the
    utterance is already in progress, so skip the trigger phase.
    stop: when set, abandon the capture and return "" (used by the dashboard
    to switch modes without waiting for an utterance)."""
    detector = detector or UtteranceDetector()
    ring: list[np.ndarray] = []
    utterance: list[np.ndarray] = []
    last_partial = 0.0
    max_frames = config.MAX_UTTERANCE_S * 1000 // config.FRAME_MS
    if preroll:
        detector.active = True
        utterance = list(preroll)
        last_partial = time.monotonic()

    while True:
        if stop is not None and stop.is_set():
            return ""
        frame = mic.get()
        if frame is None:
            continue
        if on_level is not None:  # RMS 0..~1 for UI reactivity; cheap (one frame)
            on_level(float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2))))
        state = detector.feed(frame)

        if state == "idle":
            ring.append(frame)
            if len(ring) > _PRE_ROLL_FRAMES:
                ring.pop(0)
        elif state == "started":
            utterance = ring + [frame]
            ring = []
            last_partial = time.monotonic()
        elif state == "speech":
            utterance.append(frame)
            now = time.monotonic()
            if (on_partial
                    and now - last_partial >= config.PARTIAL_INTERVAL_S
                    and mic.frames.qsize() <= _MAX_BACKLOG_FRAMES):
                window = _PARTIAL_WINDOW_S * 1000 // config.FRAME_MS
                text = transcriber.transcribe(np.concatenate(utterance[-window:]))
                # timestamp AFTER transcribing so cadence adapts to actual speed
                # instead of firing again on the very next frame when slow
                last_partial = time.monotonic()
                if text:
                    on_partial(text)
            if len(utterance) >= max_frames:
                return transcriber.transcribe(np.concatenate(utterance))
        elif state == "ended":
            if not utterance:
                continue
            return transcriber.transcribe(np.concatenate(utterance))
