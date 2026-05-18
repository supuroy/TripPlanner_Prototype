"""
Structured telemetry for Wanderly Cursor LLM calls (non-booking).

Writes JSONL to ``Travel Planner Prototype/.cache/llm_telemetry.jsonl`` with paired
``start`` / ``end`` events: timestamps, duration, prompt sizes, and optional metadata.

Activities (top-level):
  - ``itinerary_builder`` — smoke/planner turn, per-destination lazy itinerary
  - ``itinerary_refinement`` — workspace refine
  - ``destination_followup`` — card chat (advisor_feed), orchestrate intent
  - ``advisor`` — plan review, profile change, profile listener

Enable/disable: ``WANDERLY_LLM_TELEMETRY`` (default on). Set ``0``/``false`` to disable.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
_LOG_FILE = _LOG_DIR / "llm_telemetry.jsonl"

ACTIVITY_ITINERARY_BUILDER = "itinerary_builder"
ACTIVITY_ITINERARY_REFINEMENT = "itinerary_refinement"
ACTIVITY_DESTINATION_FOLLOWUP = "destination_followup"
ACTIVITY_ADVISOR = "advisor"


def llm_telemetry_enabled() -> bool:
    v = (os.environ.get("WANDERLY_LLM_TELEMETRY") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def telemetry_log_path() -> Path:
    return _LOG_FILE


def telemetry_meta(
    *,
    session_id: str | None = None,
    destination_id: str | None = None,
    request_id: str | None = None,
    focus_destination_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Compact metadata attached to each telemetry record."""
    out: dict[str, Any] = {}
    if session_id:
        out["session_id"] = (session_id or "")[:16]
    if destination_id:
        out["destination_id"] = (destination_id or "")[:48]
    if request_id:
        out["request_id"] = (request_id or "")[:24]
    if focus_destination_id:
        out["focus_destination_id"] = (focus_destination_id or "")[:48]
    for k, v in extra.items():
        if v is not None and v != "":
            out[k] = v
    return out


def prompt_size_fields(
    prompt_text: str,
    *,
    persistent_instruction_prefix: str | None = None,
) -> dict[str, int]:
    p = len(prompt_text or "")
    pref = len((persistent_instruction_prefix or "").strip())
    return {
        "prompt_chars": p,
        "prefix_chars": pref,
        "total_input_chars": p + pref,
    }


def _utc_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond // 1000:03d}Z"


def _write_record(record: dict[str, Any]) -> None:
    if not llm_telemetry_enabled():
        return
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    print(f"[llm_telemetry] {line}", flush=True)
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        from src.wanderly.itinerary_debug_log import log_itinerary_build

        ev = record.get("event")
        act = record.get("activity", "?")
        sub = record.get("sub_activity", "?")
        if ev == "start":
            log_itinerary_build(
                f"LLM_TELEMETRY start activity={act} sub={sub} "
                f"span={record.get('span_id', '')[:8]} "
                f"total_input_chars={record.get('total_input_chars', 0)}"
            )
        elif ev == "end":
            log_itinerary_build(
                f"LLM_TELEMETRY end activity={act} sub={sub} "
                f"span={record.get('span_id', '')[:8]} "
                f"status={record.get('status', '?')} "
                f"duration_ms={record.get('duration_ms', 0):.1f} "
                f"total_input_chars={record.get('total_input_chars', 0)} "
                f"response_chars={record.get('response_chars', 0)}"
            )
    except Exception:
        pass


def record_llm_start(
    *,
    activity: str,
    sub_activity: str,
    prompt_text: str,
    persistent_instruction_prefix: str | None = None,
    stream: bool = False,
    model_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Log start; returns ``span_id`` for ``record_llm_end`` (empty when telemetry disabled)."""
    if not llm_telemetry_enabled():
        return ""
    span_id = uuid.uuid4().hex[:16]
    sizes = prompt_size_fields(prompt_text, persistent_instruction_prefix=persistent_instruction_prefix)
    rec: dict[str, Any] = {
        "event": "start",
        "span_id": span_id,
        "ts": _utc_iso(),
        "activity": (activity or "").strip() or "unknown",
        "sub_activity": (sub_activity or "").strip() or "unknown",
        "stream": bool(stream),
        **sizes,
    }
    if model_id:
        rec["model_id"] = model_id.strip()
    if meta:
        for k, v in meta.items():
            if v is not None and v != "":
                rec[k] = v
    _write_record(rec)
    return span_id


def record_llm_end(
    span_id: str,
    *,
    activity: str,
    sub_activity: str,
    started_at_perf: float,
    prompt_text: str,
    persistent_instruction_prefix: str | None = None,
    stream: bool = False,
    model_id: str | None = None,
    meta: dict[str, Any] | None = None,
    status: str = "ok",
    response_text: str | None = None,
    error: BaseException | None = None,
) -> None:
    if not span_id:
        return
    duration_ms = (time.perf_counter() - started_at_perf) * 1000.0
    sizes = prompt_size_fields(prompt_text, persistent_instruction_prefix=persistent_instruction_prefix)
    rec: dict[str, Any] = {
        "event": "end",
        "span_id": span_id,
        "ts": _utc_iso(),
        "activity": (activity or "").strip() or "unknown",
        "sub_activity": (sub_activity or "").strip() or "unknown",
        "stream": bool(stream),
        "duration_ms": round(duration_ms, 2),
        "status": (status or "ok").strip().lower(),
        "response_chars": len(response_text or ""),
        **sizes,
    }
    if model_id:
        rec["model_id"] = model_id.strip()
    if meta:
        for k, v in meta.items():
            if v is not None and v != "":
                rec[k] = v
    if error is not None:
        rec["error_type"] = type(error).__name__
        rec["error_message"] = str(error)[:500]
    _write_record(rec)


@contextmanager
def llm_telemetry_span(
    *,
    activity: str,
    sub_activity: str,
    prompt_text: str,
    persistent_instruction_prefix: str | None = None,
    stream: bool = False,
    model_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """
    Context manager for LLM calls outside ``cursor_agent_client``.

    Yields a mutable dict; set ``ctx["response_text"]`` before exit for response size.
    """
    ctx: dict[str, Any] = {"response_text": ""}
    if not llm_telemetry_enabled():
        yield ctx
        return
    span_id = record_llm_start(
        activity=activity,
        sub_activity=sub_activity,
        prompt_text=prompt_text,
        persistent_instruction_prefix=persistent_instruction_prefix,
        stream=stream,
        model_id=model_id,
        meta=meta,
    )
    t0 = time.perf_counter()
    err: BaseException | None = None
    try:
        yield ctx
    except BaseException as e:
        err = e
        raise
    finally:
        record_llm_end(
            span_id,
            activity=activity,
            sub_activity=sub_activity,
            started_at_perf=t0,
            prompt_text=prompt_text,
            persistent_instruction_prefix=persistent_instruction_prefix,
            stream=stream,
            model_id=model_id,
            meta=meta,
            status="error" if err else "ok",
            response_text=str(ctx.get("response_text") or ""),
            error=err,
        )
