"""Structured logging with request_id everywhere.

Emits one JSON object per line to stdout and to a rotating file under LOG_DIR.
Every pipeline log call should pass request_id=... so eval/parse_* and manual
debugging can correlate a capture end-to-end.
"""
from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from . import config


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": round(record.created, 6),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # anything attached via logger.info(msg, extra={"request_id": ...})
        for key in ("request_id", "status", "stage", "error", "elapsed_ms",
                    "config_version", "config_hash"):
            val = getattr(record, key, None)
            if val is not None:
                obj[key] = val
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


_configured = False


def setup(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    config.ensure_dirs()
    fmt = JsonFormatter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)

    fileh = RotatingFileHandler(
        config.LOG_DIR / "server.jsonl", maxBytes=5_000_000, backupCount=3
    )
    fileh.setFormatter(fmt)

    root = logging.getLogger("glasses")
    root.setLevel(level)
    root.handlers[:] = [stream, fileh]
    root.propagate = False
    _configured = True


def get(name: str = "glasses") -> logging.Logger:
    setup()
    return logging.getLogger(name if name.startswith("glasses") else f"glasses.{name}")
