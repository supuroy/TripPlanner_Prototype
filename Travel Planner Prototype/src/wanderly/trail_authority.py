"""
Backpacking itinerary rules — generic prompts and light post-build checks.

Trail reference URLs may appear on ``alltrails_search_context`` or ``grounding`` on the
destination (catalog or LLM-emitted at itinerary build). No live AllTrails / Apify lookups.
"""

from __future__ import annotations

import re
from typing import Any

from src.wanderly.alltrails_search_context import (
    catalog_trail_anchor_fallback,
    derive_alltrails_search_context,
    normalize_alltrails_search_context,
    preferred_alltrails_urls_from_destination,
)
from src.wanderly.route_waypoints import _labels_fuzzy_match

ROUTE_POINT_TO_POINT = "point_to_point"
ROUTE_LOOP = "loop"
ROUTE_OUT_AND_BACK = "out_and_back"


def generic_backpacking_authority_prompt() -> str:
    """Applied to every backpacking / multi-day hike itinerary build."""
    return (
        "--- BACKPACKING / TRAIL AUTHORITY (U.S. & Canada) ---\n"
        "Wilderness plans must be **verifiable**, not invented.\n"
        "- **Primary source:** The named trail on this destination card and official park permit "
        "guidance; mirror plausible stats in `trail_segment` and `walk` legs.\n"
        "- **Sources:** Official park permit site codes; never invent mileage or camps.\n"
        "- **Point-to-point trails:** Never add a `walk` leg that returns to the **start trailhead** "
        "after the first hiking day.\n"
        "- **Each hiking day** with a `walk` leg requires `day_logistics.trail_segment` "
        "(`name`, `distance_mi`, `elevation_gain_ft`, `elevation_loss_ft`) matching that leg's "
        "`from_label` → `to_label`.\n"
        "- **Camps:** Named `camp_night.label` on every backcountry overnight; mirror in `outdoor_adventure.camp_nights`.\n"
        "- **Day `label`** must match the primary walk endpoints (not a stale or shortened title).\n"
        "- **Exits:** Half-trip and shuttle exits must use the published exit point for that trail "
        "(do not route back to the opposite terminus unless it is an out-and-back route)."
    )


def authoritative_trail_prompt_block(
    destination: dict[str, Any] | None,
    *,
    include_generic_backpacking: bool = True,
) -> str:
    parts: list[str] = []
    if include_generic_backpacking:
        parts.append(generic_backpacking_authority_prompt())
    ctx = derive_alltrails_search_context(destination)
    if ctx:
        import json

        parts.append(
            "--- Persisted AllTrails context (emit matching top-level JSON) ---\n"
            f"{json.dumps(ctx, ensure_ascii=False)}"
        )
    return "\n\n".join(p for p in parts if p.strip())


def _walk_legs(dl: dict[str, Any]) -> list[dict[str, Any]]:
    legs = dl.get("surface_legs")
    if not isinstance(legs, list):
        return []
    return [
        leg
        for leg in legs
        if isinstance(leg, dict) and (leg.get("kind") or "").lower() == "walk"
    ]


def _first_trailhead_label(rows: list[dict[str, Any]]) -> str | None:
    for row in sorted(rows, key=lambda r: int(r.get("day") or 0)):
        dl = row.get("day_logistics")
        if not isinstance(dl, dict):
            continue
        walks = _walk_legs(dl)
        if not walks:
            continue
        fr = str(walks[0].get("from_label") or "").strip()
        if fr:
            return fr
    return None


def enforce_trail_authority_for_destination(
    itinerary_rows: list[dict[str, Any]],
    destination: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Light post-build fixes — no catalog segment replacement.

    Currently: detect illegal return-to-trailhead on point-to-point style routes.
    """
    warnings: list[str] = []
    rows = [dict(r) for r in itinerary_rows]
    trailhead = _first_trailhead_label(rows)
    if not trailhead:
        return rows, warnings

    for row in sorted(rows, key=lambda r: int(r.get("day") or 0)):
        day_num = int(row.get("day") or 0)
        if day_num <= 1:
            continue
        dl = row.get("day_logistics")
        if not isinstance(dl, dict):
            continue
        for leg in _walk_legs(dl):
            to_lab = str(leg.get("to_label") or "").strip()
            if to_lab and _labels_fuzzy_match(to_lab, trailhead):
                if "trailhead" not in to_lab.lower():
                    warnings.append(
                        f"Day {day_num}: walk returns toward day-1 trailhead on a point-to-point route."
                    )
                break
    return rows, warnings


def build_outdoor_adventure_for_destination(
    destination: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """No catalog merge — outdoor_adventure comes from the LLM itinerary payload only."""
    return None


def preferred_alltrails_url_for_leg(
    destination: dict[str, Any] | None,
    *,
    from_label: str | None = None,
    to_label: str | None = None,
    trail_name: str | None = None,
) -> str | None:
    urls = preferred_alltrails_urls_from_destination(
        destination,
        trail_name=trail_name,
        from_label=from_label,
        to_label=to_label,
    )
    return urls[0] if urls else None


def resolve_preferred_alltrails_url_for_lookup(
    destination: dict[str, Any] | None,
    *,
    trail_name: str | None = None,
    from_label: str | None = None,
    to_label: str | None = None,
    itinerary_rows: list[dict[str, Any]] | None = None,
) -> str | None:
    if destination and itinerary_rows:
        dest = {**destination, "itinerary": itinerary_rows}
    else:
        dest = destination
    urls = preferred_alltrails_urls_from_destination(
        dest,
        trail_name=trail_name,
        from_label=from_label,
        to_label=to_label,
    )
    return urls[0] if urls else None


def collect_catalog_alltrails_urls(
    destination: dict[str, Any] | None,
    *,
    trail_name: str | None = None,
    from_label: str | None = None,
    to_label: str | None = None,
) -> list[str]:
    """Backward-compatible name — reads persisted ``alltrails_search_context`` only."""
    return preferred_alltrails_urls_from_destination(
        destination,
        trail_name=trail_name,
        from_label=from_label,
        to_label=to_label,
    )


def destination_alltrails_authority_block(destination: dict[str, Any] | None) -> dict[str, Any] | None:
    """Slim JSON for itinerary builder context — from ``alltrails_search_context``."""
    ctx = derive_alltrails_search_context(destination)
    if not ctx:
        return None
    return {
        "trail_name": ctx.get("trail_name"),
        "parent_alltrails_url": ctx.get("parent_alltrails_url"),
        "park_name": ctx.get("park_name"),
        "region": ctx.get("region"),
        "country": ctx.get("country"),
        "search_query": ctx.get("search_query"),
        "disambiguation": ctx.get("disambiguation"),
    }


def enrich_destination_alltrails_authority(destination: dict[str, Any]) -> dict[str, Any]:
    """Stamp ``alltrails_search_context`` when derivable from the card."""
    ctx = derive_alltrails_search_context(destination)
    if not ctx:
        return destination
    out = dict(destination)
    out["alltrails_search_context"] = ctx
    block = destination_alltrails_authority_block(out)
    if block:
        out["alltrails_authority"] = block
    return out


__all__ = [
    "ROUTE_POINT_TO_POINT",
    "ROUTE_LOOP",
    "ROUTE_OUT_AND_BACK",
    "authoritative_trail_prompt_block",
    "catalog_trail_anchor_fallback",
    "collect_catalog_alltrails_urls",
    "derive_alltrails_search_context",
    "destination_alltrails_authority_block",
    "enforce_trail_authority_for_destination",
    "enrich_destination_alltrails_authority",
    "normalize_alltrails_search_context",
    "preferred_alltrails_url_for_leg",
    "resolve_preferred_alltrails_url_for_lookup",
]
