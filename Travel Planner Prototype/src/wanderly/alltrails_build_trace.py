"""
AllTrails itinerary-build tracing — log lines + optional JSON dumps under ``.cache/_itinerary_build_debug/``.

Enable dumps with ``WANDERLY_ALLTRAILS_DEBUG=1`` (log lines are always on when ``WANDERLY_ITIN_LOG`` is on).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.wanderly.itinerary_debug_log import log_itinerary_build

_CACHE = Path(__file__).resolve().parent.parent.parent / ".cache"
_DUMP_DIR = _CACHE / "_itinerary_build_debug"

# Latest LLM wrapper per destination id (same process, sequential builds).
_last_llm_wrapper_by_dest: dict[str, dict[str, Any]] = {}


def alltrails_build_debug_enabled() -> bool:
    return (os.environ.get("WANDERLY_ALLTRAILS_DEBUG") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _atc_summary(atc: Any) -> dict[str, Any]:
    if not isinstance(atc, dict):
        return {"present": False}
    parent = str(atc.get("parent_alltrails_url") or "").strip()
    hint = str(atc.get("unverified_url_hint") or "").strip()
    return {
        "present": True,
        "trail_name": atc.get("trail_name"),
        "verification_status": atc.get("verification_status"),
        "verified": atc.get("verified"),
        "has_parent_url": bool(parent),
        "parent_alltrails_url": parent[:200] if parent else None,
        "has_unverified_hint": bool(hint),
        "search_query": (str(atc.get("search_query") or "")[:200] or None),
    }


def _llm_atc_snapshot(raw_wrapper: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw_wrapper, dict):
        return {"wrapper_present": False}
    raw_atc = raw_wrapper.get("alltrails_search_context")
    out: dict[str, Any] = {
        "wrapper_present": True,
        "top_level_keys": sorted(str(k) for k in raw_wrapper.keys()),
        "llm_alltrails_object": isinstance(raw_atc, dict),
    }
    if isinstance(raw_atc, dict):
        out["llm_alltrails_keys"] = sorted(str(k) for k in raw_atc.keys())
        out["llm_trail_name"] = raw_atc.get("trail_name")
        out["llm_region"] = raw_atc.get("region")
        out["llm_park_name"] = raw_atc.get("park_name")
        parent = str(raw_atc.get("parent_alltrails_url") or raw_atc.get("alltrails_url") or "").strip()
        out["llm_parent_url_in_payload"] = bool(parent)
        if parent:
            out["llm_parent_url"] = parent[:200]
    return out


def remember_llm_wrapper(dest_id: str, raw_wrapper: dict[str, Any] | None) -> None:
    did = (dest_id or "").strip()
    if not did or not isinstance(raw_wrapper, dict):
        return
    _last_llm_wrapper_by_dest[did] = raw_wrapper


def pop_llm_wrapper(dest_id: str) -> dict[str, Any] | None:
    did = (dest_id or "").strip()
    if not did:
        return None
    raw = _last_llm_wrapper_by_dest.pop(did, None)
    return raw if isinstance(raw, dict) else None


def log_alltrails_llm_wrapper(
    *,
    rid: str,
    dest_id: str,
    raw_wrapper: dict[str, Any] | None,
) -> None:
    """Log what the itinerary LLM JSON wrapper contained for AllTrails (before derive/verify)."""
    remember_llm_wrapper(dest_id, raw_wrapper)
    snap = _llm_atc_snapshot(raw_wrapper)
    if not snap.get("wrapper_present"):
        log_itinerary_build(f"ALLTRAILS_LLM rid={rid} dest_id={dest_id!r} wrapper_present=0")
        return
    log_itinerary_build(
        f"ALLTRAILS_LLM rid={rid} dest_id={dest_id!r} wrapper_present=1 "
        f"top_level_keys={snap.get('top_level_keys')!r} "
        f"llm_alltrails_object={1 if snap.get('llm_alltrails_object') else 0} "
        f"llm_parent_url={1 if snap.get('llm_parent_url_in_payload') else 0} "
        f"llm_trail_name={snap.get('llm_trail_name')!r}"
    )


def log_alltrails_patch_session(
    *,
    session_id: str,
    dest_id: str,
    alltrails_search_context: dict[str, Any] | None,
) -> None:
    """Log whether server RAM patch received ``alltrails_search_context``."""
    sid = (session_id or "").strip()[:12]
    did = (dest_id or "").strip() or "?"
    if not isinstance(alltrails_search_context, dict) or not alltrails_search_context.get("trail_name"):
        log_itinerary_build(f"ALLTRAILS_PATCH sid={sid!r} dest_id={did!r} atc_written=0")
        return
    parent = str(alltrails_search_context.get("parent_alltrails_url") or "").strip()
    log_itinerary_build(
        f"ALLTRAILS_PATCH sid={sid!r} dest_id={did!r} atc_written=1 "
        f"verification_status={str(alltrails_search_context.get('verification_status') or '')!r} "
        f"has_parent_url={1 if parent else 0}"
    )


def log_alltrails_sync_ui(
    *,
    trip_id: str | None,
    destinations: list[Any],
) -> None:
    """Log AllTrails fields on destination rows the client POSTed to ``sync_ui``."""
    tid = (trip_id or "").strip()[:24] or "?"
    rows: list[str] = []
    for raw in destinations:
        if not isinstance(raw, dict):
            continue
        did = str(raw.get("id") or "").strip() or "?"
        atc = raw.get("alltrails_search_context")
        if isinstance(atc, dict) and atc.get("trail_name"):
            parent = str(atc.get("parent_alltrails_url") or "").strip()
            rows.append(
                f"{did}:status={atc.get('verification_status')!r},parent={1 if parent else 0}"
            )
        else:
            rows.append(f"{did}:missing")
    sample = "; ".join(rows[:8])
    if len(rows) > 8:
        sample += f"; …+{len(rows) - 8}"
    log_itinerary_build(f"ALLTRAILS_SYNC_UI trip_id={tid!r} cards={len(rows)} {sample}")


def write_alltrails_build_dump(
    *,
    rid: str,
    dest_id: str,
    raw_wrapper: dict[str, Any] | None,
    atc_after_verify: dict[str, Any] | None,
    included_in_http: bool | None = None,
    http_channel: str | None = None,
) -> Path | None:
    """
    Write ``.cache/_itinerary_build_debug/<dest_id>_latest.json`` when ``WANDERLY_ALLTRAILS_DEBUG=1``.
    """
    if not alltrails_build_debug_enabled():
        return None
    did = (dest_id or "").strip() or "unknown"
    doc: dict[str, Any] = {
        "written_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "request_id": rid,
        "destination_id": did,
        "llm_wrapper": _llm_atc_snapshot(raw_wrapper),
        "after_verify": _atc_summary(atc_after_verify),
        "http": {
            "channel": http_channel,
            "included_in_response": included_in_http,
        },
    }
    if isinstance(raw_wrapper, dict) and isinstance(raw_wrapper.get("alltrails_search_context"), dict):
        doc["llm_alltrails_search_context_raw"] = raw_wrapper.get("alltrails_search_context")
    try:
        _DUMP_DIR.mkdir(parents=True, exist_ok=True)
        path = _DUMP_DIR / f"{did}_latest.json"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        log_itinerary_build(f"ALLTRAILS_DUMP dest_id={did!r} path={path!r}")
        return path
    except OSError as exc:
        log_itinerary_build(f"ALLTRAILS_DUMP_ERROR dest_id={did!r} error={exc!r}")
        return None
