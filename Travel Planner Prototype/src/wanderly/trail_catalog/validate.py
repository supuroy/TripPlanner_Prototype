"""Server-side validation for catalog-grounded picks and itineraries."""

from __future__ import annotations

import re
from typing import Any

from src.wanderly.trail_catalog.store import clean_trail_name


def validate_trail_ids(
    trail_ids: list[str],
    allowed: set[str],
) -> list[str]:
    """Return invalid ids not in allowed set."""
    bad: list[str] = []
    for tid in trail_ids:
        key = (tid or "").strip().lower()
        if not key or key not in allowed:
            bad.append(tid)
    return bad


def validate_itinerary_against_catalog(
    itinerary: dict[str, Any],
    *,
    catalog_entry: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    trail_id = str(catalog_entry.get("trail_id") or "")
    source_url = str(catalog_entry.get("source_url") or "")
    name = clean_trail_name(str(catalog_entry.get("name") or ""))
    grounding = itinerary.get("grounding") if isinstance(itinerary.get("grounding"), dict) else {}
    if str(grounding.get("trail_id") or "") != trail_id:
        errors.append(f"grounding.trail_id must be {trail_id!r}")
    if str(grounding.get("source_url") or "") != source_url:
        errors.append(f"grounding.source_url must match catalog {source_url!r}")

    blob = _itinerary_text_blob(itinerary)
    if source_url and source_url not in blob:
        errors.append("itinerary text never references catalog source_url")
    if name and not _name_mentioned(name, blob):
        errors.append(f"itinerary should reference trail name {name!r}")

    rows = itinerary.get("itinerary")
    if isinstance(rows, list):
        days = [r for r in rows if isinstance(r, dict)]
        if not days:
            errors.append("itinerary[] is empty")
        for row in days:
            dl = row.get("day_logistics")
            if not isinstance(dl, dict):
                errors.append(f"day {row.get('day')}: missing day_logistics")
    else:
        errors.append("missing itinerary[] array")

    return errors


def _itinerary_text_blob(itinerary: dict[str, Any]) -> str:
    import json

    return json.dumps(itinerary, ensure_ascii=True).lower()


def _name_mentioned(name: str, blob: str) -> bool:
    low = blob.lower()
    full = name.lower()
    if full in low:
        return True
    # "Heart Lake Trail" -> require heart lake
    core = re.sub(r"\b(trail|loop|hike)\b", "", full, flags=re.I).strip()
    if len(core) >= 6 and core in low:
        return True
    parts = [p for p in re.split(r"\s+", core) if len(p) > 3]
    return bool(parts) and parts[0] in low
