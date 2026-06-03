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
from src.wanderly.trail_catalog.intent_pick import (
    pick_trails_for_intent,
    resolve_trail_entry_for_card,
)
from src.wanderly.trail_catalog.pipeline import (
    destination_card_from_trail,
    full_catalog_entry,
    llm_select_trails,
)
from src.wanderly.trail_catalog.store import (
    heuristic_pick_trails,
    load_park_catalog,
    load_park_trails_compact,
    prefilter_backpacking_candidates,
)
from src.wanderly.trail_catalog_trace import dump_trail_catalog_debug, log_trail_catalog
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


def _build_cards_from_picks(
    *,
    picks: list[dict[str, Any]],
    park_code: str | None,
    pick_meta: dict[str, Any] | None = None,
    session_id: str = "",
) -> list[dict[str, Any]]:
    """Build catalog-grounded destination cards; itineraries load via lazy ``catalog_itinerary``."""
    destinations: list[dict[str, Any]] = []
    for pick in picks:
        tid = str(pick.get("trail_id") or "")
        full = resolve_trail_entry_for_card(pick)
        if park_code and not full.get("park_code"):
            pc = park_code
        else:
            pc = str(full.get("park_code") or park_code or "").strip().lower() or None
        if park_code and pc == park_code:
            try:
                doc_entry = full_catalog_entry(load_park_catalog(pc), tid)
            except (FileNotFoundError, ValueError):
                doc_entry = None
            if doc_entry:
                full = doc_entry
        elif pc and tid:
            try:
                doc_entry = full_catalog_entry(load_park_catalog(pc), tid)
            except (FileNotFoundError, ValueError):
                doc_entry = None
            if doc_entry:
                full = doc_entry
        reason = str(pick.get("reason") or "").strip()
        destinations.append(
            _catalog_card_shell(
                full,
                reason=reason,
                park_code=pc or "",
                itinerary_rows=None,
                outdoor_adventure=None,
            )
        )
    dump_trail_catalog_debug(
        f"catalog_cards_{(session_id or 'anon')[:12]}",
        {
            "session_id": session_id,
            "pick_meta": pick_meta,
            "destination_ids": [str(d.get("id")) for d in destinations],
        },
    )
    return destinations


def build_intent_catalog_workspace(
    *,
    user_message: str,
    session_id: str,
    resolution: TripIntentResolution,
    emit: Callable[[dict[str, Any]], None] | None = None,
    pick_count: int = 3,
) -> WanderlyWorkspaceDTO:
    """Catalog lane driven by unified intent search (region/feature/state), not a single park file."""

    def safe_emit(obj: dict[str, Any]) -> None:
        if emit:
            emit(obj)

    safe_emit({"type": "phase", "message": "Searching trail catalog for your request…"})
    profile = _profile_for_catalog(user_message, resolution)
    k = max(1, min(pick_count, 4))

    picks, pick_meta = pick_trails_for_intent(
        user_message=user_message,
        profile=profile,
        resolution=resolution,
        session_id=session_id,
        k=k,
    )
    if not picks:
        raise ValueError("intent catalog search returned no trail picks")

    safe_emit(
        {
            "type": "phase",
            "message": f"Shortlisting {len(picks)} catalog-grounded trail option(s)…",
        }
    )
    destinations = _build_cards_from_picks(
        picks=picks,
        park_code=None,
        pick_meta=pick_meta,
        session_id=session_id,
    )

    scope_label = "your search area"
    if resolution.intent_scope:
        states = resolution.intent_scope.get("state_slugs") or []
        features = resolution.intent_scope.get("feature_slugs") or []
        if states:
            scope_label = ", ".join(str(s) for s in states[:2])
        elif features:
            scope_label = ", ".join(str(f) for f in features[:2])
    names = ", ".join(str(d.get("name") or "") for d in destinations[:3])
    md = (
        f"Here are **{len(destinations)} catalog-grounded trail options** for **{scope_label}**. "
        f"Each card is one named route ({names}). "
        f"Picked via **{pick_meta.get('pick_method', 'catalog')}** from offline catalog search. "
        "Day-by-day plans build in the background for each trail."
    )
    dto = WanderlyWorkspaceDTO(
        assistant_markdown=md,
        profile_pills=_default_profile_pills(user_message, resolution),
        destinations=destinations,
        agent_status_hint="Catalog intent search (offline)",
    )
    dto = _merge_profile_with_inference(dto, user_message)
    log_itinerary_build(
        f"path=wanderly.catalog_intent lane=intent cards={len(destinations)} "
        f"method={pick_meta.get('pick_method')} sid={(session_id or '')[:12]!r}"
    )
    log_trail_catalog(
        f"intent_workspace cards={len(destinations)} method={pick_meta.get('pick_method')} "
        f"sid={(session_id or '')[:12]!r}"
    )
    log_trip_intent(
        f"catalog_intent_turn cards={len(destinations)} method={pick_meta.get('pick_method')} "
        f"sid={(session_id or '')[:12]!r}"
    )
    dump_trip_intent_debug(
        "catalog_intent_turn",
        {
            "session_id": session_id,
            "resolution": resolution.model_dump(),
            "pick_meta": pick_meta,
            "destination_ids": [str(d.get("id")) for d in destinations],
        },
    )
    return dto


def build_catalog_workspace(
    *,
    user_message: str,
    session_id: str,
    resolution: TripIntentResolution,
    emit: Callable[[dict[str, Any]], None] | None = None,
    pick_count: int = 3,
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

    destinations = _build_cards_from_picks(
        picks=picks,
        park_code=park_code,
        pick_meta={"pick_method": "park_lane", "park_code": park_code},
        session_id=session_id,
    )

    park_label = resolution.primary_park_name or park_code.upper()
    names = ", ".join(str(d.get("name") or "") for d in destinations[:3])
    md = (
        f"Here are **{len(destinations)} catalog-grounded backpacking options** in **{park_label}**. "
        f"Each card is one named trail route ({names}). "
        "Compare mileage and difficulty, then refine dates or swap trails. "
        "Day-by-day plans build in the background for each trail."
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
    log_trail_catalog(
        f"park_workspace park={park_code!r} cards={len(destinations)} sid={(session_id or '')[:12]!r}"
    )
    log_trip_intent(
        f"catalog_smoke_turn park={park_code!r} cards={len(destinations)} "
        f"lazy_dirty={sum(1 for d in destinations if d.get('itinerary_dirty'))} "
        f"sid={(session_id or '')[:12]!r}"
    )
    dump_trip_intent_debug(
        f"catalog_smoke_{park_code}",
        {
            "session_id": session_id,
            "park_code": park_code,
            "resolution": resolution.model_dump(),
            "destination_ids": [str(d.get("id")) for d in destinations],
            "lazy_itinerary": True,
            "grounding": {
                str(d.get("id")): d.get("grounding")
                for d in destinations
                if isinstance(d, dict)
            },
        },
    )
    return dto
