"""JARVIS — full assembled loop (this is also stage 6).

Run:  python -m jarvis.main

Pipeline per turn:
  mic -> VAD -> whisper(medium) partials/final -> fast-path/router(0.5B)
      -> trivial: router answers | tool: HTTP+template or 7B | main: 7B
      -> coder: swap Qwen2.5-Coder in/out
      -> Kokoro streams speech; barge-in monitor can kill everything mid-word.
"""
from __future__ import annotations

import sys
import time

from . import config, prompts
from .agent import stream_turn
from .audio import AudioPlayer, MicStream
from .bargein import BargeInMonitor, ResumeBuffer
from .coder import code_task
from .llm import ModelManager
from .memory import Memory
from .refine import refine
from .router import route
from .stt import Transcriber, listen_once
from .tts import KokoroStreamer


class Jarvis:
    def __init__(self) -> None:
        print("[boot] loading models (router + main + whisper + kokoro stay resident)")
        t0 = time.monotonic()
        self.models = ModelManager()
        self.transcriber = Transcriber()
        self.transcriber.warm_up()
        self.player = AudioPlayer()
        self.tts = KokoroStreamer(self.player)
        self.history = [{"role": "system", "content": prompts.MAIN_SYSTEM_PROMPT}]
        self.resume = ResumeBuffer()
        self.memory = Memory()
        print(f"[boot] ready in {time.monotonic() - t0:.1f}s")

    def run(self) -> None:
        with MicStream() as mic:
            monitor = BargeInMonitor(mic, self.player)
            monitor.start()
            print("Listening. (Ctrl-C to quit)\n")
            preroll = None
            try:
                while True:
                    query = listen_once(
                        mic, self.transcriber,
                        on_partial=self._show_partial, preroll=preroll,
                    )
                    preroll = None
                    if query and config.BLABBER_REFINE:
                        refined = refine(self.models, query)
                        if refined.lower() != query.strip().lower():
                            print(f"\r  [raw  : {query[:90]}]")
                        query = refined
                    print(f"\ryou    : {query:<100}")
                    if not query:
                        continue
                    interrupted = self._respond(query, monitor)
                    if interrupted:
                        preroll = monitor.leadin  # user is already mid-sentence
                    else:
                        time.sleep(config.POST_SPEECH_COOLDOWN_S)
                        mic.drain()  # flush our own voice from the mic queue
            finally:
                monitor.stop()
                self.player.close()

    # ------------------------------------------------------------------
    def _respond(self, query: str, monitor: BargeInMonitor) -> bool:
        """Speak a reply. Returns True if barged-in (caller reuses lead-in audio)."""
        t0 = time.monotonic()
        r = route(self.models, query)
        print(f"  [route={r.label} kind={r.kind} +{(time.monotonic()-t0)*1000:.0f}ms]")
        monitor.arm()
        interrupt = monitor.interrupt

        if r.kind in ("fast_path", "trivial"):
            deltas = iter([r.text])
        elif r.kind == "resume":
            rest = self.resume.remainder()
            deltas = iter([rest]) if rest else iter(["I'd already lost that one. Ask me again."])
            self.resume.clear()
        elif r.kind == "coder":
            deltas = code_task(self.models, query, interrupt=interrupt)
        else:
            q = query
            if r.kind in ("main", "cloud"):
                mem = self.memory.context(query)
                if mem:
                    print(f"  [memory: {mem.count(chr(10))} fact(s) recalled]")
                    q = f"{query}\n\n{mem}"
            deltas = stream_turn(self.models, self.history, q,
                                 r.kind, r.tool, interrupt=interrupt)

        full_parts: list[str] = []

        def tee():
            for d in deltas:
                full_parts.append(d)
                sys.stdout.write(d)
                sys.stdout.flush()
                yield d

        spoken = self.tts.speak_stream(tee(), interrupt=interrupt)
        if not interrupt.is_set():
            self.player.wait_done()
        print(f"\n  [turn {time.monotonic() - t0:.2f}s]")

        if r.kind in ("main", "cloud", "trivial") and full_parts:
            self.memory.extract_async(self.models, query, "".join(full_parts))

        if interrupt.is_set():
            self.resume.store("".join(full_parts), spoken)
            print(f"  [barge-in: reply interrupted, remainder kept {config.RESUME_WINDOW_S}s]")
            return True
        return False

    @staticmethod
    def _show_partial(text: str) -> None:
        sys.stdout.write(f"\r  hearing: {text[:90]:<90}")
        sys.stdout.flush()


def main() -> None:
    try:
        Jarvis().run()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
