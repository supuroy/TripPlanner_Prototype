"""Write ``regions_manifest.json`` and optional per-region metadata files."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.wanderly.trail_catalog.enrich import build_park_codes_by_state, state_name_from_slug


def merge_regions_with_park_catalog(
    discovered: list[dict[str, str]],
    catalog_root: Path,
) -> list[dict[str, Any]]:
    """Combine Playwright discovery with park-catalog state groupings."""
    parks_by_state = build_park_codes_by_state(catalog_root)
    by_slug: dict[str, dict[str, Any]] = {}

    for row in discovered:
        slug = str(row.get("slug") or "").strip().lower()
        if not slug:
            continue
        url = str(row.get("url") or "").strip()
        by_slug[slug] = {
            "region_slug": f"us-{slug}",
            "state_slug": slug,
            "country_code": "us",
            "region_name": str(row.get("name") or state_name_from_slug(slug) or slug).strip(),
            "listing_url": url or f"https://www.alltrails.com/us/{slug}",
            "park_codes": list(parks_by_state.get(slug) or []),
            "park_count": len(parks_by_state.get(slug) or []),
            "source": "alltrails_directory",
        }

    for state_slug, codes in parks_by_state.items():
        if state_slug in by_slug:
            continue
        by_slug[state_slug] = {
            "region_slug": f"us-{state_slug}",
            "state_slug": state_slug,
            "country_code": "us",
            "region_name": state_name_from_slug(state_slug) or state_slug.replace("-", " ").title(),
            "listing_url": f"https://www.alltrails.com/us/{state_slug}",
            "park_codes": list(codes),
            "park_count": len(codes),
            "source": "park_catalog",
        }

    return sorted(by_slug.values(), key=lambda r: str(r.get("region_name") or ""))


def region_catalog_path(catalog_root: Path, state_slug: str) -> Path:
    slug = (state_slug or "").strip().lower().replace("us-", "")
    return catalog_root / "regions" / f"us-{slug}.json"


def write_region_catalog_file(
    root: Path,
    *,
    state_slug: str,
    region_name: str,
    listing_url: str,
    entries: list[dict[str, Any]],
    method: str = "playwright_region_hub",
    out_path: Path | None = None,
) -> Path:
    """Write ``data/trail_catalog/regions/us-{state}.json``."""
    catalog_root = root / "data" / "trail_catalog"
    slug = (state_slug or "").strip().lower().replace("us-", "")
    region_slug = f"us-{slug}"
    out = out_path or region_catalog_path(catalog_root, slug)
    written_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc: dict[str, Any] = {
        "catalog_version": 1,
        "catalog_kind": "trail_region",
        "written_at_utc": written_at,
        "source": {"listing_url": listing_url, "method": method},
        "region_slug": region_slug,
        "state_slug": slug,
        "country_code": "us",
        "region_name": region_name,
        "trail_count": len(entries),
        "trails": entries,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return out


def update_regions_manifest_entry(
    manifest_path: Path,
    *,
    state_slug: str,
    region_name: str,
    listing_url: str,
    trail_count: int,
    catalog_file: str,
    park_codes: list[str] | None = None,
) -> None:
    """Patch one region row in ``regions_manifest.json`` after a trail crawl."""
    slug = (state_slug or "").strip().lower().replace("us-", "")
    region_slug = f"us-{slug}"
    written_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    regions = manifest.get("regions")
    if not isinstance(regions, list):
        regions = []
    row = {
        "region_slug": region_slug,
        "state_slug": slug,
        "country_code": "us",
        "region_name": region_name,
        "listing_url": listing_url,
        "trail_count": trail_count,
        "file": catalog_file.replace("\\", "/"),
        "park_codes": list(park_codes or []),
        "park_count": len(park_codes or []),
        "source": "alltrails_region_hub",
        "updated_at_utc": written_at,
    }
    regions = [r for r in regions if isinstance(r, dict) and r.get("state_slug") != slug]
    regions.append(row)
    manifest["catalog_kind"] = "trail_regions"
    manifest["regions"] = sorted(regions, key=lambda r: str(r.get("region_name") or ""))
    manifest["region_count"] = len(regions)
    manifest["updated_at_utc"] = written_at
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def write_regions_manifest(
    root: Path,
    regions: list[dict[str, Any]],
    *,
    listing_url: str = "https://www.alltrails.com/directory/regions/A/1",
    method: str = "playwright_regions_directory",
) -> Path:
    catalog_root = root / "data" / "trail_catalog"
    written_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest_path = catalog_root / "regions_manifest.json"
    doc: dict[str, Any] = {
        "catalog_kind": "trail_regions",
        "catalog_version": 1,
        "written_at_utc": written_at,
        "source": {"listing_url": listing_url, "method": method},
        "region_count": len(regions),
        "regions": regions,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return manifest_path
