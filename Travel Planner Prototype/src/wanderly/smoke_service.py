from __future__ import annotations

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
from src.wanderly.itinerary_debug_log import itinerary_file_log_enabled, log_itinerary_build
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
- workspace_destinations in the context JSON is an **abbreviated** view of the live shortlist
  (ids, hooks, pinned/dismissed, capped day-by-day plans, short warnings). Hero images, commerce,
  booking HTML, and rich flight/hotel rows are omitted to save tokens; the server still merges your
  JSON with the real UI cards by stable ``id``.
- Keep every destination with pinned=true in your destinations array unless the user explicitly
  asks to remove or replace that place (stable id, you may refresh hook/score).
- Do not include destinations with dismissed=true unless the user asks to bring one back.
- For new suggestions, assign new lowercase-slug ids; when editing an existing row, keep its id.
- Always return the full profile_pills array for the six keys when, who, from, vibe, budget, duration (not a partial list; placeholders ok; a fast companion pass may refine).
- Do **not** emit a ``tags`` pill; fold constraints into the appropriate one of the six fields or assistant_markdown.
- Update profile_pills whenever the user states new trip facts (dates, budget, party, vibe, home airport / "flying from …").
- If the user only **compares**, **contrasts**, or **chooses between** places (no request to change days, hotels, flights, or itineraries), return **every non-dismissed** row from workspace_destinations (same ids). You may refresh hook/score text. Do **not** drop unpinned destinations from the shortlist.
- If the user asks to **redo / rebuild / regenerate** one destination's plan, still return **the full non-dismissed shortlist** (same ids): update only the rows they mentioned; omit ``itinerary`` on other rows so the UI keeps existing day-by-day content.
- You may omit per-destination "itinerary" in JSON; the UI keeps existing day-by-day plans unless the user asks to change them.
- **Never remove a non-dismissed destination by omitting its id** from ``destinations``. Travelers remove cards only via the UI (dismiss). If they ask to drop a place in chat, still list that id until the product confirms removal — the server will merge safely.

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


def _vibe_tokens_for_refine(raw: object, *, max_n: int = 8, max_len: int = 48) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw[:max_n]:
        if isinstance(x, str) and x.strip():
            out.append(x.strip()[:max_len])
    return out


def _slim_itinerary_for_refine_prompt(
    it: object,
    *,
    max_days: int = 14,
    max_items: int = 8,
    max_item_chars: int = 200,
    max_label: int = 160,
) -> list[dict[str, Any]]:
    if not isinstance(it, list) or not it:
        return []
    rows: list[dict[str, Any]] = []
    for row in it[:max_days]:
        if not isinstance(row, dict):
            continue
        items_in = row.get("items")
        items: list[str] = []
        if isinstance(items_in, list):
            for itm in items_in[:max_items]:
                if isinstance(itm, str) and itm.strip():
                    items.append(itm.strip()[:max_item_chars])
                elif isinstance(itm, dict):
                    s = str(
                        itm.get("text")
                        or itm.get("item")
                        or itm.get("title")
                        or itm.get("label")
                        or itm.get("description")
                        or ""
                    ).strip()
                    if s:
                        items.append(s[:max_item_chars])
        rows.append(
            {
                "day": row.get("day"),
                "label": str(row.get("label") or "")[:max_label],
                "items": items,
            }
        )
    return rows


def _slim_warnings_for_refine(w: object, *, max_n: int = 6, max_text: int = 180) -> list[str]:
    if not isinstance(w, list) or not w:
        return []
    out: list[str] = []
    for x in w[:max_n]:
        if isinstance(x, str) and x.strip():
            out.append(x.strip()[:max_text])
        elif isinstance(x, dict):
            t = str(x.get("text") or x.get("message") or x.get("body") or "").strip()
            if t:
                out.append(t[:max_text])
        if len(out) >= max_n:
            break
    return out


def _slim_destination_row_for_refine_prompt(d: dict[str, Any]) -> dict[str, Any]:
    """Strip bulky UI fields; keep enough for shortlist + itinerary edits (Tier A #1)."""
    out: dict[str, Any] = {
        "id": str(d.get("id") or ""),
        "name": str(d.get("name") or ""),
        "country": str(d.get("country") or ""),
        "pinned": bool(d.get("pinned")),
        "dismissed": bool(d.get("dismissed")),
    }
    hook = str(d.get("hook") or "").strip()
    if hook:
        out["hook"] = hook[:280]
    sc = d.get("score")
    if sc is not None:
        try:
            out["score"] = int(sc)
        except (TypeError, ValueError):
            pass
    if isinstance(d.get("itinerary_dirty"), bool):
        out["itinerary_dirty"] = d["itinerary_dirty"]
    vibe = _vibe_tokens_for_refine(d.get("vibe"))
    if vibe:
        out["vibe"] = vibe
    ws = str(d.get("weather_summary") or "").strip()
    if ws:
        out["weather_summary"] = ws[:220]
    it_slim = _slim_itinerary_for_refine_prompt(d.get("itinerary"))
    if it_slim:
        out["itinerary"] = it_slim
    war = _slim_warnings_for_refine(d.get("warnings"))
    if war:
        out["warnings"] = war
    return out


def _slim_destinations_for_refine_prompt(destinations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_slim_destination_row_for_refine_prompt(x) for x in destinations if isinstance(x, dict)]


def _refine_prompt_context_json(session_id: str) -> tuple[str, list[dict[str, Any]]]:
    """
    Build the JSON block embedded in the refine planner prompt.

    Returns ``(context_json, prior_dests_full)`` — the model sees slim cards; merge logic uses full prior.
    """
    prior = snapshot_destinations_for_model(session_id)
    chat = chat_context_snippet(session_id, max_messages=6)
    slim = _slim_destinations_for_refine_prompt(prior)
    slim_ctx = json.dumps(
        {"prior_chat": chat, "workspace_destinations": slim},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    if itinerary_file_log_enabled():
        full_ctx = json.dumps(
            {"prior_chat": chat, "workspace_destinations": prior},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        fc, sc = len(full_ctx), len(slim_ctx)
        pct = round(100.0 * (1.0 - sc / max(fc, 1)), 1)
        log_itinerary_build(
            f"path=wanderly.refine_context_slim sid={session_id[:12]!r} dest_n={len(prior)} "
            f"context_json_chars_full={fc} context_json_chars_slim={sc} context_shrink_pct={pct}"
        )
    return slim_ctx, prior


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


def _reattach_non_dismissed_missing(prior: list[dict[str, Any]], merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-append prior rows missing from ``merged`` (same id) so a partial model JSON cannot shrink the board."""
    ids = {str(d.get("id") or "").strip() for d in merged if str(d.get("id") or "").strip()}
    extra: list[dict[str, Any]] = []
    for p in prior:
        if p.get("dismissed"):
            continue
        pid = str(p.get("id") or "").strip()
        if pid and pid not in ids:
            extra.append(dict(p))
    return list(merged) + extra


def _prior_non_dismissed_ids(prior: list[dict[str, Any]]) -> set[str]:
    return {
        str(p.get("id") or "").strip()
        for p in prior
        if not p.get("dismissed") and str(p.get("id") or "").strip()
    }


def _non_dismissed_ids_in_merged(merged: list[dict[str, Any]]) -> set[str]:
    return {
        str(d.get("id") or "").strip()
        for d in merged
        if not d.get("dismissed") and str(d.get("id") or "").strip()
    }


def _maybe_fill_missing_shortlist(
    prior: list[dict[str, Any]],
    merged: list[dict[str, Any]],
    session_id: str,
    *,
    allow_shortlist_shrink: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Refine JSON is only iterated in ``_merge_dest_ui_flags`` — any id omitted disappears from the UI.

    Unless ``allow_shortlist_shrink`` (traveler confirmed in the client after the model tried to drop
    non-dismissed cards), restore every prior non-dismissed row missing from the model output.
    Returns ``(destinations, omitted_ids_that_were_restored)``.
    """
    prior_ids = _prior_non_dismissed_ids(prior)
    merged_nd_ids = _non_dismissed_ids_in_merged(merged)
    omitted = sorted(pid for pid in prior_ids if pid and pid not in merged_nd_ids)
    if allow_shortlist_shrink or not omitted:
        return merged, []
    before_n = len(merged)
    out = _reattach_non_dismissed_missing(prior, merged)
    if len(out) > before_n:
        log_itinerary_build(
            f"path=wanderly.refine.shortlist_reattach sid={session_id[:12]!r} "
            f"restored_ids={omitted!r} before_rows={before_n} after_rows={len(out)} "
            f"allow_shrink={allow_shortlist_shrink}"
        )
    return out, omitted


def _merge_advisor_listener_tuple(
    session_id: str,
    out: dict[str, Any],
    advisor_result: tuple[list[dict[str, Any]], str | None] | None,
) -> dict[str, Any]:
    """Merge profile_listener pills after the main planner has committed workspace + chat state."""
    if not advisor_result:
        return out
    pills, _note = advisor_result
    if not pills:
        return out
    w = out.get("wanderly")
    if not isinstance(w, dict):
        return out
    pp = w.get("profile_pills")
    base = [x for x in pp if isinstance(x, dict)] if isinstance(pp, list) else []
    merged = merge_profile_pills_advisor_precedence(base, pills)
    w = {**w, "profile_pills": merged}
    set_last_profile_pills(session_id, merged)
    return {**out, "wanderly": w}


def _run_profile_listener_after_planner(session_id: str, user_message: str) -> tuple[list[dict[str, Any]], str | None] | None:
    """
    Cheap profile listener: must run **after** smoke/refine commits destinations so summaries match
    the latest shortlist (avoids stale/empty itinerary warnings racing the main model).
    """
    if not advisor_model_id():
        return None
    return run_profile_listener_cursor(
        user_message=(user_message or "").strip(),
        prior_pills=snapshot_profile_pills(session_id),
        prior_chat_snippet=chat_context_snippet(session_id, max_messages=6),
        workspace_destinations_summary=snapshot_destinations_for_model(session_id),
        session_id=session_id,
    )


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
    with basic_client(key) as client:
        raw_text = run_prompt_collect_assistant_text(
            client,
            prompt,
            **cursor_run_persist_kwargs(session_id, None, trace_path="wanderly.smoke_turn.json"),
        )
    out = _complete_smoke_turn(session_id, user_message, raw_text)
    listener = _run_profile_listener_after_planner(session_id, um)
    return _merge_advisor_listener_tuple(session_id, out, listener)


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
    if emit:
        safe_emit({"type": "phase", "message": "Sidebar advisor (profile)…"})
    listener = _run_profile_listener_after_planner(session_id, um)
    if emit and listener and listener[0]:
        safe_emit(
            {
                "type": "advisor_profile",
                "profile_pills": listener[0],
                "advisor_note": listener[1] or "",
            }
        )
    return _merge_advisor_listener_tuple(session_id, out, listener)


def _complete_refine_turn(
    session_id: str,
    user_message: str,
    raw_text: str,
    prior_dests: list[dict[str, Any]],
    *,
    allow_shortlist_shrink: bool,
) -> dict[str, Any]:
    parsed = _extract_json_object(raw_text)
    restored_omitted: list[str] = []
    if parsed:
        dto = _normalize_parsed(parsed)
        merged = _merge_dest_ui_flags(prior_dests, dto.destinations)
        merged = _reattach_pinned_missing(prior_dests, merged)
        merged, restored_omitted = _maybe_fill_missing_shortlist(
            prior_dests, merged, session_id, allow_shortlist_shrink=allow_shortlist_shrink
        )
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
    out: dict[str, Any] = {"wanderly": dto.model_dump(), "parse_ok": parsed is not None}
    if parsed and restored_omitted:
        out["shortlist_drop_blocked"] = True
        out["model_omitted_destination_ids"] = restored_omitted
    return out


def run_refine_turn(
    user_message: str, session_id: str, *, confirm_workspace_drop: bool = False
) -> dict[str, Any]:
    load_env_from_roots()
    key = get_api_key()
    context, prior_dests = _refine_prompt_context_json(session_id)
    um = (user_message or "").strip()
    prompt = (
        _REFINE_INSTRUCTIONS
        + "\n"
        + context
        + "\n\nUser refinement:\n"
        + um
    )
    mod, persist = _refine_model_persist_kwargs(session_id, trace_path="wanderly.refine_turn.json")
    with basic_client(key) as client:
        raw_text = run_prompt_collect_assistant_text(
            client, prompt, model_id_override=mod, **persist
        )
    out = _complete_refine_turn(
        session_id, um, raw_text, prior_dests, allow_shortlist_shrink=confirm_workspace_drop
    )
    listener = _run_profile_listener_after_planner(session_id, um)
    return _merge_advisor_listener_tuple(session_id, out, listener)


def run_refine_turn_streaming(
    user_message: str,
    session_id: str,
    emit: Callable[[dict[str, Any]], None] | None,
    *,
    confirm_workspace_drop: bool = False,
) -> dict[str, Any]:
    load_env_from_roots()
    key = get_api_key()
    context, prior_dests = _refine_prompt_context_json(session_id)
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
    out = _complete_refine_turn(
        session_id, um, raw_text, prior_dests, allow_shortlist_shrink=confirm_workspace_drop
    )
    if emit and out.get("shortlist_drop_blocked"):
        safe_emit(
            {
                "type": "shortlist_drop_blocked",
                "destination_ids": out.get("model_omitted_destination_ids") or [],
            }
        )
    if emit:
        safe_emit({"type": "phase", "message": "Sidebar advisor (profile)…"})
    listener = _run_profile_listener_after_planner(session_id, um)
    if emit and listener and listener[0]:
        safe_emit(
            {
                "type": "advisor_profile",
                "profile_pills": listener[0],
                "advisor_note": listener[1] or "",
            }
        )
    return _merge_advisor_listener_tuple(session_id, out, listener)
