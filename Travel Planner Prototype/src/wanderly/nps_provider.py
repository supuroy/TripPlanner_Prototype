"""
U.S. National Park Service Data API integration for outdoor / hiking trips.

Docs: https://www.nps.gov/subjects/developer/api-documentation.htm
Base: https://developer.nps.gov/api/v1/

Requires ``NPS_API_KEY`` (free registration at developer.nps.gov).
Covers U.S. NPS units only — Parks Canada / provincial parks are out of scope.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.integrations.cursor_agent_client import load_env_from_roots
from src.wanderly.outdoor_adventure import is_outdoor_adventure_profile, normalize_camp_night

_NPS_BASE = "https://developer.nps.gov/api/v1"
_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "nps"
_CACHE_TTL_S = 60 * 60 * 12  # 12 hours

# Common destination phrases → official parkCode (lowercase, comma-separated in API).
_PARK_CODE_ALIASES: dict[str, str] = {
    "yosemite": "yose",
    "grand canyon": "grca",
    "zion": "zion",
    "yellowstone": "yell",
    "glacier": "glac",
    "rocky mountain": "romo",
    "rockies national park": "romo",
    "acadia": "acad",
    "olympic": "olym",
    "mount rainier": "mora",
    "rainier": "mora",
    "joshua tree": "jotr",
    "arches": "arch",
    "canyonlands": "cany",
    "great smoky": "grsm",
    "smoky mountains": "grsm",
    "denali": "dena",
    "big bend": "bibe",
    "death valley": "deva",
    "sequoia": "seki",
    "kings canyon": "seki",
    "crater lake": "crla",
    "bryce canyon": "brca",
    "capitol reef": "care",
    "mesa verde": "meve",
    "everglades": "ever",
    "shenandoah": "shen",
    "badlands": "badl",
    "arches and canyonlands": "arch,cany",
    "channel islands": "chis",
    "isle royale": "isro",
    "north cascades": "noca",
    "redwood": "redw",
    "lassen": "lavo",
    "pinnacles": "pinn",
    "great basin": "grba",
    "congaree": "cong",
    "guadalupe mountains": "gumo",
    "hot springs": "hosp",
    "indiana dunes": "indu",
    "virgin islands": "viis",
    "wrangell": "wrst",
    "katmai": "katm",
    "kenai fjords": "kefj",
    "glacier bay": "glba",
    "haleakala": "hale",
    "hawaii volcanoes": "havo",
    "volcanoes national park": "havo",
    "petrified forest": "pefo",
    "saguaro": "sagu",
    "white sands": "whsa",
    "carlsbad caverns": "cave",
    "black canyon": "blca",
    "great sand dunes": "grsa",
    "wind cave": "wica",
    "theodore roosevelt": "thro",
    "voyageurs": "voya",
    "cuyahoga": "cuva",
    "new river gorge": "neri",
    "gateway arch": "jeff",
    "american samoa": "npsa",
    "dry tortugas": "drto",
    "biscayne": "bisc",
    "point reyes": "pore",  # NPS affiliated — may not resolve; search fallback
}

_US_COUNTRY_RE = re.compile(
    r"\b(usa|u\.?s\.?a\.?|united states|america)\b", re.I
)
_CANADA_RE = re.compile(r"\b(canada|bc|british columbia|alberta|ontario|parks canada)\b", re.I)
# Yellowstone backcountry site codes (e.g. 8H2, 8C6, Coyote (8S3)) — not in NPS ``/campgrounds``.
_BC_SITE_CODE_LEAD_RE = re.compile(r"^\s*(\d+[A-Za-z]\d+)\s+", re.I)
_BC_SITE_CODE_PAREN_RE = re.compile(r"\(\s*(\d+[A-Za-z]\d+)\s*\)", re.I)
_NPS_ENRICH_LOG = _CACHE_DIR / "nps_enrich.log"

# In-park zone names → parkCode when trip context already identified the parent park.
_ZONE_TO_PARK: dict[str, str] = {
    "heart lake": "yell",
    "snake river": "yell",
    "thorofare": "yell",
    "shoshone lake": "yell",
    "yellowstone lake": "yell",
    "old faithful": "yell",
    "grand prismatic": "yell",
    "lamar valley": "yell",
    "slough creek": "yell",
    "mirror lake": "yose",
    "half dome": "yose",
    "tuolumne": "yose",
    "angels landing": "zion",
    "the narrows": "zion",
    "bright angel": "grca",
    "phantom ranch": "grca",
}


def nps_api_key() -> str:
    load_env_from_roots()
    return (os.environ.get("NPS_API_KEY") or os.environ.get("NPS_API_TOKEN") or "").strip()


def nps_configured() -> bool:
    return bool(nps_api_key())


def log_nps_enrich(message: str) -> None:
    """Append one line to ``.cache/nps/nps_enrich.log`` for operator review."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with _NPS_ENRICH_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} {message}\n")
    except OSError:
        pass


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return _CACHE_DIR / f"{digest}.json"


def _read_cache(key: str) -> Any | None:
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
    return raw.get("payload")


def _write_cache(key: str, payload: Any) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(
            json.dumps({"_cached_at": time.time(), "payload": payload}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _get(
    resource: str,
    *,
    params: dict[str, str | int] | None = None,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    key = nps_api_key()
    if not key:
        return []
    q = dict(params or {})
    cache_key = json.dumps({"r": resource, "p": q}, sort_keys=True)
    if use_cache:
        hit = _read_cache(cache_key)
        if isinstance(hit, list):
            return hit
    q.setdefault("limit", 50)
    headers = {"X-Api-Key": key}
    url = f"{_NPS_BASE}/{resource.strip('/')}"
    try:
        with httpx.Client(timeout=45.0) as client:
            res = client.get(url, params=q, headers=headers)
        if res.status_code == 401:
            return []
        res.raise_for_status()
        body = res.json()
    except (httpx.HTTPError, json.JSONDecodeError):
        return []
    data = body.get("data") if isinstance(body, dict) else None
    rows = [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
    if use_cache and rows:
        _write_cache(cache_key, rows)
    return rows


def _blob(*parts: str | None) -> str:
    return " ".join(str(p or "") for p in parts).lower()


def _likely_us_destination(country: str | None, *extra: str | None) -> bool:
    blob = _blob(country, *extra)
    if _CANADA_RE.search(blob) and not _US_COUNTRY_RE.search(blob):
        return False
    if _US_COUNTRY_RE.search(blob):
        return True
    # State abbreviations / names without explicit country
    if re.search(
        r"\b(california|colorado|utah|arizona|wyoming|montana|washington|oregon|"
        r"nevada|new mexico|texas|florida|maine|vermont|hawaii|alaska)\b",
        blob,
    ):
        return True
    return False


def _alias_codes_from_blob(blob: str) -> list[str]:
    codes: list[str] = []
    for phrase, code in _PARK_CODE_ALIASES.items():
        if phrase in blob:
            for c in code.split(","):
                c = c.strip().lower()
                if c and c not in codes:
                    codes.append(c)
    return codes


def infer_park_code_from_name(name: str) -> str | None:
    """Return the first NPS ``parkCode`` matching a place name, or ``None``."""
    codes = _alias_codes_from_blob(_blob(name))
    return codes[0] if codes else None


def _trip_context_blob(
    trip_name: str | None,
    peer_destination_names: list[str] | None,
    profile_pills: list[dict[str, Any]] | None,
) -> str:
    parts: list[str] = [(trip_name or "").strip()]
    for nm in peer_destination_names or []:
        parts.append(str(nm or "").strip())
    for p in profile_pills or []:
        if isinstance(p, dict):
            parts.append(str(p.get("value") or ""))
            parts.append(str(p.get("label") or ""))
    return _blob(*parts)


def _zone_park_for_card(card_blob: str, trip_codes: list[str]) -> str | None:
    """Map in-park zone labels (Heart Lake, etc.) when the trip already identified the park."""
    if not trip_codes:
        return None
    trip_set = set(trip_codes)
    for zone, code in _ZONE_TO_PARK.items():
        if zone in card_blob and code in trip_set:
            return code
    if len(trip_codes) == 1:
        return trip_codes[0]
    return None


def resolve_park_codes(
    *,
    destination_name: str | None = None,
    destination_country: str | None = None,
    hook: str | None = None,
    extra_queries: list[str] | None = None,
    trip_name: str | None = None,
    peer_destination_names: list[str] | None = None,
    profile_pills: list[dict[str, Any]] | None = None,
) -> tuple[list[str], str]:
    """
    Return (park_codes, rationale). Empty list when not a U.S. NPS trip or no match.
    """
    name = (destination_name or "").strip()
    country = (destination_country or "").strip()
    if not name:
        return [], "no_destination_name"
    card_blob = _blob(name, hook, country)
    if _CANADA_RE.search(card_blob) and "united states" not in card_blob and "usa" not in card_blob:
        return [], "canadian_park_out_of_nps_scope"

    trip_blob = _trip_context_blob(trip_name, peer_destination_names, profile_pills)
    trip_codes = _alias_codes_from_blob(trip_blob)
    card_codes = _alias_codes_from_blob(card_blob)

    if card_codes:
        return card_codes, "alias_match"

    zone_code = _zone_park_for_card(card_blob, trip_codes)
    if zone_code:
        return [zone_code], "trip_zone_match"

    if trip_codes and _likely_us_destination(country, name, hook):
        return trip_codes[:3], "trip_context_inherit"

    if not _likely_us_destination(country, name, hook):
        return [], "not_us_nps_destination"
    if not nps_configured():
        return [], "nps_api_key_missing"

    # Last resort: parks text search (often wrong for zone-only card titles — prefer trip context above).
    search_parts = [name]
    if trip_name and trip_name.lower() not in name.lower():
        search_parts.append(trip_name)
    for q in extra_queries or []:
        q = (q or "").strip()
        if q:
            search_parts.append(q)
    search_q = " ".join(search_parts)[:120]
    rows = _get("parks", params={"q": search_q, "limit": 5})
    codes: list[str] = []
    for row in rows:
        pc = str(row.get("parkCode") or "").strip().lower()
        if pc and pc not in codes:
            codes.append(pc)
    if codes:
        return codes[:6], "parks_search"
    return [], "no_park_match"


def normalize_alert(row: dict[str, Any]) -> dict[str, Any]:
    title = str(row.get("title") or "").strip()
    if not title:
        return {}
    out: dict[str, Any] = {
        "title": title[:200],
        "description": str(row.get("description") or "").strip()[:1200] or None,
        "category": str(row.get("category") or "").strip() or None,
        "url": str(row.get("url") or "").strip() or None,
        "park_code": str(row.get("parkCode") or "").strip().lower() or None,
    }
    last = row.get("lastIndexedDate")
    if isinstance(last, str) and last.strip():
        out["last_indexed"] = last.strip()[:32]
    return {k: v for k, v in out.items() if v is not None}


def _parse_latitude(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if -90.0 <= v <= 90.0 else None


def _parse_longitude(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if -180.0 <= v <= 180.0 else None


def normalize_campground(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("name") or "").strip()
    if not name:
        return {}
    fees = row.get("fees")
    fee_summary: list[str] = []
    if isinstance(fees, list):
        for f in fees[:6]:
            if isinstance(f, dict):
                t = str(f.get("title") or f.get("name") or "").strip()
                c = str(f.get("cost") or f.get("price") or "").strip()
                if t or c:
                    fee_summary.append(f"{t}: {c}".strip(": ")[:80])
    oh = row.get("operatingHours")
    hours_note: str | None = None
    if isinstance(oh, list) and oh:
        descs = [
            str(x.get("description") or "").strip()
            for x in oh[:3]
            if isinstance(x, dict) and str(x.get("description") or "").strip()
        ]
        if descs:
            hours_note = "; ".join(descs)[:400]
    return {
        "name": name[:160],
        "description": str(row.get("description") or "").strip()[:800] or None,
        "reservation_url": str(row.get("reservationUrl") or row.get("reservation_url") or "").strip()
        or None,
        "park_code": str(row.get("parkCode") or "").strip().lower() or None,
        "fee_summary": fee_summary or None,
        "operating_hours": hours_note,
        "amenities": _string_list(row.get("amenities"), limit=12),
        "accessibility": _string_list(row.get("accessibility"), limit=8),
        "regulations": str(row.get("regulations") or "").strip()[:500] or None,
        "latitude": _parse_latitude(row.get("latitude")),
        "longitude": _parse_longitude(row.get("longitude")),
    }


def _string_list(raw: Any, *, limit: int = 10) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    out = [str(x).strip() for x in raw if str(x).strip()][:limit]
    return out or None


def normalize_park_fees(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("fullName") or row.get("name") or "").strip()
    code = str(row.get("parkCode") or "").strip().lower()
    entrance: list[dict[str, str]] = []
    for ef in row.get("entranceFees") or []:
        if not isinstance(ef, dict):
            continue
        title = str(ef.get("title") or "Entrance fee").strip()
        cost = str(ef.get("cost") or "").strip()
        desc = str(ef.get("description") or "").strip()
        if title or cost:
            entrance.append(
                {
                    "title": title[:120],
                    "cost": cost[:80],
                    "description": desc[:400],
                }
            )
    passes: list[dict[str, str]] = []
    for pi in row.get("entrancePasses") or []:
        if not isinstance(pi, dict):
            continue
        title = str(pi.get("title") or "Pass").strip()
        cost = str(pi.get("cost") or "").strip()
        desc = str(pi.get("description") or "").strip()
        if title or cost:
            passes.append(
                {"title": title[:120], "cost": cost[:80], "description": desc[:400]}
            )
    fees_raw = row.get("fees")
    other_fees: list[str] = []
    if isinstance(fees_raw, list):
        for f in fees_raw[:8]:
            if isinstance(f, dict):
                t = str(f.get("title") or "").strip()
                c = str(f.get("cost") or "").strip()
                if t or c:
                    other_fees.append(f"{t}: {c}".strip(": ")[:120])
    return {
        "park_name": name[:160] if name else None,
        "park_code": code or None,
        "url": str(row.get("url") or "").strip() or None,
        "entrance_fees": entrance or None,
        "entrance_passes": passes or None,
        "other_fees": other_fees or None,
        "reservation_info": _reservation_blurb(row),
    }


def _reservation_blurb(park: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for key in ("recreationalActivities", "activities"):
        raw = park.get(key)
        if not isinstance(raw, list):
            continue
        for act in raw[:20]:
            if not isinstance(act, dict):
                continue
            nm = str(act.get("name") or "").strip().lower()
            if "permit" in nm or "reservation" in nm or "camping" in nm:
                parts.append(str(act.get("name") or "").strip())
    if parts:
        return "; ".join(dict.fromkeys(parts))[:300]
    return None


def _extract_bc_site_code(label: str) -> str | None:
    """Yellowstone-style permit site id from leading ``8H2 …`` or parenthetical ``(8S3)``."""
    raw = (label or "").strip()
    if not raw:
        return None
    m = _BC_SITE_CODE_LEAD_RE.match(raw)
    if m:
        return m.group(1).upper()
    m = _BC_SITE_CODE_PAREN_RE.search(raw)
    if m:
        return m.group(1).upper()
    return None


def _parse_camp_label(label: str) -> tuple[str, bool]:
    """Return (label_for_matching, is_backcountry_site_code)."""
    raw = (label or "").strip()
    if not raw:
        return "", False
    is_bc = _extract_bc_site_code(raw) is not None or "backcountry" in raw.lower()
    stripped = _BC_SITE_CODE_LEAD_RE.sub("", raw)
    stripped = _BC_SITE_CODE_PAREN_RE.sub("", stripped)
    stripped = re.sub(r"\b(campground|campsite)\b", "", stripped, flags=re.I)
    stripped = " ".join(stripped.split()).strip(" -,")
    return stripped or raw, is_bc


def _camp_label_dedupe_key(label: str) -> str:
    code = _extract_bc_site_code(label)
    if code:
        return f"bc:{code}"
    base = re.sub(r"\b(campground|campsite)\b", "", label, flags=re.I)
    base = _BC_SITE_CODE_PAREN_RE.sub("", base)
    return re.sub(r"\W+", " ", base.lower()).strip()


def _prefer_camp_label(a: str, b: str) -> str:
    """Keep the label with a site code, else the longer / more specific string."""
    if _extract_bc_site_code(a) and not _extract_bc_site_code(b):
        return a
    if _extract_bc_site_code(b) and not _extract_bc_site_code(a):
        return b
    return a if len(a) >= len(b) else b


def dedupe_camp_labels(labels: list[str]) -> list[str]:
    """Collapse ``Coyote (8S3)`` + ``Coyote campsite`` into one row per site or place name."""
    by_key: dict[str, str] = {}
    order: list[str] = []
    name_to_key: dict[str, str] = {}

    def _norm_place(label: str) -> str:
        place, _ = _parse_camp_label(label)
        return re.sub(r"\W+", " ", place.lower()).strip()

    for lab in labels:
        t = (lab or "").strip()
        if not t:
            continue
        norm_name = _norm_place(t)
        code = _extract_bc_site_code(t)

        if code:
            key = f"bc:{code}"
            if norm_name and norm_name in name_to_key:
                old_key = name_to_key[norm_name]
                if old_key != key and old_key.startswith("name:") and old_key in by_key:
                    t = _prefer_camp_label(t, by_key.pop(old_key))
                    order.remove(old_key)
            if norm_name:
                name_to_key[norm_name] = key
        elif norm_name and norm_name in name_to_key:
            key = name_to_key[norm_name]
        else:
            key = f"name:{norm_name}" if norm_name else f"raw:{t.lower()}"
            if norm_name:
                name_to_key[norm_name] = key

        if key not in by_key:
            by_key[key] = t
            order.append(key)
        else:
            by_key[key] = _prefer_camp_label(by_key[key], t)
            if code and norm_name:
                name_to_key[norm_name] = key

    return [by_key[k] for k in order]


def _camp_match_score(camp_label: str, cg_name: str) -> float:
    a = re.sub(r"\W+", " ", camp_label.lower()).strip()
    b = re.sub(r"\W+", " ", cg_name.lower()).strip()
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    at = set(a.split())
    bt = set(b.split())
    if not at or not bt:
        return 0.0
    return len(at & bt) / max(len(at), len(bt))


def match_campgrounds_to_plan(
    campgrounds: list[dict[str, Any]],
    camp_labels: list[str],
    *,
    min_score: float = 0.35,
    park_codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Attach plan_camp_label to best NPS campground row per planned camp."""
    used: set[str] = set()
    matches: list[dict[str, Any]] = []
    bc_hint = (
        "Yellowstone backcountry camps (site codes like 8H2) are permit zones — not listed in the "
        "NPS developed-campground API. Use recreation.gov / park backcountry permit pages."
        if park_codes == ["yell"] or (park_codes and "yell" in park_codes)
        else "This looks like a designated backcountry or permit campsite — the NPS campground API "
        "only lists developed front-country campgrounds (reservable loops), not wilderness site codes."
    )
    for label in camp_labels:
        label = (label or "").strip()
        if not label:
            continue
        match_label, is_bc_site = _parse_camp_label(label)
        best: tuple[float, dict[str, Any]] | None = None
        for cg in campgrounds:
            nm = str(cg.get("name") or "")
            if not nm or nm in used:
                continue
            sc = _camp_match_score(match_label, nm)
            if sc >= min_score and (best is None or sc > best[0]):
                best = (sc, cg)
        if best:
            used.add(str(best[1].get("name") or ""))
            row = dict(best[1])
            row["plan_camp_label"] = label
            row["match_score"] = round(best[0], 2)
            if is_bc_site:
                row["status"] = "fuzzy_developed_match"
                row["notes"] = (
                    f"Closest developed NPS campground name (score {row['match_score']}). "
                    f"Your plan uses a backcountry site code — verify against the park permit map."
                )
            matches.append(row)
        else:
            status = "backcountry_not_in_api" if is_bc_site else "not_found_in_nps"
            matches.append(
                {
                    "plan_camp_label": label,
                    "name": None,
                    "status": status,
                    "notes": bc_hint if is_bc_site else (
                        "No NPS developed-campground listing matched this name. "
                        "It may be backcountry, a non-NPS site, or outside the parks we queried."
                    ),
                }
            )
    return matches


def collect_camp_labels_from_itinerary(itinerary: list[dict[str, Any]] | None) -> list[str]:
    labels: list[str] = []
    for day in itinerary or []:
        if not isinstance(day, dict):
            continue
        dl = day.get("day_logistics")
        if not isinstance(dl, dict):
            continue
        camp = normalize_camp_night(dl.get("camp_night"))
        if camp:
            lab = str(camp.get("label") or "").strip()
            if lab:
                labels.append(lab)
    return dedupe_camp_labels(labels)


def enrich_outdoor_nps(
    *,
    destination_name: str | None,
    destination_country: str | None = None,
    destination_hook: str | None = None,
    itinerary: list[dict[str, Any]] | None = None,
    outdoor_adventure: dict[str, Any] | None = None,
    profile_pills: list[dict[str, Any]] | None = None,
    trip_name: str | None = None,
    peer_destination_names: list[str] | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """
    Build NPS context for a destination card: alerts, campground verification, fees/passes.

    Returns ``{ ok, configured, in_scope, park_codes, parks, alerts, campgrounds, camp_matches, ... }``.
    """
    out: dict[str, Any] = {
        "ok": False,
        "configured": nps_configured(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://developer.nps.gov/api/v1/",
        "docs_url": "https://www.nps.gov/subjects/developer/api-documentation.htm",
    }
    if profile_pills is not None and not is_outdoor_adventure_profile(profile_pills):
        out["in_scope"] = False
        out["message"] = "Trip style is not hiking/camping — NPS outdoor enrich skipped."
        return out

    camp_labels = collect_camp_labels_from_itinerary(itinerary)
    if outdoor_adventure and isinstance(outdoor_adventure.get("camp_nights"), list):
        for row in outdoor_adventure["camp_nights"]:
            if isinstance(row, dict):
                lab = str(row.get("label") or "").strip()
                if lab:
                    camp_labels.append(lab)
        camp_labels = dedupe_camp_labels(camp_labels)

    extra_q: list[str] = []
    if outdoor_adventure:
        for pr in outdoor_adventure.get("permit_requirements") or []:
            if isinstance(pr, dict):
                nm = str(pr.get("name") or "").strip()
                if nm:
                    extra_q.append(nm)

    park_codes, resolve_how = resolve_park_codes(
        destination_name=destination_name,
        destination_country=destination_country,
        hook=destination_hook,
        extra_queries=extra_q,
        trip_name=trip_name,
        peer_destination_names=peer_destination_names,
        profile_pills=profile_pills,
    )
    out["park_codes"] = park_codes
    out["resolve_method"] = resolve_how
    out["camp_labels_queried"] = camp_labels

    if resolve_how == "canadian_park_out_of_nps_scope":
        out["in_scope"] = False
        out["ok"] = True
        out["message"] = (
            "This destination appears to be in Canada. The NPS API covers U.S. National Park Service "
            "units only — use Parks Canada / provincial permit links in your trip notes."
        )
        out["permits_from_plan"] = _permits_from_outdoor(outdoor_adventure)
        return out

    if not out["configured"]:
        out["in_scope"] = True
        out["message"] = (
            "Set NPS_API_KEY in .env (free at developer.nps.gov) to load live alerts, "
            "campgrounds, and entrance fees."
        )
        out["permits_from_plan"] = _permits_from_outdoor(outdoor_adventure)
        return out

    if not park_codes:
        out["in_scope"] = True
        out["message"] = (
            "Could not map this destination to a U.S. NPS park code. "
            "Permits and fees below come from your itinerary only."
        )
        out["permits_from_plan"] = _permits_from_outdoor(outdoor_adventure)
        return out

    out["in_scope"] = True
    pc_param = ",".join(park_codes)

    alert_rows = _get(
        "alerts",
        params={"parkCode": pc_param, "limit": 50},
        use_cache=use_cache,
    )
    alerts = [normalize_alert(r) for r in alert_rows]
    alerts = [a for a in alerts if a.get("title")]
    out["alerts"] = alerts

    park_rows = _get(
        "parks",
        params={"parkCode": pc_param, "limit": len(park_codes) + 2},
        use_cache=use_cache,
    )
    parks = [normalize_park_fees(r) for r in park_rows]
    parks = [p for p in parks if p.get("park_code") or p.get("park_name")]
    out["parks"] = parks

    cg_rows = _get(
        "campgrounds",
        params={"parkCode": pc_param, "limit": 50},
        use_cache=use_cache,
    )
    campgrounds = [normalize_campground(r) for r in cg_rows]
    campgrounds = [c for c in campgrounds if c.get("name")]
    out["campgrounds"] = campgrounds
    out["camp_matches"] = match_campgrounds_to_plan(
        campgrounds, camp_labels, park_codes=park_codes
    )
    cg_names = [str(c.get("name") or "") for c in campgrounds[:20]]
    out["campground_pool_sample"] = cg_names

    out["permits_from_plan"] = _permits_from_outdoor(outdoor_adventure)
    out["fee_links"] = _fee_links(parks)
    out["ok"] = True
    out["message"] = None

    log_nps_enrich(
        f"dest={destination_name!r} trip={trip_name!r} resolve={resolve_how} parks={park_codes} "
        f"labels={camp_labels!r} cg_count={len(campgrounds)} "
        f"matches={[m.get('status') or 'hit' for m in out['camp_matches']]}"
    )
    return out


def _permits_from_outdoor(outdoor: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not outdoor:
        return []
    out: list[dict[str, Any]] = []
    for row in outdoor.get("permit_requirements") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        entry: dict[str, Any] = {"name": name[:120]}
        for fk in ("agency", "quota_window", "booking_url_hint", "notes", "required"):
            v = row.get(fk)
            if isinstance(v, str) and v.strip():
                entry[fk] = v.strip()[:300]
            elif fk == "required" and v is True:
                entry["required"] = True
        out.append(entry)
    return out


def _fee_links(parks: list[dict[str, Any]]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for p in parks:
        url = str(p.get("url") or "").strip()
        nm = str(p.get("park_name") or "Park").strip()
        if url and url not in seen:
            seen.add(url)
            links.append({"label": f"{nm} — plan your visit", "url": url})
    generic = "https://www.nps.gov/planyourvisit/pass.htm"
    if generic not in seen:
        links.append({"label": "America the Beautiful passes (NPS)", "url": generic})
    return links
