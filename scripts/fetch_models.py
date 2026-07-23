"""Prefetch all models so stage timings aren't polluted by downloads.
Usage: python scripts/fetch_models.py            # everything except coder
       python scripts/fetch_models.py --all      # include coder (~4.3GB more)
"""
import sys
from huggingface_hub import snapshot_download

sys.path.insert(0, ".")
from jarvis import config  # noqa: E402

MODELS = [config.MAIN_MODEL, config.ROUTER_MODEL, config.WHISPER_MODEL, config.KOKORO_MODEL]
if "--all" in sys.argv:
    MODELS.append(config.CODER_MODEL)

for repo in MODELS:
    print(f"==> {repo}")
    snapshot_download(repo)
print("Done. ~11GB on disk with --all; models load lazily at runtime.")
