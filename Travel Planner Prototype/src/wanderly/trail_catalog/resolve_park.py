"""Resolve ``park_code`` for feature-catalog trails using existing park catalogs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_TRAIL_PATH_RE = re.compile(
    r"/(?:explore/)?trail/([a-z]{2})/([^/]+)/([^/?#]+)",
    re.I,
)
_PARK_PATH_RE = re.compile(
    r"/parks/(?P<country>[a-z]{2})/(?P<region>[^/]+)/(?P<slug>[^/?#]+)",
    re.I,
)


def trail_id_from_url(url: str, *, feature_slug: str) -> str:
    m = _TRAIL_PATH_RE.search(url or "")
    if not m:
        slug = re.sub(r"[^a-z0-9]+", "-", (url or "").lower()).strip("-")[:60]
        return f"{feature_slug}:{slug or 'trail'}"
    country = m.group(1).lower()
    region = m.group(2).lower()
    trail_slug = m.group(3).lower()
    return f"{feature_slug}:{country}-{region}-{trail_slug}"


def _normalize_trail_url(url: str) -> str:
    u = (url or "").strip().split("?")[0].rstrip("/")
    if u and not u.startswith("http"):
        u = f"https://www.alltrails.com{u if u.startswith('/') else '/' + u}"
    return u


def build_trail_url_to_park_index(catalog_root: Path) -> dict[str, str]:
    """Map normalized trail ``source_url`` -> ``park_code`` from ``parks/*.json``."""
    parks_dir = catalog_root / "parks"
    index: dict[str, str] = {}
    if not parks_dir.is_dir():
        return index
    for path in sorted(parks_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        park_code = str(doc.get("park_code") or path.stem).strip().lower()
        trails = doc.get("trails")
        if not isinstance(trails, list):
            continue
        for t in trails:
            if not isinstance(t, dict):
                continue
            url = _normalize_trail_url(str(t.get("source_url") or t.get("url") or ""))
            if url:
                index[url] = park_code
    return index


def park_code_from_park_url(park_url: str) -> str | None:
    """Infer catalog park code from an AllTrails park listing URL."""
    parsed = urlparse((park_url or "").split("?")[0])
    m = _PARK_PATH_RE.search(parsed.path or "")
    if not m:
        return None
    from src.wanderly.alltrails_park_url import infer_park_code_from_slug

    return infer_park_code_from_slug(m.group("slug"), country_code=m.group("country"))


def resolve_park_code(
    row: dict[str, Any],
    *,
    url_index: dict[str, str] | None = None,
) -> str | None:
    """
    Best-effort ``park_code`` for a feature listing row.

    Order: existing park catalog by trail URL, park link on card, location text.
    """
    url = _normalize_trail_url(str(row.get("url") or row.get("source_url") or ""))
    if url_index and url in url_index:
        return url_index[url]

    park_url = str(row.get("park_url") or "").strip()
    if park_url:
        code = park_code_from_park_url(park_url)
        if code:
            return code

    location = str(row.get("location_label") or "").strip()
    if location:
        from src.wanderly.nps_provider import infer_park_code_from_name

        for candidate in (
            location,
            f"{location} National Park",
        ):
            code = infer_park_code_from_name(candidate)
            if code:
                return code
    return None


def enrich_feature_trail_rows(
    rows: list[dict[str, Any]],
    *,
    feature_slug: str,
    catalog_root: Path,
    url_index: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Normalize listing rows into feature-catalog trail entries."""
    index = url_index if url_index is not None else build_trail_url_to_park_index(catalog_root)
    out: list[dict[str, Any]] = []
    for row in rows:
        url = _normalize_trail_url(str(row.get("url") or ""))
        if not url:
            continue
        park_code = resolve_park_code(row, url_index=index)
        entry: dict[str, Any] = {
            "trail_id": trail_id_from_url(url, feature_slug=feature_slug),
            "source_url": url,
            "name": str(row.get("name") or "").strip(),
            "difficulty": row.get("difficulty"),
            "length_mi": row.get("length_mi"),
            "rating": row.get("rating"),
            "location_label": str(row.get("location_label") or "").strip() or None,
            "park_url": str(row.get("park_url") or "").strip() or None,
            "park_code": park_code,
        }
        out.append({k: v for k, v in entry.items() if v is not None})
    return out
