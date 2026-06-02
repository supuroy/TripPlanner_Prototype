"""
Parse AllTrails ``/directory/regions`` hub and US region listing URLs.

Hub: https://www.alltrails.com/directory/regions
Top 50 + A–Z: https://www.alltrails.com/directory/regions/A/1

Region trail listings are typically state hubs: ``/us/utah``, ``/us/california``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

REGIONS_HUB_URL = "https://www.alltrails.com/directory/regions"

_REGION_DIRECTORY_PATH_RE = re.compile(
    r"^/directory/regions(?:/(?P<letter>[A-Za-z]))?(?:/(?P<page>\d+))?/?$",
    re.I,
)
_US_REGION_HUB_PATH_RE = re.compile(
    r"^/us/(?P<region>[^/]+)/?$",
    re.I,
)
_REGION_HREF_RE = re.compile(
    r'href=["\']([^"\']*?/us/([a-z0-9-]+))["\']',
    re.I,
)


@dataclass(frozen=True)
class AllTrailsRegionMeta:
    region_slug: str
    region_name: str
    listing_url: str
    country_code: str = "us"
    page: int = 1


def _slug_to_title(slug: str) -> str:
    return " ".join(part.capitalize() for part in (slug or "").split("-") if part)


def normalize_region_directory_url(url: str, *, page: int = 1) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("region directory URL is required")
    if not raw.startswith("http"):
        raw = urljoin("https://www.alltrails.com", raw)
    parsed = urlparse(raw.split("?")[0].rstrip("/"))
    m = _REGION_DIRECTORY_PATH_RE.match(parsed.path or "")
    if not m:
        raise ValueError(f"not a regions directory URL: {url!r}")
    letter = (m.group("letter") or "A").upper()
    page_num = int(m.group("page") or page or 1)
    return f"https://www.alltrails.com/directory/regions/{letter}/{max(1, page_num)}"


def normalize_us_region_listing_url(url: str) -> str:
    """Canonical ``https://www.alltrails.com/us/{region}`` (state / province hub)."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("region listing URL is required")
    if not raw.startswith("http"):
        raw = urljoin("https://www.alltrails.com", raw)
    parsed = urlparse(raw.split("?")[0].rstrip("/"))
    m = _US_REGION_HUB_PATH_RE.match(parsed.path or "")
    if not m:
        raise ValueError(f"not a US region hub URL: {url!r}")
    slug = m.group("region").lower()
    if slug in ("directory", "parks", "trail", "explore"):
        raise ValueError(f"not a US region hub URL: {url!r}")
    return f"https://www.alltrails.com/us/{slug}"


def parse_region_directory_url(url: str) -> AllTrailsRegionMeta:
    listing = normalize_region_directory_url(url)
    parsed = urlparse(listing)
    m = _REGION_DIRECTORY_PATH_RE.match(parsed.path or "")
    page = int(m.group("page") or 1) if m else 1
    letter = (m.group("letter") or "A").upper() if m else "A"
    return AllTrailsRegionMeta(
        region_slug=letter.lower(),
        region_name=f"Directory {letter}",
        listing_url=listing,
        page=page,
    )


def region_listing_url_for_slug(region_slug: str) -> str:
    slug = (region_slug or "").strip().lower().strip("/")
    if not slug:
        raise ValueError("region slug is required")
    if slug.startswith("us-"):
        slug = slug[3:]
    return normalize_us_region_listing_url(f"https://www.alltrails.com/us/{slug}")


def parse_region_links_from_html(html: str, *, limit: int | None = None) -> list[dict[str, str]]:
    """Extract ``/us/{region}`` hub links from directory HTML."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for href, slug in _REGION_HREF_RE.findall(html or ""):
        slug = (slug or "").lower().strip()
        if not slug or slug in ("directory", "parks", "trail", "explore", "us"):
            continue
        try:
            url = normalize_us_region_listing_url(f"/us/{slug}")
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "slug": slug, "name": _slug_to_title(slug)})
        if limit is not None and len(out) >= limit:
            break
    return out


def merge_region_discovery_rows(
    *groups: list[dict[str, str]],
    limit: int | None = None,
) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for group in groups:
        for row in group:
            url = str(row.get("url") or "").strip().split("?")[0].rstrip("/")
            if not url or url in seen:
                continue
            try:
                url = normalize_us_region_listing_url(url)
            except ValueError:
                continue
            slug = str(row.get("slug") or urlparse(url).path.strip("/").split("/")[-1]).lower()
            seen.add(url)
            out.append(
                {
                    "url": url,
                    "slug": slug,
                    "name": str(row.get("name") or _slug_to_title(slug)).strip(),
                }
            )
            if limit is not None and len(out) >= limit:
                return out
    return out


def trail_id_for_region_trail(source_url: str, *, state_slug: str) -> str:
    """Stable id: ``region:us-utah-angels-landing-trail`` from an AllTrails trail URL."""
    from src.wanderly.trail_catalog.resolve_park import _TRAIL_PATH_RE

    m = _TRAIL_PATH_RE.search(source_url or "")
    if m:
        return f"region:{m.group(1).lower()}-{m.group(2).lower()}-{m.group(3).lower()}"
    slug = (state_slug or "").strip().lower().replace("us-", "") or "unknown"
    tail = re.sub(r"[^a-z0-9]+", "-", (source_url or "").lower()).strip("-")[:60]
    return f"region:us-{slug}-{tail or 'trail'}"


def parse_region_pagination_urls(html: str) -> list[str]:
    pages: set[int] = {1}
    pat = re.compile(r"/directory/regions/[A-Za-z]/(\d+)", re.I)
    for m in pat.finditer(html or ""):
        try:
            pages.add(int(m.group(1)))
        except ValueError:
            continue
    letter = "A"
    lm = re.search(r"/directory/regions/([A-Za-z])/\d+", html or "", re.I)
    if lm:
        letter = lm.group(1).upper()
    return [normalize_region_directory_url(f"/directory/regions/{letter}/{p}") for p in sorted(pages)]
