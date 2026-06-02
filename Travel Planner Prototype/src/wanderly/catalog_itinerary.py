"""
Catalog-grounded lazy itinerary builds for destinations with ``grounding.status=catalog``.

Uses ``trail_catalog.pipeline.llm_build_itinerary`` then the same post-processing as
``itinerary_lazy`` (trail authority, camp sync, QA, transport hints).
"""

from __future__ import annotations

import time
from typing import Any, Callable

from src.wanderly.catalog_smoke_turn import parse_trip_length_from_text
from src.wanderly.itinerary_debug_log import log_itinerary_build
from src.wanderly.outdoor_adventure import (
    is_backpacking_or_multiday_hike,
    normalize_outdoor_adventure,
    profile_pill_value,
)
from src.wanderly.session_store import chat_context_snippet, get_trip_intent
from src.wanderly.trail_catalog.pipeline import llm_build_itinerary
from src.wanderly.trail_catalog.store import (
    entry_by_trail_id,
    load_park_catalog,
    park_catalog_path,
)
from src.wanderly.trail_catalog.validate import validate_itinerary_against_catalog
from src.wanderly.trip_intent_trace import dump_trip_intent_debug, log_trip_intent


def catalog_grounding(destination: dict[str, Any]) -> dict[str, Any] | None:
    g = destination.get("grounding")
    return g if isinstance(g, dict) else None


def is_catalog_grounded_destination(destination: dict[str, Any]) -> bool:
    g = catalog_grounding(destination)
    if not g:
        return False
    if str(g.get("status") or "").strip().lower() == "catalog":
        return bool(str(g.get("trail_id") or "").strip())
    return bool(str(g.get("trail_id") or "").strip() and str(g.get("source_url") or "").strip())


def resolve_park_code_for_destination(
    destination: dict[str, Any],
    *,
    session_id: str | None = None,
) -> str | None:
    g = catalog_grounding(destination) or {}
    pc = str(g.get("park_code") or "").strip().lower()
    if pc and park_catalog_path(pc).is_file():
        return pc
    if session_id:
        intent = get_trip_intent(session_id)
        if isinstance(intent, dict):
            res = intent.get("resolution")
            if isinstance(res, dict):
                primary = str(res.get("primary_park_code") or "").strip().lower()
                if primary and park_catalog_path(primary).is_file():
                    return primary
    from src.wanderly.trail_catalog.store import resolve_park_code_from_text

    blob = " ".join(
        str(destination.get(k) or "")
        for k in ("name", "country", "region", "hook")
    )
    return resolve_park_code_from_text(blob)


def _days_nights_from_profile(
    profile_pills: list[dict[str, Any]],
    *,
    days_param: int,
    extra_text: str = "",
) -> tuple[int, int]:
    n = max(3, min(int(days_param), 14))
    dur = profile_pill_value(profile_pills, "duration")
    blob = " ".join(x for x in (dur, extra_text) if x)
    if blob.strip():
        d, nights = parse_trip_length_from_text(blob, default_days=n)
        return max(3, min(d, 14)), nights
    return n, max(1, n - 1)


def _profile_dict_for_catalog(
    profile_pills: list[dict[str, Any]],
    *,
    user_message: str,
) -> dict[str, Any]:
    origin = profile_pill_value(profile_pills, "from") or "Not specified"
    style = profile_pill_value(profile_pills, "trip_style") or "backpacking"
    return {
        "traveler_origin": origin,
        "group_size": 2,
        "group_type": "couple",
        "pace_preference": "moderate",
        "trip_style": style.lower(),
        "preferred_activities": ["hiking", "backcountry camping"],
        "user_message": user_message[:2000],
    }


def _user_message_for_build(
    session_id: str | None,
    refinement_message: str | None,
) -> str:
    rm = (refinement_message or "").strip()
    if rm:
        return rm
    if session_id:
        chat = chat_context_snippet(session_id, max_messages=4)
        for line in reversed(chat.split("\n")):
            if line.lower().startswith("user:"):
                return line.split(":", 1)[-1].strip()[:2000]
    return "Backpacking trip"


def _postprocess_catalog_rows(
    *,
    itinerary_rows: list[dict[str, Any]],
    catalog_payload: dict[str, Any],
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    n: int,
    trace_rid: str,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    from src.wanderly.day_logistics import sync_camp_nights_to_itinerary_rows, sync_walk_surface_legs_to_itinerary_rows
    from src.wanderly.itinerary_lazy import (
        _enforce_comfortable_hops,
        _merge_wrapper_parallel_day_logistics,
        _normalize_itinerary_rows,
        _outdoor_adventure_for_itinerary_response,
        enrich_itinerary_rows_lodging_from_profile,
    )
    from src.wanderly.trail_authority import enforce_trail_authority_for_destination

    ext_list = [x for x in itinerary_rows if isinstance(x, dict)]
    out = _normalize_itinerary_rows(ext_list, max_days=n)
    wrapper_hint = {
        "outdoor_adventure": catalog_payload.get("outdoor_adventure"),
        "grounding": catalog_payload.get("grounding"),
    }
    out = _merge_wrapper_parallel_day_logistics(out, wrapper_hint)
    out = _enforce_comfortable_hops(out, max_days=n, profile_pills=profile_pills, destination=destination)
    out = enrich_itinerary_rows_lodging_from_profile(out, profile_pills)
    out, trail_warnings = enforce_trail_authority_for_destination(out, destination)
    for w in trail_warnings:
        log_itinerary_build(f"TRAIL_AUTHORITY catalog rid={trace_rid} {w}")
    oa = _outdoor_adventure_for_itinerary_response(wrapper_hint, destination)
    if not oa:
        oa = normalize_outdoor_adventure(catalog_payload.get("outdoor_adventure"))
    out = sync_camp_nights_to_itinerary_rows(out, oa, destination=destination)
    out = sync_walk_surface_legs_to_itinerary_rows(out, outdoor_adventure=oa)
    dest_qa = {**destination, "itinerary": out}
    from src.wanderly.backpacking_itinerary_qa import apply_backpacking_itinerary_qa

    dest_qa = apply_backpacking_itinerary_qa(dest_qa, profile_pills=profile_pills)
    out = dest_qa.get("itinerary") if isinstance(dest_qa.get("itinerary"), list) else out
    return out, oa, wrapper_hint


def build_catalog_itinerary_for_destination(
    *,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    days: int,
    session_id: str | None = None,
    request_id: str | None = None,
    refinement_message: str | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """
    Build day-by-day plan from offline trail catalog + catalog itinerary LLM.
    Returns the same response shape as ``itinerary_lazy.build_itinerary_for_destination``.
    """
    from src.wanderly.itinerary_lazy import (
        _alltrails_context_for_itinerary_response,
        _itinerary_client_response,
        _itinerary_qa_for_response,
        ensure_transport_plan_after_extract,
    )

    rid = (request_id or "?").strip() or "?"
    did = str(destination.get("id") or "")
    g = catalog_grounding(destination) or {}
    trail_id = str(g.get("trail_id") or "").strip()
    park_code = resolve_park_code_for_destination(destination, session_id=session_id)

    def safe_emit(msg: str) -> None:
        log_trip_intent(f"phase rid={rid} dest_id={did!r} {msg}")
        if emit:
            emit({"type": "phase", "message": msg})

    log_trip_intent(
        f"BEGIN catalog_itinerary rid={rid} sid={(session_id or '')[:12]!r} "
        f"dest_id={did!r} trail_id={trail_id!r} park_code={park_code!r}"
    )
    t0 = time.perf_counter()

    if not trail_id or not park_code:
        log_trip_intent(f"SKIP catalog_itinerary rid={rid} missing trail_id or park_code")
        raise ValueError("Catalog itinerary requires grounding.trail_id and a resolvable park_code")

    safe_emit("Loading trail catalog…")
    doc = load_park_catalog(park_code)
    entry = entry_by_trail_id(doc, trail_id)
    if not entry:
        log_trip_intent(f"FAIL catalog_itinerary rid={rid} trail_id={trail_id!r} not in park={park_code!r}")
        raise ValueError(f"Trail {trail_id!r} not found in catalog for park {park_code!r}")

    user_message = _user_message_for_build(session_id, refinement_message)
    days_n, nights_n = _days_nights_from_profile(
        profile_pills,
        days_param=days,
        extra_text=user_message,
    )
    profile = _profile_dict_for_catalog(profile_pills, user_message=user_message)
    dest_stub = {
        "id": did,
        "name": destination.get("name") or entry.get("name"),
        "country": destination.get("country") or "United States",
        "hook": destination.get("hook") or "",
        "grounding": g,
    }

    safe_emit(f"Building catalog-grounded plan ({days_n} days)…")
    catalog_payload = llm_build_itinerary(
        selected_trail=entry,
        destination=dest_stub,
        user_message=user_message,
        profile=profile,
        days=days_n,
        nights=nights_n,
        session_id=session_id or "catalog-itinerary",
    )

    if not isinstance(catalog_payload, dict):
        log_trip_intent(f"EMPTY catalog_itinerary rid={rid} llm returned no JSON")
        return _itinerary_client_response(itinerary_rows=[])

    validation_errors = validate_itinerary_against_catalog(catalog_payload, catalog_entry=entry)
    if validation_errors:
        log_trip_intent(
            f"VALIDATION catalog_itinerary rid={rid} warnings={validation_errors!r}"
        )

    raw_rows = catalog_payload.get("itinerary")
    rows_in = [x for x in raw_rows if isinstance(x, dict)] if isinstance(raw_rows, list) else []
    if catalog_payload.get("grounding") and isinstance(catalog_payload["grounding"], dict):
        g = {**g, **catalog_payload["grounding"]}
        dest_stub["grounding"] = g
        destination = {**destination, "grounding": g}

    out, outdoor_adv, _wrapper = _postprocess_catalog_rows(
        itinerary_rows=rows_in,
        catalog_payload=catalog_payload,
        destination=destination,
        profile_pills=profile_pills,
        n=days_n,
        trace_rid=rid,
    )

    transport_plan = None
    needs_cf = None
    booking_hints = None
    budget = None
    if out:
        transport_plan, needs_cf, booking_hints = ensure_transport_plan_after_extract(
            transport_plan=destination.get("transport_plan") if isinstance(destination.get("transport_plan"), dict) else None,
            needs_commercial_flight=destination.get("needs_commercial_flight")
            if isinstance(destination.get("needs_commercial_flight"), bool)
            else None,
            booking_search_hints=destination.get("booking_search_hints")
            if isinstance(destination.get("booking_search_hints"), dict)
            else None,
            budget_guidance=None,
            itinerary_rows=out,
            destination=destination,
            profile_pills=profile_pills,
            raw_wrapper=_wrapper,
        )

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    log_trip_intent(
        f"END catalog_itinerary rid={rid} dest_id={did!r} days={len(out)} "
        f"validation_n={len(validation_errors)} elapsed_ms={elapsed_ms:.1f}"
    )
    dump_trip_intent_debug(
        f"catalog_itinerary_{did}_{rid}",
        {
            "request_id": rid,
            "session_id": session_id,
            "destination_id": did,
            "park_code": park_code,
            "trail_id": trail_id,
            "catalog_entry": {
                "trail_id": entry.get("trail_id"),
                "name": entry.get("name"),
                "source_url": entry.get("source_url"),
            },
            "user_message": user_message,
            "days": days_n,
            "nights": nights_n,
            "validation_errors": validation_errors,
            "catalog_payload_keys": sorted(catalog_payload.keys()),
            "itinerary_days": len(out),
        },
    )

    atc = None
    if is_backpacking_or_multiday_hike(profile_pills):
        from src.wanderly.alltrails_search_context import derive_alltrails_search_context, normalize_alltrails_search_context

        merged_dest = {**destination, "itinerary": out, "grounding": g}
        url = str(entry.get("source_url") or g.get("source_url") or "").strip()
        if url:
            merged_dest.setdefault("alltrails_search_context", {})
        atc = derive_alltrails_search_context(merged_dest)
        atc = normalize_alltrails_search_context(atc)
        if url:
            from src.wanderly.alltrails_search_context import normalize_alltrails_search_context

            base_atc = atc if isinstance(atc, dict) else {
                "trail_name": str(destination.get("name") or entry.get("name") or ""),
                "country": destination.get("country") or "United States",
            }
            merged = {**base_atc, "parent_alltrails_url": url, "verification_status": "catalog"}
            atc = normalize_alltrails_search_context(merged)

    return _itinerary_client_response(
        itinerary_rows=out,
        budget=budget,
        booking_hints=booking_hints,
        needs_commercial_flight=needs_cf,
        transport_plan=transport_plan,
        booking_hooks=None,
        outdoor_adventure=outdoor_adv,
        itinerary_qa=_itinerary_qa_for_response(
            itinerary_rows=out,
            destination=destination,
            profile_pills=profile_pills,
        ),
        alltrails_search_context=atc
        or _alltrails_context_for_itinerary_response(_wrapper, destination, out),
    )
