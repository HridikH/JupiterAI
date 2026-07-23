"""VAD-based barge-in: continuous listening during TTS playback.

A monitor thread watches mic frames while audio is playing. Sustained speech
(VAD_BARGE_IN_MS, stricter than the normal trigger) sets the shared interrupt
Event, which:
  - the generation loops poll every token (llm.py) -> generation halts,
  - the TTS streamer polls every chunk (tts.py)    -> synthesis halts,
  - the player queue gets flushed here             -> audio stops NOW.
The frames that tripped the detector are handed to the next listen_once() as
pre-roll so the start of the interrupting sentence isn't lost.

Echo honesty: with open speakers, the mic hears Kokoro. webrtcvad can trip on
it. Mitigations, in order of effectiveness: (1) headphones/AirPods — solved
outright; (2) macOS "Voice Isolation" mic mode — very good; (3) the stricter
300ms sustained-speech window + aggressiveness 3 during playback — decent,
tuned here. Proper software AEC (speexdsp echo canceller) is the upgrade path
if you need open-speaker use; it's a contained change inside this module.

The interrupted reply is kept for RESUME_WINDOW_S. "wait, finish that" hits
the router's resume fast-path and speaks the unspoken remainder.
"""
from __future__ import annotations

import threading
import time

import numpy as np

from . import config
from .audio import AudioPlayer, MicStream, Vad


class ResumeBuffer:
    def __init__(self) -> None:
        self.full_text = ""
        self.spoken_text = ""
        self.at = 0.0

    def store(self, full: str, spoken: str) -> None:
        self.full_text, self.spoken_text, self.at = full, spoken, time.monotonic()

    def remainder(self) -> str | None:
        if not self.full_text or time.monotonic() - self.at > config.RESUME_WINDOW_S:
            return None
        rest = self.full_text[len(self.spoken_text):].strip()
        return rest or None

    def clear(self) -> None:
        self.full_text = self.spoken_text = ""


class BargeInMonitor:
    def __init__(self, mic: MicStream, player: AudioPlayer) -> None:
        self.mic = mic
        self.player = player
        self.interrupt = threading.Event()
        self.leadin: list[np.ndarray] = []   # frames that tripped the detector
        self._vad = Vad(aggressiveness=3)    # strictest during playback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._trigger_frames = config.VAD_BARGE_IN_MS // config.FRAME_MS

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def arm(self) -> None:
        """Call at the start of each spoken reply."""
        self.interrupt.clear()
        self.leadin = []
        self.mic.drain()  # anything said before this reply belongs to the past

    def _watch(self) -> None:
        voiced: list[np.ndarray] = []
        while not self._stop.is_set():
            if not self.player.is_playing or self.interrupt.is_set():
                voiced = []
                time.sleep(0.03)
                # while idle, keep mic queue from backing up between turns
                continue
            frame = self.mic.get(timeout=0.1)
            if frame is None:
                continue
            # RMS gate: our own playback echoing into the mic is quieter than a
            # user actually talking over us — VAD alone can't tell them apart.
            rms = float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2)))
            if rms < config.BARGE_IN_MIN_RMS:
                voiced = []
                continue
            if self._vad.is_speech(frame):
                voiced.append(frame)
                if len(voiced) >= self._trigger_frames:
                    self.leadin = voiced[:]
                    self.interrupt.set()   # halts generation + TTS chunking
                    self.player.stop()     # halts audio instantly
                    voiced = []
            else:
                voiced = []
