"""Lazy day-by-day itinerary for a single destination (Cursor JSON → UI ItinDay shape)."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from typing import Any

from src.integrations.cursor_agent_client import (
    basic_client,
    get_api_key,
    load_env_from_roots,
    run_prompt_collect_assistant_text,
    run_prompt_collect_assistant_text_streaming,
)
from src.wanderly.itinerary_debug_log import itinerary_debug_verbose, log_itinerary_build
from src.wanderly.session_store import cursor_run_persist_kwargs_for_lane, get_cursor_agent_id

_ITINERARY_PROMPT = """
You are Wanderly. Build a day-by-day trip outline for ONE destination.
Return ONLY a JSON array (no markdown, no prose). Each element must be:
{"day": <int 1..N>, "label": "Short title → optional area", "items": ["3-5 concrete bullets"], "accent": "emerald|blue|purple|amber"}

Rules:
- Exactly N days where N is given below (integer).
- day runs 1..N inclusive; last day is travel-out / fly home style.
- items are short strings (activities, meals, transfers) — no prices, no booking claims.
- Use the traveler profile to pace the trip (e.g. fewer hikes if pace is relaxed).
- Optional accent on 1-2 highlight days only; omit accent on most days.

N (number of days):
""".strip()


def _extract_json_array(text: str) -> list[dict[str, Any]] | None:
    """
    Parse the first JSON array in ``text``.

    Avoid greedy ``[...]`` regex: model output often includes ``]`` inside strings or
    prose after the array, which breaks ``re.search(r'\\[[\\s\\S]*\\]')`` and yields
    ``extracted_len=0`` despite a valid array (see itinerary_debug EMPTY logs).
    """
    t = text.strip()
    if not t:
        return None
    # Strip a single leading ```json ... ``` fence if present
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE).strip()
        if "```" in t:
            t = t[: t.rfind("```")].strip()

    dec = json.JSONDecoder()
    try:
        out, _ = dec.raw_decode(t)
        return out if isinstance(out, list) else None
    except json.JSONDecodeError:
        pass

    for i, ch in enumerate(t):
        if ch != "[":
            continue
        try:
            out, _end = dec.raw_decode(t, i)
            if isinstance(out, list):
                return out
        except json.JSONDecodeError:
            continue
    return None


def _normalize_itinerary_rows(raw: list[Any], *, max_days: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    allowed_accents = {"emerald", "blue", "purple", "amber"}
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            day = int(row.get("day"))
        except (TypeError, ValueError):
            continue
        if day < 1 or day > max_days:
            continue
        label = str(row.get("label") or f"Day {day}")[:120]
        items_in = row.get("items")
        items: list[str] = []
        if isinstance(items_in, list):
            for it in items_in[:8]:
                if isinstance(it, str) and it.strip():
                    items.append(it.strip()[:200])
                elif isinstance(it, dict):
                    s = str(
                        it.get("text")
                        or it.get("item")
                        or it.get("title")
                        or it.get("label")
                        or it.get("description")
                        or "",
                    ).strip()
                    if s:
                        items.append(s[:200])
        if len(items) < 1:
            continue
        if len(items) == 1:
            items.append("Flexible time — explore or rest")
        entry: dict[str, Any] = {"day": day, "label": label, "items": items}
        ac = str(row.get("accent") or "").strip().lower()
        if ac in allowed_accents:
            entry["accent"] = ac
        out.append(entry)
    out.sort(key=lambda x: int(x["day"]))
    # de-dupe by day (keep first)
    seen: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for e in out:
        d = int(e["day"])
        if d in seen:
            continue
        seen.add(d)
        deduped.append(e)
    return deduped[:max_days]


def _itinerary_cursor_lane(light: str | None) -> str:
    """
    Persistence lane for lazy itinerary only — must NOT reuse ``planner`` (smoke/refine)
    or follow-up runs see huge JSON/chat context and rarely return a bare JSON array.
    """
    o = (light or "").strip()
    return "itinerary_lazy" if not o else f"itinerary_lazy:m:{o}"


def _itin_reuse_hint(session_id: str | None, light: str | None) -> str:
    sid = (session_id or "").strip()
    if not sid:
        return "reuse=no_session"
    lane = _itinerary_cursor_lane(light)
    aid = get_cursor_agent_id(sid, lane)
    if aid:
        tail = aid[-8:] if len(aid) > 8 else aid
        return f"reuse_lane={lane} existing_agent=…{tail}"
    return f"reuse_lane={lane} existing_agent=(none)"


def _itinerary_prompt_and_days(
    *,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    days: int,
) -> tuple[str, int]:
    n = max(3, min(int(days), 14))
    ctx = json.dumps(
        {
            "destination": {
                "id": destination.get("id"),
                "name": destination.get("name"),
                "country": destination.get("country"),
                "hook": destination.get("hook"),
                "vibe": destination.get("vibe"),
            },
            "profile_pills": profile_pills[:24],
        },
        ensure_ascii=True,
    )
    prompt = _ITINERARY_PROMPT + f" {n}\n\nContext JSON:\n{ctx}"
    return prompt, n


def build_itinerary_for_destination(
    *,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    days: int,
    session_id: str | None = None,
    request_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Call Cursor once; return list of ItinDay-shaped dicts for the client.
    """
    rid = (request_id or "?").strip() or "?"
    did = str(destination.get("id") or "")
    load_env_from_roots()
    key = get_api_key()
    prompt, n = _itinerary_prompt_and_days(destination=destination, profile_pills=profile_pills, days=days)
    light = (os.environ.get("CURSOR_LIGHT_MODEL_FOR_ITINERARY") or os.environ.get("CURSOR_LIGHT_MODEL_FOR_IMAGES") or "").strip() or None
    log_itinerary_build(
        f"BEGIN rid={rid} mode=json sid={(session_id or '')[:12]!r} dest_id={did!r} days={days} n={n} "
        f"light_model={light!r} {_itin_reuse_hint(session_id, light)}"
    )
    t0 = time.perf_counter()
    raw = ""
    try:
        with basic_client(key) as client:
            raw = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=light,
                **cursor_run_persist_kwargs_for_lane(session_id or "", _itinerary_cursor_lane(light)),
            )
    except Exception as e:
        log_itinerary_build(
            f"FAIL rid={rid} mode=json dest_id={did!r} exc={type(e).__name__}: {e!s} "
            f"elapsed_ms={(time.perf_counter() - t0) * 1000.0:.1f}"
        )
        raise
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    extracted = _extract_json_array(raw) or []
    out = _normalize_itinerary_rows(extracted, max_days=n)
    log_itinerary_build(
        f"END rid={rid} mode=json dest_id={did!r} raw_chars={len(raw or '')} extracted_len={len(extracted)} "
        f"normalized_len={len(out)} elapsed_ms={elapsed_ms:.1f}"
    )
    if not out:
        tail = (raw or "").replace("\n", " ").strip()[:500]
        log_itinerary_build(f"EMPTY rid={rid} mode=json extracted_len={len(extracted)} raw_snippet={tail!r}")
    if not out and itinerary_debug_verbose():
        snippet = (raw or "").strip().replace("\r", "")[:2000]
        log_itinerary_build(f"DEBUG_EMPTY rid={rid} raw_head={snippet!r}")
    return out


def build_itinerary_for_destination_streaming(
    *,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    days: int,
    emit: Callable[[dict[str, Any]], None] | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Same as ``build_itinerary_for_destination`` but streams Cursor SSE.
    Forwards thinking/status/tool events to ``emit``; never forwards ``assistant_delta``
    so clients can show chain-of-thought without streaming raw JSON.
    """
    rid = (request_id or "?").strip() or "?"
    did = str(destination.get("id") or "")
    load_env_from_roots()
    key = get_api_key()
    prompt, n = _itinerary_prompt_and_days(destination=destination, profile_pills=profile_pills, days=days)
    light = (os.environ.get("CURSOR_LIGHT_MODEL_FOR_ITINERARY") or os.environ.get("CURSOR_LIGHT_MODEL_FOR_IMAGES") or "").strip() or None

    def _safe_emit(ev: dict[str, Any]) -> None:
        if emit is None:
            return
        if ev.get("type") == "assistant_delta":
            return
        emit(ev)

    log_itinerary_build(
        f"BEGIN rid={rid} mode=sse sid={(session_id or '')[:12]!r} dest_id={did!r} days={days} n={n} "
        f"light_model={light!r} {_itin_reuse_hint(session_id, light)}"
    )
    t0 = time.perf_counter()
    raw = ""
    try:
        with basic_client(key) as client:
            raw = run_prompt_collect_assistant_text_streaming(
                client,
                prompt,
                emit=_safe_emit if emit else None,
                model_id_override=light,
                **cursor_run_persist_kwargs_for_lane(session_id or "", _itinerary_cursor_lane(light)),
            )
    except Exception as e:
        log_itinerary_build(
            f"FAIL rid={rid} mode=sse dest_id={did!r} exc={type(e).__name__}: {e!s} "
            f"elapsed_ms={(time.perf_counter() - t0) * 1000.0:.1f}"
        )
        raise
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    extracted = _extract_json_array(raw) or []
    out = _normalize_itinerary_rows(extracted, max_days=n)
    log_itinerary_build(
        f"END rid={rid} mode=sse dest_id={did!r} raw_chars={len(raw or '')} extracted_len={len(extracted)} "
        f"normalized_len={len(out)} elapsed_ms={elapsed_ms:.1f}"
    )
    if not out:
        tail = (raw or "").replace("\n", " ").strip()[:500]
        log_itinerary_build(f"EMPTY rid={rid} mode=sse extracted_len={len(extracted)} raw_snippet={tail!r}")
    if not out and itinerary_debug_verbose():
        snippet = (raw or "").strip().replace("\r", "")[:2000]
        log_itinerary_build(f"DEBUG_EMPTY rid={rid} raw_head={snippet!r}")
    return out


def default_trip_days_from_pills(profile_pills: list[dict[str, Any]]) -> int:
    """Rough day count from duration / when pills."""
    for p in profile_pills:
        if not isinstance(p, dict):
            continue
        if str(p.get("key") or "") != "duration":
            continue
        v = str(p.get("value") or "").lower()
        if "2+ week" in v or "14" in v or "10-14" in v:
            return 10
        if "week" in v and "2" in v:
            return 10
        if "7" in v or "week" in v:
            return 7
        if "3-4" in v or "4" in v:
            return 4
        if "5" in v:
            return 5
    return int(os.environ.get("WANDERLY_DEFAULT_ITINERARY_DAYS", "6") or "6")
