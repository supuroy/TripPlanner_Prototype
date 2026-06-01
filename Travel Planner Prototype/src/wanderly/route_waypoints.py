"""Build itinerary waypoints for trail route maps (NPS coords + Nominatim geocoding)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.wanderly.nps_provider import (
    _camp_label_dedupe_key,
    _extract_bc_site_code,
    _parse_camp_label,
    _prefer_camp_label,
    dedupe_camp_labels,
)
from src.wanderly.outdoor_adventure import is_outdoor_adventure_profile
from src.wanderly.outdoor_adventure import normalize_camp_night
from src.wanderly.geo_bounds import apply_geo_bounds_to_waypoint, infer_park_bounds_key
from src.wanderly.stock_photo_heroes import infer_hero_park_phrase

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache" / "geocode"
_LOG_FILE = _CACHE_DIR / "route_waypoints.log"
_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "WanderlyTripPlanner/1.0 (EGA trip prototype; contact via org policy)"
_MIN_GAP_S = 1.1

def _log(msg: str) -> None:
    if (os.environ.get("WANDERLY_ROUTE_MAP_LOG") or "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with _LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} {msg}\n")
    except OSError:
        pass
    print(f"[route_map] {msg}", flush=True)


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return _CACHE_DIR / f"{digest}.json"


def _read_geocode_cache(query: str) -> tuple[float, float] | None:
    path = _cache_path(query)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    lat, lng = raw.get("lat"), raw.get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return float(lat), float(lng)
    return None


def _write_geocode_cache(query: str, lat: float, lng: float) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(query).write_text(
            json.dumps({"lat": lat, "lng": lng, "q": query}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _parse_coord_pair(lat: Any, lng: Any) -> tuple[float, float] | None:
    try:
        la, ln = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if -90 <= la <= 90 and -180 <= ln <= 180:
        return la, ln
    return None


def _build_geocode_query(label: str, *, park_phrase: str | None, country: str | None) -> str:
    parts: list[str] = [label.strip()]
    park = (park_phrase or "").strip()
    if park and park.lower() not in label.lower():
        parts.append(park)
    cc = (country or "").strip()
    if cc and cc.lower() not in " ".join(parts).lower():
        parts.append(cc)
    return ", ".join(p for p in parts if p)


def _nominatim_geocode(query: str) -> tuple[float, float] | None:
    q = query.strip()
    if not q:
        return None
    hit = _read_geocode_cache(q)
    if hit:
        _log(f"geocode cache hit q={q!r}")
        return hit
    if (os.environ.get("WANDERLY_GEOCODE_DISABLE") or "").strip().lower() in ("1", "true", "yes"):
        return None
    params = {"q": q, "format": "json", "limit": 1}
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    try:
        with httpx.Client(timeout=25.0, headers=headers) as client:
            res = client.get(_NOMINATIM, params=params)
        if res.status_code != 200:
            _log(f"geocode HTTP {res.status_code} q={q!r}")
            return None
        rows = res.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        _log(f"geocode error q={q!r}: {exc!r}")
        return None
    if not isinstance(rows, list) or not rows:
        _log(f"geocode miss q={q!r}")
        return None
    row = rows[0]
    if not isinstance(row, dict):
        return None
    pair = _parse_coord_pair(row.get("lat"), row.get("lon"))
    if pair:
        _write_geocode_cache(q, pair[0], pair[1])
        _log(f"geocode ok q={q!r} -> {pair[0]:.5f},{pair[1]:.5f}")
    return pair


_last_geocode_at = 0.0


def _geocode_throttled(query: str) -> tuple[float, float] | None:
    global _last_geocode_at
    now = time.monotonic()
    wait = _MIN_GAP_S - (now - _last_geocode_at)
    if wait > 0:
        time.sleep(wait)
    pair = _nominatim_geocode(query)
    _last_geocode_at = time.monotonic()
    return pair


def _norm_place(label: str) -> str:
    place, _ = _parse_camp_label(label)
    return re.sub(r"\W+", " ", place.lower()).strip()


def _nps_coord_lookups(
    camp_matches: list[dict[str, Any]] | None,
) -> tuple[dict[str, tuple[float, float]], dict[str, tuple[float, float]], dict[str, tuple[float, float]]]:
    """by exact plan label, by Yellowstone site code (8H2), by normalized place name."""
    by_label: dict[str, tuple[float, float]] = {}
    by_site: dict[str, tuple[float, float]] = {}
    by_place: dict[str, tuple[float, float]] = {}
    for row in camp_matches or []:
        if not isinstance(row, dict):
            continue
        lab = str(row.get("plan_camp_label") or "").strip()
        pair = _parse_coord_pair(row.get("latitude"), row.get("longitude"))
        if not lab or not pair:
            continue
        by_label[lab.lower()] = pair
        code = _extract_bc_site_code(lab)
        if code:
            by_site[code.upper()] = pair
        place = _norm_place(lab)
        if place:
            by_place[place] = pair
    return by_label, by_site, by_place


def _resolve_nps_coords(
    label: str,
    *,
    by_label: dict[str, tuple[float, float]],
    by_site: dict[str, tuple[float, float]],
    by_place: dict[str, tuple[float, float]],
) -> tuple[float, float] | None:
    lab = label.strip()
    if not lab:
        return None
    hit = by_label.get(lab.lower())
    if hit:
        return hit
    code = _extract_bc_site_code(lab)
    if code:
        hit = by_site.get(code.upper())
        if hit:
            return hit
    place = _norm_place(lab)
    if place:
        return by_place.get(place)
    return None


def _finalize_stops(stops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per camp / place — prefer labels with site codes (``8H2 …``) over ``… campsite``."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    place_to_key: dict[str, str] = {}

    def _merge_into(key: str, *, day: int, label: str, kind: str) -> None:
        if key not in merged:
            merged[key] = {"day": day, "label": label, "kind": kind}
            order.append(key)
            return
        prev = merged[key]
        better = _prefer_camp_label(str(prev["label"]), label)
        use_day = int(prev.get("day") or 0)
        if day > 0 and (use_day <= 0 or day < use_day):
            use_day = day
        merged[key] = {"day": use_day, "label": better, "kind": kind}

    for stop in stops:
        label = str(stop.get("label") or "").strip()
        if not label:
            continue
        day = int(stop.get("day") or 0)
        kind = str(stop.get("kind") or "camp")
        place = _norm_place(label)
        if place and place in place_to_key:
            _merge_into(place_to_key[place], day=day, label=label, kind=kind)
            continue
        key = _camp_label_dedupe_key(label)
        _merge_into(key, day=day, label=label, kind=kind)
        if place:
            place_to_key[place] = key

    out = [merged[k] for k in order]
    return sorted(out, key=lambda s: (int(s.get("day") or 999), s.get("label") or ""))


def collect_route_stops(
    itinerary: list[dict[str, Any]] | None,
    *,
    outdoor_adventure: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Ordered stops from itinerary: nightly camps first, then hike leg endpoints when no camp.
    """
    stops: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def _add(*, day: int, label: str, kind: str) -> None:
        lab = label.strip()
        if not lab:
            return
        key = re.sub(r"\W+", " ", lab.lower()).strip()
        dedupe = f"{day}:{key}"
        if dedupe in seen_keys:
            return
        seen_keys.add(dedupe)
        stops.append({"day": day, "label": lab[:160], "kind": kind})

    days = sorted(
        [d for d in (itinerary or []) if isinstance(d, dict)],
        key=lambda x: int(x.get("day") or 0),
    )
    first_day = int(days[0].get("day") or 1) if days else 1
    for day_row in days:
        day_num = int(day_row.get("day") or 0)
        if day_num < 1:
            continue
        dl = day_row.get("day_logistics")
        if not isinstance(dl, dict):
            continue
        camp = normalize_camp_night(dl.get("camp_night"))
        if camp:
            _add(day=day_num, label=str(camp.get("label") or ""), kind="camp")
        legs = dl.get("surface_legs")
        if not isinstance(legs, list):
            continue
        walk_legs = [lg for lg in legs if isinstance(lg, dict) and str(lg.get("kind") or "").lower() == "walk"]
        if not walk_legs:
            continue
        if not camp:
            to_l = str(walk_legs[-1].get("to_label") or "").strip()
            if to_l:
                _add(day=day_num, label=to_l, kind="hike_end")
        if day_num == first_day:
            fr = str(walk_legs[0].get("from_label") or "").strip()
            if fr:
                _add(day=day_num, label=fr, kind="trailhead")

    if outdoor_adventure and isinstance(outdoor_adventure.get("camp_nights"), list):
        extra_labels: list[str] = []
        for row in outdoor_adventure["camp_nights"]:
            if isinstance(row, dict):
                lab = str(row.get("label") or "").strip()
                if lab:
                    extra_labels.append(lab)
        for lab in dedupe_camp_labels(extra_labels):
            if not any(s["label"].lower() == lab.lower() for s in stops):
                _add(day=0, label=lab, kind="camp")

    return stops


def _itinerary_has_walk_legs(itinerary: list[dict[str, Any]] | None) -> bool:
    for day in itinerary or []:
        if not isinstance(day, dict):
            continue
        dl = day.get("day_logistics")
        if not isinstance(dl, dict):
            continue
        for leg in dl.get("surface_legs") or []:
            if isinstance(leg, dict) and str(leg.get("kind") or "").lower() == "walk":
                return True
    return False


def _itinerary_day_count(itinerary: list[dict[str, Any]] | None) -> int:
    n = 0
    for day in itinerary or []:
        if isinstance(day, dict) and int(day.get("day") or 0) > 0:
            n += 1
    return n


def hydrate_destination_for_route_map(
    dest: dict[str, Any],
    session: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Merge persisted session/trip fields the client may omit on POST (``nps_outdoor``, full ``itinerary``).
    """
    if not session or not isinstance(dest, dict):
        return dest
    did = str(dest.get("id") or "").strip()
    if not did:
        return dest

    def _row_matches(row: dict[str, Any]) -> bool:
        return str(row.get("id") or "").strip() == did

    session_row: dict[str, Any] | None = None
    for key in ("ui_destinations", "last_destinations"):
        raw = session.get(key)
        if not isinstance(raw, list):
            continue
        for row in raw:
            if isinstance(row, dict) and _row_matches(row):
                session_row = row
                break
        if session_row:
            break

    if not session_row:
        return dest

    merged = dict(session_row)
    merged.update(dest)

    for field in ("nps_outdoor", "outdoor_adventure", "trail_lookups", "route_map"):
        client_val = dest.get(field)
        session_val = session_row.get(field)
        if (not client_val or client_val == {}) and session_val:
            merged[field] = session_val

    client_itin = dest.get("itinerary") if isinstance(dest.get("itinerary"), list) else []
    session_itin = session_row.get("itinerary") if isinstance(session_row.get("itinerary"), list) else []
    if _itinerary_has_walk_legs(client_itin):
        merged["itinerary"] = client_itin
    elif session_itin and _itinerary_day_count(session_itin) >= _itinerary_day_count(client_itin):
        merged["itinerary"] = session_itin
    elif client_itin:
        merged["itinerary"] = client_itin

    return merged


def _parse_day_label_endpoints(label: str) -> tuple[str, str] | None:
    """
    Parse ``Heart Lake TH to 8H2 Sheridan Creek`` from day labels.
    Uses only the first ``→``-delimited segment when labels chain multiple days.
    """
    raw = (label or "").strip()
    if not raw:
        return None
    head = re.split(r"\s*→\s*|\s*->\s*", raw, maxsplit=1)[0].strip()
    parts = re.split(r"\s+to\s+", head, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return None
    fr, to = parts[0].strip(), parts[1].strip()
    if len(fr) < 3 or len(to) < 3:
        return None
    return fr[:160], to[:160]


def _trail_segment_row(dl: dict[str, Any]) -> dict[str, Any] | None:
    raw = dl.get("trail_segment")
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or raw.get("trail_name") or "").strip()
    if not name:
        return None
    out: dict[str, Any] = {"name": name[:120]}
    for fk in ("distance_mi", "elevation_gain_ft", "elevation_loss_ft"):
        v = raw.get(fk)
        if isinstance(v, (int, float)):
            out[fk] = v
    return out


def collect_hike_legs(itinerary: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """
    Hike segments for map lines: explicit ``surface_legs`` (walk), else inferred from
    day labels (``A to B``) and camp-to-camp progression when the LLM only filled prose.
    """
    legs: list[dict[str, Any]] = []
    days = sorted(
        [d for d in (itinerary or []) if isinstance(d, dict)],
        key=lambda x: int(x.get("day") or 0),
    )
    days_with_walk: set[int] = set()

    for day_row in days:
        day_num = int(day_row.get("day") or 0)
        if day_num < 1:
            continue
        dl = day_row.get("day_logistics")
        if not isinstance(dl, dict):
            continue
        surf = dl.get("surface_legs")
        if not isinstance(surf, list):
            continue
        leg_idx = 0
        for leg in surf:
            if not isinstance(leg, dict):
                continue
            if str(leg.get("kind") or "").strip().lower() != "walk":
                continue
            fr = str(leg.get("from_label") or "").strip()
            to = str(leg.get("to_label") or "").strip()
            if not fr and not to:
                continue
            row: dict[str, Any] = {
                "day": day_num,
                "leg_index": leg_idx,
                "from_label": fr[:160] if fr else None,
                "to_label": to[:160] if to else None,
                "source": "surface_leg",
            }
            tn = leg.get("trail_name") or leg.get("name")
            if isinstance(tn, str) and tn.strip():
                row["trail_name"] = tn.strip()[:120]
            dist = leg.get("distance_mi")
            if isinstance(dist, (int, float)) and float(dist) >= 0:
                row["distance_mi"] = round(float(dist), 2)
            legs.append(row)
            leg_idx += 1
        if leg_idx > 0:
            days_with_walk.add(day_num)

    prev_camp_label: str | None = None
    for day_row in days:
        day_num = int(day_row.get("day") or 0)
        if day_num < 1 or day_num in days_with_walk:
            if day_num in days_with_walk:
                dl_skip = day_row.get("day_logistics")
                if isinstance(dl_skip, dict):
                    camp_skip = normalize_camp_night(dl_skip.get("camp_night"))
                    if camp_skip:
                        prev_camp_label = str(camp_skip.get("label") or "")
            continue
        dl = day_row.get("day_logistics")
        if not isinstance(dl, dict):
            continue
        camp = normalize_camp_night(dl.get("camp_night"))
        trail = _trail_segment_row(dl)
        label = str(day_row.get("label") or "")
        base = str(day_row.get("base") or "").strip()

        fr = to = None
        parsed = _parse_day_label_endpoints(label)
        if parsed:
            fr, to = parsed
        elif camp and prev_camp_label:
            fr, to = prev_camp_label, str(camp.get("label") or "")
        elif camp and base:
            fr, to = base, str(camp.get("label") or "")

        if fr and to and fr.lower() != to.lower():
            row = {
                "day": day_num,
                "leg_index": 0,
                "from_label": fr[:160],
                "to_label": to[:160],
                "source": "inferred_label",
            }
            if trail:
                row["trail_name"] = trail.get("name")
                if trail.get("distance_mi") is not None:
                    row["distance_mi"] = trail["distance_mi"]
            legs.append(row)

        if camp:
            prev_camp_label = str(camp.get("label") or "")

    return legs


def _labels_fuzzy_match(a: str, b: str) -> bool:
    al = re.sub(r"\W+", " ", (a or "").lower()).strip()
    bl = re.sub(r"\W+", " ", (b or "").lower()).strip()
    if not al or not bl:
        return False
    if al == bl or al in bl or bl in al:
        return True
    code_a = _extract_bc_site_code(a)
    code_b = _extract_bc_site_code(b)
    if code_a and code_b and code_a == code_b:
        return True
    return False


def _trail_dict_from_lookup_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """
    Persisted ``trail_lookups`` flatten the trail record at the top level (no nested ``trail`` key).
    """
    nested = entry.get("trail")
    if isinstance(nested, dict) and (nested.get("name") or nested.get("url")):
        return nested
    if entry.get("name") or entry.get("url") or entry.get("location_label"):
        return entry
    return nested if isinstance(nested, dict) else entry


def _lookup_entry_matches_label(entry: dict[str, Any], label: str) -> bool:
    """Match saved leg lookups only when the label is that leg's from/to endpoint."""
    lab = (label or "").strip()
    if not lab:
        return False
    fr = str(entry.get("from_label") or "").strip()
    to = str(entry.get("to_label") or "").strip()
    return _labels_fuzzy_match(lab, fr) or _labels_fuzzy_match(lab, to)


def _park_context_tokens(park: str | None) -> set[str]:
    if not (park or "").strip():
        return set()
    p = park.lower()
    tokens: set[str] = set()
    for phrase in (
        "yellowstone",
        "grand teton",
        "yosemite",
        "zion",
        "olympic",
        "pacific rim",
        "vancouver island",
    ):
        if phrase in p:
            tokens.add(phrase)
    return tokens


def _trail_record_matches_park_context(
    trail: dict[str, Any],
    *,
    park: str | None,
    bounds_key: str | None = None,
) -> bool:
    """Reject AllTrails rows from the wrong park (e.g. Monument Hill / Hot Springs SP for Yellowstone)."""
    tokens = _park_context_tokens(park)
    if not tokens:
        return True
    blob = " ".join(
        str(trail.get(k) or "")
        for k in (
            "location_label",
            "area",
            "state",
            "name",
            "cityName",
            "areaName",
            "search_query",
        )
    ).lower()
    if any(t in blob for t in tokens):
        return True
    from src.wanderly.geo_bounds import coords_within_bounds

    pair = _extract_coords_from_trail_record(trail)
    if pair and bounds_key and coords_within_bounds(pair[0], pair[1], bounds_key):
        return True
    return False


def _extract_coords_from_trail_record(trail: dict[str, Any]) -> tuple[float, float] | None:
    """Lat/lng on the AllTrails row when Apify returns them (trailhead or map center)."""
    for lat_k, lng_k in (
        ("latitude", "longitude"),
        ("lat", "lng"),
        ("trailhead_latitude", "trailhead_longitude"),
    ):
        pair = _parse_coord_pair(trail.get(lat_k), trail.get(lng_k))
        if pair:
            return pair
    raw = trail.get("source_raw")
    if not isinstance(raw, dict):
        return None
    for lat_k, lng_k in (
        ("latitude", "longitude"),
        ("lat", "lng"),
        ("centerLat", "centerLng"),
        ("trailheadLatitude", "trailheadLongitude"),
    ):
        pair = _parse_coord_pair(raw.get(lat_k), raw.get(lng_k))
        if pair:
            return pair
    loc = raw.get("location") or raw.get("geoLocation") or raw.get("trailhead")
    if isinstance(loc, dict):
        pair = _parse_coord_pair(
            loc.get("latitude") or loc.get("lat"),
            loc.get("longitude") or loc.get("lng") or loc.get("lon"),
        )
        if pair:
            return pair
    return None


def _nominatim_queries_for_label(label: str, *, park: str | None, country: str) -> list[str]:
    """Ordered Nominatim queries — backcountry site codes need park + place context."""
    lab = (label or "").strip()
    if not lab:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _add(q: str) -> None:
        q = q.strip()
        if not q:
            return
        key = q.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(q)

    _add(_build_geocode_query(lab, park_phrase=park, country=country))
    place, is_bc = _parse_camp_label(lab)
    code = _extract_bc_site_code(lab)
    low = lab.lower()

    if re.search(r"\bTH\b|trailhead", lab, re.I):
        head = re.sub(r"\s*\([^)]*\)\s*", " ", lab).strip()
        head = re.sub(r"\s+TH\b", " Trailhead", head, flags=re.I).strip()
        _add(_build_geocode_query(head, park_phrase=park, country=country))

    if is_bc and park:
        if place and len(place) > 2:
            _add(_build_geocode_query(f"{place} backcountry campsite", park_phrase=park, country=country))
            _add(_build_geocode_query(f"{place} campsite", park_phrase=park, country=country))
        if code:
            _add(_build_geocode_query(f"{code} campsite", park_phrase=park, country=country))
        if "shoshone" in low or "shoshone" in (park or "").lower():
            if place:
                _add(
                    _build_geocode_query(
                        f"{place}, Shoshone Lake, Yellowstone National Park",
                        park_phrase=None,
                        country=country,
                    )
                )
            if code:
                _add(
                    _build_geocode_query(
                        f"{code}, Shoshone Lake, Yellowstone National Park, Wyoming",
                        park_phrase=None,
                        country=country,
                    )
                )
        if "delacy" in low or "de lacy" in low:
            _add(
                _build_geocode_query(
                    "DeLacy Creek Trailhead, Yellowstone National Park, Wyoming",
                    park_phrase=None,
                    country=country,
                )
            )

    if "jackson" in low and "gateway" in low:
        _add(_build_geocode_query("Jackson, Wyoming", park_phrase=None, country=country))
    if low in ("seattle",) or low.startswith("seattle,"):
        _add(_build_geocode_query("Seattle, Washington", park_phrase=None, country=country))

    return out


def _geocode_queries_for_trail(trail: dict[str, Any], *, park: str | None, country: str) -> list[str]:
    """Build Nominatim queries from an AllTrails trail row (area/state more reliable than site codes)."""
    if not isinstance(trail, dict):
        return []
    queries: list[str] = []
    loc = str(trail.get("location_label") or "").strip()
    area = str(trail.get("area") or "").strip()
    state = str(trail.get("state") or "").strip()
    name = str(trail.get("name") or "").strip()
    if loc:
        queries.append(_build_geocode_query(loc, park_phrase=park, country=country))
    if area:
        queries.append(_build_geocode_query(f"{area}, {state or country}".strip(", "), park_phrase=park, country=country))
    if name and park:
        queries.append(_build_geocode_query(f"{name}, {park}", park_phrase=None, country=country))
    return queries


def _coords_from_trail_lookups(
    label: str,
    *,
    trail_lookups: dict[str, Any] | None,
    park: str | None,
    country: str,
    cache: dict[str, tuple[float, float] | None],
    bounds_key: str | None = None,
) -> tuple[float, float] | None:
    """
    Geocode using saved AllTrails lookups (``trail_lookups`` on the destination card).

    Prefer this over raw Nominatim on backcountry codes like ``8H2`` — AllTrails carries
    ``location_label`` / area for the trail corridor.
    """
    if not isinstance(trail_lookups, dict) or not label.strip():
        return None
    lab = label.strip()
    for entry in trail_lookups.values():
        if not isinstance(entry, dict):
            continue
        if not _lookup_entry_matches_label(entry, lab):
            continue
        trail = _trail_dict_from_lookup_entry(entry)
        if not isinstance(trail, dict):
            continue
        if not _trail_record_matches_park_context(trail, park=park, bounds_key=bounds_key):
            _log(
                f"skip trail_lookup {lab!r}: area {trail.get('location_label')!r} "
                f"does not match park {park!r}"
            )
            continue
        direct = _extract_coords_from_trail_record(trail)
        if direct:
            _log(f"alltrails record coords {lab!r} -> {direct}")
            return direct
        for query in _geocode_queries_for_trail(trail, park=park, country=country):
            qkey = f"at:{query.lower()}"
            if qkey in cache:
                hit = cache[qkey]
            else:
                hit = _geocode_throttled(query)
                cache[qkey] = hit
            if hit:
                _log(f"alltrails lookup geocode {lab!r} via {query!r}")
                return hit
    return None


def resolve_waypoint(
    label: str,
    *,
    park: str | None,
    country: str,
    nps_by_label: dict[str, tuple[float, float]],
    nps_by_site: dict[str, tuple[float, float]],
    nps_by_place: dict[str, tuple[float, float]],
    trail_lookups: dict[str, Any] | None,
    cache: dict[str, tuple[float, float] | None],
    bounds_key: str | None = None,
) -> dict[str, Any]:
    """
    Resolve one place label to coordinates with provenance.

    Order: NPS camp_matches → trail_lookups (AllTrails corridor) → Nominatim → unresolved.
    """
    lab = (label or "").strip()
    if not lab:
        return {"label": "", "lat": None, "lng": None, "source": "unresolved"}
    key = lab.lower()

    def _finalize_pair(
        pair: tuple[float, float],
        source: str,
    ) -> dict[str, Any] | None:
        wp = apply_geo_bounds_to_waypoint(
            {
                "label": lab,
                "lat": round(pair[0], 6),
                "lng": round(pair[1], 6),
                "source": source,
            },
            bounds_key=bounds_key,
        )
        if wp.get("source") == "rejected_out_of_bounds":
            return None
        cache[key] = pair
        cache[f"{key}:source"] = source
        return wp

    if key in cache:
        pair = cache[key]
        if pair:
            src = cache.get(f"{key}:source") or "nominatim"
            wp = apply_geo_bounds_to_waypoint(
                {
                    "label": lab,
                    "lat": round(pair[0], 6),
                    "lng": round(pair[1], 6),
                    "source": src,
                },
                bounds_key=bounds_key,
            )
            if wp.get("source") != "rejected_out_of_bounds":
                return wp
        elif cache.get(f"{key}:source") == "unresolved":
            return apply_geo_bounds_to_waypoint(
                {"label": lab, "lat": None, "lng": None, "source": "unresolved"},
                bounds_key=bounds_key,
            )

    pair = _resolve_nps_coords(
        lab,
        by_label=nps_by_label,
        by_site=nps_by_site,
        by_place=nps_by_place,
    )
    if pair:
        wp = _finalize_pair(pair, "nps")
        if wp:
            return wp

    pair = _coords_from_trail_lookups(
        lab,
        trail_lookups=trail_lookups,
        park=park,
        country=country,
        cache=cache,
        bounds_key=bounds_key,
    )
    if pair:
        wp = _finalize_pair(pair, "trail_lookup")
        if wp:
            return wp

    for query in _nominatim_queries_for_label(lab, park=park, country=country):
        qkey = f"nom:{query.lower()}"
        if qkey in cache:
            pair = cache[qkey]
        else:
            pair = _geocode_throttled(query)
            cache[qkey] = pair
        if pair:
            wp = _finalize_pair(pair, "nominatim")
            if wp:
                _log(f"nominatim ok {lab!r} via {query!r}")
                return wp

    cache[key] = None
    cache[f"{key}:source"] = "unresolved"
    return apply_geo_bounds_to_waypoint(
        {"label": lab, "lat": None, "lng": None, "source": "unresolved"},
        bounds_key=bounds_key,
    )


def _resolve_label_coords(
    label: str,
    *,
    park: str | None,
    country: str,
    nps_by_label: dict[str, tuple[float, float]],
    nps_by_site: dict[str, tuple[float, float]],
    nps_by_place: dict[str, tuple[float, float]],
    trail_lookups: dict[str, Any] | None,
    cache: dict[str, tuple[float, float] | None],
    bounds_key: str | None = None,
) -> tuple[float, float] | None:
    wp = resolve_waypoint(
        label,
        park=park,
        country=country,
        nps_by_label=nps_by_label,
        nps_by_site=nps_by_site,
        nps_by_place=nps_by_place,
        trail_lookups=trail_lookups,
        cache=cache,
        bounds_key=bounds_key,
    )
    lat, lng = wp.get("lat"), wp.get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return float(lat), float(lng)
    return None


def build_route_waypoints(
    *,
    destination_id: str | None = None,
    destination_name: str | None = None,
    destination_country: str | None = None,
    destination_hook: str | None = None,
    itinerary: list[dict[str, Any]] | None = None,
    outdoor_adventure: dict[str, Any] | None = None,
    nps_outdoor: dict[str, Any] | None = None,
    trail_lookups: dict[str, Any] | None = None,
    profile_pills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve waypoints for a Leaflet route map."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    out: dict[str, Any] = {
        "ok": False,
        "fetched_at": fetched_at,
        "waypoints": [],
        "line": [],
        "hike_legs": [],
        "bounds": None,
        "resolved_count": 0,
        "failed_count": 0,
        "failed_labels": [],
    }

    if profile_pills is not None and not is_outdoor_adventure_profile(profile_pills):
        out["message"] = "Trail route maps are for hiking/camping trips only."
        return out

    stops = _finalize_stops(collect_route_stops(itinerary, outdoor_adventure=outdoor_adventure))
    hike_legs_raw = collect_hike_legs(itinerary)
    if not stops and not hike_legs_raw:
        out["message"] = "No camps or hike legs found in this itinerary yet."
        return out

    park = infer_hero_park_phrase(
        destination_id=destination_id,
        name=destination_name,
        hook=destination_hook,
    )
    bounds_key = infer_park_bounds_key(
        park_phrase=park,
        destination_id=destination_id,
        destination_name=destination_name,
        destination_hook=destination_hook,
    )
    nps_payload = nps_outdoor if isinstance(nps_outdoor, dict) else None
    camp_matches = (nps_payload or {}).get("camp_matches") if nps_payload else None
    nps_by_label, nps_by_site, nps_by_place = _nps_coord_lookups(
        camp_matches if isinstance(camp_matches, list) else None,
    )
    nps_with_coords = sum(
        1
        for row in (camp_matches or [])
        if isinstance(row, dict) and _parse_coord_pair(row.get("latitude"), row.get("longitude"))
    )
    country = (destination_country or "").strip() or "United States"
    coord_cache: dict[str, tuple[float, float] | None] = {}
    failed: list[str] = []

    def _coords_for(label: str) -> tuple[float, float] | None:
        lab = label.strip()
        if not lab:
            return None
        pair = _resolve_label_coords(
            lab,
            park=park,
            country=country,
            nps_by_label=nps_by_label,
            nps_by_site=nps_by_site,
            nps_by_place=nps_by_place,
            trail_lookups=trail_lookups,
            cache=coord_cache,
            bounds_key=bounds_key,
        )
        if not pair and lab not in failed:
            failed.append(lab)
        return pair

    # --- Hike leg lines (from → to per walk surface_leg) ---
    hike_legs_out: list[dict[str, Any]] = []
    all_points: list[list[float]] = []
    legs_mapped = 0
    for leg in hike_legs_raw:
        fr = str(leg.get("from_label") or "").strip()
        to = str(leg.get("to_label") or "").strip()
        fr_pair = _coords_for(fr) if fr else None
        to_pair = _coords_for(to) if to else None
        seg_line: list[list[float]] = []
        if fr_pair:
            seg_line.append([fr_pair[0], fr_pair[1]])
        if to_pair:
            seg_line.append([to_pair[0], to_pair[1]])
        if len(seg_line) >= 2:
            legs_mapped += 1
            all_points.extend(seg_line)
        title = fr and to and f"{fr} → {to}" or fr or to or "Hike leg"
        hike_legs_out.append(
            {
                "day": leg.get("day"),
                "leg_index": leg.get("leg_index"),
                "from_label": fr or None,
                "to_label": to or None,
                "trail_name": leg.get("trail_name"),
                "distance_mi": leg.get("distance_mi"),
                "title": title[:200],
                "line": seg_line,
                "resolved": len(seg_line) >= 2,
            }
        )

    # --- Waypoints: camps + leg endpoints ---
    waypoints: list[dict[str, Any]] = []
    marker_seen: set[str] = set()
    seq = 0
    camp_line: list[list[float]] = []

    def _marker_key(pair: tuple[float, float]) -> str:
        return f"{round(pair[0], 4)}:{round(pair[1], 4)}"

    def _add_waypoint(
        *,
        day: int,
        label: str,
        kind: str,
        pair: tuple[float, float] | None,
    ) -> None:
        nonlocal seq
        if not label.strip() or not pair:
            return
        mk = _marker_key(pair)
        if mk in marker_seen:
            return
        marker_seen.add(mk)
        seq += 1
        had_nps = _resolve_nps_coords(
            label,
            by_label=nps_by_label,
            by_site=nps_by_site,
            by_place=nps_by_place,
        ) is not None
        waypoints.append(
            {
                "sequence": seq,
                "day": day,
                "label": label,
                "kind": kind,
                "lat": round(pair[0], 6),
                "lng": round(pair[1], 6),
                "source": "nps" if had_nps else "nominatim",
            }
        )

    for stop in stops:
        label = str(stop["label"])
        day = int(stop.get("day") or 0)
        kind = str(stop.get("kind") or "camp")
        pair = _coords_for(label)
        if pair and kind == "camp":
            camp_line.append([pair[0], pair[1]])
        _add_waypoint(day=day, label=label, kind=kind, pair=pair)

    for leg in hike_legs_raw:
        day = int(leg.get("day") or 0)
        fr = str(leg.get("from_label") or "").strip()
        to = str(leg.get("to_label") or "").strip()
        if fr:
            _add_waypoint(day=day, label=fr, kind="trailhead", pair=_coords_for(fr))
        if to:
            _add_waypoint(day=day, label=to, kind="hike_end", pair=_coords_for(to))

    resolved = len([w for w in waypoints if w.get("lat") is not None])
    all_points.extend(camp_line)
    for pt in waypoints:
        if pt.get("lat") is not None and pt.get("lng") is not None:
            all_points.append([float(pt["lat"]), float(pt["lng"])])

    out["ok"] = resolved >= 1 or legs_mapped >= 1
    out["nps_camp_matches"] = len(camp_matches) if isinstance(camp_matches, list) else 0
    out["nps_coords_available"] = nps_with_coords
    out["waypoints"] = waypoints
    out["line"] = camp_line
    out["hike_legs"] = hike_legs_out
    out["resolved_count"] = resolved
    out["failed_count"] = len(failed)
    out["failed_labels"] = list(dict.fromkeys(failed))
    out["destination_name"] = destination_name
    out["park_phrase"] = park
    if all_points:
        lats = [p[0] for p in all_points]
        lngs = [p[1] for p in all_points]
        pad = 0.08
        out["bounds"] = [
            [min(lats) - pad, min(lngs) - pad],
            [max(lats) + pad, max(lngs) + pad],
        ]
    leg_note = f"{legs_mapped} hike leg(s)" if legs_mapped else "no hike legs placed"
    camp_note = f"{len(camp_line)} camp(s)" if camp_line else "no camps placed"
    if not out["ok"]:
        nps_hint = (
            " Run Check NPS on this card first, then rebuild the map."
            if not nps_payload
            else (
                " NPS data is present but had no coordinates for these camp names "
                "(backcountry sites are not in the developed-campground API)."
                if nps_with_coords == 0
                else ""
            )
        )
        out["message"] = (
            f"Only {resolved} point(s) on the map.{nps_hint} "
            "Try specific trailhead / camp names, or rebuild after NPS enrich."
        )
    elif failed:
        out["message"] = (
            f"Solid lines = hike legs ({leg_note}); dashed = camp order ({camp_note}). "
            f"{len(failed)} label(s) could not be geocoded."
        )
    else:
        out["message"] = (
            f"Solid lines = hike legs ({leg_note}); dashed = overnight camp sequence ({camp_note}). "
            "Lines are schematic (straight segments), not official trail tread."
        )

    _log(
        f"dest={destination_name!r} stops={len(stops)} legs={len(hike_legs_raw)} "
        f"legs_mapped={legs_mapped} resolved={resolved} failed={len(failed)} "
        f"nps_matches={out.get('nps_camp_matches')} nps_coords={nps_with_coords} park={park!r}",
    )
    return out
