"""Offline enrichment for feature (and region) catalog rows from existing JSON."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.wanderly.alltrails_search_context import alltrails_region_path_from_url
from src.wanderly.trail_catalog.canonical import attach_canonical_fields
from src.wanderly.trail_catalog.resolve_park import (
    _normalize_trail_url,
    build_trail_url_to_park_index,
    resolve_park_code,
    trail_id_from_url,
)

_TRAIL_ID_GEO_RE = re.compile(
    r"^[a-z0-9-]+:(?P<country>[a-z]{2})-(?P<state>[^-]+)-",
    re.I,
)

_US_STATE_SLUG_TO_NAME: dict[str, str] = {
    "alabama": "Alabama",
    "alaska": "Alaska",
    "arizona": "Arizona",
    "arkansas": "Arkansas",
    "california": "California",
    "colorado": "Colorado",
    "connecticut": "Connecticut",
    "delaware": "Delaware",
    "florida": "Florida",
    "georgia": "Georgia",
    "hawaii": "Hawaii",
    "idaho": "Idaho",
    "illinois": "Illinois",
    "indiana": "Indiana",
    "iowa": "Iowa",
    "kansas": "Kansas",
    "kentucky": "Kentucky",
    "louisiana": "Louisiana",
    "maine": "Maine",
    "maryland": "Maryland",
    "massachusetts": "Massachusetts",
    "michigan": "Michigan",
    "minnesota": "Minnesota",
    "mississippi": "Mississippi",
    "missouri": "Missouri",
    "montana": "Montana",
    "nebraska": "Nebraska",
    "nevada": "Nevada",
    "new-hampshire": "New Hampshire",
    "new-jersey": "New Jersey",
    "new-mexico": "New Mexico",
    "new-york": "New York",
    "north-carolina": "North Carolina",
    "north-dakota": "North Dakota",
    "ohio": "Ohio",
    "oklahoma": "Oklahoma",
    "oregon": "Oregon",
    "pennsylvania": "Pennsylvania",
    "rhode-island": "Rhode Island",
    "south-carolina": "South Carolina",
    "south-dakota": "South Dakota",
    "tennessee": "Tennessee",
    "texas": "Texas",
    "utah": "Utah",
    "vermont": "Vermont",
    "virginia": "Virginia",
    "washington": "Washington",
    "west-virginia": "West Virginia",
    "wisconsin": "Wisconsin",
    "wyoming": "Wyoming",
    "district-of-columbia": "District of Columbia",
    "washington-dc": "Washington DC",
}


def state_name_from_slug(slug: str) -> str | None:
    s = (slug or "").strip().lower()
    if not s:
        return None
    return _US_STATE_SLUG_TO_NAME.get(s) or s.replace("-", " ").title()


def geo_from_trail_id(trail_id: str) -> dict[str, str]:
    """Parse ``feature:us-utah-…`` -> country + state slug."""
    tid = (trail_id or "").strip().lower()
    m = _TRAIL_ID_GEO_RE.match(tid)
    if not m:
        return {}
    country = m.group("country").lower()
    state = m.group("state").lower()
    out = {"country_code": country, "state_slug": state}
    if country == "us":
        out["region_path"] = f"us/{state}"
        name = state_name_from_slug(state)
        if name:
            out["state_name"] = name
    return out


def geo_from_source_url(url: str) -> dict[str, str]:
    path = alltrails_region_path_from_url(url)
    if not path:
        return {}
    parts = path.split("/")
    if len(parts) < 2:
        return {}
    country, state = parts[0].lower(), parts[1].lower()
    out: dict[str, str] = {"country_code": country, "state_slug": state, "region_path": path}
    if country == "us":
        name = state_name_from_slug(state)
        if name:
            out["state_name"] = name
    return out


def build_park_codes_by_state(catalog_root: Path) -> dict[str, list[str]]:
    """Map state slug (``utah``) -> park_code list from park catalog ``region`` field."""
    by_state: dict[str, list[str]] = {}
    parks_dir = catalog_root / "parks"
    if not parks_dir.is_dir():
        return by_state
    for path in sorted(parks_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        code = str(doc.get("park_code") or path.stem).strip().lower()
        region = str(doc.get("region") or "").strip()
        if not region:
            continue
        slug = region.lower().replace(" ", "-")
        if slug == "washington dc":
            slug = "washington-dc"
        by_state.setdefault(slug, [])
        if code not in by_state[slug]:
            by_state[slug].append(code)
    return by_state


def enrich_feature_trail_entry(
    entry: dict[str, Any],
    *,
    feature_slug: str,
    url_index: dict[str, str],
    parks_by_state: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Fill geo + park_code on an existing catalog row (mutates copy)."""
    row = dict(entry)
    url = _normalize_trail_url(str(row.get("source_url") or ""))
    if url and not row.get("source_url"):
        row["source_url"] = url

    tid = str(row.get("trail_id") or "").strip()
    if not tid and url:
        tid = trail_id_from_url(url, feature_slug=feature_slug)
        row["trail_id"] = tid

    geo: dict[str, str] = {}
    geo.update(geo_from_trail_id(tid))
    if url:
        for k, v in geo_from_source_url(url).items():
            geo.setdefault(k, v)

    for key in ("country_code", "state_slug", "region_path", "state_name"):
        if geo.get(key) and not row.get(key):
            row[key] = geo[key]

    if not row.get("park_code"):
        crawl_row = {
            "url": url,
            "source_url": url,
            "park_url": row.get("park_url"),
            "location_label": row.get("location_label"),
        }
        code = resolve_park_code(crawl_row, url_index=url_index)
        if code:
            row["park_code"] = code

    return attach_canonical_fields(row)


def enrich_feature_catalog_doc(
    doc: dict[str, Any],
    *,
    catalog_root: Path,
    url_index: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Return enriched doc + stats."""
    feature_slug = str(doc.get("feature_slug") or "").strip().lower()
    if not feature_slug:
        raise ValueError("feature catalog missing feature_slug")

    index = url_index if url_index is not None else build_trail_url_to_park_index(catalog_root)
    parks_by_state = build_park_codes_by_state(catalog_root)
    trails = doc.get("trails")
    if not isinstance(trails, list):
        raise ValueError("feature catalog missing trails[]")

    stats = {
        "total": len(trails),
        "park_code_before": 0,
        "park_code_after": 0,
        "state_slug_after": 0,
        "region_path_after": 0,
        "updated_rows": 0,
    }
    out_trails: list[dict[str, Any]] = []
    for raw in trails:
        if not isinstance(raw, dict):
            continue
        before_pc = bool(raw.get("park_code"))
        if before_pc:
            stats["park_code_before"] += 1
        enriched = enrich_feature_trail_entry(
            raw,
            feature_slug=feature_slug,
            url_index=index,
            parks_by_state=parks_by_state,
        )
        after_pc = bool(enriched.get("park_code"))
        if after_pc:
            stats["park_code_after"] += 1
        if enriched.get("state_slug"):
            stats["state_slug_after"] += 1
        if enriched.get("region_path"):
            stats["region_path_after"] += 1
        if enriched != raw:
            stats["updated_rows"] += 1
        out_trails.append(enriched)

    written_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = dict(doc)
    out["trails"] = out_trails
    out["trail_count"] = len(out_trails)
    enrich_meta = dict(out.get("enrichment") or {}) if isinstance(out.get("enrichment"), dict) else {}
    enrich_meta["last_enriched_utc"] = written_at
    enrich_meta["stats"] = stats
    out["enrichment"] = enrich_meta
    out["written_at_utc"] = written_at
    return out, stats


def enrich_feature_catalog_file(
    path: Path,
    *,
    catalog_root: Path | None = None,
    dry_run: bool = False,
    url_index: dict[str, str] | None = None,
) -> dict[str, int]:
    root = catalog_root or path.parent.parent
    doc = json.loads(path.read_text(encoding="utf-8"))
    enriched, stats = enrich_feature_catalog_doc(doc, catalog_root=root, url_index=url_index)
    if not dry_run:
        path.write_text(json.dumps(enriched, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return stats


def enrich_region_trail_entry(
    entry: dict[str, Any],
    *,
    state_slug: str,
    url_index: dict[str, str],
) -> dict[str, Any]:
    """Fill geo + park_code on a region-catalog trail row."""
    row = dict(entry)
    slug = (state_slug or "").strip().lower().replace("us-", "")
    url = _normalize_trail_url(str(row.get("source_url") or ""))
    if url:
        row["source_url"] = url
    if slug:
        row.setdefault("state_slug", slug)
        row.setdefault("country_code", "us")
        row.setdefault("region_path", f"us/{slug}")
        name = state_name_from_slug(slug)
        if name:
            row.setdefault("state_name", name)
    if not row.get("park_code") and url:
        code = resolve_park_code(
            {
                "url": url,
                "source_url": url,
                "park_url": row.get("park_url"),
                "location_label": row.get("location_label"),
            },
            url_index=url_index,
        )
        if code:
            row["park_code"] = code
    return row


def enrich_region_trail_rows(
    rows: list[dict[str, Any]],
    *,
    state_slug: str,
    catalog_root: Path,
    url_index: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    from src.wanderly.alltrails_region_directory import trail_id_for_region_trail

    index = url_index if url_index is not None else build_trail_url_to_park_index(catalog_root)
    slug = (state_slug or "").strip().lower().replace("us-", "")
    out: list[dict[str, Any]] = []
    for raw in rows:
        url = _normalize_trail_url(str(raw.get("url") or raw.get("source_url") or ""))
        if not url:
            continue
        base: dict[str, Any] = {
            "trail_id": trail_id_for_region_trail(url, state_slug=slug),
            "source_url": url,
            "name": str(raw.get("name") or "").strip(),
            "difficulty": raw.get("difficulty"),
            "length_mi": raw.get("length_mi"),
            "rating": raw.get("rating"),
            "location_label": raw.get("location_label"),
            "park_url": raw.get("park_url"),
        }
        entry = enrich_region_trail_entry(
            {k: v for k, v in base.items() if v is not None},
            state_slug=slug,
            url_index=index,
        )
        out.append({k: v for k, v in entry.items() if v is not None})
    return out


def enrich_all_feature_catalogs(
    catalog_root: Path,
    *,
    slugs: list[str] | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    features_dir = catalog_root / "features"
    if not features_dir.is_dir():
        return []
    index = build_trail_url_to_park_index(catalog_root)
    summaries: list[dict[str, Any]] = []
    paths = sorted(features_dir.glob("*.json"))
    for path in paths:
        slug = path.stem.lower()
        if slugs and slug not in {s.lower() for s in slugs}:
            continue
        stats = enrich_feature_catalog_file(
            path,
            catalog_root=catalog_root,
            dry_run=dry_run,
            url_index=index,
        )
        summaries.append({"feature_slug": slug, "file": str(path), **stats})
    return summaries
