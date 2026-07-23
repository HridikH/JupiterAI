"""Stage 5 — coder model swap + sandboxed execution.

Run:  python -m stages.stage5_coder
Then: build> a python script that prints the first 20 fibonacci numbers

Watch the [llm] log lines: main KV drop -> coder load -> (maybe main weight
release) -> coder unload -> main restored. Keep Activity Monitor's Memory
Pressure graph open during this stage — that's the real test on 16GB.

Sandbox output lands in ~/jarvis_projects/<name>/.
"""
from jarvis.coder import code_task
from jarvis.llm import ModelManager


def main() -> None:
    models = ModelManager()  # resident pair loads first, like production
    print("Ready. Describe something to build.\n")
    while True:
        query = input("build> ").strip()
        if not query:
            continue
        for status in code_task(models, query):
            print(f"\njarvis> {status}")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nbye")
