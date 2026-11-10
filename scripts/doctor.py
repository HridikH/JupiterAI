#!/usr/bin/env python3
"""JARVIS diagnostics — find out why a model won't load.

Run:  .venv/bin/python scripts/doctor.py

Checks, in increasing order of cost: interpreter/platform, package versions,
known-bad version combos, model cache on disk, then an actual load of the
smallest model (0.6B router, ~0.4GB). Stops at the first hard failure.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK, BAD, WARN = "  ok  ", " FAIL ", " warn "


def line(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}")


def version(pkg: str) -> str | None:
    try:
        from importlib.metadata import version as v

        return v(pkg)
    except Exception:
        return None


def check_platform() -> bool:
    line(OK, f"python {platform.python_version()} ({sys.executable})")
    machine = platform.machine()
    if platform.system() != "Darwin" or machine != "arm64":
        line(BAD, f"MLX requires Apple Silicon macOS; this is {platform.system()}/{machine}")
        return False
    line(OK, f"macOS {platform.mac_ver()[0]} on {machine}")
    return True


def check_packages() -> bool:
    required = ["mlx", "mlx-lm", "mlx-whisper", "mlx-audio", "numpy", "huggingface-hub"]
    missing = [p for p in required if version(p) is None]
    for p in required:
        v = version(p)
        line(OK if v else BAD, f"{p}: {v or 'NOT INSTALLED'}")
    if missing:
        line(BAD, f"install missing packages: pip install {' '.join(missing)}")
        return False

    # transformers 5.x removed APIs mlx-lm still calls; this is the single most
    # common cause of a load() that blows up with AttributeError/ImportError.
    tf = version("transformers")
    if tf:
        major = int(tf.split(".")[0])
        if major >= 5:
            line(WARN, f"transformers {tf} — mlx-lm expects 4.x, this combo is known to break")
            line(WARN, "  fix:  pip install 'transformers>=4.44,<5'")
        else:
            line(OK, f"transformers: {tf}")
    return True


def check_config():
    try:
        from jarvis import config

        line(OK, f"config imported; router={config.ROUTER_MODEL}")
        return config
    except Exception:
        line(BAD, "could not import jarvis.config")
        traceback.print_exc()
        return None


def check_cache(config) -> None:
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    if not cache.exists():
        line(WARN, "no HuggingFace cache — nothing downloaded yet")
        line(WARN, "  fix:  .venv/bin/python scripts/fetch_models.py")
        return
    size = shutil.disk_usage(cache)  # noqa: F841  (kept for parity/debug)
    total = sum(f.stat().st_size for f in cache.rglob("*") if f.is_file())
    line(OK, f"HF cache: {total / 1e9:.1f} GB at {cache}")

    for repo in (config.ROUTER_MODEL, config.MAIN_MODEL, config.WHISPER_MODEL,
                 config.KOKORO_MODEL):
        folder = cache / ("models--" + repo.replace("/", "--"))
        line(OK if folder.exists() else WARN,
             f"  {'cached' if folder.exists() else 'NOT cached'}: {repo}")


def check_load(config) -> bool:
    line(OK, f"loading smallest model: {config.ROUTER_MODEL} (this may download ~0.4GB) ...")
    try:
        from mlx_lm import load, generate
    except Exception:
        line(BAD, "could not import mlx_lm")
        traceback.print_exc()
        return False

    try:
        model, tok = load(config.ROUTER_MODEL)
    except Exception:
        line(BAD, "load() raised:")
        traceback.print_exc()
        return False

    line(OK, "model loaded — running a 5-token generation")
    try:
        out = generate(model, tok, prompt="Say hi.", max_tokens=5, verbose=False)
        line(OK, f"generate() returned: {out!r}")
    except Exception:
        line(BAD, "generate() raised:")
        traceback.print_exc()
        return False
    return True


def main() -> int:
    print("=" * 60)
    print("JARVIS doctor")
    print("=" * 60)

    if not check_platform():
        return 1
    print()
    if not check_packages():
        return 1
    print()
    config = check_config()
    if config is None:
        return 1
    print()
    check_cache(config)
    print()
    if not check_load(config):
        return 1

    print()
    print("All checks passed — the model layer works. If the dashboard still")
    print("misbehaves, the problem is in the server/audio layer, not the models.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
