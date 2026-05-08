from __future__ import annotations

import concurrent.futures
import json
import re
import threading
from typing import Any, Callable
from urllib.parse import quote

from pydantic import ValidationError

from src.integrations.cursor_agent_client import (
    basic_client,
    get_api_key,
    load_env_from_roots,
    run_prompt_collect_assistant_text,
    run_prompt_collect_assistant_text_streaming,
)
from src.wanderly.cursor_advisor import (
    advisor_model_id,
    merge_profile_pills_advisor_precedence,
    run_profile_listener_cursor,
)
from src.wanderly.refinement_cursor import refinement_model_id
from src.wanderly.schemas import SmokeDestDTO, SmokeProfilePillDTO, WanderlyWorkspaceDTO, coerce_profile_pill
from src.wanderly.session_store import (
    append_chat,
    chat_context_snippet,
    cursor_run_persist_kwargs,
    set_last_destinations,
    set_last_profile_pills,
    snapshot_destinations_for_model,
    snapshot_profile_pills,
)


def _refine_model_persist_kwargs(session_id: str, *, trace_path: str) -> tuple[str | None, dict[str, Any]]:
    """Main refine JSON: optional mid-tier ``CURSOR_REFINEMENT_MODEL_ID``, else full planner."""
    rmid = refinement_model_id()
    if rmid:
        return rmid, cursor_run_persist_kwargs(session_id, rmid, trace_path=trace_path)
    return None, cursor_run_persist_kwargs(session_id, None, trace_path=trace_path)

_JSON_INSTRUCTIONS = """
You are Wanderly's planner. Reply with ONE JSON object only (no markdown fences, no prose outside JSON).
Shape:
{
  "assistant_markdown": "markdown summary for the traveler",
  "profile_pills": [
    {"key": "when", "label": "When", "value": "short", "icon": "📅", "editable": true},
    {"key": "who", "label": "Who", "value": "short", "icon": "👥", "editable": true},
    {"key": "from", "label": "Flying from", "value": "City or airport", "icon": "✈️", "editable": true},
    {"key": "vibe", "label": "Vibe", "value": "short", "icon": "✨", "editable": true},
    {"key": "budget", "label": "Budget", "value": "short", "icon": "💰", "editable": true},
    {"key": "duration", "label": "Duration", "value": "short", "icon": "⏱", "editable": true}
  ],
  "destinations": [{"id": "slug", "name": "…", "country": "…", "hook": "one line", "score": 0}]
}
Rules:
- 1–5 destinations; scores 0–100 integers.
- profile_pills: exactly those six keys in that order (placeholders ok). Companion pass may refine pills — prioritize assistant_markdown + destinations.
- No ``tags`` pill; extra nuance → pills or assistant_markdown.
- Home airport / "from Seattle" / "SEA" → set key "from".
- Infer from the user message; placeholders if unknown.
- No live fares or availability claims.
""".strip()


_REFINE_INSTRUCTIONS = """
You are Wanderly. The user is refining an existing shortlist. Reply ONLY with JSON
using the SAME shape as before (assistant_markdown, profile_pills, destinations).

Rules:
- workspace_destinations is the live shortlist (includes pinned/dismissed flags from the UI).
- Keep every destination with pinned=true in your destinations array unless the user explicitly
  asks to remove or replace that place (stable id, you may refresh hook/score).
- Do not include destinations with dismissed=true unless the user asks to bring one back.
- For new suggestions, assign new lowercase-slug ids; when editing an existing row, keep its id.
- Always return the full profile_pills array for the six keys when, who, from, vibe, budget, duration (not a partial list; placeholders ok; a fast companion pass may refine).
- Do **not** emit a ``tags`` pill; fold constraints into the appropriate one of the six fields or assistant_markdown.
- Update profile_pills whenever the user states new trip facts (dates, budget, party, vibe, home airport / "flying from …").
- You may omit per-destination "itinerary" in JSON; the UI keeps existing day-by-day plans unless the user asks to change them.

Structured context (JSON) follows:
""".strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    t = text.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    start = t.find("{")
    end = t.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(t[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


_AIRPORT_LABEL: dict[str, str] = {
    "SEA": "Seattle (SEA)",
    "PDX": "Portland (PDX)",
    "SFO": "San Francisco (SFO)",
    "LAX": "Los Angeles (LAX)",
    "SAN": "San Diego (SAN)",
    "DEN": "Denver (DEN)",
    "DFW": "Dallas/Fort Worth (DFW)",
    "IAH": "Houston (IAH)",
    "ORD": "Chicago (ORD)",
    "MSP": "Minneapolis (MSP)",
    "ATL": "Atlanta (ATL)",
    "MIA": "Miami (MIA)",
    "TPA": "Tampa (TPA)",
    "MCO": "Orlando (MCO)",
    "BOS": "Boston (BOS)",
    "JFK": "New York (JFK)",
    "EWR": "Newark (EWR)",
    "LGA": "New York (LGA)",
    "PHL": "Philadelphia (PHL)",
    "DCA": "Washington D.C. (DCA)",
    "IAD": "Washington D.C. (IAD)",
    "LAS": "Las Vegas (LAS)",
    "PHX": "Phoenix (PHX)",
}


def _infer_origin_from_message(msg: str) -> str | None:
    """Best-effort home airport / city from natural language (server-side safety net)."""
    t = (msg or "").strip()
    if not t:
        return None
    for code, label in _AIRPORT_LABEL.items():
        if re.search(rf"\b{re.escape(code)}\b", t, re.I):
            return label
    patterns = [
        r"(?i)flying\s+from\s+([A-Za-z][^.\n,?]{2,52}?)(?:\s*[\.,]|$|\s+and\s+|\s+update\b|\s+we\s+will\b)",
        r"(?i)(?:depart(?:ing|s)|leaving)\s+from\s+([A-Za-z][^.\n,?]{2,52}?)(?:\s*[\.,]|$|\s+and\s+|\s+update\b)",
        r"(?i)out\s+of\s+([A-Za-z][^.\n,?]{2,52}?)(?:\s*[\.,]|$|\s+and\s+|\s+update\b)",
        r"(?i)home\s+airport\s+(?:is\s+)?([A-Za-z][^.\n,?]{2,52}?)(?:\s*[\.,]|$)",
        r"(?i)fly(?:ing)?\s+out\s+of\s+([A-Za-z][^.\n,?]{2,52}?)(?:\s*[\.,]|$|\s+and\s+)",
    ]
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            s = m.group(1).strip()
            if len(s) >= 3:
                return s[:80]
    return None


def _merge_profile_with_inference(dto: WanderlyWorkspaceDTO, user_message: str) -> WanderlyWorkspaceDTO:
    origin = _infer_origin_from_message(user_message)
    if not origin:
        return dto
    pills = list(dto.profile_pills)
    for i, p in enumerate(pills):
        if p.key == "from":
            pills[i] = p.model_copy(update={"value": origin, "label": "Flying from"})
            return dto.model_copy(update={"profile_pills": pills})
    pills.append(
        SmokeProfilePillDTO(
            key="from",
            label="Flying from",
            value=origin,
            icon="✈️",
        )
    )
    return dto.model_copy(update={"profile_pills": pills})


def _minimal_dest_card(d: dict[str, Any]) -> dict[str, Any]:
    name = str(d.get("name") or "Destination")
    did = str(d.get("id") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "dest")[:48]
    country = str(d.get("country") or "")
    hook = str(d.get("hook") or "Suggested destination.")
    score = int(d.get("score") or 85)
    img = f"https://placehold.co/800x500/1a1a24/99aacc?text={quote(name[:24])}"
    return {
        "id": did,
        "name": name,
        "country": country,
        "hook": hook[:220],
        "image": img,
        "images": [img],
        "temp": "n/a",
        "flight": "TBD",
        "cost": "-",
        "vibe": ["Explorer"],
        "score": max(0, min(100, score)),
        "dismissed": False,
        "pinned": False,
        "warnings": [],
        "itinerary": [],
        "itinerary_dirty": True,
        "flights": [
            {"airline": "Placeholder", "route": "TBD", "duration": "TBD", "price": "TBD", "best": True}
        ],
        "hotels": [
            {
                "name": "Placeholder hotel",
                "area": "City",
                "price": "TBD/n",
                "nights": 5,
                "image": img,
                "best": True,
            }
        ],
        "activities": [],
    }


def _is_placeholderish_image(url: object) -> bool:
    u = str(url or "").strip().lower()
    return not u or "placehold.co" in u


def _merge_dest_ui_flags(prior: list[dict[str, Any]], dests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge pin/dismiss from prior, and keep rich media + itinerary when refine JSON only has placeholders."""
    flags: dict[str, dict[str, bool]] = {}
    prior_by_id: dict[str, dict[str, Any]] = {}
    for p in prior:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        flags[pid] = {
            "pinned": bool(p.get("pinned")),
            "dismissed": bool(p.get("dismissed")),
        }
        prior_by_id[pid] = p
    out: list[dict[str, Any]] = []
    for d in dests:
        did = str(d.get("id") or "").strip()
        merged = dict(d)
        if did in flags:
            merged["pinned"] = flags[did]["pinned"]
            merged["dismissed"] = flags[did]["dismissed"]
        pr = prior_by_id.get(did)
        if pr:
            cur_img = merged.get("image")
            pr_img = pr.get("image")
            if _is_placeholderish_image(cur_img) and not _is_placeholderish_image(pr_img):
                merged["image"] = pr_img
                pimgs = pr.get("images")
                if isinstance(pimgs, list) and pimgs:
                    merged["images"] = list(pimgs)
                elif pr_img:
                    merged["images"] = [pr_img]
            cur_it = merged.get("itinerary")
            cur_len = len(cur_it) if isinstance(cur_it, list) else 0
            pr_it = pr.get("itinerary")
            pr_len = len(pr_it) if isinstance(pr_it, list) else 0
            if cur_len == 0 and pr_len > 0:
                merged["itinerary"] = list(pr_it)
                merged["itinerary_dirty"] = bool(pr.get("itinerary_dirty")) if isinstance(
                    pr.get("itinerary_dirty"), bool
                ) else False
            ws = merged.get("weather_summary")
            if (not isinstance(ws, str) or not ws.strip()) and isinstance(pr.get("weather_summary"), str):
                wsp = str(pr.get("weather_summary") or "").strip()
                if wsp:
                    merged["weather_summary"] = wsp
            if not merged.get("commerce") and pr.get("commerce"):
                merged["commerce"] = pr.get("commerce")
        out.append(merged)
    return out


def _reattach_pinned_missing(prior: list[dict[str, Any]], merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    new_ids = {str(d.get("id") or "").strip() for d in merged if str(d.get("id") or "").strip()}
    extra: list[dict[str, Any]] = []
    for p in prior:
        pid = str(p.get("id") or "").strip()
        if not p.get("pinned") or not pid or pid in new_ids:
            continue
        row = dict(p)
        row["dismissed"] = False
        extra.append(row)
    return extra + merged if extra else merged


def _maybe_merge_advisor_profile(
    session_id: str,
    out: dict[str, Any],
    fut: concurrent.futures.Future | None,
) -> dict[str, Any]:
    if fut is None:
        return out
    try:
        pills, note = fut.result(timeout=90)
    except Exception as exc:
        print(f"[wanderly] advisor profile merge skipped: {exc!r}")
        return out
    if not pills:
        return out
    w = out.get("wanderly")
    if not isinstance(w, dict):
        return out
    pp = w.get("profile_pills")
    base = [x for x in pp if isinstance(x, dict)] if isinstance(pp, list) else []
    merged = merge_profile_pills_advisor_precedence(base, pills)
    w = {**w, "profile_pills": merged}
    # Do not surface advisor_note into agent_status_hint — it was flooding the main chat as “tips”.
    set_last_profile_pills(session_id, merged)
    return {**out, "wanderly": w}


def _normalize_parsed(raw: dict[str, Any]) -> WanderlyWorkspaceDTO:
    md = str(raw.get("assistant_markdown") or raw.get("summary") or "").strip()
    pills_in = raw.get("profile_pills") or []
    dests_in = raw.get("destinations") or []
    pills: list[SmokeProfilePillDTO] = []
    for p in pills_in if isinstance(pills_in, list) else []:
        if not isinstance(p, dict):
            continue
        dto = coerce_profile_pill(p)
        if dto is not None:
            pills.append(dto)
    dests: list[dict[str, Any]] = []
    for d in dests_in if isinstance(dests_in, list) else []:
        if not isinstance(d, dict):
            continue
        try:
            dto = SmokeDestDTO.model_validate(d)
            dests.append(dto.model_dump())
        except ValidationError:
            continue
    if not md and dests:
        md = "Here are some directions that could work based on what you shared."
    if not md and not dests:
        md = "Could not extract structured destinations from the model; try a shorter request."
    full_dests = [_minimal_dest_card(x) for x in dests]
    return WanderlyWorkspaceDTO(
        assistant_markdown=md,
        profile_pills=pills,
        destinations=full_dests,
    )


def _complete_smoke_turn(session_id: str, user_message: str, raw_text: str) -> dict[str, Any]:
    parsed = _extract_json_object(raw_text)
    if not parsed:
        dto = WanderlyWorkspaceDTO(
            assistant_markdown=(raw_text[:8000] or "Empty response from model.").strip(),
            profile_pills=[],
            destinations=[],
            agent_status_hint="Could not parse JSON; showing raw markdown only.",
        )
    else:
        dto = _normalize_parsed(parsed)
    dto = _merge_profile_with_inference(dto, user_message)
    append_chat(session_id, "user", user_message)
    append_chat(session_id, "assistant", dto.assistant_markdown)
    set_last_destinations(session_id, dto.destinations)
    set_last_profile_pills(session_id, [p.model_dump() for p in dto.profile_pills])
    return {
        "wanderly": dto.model_dump(),
        "parse_ok": bool(_extract_json_object(raw_text)),
    }


def run_smoke_turn(user_message: str, session_id: str) -> dict[str, Any]:
    load_env_from_roots()
    key = get_api_key()
    um = (user_message or "").strip()
    prompt = _JSON_INSTRUCTIONS + "\n\nUser request:\n" + um
    fut: concurrent.futures.Future | None = None
    # max_workers=2: advisor uses lane ``profile_listener``; main smoke uses ``planner`` — independent locks.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        if advisor_model_id():
            fut = ex.submit(
                run_profile_listener_cursor,
                user_message=um,
                prior_pills=snapshot_profile_pills(session_id),
                prior_chat_snippet=chat_context_snippet(session_id, max_messages=6),
                workspace_destinations_summary=[],
                session_id=session_id,
            )
        with basic_client(key) as client:
            raw_text = run_prompt_collect_assistant_text(
                client,
                prompt,
                **cursor_run_persist_kwargs(session_id, None, trace_path="wanderly.smoke_turn.json"),
            )
        out = _complete_smoke_turn(session_id, user_message, raw_text)
        return _maybe_merge_advisor_profile(session_id, out, fut)


def run_smoke_turn_streaming(
    user_message: str,
    session_id: str,
    emit: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    load_env_from_roots()
    key = get_api_key()
    um = (user_message or "").strip()
    prompt = _JSON_INSTRUCTIONS + "\n\nUser request:\n" + um
    emit_lock = threading.Lock()

    def safe_emit(obj: dict[str, Any]) -> None:
        if emit:
            with emit_lock:
                emit(obj)

    if emit:
        safe_emit({"type": "phase", "message": "Starting planner…"})
    fut: concurrent.futures.Future | None = None
    # Advisor lane (profile_listener) is separate from main planner lane — runs in parallel when enabled.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        if advisor_model_id():
            safe_emit({"type": "phase", "message": "Sidebar advisor running in parallel…"})

            def _listener() -> tuple[list[dict[str, Any]], str | None]:
                return run_profile_listener_cursor(
                    user_message=um,
                    prior_pills=snapshot_profile_pills(session_id),
                    prior_chat_snippet=chat_context_snippet(session_id, max_messages=6),
                    workspace_destinations_summary=[],
                    session_id=session_id,
                )

            fut = ex.submit(_listener)

            def _on_profile_ready(fb: concurrent.futures.Future) -> None:
                try:
                    pills, note = fb.result()
                    if pills:
                        safe_emit(
                            {
                                "type": "advisor_profile",
                                "profile_pills": pills,
                                "advisor_note": note or "",
                            }
                        )
                except Exception as exc:
                    print(f"[wanderly] advisor_profile sse: {exc!r}")

            fut.add_done_callback(_on_profile_ready)

        with basic_client(key) as client:
            raw_text = run_prompt_collect_assistant_text_streaming(
                client,
                prompt,
                emit=safe_emit,
                **cursor_run_persist_kwargs(session_id, None, trace_path="wanderly.smoke_turn.sse"),
            )
        if emit:
            safe_emit({"type": "phase", "message": "Parsing JSON and building cards…"})
        out = _complete_smoke_turn(session_id, user_message, raw_text)
        return _maybe_merge_advisor_profile(session_id, out, fut)


def _complete_refine_turn(
    session_id: str,
    user_message: str,
    raw_text: str,
    prior_dests: list[dict[str, Any]],
) -> dict[str, Any]:
    parsed = _extract_json_object(raw_text)
    if parsed:
        dto = _normalize_parsed(parsed)
        merged = _merge_dest_ui_flags(prior_dests, dto.destinations)
        merged = _reattach_pinned_missing(prior_dests, merged)
        dto = dto.model_copy(update={"destinations": merged})
    else:
        dto = WanderlyWorkspaceDTO(
            assistant_markdown=(raw_text[:8000] or "Empty response.").strip(),
            profile_pills=[],
            destinations=[],
            agent_status_hint="Could not parse JSON; showing raw markdown only.",
        )
    dto = _merge_profile_with_inference(dto, user_message)
    append_chat(session_id, "user", user_message)
    append_chat(session_id, "assistant", dto.assistant_markdown)
    set_last_destinations(session_id, dto.destinations)
    set_last_profile_pills(session_id, [p.model_dump() for p in dto.profile_pills])
    return {"wanderly": dto.model_dump(), "parse_ok": parsed is not None}


def run_refine_turn(user_message: str, session_id: str) -> dict[str, Any]:
    load_env_from_roots()
    key = get_api_key()
    prior_dests = snapshot_destinations_for_model(session_id)
    chat = chat_context_snippet(session_id, max_messages=6)
    context = json.dumps(
        {"prior_chat": chat, "workspace_destinations": prior_dests},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    um = (user_message or "").strip()
    prompt = (
        _REFINE_INSTRUCTIONS
        + "\n"
        + context
        + "\n\nUser refinement:\n"
        + um
    )
    fut: concurrent.futures.Future | None = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        if advisor_model_id():
            fut = ex.submit(
                run_profile_listener_cursor,
                user_message=um,
                prior_pills=snapshot_profile_pills(session_id),
                prior_chat_snippet=chat,
                workspace_destinations_summary=prior_dests,
                session_id=session_id,
            )
        mod, persist = _refine_model_persist_kwargs(session_id, trace_path="wanderly.refine_turn.json")
        with basic_client(key) as client:
            raw_text = run_prompt_collect_assistant_text(
                client, prompt, model_id_override=mod, **persist
            )
        out = _complete_refine_turn(session_id, um, raw_text, prior_dests)
        return _maybe_merge_advisor_profile(session_id, out, fut)


def run_refine_turn_streaming(
    user_message: str,
    session_id: str,
    emit: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    load_env_from_roots()
    key = get_api_key()
    prior_dests = snapshot_destinations_for_model(session_id)
    chat = chat_context_snippet(session_id, max_messages=6)
    context = json.dumps(
        {"prior_chat": chat, "workspace_destinations": prior_dests},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    um = (user_message or "").strip()
    prompt = (
        _REFINE_INSTRUCTIONS
        + "\n"
        + context
        + "\n\nUser refinement:\n"
        + um
    )
    emit_lock = threading.Lock()

    def safe_emit(obj: dict[str, Any]) -> None:
        if emit:
            with emit_lock:
                emit(obj)

    fut: concurrent.futures.Future | None = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        if advisor_model_id():

            def _listener() -> tuple[list[dict[str, Any]], str | None]:
                return run_profile_listener_cursor(
                    user_message=um,
                    prior_pills=snapshot_profile_pills(session_id),
                    prior_chat_snippet=chat,
                    workspace_destinations_summary=prior_dests,
                    session_id=session_id,
                )

            fut = ex.submit(_listener)

            def _on_profile_ready(fb: concurrent.futures.Future) -> None:
                try:
                    pills, note = fb.result()
                    if pills:
                        safe_emit(
                            {
                                "type": "advisor_profile",
                                "profile_pills": pills,
                                "advisor_note": note or "",
                            }
                        )
                except Exception as exc:
                    print(f"[wanderly] advisor_profile sse refine: {exc!r}")

            fut.add_done_callback(_on_profile_ready)

        mod, persist = _refine_model_persist_kwargs(session_id, trace_path="wanderly.refine_turn.sse")
        with basic_client(key) as client:
            raw_text = run_prompt_collect_assistant_text_streaming(
                client,
                prompt,
                emit=safe_emit,
                model_id_override=mod,
                **persist,
            )
        if emit:
            safe_emit({"type": "phase", "message": "Parsing JSON and merging pins…"})
        out = _complete_refine_turn(session_id, um, raw_text, prior_dests)
        return _maybe_merge_advisor_profile(session_id, out, fut)
