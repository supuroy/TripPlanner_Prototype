"""Load offline trail catalogs (park JSON + manifest)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_CATALOG_ROOT = Path(__file__).resolve().parents[3] / "data" / "trail_catalog"

_RANK_PREFIX_RE = re.compile(r"^#\d+\s*-\s*", re.I)


def catalog_root() -> Path:
    return _CATALOG_ROOT


def clean_trail_name(name: str) -> str:
    return _RANK_PREFIX_RE.sub("", (name or "").strip()).strip()


def resolve_park_code_from_text(text: str) -> str | None:
    from src.wanderly.nps_provider import _PARK_CODE_ALIASES

    low = (text or "").lower()
    # Longer phrases first to avoid partial matches.
    for phrase in sorted(_PARK_CODE_ALIASES, key=len, reverse=True):
        if phrase in low:
            return _PARK_CODE_ALIASES[phrase]
    return None


def park_catalog_path(park_code: str) -> Path:
    return catalog_root() / "parks" / f"{park_code.lower()}.json"


def feature_catalog_path(feature_slug: str) -> Path:
    return catalog_root() / "features" / f"{feature_slug.lower()}.json"


def load_feature_catalog(
    feature_slug: str,
    *,
    catalog_path: Path | None = None,
) -> dict[str, Any]:
    path = catalog_path or feature_catalog_path(feature_slug)
    if not path.is_file():
        raise FileNotFoundError(f"No feature catalog at {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    trails = doc.get("trails")
    if not isinstance(trails, list):
        raise ValueError(f"Invalid feature catalog (missing trails[]): {path}")
    return doc


def load_park_catalog(park_code: str, *, catalog_path: Path | None = None) -> dict[str, Any]:
    path = catalog_path or park_catalog_path(park_code)
    if not path.is_file():
        raise FileNotFoundError(f"No catalog at {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    trails = doc.get("trails")
    if not isinstance(trails, list):
        raise ValueError(f"Invalid catalog (missing trails[]): {path}")
    return doc


def entry_by_trail_id(doc: dict[str, Any], trail_id: str) -> dict[str, Any] | None:
    want = (trail_id or "").strip().lower()
    for row in doc.get("trails") or []:
        if isinstance(row, dict) and str(row.get("trail_id") or "").lower() == want:
            return row
    return None


def to_compact_row(entry: dict[str, Any]) -> dict[str, Any]:
    row = {
        "trail_id": entry.get("trail_id"),
        "name": clean_trail_name(str(entry.get("name") or "")),
        "park_code": entry.get("park_code"),
        "distance_mi": entry.get("length_mi") if entry.get("length_mi") is not None else entry.get("distance_mi"),
        "difficulty": entry.get("difficulty"),
        "route_type": entry.get("route_type"),
        "source_url": entry.get("source_url"),
        "tags": list(entry.get("tags") or []),
        "rating": entry.get("rating"),
    }
    if entry.get("location_label"):
        row["location_label"] = entry.get("location_label")
    for geo_key in ("country_code", "state_slug", "region_path", "state_name"):
        if entry.get(geo_key):
            row[geo_key] = entry.get(geo_key)
    return row


def load_park_trails_compact(
    park_code: str,
    *,
    catalog_path: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    doc = load_park_catalog(park_code, catalog_path=catalog_path)
    compact = [to_compact_row(t) for t in doc.get("trails") or [] if isinstance(t, dict)]
    return doc, compact


def prefilter_backpacking_candidates(
    compact: list[dict[str, Any]],
    *,
    min_distance_mi: float = 8.0,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in compact:
        tags = row.get("tags") or []
        dist = row.get("distance_mi")
        if "backpacking_candidate" in tags:
            out.append(row)
            continue
        if "long" in tags:
            out.append(row)
            continue
        if isinstance(dist, (int, float)) and float(dist) >= min_distance_mi:
            out.append(row)
    return out


def heuristic_pick_trails(
    candidates: list[dict[str, Any]],
    *,
    k: int = 2,
    prefer_slugs: tuple[str, ...] = ("heart-lake--2", "heart-lake", "shoshone-lake", "shoshone"),
) -> list[dict[str, Any]]:
    """Fallback when LLM unavailable: prefer known demo slugs, then longest distance."""
    picked: list[dict[str, Any]] = []
    remaining = list(candidates)
    for slug in prefer_slugs:
        for row in remaining:
            tid = str(row.get("trail_id") or "").lower()
            url = str(row.get("source_url") or "").lower()
            if slug in tid or slug in url:
                picked.append(row)
                remaining = [r for r in remaining if r is not row]
                break
        if len(picked) >= k:
            break
    ranked = sorted(
        remaining,
        key=lambda r: float(r.get("distance_mi") or 0),
        reverse=True,
    )
    for row in ranked:
        if len(picked) >= k:
            break
        if row not in picked:
            picked.append(row)
    return picked[:k]
