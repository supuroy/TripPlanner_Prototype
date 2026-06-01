"""Structured trail catalog search (shared by Wanderly + MCP spike)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.wanderly.trail_catalog.store import (
    catalog_root,
    clean_trail_name,
    load_feature_catalog,
    load_park_catalog,
    prefilter_backpacking_candidates,
    to_compact_row,
)


def load_manifest_parks() -> list[dict[str, Any]]:
    path = catalog_root() / "manifest.json"
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    parks = doc.get("parks")
    return [p for p in parks if isinstance(p, dict)] if isinstance(parks, list) else []


def search_manifest_parks(
    query: str = "",
    *,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Find parks by name substring (for resolving Yellowstone, etc.)."""
    q = (query or "").strip().lower()
    rows = load_manifest_parks()
    if not q:
        return rows[:limit]
    out: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("park_name") or "").lower()
        code = str(row.get("park_code") or "").lower()
        if q in name or q in code:
            out.append(
                {
                    "park_code": row.get("park_code"),
                    "park_name": row.get("park_name"),
                    "trail_count": row.get("trail_count"),
                }
            )
        if len(out) >= limit:
            break
    return out


def search_trails(
    park_code: str,
    *,
    query: str = "",
    limit: int = 25,
    backpacking_only: bool = True,
    min_distance_mi: float = 0.0,
) -> list[dict[str, Any]]:
    """Return compact trail rows for one park (for MCP tools or server-side retrieval)."""
    code = (park_code or "").strip().lower()
    if not code:
        return []
    doc = load_park_catalog(code)
    compact = [to_compact_row(t) for t in doc.get("trails") or [] if isinstance(t, dict)]
    if backpacking_only:
        compact = prefilter_backpacking_candidates(compact)
    q = (query or "").strip().lower()
    if q:
        tokens = [t for t in re.split(r"\W+", q) if len(t) >= 3]

        def matches(row: dict[str, Any]) -> bool:
            blob = " ".join(
                str(row.get(k) or "")
                for k in ("name", "trail_id", "difficulty", "route_type")
            ).lower()
            blob += " " + " ".join(str(x) for x in (row.get("tags") or [])).lower()
            if q in blob:
                return True
            return any(t in blob for t in tokens)

        compact = [r for r in compact if matches(r)]
    if min_distance_mi > 0:
        compact = [
            r
            for r in compact
            if isinstance(r.get("distance_mi"), (int, float))
            and float(r["distance_mi"]) >= min_distance_mi
        ]
    ranked = sorted(
        compact,
        key=lambda r: float(r.get("distance_mi") or 0),
        reverse=True,
    )
    return ranked[: max(1, min(int(limit), 50))]


def get_trail_compact(park_code: str, trail_id: str) -> dict[str, Any] | None:
    code = (park_code or "").strip().lower()
    tid = (trail_id or "").strip().lower()
    if not code or not tid:
        return None
    doc = load_park_catalog(code)
    for row in doc.get("trails") or []:
        if isinstance(row, dict) and str(row.get("trail_id") or "").lower() == tid:
            return to_compact_row(row)
    return None


def load_manifest_features() -> list[dict[str, Any]]:
    path = catalog_root() / "features_manifest.json"
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    features = doc.get("features")
    return [f for f in features if isinstance(f, dict)] if isinstance(features, list) else []


def search_manifest_features(
    query: str = "",
    *,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Find trail features by slug or display name (waterfall, camping, …)."""
    q = (query or "").strip().lower()
    rows = load_manifest_features()
    if not q:
        return rows[:limit]
    out: list[dict[str, Any]] = []
    for row in rows:
        slug = str(row.get("feature_slug") or "").lower()
        name = str(row.get("feature_name") or "").lower()
        if q in slug or q in name:
            out.append(
                {
                    "feature_slug": row.get("feature_slug"),
                    "feature_name": row.get("feature_name"),
                    "trail_count": row.get("trail_count"),
                }
            )
        if len(out) >= limit:
            break
    return out


def load_manifest_regions() -> list[dict[str, Any]]:
    path = catalog_root() / "regions_manifest.json"
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    regions = doc.get("regions")
    return [r for r in regions if isinstance(r, dict)] if isinstance(regions, list) else []


def search_manifest_regions(
    query: str = "",
    *,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Find US regions/states by name or slug (requires ``regions_manifest.json``)."""
    q = (query or "").strip().lower()
    rows = load_manifest_regions()
    if not q:
        return rows[:limit]
    out: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("region_name") or "").lower()
        slug = str(row.get("state_slug") or row.get("region_slug") or "").lower()
        if q in name or q in slug or q.replace(" ", "-") in slug:
            out.append(
                {
                    "region_slug": row.get("region_slug"),
                    "state_slug": row.get("state_slug"),
                    "region_name": row.get("region_name"),
                    "listing_url": row.get("listing_url"),
                    "park_codes": row.get("park_codes"),
                    "park_count": row.get("park_count"),
                }
            )
        if len(out) >= limit:
            break
    return out


def load_region_catalog(state_slug: str) -> dict[str, Any]:
    slug = (state_slug or "").strip().lower().replace("us-", "")
    path = catalog_root() / "regions" / f"us-{slug}.json"
    if not path.is_file():
        raise FileNotFoundError(f"No region catalog at {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    trails = doc.get("trails")
    if not isinstance(trails, list):
        raise ValueError(f"Invalid region catalog (missing trails[]): {path}")
    return doc


def search_region_trails(
    state_slug: str,
    *,
    query: str = "",
    limit: int = 25,
    min_distance_mi: float = 0.0,
    max_distance_mi: float = 0.0,
    park_code: str = "",
) -> list[dict[str, Any]]:
    """Search trails from ``regions/us-{state}.json`` (Top-50-style region hub crawl)."""
    doc = load_region_catalog(state_slug)
    compact = [to_compact_row(t) for t in doc.get("trails") or [] if isinstance(t, dict)]
    pc = (park_code or "").strip().lower()
    if pc:
        compact = [r for r in compact if str(r.get("park_code") or "").lower() == pc]
    q = (query or "").strip().lower()
    if q:
        tokens = [t for t in re.split(r"\W+", q) if len(t) >= 3]

        def matches(row: dict[str, Any]) -> bool:
            blob = " ".join(
                str(row.get(k) or "")
                for k in ("name", "trail_id", "difficulty", "location_label")
            ).lower()
            if q in blob:
                return True
            return any(t in blob for t in tokens)

        compact = [r for r in compact if matches(r)]
    if min_distance_mi > 0:
        compact = [
            r
            for r in compact
            if isinstance(r.get("distance_mi"), (int, float))
            and float(r["distance_mi"]) >= min_distance_mi
        ]
    if max_distance_mi > 0:
        compact = [
            r
            for r in compact
            if isinstance(r.get("distance_mi"), (int, float))
            and float(r["distance_mi"]) <= max_distance_mi
        ]
    ranked = sorted(compact, key=lambda r: float(r.get("distance_mi") or 0), reverse=True)
    return ranked[: max(1, min(int(limit), 50))]


def parks_for_state(state_slug: str) -> list[str]:
    """Park codes in a US state from ``regions_manifest.json`` or park files."""
    slug = (state_slug or "").strip().lower().replace("us-", "")
    for row in load_manifest_regions():
        if str(row.get("state_slug") or "").lower() == slug:
            codes = row.get("park_codes")
            if isinstance(codes, list):
                return [str(c).lower() for c in codes if c]
    from src.wanderly.trail_catalog.enrich import build_park_codes_by_state

    return list(build_park_codes_by_state(catalog_root()).get(slug) or [])


def search_feature_trails(
    feature_slug: str,
    *,
    query: str = "",
    limit: int = 25,
    min_distance_mi: float = 0.0,
    max_distance_mi: float = 0.0,
    park_code: str = "",
    state_slug: str = "",
    region_path: str = "",
) -> list[dict[str, Any]]:
    """Return compact trail rows from a feature catalog (``features/{slug}.json``)."""
    slug = (feature_slug or "").strip().lower()
    if not slug:
        return []
    doc = load_feature_catalog(slug)
    compact = [to_compact_row(t) for t in doc.get("trails") or [] if isinstance(t, dict)]
    pc = (park_code or "").strip().lower()
    if pc:
        compact = [r for r in compact if str(r.get("park_code") or "").lower() == pc]
    st = (state_slug or "").strip().lower().replace("us-", "")
    if st:
        compact = [
            r
            for r in compact
            if str(r.get("state_slug") or "").lower() == st
            or str(r.get("region_path") or "").lower() == f"us/{st}"
            or f":us-{st}-" in str(r.get("trail_id") or "").lower()
        ]
    rp = (region_path or "").strip().lower()
    if rp:
        compact = [
            r
            for r in compact
            if str(r.get("region_path") or "").lower() == rp
            or str(r.get("trail_id") or "").lower().startswith(f"{slug}:{rp.replace('/', '-')}")
        ]
    q = (query or "").strip().lower()
    if q:
        tokens = [t for t in re.split(r"\W+", q) if len(t) >= 3]

        def matches(row: dict[str, Any]) -> bool:
            blob = " ".join(
                str(row.get(k) or "")
                for k in ("name", "trail_id", "difficulty", "location_label")
            ).lower()
            if q in blob:
                return True
            return any(t in blob for t in tokens)

        compact = [r for r in compact if matches(r)]
    if min_distance_mi > 0:
        compact = [
            r
            for r in compact
            if isinstance(r.get("distance_mi"), (int, float))
            and float(r["distance_mi"]) >= min_distance_mi
        ]
    if max_distance_mi > 0:
        compact = [
            r
            for r in compact
            if isinstance(r.get("distance_mi"), (int, float))
            and float(r["distance_mi"]) <= max_distance_mi
        ]
    ranked = sorted(
        compact,
        key=lambda r: float(r.get("distance_mi") or 0),
        reverse=True,
    )
    return ranked[: max(1, min(int(limit), 50))]


def get_feature_trail_compact(feature_slug: str, trail_id: str) -> dict[str, Any] | None:
    slug = (feature_slug or "").strip().lower()
    tid = (trail_id or "").strip().lower()
    if not slug or not tid:
        return None
    doc = load_feature_catalog(slug)
    for row in doc.get("trails") or []:
        if isinstance(row, dict) and str(row.get("trail_id") or "").lower() == tid:
            return to_compact_row(row)
    return None


def catalog_stats() -> dict[str, Any]:
    parks = load_manifest_parks()
    features = load_manifest_features()
    regions = load_manifest_regions()
    total_park_trails = sum(int(p.get("trail_count") or 0) for p in parks)
    total_feature_trails = sum(int(f.get("trail_count") or 0) for f in features)
    root = catalog_root()
    region_files = len(list((root / "regions").glob("*.json"))) if (root / "regions").is_dir() else 0
    unique_canonical = 0
    index_path = root / "trail_index.json"
    if index_path.is_file():
        try:
            doc = json.loads(index_path.read_text(encoding="utf-8"))
            unique_canonical = int(doc.get("unique_trail_count") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            unique_canonical = 0
    return {
        "park_count": len(parks),
        "feature_count": len(features),
        "region_count": len(regions),
        "region_catalog_files": region_files,
        "manifest_park_trail_rows": total_park_trails,
        "manifest_feature_trail_rows": total_feature_trails,
        "unique_canonical_trails": unique_canonical,
        "trail_index_path": str(index_path) if index_path.is_file() else None,
        "catalog_root": str(root),
    }
