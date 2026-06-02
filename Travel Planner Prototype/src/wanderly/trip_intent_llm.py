"""
First-pass trip product routing (leisure vs outdoor) and destination labels.

Runs before the smoke planner. Park codes are resolved server-side from ``manifest.json``
and NPS aliases — the LLM returns place names only.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from src.integrations.cursor_agent_client import (
    basic_client,
    get_api_key,
    load_env_from_roots,
    run_prompt_collect_assistant_text,
)
from src.wanderly.cursor_advisor import advisor_model_id, structured_followup_model_id
from src.wanderly.itinerary_debug_log import log_itinerary_build
from src.wanderly.llm_telemetry import ACTIVITY_ITINERARY_BUILDER, telemetry_meta
from src.wanderly.outdoor_adventure import DEFAULT_TRIP_STYLE, infer_trip_style_from_text
from src.wanderly.session_store import cursor_run_persist_kwargs_for_lane
from src.wanderly.trip_intent_trace import dump_trip_intent_debug, log_trip_intent

TripProduct = Literal[
    "leisure",
    "outdoor_backpacking",
    "outdoor_camping",
    "outdoor_general",
]

_CURSOR_LANE = "wanderly_trip_intent"


class TripIntentClassification(BaseModel):
    trip_product: TripProduct = "leisure"
    destination_hints: list[str] = Field(
        default_factory=list,
        description="Place or park names only (no park codes).",
    )
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    rationale: str = Field(default="", max_length=400)


_INTENT_PROMPT = """
You classify a traveler's first trip-planning message for routing.

Reply with ONE JSON object only (no markdown):
{
  "trip_product": "leisure|outdoor_backpacking|outdoor_camping|outdoor_general",
  "destination_hints": ["Yellowstone National Park", "Iceland"],
  "confidence": 0.0,
  "rationale": "short"
}

Rules:
- **destination_hints**: human place or park names the traveler named or clearly implied (0–5 strings).
  Do NOT emit NPS park codes (yell, yose) or trail slugs.
- **outdoor_backpacking**: backpacking, multi-day wilderness hikes, thru-hikes, backcountry permits.
- **outdoor_camping**: car camping, campground-focused trips without long wilderness carries.
- **outdoor_general**: hiking/day hikes/outdoor adventure without clear backpacking vs car-camp split.
- **leisure**: city breaks, international leisure, resorts, food/wine, beaches, generic vacation without trail focus.
- When unsure between outdoor and leisure, prefer **leisure** unless trail/backcountry language is clear.
""".strip()


def trip_intent_llm_enabled() -> bool:
    raw = os.environ.get("WANDERLY_TRIP_INTENT_LLM", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _intent_model_id() -> str | None:
    return advisor_model_id() or structured_followup_model_id()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    t = (text or "").strip()
    if not t:
        return None
    try:
        out = json.loads(t)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.I)
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


def _clean_hints(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw[:5]:
        s = str(x or "").strip()
        if s and s not in out:
            out.append(s[:120])
    return out


def fast_path_trip_intent(user_message: str) -> TripIntentClassification | None:
    """Keyword / trip_style heuristic when LLM is off or unavailable."""
    um = (user_message or "").strip()
    if not um:
        return None
    style = infer_trip_style_from_text(um)
    low = um.lower()
    if style == "Backpacking / multi-day hike":
        product: TripProduct = "outdoor_backpacking"
        conf = 0.85
    elif re.search(r"\b(car\s+camp|campground|camping\s+trip)\b", low):
        product = "outdoor_camping"
        conf = 0.82
    elif re.search(
        r"\b(national\s+park|backcountry|wilderness|day\s+hike|hiking\s+trip|trail)\b",
        low,
    ):
        product = "outdoor_general"
        conf = 0.75
    else:
        return None
    hints = _hints_from_message_heuristic(um)
    return TripIntentClassification(
        trip_product=product,
        destination_hints=hints,
        confidence=conf,
        rationale="fast_path_keywords",
    )


def _hints_from_message_heuristic(user_message: str) -> list[str]:
    """Pull obvious park/place phrases without an LLM."""
    from src.wanderly.nps_provider import _PARK_CODE_ALIASES

    low = (user_message or "").lower()
    hints: list[str] = []
    for phrase in sorted(_PARK_CODE_ALIASES, key=len, reverse=True):
        if phrase in low and phrase not in {h.lower() for h in hints}:
            # Title-case-ish label for display / manifest match
            label = phrase.title() if len(phrase) > 3 else phrase
            if "national park" not in label and phrase in (
                "yellowstone",
                "yosemite",
                "zion",
                "glacier",
                "arches",
                "bryce",
                "grand canyon",
                "rocky mountain",
            ):
                label = f"{label.title()} National Park"
            hints.append(label[:120])
            if len(hints) >= 3:
                break
    m = re.search(
        r"(?i)(?:trip|hike|backpack(?:ing)?|visit)\s+(?:in|to|at)\s+([A-Z][A-Za-z0-9\s,'-]{3,60}?)(?:\s+for\b|\s+with\b|[.;,]|$)",
        user_message or "",
    )
    if m:
        seg = m.group(1).strip()
        if seg and seg.lower() not in {h.lower() for h in hints}:
            hints.append(seg[:120])
    return hints[:5]


def classify_trip_intent_llm(
    *,
    user_message: str,
    session_id: str,
) -> TripIntentClassification:
    """
    Classify trip product + destination hints. Falls back to leisure on failure.
    """
    um = (user_message or "").strip()
    if not um:
        return TripIntentClassification(
            trip_product="leisure",
            destination_hints=[],
            confidence=0.5,
            rationale="empty_message",
        )

    fast = fast_path_trip_intent(um)
    if fast is not None and not trip_intent_llm_enabled():
        log_itinerary_build(
            f"path=wanderly.trip_intent_llm fast_only sid={(session_id or '')[:12]!r} "
            f"product={fast.trip_product!r}"
        )
        return fast

    dto: TripIntentClassification | None = None
    if trip_intent_llm_enabled():
        mid = _intent_model_id()
        if mid:
            load_env_from_roots()
            prompt = _INTENT_PROMPT + "\n\nUser message:\n" + um[:6000]
            try:
                with basic_client(get_api_key(), timeout_s=90.0) as client:
                    raw = run_prompt_collect_assistant_text(
                        client,
                        prompt,
                        model_id_override=mid,
                        persistent_instruction_prefix=_INTENT_PROMPT,
                        llm_telemetry_activity=ACTIVITY_ITINERARY_BUILDER,
                        llm_telemetry_sub_activity="trip_intent",
                        llm_telemetry_meta=telemetry_meta(session_id=session_id),
                        **cursor_run_persist_kwargs_for_lane(
                            session_id or "",
                            _CURSOR_LANE,
                            trace_path="wanderly.trip_intent_llm.json",
                        ),
                    )
                parsed = _extract_json_object(raw)
                if parsed:
                    dto = TripIntentClassification.model_validate(
                        {
                            **parsed,
                            "destination_hints": _clean_hints(parsed.get("destination_hints")),
                        }
                    )
            except Exception as exc:
                log_itinerary_build(
                    f"path=wanderly.trip_intent_llm FAIL sid={(session_id or '')[:12]!r} "
                    f"exc={type(exc).__name__}:{exc!s}"
                )
        else:
            log_itinerary_build("path=wanderly.trip_intent_llm skip no_model_id")

    if dto is None:
        dto = fast or TripIntentClassification(
            trip_product="leisure",
            destination_hints=_hints_from_message_heuristic(um),
            confidence=0.55,
            rationale="default_leisure",
        )
        if infer_trip_style_from_text(um) != DEFAULT_TRIP_STYLE and dto.trip_product == "leisure":
            fp = fast_path_trip_intent(um)
            if fp is not None:
                dto = fp

    log_itinerary_build(
        f"path=wanderly.trip_intent_llm ok sid={(session_id or '')[:12]!r} "
        f"product={dto.trip_product!r} hints={dto.destination_hints!r} conf={dto.confidence:.2f}"
    )
    log_trip_intent(
        f"classify sid={(session_id or '')[:12]!r} product={dto.trip_product!r} "
        f"hints={dto.destination_hints!r} conf={dto.confidence:.2f} rationale={dto.rationale[:80]!r}"
    )
    dump_trip_intent_debug(
        f"classify_{(session_id or 'anon')[:8]}",
        {"session_id": session_id, "classification": dto.model_dump()},
    )
    return dto


def is_outdoor_trip_product(product: str) -> bool:
    return str(product or "").strip().lower().startswith("outdoor_")


def leisure_destination_context_block(intent: TripIntentClassification) -> str:
    """Extra planner context for leisure lane (named destinations only)."""
    hints = [h for h in intent.destination_hints if h.strip()]
    if not hints:
        return ""
    joined = "; ".join(hints[:5])
    return (
        f"\n\n**Destination intent (traveler-named places — expand into cards):** {joined}\n"
        "- Suggest 1–5 destinations aligned with these places and the trip style in profile_pills.\n"
        "- Do not invent unrelated regions unless the user asked for alternatives."
    )
