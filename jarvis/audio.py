"""Mic capture, VAD gating, speaker playback. All int16 mono.

Mic and speaker run on sounddevice (PortAudio) callbacks feeding queues, so
nothing here blocks on the Metal-side model work.
"""
from __future__ import annotations

import queue
import threading

import numpy as np
import sounddevice as sd
import webrtcvad

from . import config

FRAME_SAMPLES = config.SAMPLE_RATE * config.FRAME_MS // 1000  # 480 @ 16k/30ms


class MicStream:
    """Continuous mic capture -> queue of int16 frames of FRAME_MS each."""

    def __init__(self) -> None:
        self.frames: queue.Queue[np.ndarray] = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            callback=self._on_audio,
        )

    def _on_audio(self, indata, _frames, _time, _status) -> None:
        self.frames.put(indata[:, 0].copy())

    def __enter__(self) -> "MicStream":
        self._stream.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stream.stop()
        self._stream.close()

    def get(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        while not self.frames.empty():
            self.frames.get_nowait()


class Vad:
    """webrtcvad wrapper with trigger/hangover counting.

    Deliberately NOT silero: silero is better at echo rejection but drags in
    torch (~1GB+ of libs) — real money at the 16GB tier. webrtcvad is ~100KB.
    The barge-in echo problem is handled with a stricter trigger window
    instead (see BargeInMonitor in bargein.py).
    """

    def __init__(self, aggressiveness: int = config.VAD_AGGRESSIVENESS) -> None:
        self._vad = webrtcvad.Vad(aggressiveness)

    def is_speech(self, frame: np.ndarray) -> bool:
        return self._vad.is_speech(frame.tobytes(), config.SAMPLE_RATE)


class UtteranceDetector:
    """Feed frames; returns state transitions for utterance start/end."""

    def __init__(self, vad: Vad | None = None) -> None:
        self.vad = vad or Vad()
        self.trigger_frames = config.VAD_TRIGGER_MS // config.FRAME_MS
        self.silence_frames = config.VAD_SILENCE_MS // config.FRAME_MS
        self.reset()

    def reset(self) -> None:
        self.active = False
        self._voiced = 0
        self._silent = 0

    def feed(self, frame: np.ndarray) -> str:
        """Returns one of: 'idle', 'started', 'speech', 'ended'."""
        speech = self.vad.is_speech(frame)
        if not self.active:
            self._voiced = self._voiced + 1 if speech else 0
            if self._voiced >= self.trigger_frames:
                self.active = True
                self._silent = 0
                return "started"
            return "idle"
        if speech:
            self._silent = 0
            return "speech"
        self._silent += 1
        if self._silent >= self.silence_frames:
            self.reset()
            return "ended"
        return "speech"


class AudioPlayer:
    """Speaker output with an interruptible queue (float32 chunks)."""

    def __init__(self, sample_rate: int = config.TTS_SAMPLE_RATE) -> None:
        self._q: queue.Queue[np.ndarray | None] = queue.Queue()
        self._buf = np.zeros(0, dtype=np.float32)
        self._playing = threading.Event()
        self._stream = sd.OutputStream(
            samplerate=sample_rate, channels=1, dtype="float32",
            callback=self._on_need_audio,
        )
        self._stream.start()

    def _on_need_audio(self, outdata, frames, _time, _status) -> None:
        while len(self._buf) < frames:
            try:
                chunk = self._q.get_nowait()
            except queue.Empty:
                break
            if chunk is not None:
                self._buf = np.concatenate([self._buf, chunk])
        n = min(frames, len(self._buf))
        outdata[:n, 0] = self._buf[:n]
        outdata[n:, 0] = 0.0
        self._buf = self._buf[n:]
        if n == 0 and self._q.empty():
            self._playing.clear()

    def play(self, chunk: np.ndarray) -> None:
        self._playing.set()
        self._q.put(chunk.astype(np.float32))

    @property
    def is_playing(self) -> bool:
        return self._playing.is_set()

    def stop(self) -> None:
        """Hard interrupt: flush everything queued."""
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._buf = np.zeros(0, dtype=np.float32)
        self._playing.clear()

    def wait_done(self, poll: float = 0.05) -> None:
        import time
        while self.is_playing:
            time.sleep(poll)

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()
