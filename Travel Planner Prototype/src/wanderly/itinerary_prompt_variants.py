"""Compact itinerary builder prompts and slim traveler context (feature-flagged)."""

from __future__ import annotations

import json
import os
from typing import Any

_CANONICAL_TRAVELER_KEYS = ("from", "who", "when", "duration", "budget", "vibe")

_ITINERARY_PROMPT_COMPACT = """
You are Wanderly. For ONE destination, return ONLY one JSON object (no markdown fences, no prose).

Top-level keys (all required unless noted):
- itinerary: exactly N days (integers 1..N). Each day: day, label, base, items (3-5 short strings), optional accent,
  optional day_transport {mode, note<=80}, required day_logistics {flight, lodging, surface_legs}.
- transport_plan: {primary_mode, summary, commercial_flight_segments[], lodging_moves[] optional}.
- budget_guidance: seven non-empty strings — trip_ballpark_usd_pp, lodging_style, flight_ballpark, notes,
  trip_usd_pp_short, flight_band_short, lodging_band_short (rough bands only; never live quotes).
- needs_commercial_flight: boolean matching transport_plan.
- booking_search_hints: {flight_to_label, flight_return_from_label} when commercial_flight_segments is non-empty;
  omit when primary_mode is ground_only with no positioning flights.

Hard contracts (invalid keys are dropped server-side):
1) Airline legs (transport_plan.commercial_flight_segments and day_logistics.flight.segments): ONLY
   from_search_label, to_search_label, optional role (inbound|within_trip|return). Expedia-style city + region + country.
   NEVER from_label/to_label on airline segments — those belong on surface_legs only.
2) transport_plan covers the full journey from traveler.from (Context) to this destination and back — not only
   in-country loops. US home + overseas destination: commercial_air or mixed with inbound + return (+ within_trip as needed);
   needs_commercial_flight true. ground_only only when there is truly no commercial airline leg (e.g. regional ferry/drive).
3) Every day includes day_logistics. Each day must have at least one of: non-null lodging (overnight check-in),
   flight.segments non-empty, or surface_legs with >=1 leg. Rest day at one base: one short walk surface_leg.
   lodging when used: destination_search_label, optional area_hint, check_in/check_out YYYY-MM-DD when dates known,
   rooms, guests {adults,children,infants_in_seat}, traveler_party matching guests, pet_friendly from Who,
   room_preferences[], booking_factors[].
4) Align day_logistics.flight with transport_plan on fly days (especially day 1 inbound and last-day return).
5) Comfortable pace; max overnight bases: 4-5d:2, 6-8d:3, 9-11d:4, 12-14d:5. Prefer 2-3 nights per base.
6) budget *_short fields <=40 characters. No exact prices in day items.

Legacy fallback: bare JSON array of days only if the object cannot be produced (budget_guidance will be missing).

N (number of days):
""".strip()

_ITINERARY_TAIL_COMPACT = """
--- APPLY (including agent reuse when static instructions were omitted) ---
Same JSON contract as above. Flight segments: from_search_label/to_search_label only on airline legs.
transport_plan = home (traveler.from) to destination and back; ground_only only if no commercial segments.
Every day needs day_logistics. Include booking_search_hints when flying.
""".strip()


def itinerary_compact_prompt_enabled() -> bool:
    raw = (os.environ.get("WANDERLY_ITINERARY_COMPACT_PROMPT") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def slim_traveler_from_profile_pills(profile_pills: list[dict[str, Any]] | None) -> dict[str, str]:
    """Six canonical traveler slots for compact Context JSON."""
    out: dict[str, str] = {}
    for p in profile_pills or []:
        if not isinstance(p, dict):
            continue
        k = str(p.get("key") or "").strip().lower()
        if k not in _CANONICAL_TRAVELER_KEYS:
            continue
        v = str(p.get("value") or "").strip()
        if v:
            out[k] = v[:520]
    return out


def destination_block_for_prompt(destination: dict[str, Any]) -> dict[str, Any]:
    hook = str(destination.get("hook") or "").strip()
    vibe_raw = destination.get("vibe")
    vibe: list[str] = []
    if isinstance(vibe_raw, list):
        for x in vibe_raw[:8]:
            if isinstance(x, str) and x.strip():
                vibe.append(x.strip()[:48])
    return {
        "id": destination.get("id"),
        "name": destination.get("name"),
        "country": destination.get("country"),
        "hook": hook[:320],
        "vibe": vibe,
    }


def build_itinerary_context_payload(
    *,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    compact: bool | None = None,
) -> dict[str, Any]:
    use_compact = itinerary_compact_prompt_enabled() if compact is None else compact
    dest = destination_block_for_prompt(destination)
    if use_compact:
        return {"destination": dest, "traveler": slim_traveler_from_profile_pills(profile_pills)}
    return {"destination": dest, "profile_pills": profile_pills[:24]}


def build_itinerary_context_json(
    *,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    compact: bool | None = None,
) -> str:
    payload = build_itinerary_context_payload(
        destination=destination,
        profile_pills=profile_pills,
        compact=compact,
    )
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
