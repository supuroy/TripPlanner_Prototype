"""
LLM routing for the traveler's next line (refine vs Q&A vs compare vs explicit removal).

Used by ``POST /api/wanderly/orchestrate`` and the Figma client so orchestration is model-driven
instead of regex. Requires ``CURSOR_ADVISOR_MODEL_ID`` or ``CURSOR_REFINEMENT_MODEL_ID`` (or
``CURSOR_MODEL_ID`` via refinement helper) to be set; otherwise callers get ``parse_ok: false``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from src.integrations.cursor_agent_client import basic_client, get_api_key, load_env_from_roots, run_prompt_collect_assistant_text
from src.wanderly.llm_telemetry import ACTIVITY_DESTINATION_FOLLOWUP, telemetry_meta
from src.wanderly.cursor_advisor import advisor_model_id, structured_followup_model_id
from src.wanderly.inline_itinerary import is_plan_compare_only_question, itinerary_primary_refine_message
from src.wanderly.itinerary_debug_log import log_itinerary_build
from src.wanderly.session_store import cursor_run_persist_kwargs_for_lane


_CURSOR_LANE_INTENT = "wanderly_user_intent"


class WanderlyIntentClassification(BaseModel):
    """Structured intent for a single user line (refine / Q&A / compare / removal language)."""

    workspace_action: str = Field(
        ...,
        description=(
            "One of: refine_workspace, targeted_itinerary_edit, compare_places, "
            "lightweight_qa, explicit_remove_destinations, ambiguous"
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    rationale: str = Field(default="", max_length=400)
    itinerary_refresh: str = Field(
        default="auto",
        description='After a workspace refine: "auto" | "none" | "all" | "focus_only" (which cards need lazy itinerary rebuild).',
    )
    itinerary_delivery: str = Field(
        default="auto",
        description=(
            'How to deliver rebuilt itineraries after refine: "auto" | "inline_in_refine" | '
            '"lazy_build" | "none".'
        ),
    )
    focus_destination_ids: list[str] = Field(
        default_factory=list,
        description="Destination ids (workspace slugs) that need itinerary rebuild when itinerary_refresh is focus_only.",
    )
    lock_canvas_navigation: bool = Field(
        default=True,
        description="If false, the UI may let users switch destination cards while the planner request runs (single-card edits).",
    )


_INTENT_PROMPT = """You route one user line for a trip-planning UI.

Reply with ONE JSON object only (no markdown fences, no prose).

Schema:
{
  "workspace_action": "refine_workspace|targeted_itinerary_edit|compare_places|lightweight_qa|explicit_remove_destinations|ambiguous",
  "confidence": 0.0,
  "rationale": "short",
  "itinerary_refresh": "auto|none|all|focus_only",
  "itinerary_delivery": "auto|inline_in_refine|lazy_build|none",
  "focus_destination_ids": ["optional-destination-id-slugs"],
  "lock_canvas_navigation": true
}

Also set:
- **itinerary_refresh**:
  - **none** — sidebar/workspace wording only; no day-by-day itinerary rebuild.
  - **all** — trip shape or multi-card change; every card may need a new lazy itinerary.
  - **focus_only** — only one (or listed) destination(s) need rebuild; set **focus_destination_ids**.
  - **auto** — client may fall back to lighter heuristics.
- **itinerary_delivery**:
  - **inline_in_refine** — embed complete day-by-day ``itinerary`` JSON in the refine response for focus destination(s) when possible (skips a second lazy build).
  - **lazy_build** — always run dedicated itinerary builder after refine.
  - **none** — no itinerary rebuild.
  - **auto** — on an open destination card with **focus_destination_id** set: use **targeted_itinerary_edit** + **focus_only** + **lazy_build** for redo/rebuild/lodging/hotel/day-plan asks on **that card only**; use **refine_workspace** + **all** only when the user clearly means the **whole shortlist** or **every** destination.
- **focus_destination_ids**: workspace destination ids when refreshing a subset (e.g. ["costa-rica"]). When **focus_destination_id** is provided in instructions, default to that id for single-card itinerary/lodging edits.
- **lock_canvas_navigation**: **false** when the user clearly edits **one named card** and others should stay browsable during the run; **true** for broad replans.

Rules:
- **refine_workspace**: user wants to change the **whole shortlist**, trip-wide profile/dates/budget, **multiple** destinations at once, or add/remove cards — **not** a redo of only the open card's day-by-day plan.
- **targeted_itinerary_edit**: redo/rebuild/regenerate/refresh **one** destination's day-by-day plan (lodging consolidation on that card, fewer hotels, redo days, etc.); not removing other cards from the shortlist.
- **compare_places**: choose between / compare / vs / pros and cons without asking to change itineraries.
- **lightweight_qa**: general travel question that does not require rewriting the workspace JSON.
- **explicit_remove_destinations**: user clearly asks to remove, delete, or drop named destination(s) from the list (product should confirm before shrinking the board).
- **ambiguous**: cannot tell confidently.

When **focus_destination_id** is in the instructions and the user asks to redo/rebuild/regenerate the trip plan, consolidate lodging, use one hotel, or change days **without naming other destinations**, use **targeted_itinerary_edit** (not **refine_workspace**), **itinerary_refresh: focus_only**, **focus_destination_ids: [that id]**, **itinerary_delivery: lazy_build**, **lock_canvas_navigation: false**.

Use **refine_workspace** + **itinerary_refresh: all** only when the user clearly wants **every** destination updated or the **entire** shortlist/workspace changed.

For **targeted_itinerary_edit** with explicit "inline" / one-shot wording only, you may set **itinerary_delivery** to **inline_in_refine** (still **focus_only**, exactly one id).
""".strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    t = (text or "").strip()
    if not t:
        return None
    try:
        out = json.loads(t)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.IGNORECASE)
    if m:
        try:
            out = json.loads(m.group(1).strip())
            return out if isinstance(out, dict) else None
        except json.JSONDecodeError:
            return None
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            dec = json.JSONDecoder()
            out, _ = dec.raw_decode(t, i)
            return out if isinstance(out, dict) else None
        except json.JSONDecodeError:
            continue
    return None


def _intent_model_id() -> str | None:
    """Prefer cheapest advisor for routing-only calls."""
    return advisor_model_id() or structured_followup_model_id()


def _fast_path_intent_classification(
    user_message: str,
    *,
    session_id: str = "",
    focus_destination_id: str | None = None,
) -> WanderlyIntentClassification | None:
    """Skip a blocking Cursor call for unambiguous trip-wide itinerary / compare lines."""
    um = (user_message or "").strip()
    if not um:
        return None
    focus = (focus_destination_id or "").strip()
    if is_plan_compare_only_question(um):
        return WanderlyIntentClassification(
            workspace_action="compare_places",
            confidence=0.88,
            rationale="compare_only_fast_path",
            itinerary_refresh="none",
            itinerary_delivery="none",
            focus_destination_ids=[focus] if focus else [],
            lock_canvas_navigation=False,
        )
    from src.wanderly.refine_shortlist_guard import (
        is_trip_wide_itinerary_refine_message,
        match_focus_destination_id,
        resolve_refine_prior_destinations,
    )

    if itinerary_primary_refine_message(um) and is_trip_wide_itinerary_refine_message(um):
        focus_ids = [focus] if focus else []
        return WanderlyIntentClassification(
            workspace_action="refine_workspace",
            confidence=0.9,
            rationale="trip_wide_itinerary_rebuild_fast_path",
            itinerary_refresh="all",
            itinerary_delivery="lazy_build",
            focus_destination_ids=focus_ids,
            lock_canvas_navigation=True,
        )
    prior_dests = resolve_refine_prior_destinations(session_id)
    matched = focus or match_focus_destination_id(um, prior_dests)
    if matched and not is_trip_wide_itinerary_refine_message(um):
        if re.search(
            r"\b(rebuild|refresh|regenerate|redo|revise|update|rewrite)\b",
            um,
            re.I,
        ):
            return WanderlyIntentClassification(
                workspace_action="targeted_itinerary_edit",
                confidence=0.88,
                rationale="single_card_refine_fast_path",
                itinerary_refresh="focus_only",
                itinerary_delivery="lazy_build",
                focus_destination_ids=[matched],
                lock_canvas_navigation=False,
            )
    return None


def classify_user_intent_llm(
    *,
    user_message: str,
    session_id: str,
    focus_destination_id: str | None = None,
) -> WanderlyIntentClassification | None:
    """
    Run a small structured Cursor call. Returns ``None`` if no model id, network failure, or invalid JSON.
    """
    fast = _fast_path_intent_classification(
        user_message,
        session_id=session_id,
        focus_destination_id=focus_destination_id,
    )
    if fast is not None:
        log_itinerary_build(
            f"path=wanderly.user_intent_llm fast_path sid={(session_id or '')[:12]!r} "
            f"action={fast.workspace_action!r} refresh={fast.itinerary_refresh!r}"
        )
        return fast
    mid = _intent_model_id()
    if not mid:
        log_itinerary_build("path=wanderly.user_intent_llm skip no_model_id")
        return None
    load_env_from_roots()
    um = (user_message or "").strip()
    if not um:
        return None
    focus = (focus_destination_id or "").strip()
    extra = ""
    if focus:
        extra = f"\nThe traveler is focused on destination card id: {focus!r} (name may appear in the message).\n"
    prompt = _INTENT_PROMPT + extra + "\nUser line:\n" + um[:6000]
    try:
        with basic_client(get_api_key(), timeout_s=90.0) as client:
            raw = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=mid,
                persistent_instruction_prefix=_INTENT_PROMPT,
                llm_telemetry_activity=ACTIVITY_DESTINATION_FOLLOWUP,
                llm_telemetry_sub_activity="user_intent",
                llm_telemetry_meta=telemetry_meta(
                    session_id=session_id,
                    focus_destination_id=focus_destination_id,
                ),
                **cursor_run_persist_kwargs_for_lane(
                    session_id or "", _CURSOR_LANE_INTENT, trace_path="wanderly.user_intent_llm"
                ),
            )
    except Exception as exc:
        log_itinerary_build(
            f"path=wanderly.user_intent_llm FAIL sid={(session_id or '')[:12]!r} exc={type(exc).__name__}:{exc!s}"
        )
        return None
    parsed = _extract_json_object(raw)
    if not parsed:
        log_itinerary_build(f"path=wanderly.user_intent_llm no_json sid={(session_id or '')[:12]!r}")
        return None
    try:
        dto = WanderlyIntentClassification.model_validate(parsed)
    except ValidationError:
        log_itinerary_build(f"path=wanderly.user_intent_llm invalid_schema sid={(session_id or '')[:12]!r}")
        return None
    log_itinerary_build(
        f"path=wanderly.user_intent_llm ok sid={(session_id or '')[:12]!r} action={dto.workspace_action!r} "
        f"conf={dto.confidence:.2f} rationale={dto.rationale[:120]!r}"
    )
    return dto


def run_orchestrate_turn(
    *,
    user_message: str,
    session_id: str,
    focus_destination_id: str | None = None,
) -> dict[str, Any]:
    """HTTP handler helper: JSON-serializable orchestration payload."""
    dto = classify_user_intent_llm(
        user_message=user_message,
        session_id=session_id,
        focus_destination_id=focus_destination_id,
    )
    if dto is None:
        return {
            "session_id": session_id,
            "parse_ok": False,
            "orchestration": None,
            "fallback_reason": "intent_unavailable_or_invalid",
        }
    return {
        "session_id": session_id,
        "parse_ok": True,
        "orchestration": dto.model_dump(),
    }


# Back-compat for callers that still import the old name
def maybe_classify_user_intent_llm(*, user_message: str, session_id: str) -> WanderlyIntentClassification | None:
    return classify_user_intent_llm(user_message=user_message, session_id=session_id, focus_destination_id=None)
