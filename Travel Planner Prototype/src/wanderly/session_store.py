from __future__ import annotations

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
