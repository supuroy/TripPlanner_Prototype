"""
Discover AllTrails park listing URLs from directory pages or saved HTML.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

_PARK_PATH_RE = re.compile(
    r"https?://(?:www\.)?alltrails\.com/parks/(?P<country>[a-z]{2})/(?P<region>[^/\s\"'?]+)/(?P<slug>[^/\s\"'?]+)",
    re.I,
)
_PARK_REL_RE = re.compile(
    r"/parks/(?P<country>[a-z]{2})/(?P<region>[^/\s\"'?]+)/(?P<slug>[^/\s\"'?]+)",
    re.I,
)
_DIRECTORY_PATH_RE = re.compile(
    r"^/directory/parks(?:/(?P<letter>[A-Za-z]))?(?:/(?P<page>\d+))?/?$",
    re.I,
)
_US_PARKS_HUB_RE = re.compile(r"^/us/?$", re.I)

# Parks directory hub (Parks tab) — includes the curated "Top 50 Parks" block.
# ``/us`` also works when that page shows the same Parks directory UI.
TOP_50_PARKS_LISTING_URL = "https://www.alltrails.com/directory/parks"

_TOP_50_MARKER_RE = re.compile(r"top\s*50\s*parks?", re.I)


def normalize_park_listing_url(url: str) -> str | None:
    """Canonical ``https://www.alltrails.com/parks/...`` URL or ``None``."""
    raw = (url or "").strip().split("?")[0].rstrip("/")
    if not raw:
        return None
    if not raw.startswith("http"):
        raw = urljoin("https://www.alltrails.com", raw)
    m = _PARK_PATH_RE.search(raw)
    if not m:
        m2 = _PARK_REL_RE.search(raw)
        if not m2:
            return None
        cc, region, slug = m2.group("country").lower(), m2.group("region").lower(), m2.group("slug").lower()
    else:
        cc, region, slug = m.group("country").lower(), m.group("region").lower(), m.group("slug").lower()
    return f"https://www.alltrails.com/parks/{cc}/{region}/{slug}"


def parse_directory_url(url: str) -> str:
    """Normalize an AllTrails parks directory URL (legacy alias for :func:`parse_listing_page_url`)."""
    return parse_listing_page_url(url)


def parse_listing_page_url(url: str) -> str:
    """
    Normalize a parks listing / directory hub URL.

    Supported paths include ``/us`` (Top 50 Parks block), ``/directory/parks``, and
    paginated ``/directory/parks/A/1``.
    """
    raw = (url or "").strip()
    if not raw.startswith("http"):
        raw = f"https://www.alltrails.com/{raw.lstrip('/')}"
    parsed = urlparse(raw.split("?")[0].rstrip("/"))
    if "alltrails.com" not in (parsed.netloc or "").lower():
        raise ValueError(f"not an AllTrails URL: {url!r}")
    path = parsed.path or ""
    if _US_PARKS_HUB_RE.match(path) or _DIRECTORY_PATH_RE.match(path):
        return f"https://www.alltrails.com{path}"
    raise ValueError(
        "expected a parks listing URL like "
        "https://www.alltrails.com/us or "
        "https://www.alltrails.com/directory/parks/A/1"
    )


def is_us_top_50_hub(url: str) -> bool:
    try:
        parsed = urlparse(parse_listing_page_url(url))
    except ValueError:
        return False
    return bool(_US_PARKS_HUB_RE.match(parsed.path or ""))


def parse_park_urls_from_html(html: str, *, limit: int | None = None) -> list[dict[str, str]]:
    """
    Extract unique park listing URLs in document order.

    Returns ``[{"url", "name"}, ...]`` (name may be empty).
    """
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for m in _PARK_PATH_RE.finditer(html or ""):
        url = normalize_park_listing_url(m.group(0))
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append({"url": url, "name": ""})
        if limit is not None and len(rows) >= limit:
            return rows
    for m in _PARK_REL_RE.finditer(html or ""):
        url = normalize_park_listing_url(m.group(0))
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append({"url": url, "name": ""})
        if limit is not None and len(rows) >= limit:
            return rows
    return rows


def parse_top_50_parks_from_html(html: str) -> list[dict[str, str]]:
    """
    Extract park URLs only from the curated **Top 50 Parks** section of a listing page.

    Falls back to an empty list when the section marker is missing.
    """
    text = html or ""
    m = _TOP_50_MARKER_RE.search(text)
    if not m:
        return []
    # Slice from the section heading through the next major directory block.
    chunk = text[m.start() : m.start() + 120_000]
    stop_markers = (
        "trail features",
        "points of interest",
        "countries",
        "all parks",
        "browse by",
    )
    low_chunk = chunk.lower()
    end = len(chunk)
    for marker in stop_markers:
        pos = low_chunk.find(marker, 40)
        if pos > 0:
            end = min(end, pos)
    return parse_park_urls_from_html(chunk[:end], limit=50)


def merge_park_discovery_rows(
    *groups: list[dict[str, str]],
    limit: int | None = None,
) -> list[dict[str, str]]:
    """Dedupe park rows by URL, preserving first-seen order across groups."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for group in groups:
        for row in group:
            url = normalize_park_listing_url(str(row.get("url") or ""))
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(
                {
                    "url": url,
                    "name": str(row.get("name") or "").strip(),
                }
            )
            if limit is not None and len(out) >= limit:
                return out
    return out
