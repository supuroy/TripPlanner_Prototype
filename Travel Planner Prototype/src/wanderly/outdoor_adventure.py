"""Outdoor / backpacking trip profile helpers and schema normalization."""

from __future__ import annotations

import re
from typing import Any

TRIP_STYLE_KEY = "trip_style"
DEFAULT_TRIP_STYLE = "General leisure"

_CAMP_KINDS = frozenset({"backcountry", "campground", "hut", "hostel", "other"})


def profile_pill_value(profile_pills: list[dict[str, Any]] | None, key: str) -> str:
    k0 = (key or "").strip().lower()
    for p in profile_pills or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("key") or "").strip().lower() != k0:
            continue
        return str(p.get("value") or "").strip()
    return ""


def trip_style_from_profile(profile_pills: list[dict[str, Any]] | None) -> str:
    v = profile_pill_value(profile_pills, TRIP_STYLE_KEY)
    return v if v else DEFAULT_TRIP_STYLE


def is_backpacking_or_multiday_hike(profile_pills: list[dict[str, Any]] | None) -> bool:
    style = trip_style_from_profile(profile_pills).lower()
    return bool(
        re.search(
            r"\b(backpack|backpacking|multi[\s-]?day\s+hike|thru[\s-]?hike|wilderness\s+trek)\b",
            style,
        )
    )


def is_outdoor_adventure_profile(profile_pills: list[dict[str, Any]] | None) -> bool:
    style = trip_style_from_profile(profile_pills).lower()
    if is_backpacking_or_multiday_hike(profile_pills):
        return True
    return bool(
        re.search(r"\b(car\s+camp|campground|camping|road\s+trip|trail)\b", style)
    )


def infer_trip_style_from_text(*blobs: str | None) -> str:
    """Map free text (user message, vibe, notes) to a trip_style pill value."""
    blob = " ".join(str(b or "") for b in blobs).lower()
    if not blob.strip():
        return DEFAULT_TRIP_STYLE
    if re.search(
        r"\b(backpack|backpacking|multi[\s-]?day\s+hike|thru[\s-]?hike|wilderness\s+trek|long[\s-]?distance\s+trail)\b",
        blob,
    ):
        return "Backpacking / multi-day hike"
    if re.search(r"\b(car\s+camp|campground|camping\s+trip)\b", blob):
        return "Car camping"
    if re.search(r"\b(road\s+trip|driving\s+trip|drive\s+trip)\b", blob):
        return "Road trip"
    if re.search(r"\b(city\s+break|weekend\s+in\s+\w+|urban\s+getaway)\b", blob):
        return "City break"
    return DEFAULT_TRIP_STYLE


def normalize_trail_segment(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or raw.get("trail_name") or "").strip()
    if not name:
        return None
    out: dict[str, Any] = {"name": name[:120]}
    dist = raw.get("distance_mi")
    if isinstance(dist, (int, float)) and 0 <= float(dist) <= 9999:
        out["distance_mi"] = round(float(dist), 2)
    for fk in ("elevation_gain_ft", "elevation_loss_ft"):
        v = raw.get(fk)
        if isinstance(v, (int, float)) and 0 <= int(v) <= 999999:
            out[fk] = int(v)
    notes = raw.get("notes")
    if isinstance(notes, str) and notes.strip():
        out["notes"] = notes.strip()[:240]
    at_url = raw.get("alltrails_url")
    if isinstance(at_url, str) and at_url.strip():
        out["alltrails_url"] = at_url.strip()[:500]
    park = raw.get("park_name")
    if isinstance(park, str) and park.strip():
        out["park_name"] = park.strip()[:120]
    region = raw.get("region")
    if isinstance(region, str) and region.strip():
        out["region"] = region.strip()[:80]
    return out


def normalize_camp_night(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in _CAMP_KINDS:
        kind = "other"
    label = str(raw.get("label") or raw.get("name") or "").strip()
    if not label:
        return None
    out: dict[str, Any] = {"kind": kind, "label": label[:120]}
    if raw.get("permit_required") is True:
        out["permit_required"] = True
    elif raw.get("permit_required") is False:
        out["permit_required"] = False
    for fk in ("booking_hint", "notes"):
        v = raw.get(fk)
        if isinstance(v, str) and v.strip():
            out[fk] = v.strip()[:240]
    return out


def normalize_permit_requirement(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or raw.get("label") or "").strip()
    if not name:
        return None
    out: dict[str, Any] = {"name": name[:120]}
    agency = raw.get("agency")
    if isinstance(agency, str) and agency.strip():
        out["agency"] = agency.strip()[:120]
    for fk in ("quota_window", "booking_url_hint", "notes"):
        v = raw.get(fk)
        if isinstance(v, str) and v.strip():
            out[fk] = v.strip()[:240]
    if raw.get("required") is True:
        out["required"] = True
    return out


def normalize_outdoor_adventure(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    ts = str(raw.get("trip_style") or "").strip()
    if ts:
        out["trip_style"] = ts[:80]
    permits: list[dict[str, Any]] = []
    pr = raw.get("permit_requirements")
    if isinstance(pr, list):
        for row in pr[:12]:
            norm = normalize_permit_requirement(row)
            if norm:
                permits.append(norm)
    if permits:
        out["permit_requirements"] = permits
    camps: list[dict[str, Any]] = []
    cn = raw.get("camp_nights")
    if isinstance(cn, list):
        for row in cn[:20]:
            norm = normalize_camp_night(row)
            if norm:
                camps.append(norm)
    if camps:
        out["camp_nights"] = camps
    notes = raw.get("notes")
    if isinstance(notes, str) and notes.strip():
        out["notes"] = notes.strip()[:400]
    return out if out else None


_OUTDOOR_ITINERARY_BLOCK = """
--- OUTDOOR / BACKCOUNTRY TRIP (profile trip_style) ---

This traveler chose an outdoor adventure style. In addition to standard keys, include top-level
``outdoor_adventure`` on the JSON object:
{
  "outdoor_adventure": {
    "trip_style": "<mirror profile trip_style pill>",
    "permit_requirements": [
      {"name": "e.g. West Coast Trail permit", "agency": "Parks Canada", "quota_window": "lottery opens …", "required": true, "notes": "≤240 chars"}
    ],
    "camp_nights": [
      {"kind": "backcountry|campground|hut|hostel|other", "label": "official campsite or zone name", "permit_required": true, "booking_hint": "recreation.gov / park site", "notes": "bear cache, water, …"}
    ],
    "notes": "1–2 sentences on trailhead access, season, or gear assumptions"
  }
}

**Required on every trail/backcountry overnight** — ``day_logistics.camp_night``:
``{kind, label, permit_required?, booking_hint?, notes?}`` with the **specific campsite name** (e.g. "Michigan Creek campsite", "Darling River campsite") — not a vague region alone.
Use **camp_night** instead of **lodging** on backcountry nights; **lodging** only for gateway hotels before/after the trail.

**Required top-level for backpacking** — ``alltrails_search_context`` (see backpacking rules block above).

**Required on every main hiking day** — ``day_logistics.trail_segment`` (must match the primary ``walk`` surface_leg that day):
``{name, distance_mi, elevation_gain_ft, elevation_loss_ft, notes?, alltrails_url?, park_name?, region?}``
- ``name``: official trail name (e.g. "West Coast Trail — North section", "Heart Lake Trail")
- ``distance_mi``: that day's hiking distance (realistic estimate; one decimal ok)
- ``elevation_gain_ft`` and ``elevation_loss_ft``: non-negative integers for the day's hike (use 0 when flat)
Duplicate the same stats on the ``walk`` surface_leg as ``trail_name``, ``distance_mi``, ``elevation_gain_ft``, ``elevation_loss_ft``.

**HARD REQUIREMENT — ``walk`` rows in ``day_logistics.surface_legs`` (do not rely on prose alone):**
The route map and logistics UI read **structured** legs, not day ``items`` bullets. Putting the hike only in ``items`` (e.g. "Hike 9.1 mi…") without ``surface_legs`` is **invalid**.

On **every calendar day** that includes on-trail hiking between camps or trail points, ``surface_legs`` MUST include **at least one** leg with ``"kind": "walk"`` and **both**:
- ``from_label``: trailhead, prior camp, or named start (e.g. "Heart Lake Trailhead", "8H2 Sheridan Creek campground")
- ``to_label``: that night's camp or trail destination (e.g. "8H2 Sheridan Creek campground", "8C6 Snake River Ford campground")
- ``trail_name`` (recommended): same as ``trail_segment.name``
- ``distance_mi``, ``elevation_gain_ft``, ``elevation_loss_ft`` when known (mirror ``trail_segment``)

**Example hiking day (shape the model must follow):**
```json
"day": 2,
"label": "Heart Lake TH to 8H2 Sheridan Creek",
"day_logistics": {
  "camp_night": {"kind": "backcountry", "label": "8H2 Sheridan Creek campground", "permit_required": true},
  "lodging": null,
  "flight": null,
  "trail_segment": {
    "name": "Heart Lake Trail",
    "distance_mi": 9.1,
    "elevation_gain_ft": 691,
    "elevation_loss_ft": 1102
  },
  "surface_legs": [
    {"kind": "drive", "from_label": "Grant Village", "to_label": "Heart Lake Trailhead", "operator_hint": "park road"},
    {
      "kind": "walk",
      "from_label": "Heart Lake Trailhead",
      "to_label": "8H2 Sheridan Creek campground",
      "trail_name": "Heart Lake Trail",
      "distance_mi": 9.1,
      "elevation_gain_ft": 691,
      "elevation_loss_ft": 1102
    }
  ]
}
```

**Ordering:** List legs in travel order (e.g. ``drive`` to trailhead, then ``walk`` to camp). Gateway ``drive`` legs are encouraged on the first/last trail days.

**Day labels:** ``day.label`` should read ``<from> to <to>`` and **match** the primary ``walk`` leg's ``from_label`` / ``to_label`` (do not append unrelated segments after ``→``).

Rules for outdoor trips:
- **Every night on trail** must have ``camp_night.label`` naming where they sleep; mirror the list in ``outdoor_adventure.camp_nights``.
- Day **items** bullets should mention the campsite and trail stats in prose **and** must not replace ``surface_legs``.
- Mention permit lottery / quota windows in ``outdoor_adventure.permit_requirements`` — do not invent reservation numbers.
- Keep **transport_plan** for getting to the trail region; use **surface_legs** for drives/ferries to trailheads and **walk** legs for on-trail segments.
- Each day still needs **day_logistics** with at least one of: lodging, camp_night, flight segments, or surface_legs (hiking days: **walk** + usually **camp_night**).
""".strip()


_BACKPACKING_EXTRA = """
Backpacking-specific (hard requirements):
- Plan realistic daily mileage and elevation; include a resupply or zero day when trip length allows.
- **Every hiking day MUST include ``surface_legs`` with a ``walk`` leg** (from_label + to_label). **Never** describe the hike only in ``items`` without structured legs.
- **No trail day without ``trail_segment``** when that day has a ``walk`` leg between camps or trail points.
- **No backcountry overnight without ``camp_night``** — label must be the named campsite or designated camp zone (include site codes like 8H2 when the park uses them).
- Align ``outdoor_adventure.camp_nights[]`` with each night's ``day_logistics.camp_night.label``.
- On point-to-point routes, do **not** walk back to the start trailhead after day 1 unless the user explicitly requests a loop.
- Call out wilderness permits, timed entry, and campsite quotas explicitly.
""".strip()


def outdoor_itinerary_prompt_block(profile_pills: list[dict[str, Any]] | None) -> str:
    if not is_outdoor_adventure_profile(profile_pills):
        return ""
    from src.wanderly.alltrails_search_context import alltrails_search_context_prompt_block

    parts = [_OUTDOOR_ITINERARY_BLOCK]
    if is_backpacking_or_multiday_hike(profile_pills):
        parts.append(_BACKPACKING_EXTRA)
        parts.append(alltrails_search_context_prompt_block())
    return "\n\n".join(parts)


def outdoor_planner_destination_hints() -> str:
    """Rules for the initial smoke/refine planner when suggesting backpacking destinations."""
    return (
        "**Backpacking destination cards (planner):**\n"
        "- **One card = one named trail route in one national park** (e.g. \"Heart Lake Trail\", "
        "\"Shoshone Lake loop\"). The **name** and **hook** describe that single route only.\n"
        "- **Never** create combo/sampler cards that merge unrelated trails "
        "(no \"Heart Lake & Snake River\", no \"A + B sampler\", no cross-park mashups).\n"
        "- **AllTrails-first (U.S. & Canada):** Prefer routes with a well-known AllTrails page in the "
        "correct province/state — duplicate trail names exist worldwide (verify park + region).\n"
        "- **Stay in one park per card.** Do not combine two parks or unrelated trails on one card.\n"
        "- Suggest **2–4 separate cards** for different trails (compare options), not one card that tries "
        "to hike every trail in the trip."
    )


def outdoor_refine_itinerary_hints() -> str:
    return (
        "**Outdoor trip style:** Profile ``trip_style`` indicates hiking/camping. When emitting ``itinerary``, "
        "include ``outdoor_adventure`` (permits, camp_nights). On every backcountry night set "
        "``day_logistics.camp_night`` with the **named campsite**. On every main hiking day set "
        "``day_logistics.trail_segment`` AND at least one ``surface_legs`` entry with "
        "``kind\":\"walk\"``, ``from_label``, ``to_label`` (plus trail stats on the walk leg). "
        "Do not put hikes only in ``items`` bullets. Gateway-town ``lodging`` only before/after the trail."
    )


def outdoor_logistics_repair_append(profile_pills: list[dict[str, Any]] | None) -> str:
    if not is_backpacking_or_multiday_hike(profile_pills):
        if not is_outdoor_adventure_profile(profile_pills):
            return ""
        return (
            "\n\nOutdoor trip: include ``day_logistics.camp_night`` on camping nights, "
            "``trail_segment`` on hiking days, and **``surface_legs`` with ``kind\":\"walk\"``** "
            "(from_label + to_label) — not hike prose in ``items`` only."
        )
    return (
        "\n\nBackpacking trip — also required in ``day_logistics``:\n"
        "- ``camp_night`` on **every** backcountry overnight with the **official campsite name** in ``label``.\n"
        "- ``trail_segment`` on **every** hiking day: ``name``, ``distance_mi``, "
        "``elevation_gain_ft``, ``elevation_loss_ft`` (integers ≥ 0).\n"
        "- ``surface_legs`` on **every** hiking day with ≥1 ``walk`` leg: ``from_label``, ``to_label``, "
        "``trail_name``, and matching distance/elevation fields.\n"
        "- Top-level ``outdoor_adventure.camp_nights`` listing each night's camp.\n"
        "- Day ``label`` must match the walk leg endpoints (``A to B``)."
    )
