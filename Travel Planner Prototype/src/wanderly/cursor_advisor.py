"""
Cursor Cloud Agent helpers for Wanderly (no local Ollama).

- **Advisor** (``CURSOR_ADVISOR_MODEL_ID``): cheapest parallel profile listener.
- **Structured follow-ups** (``structured_followup_model_id``): prefers ``CURSOR_REFINEMENT_MODEL_ID``,
  else advisor — plan review, advisor_feed chat, profile-change JSON.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from src.integrations.cursor_agent_client import basic_client, get_api_key, load_env_from_roots, run_prompt_collect_assistant_text
from src.wanderly.llm_telemetry import (
    ACTIVITY_ADVISOR,
    ACTIVITY_DESTINATION_FOLLOWUP,
    telemetry_meta,
)
from src.wanderly.refinement_cursor import refinement_model_id
from src.wanderly.schemas import coerce_profile_pill
from src.wanderly.itinerary_debug_log import log_itinerary_build
from src.wanderly.session_store import cursor_run_persist_kwargs_for_lane

# Separate Cloud Agent lanes so cheap follow-ups never POST /runs on the same agent as
# smoke/refine (which also use ``m:<CURSOR_REFINEMENT_MODEL_ID>`` when set) — Cursor returns 409
# if a run is still active on that agent.
_CURSOR_LANE_PROFILE_LISTENER = "profile_listener"
_CURSOR_LANE_PLAN_REVIEW = "plan_review"
_CURSOR_LANE_ADVISOR_FEED = "advisor_feed"
_CURSOR_LANE_PROFILE_CHANGE = "profile_change_advisor"


def advisor_model_id() -> str | None:
    mid = (os.environ.get("CURSOR_ADVISOR_MODEL_ID") or "").strip()
    return mid or None


def structured_followup_model_id() -> str | None:
    """
    Model for structured JSON / prose follow-ups (plan review, advisor_feed, profile-change hints).

    Prefers ``CURSOR_REFINEMENT_MODEL_ID`` when set, else ``CURSOR_ADVISOR_MODEL_ID``.
    **Not** used for ``run_profile_listener_cursor`` (keep that on the cheapest advisor pass).
    """
    return refinement_model_id() or advisor_model_id()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """
    Parse the first complete JSON object in ``text``.

    Models sometimes emit two objects back-to-back; greedy ``{``..``}`` would fail or merge badly.
    """
    t = (text or "").strip()
    if not t:
        return None
    dec = json.JSONDecoder()
    try:
        out, _ = dec.raw_decode(t)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.IGNORECASE)
    if m:
        inner = m.group(1).strip()
        try:
            out, _ = dec.raw_decode(inner)
            return out if isinstance(out, dict) else None
        except json.JSONDecodeError:
            pass
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            out, _end = dec.raw_decode(t, i)
            if isinstance(out, dict):
                return out
        except json.JSONDecodeError:
            continue
    return None


_PROFILE_LISTENER_PROMPT = """
You are Wanderly's fast profile extractor. Reply with ONE JSON object only (no markdown fences, no prose).

Schema:
{
  "profile_pills": [
    {"key": "when", "label": "When", "value": "short text", "icon": "📅", "editable": true},
    {"key": "who", "label": "Who", "value": "short text", "icon": "👥", "editable": true},
    {"key": "from", "label": "Flying from", "value": "City or airport", "icon": "✈️", "editable": true},
    {"key": "vibe", "label": "Vibe", "value": "short text", "icon": "✨", "editable": true},
    {"key": "budget", "label": "Budget", "value": "short text", "icon": "💰", "editable": true},
    {"key": "duration", "label": "Duration", "value": "short text", "icon": "⏱", "editable": true}
  ],
  "advisor_note": "One short optional sentence — your concise read / coaching for the sidebar only (transient). Not a profile field. Empty string if nothing useful."
}

Rules:
- Always include exactly these six keys in profile_pills in this order. Never add ``tags`` or any other pill — interpretive or \"inner voice\" content belongs **only** in ``advisor_note``.
- ``advisor_note``: brief sidebar whisper (e.g. what you noticed about tension, pace, or unstated needs). Do **not** duplicate the six pill values as prose; do not list destinations. Max ~220 chars. Empty if redundant with prior context.
- **Stability (critical):** When ``prior_profile_pills`` already has substantive text for a slot and the latest user message does **not** state a **new** traveler fact for that slot, **copy that pill's value verbatim** from ``prior_profile_pills``. Do **not** paraphrase **vibe**, re-punctuate, or swap synonyms for polish.
- If the user only asks to refresh/rebuild/regenerate **itineraries** or **day plans** (no new profile facts), return ``profile_pills`` that match ``prior_profile_pills`` **character-for-character** on every value.
- **Material change** = dates/length, budget, party/pets, home airport, accessibility, or clearly different vibe/pace **intent** stated in the message — only then rewrite the affected pill(s).
- Infer new facts from the latest user message; use prior pills + context only to disambiguate (prefer new facts over stale values).
- If unknown, use concise placeholders (e.g. "Flexible dates", "Not specified") for the six required pills only.
- If the user names a home city or airport (e.g. "from Seattle", "SEA"), set "from" accordingly.
- **from** (and any city/airport you infer for it): use plain **OTA-style** place text only — e.g. ``Seattle, WA``, ``San Juan, PR``, ``SEA``, ``SFO``. Never narrative qualifiers like ``Seattle-based``, ``…-based hub``, or a trailing `` based``; those strings are not valid flight-search endpoints and confuse downstream tools.
- **when**: travel dates **and** trip length if clear (e.g. "Apr 1–8 · 8 days"). **duration**: trip length ("1 week", "10 days") when not already fully covered in **when**; otherwise a short length placeholder — **not** intraday packing density. **vibe**: style + how busy days should feel (romantic, packed days, slow mornings, …). **who**: travelers and pets only (no budget, no dates).
""".strip()


_PLAN_REVIEW_PROMPT = """
You are Wanderly's trip QA advisor. Compare the traveler profile to the destination cards and their day-by-day itineraries.

Reply with ONE JSON object only (no markdown fences, no prose).

Schema:
{
  "assistant_markdown": "2–6 short paragraphs in markdown: friendly summary, mismatches, pacing or budget tension if any",
  "issues": [
    {"severity": "info|warning", "text": "plain language issue or conflict"}
  ],
  "suggested_prompts": [
    "Concrete follow-up the user could type in the chat to fix or explore the top issue"
  ]
}

Rules:
- Use only the JSON context; do not invent bookings or live prices.
- If plans fit the profile well, say so briefly and keep issues minimal.
- Treat the six profile slots as the only persisted traveler facts (no separate tags field).
- suggested_prompts: 0–3 strings, each actionable (start with a verb).
- severity "warning" for real trip friction (pace, budget, weather fit, mobility, party size vs activities); "info" for mild notes.
""".strip()


def _validate_profile_pills(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        dto = coerce_profile_pill(p)
        if dto is not None:
            out.append(dto.model_dump())
    return out


def run_profile_listener_cursor(
    *,
    user_message: str,
    prior_pills: list[dict[str, Any]],
    prior_chat_snippet: str,
    workspace_destinations_summary: list[dict[str, Any]],
    session_id: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Single cheap Cursor agent call; returns (profile_pills dicts, advisor_note)."""
    mid = advisor_model_id()
    if not mid:
        return [], None
    load_env_from_roots()
    ctx = json.dumps(
        {
            "prior_profile_pills": prior_pills[:24],
            "prior_chat_snippet": (prior_chat_snippet or "")[:4500],
            "workspace_destination_titles": [
                {
                    "id": str(x.get("id") or ""),
                    "name": str(x.get("name") or ""),
                    "country": str(x.get("country") or ""),
                    "pinned": bool(x.get("pinned")),
                    "dismissed": bool(x.get("dismissed")),
                }
                for x in (workspace_destinations_summary or [])[:12]
                if isinstance(x, dict)
            ],
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    prompt = (
        _PROFILE_LISTENER_PROMPT
        + "\n\nContext JSON:\n"
        + ctx
        + "\n\nLatest user message:\n"
        + (user_message or "").strip()[:8000]
    )
    try:
        with basic_client(get_api_key(), timeout_s=120.0) as client:
            raw_text = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=mid,
                persistent_instruction_prefix=_PROFILE_LISTENER_PROMPT,
                llm_telemetry_activity=ACTIVITY_ADVISOR,
                llm_telemetry_sub_activity="profile_listener",
                llm_telemetry_meta=telemetry_meta(session_id=session_id),
                **cursor_run_persist_kwargs_for_lane(
                    session_id or "", _CURSOR_LANE_PROFILE_LISTENER, trace_path="cursor_advisor.profile_listener"
                ),
            )
    except Exception as exc:
        log_itinerary_build(
            f"FAIL path=cursor_advisor.profile_listener sid={(session_id or '')[:12]!r} exc={type(exc).__name__}: {exc!s}"
        )
        print(f"[cursor_advisor] profile_listener failed: {exc!r}")
        return [], None
    parsed = _extract_json_object(raw_text)
    if not parsed:
        print("[cursor_advisor] profile_listener: no JSON parsed")
        return [], None
    pills = _validate_profile_pills(parsed.get("profile_pills"))
    note = parsed.get("advisor_note")
    note_s = str(note).strip() if note is not None else ""
    if len(pills) < 4:
        return [], note_s or None
    return pills, note_s or None


def run_plan_review_cursor(
    *,
    profile_pills: list[dict[str, Any]],
    destinations: list[dict[str, Any]],
    session_id: str | None = None,
) -> dict[str, Any]:
    """QA review: profile vs destinations + itineraries. Returns wanderly-shaped fragments."""
    mid = structured_followup_model_id()
    if not mid:
        return {
            "assistant_markdown": "",
            "issues": [],
            "suggested_prompts": [],
            "skipped": True,
            "reason": "CURSOR_REFINEMENT_MODEL_ID and CURSOR_ADVISOR_MODEL_ID unset",
        }
    load_env_from_roots()
    slim_dests: list[dict[str, Any]] = []
    for d in (destinations or [])[:12]:
        if not isinstance(d, dict):
            continue
        it = d.get("itinerary")
        it_out: list[dict[str, Any]] = []
        if isinstance(it, list):
            for row in it[:14]:
                if not isinstance(row, dict):
                    continue
                it_out.append(
                    {
                        "day": row.get("day"),
                        "label": str(row.get("label") or "")[:120],
                        "items": [
                            str(x)[:200]
                            for x in (row.get("items") or [])[:8]
                            if isinstance(x, str) and x.strip()
                        ],
                    }
                )
        slim_dests.append(
            {
                "id": str(d.get("id") or ""),
                "name": str(d.get("name") or ""),
                "country": str(d.get("country") or ""),
                "hook": str(d.get("hook") or "")[:240],
                "cost": str(d.get("cost") or ""),
                "flight": str(d.get("flight") or ""),
                "vibe": d.get("vibe") if isinstance(d.get("vibe"), list) else [],
                "warnings": d.get("warnings") if isinstance(d.get("warnings"), list) else [],
                "itinerary": it_out,
            }
        )
    ctx = json.dumps(
        {"profile_pills": profile_pills[:24], "destinations": slim_dests},
        ensure_ascii=True,
    )
    prompt = _PLAN_REVIEW_PROMPT + "\n\nContext JSON:\n" + ctx[:48000]
    try:
        with basic_client(get_api_key(), timeout_s=120.0) as client:
            raw_text = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=mid,
                persistent_instruction_prefix=_PLAN_REVIEW_PROMPT,
                llm_telemetry_activity=ACTIVITY_ADVISOR,
                llm_telemetry_sub_activity="plan_review",
                llm_telemetry_meta=telemetry_meta(session_id=session_id),
                **cursor_run_persist_kwargs_for_lane(
                    session_id or "", _CURSOR_LANE_PLAN_REVIEW, trace_path="cursor_advisor.plan_review"
                ),
            )
    except Exception as exc:
        log_itinerary_build(
            f"FAIL path=cursor_advisor.plan_review sid={(session_id or '')[:12]!r} exc={type(exc).__name__}: {exc!s}"
        )
        print(f"[cursor_advisor] plan_review failed: {exc!r}")
        return {
            "assistant_markdown": "",
            "issues": [],
            "suggested_prompts": [],
            "error": str(exc),
        }
    parsed = _extract_json_object(raw_text)
    if not parsed:
        return {
            "assistant_markdown": (raw_text or "")[:4000].strip().replace("\x00", ""),
            "issues": [],
            "suggested_prompts": [],
            "parse_ok": False,
        }
    issues_raw = parsed.get("issues") or []
    issues: list[dict[str, str]] = []
    if isinstance(issues_raw, list):
        for it in issues_raw[:12]:
            if not isinstance(it, dict):
                continue
            sev = str(it.get("severity") or "info").lower()
            if sev not in ("info", "warning"):
                sev = "info"
            tx = str(it.get("text") or "").strip()
            if tx:
                issues.append({"severity": sev, "text": tx[:500]})
    prompts_raw = parsed.get("suggested_prompts") or []
    prompts: list[str] = []
    if isinstance(prompts_raw, list):
        for x in prompts_raw[:5]:
            if isinstance(x, str) and x.strip():
                prompts.append(x.strip()[:400])
    md = str(parsed.get("assistant_markdown") or "").strip().replace("\x00", "")
    # Model occasionally nests another JSON object inside assistant_markdown.
    if md.startswith("{") and '"assistant_markdown"' in md:
        nested = _extract_json_object(md)
        if nested and isinstance(nested.get("assistant_markdown"), str):
            inner = str(nested["assistant_markdown"]).strip().replace("\x00", "")
            if inner:
                md = inner
    return {
        "assistant_markdown": md[:8000],
        "issues": issues,
        "suggested_prompts": prompts,
        "parse_ok": True,
    }


_ADVISOR_CHAT_PROMPT = """
You are Wanderly's quick trip advisor. Reply with ONE JSON object only (no markdown fences, no prose before or after).

Schema:
{
  "assistant_markdown": "2–8 sentences for the traveler; you may use **double asterisks** for emphasis. Ground answers in the context JSON when possible.",
  "refine_prompt": null OR one imperative paragraph the MAIN trip planner should run to change the workspace/itinerary (concrete edits: swap days, add rest time, change pace, replace activities, etc.). Must name the destination by id and name from context. Use null when the user only needs Q&A, logistics you do not have, or no itinerary edit is appropriate,
  "offer_web_search": false,
  "suggested_search_query": null
}

Rules:
- Destinations may include "itinerary" (day, label, items) — cite it when answering about the plan.
- profile_pills are the six main slots only (when, who, from, vibe, budget, duration).
- refine_prompt: only when applying that change would clearly improve the trip per the user's ask; keep it self-contained (the app will prepend focus destination). null for pure information, validation, or "I don't have live data" replies.
- Do not claim live prices, availability, or bookings.
- When the user's question needs **facts not present** in the JSON (weather, ocean temps, reef quality, live hours, whether a specific activity exists near X, etc.), say so clearly in assistant_markdown and set **offer_web_search** to **true**. Provide **suggested_search_query**: one short web-search phrase (destination + topic, no quotes), or null if offer_web_search is false.
- When you can answer fully from context alone, set **offer_web_search** to **false** and **suggested_search_query** to **null**.
- If the prompt includes a **WEB_EXCERPTS** block, the server already ran search — answer using JSON + excerpts, set **offer_web_search** false and **suggested_search_query** null.
""".strip()


def _destination_row_for_advisor_chat(
    d: dict[str, Any],
    *,
    include_itinerary: bool,
    max_days: int = 14,
    max_items_per_day: int = 8,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(d.get("id") or ""),
        "name": str(d.get("name") or ""),
        "country": str(d.get("country") or ""),
    }
    hook = str(d.get("hook") or "").strip()
    if hook:
        out["hook"] = hook[:280]
    if not include_itinerary:
        return out
    it = d.get("itinerary")
    if not isinstance(it, list) or not it:
        return out
    rows: list[dict[str, Any]] = []
    for row in it[:max_days]:
        if not isinstance(row, dict):
            continue
        items_in = row.get("items")
        items: list[str] = []
        if isinstance(items_in, list):
            for itm in items_in[:max_items_per_day]:
                if isinstance(itm, str) and itm.strip():
                    items.append(itm.strip()[:200])
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
                        items.append(s[:200])
        rows.append(
            {
                "day": row.get("day"),
                "label": str(row.get("label") or "")[:160],
                "items": items,
            }
        )
    if rows:
        out["itinerary"] = rows
    return out


_ADVISOR_WEB_FACT_USER_RE = re.compile(
    r"\b(population|how\s+many\s+people|people\s+live|how\s+large|land\s+area|city\s+size|size\s+of|"
    r"km2|km²|square\s*miles?|sq\s*mi|weather|temperature|water\s+temp|hours?|opening|closes?|"
    r"when\s+does|elevation|depth|tide|current\s+conditions|covid|visa\s+rules?|"
    r"safety\s+in|crime\s+rate|exchange\s+rate|time\s+zone)\b",
    re.I,
)
_ADVISOR_WEB_GAP_MD_RE = re.compile(
    r"(can\x27t|cannot|can not|doesn\x27t|does not|do not|not in (?:the|your|this)|from this context|"
    r"json (?:does not|doesn\x27t)|no (?:population|data|number)|isn\x27t in|not included|"
    r"i can\x27t give|without going beyond|does not name|does not include|"
    r"so i can\x27t|i can\x27t confirm|i can\x27t tell)",
    re.I,
)


def _patch_advisor_web_search_when_obvious(
    out: dict[str, Any],
    *,
    user_message: str,
    focus_destination_id: str,
    dest_payload: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Models sometimes omit ``offer_web_search`` even when they admit missing facts.
    If the user clearly asked for external facts and the reply says context is insufficient, offer search.
    """
    if out.get("offer_web_search") and out.get("suggested_search_query"):
        return out
    um = (user_message or "").strip()
    md = str(out.get("assistant_markdown") or "").strip()
    if len(um) < 6 or len(md) < 24:
        return out
    if not _ADVISOR_WEB_FACT_USER_RE.search(um) or not _ADVISOR_WEB_GAP_MD_RE.search(md):
        return out
    fid = (focus_destination_id or "").strip()
    geo = ""
    for row in dest_payload or []:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        if fid and rid == fid:
            geo = f"{str(row.get('name') or '').strip()} {str(row.get('country') or '').strip()}".strip()
            break
    if not geo and dest_payload and isinstance(dest_payload[0], dict):
        r0 = dest_payload[0]
        geo = f"{str(r0.get('name') or '').strip()} {str(r0.get('country') or '').strip()}".strip()
    tail = um[:220].strip()
    sq = f"{geo} {tail}".strip() if geo else tail
    if not sq:
        return out
    return {**out, "offer_web_search": True, "suggested_search_query": sq[:500]}


def _web_snippets_markdown(snippets: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for i, s in enumerate(snippets[:8], 1):
        title = str(s.get("title") or "").strip()
        url = str(s.get("url") or "").strip()
        body = str(s.get("body") or "").strip()
        parts.append(f"### [{i}] {title}\nURL: {url}\n{body}\n")
    return "\n".join(parts) if parts else "(no excerpts)"


def _advisor_prompt_with_web_excerpts(
    *,
    ctx_json: str,
    user_message: str,
    snippets: list[dict[str, str]],
) -> str:
    block = _web_snippets_markdown(snippets)
    extra = """

WEB SEARCH EXCERPTS (server-fetched; may be incomplete or dated). Use with the Context JSON to answer the user. Prefer excerpts for hard facts when present; if excerpts disagree, say briefly. Do not invent beyond JSON + excerpts. For this reply set offer_web_search to false and suggested_search_query to null.
"""
    return (
        _ADVISOR_CHAT_PROMPT
        + extra
        + "\n\nContext JSON:\n"
        + ctx_json[:16000]
        + "\n\nWEB_EXCERPTS:\n"
        + block
        + "\n\nUser message:\n"
        + (user_message or "").strip()[:4000]
    )


def _advisor_chat_parse_response(raw_text: str) -> dict[str, Any]:
    """Parse advisor JSON; on failure treat whole body as assistant_markdown."""
    raw = (raw_text or "").strip()
    empty_out: dict[str, Any] = {
        "assistant_markdown": "",
        "refine_prompt": None,
        "offer_web_search": False,
        "suggested_search_query": None,
    }
    if not raw:
        return empty_out
    parsed = _extract_json_object(raw)
    if parsed and isinstance(parsed.get("assistant_markdown"), str):
        md = str(parsed["assistant_markdown"]).strip()
        rp_any = parsed.get("refine_prompt")
        refine: str | None = None
        if isinstance(rp_any, str):
            rps = rp_any.strip()
            if rps and rps.lower() not in ("null", "none"):
                refine = rps[:4000]
        offer = bool(parsed.get("offer_web_search"))
        sq_any = parsed.get("suggested_search_query")
        sq: str | None = None
        if isinstance(sq_any, str):
            sqt = sq_any.strip()
            if sqt and sqt.lower() not in ("null", "none"):
                sq = sqt[:500]
        if offer and not sq:
            offer = False
        out: dict[str, Any] = {
            "assistant_markdown": md[:8000] or "",
            "refine_prompt": refine,
            "offer_web_search": offer,
            "suggested_search_query": sq,
        }
        return out
    return {
        "assistant_markdown": raw[:6000],
        "refine_prompt": None,
        "offer_web_search": False,
        "suggested_search_query": None,
    }


def run_advisor_chat_cursor(
    *,
    user_message: str,
    profile_pills: list[dict[str, Any]],
    destinations: list[dict[str, Any]],
    session_id: str | None = None,
    focus_destination_id: str | None = None,
    web_snippets: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Structured reply for /advisor_feed: ``assistant_markdown`` + optional ``refine_prompt`` for the main planner."""
    mid = structured_followup_model_id()
    if not mid:
        return {
            "assistant_markdown": "Set CURSOR_REFINEMENT_MODEL_ID or CURSOR_ADVISOR_MODEL_ID to enable the Cursor-based advisor.",
            "refine_prompt": None,
            "offer_web_search": False,
            "suggested_search_query": None,
        }
    load_env_from_roots()
    fid = (focus_destination_id or "").strip()
    dest_payload: list[dict[str, Any]] = []
    seen: set[str] = set()
    if fid:
        for x in destinations or []:
            if isinstance(x, dict) and str(x.get("id") or "").strip() == fid:
                dest_payload.append(_destination_row_for_advisor_chat(x, include_itinerary=True))
                seen.add(fid)
                break
    for x in destinations or []:
        if not isinstance(x, dict):
            continue
        did = str(x.get("id") or "").strip()
        if not did or did in seen:
            continue
        dest_payload.append(_destination_row_for_advisor_chat(x, include_itinerary=False))
        if len(dest_payload) >= 10:
            break
        seen.add(did)
    ctx = json.dumps(
        {
            "profile_pills": profile_pills[:24],
            "destinations": dest_payload,
            "focus_destination_id": fid or None,
        },
        ensure_ascii=True,
    )
    um = (user_message or "").strip()[:4000]
    if web_snippets:
        prompt = _advisor_prompt_with_web_excerpts(ctx_json=ctx, user_message=um, snippets=web_snippets)
    else:
        prompt = (
            _ADVISOR_CHAT_PROMPT
            + "\n\nContext JSON:\n"
            + ctx[:16000]
            + "\n\nUser message:\n"
            + um
        )
    try:
        with basic_client(get_api_key(), timeout_s=90.0) as client:
            raw_text = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=mid,
                persistent_instruction_prefix=_ADVISOR_CHAT_PROMPT,
                llm_telemetry_activity=ACTIVITY_DESTINATION_FOLLOWUP,
                llm_telemetry_sub_activity="advisor_feed",
                llm_telemetry_meta=telemetry_meta(
                    session_id=session_id,
                    focus_destination_id=focus_destination_id,
                ),
                **cursor_run_persist_kwargs_for_lane(
                    session_id or "", _CURSOR_LANE_ADVISOR_FEED, trace_path="cursor_advisor.advisor_feed"
                ),
            )
    except Exception as exc:
        log_itinerary_build(
            f"FAIL path=cursor_advisor.advisor_feed sid={(session_id or '')[:12]!r} "
            f"focus={(focus_destination_id or '')[:24]!r} exc={type(exc).__name__}: {exc!s}"
        )
        return {
            "assistant_markdown": f"(Advisor unavailable: {exc})",
            "refine_prompt": None,
            "offer_web_search": False,
            "suggested_search_query": None,
        }
    parsed = _advisor_chat_parse_response(raw_text)
    if web_snippets:
        parsed["offer_web_search"] = False
        parsed["suggested_search_query"] = None
        return parsed
    return _patch_advisor_web_search_when_obvious(
        parsed,
        user_message=(user_message or "").strip(),
        focus_destination_id=fid,
        dest_payload=dest_payload,
    )


_PROFILE_CHANGE_PROMPT = """
You are Wanderly's advisor. The traveler changed ONE field in their sidebar profile (budget, who, vibe, when, duration, or flying-from).

Reply with ONE JSON object only (no markdown fences, no prose).

Schema:
{
  "headline": "1-3 sentences: react to the change and what it implies for their shortlist",
  "warnings": [
    { "destination_id": "exact id from context or empty string", "text": "short card-level warning if one place clashes with the new profile" }
  ],
  "suggestions": [
    {
      "label": "Short button title (max 10 words)",
      "description": "One line — what asking the planner to do will improve",
      "icon": "single emoji",
      "refine_prompt": "One imperative sentence the user could send as a chat message to the main planner; name real destinations from context; self-contained"
    }
  ]
}

Rules:
- 0–4 suggestions; refine_prompt must be ready to paste as the user's next message (no quotes around it).
- 0–6 warnings; use destination_id from context when the warning targets one card; use "" only for trip-wide notes.
- Use only the JSON context; do not invent exact prices, bookings, or policies.
- If the change is minor and nothing needs fixing, return empty suggestions and a short supportive headline.
""".strip()


def _slim_destinations_for_profile_change(destinations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for d in (destinations or [])[:14]:
        if not isinstance(d, dict):
            continue
        it = d.get("itinerary")
        it_brief: list[dict[str, Any]] = []
        if isinstance(it, list):
            for row in it[:5]:
                if not isinstance(row, dict):
                    continue
                it_brief.append(
                    {
                        "day": row.get("day"),
                        "label": str(row.get("label") or "")[:80],
                        "n_items": len(row.get("items") or []) if isinstance(row.get("items"), list) else 0,
                    }
                )
        out.append(
            {
                "id": str(d.get("id") or ""),
                "name": str(d.get("name") or ""),
                "country": str(d.get("country") or ""),
                "hook": str(d.get("hook") or "")[:200],
                "cost": str(d.get("cost") or "")[:40],
                "flight": str(d.get("flight") or "")[:40],
                "vibe": (d.get("vibe") if isinstance(d.get("vibe"), list) else [])[:6],
                "pinned": bool(d.get("pinned")),
                "dismissed": bool(d.get("dismissed")),
                "itinerary_days": it_brief,
            }
        )
    return out


def run_profile_change_advisor_cursor(
    *,
    pill_key: str,
    old_value: str,
    new_value: str,
    profile_pills: list[dict[str, Any]],
    destinations: list[dict[str, Any]],
    session_id: str | None = None,
) -> dict[str, Any]:
    """Headline + optional card warnings + refine_prompt suggestions after a profile edit (refinement or advisor)."""
    mid = structured_followup_model_id()
    if not mid:
        return {
            "skipped": True,
            "reason": "CURSOR_REFINEMENT_MODEL_ID and CURSOR_ADVISOR_MODEL_ID unset",
            "headline": "",
            "warnings": [],
            "suggestions": [],
        }
    load_env_from_roots()
    ctx = json.dumps(
        {
            "change": {
                "key": pill_key,
                "old_value": (old_value or "")[:400],
                "new_value": (new_value or "")[:400],
            },
            "profile_pills": profile_pills[:24],
            "destinations": _slim_destinations_for_profile_change(destinations),
        },
        ensure_ascii=True,
    )
    prompt = _PROFILE_CHANGE_PROMPT + "\n\nContext JSON:\n" + ctx[:52000]
    try:
        with basic_client(get_api_key(), timeout_s=120.0) as client:
            raw_text = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=mid,
                persistent_instruction_prefix=_PROFILE_CHANGE_PROMPT + "\n\nContext JSON:\n",
                llm_telemetry_activity=ACTIVITY_ADVISOR,
                llm_telemetry_sub_activity="profile_change",
                llm_telemetry_meta=telemetry_meta(session_id=session_id, pill_key=pill_key),
                **cursor_run_persist_kwargs_for_lane(
                    session_id or "", _CURSOR_LANE_PROFILE_CHANGE, trace_path="cursor_advisor.profile_change"
                ),
            )
    except Exception as exc:
        log_itinerary_build(
            f"FAIL path=cursor_advisor.profile_change sid={(session_id or '')[:12]!r} "
            f"pill_key={pill_key!r} exc={type(exc).__name__}: {exc!s}"
        )
        print(f"[cursor_advisor] profile_change_advisor failed: {exc!r}")
        return {
            "skipped": True,
            "reason": str(exc),
            "headline": "",
            "warnings": [],
            "suggestions": [],
            "error": str(exc),
        }
    parsed = _extract_json_object(raw_text)
    if not parsed:
        return {
            "skipped": True,
            "reason": "no_json",
            "headline": (raw_text or "")[:500].strip(),
            "warnings": [],
            "suggestions": [],
        }
    headline = str(parsed.get("headline") or "").strip()[:2000]
    warnings_raw = parsed.get("warnings") or []
    warnings: list[dict[str, str]] = []
    if isinstance(warnings_raw, list):
        for w in warnings_raw[:8]:
            if not isinstance(w, dict):
                continue
            did = str(w.get("destination_id") or w.get("destId") or "").strip()
            tx = str(w.get("text") or "").strip()
            if tx:
                warnings.append({"destination_id": did, "text": tx[:400]})
    sug_raw = parsed.get("suggestions") or []
    suggestions: list[dict[str, str]] = []
    if isinstance(sug_raw, list):
        for s in sug_raw[:5]:
            if not isinstance(s, dict):
                continue
            lab = str(s.get("label") or "").strip()
            desc = str(s.get("description") or "").strip()
            ic = str(s.get("icon") or "✨").strip()[:8] or "✨"
            rp = str(s.get("refine_prompt") or s.get("refinePrompt") or "").strip()
            if lab and rp:
                suggestions.append(
                    {
                        "label": lab[:120],
                        "description": desc[:240],
                        "icon": ic,
                        "refine_prompt": rp[:1200],
                    }
                )
    return {
        "skipped": False,
        "headline": headline,
        "warnings": warnings,
        "suggestions": suggestions,
    }


def merge_profile_pills_advisor_precedence(
    main_pills: list[dict[str, Any]],
    advisor_pills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Overlay advisor pills onto main; advisor wins when its value looks substantive."""
    by_key: dict[str, dict[str, Any]] = {}
    for p in main_pills:
        if isinstance(p, dict) and p.get("key"):
            mk = str(p["key"])
            if mk == "tags":
                continue
            by_key[mk] = dict(p)
    for p in advisor_pills:
        if not isinstance(p, dict):
            continue
        dto = coerce_profile_pill(p)
        if dto is None:
            continue
        p = dto.model_dump()
        k = str(p.get("key") or "").strip()
        if not k or k == "tags":
            continue
        v = str(p.get("value") or "").strip()
        substantive = bool(v) and v.lower() not in ("not specified", "tbd", "unknown", "n/a", "flexible dates")
        if substantive:
            by_key[k] = p
        elif k not in by_key:
            by_key[k] = p
    preferred = ["when", "who", "from", "vibe", "budget", "duration"]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for k in preferred:
        if k in by_key:
            out.append(by_key[k])
            seen.add(k)
    for k, row in by_key.items():
        if k not in seen:
            out.append(row)
    return out
