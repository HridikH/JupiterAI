"""First-boot wake: clap/snap detection + wake-sound construction.

The dashboard starts in a dormant "standby" state. A clap or finger snap —
a loud, SHORT transient against a quiet background — wakes it: the wake
music plays with the spoken wake line mixed on top. This happens exactly
once per server run; afterwards every mode behaves as normal.
"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from . import config


class ClapDetector:
    """Transient detector: fires on a sharp spike that decays fast.

    Sustained loudness (speech, music) never fires — the spike must fall
    back near the background level within ~240ms of onset."""

    def __init__(self, threshold: float = config.CLAP_THRESHOLD) -> None:
        self.threshold = threshold
        self.bg = 0.0        # EMA of background RMS
        self._spike = 0      # frames since spike onset (0 = idle)

    def feed(self, rms: float) -> bool:
        fired = False
        if self._spike:
            self._spike += 1
            if rms < max(self.bg * 2, 0.02):
                fired = self._spike <= 8          # short = clap/snap
                self._spike = 0
            elif self._spike > 12:                # long = speech/music, not a clap
                self._spike = 0
        elif rms > max(self.threshold, self.bg * 6):
            self._spike = 1
        self.bg = 0.95 * self.bg + 0.05 * rms
        return fired


def build_wake_audio(tts) -> np.ndarray | None:
    """Wake music (first 10s, ducked) + spoken wake line overlaid at ~1.2s."""
    music, sr = sf.read(str(config.WAKE_SOUND), dtype="float32")
    if music.ndim > 1:
        music = music.mean(axis=1)
    if sr != config.TTS_SAMPLE_RATE:  # nearest-neighbour resample is fine for a jingle
        idx = np.linspace(0, len(music) - 1,
                          int(len(music) * config.TTS_SAMPLE_RATE / sr)).astype(int)
        music = music[idx]
    music = music[: 10 * config.TTS_SAMPLE_RATE] * 0.85  # loud — ducked only under the voice

    speech = tts._synthesize(config.WAKE_LINE)
    if speech is not None:
        start = int(1.2 * config.TTS_SAMPLE_RATE)
        end = min(len(music), start + len(speech))
        if end > start:
            music[start:end] *= 0.4  # sidechain duck so the line cuts through
            music[start:end] += speech[: end - start] * 1.3

    return np.clip(music, -1.0, 1.0)
