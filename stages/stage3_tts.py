"""Stage 3 — Kokoro streaming TTS fed by live main-model tokens.

Run:  python -m stages.stage3_tts                       # LLM -> TTS pipeline REPL
      python -m stages.stage3_tts --say "hello there"   # TTS only, no LLMs loaded

Watch for: audio starting BEFORE the printed text finishes streaming — that's
the sentence-chunked pipeline working.
"""
import sys
import time

from jarvis import prompts
from jarvis.audio import AudioPlayer
from jarvis.tts import KokoroStreamer


def main() -> None:
    player = AudioPlayer()
    print("Loading Kokoro...")
    tts = KokoroStreamer(player)

    if len(sys.argv) > 2 and sys.argv[1] == "--say":
        tts.say(" ".join(sys.argv[2:]))
        player.wait_done()
        return

    from jarvis.llm import ModelManager  # heavy import only in pipeline mode
    models = ModelManager()
    history = [{"role": "system", "content": prompts.MAIN_SYSTEM_PROMPT}]
    print("Ready. Type something; reply streams to speakers.\n")
    while True:
        query = input("you> ").strip()
        if not query:
            continue
        history.append({"role": "user", "content": query})
        t0 = time.monotonic()

        def stream_and_print():
            for delta in models.chat_stream(history):
                print(delta, end="", flush=True)
                yield delta

        print("jarvis> ", end="", flush=True)
        spoken = tts.speak_stream(stream_and_print())
        history.append({"role": "assistant", "content": spoken})
        player.wait_done()
        print(f"\n  [turn {time.monotonic() - t0:.2f}s]")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nbye")
