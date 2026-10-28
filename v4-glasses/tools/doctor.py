"""Preflight doctor — checks the laptop side is demo-ready.

    python -m tools.doctor

Verifies: core deps import, the OCR backend is usable, the catalog validates,
thresholds are frozen, cached datasheets are present + reviewed, and the UI exists.
Prints an OK/WARN/FAIL table. Mirrors the parent repo's scripts/doctor.py style.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"


def _check(name, fn):
    try:
        status, detail = fn()
    except Exception as e:
        status, detail = FAIL, str(e)
    print(f"[{status}] {name:28s} {detail}")
    return status


def check_deps():
    missing = [m for m in ("fastapi", "uvicorn", "PIL", "numpy", "yaml", "jsonschema", "fitz")
               if importlib.util.find_spec(m) is None]
    return (OK, "all core deps present") if not missing else (FAIL, "missing: " + ", ".join(missing))


def check_ocr():
    from server import config
    from server.ocr.base import get_engine
    try:
        get_engine(config.OCR_BACKEND)
        return OK, f"backend '{config.OCR_BACKEND}' usable"
    except Exception as e:
        return WARN, f"backend '{config.OCR_BACKEND}' unavailable ({str(e)[:50]}); set GLASSES_OCR_BACKEND"


def check_catalog():
    from catalog.validate import validate
    errs = validate()
    return (OK, "catalog valid") if not errs else (FAIL, f"{len(errs)} problem(s); run catalog.validate")


def check_thresholds():
    from server import thresholds as th
    t = th.load()
    if t.frozen:
        return OK, f"frozen {t.version}"
    return WARN, "UNFROZEN — run eval.calibrate then eval.freeze before trials"


def check_datasheets():
    from catalog.validate import validate
    errs = validate(strict=True)
    ds = [e for e in errs if "PDF missing" in e or "not reviewed" in e]
    return (OK, "all datasheets cached + reviewed") if not ds else (WARN, f"{len(ds)} unresolved (run catalog.fetch + review)")


def check_ui():
    idx = ROOT / "ui" / "web" / "index.html"
    return (OK, "ui present") if idx.exists() else (FAIL, "ui/web/index.html missing")


def main() -> int:
    print("Glasses doctor\n" + "-" * 50)
    statuses = [
        _check("python deps", check_deps),
        _check("OCR backend", check_ocr),
        _check("catalog", check_catalog),
        _check("thresholds", check_thresholds),
        _check("datasheets (strict)", check_datasheets),
        _check("UI", check_ui),
    ]
    print("-" * 50)
    if FAIL in statuses:
        print("NOT ready — resolve FAIL items.")
        return 1
    if WARN in statuses:
        print("Mostly ready — WARN items are fine for dev, resolve before a graded demo.")
        return 0
    print("All green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
