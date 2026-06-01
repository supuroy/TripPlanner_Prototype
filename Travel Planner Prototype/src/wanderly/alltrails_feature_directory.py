"""
Parse AllTrails ``/directory/trail-features`` hub and per-feature listing URLs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

TRAIL_FEATURES_HUB_URL = "https://www.alltrails.com/directory/trail-features"

_FEATURE_LISTING_PATH_RE = re.compile(
    r"^/directory/trail-features/(?P<slug>[^/]+)(?:/A/(?P<page>\d+))?/?$",
    re.I,
)
_FEATURE_HREF_RE = re.compile(
    r'href=["\']([^"\']*?/directory/trail-features/([a-z0-9-]+)(?:/A/\d+)?)["\']',
    re.I,
)
_TRAIL_FEATURES_SECTION_RE = re.compile(
    r"trail\s*features",
    re.I,
)


@dataclass(frozen=True)
class AllTrailsFeatureMeta:
    feature_slug: str
    feature_name: str
    listing_url: str
    page: int = 1


def _slug_to_title(slug: str) -> str:
    return " ".join(part.capitalize() for part in (slug or "").split("-") if part)


def normalize_feature_listing_url(url: str, *, page: int = 1) -> str:
    """Canonical first-page listing URL for a feature slug."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("feature URL is required")
    if not raw.startswith("http"):
        raw = urljoin("https://www.alltrails.com", raw)
    parsed = urlparse(raw.split("?")[0].rstrip("/"))
    m = _FEATURE_LISTING_PATH_RE.match(parsed.path or "")
    if not m:
        raise ValueError(f"not a trail-features listing URL: {url!r}")
    slug = m.group("slug").lower()
    page_num = int(m.group("page") or page or 1)
    return f"https://www.alltrails.com/directory/trail-features/{slug}/A/{max(1, page_num)}"


def parse_feature_listing_url(url: str) -> AllTrailsFeatureMeta:
    listing = normalize_feature_listing_url(url)
    parsed = urlparse(listing)
    m = _FEATURE_LISTING_PATH_RE.match(parsed.path or "")
    if not m:
        raise ValueError(f"not a trail-features listing URL: {url!r}")
    slug = m.group("slug").lower()
    page = int(m.group("page") or 1)
    return AllTrailsFeatureMeta(
        feature_slug=slug,
        feature_name=_slug_to_title(slug),
        listing_url=listing,
        page=page,
    )


def feature_listing_url_for_slug(slug: str, *, page: int = 1) -> str:
    s = (slug or "").strip().lower().strip("/")
    if not s or s == "trail-features":
        raise ValueError("feature slug is required")
    return normalize_feature_listing_url(
        f"https://www.alltrails.com/directory/trail-features/{s}/A/{max(1, page)}"
    )


def parse_feature_links_from_html(html: str, *, limit: int | None = None) -> list[dict[str, str]]:
    """Extract feature listing links from hub HTML (regex fallback)."""
    text = html or ""
    m = _TRAIL_FEATURES_SECTION_RE.search(text)
    chunk = text[m.start() : m.start() + 200_000] if m else text

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for href, slug in _FEATURE_HREF_RE.findall(chunk):
        slug = (slug or "").lower().strip()
        if not slug or slug == "trail-features":
            continue
        try:
            url = feature_listing_url_for_slug(slug, page=1)
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "slug": slug, "name": _slug_to_title(slug)})
        if limit is not None and len(out) >= limit:
            break
    return out


def merge_feature_discovery_rows(
    *groups: list[dict[str, str]],
    limit: int | None = None,
) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for group in groups:
        for row in group:
            slug = str(row.get("slug") or "").strip().lower()
            if not slug:
                try:
                    meta = parse_feature_listing_url(str(row.get("url") or ""))
                    slug = meta.feature_slug
                except ValueError:
                    continue
            try:
                url = feature_listing_url_for_slug(slug, page=1)
            except ValueError:
                continue
            if url in seen:
                continue
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


def is_feature_city_listing_path(path: str, feature_slug: str) -> bool:
    """
    True for AllTrails location+feature pages that list trails, e.g.

    ``/us/tennessee/chattanooga/waterfall`` or ``/us/washington-dc/waterfall``.
    """
    parts = [p for p in (path or "").strip("/").split("/") if p]
    slug = (feature_slug or "").strip().lower()
    if not slug or not parts or parts[0].lower() != "us":
        return False
    if parts[-1].lower() != slug:
        return False
    if "/directory/" in (path or "").lower():
        return False
    # us / {region} / {feature}
    if len(parts) == 3:
        return True
    # us / {region} / {city} / {feature}
    if len(parts) == 4:
        return parts[1].lower() != slug and parts[2].lower() != slug
    return False


def normalize_feature_city_listing_url(url: str, *, feature_slug: str) -> str:
    """Canonical URL for a city/state feature trail listing page."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("city listing URL is required")
    if not raw.startswith("http"):
        raw = urljoin("https://www.alltrails.com", raw)
    parsed = urlparse(raw.split("?")[0].rstrip("/"))
    if not is_feature_city_listing_path(parsed.path or "", feature_slug):
        raise ValueError(f"not a {feature_slug!r} city listing URL: {url!r}")
    return raw.split("?")[0].rstrip("/")


def parse_feature_city_links_from_html(
    html: str,
    *,
    feature_slug: str,
    limit: int | None = None,
) -> list[dict[str, str]]:
    """Extract ``/us/.../{feature_slug}`` city listing URLs from directory HTML."""
    slug = (feature_slug or "").strip().lower()
    if not slug:
        return []
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for href in re.findall(r"""href=["']([^"']+)["']""", html or "", flags=re.I):
        try:
            url = normalize_feature_city_listing_url(href, feature_slug=slug)
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        label = parts[-2].replace("-", " ").title() if len(parts) == 4 else parts[1].replace("-", " ").title()
        out.append({"url": url, "name": label})
        if limit is not None and len(out) >= limit:
            break
    return out


def merge_feature_city_rows(
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
            seen.add(url)
            out.append({"url": url, "name": str(row.get("name") or "").strip()})
            if limit is not None and len(out) >= limit:
                return out
    return out


def parse_feature_pagination_urls(html: str, *, feature_slug: str) -> list[str]:
    """Return sorted listing URLs for pages 1..N found in HTML."""
    slug = (feature_slug or "").strip().lower()
    if not slug:
        return []
    pages: set[int] = {1}
    pat = re.compile(
        rf"/directory/trail-features/{re.escape(slug)}/A/(\d+)",
        re.I,
    )
    for m in pat.finditer(html or ""):
        try:
            pages.add(int(m.group(1)))
        except ValueError:
            continue
    return [feature_listing_url_for_slug(slug, page=p) for p in sorted(pages)]
