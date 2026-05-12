from __future__ import annotations

import json
import threading
from typing import Any

_lock = threading.Lock()
_sessions: dict[str, dict[str, Any]] = {}


def get_session(session_id: str) -> dict[str, Any]:
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = {
                "chat": [],
                "last_destinations": [],
                "mode": "idle",
            }
        return _sessions[session_id]


def append_chat(session_id: str, role: str, text: str) -> None:
    s = get_session(session_id)
    s["chat"].append({"role": role, "text": text})


def set_last_destinations(session_id: str, destinations: list[dict[str, Any]]) -> None:
    s = get_session(session_id)
    s["last_destinations"] = destinations


def set_last_profile_pills(session_id: str, pills: list[dict[str, Any]]) -> None:
    s = get_session(session_id)
    s["profile_pills"] = [x for x in pills if isinstance(x, dict)]


def snapshot_profile_pills(session_id: str) -> list[dict[str, Any]]:
    s = get_session(session_id)
    raw = s.get("profile_pills")
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def chat_context_snippet(session_id: str, max_messages: int = 6, *, max_chars: int = 4500) -> str:
    """
    Recent chat for advisor / refine context. Drops consecutive duplicate lines (model retries),
    then trims to ``max_chars`` from the start when oversized (keeps most recent by trimming oldest).
    """
    s = get_session(session_id)
    lines: list[str] = []
    for m in s["chat"][-max_messages:]:
        role = str(m.get("role") or "?")
        text = str(m.get("text") or "").strip()
        if not text:
            continue
        seg = f"{role}: {text}"
        if lines and lines[-1] == seg:
            continue
        lines.append(seg)
    out = "\n".join(lines)
    if not out:
        return "(no prior turns)"
    cap = max(512, int(max_chars))
    if len(out) <= cap:
        return out
    # Prioritize recent turns: keep suffix within budget (prior_chat most often ends with user ask).
    return out[-cap:]


def set_booking_hotels_plan(session_id: str, destination_id: str, hotels: list[dict[str, Any]]) -> None:
    """Cache multi-stay rows from ``booking_search_prepare`` for subsequent ``booking_search_step`` calls."""
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return
    s = get_session(sid)
    plans = s.setdefault("booking_hotels_plans", {})
    if isinstance(plans, dict):
        plans[did] = [x for x in hotels if isinstance(x, dict)]


def get_booking_hotels_plan(session_id: str, destination_id: str) -> list[dict[str, Any]] | None:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return None
    s = get_session(sid)
    plans = s.get("booking_hotels_plans")
    if not isinstance(plans, dict):
        return None
    raw = plans.get(did)
    return raw if isinstance(raw, list) else None


def set_booking_search_intent(session_id: str, destination_id: str, intent: dict[str, Any]) -> None:
    """Cache LLM-derived booking URL intent params from prepare for step reuse."""
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did or not isinstance(intent, dict):
        return
    s = get_session(sid)
    intents = s.setdefault("booking_search_intents", {})
    if isinstance(intents, dict):
        intents[did] = dict(intent)


def get_booking_search_intent(session_id: str, destination_id: str) -> dict[str, Any] | None:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return None
    s = get_session(sid)
    intents = s.get("booking_search_intents")
    if not isinstance(intents, dict):
        return None
    raw = intents.get(did)
    return raw if isinstance(raw, dict) else None


def bump_booking_prefetch_generation(session_id: str, destination_id: str) -> int:
    """
    Increment per-destination prefetch generation so in-flight background Playwright fetches
    can detect superseded runs and skip writes.
    """
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return 0
    with _lock:
        if sid not in _sessions:
            _sessions[sid] = {
                "chat": [],
                "last_destinations": [],
                "mode": "idle",
            }
        s = _sessions[sid]
        gens = s.setdefault("booking_prefetch_generations", {})
        if not isinstance(gens, dict):
            gens = {}
            s["booking_prefetch_generations"] = gens
        cur = int(gens.get(did, 0)) + 1
        gens[did] = cur
        return cur


def get_booking_prefetch_generation(session_id: str, destination_id: str) -> int:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return 0
    with _lock:
        s = _sessions.get(sid)
        if not s:
            return 0
        gens = s.get("booking_prefetch_generations")
        if not isinstance(gens, dict):
            return 0
        return int(gens.get(did, 0))


def clear_booking_prepare_caches(session_id: str, destination_id: str) -> None:
    """
    Invalidate everything ``booking_search_prepare`` owns for one destination:
    cached hotel rows, search-intent JSON, criteria snapshot for steps, prefetched HTML,
    and the short-lived duplicate-prepare response cache.
    """
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return
    bump_booking_prefetch_generation(sid, did)
    s = get_session(sid)
    bp = s.setdefault("booking_hotels_plans", {})
    if isinstance(bp, dict):
        bp.pop(did, None)
    bi = s.setdefault("booking_search_intents", {})
    if isinstance(bi, dict):
        bi.pop(did, None)
    cs = s.setdefault("booking_criteria_snapshots", {})
    if isinstance(cs, dict):
        cs.pop(did, None)
    hp = s.setdefault("booking_html_prefetch", {})
    if isinstance(hp, dict):
        hp.pop(did, None)
    pc = s.setdefault("booking_prepare_response_cache", {})
    if isinstance(pc, dict):
        pc.pop(did, None)


def get_booking_prepare_response_cache(
    session_id: str, destination_id: str, fingerprint: str
) -> dict[str, Any] | None:
    """Return last ``booking_search_prepare`` JSON when ``fingerprint`` still matches (Strict Mode dedupe)."""
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    fp = (fingerprint or "").strip()
    if not sid or not did or not fp:
        return None
    s = get_session(sid)
    box = s.get("booking_prepare_response_cache")
    if not isinstance(box, dict):
        return None
    cell = box.get(did)
    if not isinstance(cell, dict):
        return None
    if str(cell.get("fingerprint") or "") != fp:
        return None
    raw = cell.get("payload")
    return dict(raw) if isinstance(raw, dict) else None


def set_booking_prepare_response_cache(
    session_id: str, destination_id: str, fingerprint: str, payload: dict[str, Any]
) -> None:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    fp = (fingerprint or "").strip()
    if not sid or not did or not fp or not isinstance(payload, dict):
        return
    s = get_session(sid)
    box = s.setdefault("booking_prepare_response_cache", {})
    if isinstance(box, dict):
        box[did] = {"fingerprint": fp, "payload": dict(payload)}


def set_booking_criteria_snapshot(session_id: str, destination_id: str, payload: dict[str, Any]) -> None:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did or not isinstance(payload, dict):
        return
    s = get_session(sid)
    box = s.setdefault("booking_criteria_snapshots", {})
    if isinstance(box, dict):
        box[did] = dict(payload)


def get_booking_criteria_snapshot(session_id: str, destination_id: str) -> dict[str, Any] | None:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return None
    s = get_session(sid)
    box = s.get("booking_criteria_snapshots")
    if not isinstance(box, dict):
        return None
    raw = box.get(did)
    return raw if isinstance(raw, dict) else None


def clear_booking_html_prefetch(session_id: str, destination_id: str) -> None:
    """Drop only Playwright HTML prefetches (e.g. before a fresh ``build_booking_criteria``)."""
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return
    bump_booking_prefetch_generation(sid, did)
    s = get_session(sid)
    hp = s.setdefault("booking_html_prefetch", {})
    if isinstance(hp, dict):
        hp.pop(did, None)


def set_booking_prefetch_html(
    session_id: str,
    destination_id: str,
    cache_key: str,
    html: str,
    *,
    expected_generation: int | None = None,
) -> None:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    key = (cache_key or "").strip()
    if not sid or not did or not key:
        return
    if expected_generation is not None and get_booking_prefetch_generation(sid, did) != int(expected_generation):
        return
    s = get_session(sid)
    outer = s.setdefault("booking_html_prefetch", {})
    if not isinstance(outer, dict):
        outer = {}
        s["booking_html_prefetch"] = outer
    inner = outer.setdefault(did, {})
    if isinstance(inner, dict):
        inner[key] = html


def pop_booking_prefetch_html(session_id: str, destination_id: str, cache_key: str) -> str | None:
    """Remove and return one prefetched HTML blob, if present."""
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    key = (cache_key or "").strip()
    if not sid or not did or not key:
        return None
    s = get_session(sid)
    outer = s.get("booking_html_prefetch")
    if not isinstance(outer, dict):
        return None
    inner = outer.get(did)
    if not isinstance(inner, dict):
        return None
    raw = inner.pop(key, None)
    if not inner:
        outer.pop(did, None)
    if isinstance(raw, str) and raw:
        return raw
    return None


def date_hints_fingerprint(date_hints: dict[str, Any] | None) -> str:
    """Stable string for matching prepare vs step ``date_hints`` payloads."""
    if not date_hints:
        return "{}"
    try:
        return json.dumps(date_hints, sort_keys=True, default=str)
    except TypeError:
        return repr(date_hints)


def snapshot_destinations_for_model(session_id: str) -> list[dict[str, Any]]:
    """Prefer client-synced cards (pin/dismiss) for refine context; else last model list."""
    s = get_session(session_id)
    ui = s.get("ui_destinations")
    if isinstance(ui, list) and len(ui) > 0:
        return [x for x in ui if isinstance(x, dict)]
    last = s.get("last_destinations") or []
    return [x for x in last if isinstance(x, dict)]


def cursor_lane_key(model_id_override: str | None) -> str:
    """Stable key for Cursor Cloud Agent reuse (one agent per lane per Wanderly session)."""
    o = (model_id_override or "").strip()
    return "planner" if not o else f"m:{o}"


def get_cursor_agent_id(session_id: str, lane: str) -> str | None:
    with _lock:
        s = _sessions.get(session_id)
        if not s:
            return None
        agents = s.get("cursor_agents")
        if not isinstance(agents, dict):
            return None
        aid = agents.get(lane)
        if aid is None:
            return None
        out = str(aid).strip()
        return out or None


def set_cursor_agent_id(session_id: str, lane: str, agent_id: str) -> None:
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = {
                "chat": [],
                "last_destinations": [],
                "mode": "idle",
            }
        s = _sessions[session_id]
        if "cursor_agents" not in s or not isinstance(s.get("cursor_agents"), dict):
            s["cursor_agents"] = {}
        s["cursor_agents"][lane] = agent_id


def clear_cursor_agent_id(session_id: str, lane: str) -> None:
    with _lock:
        s = _sessions.get(session_id)
        if not s:
            return
        agents = s.get("cursor_agents")
        if isinstance(agents, dict) and lane in agents:
            del agents[lane]


def cursor_run_persist_kwargs(
    session_id: str, model_id_override: str | None, *, trace_path: str | None = None
) -> dict[str, Any]:
    """
    Optional kwargs for ``run_prompt_collect_assistant_text*`` when
    ``cursor_reuse_agents_enabled()`` and session_id are set.

    ``trace_path`` is copied into ``cursor_trace_path`` for ``.cache/itinerary_debug.log`` lines
    (which Wanderly code path hit Cursor / 409).

    ``cursor_run_lock_key`` serializes Cursor HTTP for ``session_id`` + persistence lane (avoids 409 when
    two HTTP handlers start runs concurrently).
    """
    from src.integrations.cursor_agent_client import cursor_reuse_agents_enabled

    sid = (session_id or "").strip()
    lane = cursor_lane_key(model_id_override)
    if not sid or not cursor_reuse_agents_enabled():
        tp0 = (trace_path or "").strip()
        out: dict[str, Any] = {}
        if tp0:
            out["cursor_trace_path"] = tp0
        if sid:
            out["cursor_run_lock_key"] = f"{sid}\t{lane}"
        return out
    existing = get_cursor_agent_id(sid, lane)
    tp = (trace_path or "").strip() or f"wanderly.persist:{lane}"

    def on_new(aid: str, _sid: str = sid, _lane: str = lane) -> None:
        set_cursor_agent_id(_sid, _lane, aid)

    def on_clear(_sid: str = sid, _lane: str = lane) -> None:
        clear_cursor_agent_id(_sid, _lane)

    return {
        "existing_agent_id": existing,
        "on_new_agent": on_new,
        "on_agent_cleared": on_clear,
        "cursor_trace_path": tp,
        "cursor_run_lock_key": f"{sid}\t{lane}",
    }


def cursor_run_persist_kwargs_for_lane(
    session_id: str, lane: str, *, trace_path: str | None = None
) -> dict[str, Any]:
    """
    Like ``cursor_run_persist_kwargs`` but uses an explicit persistence lane key
    (e.g. ``itinerary_lazy``) so callers do not share a Cloud Agent with unrelated flows.

    ``trace_path`` → ``cursor_trace_path`` for log correlation (see ``cursor_run_persist_kwargs``).

    ``cursor_run_lock_key`` — same serialization contract as ``cursor_run_persist_kwargs``.
    """
    from src.integrations.cursor_agent_client import cursor_reuse_agents_enabled

    sid = (session_id or "").strip()
    lane_k = (lane or "").strip() or "default"
    if not sid or not cursor_reuse_agents_enabled():
        tp0 = (trace_path or "").strip()
        out: dict[str, Any] = {}
        if tp0:
            out["cursor_trace_path"] = tp0
        if sid:
            out["cursor_run_lock_key"] = f"{sid}\t{lane_k}"
        return out
    existing = get_cursor_agent_id(sid, lane_k)
    tp = (trace_path or "").strip() or f"wanderly.persist_lane:{lane_k}"

    def on_new(aid: str, _sid: str = sid, _lk: str = lane_k) -> None:
        set_cursor_agent_id(_sid, _lk, aid)

    def on_clear(_sid: str = sid, _lk: str = lane_k) -> None:
        clear_cursor_agent_id(_sid, _lk)

    return {
        "existing_agent_id": existing,
        "on_new_agent": on_new,
        "on_agent_cleared": on_clear,
        "cursor_trace_path": tp,
        "cursor_run_lock_key": f"{sid}\t{lane_k}",
    }
