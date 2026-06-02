"""
Trip intent + catalog outdoor path tracing.

Log lines go to ``.cache/trip_intent_trace.log`` when ``WANDERLY_ITIN_LOG`` is on (default).
JSON dumps under ``.cache/_trip_intent_debug/`` when ``WANDERLY_TRIP_INTENT_DEBUG=1``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.wanderly.itinerary_debug_log import itinerary_file_log_enabled, log_itinerary_build

_CACHE = Path(__file__).resolve().parent.parent.parent / ".cache"
_LOG_FILE = _CACHE / "trip_intent_trace.log"
_DUMP_DIR = _CACHE / "_trip_intent_debug"


def trip_intent_debug_enabled() -> bool:
    return (os.environ.get("WANDERLY_TRIP_INTENT_DEBUG") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def log_trip_intent(msg: str) -> None:
    """Console + trip_intent_trace.log (mirrors itinerary log gate)."""
    if not itinerary_file_log_enabled():
        return
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond // 1000:03d}Z"
    line = f"{ts} [trip_intent] {msg}"
    print(line, flush=True)
    try:
        _CACHE.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{line}\n")
    except OSError:
        pass
    log_itinerary_build(f"TRIP_INTENT {msg}")


def dump_trip_intent_debug(name: str, payload: dict[str, Any]) -> None:
    if not trip_intent_debug_enabled():
        return
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:80]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = _DUMP_DIR / f"{ts}_{safe}.json"
    try:
        _DUMP_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        log_trip_intent(f"dump {path.name} keys={sorted(payload.keys())}")
    except OSError as exc:
        log_trip_intent(f"dump_fail name={name!r} err={exc!s}")
