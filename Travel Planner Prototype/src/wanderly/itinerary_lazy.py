"""Lazy day-by-day itinerary for a single destination (Cursor JSON → UI ItinDay shape)."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from typing import Any, Callable

from src.integrations.cursor_agent_client import (
    basic_client,
    get_api_key,
    load_env_from_roots,
    run_prompt_collect_assistant_text,
    run_prompt_collect_assistant_text_streaming,
)
from src.wanderly.day_logistics import (
    enrich_itinerary_rows_lodging_from_profile,
    normalize_day_logistics,
    sync_camp_nights_to_itinerary_rows,
    sync_walk_surface_legs_to_itinerary_rows,
)
from src.wanderly.llm_telemetry import ACTIVITY_ITINERARY_BUILDER, telemetry_meta
from src.wanderly.itinerary_debug_log import (
    itinerary_debug_verbose,
    itinerary_file_log_enabled,
    logistics_debug_enabled,
    log_itinerary_build,
)
from src.wanderly.itinerary_prompt_variants import (
    _ITINERARY_PROMPT_COMPACT,
    _ITINERARY_TAIL_COMPACT,
    build_itinerary_context_json,
    itinerary_compact_prompt_enabled,
)
from src.wanderly.outdoor_adventure import (
    is_backpacking_or_multiday_hike,
    normalize_outdoor_adventure,
    outdoor_itinerary_prompt_block,
    outdoor_logistics_repair_append,
)
from src.wanderly.alltrails_search_context import (
    derive_alltrails_search_context,
    normalize_alltrails_search_context,
)
from src.wanderly.trail_authority import (
    authoritative_trail_prompt_block,
    enforce_trail_authority_for_destination,
)
from src.wanderly.session_store import cursor_run_persist_kwargs_for_lane, get_cursor_agent_id

_ITINERARY_PROMPT = """
You are Wanderly. Build a day-by-day trip outline for ONE destination plus rough planning-level budget guidance (ranges / bands only — not quotes or live fares).

Return ONLY one JSON object (no markdown fences, no prose before or after). Shape:
{
  "itinerary": [
    {
      "day": <int 1..N>,
      "label": "Short title → optional area",
      "base": "Overnight base city/town (repeat on day trips)",
      "items": ["3-5 concrete bullets"],
      "accent": "emerald|blue|purple|amber",
      "day_transport": {"mode": "drive|commercial_flight|ferry|train|local|mixed", "note": "≤80 chars — how you move that day (omit if same as trip default)"},
      "day_logistics": {
        "flight": null,
        "lodging": {
          "destination_search_label": "Nanaimo, BC, Canada",
          "area_hint": "harbourfront",
          "check_in": "2026-05-21",
          "check_out": "2026-05-22",
          "rooms": 1,
          "guests": {"adults": 3, "children": 0, "infants_in_seat": 0},
          "traveler_party": {"total": 3, "adults": 3, "children": 0, "infants_in_seat": 0},
          "pet_friendly": true,
          "room_preferences": ["ground floor", "two beds", "pet-friendly property"],
          "booking_factors": ["late_check_in_possible", "holiday_weekend_ferry_buffer", "dog_on_leash_property"]
        },
        "surface_legs": [
          {"kind": "ferry", "from_label": "Tsawwassen, BC, Canada", "to_label": "Nanaimo, BC, Canada", "operator_hint": "BC Ferries"}
        ]
      }
    }
  ],
  "transport_plan": {
    "primary_mode": "ground_only | commercial_air | mixed",
    "summary": "1-3 sentences: home → destination movement (e.g. all driving + ferries, or fly in then drive). Must agree with day bullets.",
    "commercial_flight_segments": [
      {"from_search_label": "Seattle, WA, United States", "to_search_label": "Lisbon, Portugal", "role": "inbound"}
    ],
    "lodging_moves": [{"day_range": "1-2", "area": "City / town", "note": "why you sleep here"}]
  },
  "budget_guidance": {
    "trip_ballpark_usd_pp": "e.g. ≈ $2.2–3.8k pp all-in (ballpark; say if intl flights excluded)",
    "lodging_style": "e.g. mid-range boutique + one splurge night",
    "flight_ballpark": "e.g. economy return from a major US hub often ~$600–1.1k pp (very rough)",
    "notes": "1–2 sentences on what widens or tightens the band (season, pace, dining); never claim availability.",
    "trip_usd_pp_short": "≤40 chars for small UI cards, e.g. ≈ $3.5–5k pp",
    "flight_band_short": "≤40 chars for small UI cards, e.g. ~$700–1.1k rt pp",
    "lodging_band_short": "≤40 chars for small UI cards, e.g. mid guesthouse ~$90–180/n"
  },
  "booking_search_hints": {
    "flight_to_label": "Expedia typeahead string for flying TO this destination (e.g. primary gateway city + country)",
    "flight_return_from_label": "Expedia typeahead for the return leg departure city (usually the last overnight base / main airport city, NOT a vague region name)"
  },
  "needs_commercial_flight": true
}

Rules:
- itinerary: exactly N days; day 1..N inclusive; last day travel-out / fly home style.
- items: short strings — no exact prices inside day bullets.
- **Airline segment keys (hard contract):** In ``transport_plan.commercial_flight_segments`` and in ``day_logistics.flight.segments``, every commercial leg MUST use **only** ``from_search_label``, ``to_search_label``, and optional ``role`` (``inbound`` | ``within_trip`` | ``return``). Values must be Expedia-style city + state/country (e.g. ``Seattle, WA, United States`` → ``Funchal, Madeira Island, Portugal``). **Do not** use ``from_label`` or ``to_label`` on airline segments — the server treats those as invalid and **drops all flight legs** while leaving modes/hints inconsistent. The keys ``from_label`` / ``to_label`` are **only** for ``surface_legs`` (drive, ferry, train, walk).
- **transport_plan (REQUIRED):** Structured truth for downstream OTA booking — **not** regex guesses.
  - **Scope (critical):** The Context JSON includes ``profile_pills`` (especially **from** / home airport and **Who**). ``transport_plan`` MUST describe the **full journey from that home / hub to this card's destination and back** — not only rental-car or ferry loops **inside** the destination country. Never emit ``ground_only`` + empty ``commercial_flight_segments`` just because day bullets only mention local drives; that breaks booking for anyone flying in from another continent.
  - **International / long-haul default:** If **from** is a US city or airport code (e.g. ``Seattle, WA``, ``SEA``) and this destination is overseas (e.g. New Zealand, Australia, Japan, Europe, Iceland — anywhere you cannot realistically complete the trip with surface transport only in the trip length), you MUST set ``primary_mode`` to ``commercial_air`` or ``mixed`` and populate **commercial_flight_segments** with at least one **inbound** leg (home metro → sensible international gateway for this destination) and at least one **return** leg (last major airport / hub city → home). Add **within_trip** segments for required domestic connectors (e.g. Auckland ↔ Queenstown). Set ``needs_commercial_flight``: **true**. The **summary** must mention the long-haul positioning explicitly.
  - **ground_only is rare:** Use ``primary_mode`` = ``ground_only`` **only** when there is truly **no** commercial airline segment for the whole trip (regional road trip, ferries, trains only — e.g. Seattle ↔ Vancouver Island). If the traveler is already in-country or explicitly says they are **not** flying, reflect that; otherwise assume positioning flights are required.
  - **primary_mode** = `commercial_air` when the trip **depends** on flying (in/out or between cities). `mixed` when both apply (e.g. fly in, then drive); then **commercial_flight_segments** must list **every** airline leg with Expedia-style `from_search_label` / `to_search_label` (city + state/country) and `role`: `inbound` | `within_trip` | `return`.
  - **commercial_flight_segments**: Use **[]** only when `primary_mode` is `ground_only`. Never omit the key.
  - **lodging_moves**: Optional but encouraged when the traveler changes overnight towns — helps hotel search alignment.
- **day_transport:** Optional per day; use `mode`=`commercial_flight` only on days with a real airline segment; otherwise `drive`, `ferry`, `train`, or `local`. Must be consistent with **transport_plan**.
  - **day_logistics (REQUIRED on every itinerary day):** Machine-readable booking slice **in addition to** human bullets. **Do not omit** this key. Downstream automation drops days with no concrete logistics.
  - **flight:** Always present on the object. Use **null** when this day has **no** commercial airline segment (typical road/ferry days). On days with airline travel (especially **day 1** inbound and the **last day** return when the trip uses long-haul), set non-null **flight.segments** using the **same** ``from_search_label`` / ``to_search_label`` / ``role`` fields as ``transport_plan`` (never ``from_label`` / ``to_label`` here). Do **not** leave **every** day's ``flight`` null while ``commercial_flight_segments`` lists international legs — align day rows with the plan.
  - **lodging:** **null** only when the traveler does **not** sleep in a new place that night (same hotel as wake-up, or pure multi-hop travel with no check-in). Otherwise include **destination_search_label** (city/town + country), optional **area_hint** (neighborhood when it narrows search, e.g. harbourfront, waterfront), **check_in** / **check_out** as **YYYY-MM-DD** when traveler dates are known from context else omit dates, **rooms** (integer 1–9; default one room per party unless the profile implies otherwise), **guests** `{adults, children, infants_in_seat}` **matching the profile Who pill counts**, **traveler_party** `{total, adults, children, infants_in_seat}` where **total** equals the sum of the four counts (repeat the same numbers as in **guests**), **pet_friendly** boolean (**true** whenever the profile mentions a dog/cat/pet; **false** only when the profile clearly excludes pets), **room_preferences** (1–6 short strings, e.g. `pet-friendly property`, `two beds`, `ground floor`, `kitchenette`), and **booking_factors** (0–10 short snake_case or human tags the OTA layer should respect: e.g. `ada_accessible_room`, `connecting_rooms`, `ev_charging`, `late_arrival`, `quiet_wing`, `kitchenette`, `free_cancellation_priority`, `ferry_schedule_risk` — anything you infer from profile or day story that matters for search filters or ranking, not prose).
  - **surface_legs:** **Required** whenever the day moves by ferry/drive/train/walk between places (even if lodging is null). Use ordered legs: `{kind: drive|ferry|train|walk|other, from_label?, to_label?, operator_hint?}` (e.g. BC Ferries Tsawwassen→Nanaimo). On **backpacking / hiking days**, you MUST include a **`walk`** leg with **both** `from_label` and `to_label` (trailhead → camp); put `trail_name`, `distance_mi`, `elevation_gain_ft`, `elevation_loss_ft` on that walk leg when you have a `trail_segment`. **Do not** describe the hike only in `items` without a `walk` row. On a quiet rest day at one base with no movement, use one short `walk` leg around the base town instead of an empty object.
  - **Backpacking day labels (critical):** Each hiking day `label` is **one** segment only — format `Place A to Place B` (or `A → B`). **Never** chain multiple days in one label (no `Day2 hike → Day3 hike → …`). Day N+1 `walk.from_label` must match day N `camp_night.label`. Exit / fly-home days: **no** backcountry `camp_night`; use flight/lodging only. All hike endpoints must stay in the **same national park** as this destination card.
  - **Validator rule:** Each day must have **at least one** of: non-null **lodging**, **flight** with a non-empty **segments** array, or **surface_legs** with ≥1 leg. All-null / all-omitted logistics is invalid.
- budget_guidance: include all seven keys with non-empty strings. The four long fields are for detail panels; the three *_short fields MUST be concise (hard cap ~40 characters each, no newlines) so they fit destination mini-cards — compress ranges and drop filler words.
- booking_search_hints: When ``commercial_flight_segments`` is non-empty, you MUST include this object with **flight_to_label** (primary gateway to search toward) and **flight_return_from_label** (city of the last main departure before home). Use concrete cities/airport metros Expedia would recognize (avoid broad regions like "Tuscany" alone for flight endpoints). Never append prose suffixes like "-based" or " based" to city names (breaks Expedia typeahead). Omit the whole object only when **primary_mode** is ``ground_only`` with **no** international positioning, or if you are genuinely unsure of endpoints.
- needs_commercial_flight: REQUIRED boolean — must match **transport_plan** (`false` iff `primary_mode` is `ground_only`; `true` if `commercial_air`; if `mixed`, **true**). Downstream booking uses **transport_plan** first; this flag is a compact duplicate for older clients.
- Optional accent on 1-2 highlight days only; omit accent on most days.
- Set `base` for every day to the overnight city/town. Day trips and excursions should keep the same `base` unless there is an actual lodging move that night.
- Default pace should be comfortable, not exhausting. Avoid "run around every day" plans unless user explicitly asks for fast-paced / whirlwind / many-city hopping.
- Keep base changes reasonable for trip length:
  - 3-5 days: at most 2 bases
  - 6-8 days: at most 3 bases
  - 9-11 days: at most 4 bases
  - 12-14 days: at most 5 bases
- Prefer 2-3 nights per base before moving, except when explicit user intent says otherwise.
- For mixed-region destinations (e.g., "X + Y"), choose a few anchor bases and do day trips instead of nightly relocations.

Legacy: if you cannot produce the object, a bare JSON array of days is accepted but budget_guidance will be missing.

N (number of days):
""".strip()

# When a persisted Cloud Agent id is reused, ``cursor_agent_client.effective_followup_prompt_for_reuse``
# strips the leading block matching ``persistent_instruction_prefix`` (this module's _ITINERARY_PROMPT).
# Follow-up runs would otherwise only see day count + Context JSON. Keep non‑negotiable transport rules
# in this tail so every run (first or follow-up) still receives them.
_ITINERARY_TAIL_ALWAYS = """
--- STILL APPLY (including on thread follow-ups after static instructions were omitted) ---

Return ONLY one JSON object: itinerary (exactly N days), transport_plan, budget_guidance (all 7 keys),
booking_search_hints when flying applies, needs_commercial_flight.

**Flight keys:** In transport_plan.commercial_flight_segments and day_logistics.flight.segments use ONLY
from_search_label, to_search_label, and role. Never from_label or to_label on airline legs (those keys are
only for surface_legs); wrong keys cause all segments to be discarded server-side.

transport_plan MUST cover home to destination and back using profile_pills **from** / home and this card's
destination. If **from** is a US city or airport code (e.g. Seattle, SEA) and the destination is overseas
(not a same-region surface-only trip), set primary_mode to commercial_air or mixed — never ground_only with
empty commercial_flight_segments. commercial_flight_segments: at least one inbound (home metro to a sensible
international gateway for this destination), within_trip legs for required domestic connectors, and return
(last main hub to home). needs_commercial_flight: true unless primary_mode is genuinely ground_only.

When commercial_flight_segments is non-empty: include booking_search_hints with flight_to_label and
flight_return_from_label; put matching segments on day_logistics.flight for days that fly (especially day 1
and the last day). Every itinerary day must include day_logistics (flight null or with segments; lodging
when checking in somewhere new; surface_legs when moving by drive/ferry/train).
""".strip()


def _itinerary_day_dict_count(rows: list[Any]) -> int:
    """Count list elements that look like day rows (not bare activity strings)."""
    n = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_day = row.get("day")
        if raw_day is None:
            raw_day = row.get("day_num") or row.get("dayNumber") or row.get("index")
        if _parse_itinerary_day_number(raw_day) is not None:
            n += 1
    return n


def _score_itinerary_array(rows: list[Any]) -> int:
    """Prefer arrays of ``{day, label, items}`` over nested ``items`` string lists (Kyoto EMPTY logs)."""
    day_n = _itinerary_day_dict_count(rows)
    return day_n * 1000 + len(rows) * 10


def _extract_json_array(text: str) -> list[Any] | None:
    """
    Parse the best JSON array in ``text`` (not merely the first parseable ``[``).

    Avoid greedy ``[...]`` regex: model output often includes ``]`` inside strings or
    prose after the array, which breaks ``re.search(r'\\[[\\s\\S]*\\]')`` and yields
    ``extracted_len=0`` despite a valid array (see itinerary_debug EMPTY logs).

    When the outer object is truncated, ``raw_decode`` may succeed on a day row's
    ``items`` array (strings only) before the full ``itinerary`` array — always pick
    the array with the most day-shaped dicts.
    """
    t = text.strip()
    if not t:
        return None
    # Strip a single leading ```json ... ``` fence if present
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE).strip()
        if "```" in t:
            t = t[: t.rfind("```")].strip()

    dec = json.JSONDecoder()
    best: list[Any] | None = None
    best_score = -1

    def consider(candidate: Any) -> None:
        nonlocal best, best_score
        if not isinstance(candidate, list) or not candidate:
            return
        sc = _score_itinerary_array(candidate)
        if sc > best_score:
            best_score = sc
            best = candidate

    try:
        out, _ = dec.raw_decode(t)
        consider(out)
    except json.JSONDecodeError:
        pass

    for i, ch in enumerate(t):
        if ch != "[":
            continue
        try:
            out, _end = dec.raw_decode(t, i)
            consider(out)
        except json.JSONDecodeError:
            continue
    return best


def _strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE).strip()
        if "```" in t:
            t = t[: t.rfind("```")].strip()
    return t


def _bracket_balance_suffix(fragment: str) -> str:
    """Close unterminated ``{`` / ``[`` when the model JSON was cut off mid-stream."""
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in fragment:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack and stack[-1] == ch:
            stack.pop()
    return "".join(reversed(stack))


def _try_parse_truncated_json_value(t: str, *, start: int = 0) -> Any | None:
    """Parse JSON at ``start``, appending bracket closers when the value was truncated."""
    dec = json.JSONDecoder()
    fragment = t[start:].lstrip()
    if not fragment:
        return None
    try:
        val, _end = dec.raw_decode(fragment)
        return val
    except json.JSONDecodeError:
        pass
    suffix = _bracket_balance_suffix(fragment)
    if not suffix:
        return None
    try:
        val, _end = dec.raw_decode(fragment + suffix)
        return val
    except json.JSONDecodeError:
        return None


_BUDGET_GUIDANCE_KEYS = (
    "trip_ballpark_usd_pp",
    "lodging_style",
    "flight_ballpark",
    "notes",
    "trip_usd_pp_short",
    "flight_band_short",
    "lodging_band_short",
)
_BUDGET_GUIDANCE_CAP: dict[str, int] = {
    "trip_usd_pp_short": 44,
    "flight_band_short": 44,
    "lodging_band_short": 44,
}


def _normalize_budget_guidance(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, str] = {}
    for k in _BUDGET_GUIDANCE_KEYS:
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            cap = _BUDGET_GUIDANCE_CAP.get(k, 500)
            out[str(k)] = " ".join(v.strip().split())[:cap]
    return out or None


def _normalize_booking_search_hints(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    allow = frozenset({"flight_to_label", "flight_return_from_label"})
    out: dict[str, str] = {}
    for k in allow:
        v = str(raw.get(k) or "").strip()
        if v:
            out[k] = " ".join(v.split())[:120]
    return out or None


def _normalize_transport_plan(raw: Any) -> dict[str, Any] | None:
    """Structured movement plan from the itinerary agent — primary input for booking (flight vs ground)."""
    from src.wanderly.booking_hooks import normalize_transport_plan

    return normalize_transport_plan(raw)


def _normalize_needs_commercial_flight(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("true", "yes", "1"):
            return True
        if s in ("false", "no", "0"):
            return False
    return None


def _itinerary_list_from_wrapper(obj: dict[str, Any]) -> list[Any] | None:
    best: list[Any] | None = None
    best_score = 0
    for key in ("itinerary", "Itinerary", "days", "Days"):
        v = obj.get(key)
        if not isinstance(v, list) or not v:
            continue
        sc = _score_itinerary_array(v)
        if sc > best_score:
            best_score = sc
            best = v
    if best is not None and _itinerary_day_dict_count(best) > 0:
        return best
    return None


def _collect_wrapped_itinerary_dicts(t: str) -> list[dict[str, Any]]:
    """Every JSON object in ``t`` that contains a non-empty itinerary list."""
    dec = json.JSONDecoder()
    t0 = t.lstrip()
    out: list[dict[str, Any]] = []
    seen_i: set[int] = set()
    for i, ch in enumerate(t0):
        if ch != "{":
            continue
        try:
            obj, _end = dec.raw_decode(t0, i)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if _itinerary_list_from_wrapper(obj) is None:
            continue
        if i in seen_i:
            continue
        seen_i.add(i)
        out.append(obj)
    return out


def _score_itinerary_wrapper(obj: dict[str, Any]) -> int:
    rows = _itinerary_list_from_wrapper(obj) or []
    sc = _score_itinerary_array(rows)
    raw_tp = obj.get("transport_plan") if isinstance(obj.get("transport_plan"), dict) else None
    if raw_tp is None and isinstance(obj.get("transport"), dict):
        raw_tp = obj["transport"]
    if _normalize_transport_plan(raw_tp):
        sc += 5000
    if _normalize_budget_guidance(obj.get("budget_guidance")):
        sc += 80
    if _normalize_booking_search_hints(obj.get("booking_search_hints")):
        sc += 20
    return sc


def _pick_best_itinerary_wrapper(objs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not objs:
        return None
    return max(objs, key=_score_itinerary_wrapper)


def _movement_context_blob(
    itinerary_rows: list[dict[str, Any]],
    budget: dict[str, str] | None,
    booking_hints: dict[str, str] | None,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]] | None,
) -> str:
    parts: list[str] = []
    parts.append(str(destination.get("hook") or ""))
    parts.append(str(destination.get("name") or ""))
    if isinstance(budget, dict):
        parts.extend(str(v) for v in budget.values())
    if isinstance(booking_hints, dict):
        parts.extend(str(v) for v in booking_hints.values())
    for p in profile_pills or []:
        if isinstance(p, dict):
            parts.append(str(p.get("value") or ""))
    for r in itinerary_rows:
        if not isinstance(r, dict):
            continue
        parts.append(str(r.get("label", "")))
        parts.append(str(r.get("base", "")))
        for it in r.get("items") or []:
            parts.append(str(it))
        dt = r.get("day_transport")
        if isinstance(dt, dict):
            parts.append(str(dt.get("mode", "")))
            parts.append(str(dt.get("note", "")))
    return " ".join(parts).upper()


def _day_transport_implies_air(itinerary_rows: list[dict[str, Any]]) -> bool:
    for r in itinerary_rows:
        if not isinstance(r, dict):
            continue
        dt = r.get("day_transport")
        if isinstance(dt, dict) and str(dt.get("mode") or "").strip().lower() == "commercial_flight":
            return True
    return False


def ensure_transport_plan_after_extract(
    *,
    transport_plan: dict[str, Any] | None,
    needs_commercial_flight: bool | None,
    booking_search_hints: dict[str, str] | None,
    budget_guidance: dict[str, str] | None,
    itinerary_rows: list[dict[str, Any]],
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]] | None,
    raw_wrapper: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, bool | None, dict[str, str] | None]:
    """
    Guarantee a normalized ``transport_plan`` whenever we have itinerary rows.

    Models sometimes omit ``transport_plan``, use ``transport`` instead, emit invalid
    ``primary_mode`` strings, or label drive+ferry trips as ``mixed`` with no airline segments.
    """
    from src.wanderly.booking_hooks import normalize_transport_plan, refine_transport_plan_with_movement_text

    hints = dict(booking_search_hints) if isinstance(booking_search_hints, dict) and booking_search_hints else None
    ncf = needs_commercial_flight
    tp = transport_plan

    raw_tp: Any = None
    if isinstance(raw_wrapper, dict):
        raw_tp = raw_wrapper.get("transport_plan") if isinstance(raw_wrapper.get("transport_plan"), dict) else None
        if raw_tp is None and isinstance(raw_wrapper.get("transport"), dict):
            raw_tp = raw_wrapper["transport"]
    if tp is None and raw_tp is not None:
        tp = normalize_transport_plan(raw_tp)

    blob = _movement_context_blob(itinerary_rows, budget_guidance, hints, destination, profile_pills)

    if tp is not None:
        tp = refine_transport_plan_with_movement_text(tp, blob)

    no_flights_budget = bool(budget_guidance) and any(
        "no flight" in str(v).lower() or "no flights" in str(v).lower() for v in (budget_guidance or {}).values()
    )
    profile_blob = " ".join(
        str(p.get("value") or "") for p in (profile_pills or []) if isinstance(p, dict)
    ).upper()
    profile_road = bool(re.search(r"\b(ROAD\s+TRIP|DRIVE|FERRY|BC\s+FERRIES)\b", profile_blob))

    if tp is None and ncf is False:
        tp = {
            "primary_mode": "ground_only",
            "summary": "Ground-only trip (model set needs_commercial_flight=false; transport_plan was missing).",
            "commercial_flight_segments": [],
        }

    if tp is None and itinerary_rows:
        air_day = _day_transport_implies_air(itinerary_rows)
        strong_air = air_day or bool(
            re.search(
                r"\b(SEA\s*[-–]\s*YVR|YVR\s*[-–]\s*SEA|SHORT-HOP\b.*\bYVR\b|LAND\s+IN\s+YVR|"
                r"\bFLY\b.*\b(YVR|YYJ|YCD)\b|\bSEA\s*[-–]\s*YYJ\b|\bSEA\s*[-–]\s*YCD\b)",
                blob,
                re.I,
            )
        )
        groundish = bool(
            re.search(
                r"\b(BC\s+FERRIES|BCFERRIES|\bFERRY\b|PEACE\s+ARCH|HIGHWAY\s+4|HWY\s+4|TSAWWASSEN|"
                r"HORSESHOE\s+BAY|DEPARTURE\s+BAY|NANAIMO|DRIVE|DRIVING|ROAD\s+TRIP|"
                r"I-5|CUSTOMS|VEHICLE\s+RESERVATION)\b",
                blob,
            )
        ) or bool(re.search(r"\bNO\s+FLIGHT", blob)) or no_flights_budget or profile_road

        if strong_air and not groundish:
            tp = {
                "primary_mode": "commercial_air",
                "summary": "Commercial flights expected (inferred from itinerary text; model omitted transport_plan).",
                "commercial_flight_segments": [],
            }
        elif strong_air and groundish:
            tp = {
                "primary_mode": "mixed",
                "summary": "Mixed air + surface travel (inferred; model omitted transport_plan and segment list).",
                "commercial_flight_segments": [],
            }
        else:
            tp = {
                "primary_mode": "ground_only",
                "summary": "Ground-based travel (inferred from itinerary; model omitted transport_plan).",
                "commercial_flight_segments": [],
            }

    if isinstance(tp, dict) and str(tp.get("primary_mode") or "").strip().lower() == "ground_only":
        hints = None

    if tp is not None and ncf is None:
        mode = str(tp.get("primary_mode") or "").strip().lower()
        segs = tp.get("commercial_flight_segments")
        n_seg = len([x for x in segs if isinstance(x, dict)]) if isinstance(segs, list) else 0
        if mode == "ground_only":
            ncf = False
        elif mode == "commercial_air":
            ncf = True
        elif mode == "mixed":
            ncf = n_seg > 0

    return tp, ncf, hints


def _extract_itinerary_payload(
    raw_text: str,
) -> tuple[
    list[Any],
    dict[str, str] | None,
    dict[str, str] | None,
    bool | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """
    Parse model JSON.

    Returns ``(itinerary, budget, booking_hints, needs_cf, transport_plan, booking_hooks, raw_wrapper)``.
    ``raw_wrapper`` is the chosen object (for salvage) or ``None`` on legacy-array path.
    """
    t = _strip_code_fences(raw_text).lstrip("\ufeff")
    if not t:
        return [], None, None, None, None, None, None
    lead = t.lstrip()
    if lead and not lead.startswith(("{", "[")):
        cut = lead.find("{")
        if cut > 0:
            t = lead[cut:]

    wrapped = _collect_wrapped_itinerary_dicts(t)
    best = _pick_best_itinerary_wrapper(wrapped) if wrapped else None
    if best is not None:
        bg = _normalize_budget_guidance(best.get("budget_guidance"))
        bsearch = _normalize_booking_search_hints(best.get("booking_search_hints"))
        ncf = _normalize_needs_commercial_flight(best.get("needs_commercial_flight"))
        raw_tp = best.get("transport_plan") if isinstance(best.get("transport_plan"), dict) else None
        if raw_tp is None and isinstance(best.get("transport"), dict):
            raw_tp = best["transport"]
        tp = _normalize_transport_plan(raw_tp)
        bhooks = best.get("booking_hooks") if isinstance(best.get("booking_hooks"), dict) else None
        rows = _itinerary_list_from_wrapper(best) or []
        return list(rows), bg, bsearch, ncf, tp, bhooks, best

    dec = json.JSONDecoder()
    try:
        top, _end = dec.raw_decode(t, 0)
        if isinstance(top, dict):
            rows = _itinerary_list_from_wrapper(top)
            if rows:
                bg = _normalize_budget_guidance(top.get("budget_guidance"))
                bsearch = _normalize_booking_search_hints(top.get("booking_search_hints"))
                ncf = _normalize_needs_commercial_flight(top.get("needs_commercial_flight"))
                raw_tp = top.get("transport_plan") if isinstance(top.get("transport_plan"), dict) else None
                if raw_tp is None and isinstance(top.get("transport"), dict):
                    raw_tp = top["transport"]
                tp = _normalize_transport_plan(raw_tp)
                bhooks = top.get("booking_hooks") if isinstance(top.get("booking_hooks"), dict) else None
                return list(rows), bg, bsearch, ncf, tp, bhooks, top
        if isinstance(top, list) and _itinerary_day_dict_count(top) > 0:
            return top, None, None, None, None, None, None
    except json.JSONDecodeError:
        pass
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            obj, _end = dec.raw_decode(t, i)
            if isinstance(obj, dict):
                rows = _itinerary_list_from_wrapper(obj)
                if rows:
                    bg = _normalize_budget_guidance(obj.get("budget_guidance"))
                    bsearch = _normalize_booking_search_hints(obj.get("booking_search_hints"))
                    ncf = _normalize_needs_commercial_flight(obj.get("needs_commercial_flight"))
                    raw_tp = obj.get("transport_plan") if isinstance(obj.get("transport_plan"), dict) else None
                    if raw_tp is None and isinstance(obj.get("transport"), dict):
                        raw_tp = obj["transport"]
                    tp = _normalize_transport_plan(raw_tp)
                    bhooks = obj.get("booking_hooks") if isinstance(obj.get("booking_hooks"), dict) else None
                    return list(rows), bg, bsearch, ncf, tp, bhooks, obj
        except json.JSONDecodeError:
            continue

    anchor = t.find('{"itinerary"')
    if anchor < 0:
        anchor = t.find("{")
    if anchor >= 0:
        repaired = _try_parse_truncated_json_value(t, start=anchor)
        if isinstance(repaired, dict):
            rows = _itinerary_list_from_wrapper(repaired)
            if rows:
                log_itinerary_build(
                    f"EXTRACT_TRUNC_REPAIR day_rows={len(rows)} anchor={anchor}"
                )
                bg = _normalize_budget_guidance(repaired.get("budget_guidance"))
                bsearch = _normalize_booking_search_hints(repaired.get("booking_search_hints"))
                ncf = _normalize_needs_commercial_flight(repaired.get("needs_commercial_flight"))
                raw_tp = (
                    repaired.get("transport_plan")
                    if isinstance(repaired.get("transport_plan"), dict)
                    else None
                )
                if raw_tp is None and isinstance(repaired.get("transport"), dict):
                    raw_tp = repaired["transport"]
                tp = _normalize_transport_plan(raw_tp)
                bhooks = repaired.get("booking_hooks") if isinstance(repaired.get("booking_hooks"), dict) else None
                return list(rows), bg, bsearch, ncf, tp, bhooks, repaired

    legacy = _extract_json_array(raw_text) or []
    if _itinerary_day_dict_count(legacy) > 0:
        return legacy, None, None, None, None, None, None

    salvaged = _salvage_itinerary_day_dicts_from_text(t, max_days=16)
    if salvaged:
        log_itinerary_build(
            f"EXTRACT_SALVAGE day_objects={len(salvaged)} "
            f"legacy_string_array={bool(legacy) and _itinerary_day_dict_count(legacy) == 0}"
        )
        return salvaged, None, None, None, None, None, None

    return legacy, None, None, None, None, None, None


def _parse_itinerary_day_number(raw: Any) -> int | None:
    """Accept ``1``, ``1.0``, ``"1"``, ``"Day 3"`` from model JSON."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        try:
            d = int(raw)
        except (TypeError, ValueError):
            return None
        return d if abs(raw - d) < 1e-9 else None
    s = str(raw).strip()
    if not s:
        return None
    m = re.match(r"^day\s*(\d+)\s*$", s, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    try:
        return int(float(s))
    except ValueError:
        m2 = re.search(r"(\d+)", s)
        return int(m2.group(1)) if m2 else None


def _salvage_itinerary_day_dicts_from_text(t: str, *, max_days: int = 12) -> list[dict[str, Any]]:
    """
    Collect complete ``{day, label, items, ...}`` objects when the outer wrapper JSON was truncated.

    Kyoto-style failures: the only fully closed array is day-1 ``items`` (strings); bracket repair on
  the full wrapper recovers the real itinerary rows.
    """
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    lower = t.lower()
    pos = 0
    while len(out) < max_days:
        i = lower.find('{"day":', pos)
        if i < 0:
            break
        obj = _try_parse_truncated_json_value(t, start=i)
        if isinstance(obj, dict):
            day = _parse_itinerary_day_number(obj.get("day"))
            if day is not None and 1 <= day <= max_days and day not in seen:
                seen.add(day)
                out.append(obj)
        pos = i + 7
    out.sort(key=lambda o: int(_parse_itinerary_day_number(o.get("day")) or 0))
    return out


def _string_to_item_bullets(s: str) -> list[str]:
    """Turn a single prose / semicolon / bullet string into discrete items."""
    t = s.strip()
    if not t:
        return []
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if len(lines) > 1:
        return [ln[:200] for ln in lines[:8]]
    one = lines[0] if lines else t
    if "•" in one:
        parts = [p.strip() for p in one.split("•") if p.strip()]
        return [p[:200] for p in parts[:8]]
    if ";" in one:
        parts = [p.strip() for p in one.split(";") if p.strip()]
        return [p[:200] for p in parts[:8]]
    if " | " in one:
        parts = [p.strip() for p in one.split(" | ") if p.strip()]
        return [p[:200] for p in parts[:8]]
    return [one[:200]]


def _item_cell_to_str(it: Any) -> str | None:
    if isinstance(it, str) and it.strip():
        return it.strip()[:200]
    if isinstance(it, dict):
        s = str(
            it.get("text")
            or it.get("item")
            or it.get("title")
            or it.get("label")
            or it.get("description")
            or it.get("bullet")
            or it.get("summary")
            or it.get("headline")
            or it.get("name")
            or it.get("activity")
            or "",
        ).strip()
        return s[:200] if s else None
    return None


def _normalize_itinerary_rows(raw: list[Any], *, max_days: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    allowed_accents = {"emerald", "blue", "purple", "amber"}
    for row in raw:
        if not isinstance(row, dict):
            continue
        raw_day = row.get("day")
        if raw_day is None:
            raw_day = row.get("day_num") or row.get("dayNumber") or row.get("index")
        day = _parse_itinerary_day_number(raw_day)
        if day is None or day < 1 or day > max_days:
            continue
        label = str(row.get("label") or f"Day {day}")[:120]
        items_in = row.get("items")
        if items_in is None:
            items_in = (
                row.get("bullets")
                or row.get("highlights")
                or row.get("activities")
                or row.get("Activities")
            )
        items: list[str] = []
        if isinstance(items_in, list):
            for it in items_in[:12]:
                cell = _item_cell_to_str(it)
                if cell:
                    items.append(cell)
        elif isinstance(items_in, str) and items_in.strip():
            items.extend(_string_to_item_bullets(items_in)[:8])
        if len(items) < 1 and label.strip():
            items = [label.strip()[:200]]
        if len(items) < 1:
            continue
        if len(items) == 1:
            items.append("Flexible time — explore or rest")
        entry: dict[str, Any] = {"day": day, "label": label, "items": items}
        base_raw = (
            row.get("base")
            or row.get("stay_base")
            or row.get("overnight_base")
            or row.get("city")
        )
        if isinstance(base_raw, str) and base_raw.strip():
            entry["base"] = " ".join(base_raw.strip().split())[:96]
        ac = str(row.get("accent") or "").strip().lower()
        if ac in allowed_accents:
            entry["accent"] = ac
        dt = row.get("day_transport")
        if isinstance(dt, dict):
            dm = str(dt.get("mode") or "").strip().lower()[:32]
            dnote = " ".join(str(dt.get("note") or "").split())[:160]
            if dm or dnote:
                entry["day_transport"] = {"mode": dm, "note": dnote}
        dl = normalize_day_logistics(row.get("day_logistics"))
        if dl is not None:
            entry["day_logistics"] = dl
        out.append(entry)
    out.sort(key=lambda x: int(x["day"]))
    # de-dupe by day (keep first)
    seen: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for e in out:
        d = int(e["day"])
        if d in seen:
            continue
        seen.add(d)
        deduped.append(e)
    return deduped[:max_days]


def _max_comfortable_bases(days: int) -> int:
    d = max(1, int(days))
    if d <= 5:
        return 2
    if d <= 8:
        return 3
    if d <= 11:
        return 4
    return 5


def _is_explicit_fast_pace_request(profile_pills: list[dict[str, Any]], destination: dict[str, Any]) -> bool:
    text_bits: list[str] = []
    for p in profile_pills:
        if not isinstance(p, dict):
            continue
        text_bits.append(str(p.get("value") or ""))
    text_bits.append(str(destination.get("hook") or ""))
    blob = " ".join(text_bits).lower()
    return any(
        k in blob
        for k in (
            "fast-paced",
            "fast paced",
            "whirlwind",
            "packed itinerary",
            "pack in",
            "many cities",
            "as many cities",
            "city hopping",
            "multi-city sprint",
            "aggressive pace",
            "every day a new city",
            "road trip every day",
        )
    )


def _label_base_token(label: str) -> str:
    s = " ".join(str(label or "").split())
    if not s:
        return ""
    tail = s.split("→")[-1] if "→" in s else s
    tail = tail.split("->")[-1] if "->" in tail else tail
    # Keep the location side of "title · location" when present.
    if "·" in tail:
        parts = [x.strip() for x in tail.split("·") if x.strip()]
        if parts:
            tail = parts[-1]
    return tail.strip()


def _canon_token(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _replace_label_base(label: str, new_base: str) -> str:
    s = " ".join(str(label or "").split())
    if not s:
        return label
    if "→" in s:
        left = s.split("→", 1)[0].strip()
        return f"{left} → {new_base}".strip()
    if "->" in s:
        left = s.split("->", 1)[0].strip()
        return f"{left} → {new_base}".strip()
    if "·" in s:
        parts = [x.strip() for x in s.split("·")]
        if parts:
            parts[-1] = new_base
            return " · ".join([x for x in parts if x])
    return f"{s} → {new_base}"


def _enforce_comfortable_hops(
    rows: list[dict[str, Any]],
    *,
    max_days: int,
    profile_pills: list[dict[str, Any]],
    destination: dict[str, Any],
) -> list[dict[str, Any]]:
    if not rows:
        return rows
    if _is_explicit_fast_pace_request(profile_pills, destination):
        return rows

    max_bases = _max_comfortable_bases(max_days)
    kept_bases: list[str] = []
    remap: dict[str, str] = {}
    changed = False
    out: list[dict[str, Any]] = []

    for r in rows:
        rr = dict(r)
        label = str(rr.get("label") or "")
        base = _label_base_token(label)
        if not base:
            out.append(rr)
            continue
        ck = _canon_token(base)
        if not ck:
            out.append(rr)
            continue
        if ck in remap:
            mapped = remap[ck]
        else:
            if len(kept_bases) < max_bases:
                kept_bases.append(base)
                remap[ck] = base
                mapped = base
            else:
                # Clamp additional new bases to the latest kept base to avoid daily relocations.
                mapped = kept_bases[-1]
                remap[ck] = mapped
        if _canon_token(mapped) != ck:
            rr["label"] = _replace_label_base(label, mapped)[:120]
            changed = True
        out.append(rr)

    if changed:
        log_itinerary_build(
            f"HOP_GUARD applied days={len(rows)} max_bases={max_bases} kept={kept_bases!r}"
        )
    return out


def _log_itinerary_normalize_all_dropped(rid: str, mode: str, extracted: list[Any]) -> None:
    """When JSON parsed but every day row failed ``_normalize_itinerary_rows`` (helps debug UI EMPTY)."""
    first = extracted[0] if extracted else None
    if not isinstance(first, dict):
        type_counts: dict[str, int] = {}
        for row in extracted[:8]:
            k = type(row).__name__
            type_counts[k] = type_counts.get(k, 0) + 1
        first_preview = repr(first)[:180] if first is not None else ""
        log_itinerary_build(
            f"DROPPED_ALL rid={rid} mode={mode} n_in={len(extracted)} first_type={type(first).__name__!r} "
            f"type_counts={type_counts!r} first_preview={first_preview!r}"
        )
        return
    items_v = first.get("items")
    log_itinerary_build(
        f"DROPPED_ALL rid={rid} mode={mode} n_in={len(extracted)} first_keys={list(first.keys())!r} "
        f"day={first.get('day')!r} items_type={type(items_v).__name__} items_preview={repr(items_v)[:220]}"
    )


def _merge_wrapper_parallel_day_logistics(
    rows: list[dict[str, Any]],
    raw_wrapper: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Some models emit ``"day_logistics": [ {...}, ... ]`` parallel to ``itinerary`` on the wrapper
    object instead of nesting objects under each day. Zip-merge when lengths match.
    """
    if not rows or not isinstance(raw_wrapper, dict):
        return rows
    arr = raw_wrapper.get("day_logistics")
    if not isinstance(arr, list) or not arr:
        return rows
    if len(arr) != len(rows):
        if itinerary_file_log_enabled():
            log_itinerary_build(
                f"LOGISTICS_WARN parallel_day_logistics_len_mismatch "
                f"wrapper_list_len={len(arr)} itinerary_rows={len(rows)}"
            )
        return rows
    out: list[dict[str, Any]] = []
    merged_any = False
    for i, row in enumerate(rows):
        r = dict(row)
        if not r.get("day_logistics") and i < len(arr):
            norm = normalize_day_logistics(arr[i])
            if norm is not None:
                r["day_logistics"] = norm
                merged_any = True
        out.append(r)
    if merged_any and itinerary_file_log_enabled():
        log_itinerary_build(
            "LOGISTICS_TRACE merged_parallel_top_level_day_logistics_array_into_rows=1"
        )
    return out


def _logistics_trace_lazy_build(
    *,
    rid: str,
    mode: str,
    dest_id: str,
    raw: str,
    extracted: list[Any],
    normalized: list[dict[str, Any]],
    raw_wrapper: dict[str, Any] | None = None,
) -> None:
    """Console + itinerary_debug.log: did the model emit ``day_logistics`` and did we persist it?"""
    if not itinerary_file_log_enabled():
        return
    raw_s = raw or ""
    raw_mentions = "day_logistics" in raw_s
    wrap_par = isinstance(raw_wrapper, dict) and isinstance(raw_wrapper.get("day_logistics"), list)
    ext_n = len(extracted) if isinstance(extracted, list) else 0
    ext_with_key = 0
    if isinstance(extracted, list):
        for row in extracted:
            if isinstance(row, dict) and "day_logistics" in row:
                ext_with_key += 1
    norm_days = 0
    for row in normalized:
        if isinstance(row, dict) and row.get("day_logistics"):
            norm_days += 1
    log_itinerary_build(
        f"LOGISTICS_TRACE rid={rid} mode={mode} dest_id={dest_id!r} "
        f"raw_mentions_day_logistics={raw_mentions} extracted_rows_with_dl_key={ext_with_key}/{ext_n} "
        f"normalized_days_with_day_logistics={norm_days}/{len(normalized)} "
        f"raw_has_transport_plan={'transport_plan' in raw_s} "
        f"wrapper_parallel_day_logistics_list={wrap_par}"
    )
    if raw_mentions and ext_with_key == 0 and ext_n > 0 and not wrap_par:
        log_itinerary_build(
            f"LOGISTICS_WARN rid={rid} mode={mode} dest_id={dest_id!r} "
            "raw_text_contains_day_logistics_but_no_extracted_itinerary_row_had_key"
        )
    if not logistics_debug_enabled():
        return
    if isinstance(extracted, list) and extracted:
        z = extracted[0]
        if isinstance(z, dict):
            dl0 = z.get("day_logistics")
            log_itinerary_build(
                f"LOGISTICS_TRACE_VERBOSE rid={rid} first_extracted_keys={sorted(z.keys())!r} "
                f"day_logistics_type={type(dl0).__name__}"
            )
    cap = 2200
    body = raw_s.replace("\r", "").strip()
    if len(body) > cap:
        body = body[: cap // 2] + "\n…\n" + body[-cap // 2 :]
    log_itinerary_build(f"LOGISTICS_TRACE_VERBOSE rid={rid} raw_chars={len(raw_s)} raw_body={body!r}")


def _persisted_logistics_day_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for r in rows if isinstance(r, dict) and r.get("day_logistics"))


def _logistics_followup_retry_enabled() -> bool:
    """Second Cursor pass when the first JSON had itinerary rows but nothing bookable in ``day_logistics``."""
    return (os.environ.get("WANDERLY_ITIN_LOGISTICS_RETRY") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _itinerary_qa_retry_enabled() -> bool:
    """Second Cursor pass when backpacking QA reports errors (chain breaks, cross-park, etc.)."""
    return (os.environ.get("WANDERLY_ITIN_QA_RETRY") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _itinerary_qa_repair_user_message(
    raw: str,
    *,
    qa: dict[str, Any],
    n_days: int,
    profile_pills: list[dict[str, Any]] | None = None,
    destination: dict[str, Any] | None = None,
) -> str:
    body = (raw or "").replace("\r", "").strip()
    cap_s = (os.environ.get("WANDERLY_ITIN_QA_RETRY_MAX_PREVIOUS_CHARS") or "14000").strip() or "14000"
    try:
        cap = int(cap_s)
    except ValueError:
        cap = 14000
    cap = max(2000, min(cap, 100_000))
    if len(body) > cap:
        body = body[: cap // 2] + "\n…\n" + body[-cap // 2 :]
    issues = [i for i in (qa.get("issues") or []) if isinstance(i, dict)]
    issue_lines = []
    for row in issues[:12]:
        if str(row.get("severity") or "").lower() != "error":
            continue
        day = row.get("day")
        prefix = f"Day {day}: " if day else ""
        issue_lines.append(f"- {prefix}{row.get('code')}: {row.get('message')}")
    issues_block = "\n".join(issue_lines) or "- Fix backpacking route continuity and park scope."
    from src.wanderly.trail_authority import authoritative_trail_prompt_block

    authority = ""
    if destination:
        authority = authoritative_trail_prompt_block(destination) or ""
    return (
        "BACKPACKING QA FIX REQUIRED: your previous JSON failed automated route checks.\n\n"
        "Fix every issue below. Return ONLY one complete JSON object (no markdown fences).\n"
        f"Keep exactly {n_days} days when possible; preserve transport and gateway days.\n\n"
        "Issues to fix:\n"
        f"{issues_block}\n\n"
        "Rules:\n"
        "- One national park per destination card — no day hikes in a different park on this card.\n"
        "- Day N+1 walk `from_label` must match day N `camp_night.label` (or trailhead on day 1).\n"
        "- No `camp_night` on fly-home or exit-only days.\n"
        "- Use trail stats from Context `alltrails_search_context` when the model emits it (optional reference URL).\n"
        "- Every hiking day: `surface_legs` with `walk` + `trail_segment`.\n\n"
        f"{authority}\n\n"
        "Broken output to repair:\n"
        f"{body}\n"
        f"{outdoor_logistics_repair_append(profile_pills)}"
    )


def _logistics_repair_user_message(
    raw: str,
    *,
    n_days: int,
    profile_pills: list[dict[str, Any]] | None = None,
) -> str:
    body = (raw or "").replace("\r", "").strip()
    cap_s = (os.environ.get("WANDERLY_ITIN_LOGISTICS_RETRY_MAX_PREVIOUS_CHARS") or "16000").strip() or "16000"
    try:
        cap = int(cap_s)
    except ValueError:
        cap = 16000
    cap = max(2000, min(cap, 100_000))
    if len(body) > cap:
        body = body[: cap // 2] + "\n…\n" + body[-cap // 2 :]
    return (
        "SCHEMA FIX REQUIRED: your previous answer for this destination was valid JSON but omitted "
        "usable per-day `day_logistics` (missing keys, empty objects, or only nulls — nothing bookable).\n\n"
        "Return ONLY one complete JSON object (no markdown fences, no commentary). "
        f"Keep exactly {n_days} itinerary days and preserve the same trip story, labels, bases, and items as much as practical, "
        "but **every** itinerary object MUST include `day_logistics` with:\n"
        "- `flight`: present (null when no airline segment that day; otherwise `segments` arrays where **each** "
        "segment uses **only** `from_search_label`, `to_search_label`, and optional `role` — same as "
        "`transport_plan.commercial_flight_segments`. **Never** use `from_label` or `to_label` on flight segments "
        "(server drops them); those keys are only for `surface_legs`.\n"
        "- `lodging`: non-null when the traveler checks into an overnight stay that night (include `destination_search_label`, "
        "`check_in`/`check_out` as YYYY-MM-DD when known, **`rooms`**, **`guests`**, **`traveler_party`** (total = sum of party), "
        "**`pet_friendly`** aligned to profile Who, **`room_preferences`**, optional **`area_hint`**, **`booking_factors`** for any other OTA-relevant constraints).\n"
        "- `surface_legs`: non-empty whenever the day moves by ferry/drive/train/walk (kind + from_label + to_label). "
        "On hiking/backpacking days: at least one `walk` leg with from_label + to_label matching day label / camp_night; "
        "mirror trail_segment stats on the walk leg. On a rest day at one base, one short `walk` leg around that base.\n"
        "Each day must satisfy: non-null lodging OR non-empty flight.segments OR non-empty surface_legs.\n\n"
        "Also correct **transport_plan**, **needs_commercial_flight**, and **booking_search_hints** if they are inconsistent "
        "with home-to-destination reality: when the traveler flies from a US hub (see Context **from** / profile) to an "
        "overseas destination, you must **not** leave `primary_mode` as `ground_only` with empty `commercial_flight_segments`. "
        "Emit **inbound** + **return** (and **within_trip** domestic connectors as needed) on **transport_plan** "
        "using **only** `from_search_label` / `to_search_label` / `role` on each segment (never `from_label`/`to_label` "
        "on flights — same as lazy itinerary), set `needs_commercial_flight` true, "
        "include `booking_search_hints` with `flight_to_label` and `flight_return_from_label`, and put matching segments on "
        "the relevant days' `day_logistics.flight`.\n\n"
        "Broken output to repair:\n"
        f"{body}\n"
        f"{outdoor_logistics_repair_append(profile_pills)}"
    )


def _apply_itinerary_qa_retry(
    *,
    raw: str,
    out: list[dict[str, Any]],
    extracted: list[Any],
    budget: dict[str, str] | None,
    booking_hints: dict[str, str] | None,
    needs_cf: bool | None,
    transport_plan: dict[str, Any] | None,
    booking_hooks: dict[str, Any] | None,
    raw_wrapper: dict[str, Any] | None,
    rid: str,
    mode: str,
    did: str,
    n: int,
    profile_pills: list[dict[str, Any]],
    destination: dict[str, Any],
    run_repair_prompt: Callable[[str], str],
) -> tuple[
    str,
    list[dict[str, Any]],
    list[Any],
    dict[str, str] | None,
    dict[str, str] | None,
    bool | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """Run a second LLM pass when backpacking QA still reports errors."""
    if not _itinerary_qa_retry_enabled() or not out:
        return raw, out, extracted, budget, booking_hints, needs_cf, transport_plan, booking_hooks, raw_wrapper
    qa = _itinerary_qa_for_response(
        itinerary_rows=out, destination=destination, profile_pills=profile_pills
    )
    if not qa or qa.get("ok") is not False or int(qa.get("error_count") or 0) < 1:
        return raw, out, extracted, budget, booking_hints, needs_cf, transport_plan, booking_hooks, raw_wrapper
    log_itinerary_build(
        f"QA_RETRY_BEGIN rid={rid} mode={mode} dest_id={did!r} errors={qa.get('error_count')}"
    )
    repair = _itinerary_qa_repair_user_message(
        raw, qa=qa, n_days=n, profile_pills=profile_pills, destination=destination
    )
    t_retry = time.perf_counter()
    try:
        raw2 = run_repair_prompt(repair)
    except Exception as e:
        log_itinerary_build(
            f"QA_RETRY_FAIL rid={rid} mode={mode} dest_id={did!r} exc={type(e).__name__}: {e!s}"
        )
        return raw, out, extracted, budget, booking_hints, needs_cf, transport_plan, booking_hooks, raw_wrapper
    (
        out2,
        extracted2,
        budget2,
        booking_hints2,
        needs_cf2,
        transport_plan2,
        booking_hooks2,
        raw_wrapper2,
    ) = _process_lazy_itinerary_raw(
        raw2,
        trace_rid=f"{rid}+qa_retry",
        mode=mode,
        dest_id=did,
        n=n,
        profile_pills=profile_pills,
        destination=destination,
    )
    retry_ms = (time.perf_counter() - t_retry) * 1000.0
    qa2 = _itinerary_qa_for_response(
        itinerary_rows=out2, destination=destination, profile_pills=profile_pills
    )
    improved = bool(
        out2
        and qa2
        and (
            qa2.get("ok") is True
            or int(qa2.get("error_count") or 99) < int(qa.get("error_count") or 0)
        )
    )
    if improved:
        log_itinerary_build(
            f"QA_RETRY_OK rid={rid} mode={mode} dest_id={did!r} "
            f"errors_before={qa.get('error_count')} errors_after={qa2.get('error_count')} "
            f"elapsed_retry_ms={retry_ms:.1f}"
        )
        return (
            raw2,
            out2,
            extracted2,
            budget2,
            booking_hints2,
            needs_cf2,
            transport_plan2,
            booking_hooks2,
            raw_wrapper2,
        )
    log_itinerary_build(
        f"QA_RETRY_NO_IMPROVEMENT rid={rid} mode={mode} dest_id={did!r} "
        f"errors_first={qa.get('error_count')} errors_second={(qa2 or {}).get('error_count')} "
        f"elapsed_retry_ms={retry_ms:.1f}"
    )
    return raw, out, extracted, budget, booking_hints, needs_cf, transport_plan, booking_hooks, raw_wrapper


def _process_lazy_itinerary_raw(
    raw: str,
    *,
    trace_rid: str,
    mode: str,
    dest_id: str,
    n: int,
    profile_pills: list[dict[str, Any]],
    destination: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[Any],
    dict[str, str] | None,
    dict[str, str] | None,
    bool | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    extracted, budget, booking_hints, needs_cf, transport_plan, booking_hooks, raw_wrapper = _extract_itinerary_payload(raw)
    ext_list = extracted if isinstance(extracted, list) else []
    out = _normalize_itinerary_rows(ext_list, max_days=n)
    out = _merge_wrapper_parallel_day_logistics(out, raw_wrapper if isinstance(raw_wrapper, dict) else None)
    out = _enforce_comfortable_hops(out, max_days=n, profile_pills=profile_pills, destination=destination)
    out = enrich_itinerary_rows_lodging_from_profile(out, profile_pills)
    out, trail_warnings = enforce_trail_authority_for_destination(out, destination)
    for w in trail_warnings:
        log_itinerary_build(f"TRAIL_AUTHORITY rid={trace_rid} {w}")
    oa_for_camps = _outdoor_adventure_for_itinerary_response(
        raw_wrapper if isinstance(raw_wrapper, dict) else None,
        destination,
    )
    out = sync_camp_nights_to_itinerary_rows(out, oa_for_camps, destination=destination)
    out = sync_walk_surface_legs_to_itinerary_rows(out, outdoor_adventure=oa_for_camps)
    dest_qa = dict(destination)
    dest_qa["itinerary"] = out
    from src.wanderly.backpacking_itinerary_qa import apply_backpacking_itinerary_qa

    dest_qa = apply_backpacking_itinerary_qa(dest_qa, profile_pills=profile_pills)
    out = dest_qa.get("itinerary") if isinstance(dest_qa.get("itinerary"), list) else out
    _logistics_trace_lazy_build(
        rid=trace_rid,
        mode=mode,
        dest_id=dest_id,
        raw=raw,
        extracted=list(ext_list),
        normalized=out,
        raw_wrapper=raw_wrapper if isinstance(raw_wrapper, dict) else None,
    )
    from src.wanderly.alltrails_build_trace import log_alltrails_llm_wrapper

    log_alltrails_llm_wrapper(
        rid=trace_rid,
        dest_id=dest_id,
        raw_wrapper=raw_wrapper if isinstance(raw_wrapper, dict) else None,
    )
    return out, ext_list, budget, booking_hints, needs_cf, transport_plan, booking_hooks, raw_wrapper


def _itinerary_cursor_lane(light: str | None) -> str:
    """
    Persistence lane for lazy itinerary only — must NOT reuse ``planner`` (smoke/refine)
    or follow-up runs see huge JSON/chat context and rarely return a bare JSON array.
    """
    o = (light or "").strip()
    return "itinerary_lazy" if not o else f"itinerary_lazy:m:{o}"


def itinerary_parallel_dest_enabled() -> bool:
    """When true (default), each destination uses its own Cursor persistence lane (parallel builds).

    Opt out with ``WANDERLY_ITINERARY_PARALLEL_DEST=0`` (or ``false`` / ``off``).
    """
    raw = (os.environ.get("WANDERLY_ITINERARY_PARALLEL_DEST") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _cursor_lane_for_itinerary_build(session_id: str, destination_id: str, light: str | None) -> str:
    """Cursor agent lane for one itinerary build (optionally unique per destination)."""
    base = _itinerary_cursor_lane(light)
    if not itinerary_parallel_dest_enabled():
        return base
    did = re.sub(r"[^a-zA-Z0-9_.-]+", "-", (destination_id or "").strip())[:56]
    return f"{base}:d:{did}" if did else base


def itinerary_build_lock_key(session_id: str, destination_id: str) -> str:
    """Serialize server-side builds for the same Cursor lane (avoids 409 on concurrent POSTs)."""
    light = (os.environ.get("CURSOR_LIGHT_MODEL_FOR_ITINERARY") or os.environ.get("CURSOR_LIGHT_MODEL_FOR_IMAGES") or "").strip() or None
    lane = _cursor_lane_for_itinerary_build(session_id, destination_id, light)
    return f"{(session_id or '').strip()}\t{lane}"


def _itin_reuse_hint(session_id: str | None, light: str | None, destination_id: str = "") -> str:
    sid = (session_id or "").strip()
    if not sid:
        return "reuse=no_session"
    lane = _cursor_lane_for_itinerary_build(sid, destination_id, light)
    aid = get_cursor_agent_id(sid, lane)
    if aid:
        tail = aid[-8:] if len(aid) > 8 else aid
        return f"reuse_lane={lane} existing_agent=…{tail}"
    return f"reuse_lane={lane} existing_agent=(none)"


def _vibe_list_for_prompt(raw: object) -> list[str]:
    """Cap vibe tokens — cards may carry long arrays from enrichment."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw[:8]:
        if isinstance(x, str) and x.strip():
            out.append(x.strip()[:48])
    return out


def _itinerary_persistent_prefix() -> str:
    if itinerary_compact_prompt_enabled():
        return _ITINERARY_PROMPT_COMPACT
    return _ITINERARY_PROMPT


def _itinerary_prompt_tail() -> str:
    if itinerary_compact_prompt_enabled():
        return _ITINERARY_TAIL_COMPACT
    return _ITINERARY_TAIL_ALWAYS


def _itinerary_prompt_and_days(
    *,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    days: int,
    refinement_message: str | None = None,
) -> tuple[str, int, str]:
    n = max(3, min(int(days), 14))
    prefix = _itinerary_persistent_prefix()
    tail = _itinerary_prompt_tail()
    outdoor_block = outdoor_itinerary_prompt_block(profile_pills)
    if outdoor_block:
        tail = tail + "\n\n" + outdoor_block
    if is_backpacking_or_multiday_hike(profile_pills):
        trail_block = authoritative_trail_prompt_block(destination)
        if trail_block:
            tail = tail + "\n\n" + trail_block
    ctx = build_itinerary_context_json(destination=destination, profile_pills=profile_pills)
    prompt = prefix + f" {n}\n\nContext JSON:\n{ctx}\n\n" + tail
    rm = (refinement_message or "").strip()
    if rm:
        prompt += f"\n\nTraveler refinement (honor this when rebuilding the day-by-day plan):\n{rm[:4000]}\n"
    return prompt, n, prefix


def _outdoor_from_wrapper(raw_wrapper: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw_wrapper, dict):
        return None
    return normalize_outdoor_adventure(raw_wrapper.get("outdoor_adventure"))


def _outdoor_adventure_for_itinerary_response(
    raw_wrapper: dict[str, Any] | None,
    destination: dict[str, Any],
) -> dict[str, Any] | None:
    return _outdoor_from_wrapper(raw_wrapper)


def _alltrails_context_for_itinerary_response(
    raw_wrapper: dict[str, Any] | None,
    destination: dict[str, Any],
    itinerary_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    from src.wanderly.itinerary_debug_log import log_alltrails_itinerary_context

    did = str(destination.get("id") or "")
    llm_had_ctx: bool | None = None
    ctx = None
    if isinstance(raw_wrapper, dict):
        llm_had_ctx = isinstance(raw_wrapper.get("alltrails_search_context"), dict)
        ctx = normalize_alltrails_search_context(raw_wrapper.get("alltrails_search_context"))
    if not ctx:
        ctx = derive_alltrails_search_context(
            {**destination, "itinerary": itinerary_rows},
        )
    if not ctx:
        log_alltrails_itinerary_context(
            dest_id=did, atc=None, llm_wrapper_had_ctx=llm_had_ctx, channel="build"
        )
        return None
    # Apify verify disabled by default; pass through LLM-emitted context only.
    out = normalize_alltrails_search_context(ctx)
    log_alltrails_itinerary_context(
        dest_id=did, atc=out, llm_wrapper_had_ctx=llm_had_ctx, channel="build"
    )
    return out


def _itinerary_client_response(
    *,
    itinerary_rows: list[dict[str, Any]],
    budget: dict[str, str] | None,
    booking_hints: dict[str, str] | None,
    needs_commercial_flight: bool | None,
    transport_plan: dict[str, Any] | None = None,
    booking_hooks: dict[str, Any] | None = None,
    outdoor_adventure: dict[str, Any] | None = None,
    itinerary_qa: dict[str, Any] | None = None,
    alltrails_search_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from src.wanderly.booking_hooks import build_booking_hooks_from_transport_plan, normalize_booking_hooks

    out: dict[str, Any] = {"itinerary": itinerary_rows, "budget_guidance": budget}
    if booking_hints:
        out["booking_search_hints"] = booking_hints
    if isinstance(needs_commercial_flight, bool):
        out["needs_commercial_flight"] = needs_commercial_flight
    bh = normalize_booking_hooks(booking_hooks) if isinstance(booking_hooks, dict) else None
    if not bh:
        bh = build_booking_hooks_from_transport_plan(transport_plan)
    tp_out = transport_plan
    if not isinstance(tp_out, dict) and isinstance(bh, dict):
        tp_out = bh.get("transport")
    if isinstance(tp_out, dict) and tp_out.get("primary_mode"):
        out["transport_plan"] = tp_out
    if bh:
        out["booking_hooks"] = bh
    oa = normalize_outdoor_adventure(outdoor_adventure)
    if not oa and isinstance(bh, dict):
        oa = normalize_outdoor_adventure(bh.get("outdoor_adventure"))
    if oa:
        out["outdoor_adventure"] = oa
    if isinstance(itinerary_qa, dict) and not itinerary_qa.get("skipped"):
        out["itinerary_qa"] = itinerary_qa
    atc = normalize_alltrails_search_context(alltrails_search_context)
    if atc:
        out["alltrails_search_context"] = atc
    return out


def _itinerary_qa_for_response(
    *,
    itinerary_rows: list[dict[str, Any]],
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
) -> dict[str, Any] | None:
    dest = {**destination, "itinerary": itinerary_rows}
    from src.wanderly.backpacking_itinerary_qa import validate_backpacking_itinerary

    qa = validate_backpacking_itinerary(dest, profile_pills=profile_pills)
    if qa.get("skipped"):
        return None
    return qa


def build_itinerary_for_destination(
    *,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    days: int,
    session_id: str | None = None,
    request_id: str | None = None,
    refinement_message: str | None = None,
) -> dict[str, Any]:
    """
    Call Cursor once; return itinerary rows, optional budget bands, and optional booking URL hints for the client.
    """
    rid = (request_id or "?").strip() or "?"
    did = str(destination.get("id") or "")
    from src.wanderly.catalog_itinerary import (
        build_catalog_itinerary_for_destination,
        is_catalog_grounded_destination,
    )

    if is_catalog_grounded_destination(destination):
        log_itinerary_build(
            f"ROUTE catalog_itinerary rid={rid} sid={(session_id or '')[:12]!r} dest_id={did!r}"
        )
        return build_catalog_itinerary_for_destination(
            destination=destination,
            profile_pills=profile_pills,
            days=days,
            session_id=session_id,
            request_id=rid,
            refinement_message=refinement_message,
        )
    load_env_from_roots()
    key = get_api_key()
    prompt, n, itin_prefix = _itinerary_prompt_and_days(
        destination=destination,
        profile_pills=profile_pills,
        days=days,
        refinement_message=refinement_message,
    )
    light = (os.environ.get("CURSOR_LIGHT_MODEL_FOR_ITINERARY") or os.environ.get("CURSOR_LIGHT_MODEL_FOR_IMAGES") or "").strip() or None
    log_itinerary_build(
        f"BEGIN rid={rid} mode=json sid={(session_id or '')[:12]!r} dest_id={did!r} days={days} n={n} "
        f"compact={1 if itinerary_compact_prompt_enabled() else 0} prompt_chars={len(prompt)} "
        f"light_model={light!r} {_itin_reuse_hint(session_id, light, did)}"
    )
    t0 = time.perf_counter()
    raw = ""
    _lane = _cursor_lane_for_itinerary_build(session_id or "", did, light)
    _tp = f"itinerary_lazy.json rid={rid} dest={did} lane={_lane}"
    try:
        with basic_client(key) as client:
            raw = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=light,
                persistent_instruction_prefix=itin_prefix,
                llm_telemetry_activity=ACTIVITY_ITINERARY_BUILDER,
                llm_telemetry_sub_activity="build_itinerary",
                llm_telemetry_meta=telemetry_meta(
                    session_id=session_id,
                    destination_id=did,
                    request_id=rid,
                    days=days,
                    compact_prompt=itinerary_compact_prompt_enabled(),
                ),
                **cursor_run_persist_kwargs_for_lane(session_id or "", _lane, trace_path=_tp),
            )
    except Exception as e:
        log_itinerary_build(
            f"FAIL rid={rid} mode=json dest_id={did!r} exc={type(e).__name__}: {e!s} "
            f"elapsed_ms={(time.perf_counter() - t0) * 1000.0:.1f}"
        )
        raise
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    (
        out,
        extracted,
        budget,
        booking_hints,
        needs_cf,
        transport_plan,
        booking_hooks,
        raw_wrapper,
    ) = _process_lazy_itinerary_raw(
        raw,
        trace_rid=rid,
        mode="json",
        dest_id=did,
        n=n,
        profile_pills=profile_pills,
        destination=destination,
    )
    if _logistics_followup_retry_enabled() and out and _persisted_logistics_day_count(out) == 0:
        log_itinerary_build(
            f"LOGISTICS_RETRY_BEGIN rid={rid} mode=json dest_id={did!r} itinerary_days={len(out)} n={n}"
        )
        repair = _logistics_repair_user_message(raw, n_days=n, profile_pills=profile_pills)
        t_retry = time.perf_counter()
        try:
            with basic_client(key) as client:
                raw2 = run_prompt_collect_assistant_text(
                    client,
                    repair,
                    model_id_override=light,
                    persistent_instruction_prefix=itin_prefix,
                    llm_telemetry_activity=ACTIVITY_ITINERARY_BUILDER,
                    llm_telemetry_sub_activity="build_itinerary_logistics_retry",
                    llm_telemetry_meta=telemetry_meta(
                        session_id=session_id,
                        destination_id=did,
                        request_id=rid,
                        days=days,
                    ),
                    **cursor_run_persist_kwargs_for_lane(
                        session_id or "",
                        _lane,
                        trace_path=f"{_tp}.retry",
                    ),
                )
        except Exception as e:
            log_itinerary_build(
                f"LOGISTICS_RETRY_FAIL rid={rid} mode=json dest_id={did!r} exc={type(e).__name__}: {e!s}"
            )
        else:
            (
                out2,
                extracted2,
                budget2,
                booking_hints2,
                needs_cf2,
                transport_plan2,
                booking_hooks2,
                raw_wrapper2,
            ) = _process_lazy_itinerary_raw(
                raw2,
                trace_rid=f"{rid}+logistics_retry",
                mode="json",
                dest_id=did,
                n=n,
                profile_pills=profile_pills,
                destination=destination,
            )
            retry_ms = (time.perf_counter() - t_retry) * 1000.0
            if out2 and _persisted_logistics_day_count(out2) > 0:
                raw = raw2
                out = out2
                extracted = extracted2
                budget = budget2
                booking_hints = booking_hints2
                needs_cf = needs_cf2
                transport_plan = transport_plan2
                booking_hooks = booking_hooks2
                raw_wrapper = raw_wrapper2
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                log_itinerary_build(
                    f"LOGISTICS_RETRY_OK rid={rid} mode=json dest_id={did!r} "
                    f"persisted_dl={_persisted_logistics_day_count(out)} elapsed_retry_ms={retry_ms:.1f}"
                )
            else:
                log_itinerary_build(
                    f"LOGISTICS_RETRY_NO_IMPROVEMENT rid={rid} mode=json dest_id={did!r} "
                    f"persisted_dl_first={_persisted_logistics_day_count(out)} "
                    f"persisted_dl_second={_persisted_logistics_day_count(out2)} elapsed_retry_ms={retry_ms:.1f}"
                )

    def _run_qa_repair(repair_prompt: str) -> str:
        with basic_client(key) as client:
            return run_prompt_collect_assistant_text(
                client,
                repair_prompt,
                model_id_override=light,
                persistent_instruction_prefix=itin_prefix,
                llm_telemetry_activity=ACTIVITY_ITINERARY_BUILDER,
                llm_telemetry_sub_activity="build_itinerary_qa_retry",
                llm_telemetry_meta=telemetry_meta(
                    session_id=session_id,
                    destination_id=did,
                    request_id=rid,
                    days=days,
                ),
                **cursor_run_persist_kwargs_for_lane(
                    session_id or "",
                    _lane,
                    trace_path=f"{_tp}.qa_retry",
                ),
            )

    (
        raw,
        out,
        extracted,
        budget,
        booking_hints,
        needs_cf,
        transport_plan,
        booking_hooks,
        raw_wrapper,
    ) = _apply_itinerary_qa_retry(
        raw=raw,
        out=out,
        extracted=extracted,
        budget=budget,
        booking_hints=booking_hints,
        needs_cf=needs_cf,
        transport_plan=transport_plan,
        booking_hooks=booking_hooks,
        raw_wrapper=raw_wrapper if isinstance(raw_wrapper, dict) else None,
        rid=rid,
        mode="json",
        did=did,
        n=n,
        profile_pills=profile_pills,
        destination=destination,
        run_repair_prompt=_run_qa_repair,
    )
    if out:
        transport_plan, needs_cf, booking_hints = ensure_transport_plan_after_extract(
            transport_plan=transport_plan,
            needs_commercial_flight=needs_cf,
            booking_search_hints=booking_hints,
            budget_guidance=budget,
            itinerary_rows=out,
            destination=destination,
            profile_pills=profile_pills,
            raw_wrapper=raw_wrapper,
        )
        booking_hooks = None
    log_itinerary_build(
        f"END rid={rid} mode=json dest_id={did!r} raw_chars={len(raw or '')} extracted_len={len(extracted)} "
        f"normalized_len={len(out)} budget_keys={list(budget or [])} "
        f"transport_mode={(transport_plan or {}).get('primary_mode')!r} elapsed_ms={elapsed_ms:.1f}"
    )
    if not out:
        tail = (raw or "").replace("\n", " ").strip()[:500]
        log_itinerary_build(f"EMPTY rid={rid} mode=json extracted_len={len(extracted)} raw_snippet={tail!r}")
        if extracted:
            _log_itinerary_normalize_all_dropped(rid, "json", extracted)
    if not out and itinerary_debug_verbose():
        snippet = (raw or "").strip().replace("\r", "")[:2000]
        log_itinerary_build(f"DEBUG_EMPTY rid={rid} raw_head={snippet!r}")
    return _itinerary_client_response(
        itinerary_rows=out,
        budget=budget,
        booking_hints=booking_hints,
        needs_commercial_flight=needs_cf,
        transport_plan=transport_plan,
        booking_hooks=booking_hooks,
        outdoor_adventure=_outdoor_adventure_for_itinerary_response(
            raw_wrapper if isinstance(raw_wrapper, dict) else None,
            destination,
        ),
        itinerary_qa=_itinerary_qa_for_response(
            itinerary_rows=out,
            destination=destination,
            profile_pills=profile_pills,
        ),
        alltrails_search_context=_alltrails_context_for_itinerary_response(
            raw_wrapper if isinstance(raw_wrapper, dict) else None,
            destination,
            out,
        ),
    )


def build_itinerary_for_destination_streaming(
    *,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    days: int,
    emit: Callable[[dict[str, Any]], None] | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    refinement_message: str | None = None,
) -> dict[str, Any]:
    """
    Same as ``build_itinerary_for_destination`` but streams Cursor SSE.
    Forwards thinking/status/tool/assistant SSE to ``emit``; the client maps JSON deltas
    to traveler-facing overlay text (does not render raw JSON in the UI).
    """
    rid = (request_id or "?").strip() or "?"
    did = str(destination.get("id") or "")
    from src.wanderly.catalog_itinerary import (
        build_catalog_itinerary_for_destination,
        is_catalog_grounded_destination,
    )

    if is_catalog_grounded_destination(destination):
        log_itinerary_build(
            f"ROUTE catalog_itinerary_sse rid={rid} sid={(session_id or '')[:12]!r} dest_id={did!r}"
        )

        def _safe_emit(ev: dict[str, Any]) -> None:
            if emit is not None:
                emit(ev)

        return build_catalog_itinerary_for_destination(
            destination=destination,
            profile_pills=profile_pills,
            days=days,
            session_id=session_id,
            request_id=rid,
            refinement_message=refinement_message,
            emit=_safe_emit,
        )
    load_env_from_roots()
    key = get_api_key()
    prompt, n, itin_prefix = _itinerary_prompt_and_days(
        destination=destination,
        profile_pills=profile_pills,
        days=days,
        refinement_message=refinement_message,
    )
    light = (os.environ.get("CURSOR_LIGHT_MODEL_FOR_ITINERARY") or os.environ.get("CURSOR_LIGHT_MODEL_FOR_IMAGES") or "").strip() or None

    def _safe_emit(ev: dict[str, Any]) -> None:
        if emit is not None:
            emit(ev)

    log_itinerary_build(
        f"BEGIN rid={rid} mode=sse sid={(session_id or '')[:12]!r} dest_id={did!r} days={days} n={n} "
        f"compact={1 if itinerary_compact_prompt_enabled() else 0} prompt_chars={len(prompt)} "
        f"light_model={light!r} {_itin_reuse_hint(session_id, light, did)}"
    )
    t0 = time.perf_counter()
    raw = ""
    _lane = _cursor_lane_for_itinerary_build(session_id or "", did, light)
    _tp = f"itinerary_lazy.sse rid={rid} dest={did} lane={_lane}"
    try:
        with basic_client(key) as client:
            raw = run_prompt_collect_assistant_text_streaming(
                client,
                prompt,
                emit=_safe_emit if emit else None,
                model_id_override=light,
                persistent_instruction_prefix=itin_prefix,
                llm_telemetry_activity=ACTIVITY_ITINERARY_BUILDER,
                llm_telemetry_sub_activity="build_itinerary_stream",
                llm_telemetry_meta=telemetry_meta(
                    session_id=session_id,
                    destination_id=did,
                    request_id=rid,
                    days=days,
                ),
                **cursor_run_persist_kwargs_for_lane(session_id or "", _lane, trace_path=_tp),
            )
    except Exception as e:
        log_itinerary_build(
            f"FAIL rid={rid} mode=sse dest_id={did!r} exc={type(e).__name__}: {e!s} "
            f"elapsed_ms={(time.perf_counter() - t0) * 1000.0:.1f}"
        )
        raise
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    (
        out,
        extracted,
        budget,
        booking_hints,
        needs_cf,
        transport_plan,
        booking_hooks,
        raw_wrapper,
    ) = _process_lazy_itinerary_raw(
        raw,
        trace_rid=rid,
        mode="sse",
        dest_id=did,
        n=n,
        profile_pills=profile_pills,
        destination=destination,
    )
    if _logistics_followup_retry_enabled() and out and _persisted_logistics_day_count(out) == 0:
        log_itinerary_build(
            f"LOGISTICS_RETRY_BEGIN rid={rid} mode=sse dest_id={did!r} itinerary_days={len(out)} n={n}"
        )
        repair = _logistics_repair_user_message(raw, n_days=n, profile_pills=profile_pills)
        t_retry = time.perf_counter()
        try:
            with basic_client(key) as client:
                raw2 = run_prompt_collect_assistant_text_streaming(
                    client,
                    repair,
                    emit=_safe_emit if emit else None,
                    model_id_override=light,
                    persistent_instruction_prefix=itin_prefix,
                    llm_telemetry_activity=ACTIVITY_ITINERARY_BUILDER,
                    llm_telemetry_sub_activity="build_itinerary_logistics_retry_stream",
                    llm_telemetry_meta=telemetry_meta(
                        session_id=session_id,
                        destination_id=did,
                        request_id=rid,
                        days=days,
                    ),
                    **cursor_run_persist_kwargs_for_lane(
                        session_id or "",
                        _lane,
                        trace_path=f"{_tp}.retry",
                    ),
                )
        except Exception as e:
            log_itinerary_build(
                f"LOGISTICS_RETRY_FAIL rid={rid} mode=sse dest_id={did!r} exc={type(e).__name__}: {e!s}"
            )
        else:
            (
                out2,
                extracted2,
                budget2,
                booking_hints2,
                needs_cf2,
                transport_plan2,
                booking_hooks2,
                raw_wrapper2,
            ) = _process_lazy_itinerary_raw(
                raw2,
                trace_rid=f"{rid}+logistics_retry",
                mode="sse",
                dest_id=did,
                n=n,
                profile_pills=profile_pills,
                destination=destination,
            )
            retry_ms = (time.perf_counter() - t_retry) * 1000.0
            if out2 and _persisted_logistics_day_count(out2) > 0:
                raw = raw2
                out = out2
                extracted = extracted2
                budget = budget2
                booking_hints = booking_hints2
                needs_cf = needs_cf2
                transport_plan = transport_plan2
                booking_hooks = booking_hooks2
                raw_wrapper = raw_wrapper2
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                log_itinerary_build(
                    f"LOGISTICS_RETRY_OK rid={rid} mode=sse dest_id={did!r} "
                    f"persisted_dl={_persisted_logistics_day_count(out)} elapsed_retry_ms={retry_ms:.1f}"
                )
            else:
                log_itinerary_build(
                    f"LOGISTICS_RETRY_NO_IMPROVEMENT rid={rid} mode=sse dest_id={did!r} "
                    f"persisted_dl_first={_persisted_logistics_day_count(out)} "
                    f"persisted_dl_second={_persisted_logistics_day_count(out2)} elapsed_retry_ms={retry_ms:.1f}"
                )

    def _run_qa_repair_sse(repair_prompt: str) -> str:
        with basic_client(key) as client:
            return run_prompt_collect_assistant_text_streaming(
                client,
                repair_prompt,
                emit=_safe_emit if emit else None,
                model_id_override=light,
                persistent_instruction_prefix=itin_prefix,
                llm_telemetry_activity=ACTIVITY_ITINERARY_BUILDER,
                llm_telemetry_sub_activity="build_itinerary_qa_retry_stream",
                llm_telemetry_meta=telemetry_meta(
                    session_id=session_id,
                    destination_id=did,
                    request_id=rid,
                    days=days,
                ),
                **cursor_run_persist_kwargs_for_lane(
                    session_id or "",
                    _lane,
                    trace_path=f"{_tp}.qa_retry",
                ),
            )

    (
        raw,
        out,
        extracted,
        budget,
        booking_hints,
        needs_cf,
        transport_plan,
        booking_hooks,
        raw_wrapper,
    ) = _apply_itinerary_qa_retry(
        raw=raw,
        out=out,
        extracted=extracted,
        budget=budget,
        booking_hints=booking_hints,
        needs_cf=needs_cf,
        transport_plan=transport_plan,
        booking_hooks=booking_hooks,
        raw_wrapper=raw_wrapper if isinstance(raw_wrapper, dict) else None,
        rid=rid,
        mode="sse",
        did=did,
        n=n,
        profile_pills=profile_pills,
        destination=destination,
        run_repair_prompt=_run_qa_repair_sse,
    )
    if out:
        transport_plan, needs_cf, booking_hints = ensure_transport_plan_after_extract(
            transport_plan=transport_plan,
            needs_commercial_flight=needs_cf,
            booking_search_hints=booking_hints,
            budget_guidance=budget,
            itinerary_rows=out,
            destination=destination,
            profile_pills=profile_pills,
            raw_wrapper=raw_wrapper,
        )
        booking_hooks = None
    log_itinerary_build(
        f"END rid={rid} mode=sse dest_id={did!r} raw_chars={len(raw or '')} extracted_len={len(extracted)} "
        f"normalized_len={len(out)} budget_keys={list(budget or [])} "
        f"transport_mode={(transport_plan or {}).get('primary_mode')!r} elapsed_ms={elapsed_ms:.1f}"
    )
    if not out:
        tail = (raw or "").replace("\n", " ").strip()[:500]
        log_itinerary_build(f"EMPTY rid={rid} mode=sse extracted_len={len(extracted)} raw_snippet={tail!r}")
        if extracted:
            _log_itinerary_normalize_all_dropped(rid, "sse", extracted)
    if not out and itinerary_debug_verbose():
        snippet = (raw or "").strip().replace("\r", "")[:2000]
        log_itinerary_build(f"DEBUG_EMPTY rid={rid} raw_head={snippet!r}")
    return _itinerary_client_response(
        itinerary_rows=out,
        budget=budget,
        booking_hints=booking_hints,
        needs_commercial_flight=needs_cf,
        transport_plan=transport_plan,
        booking_hooks=booking_hooks,
        outdoor_adventure=_outdoor_adventure_for_itinerary_response(
            raw_wrapper if isinstance(raw_wrapper, dict) else None,
            destination,
        ),
        itinerary_qa=_itinerary_qa_for_response(
            itinerary_rows=out,
            destination=destination,
            profile_pills=profile_pills,
        ),
        alltrails_search_context=_alltrails_context_for_itinerary_response(
            raw_wrapper if isinstance(raw_wrapper, dict) else None,
            destination,
            out,
        ),
    )


def default_trip_days_from_pills(profile_pills: list[dict[str, Any]]) -> int:
    """Rough day count from duration / when pills."""
    for p in profile_pills:
        if not isinstance(p, dict):
            continue
        if str(p.get("key") or "") != "duration":
            continue
        v = str(p.get("value") or "").lower()
        if "2+ week" in v or "14" in v or "10-14" in v:
            return 10
        if "week" in v and "2" in v:
            return 10
        if "7" in v or "week" in v:
            return 7
        if "3-4" in v or "4" in v:
            return 4
        if "5" in v:
            return 5
    return int(os.environ.get("WANDERLY_DEFAULT_ITINERARY_DAYS", "6") or "6")
