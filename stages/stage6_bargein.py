"""Stage 6 — full voice loop with VAD barge-in. Thin alias for jarvis.main.

Run:  python -m stages.stage6_bargein     (identical to: python -m jarvis.main)

Test script:
1. Ask "explain how transformers work" — a deliberately long answer.
2. Talk over it mid-sentence. Audio should die within ~300ms and it should
   transcribe what you said, starting from your first word (lead-in frames
   are recycled into the next utterance).
3. Say "wait, finish that" — it speaks the generated-but-unspoken remainder.
4. Do this on speakers vs headphones; see bargein.py docstring for the echo
   tradeoff if speakers self-trigger.
"""
from jarvis.main import main

if __name__ == "__main__":
    main()
