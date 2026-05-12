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
from src.wanderly.cursor_advisor import advisor_model_id, structured_followup_model_id
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
  "focus_destination_ids": ["optional-destination-id-slugs"],
  "lock_canvas_navigation": true
}

Also set:
- **itinerary_refresh**:
  - **none** — sidebar/workspace wording only; no day-by-day itinerary rebuild.
  - **all** — trip shape or multi-card change; every card may need a new lazy itinerary.
  - **focus_only** — only one (or listed) destination(s) need rebuild; set **focus_destination_ids**.
  - **auto** — client may fall back to lighter heuristics.
- **focus_destination_ids**: workspace destination ids when refreshing a subset (e.g. ["costa-rica"]).
- **lock_canvas_navigation**: **false** when the user clearly edits **one named card** and others should stay browsable during the run; **true** for broad replans.

Rules:
- **refine_workspace**: user wants to change the shortlist, dates, budget, vibe, pins, or multiple cards materially (not only compare).
- **targeted_itinerary_edit**: redo/rebuild/regenerate/refresh **one** destination's day-by-day plan; not removing other cards.
- **compare_places**: choose between / compare / vs / pros and cons without asking to change itineraries.
- **lightweight_qa**: general travel question that does not require rewriting the workspace JSON.
- **explicit_remove_destinations**: user clearly asks to remove, delete, or drop named destination(s) from the list (product should confirm before shrinking the board).
- **ambiguous**: cannot tell confidently.

Prefer **targeted_itinerary_edit** when the message names one place and asks to redo/rebuild only its itinerary.
When a ``focus_destination_id`` is provided in the instructions, bias toward **targeted_itinerary_edit** when that place is the subject of a redo/rebuild request.
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


def classify_user_intent_llm(
    *,
    user_message: str,
    session_id: str,
    focus_destination_id: str | None = None,
) -> WanderlyIntentClassification | None:
    """
    Run a small structured Cursor call. Returns ``None`` if no model id, network failure, or invalid JSON.
    """
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
