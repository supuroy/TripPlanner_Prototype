"""AllTrails trail lookup via Apify ``parseforge/alltrails-scraper`` (on-demand per hike leg)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx

from src.wanderly.stock_photo_heroes import infer_hero_park_phrase

_ACTOR_ID = "parseforge~alltrails-scraper"
_SYNC_DATASET_URL = f"https://api.apify.com/v2/acts/{_ACTOR_ID}/run-sync-get-dataset-items"
_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "alltrails"
_DEBUG_LOG = Path(__file__).resolve().parents[2] / ".cache" / "alltrails_debug.log"
_CACHE_TTL_S = 60 * 60 * 24 * 7  # 7 days

# Parent-trail slug guesses that map to the wrong full route — prefer catalog ``parent_alltrails_url``.
_BLOCKED_GENERIC_PARENT_TRAIL_SLUGS = frozenset(
    {
        "west-coast-trail",
        "west-coast-trail-pachena-half",
        "west-coast-trail-south-to-north",
    }
)
# Apify search often surfaces unrelated US trails when Canada/BC is expected.
_US_WRONG_REGION_TOKENS = (
    "colorado",
    "denver",
    "boulder",
    "california",
    "yosemite",
    "arizona",
    "utah",
    "montana",
    "wyoming",
    "texas",
)
_BC_REGION_TOKENS = (
    "british columbia",
    "vancouver island",
    "pacific rim",
    "port renfrew",
    "bamfield",
    "anacla",
    "pachena",
    "nitinaht",
)

# Apify strategies that load a known trail page (not open search).
_CATALOG_FETCH_STRATEGIES = frozenset(
    {"authoritative_trail_url", "segment_trail_url", "direct_trail_url"}
)

# Hard cap on Apify actor calls for one verification / lookup (avoids the old N x 12 looping).
_MAX_VERIFY_ACTOR_CALLS = 2
# Confidence thresholds for accepting a parent-trail match (name similarity in the right region).
_VERIFY_MIN_NAME_SIM = 0.55
_VERIFY_MIN_SCORE = 0.70
# Generic words that must not alone satisfy a parent-trail name match (e.g. "trail" → Monument Hill).
_GENERIC_TRAIL_NAME_TOKENS = frozenset(
    {
        "trail",
        "hike",
        "hiking",
        "loop",
        "lake",
        "river",
        "creek",
        "peak",
        "mountain",
        "meadow",
        "basin",
        "point",
        "ridge",
        "canyon",
        "falls",
        "spring",
        "pass",
        "camp",
        "campground",
        "national",
        "park",
        "section",
        "via",
        "and",
        "the",
    }
)


def _distinctive_trail_tokens(trail_name: str) -> list[str]:
    """Tokens that must appear in the matched AllTrails row name (e.g. heart for Heart Lake Trail)."""
    return [
        t
        for t in re.split(r"\W+", (trail_name or "").lower())
        if len(t) >= 3 and t not in _GENERIC_TRAIL_NAME_TOKENS
    ]

_ALERT_KEYWORDS = re.compile(
    r"\b(closed|closure|warning|alert|caution|bear|avalanche|fire|flood|detour|impassable)\b",
    re.I,
)


def apify_token() -> str:
    return (os.environ.get("APIFY_API_TOKEN") or os.environ.get("APIFY_TOKEN") or "").strip()


def _logging_enabled() -> bool:
    raw = (os.environ.get("WANDERLY_ALLTRAILS_LOG") or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def log_alltrails(message: str) -> None:
    if not _logging_enabled():
        return
    line = f"{datetime.now(timezone.utc).isoformat()} {message}"
    try:
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(f"[alltrails] {message}", flush=True)


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return _CACHE_DIR / f"{digest}.json"


def _read_cache(key: str) -> dict[str, Any] | None:
    path = _cache_path(key)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    saved_at = float(raw.get("_cached_at") or 0)
    if saved_at and (time.time() - saved_at) > _CACHE_TTL_S:
        return None
    payload = raw.get("payload")
    return payload if isinstance(payload, dict) else None


def _write_cache(key: str, payload: dict[str, Any]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(
            json.dumps({"_cached_at": time.time(), "payload": payload}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _strip_location_suffix(label: str) -> str:
    s = (label or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s*,\s*(BC|AB|ON|QC|Canada|USA|United States).*$", "", s, flags=re.I)
    s = re.sub(r"\s+campsite\s*$", "", s, flags=re.I)
    s = re.sub(r"\s+camp\s*$", "", s, flags=re.I)
    s = re.sub(r"\s+trailhead\s*$", "", s, flags=re.I)
    return s.strip()


def build_trail_search_query(
    *,
    trail_name: str | None = None,
    from_label: str | None = None,
    to_label: str | None = None,
    area_hint: str | None = None,
    day_label: str | None = None,
    destination_name: str | None = None,
    destination_country: str | None = None,
    destination_id: str | None = None,
) -> str:
    """Compose a search string for AllTrails from hike-leg context."""
    parts: list[str] = []
    park = infer_hero_park_phrase(
        destination_id=destination_id,
        name=destination_name,
        hook=area_hint,
    )
    if park:
        parts.append(park)
    tn = (trail_name or "").strip()
    if tn:
        parts.append(tn)
    fr = _strip_location_suffix(from_label or "")
    to = _strip_location_suffix(to_label or "")
    if fr and to:
        parts.append(f"{fr} to {to}")
    elif fr:
        parts.append(fr)
    elif to:
        parts.append(to)
    ah = (area_hint or "").strip()
    if ah:
        parts.append(ah.replace("(", " ").replace(")", " "))
    dl = (day_label or "").strip().replace("→", " ").replace("->", " ")
    if dl and dl not in " ".join(parts):
        parts.append(dl)
    dn = (destination_name or "").strip()
    if dn and dn.lower() not in " ".join(parts).lower():
        parts.append(dn)
    dc = (destination_country or "").strip()
    if dc and dc.lower() not in " ".join(parts).lower():
        parts.append(dc)
    if park and re.search(r"\bnational park\b", park, re.I):
        parts.append(park)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        key = p.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return " ".join(out)[:240]


def _slugify_trail_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:80]


_BACKCOUNTRY_SITE_LABEL_RE = re.compile(
    r"\b\d{1,2}[a-z]\d{1,2}\b|\b8[a-z]\d\b",
    re.I,
)


def _is_usable_alltrails_city_label(label: str | None) -> bool:
    """Skip NPS backcountry codes (8N1, 8H2) — they are not AllTrails city searches."""
    s = (label or "").strip()
    if not s or len(s) < 3:
        return False
    if _BACKCOUNTRY_SITE_LABEL_RE.search(s):
        return False
    return True


def _extract_city_from_label(label: str | None) -> str | None:
    s = (label or "").strip()
    if not s or not _is_usable_alltrails_city_label(s):
        return None
    m = re.match(r"^([^,]+),\s*([A-Za-z]{2,})\b", s)
    if m:
        return f"{m.group(1).strip()}, {m.group(2).strip()}"
    return _strip_location_suffix(s) or None


def _allows_invented_segment_slug(
    *,
    from_label: str | None = None,
    to_label: str | None = None,
    trail_name: str | None = None,
    destination_name: str | None = None,
    destination_country: str | None = None,
    destination_id: str | None = None,
) -> bool:
    """
    Invented ``{from-slug}-to-{to-slug}`` AllTrails URLs are disabled for all trails.

    U.S. and Canada hikes use catalog ``alltrails_url`` values + park-biased search.
  """
    return False


def _trail_region_bias(
    *,
    trail_name: str | None = None,
    from_label: str | None = None,
    to_label: str | None = None,
    destination_name: str | None = None,
    destination_country: str | None = None,
    destination_id: str | None = None,
    query: str | None = None,
) -> str | None:
    """
    Region scoring bias for pick_best_trail: ``ca``, ``us``, or None.

    Used to demote wrong-country AllTrails matches (duplicate trail names are common).
    """
    blob = " ".join(
        str(x or "")
        for x in (
            trail_name,
            from_label,
            to_label,
            destination_name,
            destination_country,
            destination_id,
            query,
            infer_hero_park_phrase(
                destination_id=destination_id,
                name=destination_name,
                hook=None,
            ),
        )
    ).lower()
    if re.search(r"\b(canada|bc|british columbia|alberta|ontario)\b", blob) and "united states" not in blob:
        return "ca"
    dc = (destination_country or "").strip().lower()
    if dc in ("united states", "usa", "us"):
        return "us"
    if re.search(
        r"\b(national park|united states|u\.s\.|usa)\b", blob
    ) and not re.search(r"\b(bc|british columbia)\b", blob):
        return "us"
    return None


def _trail_region_path(
    *labels: str | None,
    destination_country: str | None = None,
    destination_id: str | None = None,
) -> str | None:
    """AllTrails URL path segment (e.g. ``us/wyoming``) or None when unknown — do not guess BC."""
    bias = _trail_region_bias(
        trail_name=labels[2] if len(labels) > 2 else None,
        from_label=labels[0] if len(labels) > 0 else None,
        to_label=labels[1] if len(labels) > 1 else None,
        destination_name=labels[3] if len(labels) > 3 else labels[-1] if labels else None,
        destination_country=destination_country,
        destination_id=destination_id,
    )
    blob = " ".join(str(x or "") for x in labels).lower()
    if bias == "ca":
        if re.search(r"\b(ontario|algonquin)\b", blob):
            return "canada/ontario"
        if re.search(r"\b(alberta|banff|jasper)\b", blob):
            return "canada/alberta"
        if re.search(
            r"\b(bc|british columbia|vancouver island|pacific rim|pachena|nitinaht)\b", blob
        ):
            return "canada/british-columbia"
    if bias == "us" or re.search(r"\b(united states|usa)\b", blob):
        if re.search(r"\b(yosemite|sequoia|kings canyon|california)\b", blob):
            return "us/california"
        if re.search(r"\b(zion|arches|utah)\b", blob):
            return "us/utah"
        if re.search(r"\b(washington|olympic|rainier)\b", blob):
            return "us/washington"
        if re.search(r"\b(colorado|rocky mountain)\b", blob):
            return "us/colorado"
        if re.search(r"\b(arizona|grand canyon)\b", blob):
            return "us/arizona"
        if re.search(r"\b(wyoming|montana|yellowstone|grand teton|idaho)\b", blob):
            return "us/wyoming"
    if re.search(r"\b(yosemite|sequoia|kings canyon)\b", blob):
        return "us/california"
    if re.search(r"\b(zion|utah)\b", blob):
        return "us/utah"
    if re.search(r"\b(washington|olympic)\b", blob):
        return "us/washington"
    return None


def _segment_endpoint_tokens(label: str | None) -> list[str]:
    s = _strip_location_suffix(label or "").lower()
    if not s:
        return []
    stop = frozenset(
        {
            "bay",
            "river",
            "creek",
            "camp",
            "campsite",
            "trail",
            "falls",
            "lake",
            "beach",
            "north",
            "south",
            "east",
            "west",
        }
    )
    tokens: list[str] = []
    for part in re.split(r"\W+", s):
        p = part.strip()
        if len(p) >= 4 and p not in stop:
            tokens.append(p)
    return tokens[:4]


def _expect_canada_trail_region(
    *,
    trail_name: str | None = None,
    from_label: str | None = None,
    to_label: str | None = None,
    destination_name: str | None = None,
    destination_country: str | None = None,
    query: str | None = None,
) -> bool:
    blob = " ".join(
        str(x or "")
        for x in (from_label, to_label, destination_name, destination_country, query)
    ).lower()
    dc = (destination_country or "").strip().lower()
    if dc in ("canada", "ca"):
        return True
    return bool(re.search(r"\b(canada|bc|british columbia|alberta|ontario)\b", blob))


# Backward-compatible alias
_expect_bc_trail_region = _expect_canada_trail_region


def _segment_trail_urls(
    *,
    from_label: str | None,
    to_label: str | None,
    trail_name: str | None,
    destination_name: str | None = None,
    destination_country: str | None = None,
    destination_id: str | None = None,
) -> list[str]:
    """Build AllTrails trail-page URLs for a day leg (from → to), not the full parent trail."""
    fr = _slugify_trail_name(_strip_location_suffix(from_label or ""))
    to = _slugify_trail_name(_strip_location_suffix(to_label or ""))
    if not fr or not to:
        return []
    region = _trail_region_path(
        from_label,
        to_label,
        trail_name,
        destination_name,
        destination_country=destination_country,
        destination_id=destination_id,
    )
    urls: list[str] = []

    from src.wanderly.alltrails_search_context import preferred_alltrails_urls_from_destination

    dest_stub = {
        "id": destination_id,
        "name": destination_name,
        "hook": None,
    }
    urls.extend(
        preferred_alltrails_urls_from_destination(
            dest_stub,
            trail_name=trail_name,
            from_label=from_label,
            to_label=to_label,
        )
    )
    tn = (trail_name or "").strip()
    if region and tn:
        slug = _slugify_trail_name(tn)
        if slug and slug not in (fr, to) and slug not in _BLOCKED_GENERIC_PARENT_TRAIL_SLUGS:
            urls.append(f"https://www.alltrails.com/trail/{region}/{slug}")
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _guess_parent_trail_urls(
    *,
    query: str,
    trail_name: str | None,
    destination_name: str | None,
    destination_country: str | None = None,
    destination_id: str | None = None,
) -> list[str]:
    """Full-trail URLs when the hike leg is not a from→to segment."""
    urls: list[str] = []
    blobs = [trail_name or "", destination_name or "", query]
    joined = " ".join(blobs).lower()
    from src.wanderly.alltrails_search_context import preferred_alltrails_urls_from_destination

    dest_stub = {"id": destination_id, "name": destination_name, "hook": None}
    urls.extend(preferred_alltrails_urls_from_destination(dest_stub, trail_name=trail_name))
    primary = (trail_name or destination_name or "").strip()
    if primary:
        slug = _slugify_trail_name(primary)
        if slug and slug not in _BLOCKED_GENERIC_PARENT_TRAIL_SLUGS:
            region = _trail_region_path(
                destination_name,
                trail_name,
                query,
                destination_country=destination_country,
                destination_id=destination_id,
            )
            if region:
                candidate = f"https://www.alltrails.com/trail/{region}/{slug}"
                if candidate not in urls:
                    urls.append(candidate)
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        slug_part = u.rsplit("/", 1)[-1]
        if slug_part in _BLOCKED_GENERIC_PARENT_TRAIL_SLUGS:
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _is_segment_leg(from_label: str | None, to_label: str | None) -> bool:
    return bool(_strip_location_suffix(from_label or "") and _strip_location_suffix(to_label or ""))


def _is_invented_segment_trail_url(url: str, *, trail_name: str | None = None) -> bool:
    """
    True for guessed ``{from}-to-{to}`` slugs without the parent trail name embedded.

    Published AllTrails segments (e.g. ``west-coast-trail-pachena-bay-to-michigan-creek``)
    include the route name; bare ``pachena-bay-to-michigan-creek`` guesses are unreliable.
    """
    u = (url or "").lower()
    slug = u.rsplit("/", 1)[-1]
    if "-to-" not in slug:
        return False
    tn = _slugify_trail_name(trail_name or "")
    if tn and len(tn) >= 6 and tn in slug:
        return False
    return True


def build_search_strategies(
    *,
    query: str,
    trail_name: str | None,
    from_label: str | None,
    to_label: str | None,
    destination_name: str | None,
    destination_country: str | None = None,
    destination_id: str | None = None,
    max_items: int,
    preferred_alltrails_url: str | None = None,
    park_search_cities: list[str] | None = None,
    catalog_trail_urls: list[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Ordered Apify inputs — catalog URLs first, then park-biased AllTrails search."""
    from src.wanderly.alltrails_search_context import is_alltrails_trail_page_url

    cap = max(1, min(int(max_items), 25))
    strategies: list[tuple[str, dict[str, Any]]] = []

    authoritative_urls: list[str] = []
    seen_auth: set[str] = set()

    def add_auth(u: str | None) -> None:
        u = (u or "").strip()
        if u and u not in seen_auth and is_alltrails_trail_page_url(u):
            seen_auth.add(u)
            authoritative_urls.append(u)

    pref = (preferred_alltrails_url or "").strip()
    add_auth(pref)
    for u in catalog_trail_urls or []:
        add_auth(u)

    for url in authoritative_urls[:5]:
        strategies.append(
            ("authoritative_trail_url", {"startUrl": url, "maxItems": min(3, cap)}),
        )

    allow_invented = _allows_invented_segment_slug(
        from_label=from_label,
        to_label=to_label,
        trail_name=trail_name,
        destination_name=destination_name,
        destination_country=destination_country,
        destination_id=destination_id,
    )
    if _is_segment_leg(from_label, to_label):
        segment_urls = _segment_trail_urls(
            from_label=from_label,
            to_label=to_label,
            trail_name=trail_name,
            destination_name=destination_name,
            destination_country=destination_country,
            destination_id=destination_id,
        )
        for url in segment_urls:
            if not allow_invented and _is_invented_segment_trail_url(url, trail_name=trail_name):
                continue
            strategies.append(("segment_trail_url", {"startUrl": url, "maxItems": min(3, cap)}))
    else:
        for url in _guess_parent_trail_urls(
            query=query,
            trail_name=trail_name,
            destination_name=destination_name,
            destination_country=destination_country,
            destination_id=destination_id,
        ):
            strategies.append(("direct_trail_url", {"startUrl": url, "maxItems": min(5, cap)}))

    cities: list[str] = []
    for city in park_search_cities or []:
        c = (city or "").strip()
        if c and c not in cities:
            cities.append(c)
    for label in (from_label, to_label):
        city = _extract_city_from_label(label)
        if city and city not in cities:
            cities.append(city)

    for city in cities:
        strategies.append(("city", {"searchType": "city", "city": city, "maxItems": cap}))

    strategies.append(
        ("search_url", {"startUrl": _alltrails_search_url(query), "maxItems": cap}),
    )
    return strategies


def _alltrails_search_url(query: str) -> str:
    q = urllib.parse.quote_plus(query.strip())
    return f"https://www.alltrails.com/search?search={q}"


def _num(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            return None
    return None


def _extract_alerts(item: dict[str, Any]) -> list[str]:
    alerts: list[str] = []
    for key in ("alerts", "warnings", "conditions", "trailConditions", "trail_conditions"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            alerts.append(val.strip()[:500])
        elif isinstance(val, list):
            for row in val:
                if isinstance(row, str) and row.strip():
                    alerts.append(row.strip()[:500])
                elif isinstance(row, dict):
                    msg = str(row.get("text") or row.get("message") or row.get("title") or "").strip()
                    if msg:
                        alerts.append(msg[:500])
    desc = str(item.get("description") or "")
    if desc and _ALERT_KEYWORDS.search(desc):
        snippet = desc.strip()
        if len(snippet) > 280:
            snippet = snippet[:277] + "…"
        if snippet not in alerts:
            alerts.append(snippet)
    return alerts[:5]


def _route_type_label(raw: Any) -> str | None:
    rt = str(raw or "").strip()
    if not rt:
        return None
    if len(rt) == 1:
        return {"O": "Out & Back", "L": "Loop", "P": "Point to Point"}.get(rt.upper(), rt)
    return rt


def _enrich_trail_fields(trail: dict[str, Any]) -> dict[str, Any]:
    """Fill url / estimated_time on older cached rows that omitted them."""
    out = dict(trail)
    raw = out.get("source_raw")
    if isinstance(raw, dict):
        merged = normalize_trail_record(raw, include_raw=True)
        for key in ("url", "estimated_time", "duration_minutes", "map_url"):
            if not out.get(key) and merged.get(key):
                out[key] = merged[key]
        if not out.get("source_raw"):
            out["source_raw"] = raw
    if not out.get("url"):
        slug = ""
        if isinstance(raw, dict):
            slug = str(raw.get("slug") or "").strip().lstrip("/")
        if slug:
            out["url"] = f"https://www.alltrails.com/{slug}"
    if not out.get("estimated_time"):
        mins = out.get("duration_minutes")
        if isinstance(mins, (int, float)) and mins > 0:
            m = int(mins)
            if m < 60:
                out["estimated_time"] = f"{m} min"
            else:
                h, rem = divmod(m, 60)
                out["estimated_time"] = f"{h} h {rem} min" if rem else f"{h} h"
    return out


def normalize_trail_record(item: dict[str, Any], *, include_raw: bool = True) -> dict[str, Any]:
    """Map Apify dataset row → Wanderly trail_lookup payload."""
    name = str(item.get("name") or "").strip()
    url = str(item.get("url") or item.get("trailUrl") or item.get("link") or "").strip()
    if not url:
        slug = str(item.get("slug") or "").strip().lstrip("/")
        if slug:
            url = f"https://www.alltrails.com/{slug}"
    length_mi = _num(item.get("lengthMiles") or item.get("length_miles"))
    if length_mi is None:
        km = _num(item.get("lengthKm") or item.get("length_km"))
        if km is not None:
            length_mi = round(km * 0.621371, 2)
        else:
            meters = _num(item.get("lengthMeters"))
            if meters is not None:
                length_mi = round(meters * 0.000621371, 2)
    elev = _num(item.get("elevationGainFt") or item.get("elevation_gain_ft"))
    if elev is None:
        em = _num(item.get("elevationGainMeters") or item.get("elevation_gain_m"))
        if em is not None:
            elev = round(em * 3.28084)
    diff_raw = item.get("difficulty") or item.get("difficultyRating")
    difficulty = str(diff_raw).strip() if diff_raw is not None and str(diff_raw).strip() else None
    route_type = _route_type_label(item.get("routeType") or item.get("route_type"))
    rating = _num(item.get("rating") or item.get("avgRating"))
    review_count = item.get("reviewCount") or item.get("review_count") or item.get("numReviews")
    rc = int(review_count) if isinstance(review_count, (int, float)) else None
    area = str(item.get("area") or item.get("areaName") or "").strip() or None
    state = str(item.get("state") or item.get("stateName") or "").strip() or None
    parking = str(item.get("parking") or "").strip() or None
    desc = str(item.get("description") or "").strip()
    if desc and len(desc) > 400:
        desc = desc[:397] + "…"
    features = item.get("features")
    feat_list = [str(x) for x in features if str(x).strip()] if isinstance(features, list) else None
    activities = item.get("activities")
    act_list = [str(x) for x in activities if str(x).strip()] if isinstance(activities, list) else None
    collections = item.get("collections")
    col_list = [str(x) for x in collections if str(x).strip()] if isinstance(collections, list) else None
    duration_minutes = (
        int(item["durationMinutes"])
        if isinstance(item.get("durationMinutes"), (int, float))
        else int(item["durationHiking"])
        if isinstance(item.get("durationHiking"), (int, float))
        else None
    )
    estimated_time = str(item.get("estimatedTime") or "").strip() or None
    if not estimated_time and duration_minutes and duration_minutes > 0:
        if duration_minutes < 60:
            estimated_time = f"{duration_minutes} min"
        else:
            h, m = divmod(duration_minutes, 60)
            estimated_time = f"{h} h {m} min" if m else f"{h} h"
    lat = _num(item.get("latitude") or item.get("lat") or item.get("trailheadLatitude"))
    lng = _num(item.get("longitude") or item.get("lng") or item.get("lon") or item.get("trailheadLongitude"))
    loc_obj = item.get("location") or item.get("geoLocation")
    if (lat is None or lng is None) and isinstance(loc_obj, dict):
        lat = lat if lat is not None else _num(loc_obj.get("latitude") or loc_obj.get("lat"))
        lng = lng if lng is not None else _num(
            loc_obj.get("longitude") or loc_obj.get("lng") or loc_obj.get("lon")
        )
    out: dict[str, Any] = {
        "name": name[:200] if name else None,
        "url": url[:500] if url else None,
        "latitude": round(lat, 6) if lat is not None and -90 <= lat <= 90 else None,
        "longitude": round(lng, 6) if lng is not None and -180 <= lng <= 180 else None,
        "length_mi": length_mi,
        "elevation_gain_ft": int(elev) if elev is not None else None,
        "difficulty": difficulty,
        "route_type": route_type,
        "rating": round(rating, 2) if rating is not None else None,
        "review_count": rc,
        "area": area,
        "state": state,
        "country": str(item.get("country") or item.get("countryName") or "").strip() or None,
        "location_label": str(item.get("locationLabel") or "").strip() or None,
        "parking": parking,
        "description": desc or None,
        "image_url": str(
            item.get("imageUrl") or item.get("image_url") or item.get("profilePhotoUrl") or ""
        ).strip()
        or None,
        "map_url": str(item.get("mapUrl") or "").strip() or None,
        "estimated_time": estimated_time,
        "duration_minutes": duration_minutes,
        "dogs_allowed": item.get("dogsAllowed") if isinstance(item.get("dogsAllowed"), bool) else None,
        "kid_friendly": item.get("kidFriendly") if isinstance(item.get("kidFriendly"), bool) else None,
        "wheelchair_friendly": item.get("wheelchairFriendly")
        if isinstance(item.get("wheelchairFriendly"), bool)
        else None,
        "is_closed": item.get("isClosed") if isinstance(item.get("isClosed"), bool) else None,
        "has_alerts": item.get("hasAlerts") if isinstance(item.get("hasAlerts"), bool) else None,
        "features": feat_list,
        "activities": act_list,
        "collections": col_list,
        "alerts": _extract_alerts(item),
        "alltrails_id": str(item.get("id") or item.get("trailId") or "").strip() or None,
    }
    if include_raw:
        out["source_raw"] = item
    return out


def _trail_item_url(item: dict[str, Any]) -> str:
    return str(item.get("url") or item.get("trailUrl") or item.get("link") or "").strip().lower()


def _alltrails_region_path_from_url(url: str) -> str | None:
    from src.wanderly.alltrails_search_context import alltrails_region_path_from_url

    return alltrails_region_path_from_url(url)


def _trail_slug_from_url(url: str) -> str:
    from src.wanderly.alltrails_search_context import alltrails_slug_from_url

    return alltrails_slug_from_url(url)


def _urls_same_trail_page(a: str, b: str) -> bool:
    """Same AllTrails trail page — never treat ``heart-lake-trail`` as ``heart-lake-trail-from-castle-lake``."""
    al = (a or "").strip().lower().split("?")[0].rstrip("/")
    bl = (b or "").strip().lower().split("?")[0].rstrip("/")
    if not al or not bl:
        return False
    if al == bl:
        return True
    ar, br = _alltrails_region_path_from_url(al), _alltrails_region_path_from_url(bl)
    from src.wanderly.alltrails_search_context import alltrails_slugs_equivalent

    sa, sb = _trail_slug_from_url(al), _trail_slug_from_url(bl)
    if sa and sb and (sa == sb or alltrails_slugs_equivalent(sa, sb)):
        return not ar or not br or ar == br
    if ar and br and ar != br:
        return False
    return False


def _item_matches_start_url(item: dict[str, Any], start_url: str) -> bool:
    """True when Apify returned the trail page we requested (region + slug must match)."""
    req = (start_url or "").strip().lower().split("?")[0].rstrip("/")
    got = _trail_item_url(item).split("?")[0].rstrip("/")
    if not req or not got:
        return False
    if got == req:
        return True
    if _alltrails_region_path_from_url(req) != _alltrails_region_path_from_url(got):
        return False
    from src.wanderly.alltrails_search_context import alltrails_slugs_equivalent

    rs, gs = _trail_slug_from_url(req), _trail_slug_from_url(got)
    return rs == gs or alltrails_slugs_equivalent(rs, gs)


def _item_matches_catalog_urls(item: dict[str, Any], catalog_urls: list[str]) -> bool:
    if not catalog_urls:
        return False
    return any(cu and _item_matches_start_url(item, cu) for cu in catalog_urls)


def _filter_items_for_catalog_urls(
    items: list[dict[str, Any]],
    catalog_urls: list[str],
) -> list[dict[str, Any]]:
    if not catalog_urls:
        return items
    matched = [it for it in items if _item_matches_catalog_urls(it, catalog_urls)]
    return matched if matched else items


def _filter_items_for_region_path(
    items: list[dict[str, Any]],
    region_path: str | None,
) -> list[dict[str, Any]]:
    exp = (region_path or "").strip().lower()
    if not exp:
        return items
    matched = [
        it
        for it in items
        if _alltrails_region_path_from_url(_trail_item_url(it)) == exp
    ]
    return matched if matched else items


def _score_trail_match(
    item: dict[str, Any],
    *,
    query: str,
    trail_name: str | None,
    from_label: str | None = None,
    to_label: str | None = None,
    expected_distance_mi: float | None = None,
    expect_bc: bool = False,
    region_bias: str | None = None,
    preferred_url: str | None = None,
    expected_region_path: str | None = None,
) -> float:
    name = str(item.get("name") or "").lower()
    area = str(item.get("area") or item.get("areaName") or "").lower()
    state = str(item.get("state") or item.get("stateName") or "").lower()
    country = str(item.get("country") or item.get("countryName") or "").lower()
    loc = str(item.get("locationLabel") or "").lower()
    url = _trail_item_url(item)
    blob = f"{name} {area} {state} {country} {loc} {url}"
    q = query.lower()
    pref = (preferred_url or "").strip().lower()
    if pref:
        if _urls_same_trail_page(url, pref):
            score = 1.35
        else:
            score = SequenceMatcher(None, blob, q).ratio()
            pref_region = _alltrails_region_path_from_url(pref)
            item_region = _alltrails_region_path_from_url(url)
            if pref_region and item_region and pref_region != item_region:
                score -= 0.55
            elif pref_region and item_region == pref_region:
                score += 0.18
        score += SequenceMatcher(None, blob, q).ratio() * 0.08
    else:
        score = SequenceMatcher(None, blob, q).ratio()
    primary = (trail_name or "").strip().lower()
    if primary:
        if primary in name or name in primary:
            score += 0.35
        score += SequenceMatcher(None, name, primary).ratio() * 0.25
    endpoint_tokens = _segment_endpoint_tokens(from_label) + _segment_endpoint_tokens(to_label)
    if endpoint_tokens:
        hits = sum(1 for t in endpoint_tokens if t in blob)
        score += 0.22 * (hits / len(endpoint_tokens))
        if hits < max(1, len(endpoint_tokens) // 2):
            score -= 0.25
    tokens = [t for t in re.split(r"\W+", q) if len(t) > 3]
    hits = sum(1 for t in tokens if t in blob)
    if tokens:
        score += 0.15 * (hits / len(tokens))
    # Penalize full parent trail when this leg is a short segment
    length_mi = _num(item.get("lengthMiles") or item.get("length_miles"))
    if length_mi is None:
        meters = _num(item.get("lengthMeters"))
        if meters is not None:
            length_mi = meters * 0.000621371
    if expected_distance_mi is not None and length_mi is not None:
        if length_mi > expected_distance_mi * 2.5 + 5:
            score -= 0.45
        elif abs(length_mi - expected_distance_mi) <= max(2.0, expected_distance_mi * 0.35):
            score += 0.2
    elif endpoint_tokens and length_mi is not None and length_mi > 25:
        score -= 0.35
    rating = _num(item.get("rating") or item.get("avgRating"))
    if rating is not None:
        score += min(rating, 5.0) * 0.02
    exp_path = (expected_region_path or "").strip().lower()
    item_path = _alltrails_region_path_from_url(url)
    if exp_path and item_path:
        if item_path == exp_path:
            score += 0.42
        elif region_bias == "us" and item_path.startswith("us/") and not item_path.startswith(exp_path):
            score -= 0.62
    if region_bias == "us" or (
        not region_bias and expect_bc is False and re.search(r"\b(united states|usa)\b", q)
    ):
        if "canada/british-columbia" in url or "/trail/canada/" in url:
            score -= 0.8
        if "/trail/us/" in url or "united states" in country:
            score += 0.35
        if re.search(r"\bnational park\b", blob) and "/trail/us/" in url:
            score += 0.12
        if any(tok in blob for tok in _BC_REGION_TOKENS) and "/trail/us/" not in url:
            score -= 0.55
        if re.search(r"\b(yellowstone|wyoming|montana|idaho|grand teton)\b", q) and "california" in url:
            score -= 0.75
        if re.search(r"\b(yellowstone|wyoming)\b", q) and "/us/wyoming/" in url:
            score += 0.28
    if expect_bc or region_bias == "ca":
        if "canada/british-columbia" in url or "/trail/canada/" in url:
            score += 0.45
        if any(tok in blob for tok in _BC_REGION_TOKENS):
            score += 0.28
        if any(tok in blob for tok in _US_WRONG_REGION_TOKENS):
            if not any(tok in blob for tok in _BC_REGION_TOKENS):
                score -= 0.72
        primary = (trail_name or "").strip().lower()
        if primary and primary in name:
            score += 0.22
    return score


def _minimum_trail_match_score(region_bias: str | None) -> float:
    """Reject weak AllTrails matches that would geocode to the wrong state/country."""
    if region_bias == "us":
        return 0.40
    if region_bias == "ca":
        return 0.38
    return 0.0


def pick_best_trail(
    items: list[dict[str, Any]],
    *,
    query: str,
    trail_name: str | None,
    from_label: str | None = None,
    to_label: str | None = None,
    expected_distance_mi: float | None = None,
    expect_bc: bool = False,
    region_bias: str | None = None,
    preferred_url: str | None = None,
    expected_region_path: str | None = None,
    catalog_urls: list[str] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], float]:
    if not items:
        return None, [], 0.0
    catalog = [u for u in (catalog_urls or []) if u]
    pref = (preferred_url or "").strip()
    if pref and pref not in catalog:
        catalog = [pref, *catalog]
    if catalog:
        catalog_hits = [it for it in items if _item_matches_catalog_urls(it, catalog)]
        if len(catalog_hits) == 1:
            best = normalize_trail_record(catalog_hits[0])
            alts = [
                normalize_trail_record(it)
                for it in items
                if it is not catalog_hits[0]
            ][:3]
            return best, alts, 1.0
    scored: list[tuple[float, dict[str, Any]]] = []
    for it in items:
        s = _score_trail_match(
            it,
            query=query,
            trail_name=trail_name,
            from_label=from_label,
            to_label=to_label,
            expected_distance_mi=expected_distance_mi,
            expect_bc=expect_bc,
            region_bias=region_bias,
            preferred_url=pref or None,
            expected_region_path=expected_region_path,
        )
        scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_raw = scored[0]
    alts = [normalize_trail_record(it) for _, it in scored[1:4]]
    return normalize_trail_record(best_raw), alts, best_score


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        key = str(it.get("id") or it.get("url") or it.get("name") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _run_apify_search(run_input: dict[str, Any], *, timeout_s: float = 180.0) -> list[dict[str, Any]]:
    token = apify_token()
    if not token:
        raise RuntimeError("APIFY_API_TOKEN is not set.")
    with httpx.Client(timeout=timeout_s) as client:
        res = client.post(
            _SYNC_DATASET_URL,
            params={"token": token},
            json=run_input,
            headers={"Content-Type": "application/json"},
        )
    if res.status_code == 401:
        raise RuntimeError("Apify rejected the API token (401). Check APIFY_API_TOKEN.")
    if res.status_code >= 400:
        detail = (res.text or "")[:400]
        raise RuntimeError(f"Apify actor failed ({res.status_code}): {detail}")
    data = res.json()
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        items = data.get("items") or data.get("data")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    return []


def _fetch_trails_multi_strategy(
    *,
    query: str,
    trail_name: str | None,
    from_label: str | None,
    to_label: str | None,
    destination_name: str | None,
    destination_country: str | None = None,
    destination_id: str | None = None,
    max_items: int,
    preferred_alltrails_url: str | None = None,
    catalog_urls: list[str] | None = None,
    park_search_cities: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Returns (items, attempt_log, winning_strategy)."""
    catalog = [u for u in (catalog_urls or []) if u]
    pref = (preferred_alltrails_url or "").strip()
    if pref and pref not in catalog:
        catalog = [pref, *catalog]
    strategies = build_search_strategies(
        query=query,
        trail_name=trail_name,
        from_label=from_label,
        to_label=to_label,
        destination_name=destination_name,
        destination_country=destination_country,
        destination_id=destination_id,
        max_items=max_items,
        preferred_alltrails_url=preferred_alltrails_url,
        park_search_cities=park_search_cities,
        catalog_trail_urls=catalog,
    )
    attempts: list[dict[str, Any]] = []
    pool: list[dict[str, Any]] = []
    winning: str | None = None

    for strategy, run_input in strategies:
        t0 = time.perf_counter()
        log_alltrails(f"try strategy={strategy} input={json.dumps(run_input, ensure_ascii=False)[:500]}")
        try:
            batch = _run_apify_search(run_input)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            sample = [str(x.get("name") or "")[:60] for x in batch[:5]]
            attempts.append(
                {
                    "strategy": strategy,
                    "count": len(batch),
                    "elapsed_ms": round(elapsed_ms, 1),
                    "sample_names": sample,
                }
            )
            log_alltrails(
                f"strategy={strategy} count={len(batch)} elapsed_ms={elapsed_ms:.0f} samples={sample!r}"
            )
            if batch:
                if catalog and strategy in _CATALOG_FETCH_STRATEGIES:
                    start_url = str(run_input.get("startUrl") or "").strip()
                    if start_url:
                        hits = [x for x in batch if _item_matches_start_url(x, start_url)]
                    else:
                        hits = [x for x in batch if _item_matches_catalog_urls(x, catalog)]
                    if hits:
                        log_alltrails(
                            f"catalog_hit strategy={strategy} count={len(hits)} "
                            f"url={_trail_item_url(hits[0])[:120]!r}"
                        )
                        return _dedupe_items(hits), attempts, strategy
                    if start_url:
                        log_alltrails(
                            f"catalog_mismatch strategy={strategy} requested={start_url[:120]!r} "
                            f"got={_trail_item_url(batch[0])[:120]!r}"
                        )
                pool = _dedupe_items(pool + batch)
                if winning is None:
                    winning = strategy
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            attempts.append(
                {
                    "strategy": strategy,
                    "count": 0,
                    "elapsed_ms": round(elapsed_ms, 1),
                    "error": str(exc)[:300],
                }
            )
            log_alltrails(f"strategy={strategy} error={exc!r} elapsed_ms={elapsed_ms:.0f}")

    return pool, attempts, winning


def _verify_search_strategies(
    *,
    trail_name: str,
    park_name: str | None,
    region: str | None,
    country: str | None,
    search_query: str | None,
    anchor_latitude: float | None,
    anchor_longitude: float | None,
    cap: int,
    destination_id: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Actor inputs for parent verification: catalog page first, then park/region search."""
    strategies: list[tuple[str, dict[str, Any]]] = []
    q = (search_query or "").strip() or " ".join(
        x for x in (park_name, trail_name, region, country) if x
    ).strip()
    for url in _guess_parent_trail_urls(
        query=q,
        trail_name=trail_name,
        destination_name=trail_name,
        destination_country=country,
        destination_id=destination_id,
    )[:3]:
        strategies.append(
            ("direct_trail_page", {"startUrl": url, "maxItems": min(3, cap)}),
        )
    if isinstance(anchor_latitude, (int, float)) and isinstance(anchor_longitude, (int, float)):
        strategies.append(
            (
                "coordinates",
                {
                    "searchType": "coordinates",
                    "lat": float(anchor_latitude),
                    "lng": float(anchor_longitude),
                    "maxItems": cap,
                },
            )
        )
    seen_cities: set[str] = set()

    def add_city(label: str | None) -> None:
        c = (label or "").strip()
        if c and c.lower() not in seen_cities and _is_usable_alltrails_city_label(c):
            seen_cities.add(c.lower())
            strategies.append(("park_region", {"searchType": "city", "city": c, "maxItems": cap}))

    add_city(park_name)
    region_country = ", ".join([x for x in (region, country) if x])
    if region_country and region_country != (park_name or ""):
        add_city(region_country)
    if q:
        strategies.append(("search_url", {"startUrl": _alltrails_search_url(q), "maxItems": cap}))
    return strategies


def _verify_direct_page_match(
    batch: list[dict[str, Any]],
    *,
    start_url: str,
    trail_name: str,
    expected_region_path: str | None,
) -> tuple[dict[str, Any] | None, float]:
    """Accept a catalog page fetch when URL + trail name + region align."""
    hits = [x for x in batch if _item_matches_start_url(x, start_url)]
    if not hits:
        return None, 0.0
    return _best_verified_match(
        hits, trail_name=trail_name, expected_region_path=expected_region_path
    )


def _best_verified_match(
    items: list[dict[str, Any]],
    *,
    trail_name: str,
    expected_region_path: str | None,
) -> tuple[dict[str, Any] | None, float]:
    """Pick the trail that matches ``trail_name`` **in the expected region**, or (None, 0)."""
    tn = (trail_name or "").strip().lower()
    if not tn or not items:
        return None, 0.0
    exp = (expected_region_path or "").strip().lower()
    required = _distinctive_trail_tokens(tn)
    scored: list[tuple[float, float, dict[str, Any]]] = []
    for it in items:
        name = str(it.get("name") or "").strip().lower()
        if not name:
            continue
        if required and not all(tok in name for tok in required):
            continue
        url = _trail_item_url(it)
        item_region = _alltrails_region_path_from_url(url)
        # Reject homonyms in the wrong state/province (the Heart Lake failure mode).
        if exp and item_region and item_region != exp:
            continue
        sim = SequenceMatcher(None, name, tn).ratio()
        score = sim
        if tn in name or name in tn:
            score += 0.30
        toks = [t for t in re.split(r"\W+", tn) if len(t) >= 4]
        if toks:
            hits = sum(1 for t in toks if t in name)
            score += 0.20 * (hits / len(toks))
        if exp and item_region == exp:
            score += 0.20
        scored.append((score, sim, it))
    if not scored:
        return None, 0.0
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_sim, best_it = scored[0]
    best_name = str(best_it.get("name") or "").lower()
    if best_sim < _VERIFY_MIN_NAME_SIM and tn not in best_name and best_name not in tn:
        return None, best_score
    if best_score < _VERIFY_MIN_SCORE:
        return None, best_score
    return best_it, best_score


def verify_parent_trail(
    *,
    trail_name: str,
    park_name: str | None = None,
    region: str | None = None,
    country: str | None = None,
    search_query: str | None = None,
    anchor_latitude: float | None = None,
    anchor_longitude: float | None = None,
    destination_id: str | None = None,
    max_items: int = 12,
) -> dict[str, Any]:
    """
    One bounded AllTrails verification for a destination's parent trail.

    Uses reliable actor inputs (coordinates / park-region search), matches ``trail_name`` within the
    expected region, and returns verified evidence or an honest miss — capped at
    ``_MAX_VERIFY_ACTOR_CALLS`` actor calls.

    Returns ``{ status, parent_alltrails_url?, alltrails_trail_id?, latitude?, longitude?, trail?,
    match_score?, attempts }`` with ``status`` in ``verified`` | ``unverified`` | ``provider_unavailable``.
    """
    from src.wanderly.alltrails_search_context import region_path_from_context

    tn = (trail_name or "").strip()
    if not tn:
        return {"status": "unverified", "attempts": []}
    if not apify_token():
        return {"status": "provider_unavailable", "attempts": []}
    cap = max(3, min(int(max_items), 25))
    expected_region_path = region_path_from_context(
        {"region": region, "country": country, "search_query": search_query}
    )
    strategies = _verify_search_strategies(
        trail_name=tn,
        park_name=park_name,
        region=region,
        country=country,
        search_query=search_query,
        anchor_latitude=anchor_latitude,
        anchor_longitude=anchor_longitude,
        cap=cap,
        destination_id=destination_id,
    )
    log_alltrails(
        f"verify_begin trail={tn!r} region={expected_region_path!r} strategies={len(strategies)}"
    )
    attempts: list[dict[str, Any]] = []
    pool: list[dict[str, Any]] = []
    calls = 0
    best: dict[str, Any] | None = None
    best_score = 0.0
    for label, run_input in strategies:
        if calls >= _MAX_VERIFY_ACTOR_CALLS:
            break
        calls += 1
        t0 = time.perf_counter()
        try:
            batch = _run_apify_search(run_input)
        except RuntimeError as exc:
            attempts.append({"strategy": label, "count": 0, "error": str(exc)[:300]})
            log_alltrails(f"verify_error strategy={label} error={exc!r}")
            if "401" in str(exc) or "token" in str(exc).lower():
                return {"status": "provider_unavailable", "attempts": attempts}
            continue
        except Exception as exc:  # noqa: BLE001 - bounded, treated as a miss
            attempts.append({"strategy": label, "count": 0, "error": str(exc)[:300]})
            continue
        elapsed_ms = (time.perf_counter() - t0) * 1000
        attempts.append(
            {
                "strategy": label,
                "count": len(batch),
                "elapsed_ms": round(elapsed_ms, 1),
                "sample_names": [str(x.get("name") or "")[:60] for x in batch[:5]],
            }
        )
        if label == "direct_trail_page" and batch:
            start_url = str(run_input.get("startUrl") or "").strip()
            if start_url:
                direct, direct_score = _verify_direct_page_match(
                    batch,
                    start_url=start_url,
                    trail_name=tn,
                    expected_region_path=expected_region_path,
                )
                if direct:
                    best, best_score = direct, direct_score
                    break
        pool = _dedupe_items(pool + batch)
        best, best_score = _best_verified_match(
            pool, trail_name=tn, expected_region_path=expected_region_path
        )
        if best:
            break
    if not best:
        best, best_score = _best_verified_match(
            pool, trail_name=tn, expected_region_path=expected_region_path
        )
    if not best:
        log_alltrails(
            f"verify_miss trail={tn!r} region={expected_region_path!r} pool={len(pool)} calls={calls}"
        )
        return {"status": "unverified", "attempts": attempts}
    rec = normalize_trail_record(best, include_raw=False)
    log_alltrails(
        f"verify_ok trail={tn!r} matched={rec.get('name')!r} url={rec.get('url')!r} "
        f"score={best_score:.3f} calls={calls}"
    )
    return {
        "status": "verified",
        "parent_alltrails_url": rec.get("url"),
        "alltrails_trail_id": rec.get("alltrails_id"),
        "latitude": rec.get("latitude"),
        "longitude": rec.get("longitude"),
        "trail": rec,
        "match_score": round(best_score, 3),
        "attempts": attempts,
    }


def _verified_trail_record_from_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Build a trail record from build-verified evidence (no actor call)."""
    return {
        "name": str(ctx.get("trail_name") or "Trail")[:200],
        "url": str(ctx.get("parent_alltrails_url") or "").strip() or None,
        "latitude": ctx.get("anchor_latitude"),
        "longitude": ctx.get("anchor_longitude"),
        "alltrails_id": str(ctx.get("alltrails_trail_id") or "").strip() or None,
        "area": ctx.get("park_name"),
        "state": ctx.get("region"),
        "country": ctx.get("country"),
    }


def _unverified_trail_record(
    *,
    trail_name: str | None,
    ctx: dict[str, Any] | None,
    expected_distance_mi: float | None,
) -> dict[str, Any]:
    """Deterministic estimate from LLM context — usable even when AllTrails has no match."""
    name = (trail_name or (ctx or {}).get("trail_name") or "Planned trail segment")
    out: dict[str, Any] = {"name": str(name)[:200]}
    if isinstance(expected_distance_mi, (int, float)) and expected_distance_mi > 0:
        out["length_mi"] = round(float(expected_distance_mi), 2)
    if ctx:
        out["area"] = ctx.get("park_name")
        out["state"] = ctx.get("region")
        out["country"] = ctx.get("country")
        lat, lng = ctx.get("anchor_latitude"), ctx.get("anchor_longitude")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            out["latitude"] = float(lat)
            out["longitude"] = float(lng)
    return out


def lookup_trail(
    *,
    trail_name: str | None = None,
    from_label: str | None = None,
    to_label: str | None = None,
    area_hint: str | None = None,
    day_label: str | None = None,
    destination_name: str | None = None,
    destination_country: str | None = None,
    destination_id: str | None = None,
    max_items: int = 15,
    use_cache: bool = True,
    expected_distance_mi: float | None = None,
    preferred_alltrails_url: str | None = None,
    destination: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Resolve a hike leg from **stored AllTrails evidence**, not URL guessing.

    Order: (1) build-verified parent on the destination context → return it with zero actor calls;
    (2) negative cache → return the deterministic unverified estimate; (3) one bounded verification
    attempt; on confident match return verified, otherwise cache + return the unverified estimate.

    Returns ``{ ok, verified, verification_status, trail?, parent_trail_url?, segment_note?, ... }``.
    The trip is always usable: an unverified result still carries the LLM trail stats.
    """
    from src.wanderly.alltrails_search_context import (
        derive_alltrails_search_context,
        normalize_alltrails_search_context,
        region_path_from_context,
    )

    if isinstance(destination, dict):
        dest_stub: dict[str, Any] = dict(destination)
    else:
        dest_stub = {
            "id": destination_id,
            "name": destination_name,
            "hook": area_hint,
            "country": destination_country,
        }
    ctx = normalize_alltrails_search_context(dest_stub.get("alltrails_search_context"))
    if not ctx:
        ctx = derive_alltrails_search_context(dest_stub)

    query = str(ctx.get("search_query") or "").strip() if ctx else ""
    if not query:
        query = build_trail_search_query(
            trail_name=trail_name,
            from_label=from_label,
            to_label=to_label,
            area_hint=area_hint or (ctx.get("park_name") if ctx else None),
            day_label=day_label,
            destination_name=destination_name,
            destination_country=destination_country or (ctx.get("country") if ctx else None),
            destination_id=destination_id,
        )
    log_alltrails(f"lookup_begin query={query!r} dest_id={destination_id!r} leg={from_label!r}->{to_label!r}")

    # (1) Build-verified evidence — return immediately, no actor call.
    if ctx and ctx.get("verified") is True and ctx.get("parent_alltrails_url"):
        trail = _verified_trail_record_from_context(ctx)
        if isinstance(expected_distance_mi, (int, float)) and expected_distance_mi > 0 and not trail.get("length_mi"):
            trail["length_mi"] = round(float(expected_distance_mi), 2)
        out = {
            "ok": True,
            "verified": True,
            "verification_status": "verified",
            "search_query": query,
            "trail": trail,
            "parent_trail_url": trail.get("url"),
            "match_score": 1.0,
            "cached": False,
            "winning_strategy": "build_verified",
            "debug_log_path": str(_DEBUG_LOG),
        }
        log_alltrails(f"lookup_use_verified query={query!r} url={trail.get('url')!r}")
        return out

    def _unverified_out(status: str, attempts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        note = (
            "Estimated from your itinerary — not confirmed on AllTrails."
            if status == "unverified"
            else "AllTrails provider unavailable — showing your itinerary estimate."
        )
        return {
            "ok": True,
            "verified": False,
            "verification_status": status,
            "search_query": query,
            "trail": _unverified_trail_record(
                trail_name=trail_name, ctx=ctx, expected_distance_mi=expected_distance_mi
            ),
            "segment_note": note,
            "match_score": 0.0,
            "cached": False,
            "winning_strategy": status,
            "lookup_attempts": attempts or [],
            "debug_log_path": str(_DEBUG_LOG),
        }

    region_path = region_path_from_context(ctx) if ctx else None
    cache_key = json.dumps(
        {
            "v": 5,
            "q": query,
            "did": destination_id,
            "tn": trail_name or (ctx or {}).get("trail_name"),
            "region": region_path,
        },
        sort_keys=True,
    )
    if not use_cache:
        try:
            p = _cache_path(cache_key)
            if p.is_file():
                p.unlink()
        except OSError:
            pass
        log_alltrails(f"lookup_cache_bypass query={query!r}")
    elif ctx:
        # (2) Negative cache — never re-loop an identical unanswerable query.
        hit = _read_cache(cache_key)
        if isinstance(hit, dict):
            hit = dict(hit)
            hit["cached"] = True
            log_alltrails(f"lookup_cache_hit query={query!r} status={hit.get('verification_status')!r}")
            return hit

    # (3) One bounded verification attempt.
    if not apify_token():
        return _unverified_out("provider_unavailable")
    if not ctx:
        return {"ok": False, "error": "No AllTrails context for this segment.", "verified": False}

    res = verify_parent_trail(
        trail_name=str(ctx.get("trail_name") or trail_name or ""),
        park_name=str(ctx.get("park_name") or "") or None,
        region=str(ctx.get("region") or "") or None,
        country=str(ctx.get("country") or destination_country or "") or None,
        search_query=query,
        anchor_latitude=ctx.get("anchor_latitude"),
        anchor_longitude=ctx.get("anchor_longitude"),
        max_items=max_items,
    )
    status = str(res.get("status") or "unverified")
    if status == "verified" and res.get("parent_alltrails_url"):
        trail = res.get("trail") if isinstance(res.get("trail"), dict) else _verified_trail_record_from_context(ctx)
        if isinstance(expected_distance_mi, (int, float)) and expected_distance_mi > 0 and not trail.get("length_mi"):
            trail["length_mi"] = round(float(expected_distance_mi), 2)
        out = {
            "ok": True,
            "verified": True,
            "verification_status": "verified",
            "search_query": query,
            "trail": trail,
            "parent_trail_url": res.get("parent_alltrails_url"),
            "match_score": res.get("match_score") or 1.0,
            "cached": False,
            "winning_strategy": "verified",
            "lookup_attempts": res.get("attempts") or [],
            "debug_log_path": str(_DEBUG_LOG),
        }
        if use_cache:
            _write_cache(cache_key, out)
        log_alltrails(f"lookup_verified query={query!r} url={res.get('parent_alltrails_url')!r}")
        return out

    if status == "provider_unavailable":
        return _unverified_out("provider_unavailable", res.get("attempts"))
    out = _unverified_out("unverified", res.get("attempts"))
    if use_cache:
        _write_cache(cache_key, out)
    log_alltrails(f"lookup_unverified query={query!r} cached_negative=1")
    return out
