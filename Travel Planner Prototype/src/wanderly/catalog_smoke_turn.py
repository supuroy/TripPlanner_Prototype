"""
Catalog-grounded first smoke turn (outdoor / backpacking) using offline trail JSON.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from src.wanderly.itinerary_debug_log import log_itinerary_build
from src.wanderly.outdoor_adventure import DEFAULT_TRIP_STYLE, infer_trip_style_from_text
from src.wanderly.schemas import SmokeProfilePillDTO, WanderlyWorkspaceDTO
from src.wanderly.smoke_service import (
    _infer_origin_from_message,
    _merge_profile_with_inference,
    _minimal_dest_card,
)
from src.wanderly.trail_catalog.pipeline import (
    destination_card_from_trail,
    full_catalog_entry,
    llm_build_itinerary,
    llm_select_trails,
)
from src.wanderly.trail_catalog.store import (
    heuristic_pick_trails,
    load_park_trails_compact,
    prefilter_backpacking_candidates,
)
from src.wanderly.trip_intent_resolve import TripIntentResolution
from src.wanderly.trip_intent_trace import dump_trip_intent_debug, log_trip_intent


def parse_trip_length_from_text(text: str, *, default_days: int = 4) -> tuple[int, int]:
    um = text or ""
    dm = re.search(r"\b(\d{1,2})\s*[- ]?\s*day", um, re.I)
    nm = re.search(r"\b(\d{1,2})\s*[- ]?\s*night", um, re.I)
    days = int(dm.group(1)) if dm else default_days
    nights = int(nm.group(1)) if nm else max(1, days - 1)
    return max(1, min(days, 21)), max(0, min(nights, 20))


def _profile_for_catalog(user_message: str, resolution: TripIntentResolution) -> dict[str, Any]:
    origin = _infer_origin_from_message(user_message) or "Not specified"
    style = infer_trip_style_from_text(user_message)
    if resolution.trip_product == "outdoor_camping":
        trip_style = "car camping"
    elif resolution.trip_product == "outdoor_backpacking":
        trip_style = "backpacking"
    else:
        trip_style = "backpacking" if style == "Backpacking / multi-day hike" else "hiking"
    return {
        "traveler_origin": origin,
        "group_size": 2,
        "group_type": "couple",
        "pace_preference": "moderate",
        "trip_style": trip_style,
        "preferred_activities": ["hiking", "backcountry camping"],
        "destination_preferences": resolution.destination_hints[:5],
    }


def _default_profile_pills(user_message: str, resolution: TripIntentResolution) -> list[SmokeProfilePillDTO]:
    origin = _infer_origin_from_message(user_message) or "Not specified"
    days, nights = parse_trip_length_from_text(user_message)
    trip_style = infer_trip_style_from_text(user_message)
    if trip_style == DEFAULT_TRIP_STYLE and resolution.trip_product.startswith("outdoor"):
        trip_style = "Backpacking / multi-day hike"
    park = resolution.primary_park_name or (resolution.destination_hints[0] if resolution.destination_hints else "National park")
    return [
        SmokeProfilePillDTO(key="when", label="When", value="Dates TBD", icon="📅"),
        SmokeProfilePillDTO(key="who", label="Who", value="2 travelers", icon="👥"),
        SmokeProfilePillDTO(key="from", label="Flying from", value=origin[:120], icon="✈️"),
        SmokeProfilePillDTO(
            key="vibe",
            label="Vibe",
            value=f"Backcountry hiking — {park}"[:120],
            icon="✨",
        ),
        SmokeProfilePillDTO(key="trip_style", label="Trip style", value=trip_style[:120], icon="🥾"),
        SmokeProfilePillDTO(key="budget", label="Budget", value="Flexible", icon="💰"),
        SmokeProfilePillDTO(
            key="duration",
            label="Duration",
            value=f"{days} days / {nights} nights"[:120],
            icon="⏱",
        ),
    ]


def _catalog_card_shell(
    entry: dict[str, Any],
    *,
    reason: str,
    park_code: str,
    itinerary_rows: list[dict[str, Any]] | None,
    outdoor_adventure: dict[str, Any] | None,
) -> dict[str, Any]:
    base = destination_card_from_trail(entry, reason=reason, park_code=park_code)
    shell = _minimal_dest_card(base)
    for key in (
        "id",
        "name",
        "country",
        "hook",
        "grounding",
        "tags",
        "distance_mi",
        "difficulty",
        "region",
    ):
        if key in base:
            shell[key] = base[key]
    if itinerary_rows:
        shell["itinerary"] = itinerary_rows
        shell["itinerary_dirty"] = False
    else:
        shell["itinerary"] = []
        shell["itinerary_dirty"] = True
    if outdoor_adventure:
        shell["outdoor_adventure"] = outdoor_adventure
    return shell


def build_catalog_workspace(
    *,
    user_message: str,
    session_id: str,
    resolution: TripIntentResolution,
    emit: Callable[[dict[str, Any]], None] | None = None,
    pick_count: int = 3,
    build_itineraries: bool = True,
) -> WanderlyWorkspaceDTO:
    park_code = (resolution.primary_park_code or "").strip().lower()
    if not park_code:
        raise ValueError("catalog_smoke_turn requires primary_park_code")

    def safe_emit(obj: dict[str, Any]) -> None:
        if emit:
            emit(obj)

    safe_emit({"type": "phase", "message": f"Loading trail catalog for {resolution.primary_park_name or park_code}…"})
    doc, compact = load_park_trails_compact(park_code)
    candidates = prefilter_backpacking_candidates(compact)
    profile = _profile_for_catalog(user_message, resolution)
    days, nights = parse_trip_length_from_text(user_message)
    k = max(1, min(pick_count, 4))

    safe_emit({"type": "phase", "message": "Choosing catalog-backed trails…"})
    picks = llm_select_trails(
        park_code=park_code,
        park_trails=candidates,
        user_message=user_message,
        profile=profile,
        k=k,
        session_id=session_id,
    )
    if not picks:
        picks = heuristic_pick_trails(candidates, k=k)
        for p in picks:
            p.setdefault("reason", "Heuristic catalog pick (trail picker unavailable).")

    destinations: list[dict[str, Any]] = []
    for i, pick in enumerate(picks, 1):
        tid = str(pick.get("trail_id") or "")
        full = full_catalog_entry(doc, tid) or pick
        reason = str(pick.get("reason") or "").strip()
        itin_rows: list[dict[str, Any]] | None = None
        outdoor_adv: dict[str, Any] | None = None
        if build_itineraries:
            safe_emit(
                {
                    "type": "phase",
                    "message": f"Building day plan {i}/{len(picks)}: {full.get('name', tid)}…",
                }
            )
            dest_stub = destination_card_from_trail(full, reason=reason, park_code=park_code)
            built = llm_build_itinerary(
                selected_trail=full,
                destination=dest_stub,
                user_message=user_message,
                profile=profile,
                days=days,
                nights=nights,
                session_id=session_id,
            )
            if isinstance(built, dict):
                raw_it = built.get("itinerary")
                if isinstance(raw_it, list) and raw_it:
                    itin_rows = [x for x in raw_it if isinstance(x, dict)]
                oa = built.get("outdoor_adventure")
                if isinstance(oa, dict):
                    outdoor_adv = oa
        destinations.append(
            _catalog_card_shell(
                full,
                reason=reason,
                park_code=park_code,
                itinerary_rows=itin_rows,
                outdoor_adventure=outdoor_adv,
            )
        )

    park_label = resolution.primary_park_name or park_code.upper()
    names = ", ".join(str(d.get("name") or "") for d in destinations[:3])
    md = (
        f"Here are **{len(destinations)} catalog-grounded backpacking options** in **{park_label}**. "
        f"Each card is one named trail route ({names}). "
        "Compare mileage and difficulty, then refine dates or swap trails."
    )
    dto = WanderlyWorkspaceDTO(
        assistant_markdown=md,
        profile_pills=_default_profile_pills(user_message, resolution),
        destinations=destinations,
        agent_status_hint="Catalog-grounded outdoor planner",
    )
    dto = _merge_profile_with_inference(dto, user_message)
    log_itinerary_build(
        f"path=wanderly.catalog_smoke_turn park={park_code!r} cards={len(destinations)} "
        f"sid={(session_id or '')[:12]!r}"
    )
    log_trip_intent(
        f"catalog_smoke_turn park={park_code!r} cards={len(destinations)} "
        f"inline_itins={sum(1 for d in destinations if (d.get('itinerary') or []))} "
        f"sid={(session_id or '')[:12]!r}"
    )
    dump_trip_intent_debug(
        f"catalog_smoke_{park_code}",
        {
            "session_id": session_id,
            "park_code": park_code,
            "resolution": resolution.model_dump(),
            "destination_ids": [str(d.get("id")) for d in destinations],
            "itinerary_days": {
                str(d.get("id")): len(d.get("itinerary") or [])
                for d in destinations
                if isinstance(d, dict)
            },
        },
    )
    return dto
