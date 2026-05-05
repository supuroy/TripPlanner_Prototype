"""Optional debug logging for lazy itinerary builds and Cursor Cloud Agent traces.

Lines prefixed ``CURSOR path=…`` come from ``cursor_agent_client`` (409 reuse, SSE errors).
Filter ``itinerary_debug.log`` by ``path=cursor_advisor.advisor_feed`` (or other ids) to see
which app path triggered a Cursor call.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
_LOG_FILE = _LOG_DIR / "itinerary_debug.log"


def itinerary_debug_verbose() -> bool:
    return (os.environ.get("WANDERLY_ITIN_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")


def itinerary_file_log_enabled() -> bool:
    """Mirror hero_images: default on unless explicitly disabled."""
    v = (os.environ.get("WANDERLY_ITIN_LOG") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def log_itinerary_build(msg: str) -> None:
    """Console + ``.cache/itinerary_debug.log`` when ``WANDERLY_ITIN_LOG`` is not disabled (default on).

    Wanderly uses this for itinerary rows, HTTP milestones (``path=http.…``), advisor failures
    (``path=cursor_advisor.…``), and Cursor transport (``CURSOR path=…``).
    """
    if not itinerary_file_log_enabled():
        return
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond // 1000:03d}Z"
    line = f"{ts} [itinerary] {msg}"
    print(line, flush=True)
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{line}\n")
    except OSError:
        pass
