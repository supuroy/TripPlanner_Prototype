"""
Parse AllTrails ``/parks/{country}/{region}/{slug}`` URLs and infer trail-catalog metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_PARK_PATH_RE = re.compile(
    r"^/parks/(?P<country>[a-z]{2})/(?P<region>[^/]+)/(?P<slug>[^/?#]+)/?$",
    re.I,
)

_COUNTRY_NAMES: dict[str, str] = {
    "us": "United States",
    "ca": "Canada",
    "gb": "United Kingdom",
    "au": "Australia",
    "nz": "New Zealand",
    "mx": "Mexico",
    "fr": "France",
    "de": "Germany",
    "it": "Italy",
    "es": "Spain",
    "ch": "Switzerland",
    "no": "Norway",
    "se": "Sweden",
}

_PARK_SLUG_SUFFIXES: tuple[str, ...] = (
    "-national-park",
    "-national-monument",
    "-national-forest",
    "-national-lakeshore",
    "-national-seashore",
    "-national-recreation-area",
    "-provincial-park",
    "-state-park",
    "-regional-park",
    "-park",
)


@dataclass(frozen=True)
class AllTrailsParkMeta:
    park_url: str
    country_code: str
    region_slug: str
    park_slug: str
    park_name: str
    region: str
    country: str
    park_code: str


def _slug_to_title(slug: str) -> str:
    return " ".join(part.capitalize() for part in (slug or "").split("-") if part)


def _strip_park_suffix(slug: str) -> str:
    s = (slug or "").lower().strip()
    for suffix in sorted(_PARK_SLUG_SUFFIXES, key=len, reverse=True):
        if s.endswith(suffix):
            return s[: -len(suffix)].strip("-")
    return s


def _fallback_park_code(park_slug: str) -> str:
    core = _strip_park_suffix(park_slug)
    parts = [p for p in core.split("-") if p]
    if not parts:
        return re.sub(r"[^a-z0-9]", "", (park_slug or "park").lower())[:8] or "park"
    if len(parts) == 1:
        return parts[0][:8]
    return "".join(p[:2] for p in parts[:4])[:8]


def infer_park_code_from_slug(park_slug: str, *, country_code: str = "us") -> str:
    """Best-effort catalog ``park_code`` (NPS code when U.S. park is recognized)."""
    core = _strip_park_suffix(park_slug)
    phrase = _slug_to_title(core)
    if country_code.lower() == "us":
        from src.wanderly.nps_provider import infer_park_code_from_name

        for candidate in (
            f"{phrase} National Park",
            phrase,
            core.replace("-", " "),
        ):
            code = infer_park_code_from_name(candidate)
            if code:
                return code
    return _fallback_park_code(park_slug)


def infer_park_name_from_slug(park_slug: str) -> str:
    core = _strip_park_suffix(park_slug)
    title = _slug_to_title(core)
    slug_low = (park_slug or "").lower()
    if "national-park" in slug_low and "National Park" not in title:
        return f"{title} National Park"
    if "provincial-park" in slug_low and "Provincial Park" not in title:
        return f"{title} Provincial Park"
    if "state-park" in slug_low and "State Park" not in title:
        return f"{title} State Park"
    if slug_low.endswith("-park") and not title.endswith("Park"):
        return f"{title} Park"
    return title


def parse_alltrails_park_url(url: str) -> AllTrailsParkMeta:
    """
    Parse a canonical AllTrails park listing URL.

    Raises ``ValueError`` when the path is not ``/parks/{cc}/{region}/{slug}``.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("park URL is required")
    if not raw.startswith("http"):
        raw = f"https://www.alltrails.com/{raw.lstrip('/')}"
    parsed = urlparse(raw.split("?")[0].rstrip("/"))
    host = (parsed.netloc or "").lower()
    if host and "alltrails.com" not in host:
        raise ValueError(f"not an AllTrails URL: {url!r}")
    m = _PARK_PATH_RE.match(parsed.path or "")
    if not m:
        raise ValueError(
            "expected an AllTrails park URL like "
            "https://www.alltrails.com/parks/us/california/yosemite-national-park"
        )
    country_code = m.group("country").lower()
    region_slug = m.group("region").lower()
    park_slug = m.group("slug").lower()
    park_url = f"https://www.alltrails.com/parks/{country_code}/{region_slug}/{park_slug}"
    return AllTrailsParkMeta(
        park_url=park_url,
        country_code=country_code,
        region_slug=region_slug,
        park_slug=park_slug,
        park_name=infer_park_name_from_slug(park_slug),
        region=_slug_to_title(region_slug),
        country=_COUNTRY_NAMES.get(country_code, country_code.upper()),
        park_code=infer_park_code_from_slug(park_slug, country_code=country_code),
    )


def resolve_catalog_fields(
    park_url: str,
    *,
    park_code: str | None = None,
    park_name: str | None = None,
    region: str | None = None,
    country: str | None = None,
) -> AllTrailsParkMeta:
    """Merge explicit CLI overrides with values inferred from ``park_url``."""
    inferred = parse_alltrails_park_url(park_url)
    return AllTrailsParkMeta(
        park_url=inferred.park_url,
        country_code=inferred.country_code,
        region_slug=inferred.region_slug,
        park_slug=inferred.park_slug,
        park_name=(park_name or "").strip() or inferred.park_name,
        region=(region or "").strip() or inferred.region,
        country=(country or "").strip() or inferred.country,
        park_code=(park_code or "").strip().lower() or inferred.park_code,
    )
