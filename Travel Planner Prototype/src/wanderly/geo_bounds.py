"""Park bounding boxes for coordinate sanity checks on backpacking trips."""

from __future__ import annotations

import re
from typing import Any

from src.wanderly.stock_photo_heroes import infer_hero_park_phrase

# (min_lat, max_lat, min_lng, max_lng) — approximate envelopes. Extend as needed.
PARK_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "yellowstone": (44.10, 45.15, -111.20, -109.75),
    "grand_teton": (43.55, 44.05, -110.95, -110.30),
    "yosemite": (37.50, 38.30, -119.90, -119.10),
    "zion": (37.10, 37.45, -113.10, -112.70),
    "olympic": (47.40, 48.30, -124.85, -123.10),
    "pacific_rim_bc": (48.55, 49.10, -125.85, -124.45),
}

# Substrings that map context text → ``PARK_BOUNDS`` key (data table, not branching logic).
PARK_BOUND_TRIGGERS: dict[str, tuple[str, ...]] = {
    "yellowstone": ("yellowstone",),
    "grand_teton": ("grand teton", "grand teton national"),
    "yosemite": ("yosemite",),
    "zion": ("zion national", "zion national park"),
    "olympic": ("olympic national",),
    "pacific_rim_bc": (
        "pacific rim",
        "west coast trail",
        "vancouver island",
        "british columbia",
    ),
}


def infer_park_bounds_key(
    *,
    park_phrase: str | None = None,
    destination_id: str | None = None,
    destination_name: str | None = None,
    destination_hook: str | None = None,
) -> str | None:
    """
    Resolve a ``PARK_BOUNDS`` key from destination context.

    Uses ``infer_hero_park_phrase`` when available, then substring triggers.
    Returns None when no envelope is known (coordinates are not rejected).
    """
    park = (park_phrase or "").strip() or infer_hero_park_phrase(
        destination_id=destination_id,
        name=destination_name,
        hook=destination_hook,
    )
    blob = " ".join(
        str(x or "")
        for x in (park, destination_id, destination_name, destination_hook)
    ).lower()
    if not blob.strip():
        return None
    for key, triggers in PARK_BOUND_TRIGGERS.items():
        if any(t in blob for t in triggers):
            return key
    if park:
        slug = re.sub(r"[^a-z0-9]+", "_", park.lower()).strip("_")
        if slug in PARK_BOUNDS:
            return slug
        for key in PARK_BOUNDS:
            if key.replace("_", " ") in park.lower():
                return key
    return None


def coords_within_bounds(lat: float, lng: float, bounds_key: str) -> bool:
    box = PARK_BOUNDS.get(bounds_key)
    if not box:
        return True
    min_lat, max_lat, min_lng, max_lng = box
    return min_lat <= lat <= max_lat and min_lng <= lng <= max_lng


def validate_coords_for_park(
    lat: float | None,
    lng: float | None,
    *,
    bounds_key: str | None,
) -> tuple[bool, str | None]:
    if bounds_key is None or lat is None or lng is None:
        return True, None
    if coords_within_bounds(float(lat), float(lng), bounds_key):
        return True, None
    label = bounds_key.replace("_", " ")
    return False, f"Coordinates ({lat}, {lng}) fall outside expected bounds for {label}."


def apply_geo_bounds_to_waypoint(
    wp: dict[str, Any],
    *,
    bounds_key: str | None,
) -> dict[str, Any]:
    """Clear lat/lng when outside the destination park envelope."""
    if not bounds_key:
        return wp
    lat, lng = wp.get("lat"), wp.get("lng")
    ok, reason = validate_coords_for_park(
        float(lat) if isinstance(lat, (int, float)) else None,
        float(lng) if isinstance(lng, (int, float)) else None,
        bounds_key=bounds_key,
    )
    if ok:
        return wp
    out = dict(wp)
    out["lat"] = None
    out["lng"] = None
    out["source"] = "rejected_out_of_bounds"
    if reason:
        out["geo_reject_reason"] = reason
    return out


def summarize_waypoint_geo_issues(
    days: list[dict[str, Any]],
    *,
    bounds_key: str | None,
) -> dict[str, Any]:
    """Aggregate out-of-bounds / unresolved points for advisor + sheet UI."""
    if not bounds_key:
        return {"bounds_key": None, "out_of_bounds": [], "rejected_count": 0}
    rejected: list[dict[str, Any]] = []
    for day in days:
        day_num = day.get("day")
        for role in ("hike_start", "hike_end", "camp"):
            pt = day.get(role)
            if not isinstance(pt, dict):
                continue
            if pt.get("source") == "rejected_out_of_bounds":
                rejected.append(
                    {
                        "day": day_num,
                        "role": role,
                        "label": pt.get("label"),
                        "reason": pt.get("geo_reject_reason"),
                    }
                )
    return {
        "bounds_key": bounds_key,
        "out_of_bounds": rejected,
        "rejected_count": len(rejected),
    }
