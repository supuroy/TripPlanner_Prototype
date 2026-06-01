"""Canonical trail identity (``source_url`` join key) across park / feature / region catalogs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from src.wanderly.trail_catalog.resolve_park import _TRAIL_PATH_RE, _normalize_trail_url

TRAIL_INDEX_FILENAME = "trail_index.json"


def canonical_trail_id_from_source_url(url: str) -> str | None:
    """``trail:us-utah-angels-landing-trail`` from an AllTrails trail page URL."""
    u = _normalize_trail_url(url)
    m = _TRAIL_PATH_RE.search(u)
    if not m:
        return None
    return f"trail:{m.group(1).lower()}-{m.group(2).lower()}-{m.group(3).lower()}"


def attach_canonical_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Set ``canonical_trail_id`` on a catalog row; preserve dimension ``trail_id`` as alias."""
    out = dict(row)
    url = _normalize_trail_url(str(out.get("source_url") or out.get("url") or ""))
    if url:
        out["source_url"] = url
    cid = canonical_trail_id_from_source_url(url) if url else None
    if cid:
        out["canonical_trail_id"] = cid
        dim_tid = str(out.get("trail_id") or "").strip()
        if dim_tid and dim_tid.lower() != cid.lower():
            out.setdefault("catalog_trail_id", dim_tid)
    return out


@dataclass
class CatalogScanRef:
    dimension: str
    slug: str
    trail_id: str


def _iter_catalog_trail_rows(catalog_root: Path) -> Iterator[tuple[CatalogScanRef, dict[str, Any]]]:
    parks_dir = catalog_root / "parks"
    if parks_dir.is_dir():
        for path in sorted(parks_dir.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            code = str(doc.get("park_code") or path.stem).lower()
            for row in doc.get("trails") or []:
                if isinstance(row, dict):
                    yield CatalogScanRef("park", code, str(row.get("trail_id") or "")), row

    features_dir = catalog_root / "features"
    if features_dir.is_dir():
        for path in sorted(features_dir.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            slug = str(doc.get("feature_slug") or path.stem).lower()
            for row in doc.get("trails") or []:
                if isinstance(row, dict):
                    yield CatalogScanRef("feature", slug, str(row.get("trail_id") or "")), row

    regions_dir = catalog_root / "regions"
    if regions_dir.is_dir():
        for path in sorted(regions_dir.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            slug = str(doc.get("state_slug") or path.stem).replace("us-", "").lower()
            for row in doc.get("trails") or []:
                if isinstance(row, dict):
                    yield CatalogScanRef("region", slug, str(row.get("trail_id") or "")), row


def build_trail_index_doc(catalog_root: Path) -> dict[str, Any]:
    """
    Merge all dimension catalogs by ``source_url`` into one index document.

    Safe to re-run after region crawl adds new ``regions/*.json`` files.
    """
    by_url: dict[str, dict[str, Any]] = {}
    skipped_no_url = 0
    skipped_no_canonical = 0

    for ref, raw in _iter_catalog_trail_rows(catalog_root):
        enriched = attach_canonical_fields(raw)
        url = str(enriched.get("source_url") or "")
        cid = str(enriched.get("canonical_trail_id") or "")
        if not url:
            skipped_no_url += 1
            continue
        if not cid:
            skipped_no_canonical += 1
            continue

        alias = {
            "trail_id": ref.trail_id or enriched.get("catalog_trail_id") or enriched.get("trail_id"),
            "dimension": ref.dimension,
            "slug": ref.slug,
        }
        if cid not in by_url:
            by_url[cid] = {
                "canonical_trail_id": cid,
                "source_url": url,
                "name": enriched.get("name"),
                "park_code": enriched.get("park_code"),
                "state_slug": enriched.get("state_slug"),
                "region_path": enriched.get("region_path"),
                "state_name": enriched.get("state_name"),
                "country_code": enriched.get("country_code"),
                "distance_mi": enriched.get("distance_mi") or enriched.get("length_mi"),
                "difficulty": enriched.get("difficulty"),
                "rating": enriched.get("rating"),
                "location_label": enriched.get("location_label"),
                "aliases": [],
                "dimensions": [],
            }
        entry = by_url[cid]
        for key in (
            "name",
            "park_code",
            "state_slug",
            "region_path",
            "state_name",
            "country_code",
            "distance_mi",
            "difficulty",
            "rating",
            "location_label",
        ):
            if not entry.get(key) and enriched.get(key):
                entry[key] = enriched.get(key)
        dim_tag = f"{ref.dimension}:{ref.slug}"
        dims: list[str] = entry["dimensions"]
        if dim_tag not in dims:
            dims.append(dim_tag)
        aliases: list[dict[str, Any]] = entry["aliases"]
        if alias["trail_id"] and not any(
            a.get("trail_id") == alias["trail_id"] for a in aliases if isinstance(a, dict)
        ):
            aliases.append(alias)

    trails = sorted(by_url.values(), key=lambda t: str(t.get("canonical_trail_id") or ""))
    written_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "catalog_kind": "trail_index",
        "catalog_version": 1,
        "written_at_utc": written_at,
        "unique_trail_count": len(trails),
        "scan_stats": {
            "skipped_no_url": skipped_no_url,
            "skipped_no_canonical": skipped_no_canonical,
        },
        "trails": trails,
    }


def trail_index_path(catalog_root: Path | None = None) -> Path:
    from src.wanderly.trail_catalog.store import catalog_root as default_root

    root = catalog_root or default_root()
    return root / TRAIL_INDEX_FILENAME


def write_trail_index(catalog_root: Path, *, out_path: Path | None = None) -> Path:
    doc = build_trail_index_doc(catalog_root)
    path = out_path or trail_index_path(catalog_root)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return path


@dataclass
class TrailIndex:
    """In-memory view of ``trail_index.json``."""

    by_canonical: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_source_url: dict[str, str] = field(default_factory=dict)
    by_alias: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, catalog_root: Path | None = None, *, build_if_missing: bool = False) -> TrailIndex:
        from src.wanderly.trail_catalog.store import catalog_root as default_root

        root = catalog_root or default_root()
        path = trail_index_path(root)
        if not path.is_file():
            if build_if_missing:
                write_trail_index(root)
            else:
                return cls()
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls.from_doc(doc)

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> TrailIndex:
        idx = cls()
        trails = doc.get("trails")
        if not isinstance(trails, list):
            return idx
        for row in trails:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("canonical_trail_id") or "").strip().lower()
            url = _normalize_trail_url(str(row.get("source_url") or ""))
            if not cid:
                continue
            idx.by_canonical[cid] = row
            if url:
                idx.by_source_url[url] = cid
            for alias in row.get("aliases") or []:
                if isinstance(alias, dict):
                    tid = str(alias.get("trail_id") or "").strip().lower()
                    if tid:
                        idx.by_alias[tid] = cid
            tid = str(row.get("catalog_trail_id") or "").strip().lower()
            if tid:
                idx.by_alias[tid] = cid
        return idx

    def resolve_canonical_id(self, trail_id_or_url: str) -> str | None:
        raw = (trail_id_or_url or "").strip()
        if not raw:
            return None
        low = raw.lower()
        if low in self.by_canonical:
            return low
        if low in self.by_alias:
            return self.by_alias[low]
        if raw.startswith("http"):
            url = _normalize_trail_url(raw)
            if url in self.by_source_url:
                return self.by_source_url[url]
            return canonical_trail_id_from_source_url(url)
        return None

    def compact_row(self, canonical_trail_id: str) -> dict[str, Any] | None:
        cid = self.resolve_canonical_id(canonical_trail_id)
        if not cid:
            return None
        row = self.by_canonical.get(cid)
        if not row:
            return None
        return {
            "canonical_trail_id": cid,
            "trail_id": cid,
            "source_url": row.get("source_url"),
            "name": row.get("name"),
            "park_code": row.get("park_code"),
            "state_slug": row.get("state_slug"),
            "region_path": row.get("region_path"),
            "state_name": row.get("state_name"),
            "distance_mi": row.get("distance_mi"),
            "difficulty": row.get("difficulty"),
            "rating": row.get("rating"),
            "location_label": row.get("location_label"),
            "dimensions": list(row.get("dimensions") or []),
        }
