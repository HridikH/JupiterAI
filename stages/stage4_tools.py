"""Stage 4 — tool layer, standalone.

Run:  python -m stages.stage4_tools --test-tools     # raw HTTP tools, no models
      python -m stages.stage4_tools                  # full routed REPL with tools

Try in the REPL:
  "what's the weather in Tokyo"        -> router->WEATHER, template answer, no 7B
  "nvidia stock"                       -> router->STOCK, template answer, no 7B
  "who won the last champions league"  -> router->SEARCH, snippets -> 7B summary
  "compare AAPL and MSFT performance"  -> MAIN, 7B makes native <tool_call>s
"""
import sys
import time

from jarvis import tools


def test_tools() -> None:
    print("web_search('mlx apple framework'):")
    for r in tools.web_search("mlx apple framework", max_results=3):
        print(f"  - {r['title'][:70]}")
    print("\nget_weather('Tokyo'):", tools.get_weather("Tokyo"))
    print("\nget_stock('AAPL'):", tools.get_stock("AAPL"))
    print("\nspoken:", tools.speak_weather(tools.get_weather("Tokyo")))
    print("spoken:", tools.speak_stock(tools.get_stock("AAPL")))


def repl() -> None:
    from jarvis import prompts
    from jarvis.agent import stream_turn
    from jarvis.llm import ModelManager
    from jarvis.router import route

    models = ModelManager()
    history = [{"role": "system", "content": prompts.MAIN_SYSTEM_PROMPT}]
    print("Ready.\n")
    while True:
        query = input("you> ").strip()
        if not query:
            continue
        t0 = time.monotonic()
        r = route(models, query)
        print(f"  [route={r.label} kind={r.kind}]")
        if r.kind in ("fast_path", "trivial"):
            print(f"jarvis> {r.text}")
            continue
        if r.kind in ("coder", "resume"):
            print("jarvis> (later stage)")
            continue
        print("jarvis> ", end="", flush=True)
        for delta in stream_turn(models, history, query, r.kind, r.tool):
            print(delta, end="", flush=True)
        print(f"\n  [turn {time.monotonic() - t0:.2f}s]")


if __name__ == "__main__":
    try:
        test_tools() if "--test-tools" in sys.argv else repl()
    except (KeyboardInterrupt, EOFError):
        print("\nbye")
