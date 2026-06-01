"""Optional debug logging for lazy itinerary builds and Cursor Cloud Agent traces.

Lines prefixed ``CURSOR path=…`` come from ``cursor_agent_client`` (409 reuse, SSE errors).
Filter ``itinerary_debug.log`` by ``path=cursor_advisor.advisor_feed`` (or other ids) to see
which app path triggered a Cursor call.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
_LOG_FILE = _LOG_DIR / "itinerary_debug.log"


def itinerary_debug_verbose() -> bool:
    return (os.environ.get("WANDERLY_ITIN_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")


def logistics_debug_enabled() -> bool:
    """Extra per-build logistics / ``day_logistics`` traces (raw keys, first row)."""
    return (os.environ.get("WANDERLY_LOGISTICS_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")


def itinerary_file_log_enabled() -> bool:
    """Mirror hero_images: default on unless explicitly disabled."""
    v = (os.environ.get("WANDERLY_ITIN_LOG") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def log_alltrails_itinerary_context(
    *,
    dest_id: str,
    atc: dict[str, Any] | None,
    llm_wrapper_had_ctx: bool | None = None,
    channel: str = "build",
) -> None:
    """Trace AllTrails context attached to an itinerary build (debug; no API shape change)."""
    did = (dest_id or "").strip() or "?"
    if not isinstance(atc, dict):
        log_itinerary_build(
            f"ALLTRAILS_CTX channel={channel} dest_id={did!r} atc_present=0 "
            f"llm_wrapper_had_ctx={llm_wrapper_had_ctx}"
        )
        return
    parent = str(atc.get("parent_alltrails_url") or "").strip()
    log_itinerary_build(
        f"ALLTRAILS_CTX channel={channel} dest_id={did!r} atc_present=1 "
        f"trail_name={str(atc.get('trail_name') or '')[:80]!r} "
        f"verification_status={str(atc.get('verification_status') or '')!r} "
        f"verified={atc.get('verified')!r} has_parent_url={1 if parent else 0} "
        f"llm_wrapper_had_ctx={llm_wrapper_had_ctx}"
    )


def log_alltrails_http_payload(
    *,
    dest_id: str,
    atc: dict[str, Any] | None,
    channel: str,
    included_in_response: bool,
) -> None:
    """Log whether ``alltrails_search_context`` is included in the HTTP/SSE response body."""
    did = (dest_id or "").strip() or "?"
    if not isinstance(atc, dict):
        log_itinerary_build(
            f"ALLTRAILS_HTTP channel={channel} dest_id={did!r} atc_in_response=0 included=0"
        )
        return
    parent = str(atc.get("parent_alltrails_url") or "").strip()
    log_itinerary_build(
        f"ALLTRAILS_HTTP channel={channel} dest_id={did!r} atc_in_response=1 "
        f"included_in_{channel}={1 if included_in_response else 0} "
        f"verification_status={str(atc.get('verification_status') or '')!r} "
        f"has_parent_url={1 if parent else 0}"
    )


def log_itinerary_build(msg: str) -> None:
    """Console + ``.cache/itinerary_debug.log`` when ``WANDERLY_ITIN_LOG`` is not disabled (default on).

    Wanderly uses this for itinerary rows, HTTP milestones (``path=http.…``), advisor failures
    (``path=cursor_advisor.…``), Cursor transport (``CURSOR path=…``), and ``LOGISTICS_TRACE`` lines.
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
