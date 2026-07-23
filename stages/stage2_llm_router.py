"""Stage 2 — router + main model resident, text REPL.

Run:  python -m stages.stage2_llm_router
Type queries; you'll see the routing decision and latency per turn.
Tools aren't wired yet (stage 4) — tool routes just print what WOULD run.
"""
import time

from jarvis import prompts
from jarvis.llm import ModelManager
from jarvis.router import route


def main() -> None:
    models = ModelManager()
    history = [{"role": "system", "content": prompts.MAIN_SYSTEM_PROMPT}]
    print("Ready. Try: 'what time is it' (fast path), 'hi' (router-answered), "
          "'explain the Monty Hall problem' (main model).\n")
    while True:
        query = input("you> ").strip()
        if not query:
            continue
        t0 = time.monotonic()
        r = route(models, query)
        t_route = (time.monotonic() - t0) * 1000
        print(f"  [route={r.label or r.kind} kind={r.kind} {t_route:.0f}ms]")

        if r.kind in ("fast_path", "trivial"):
            print(f"jarvis> {r.text}")
        elif r.kind == "tool":
            print(f"jarvis> (stage 4 will run {r.tool} here)")
        elif r.kind == "coder":
            print("jarvis> (stage 5 will swap in the coder model here)")
        elif r.kind == "resume":
            print("jarvis> (stage 6 will resume interrupted speech here)")
        else:
            history.append({"role": "user", "content": query})
            print("jarvis> ", end="", flush=True)
            reply, first = [], None
            for delta in models.chat_stream(history):
                if first is None:
                    first = time.monotonic() - t0
                print(delta, end="", flush=True)
                reply.append(delta)
            history.append({"role": "assistant", "content": "".join(reply)})
            print(f"\n  [first token {first:.2f}s]")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nbye")
