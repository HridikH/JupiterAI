"""Stage 1 — live mic transcription with streaming partials.

Run:  python -m stages.stage1_stt
Speak; partials print as you talk, final line prints on silence. Ctrl-C quits.
"""
import sys

from jarvis.audio import MicStream
from jarvis.stt import Transcriber, listen_once


def main() -> None:
    print("Loading whisper medium (first run downloads ~1.5GB)...")
    t = Transcriber()
    t.warm_up()
    print("Ready. Speak.\n")
    with MicStream() as mic:
        while True:
            def show_partial(text: str) -> None:
                sys.stdout.write(f"\r  partial: {text[:100]:<100}")
                sys.stdout.flush()

            final = listen_once(mic, t, on_partial=show_partial)
            print(f"\rFINAL   : {final:<100}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye")
