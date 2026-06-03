"""
Trail catalog lane tracing (intent search, pick, itinerary).

Logs to ``.cache/trail_catalog_trace.log`` when ``WANDERLY_ITIN_LOG`` is on.
JSON dumps under ``.cache/_trail_catalog_debug/`` when ``WANDERLY_TRAIL_CATALOG_DEBUG=1``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.wanderly.itinerary_debug_log import itinerary_file_log_enabled, log_itinerary_build

_CACHE = Path(__file__).resolve().parent.parent.parent / ".cache"
_LOG_FILE = _CACHE / "trail_catalog_trace.log"
_DUMP_DIR = _CACHE / "_trail_catalog_debug"


def trail_catalog_debug_enabled() -> bool:
    v = (os.environ.get("WANDERLY_TRAIL_CATALOG_DEBUG") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    return (os.environ.get("WANDERLY_TRIP_INTENT_DEBUG") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def log_trail_catalog(msg: str) -> None:
    if not itinerary_file_log_enabled():
        return
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond // 1000:03d}Z"
    line = f"{ts} [trail_catalog] {msg}"
    print(line, flush=True)
    try:
        _CACHE.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{line}\n")
    except OSError:
        pass
    log_itinerary_build(f"TRAIL_CATALOG {msg}")


def dump_trail_catalog_debug(name: str, payload: dict[str, Any]) -> None:
    if not trail_catalog_debug_enabled():
        return
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:80]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = _DUMP_DIR / f"{ts}_{safe}.json"
    try:
        _DUMP_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, default=str) + "\n", encoding="utf-8")
        log_trail_catalog(f"dump {path.name} keys={sorted(payload.keys())}")
    except OSError as exc:
        log_trail_catalog(f"dump_fail name={name!r} err={exc!s}")
