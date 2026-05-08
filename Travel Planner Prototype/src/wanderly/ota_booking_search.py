"""
Lab-only Expedia snapshot search: Playwright fetch + heuristic parse (hotels + flights).

Flights / hotels: optional Playwright HTML → Cursor LLM listing parse (``BOOKING_SEARCH_FLIGHT_LLM_PARSE`` /
``BOOKING_SEARCH_HOTEL_LLM_PARSE``); optional advisor-tier rerank (``BOOKING_SEARCH_*_ADVISOR_RANK``). Default flight
URLs are **one-way** outbound (``BOOKING_SEARCH_FLIGHT_FORCE_ONEWAY``).

Requires sibling ``expedia-listing-cli`` on sys.path and optional catalog JSON paths in env.
Set BOOKING_SEARCH_ENABLED=0 to disable network/browser work from the API.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from src.integrations.cursor_agent_client import basic_client, get_api_key, load_env_from_roots, run_prompt_collect_assistant_text
from src.wanderly.cursor_advisor import _extract_json_object, structured_followup_model_id
from src.wanderly.session_store import (
    cursor_run_persist_kwargs_for_lane,
    get_booking_hotels_plan,
    get_booking_search_intent,
    set_booking_hotels_plan,
    set_booking_search_intent,
)

_TRAVEL_PLANNER_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _TRAVEL_PLANNER_ROOT.parent
_EXPEDIA_CLI = _REPO_ROOT / "expedia-listing-cli"


def _trace_log_path() -> Path:
    raw = (os.environ.get("BOOKING_SEARCH_TRACE_LOG") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = _TRAVEL_PLANNER_ROOT / raw
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    d = _TRAVEL_PLANNER_ROOT / ".cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / "booking_search_trace.log"


def booking_search_trace(event: str, **fields: Any) -> None:
    """Append JSON lines to trace file + stdout (for debugging URLs and HTML snapshots)."""
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    line = json.dumps(rec, ensure_ascii=False, default=str)
    try:
        with _trace_log_path().open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        print(f"[booking_search] trace file write failed: {exc}", flush=True)
    # Short console line (full URL + key sizes)
    url = fields.get("url") or fields.get("search_url")
    extra = f" url={url}" if url else ""
    hlen = fields.get("html_len")
    if hlen is not None:
        extra += f" html_len={hlen}"
    print(f"[booking_search] {event}{extra}", flush=True)


def _ensure_cli_path() -> None:
    p = str(_EXPEDIA_CLI)
    if p not in sys.path:
        sys.path.insert(0, p)


def booking_search_enabled() -> bool:
    return os.environ.get("BOOKING_SEARCH_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def booking_search_api_debug_enabled() -> bool:
    """When true, booking_search_step JSON includes a ``debug_parse`` object for browser/server logs."""
    return os.environ.get("BOOKING_SEARCH_API_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _flight_force_oneway_url() -> bool:
    """
    When true (default), flight catalog URLs use **one-way** ``trip=oneway`` and omit ``leg2``.

    Round-trip SRP snapshots often omit return-leg rows until a second UI step, so a single HTML fetch
    is a poor match for ``booking_search_step``'s one-card summary.
    """
    return os.environ.get("BOOKING_SEARCH_FLIGHT_FORCE_ONEWAY", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _short(s: str | None, n: int = 160) -> str:
    if not s:
        return ""
    t = " ".join(str(s).split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _parse_adults_from_who(who_val: str) -> int:
    """
    Prefer explicit ``N adults`` / ``N adult``; avoid concatenating all digits (e.g. ``2 adults 2 kids`` → ``222``).
    Recognize solo-traveler copy (no leading digit) before defaulting to 2.
    """
    s = (who_val or "").strip()
    if not s:
        return 2
    low = s.lower()
    if any(
        x in low
        for x in (
            "solo traveler",
            "solo traveller",
            "solo trip",
            "traveling alone",
            "travelling alone",
            "travel alone",
            "just me",
            "only me",
            "by myself",
            "single traveler",
            "single traveller",
            "one traveler",
            "1 traveler",
            "going solo",
        )
    ) or re.search(r"\bsolo\b", low):
        return 1
    m = re.search(r"(\d+)\s*adults?", s, flags=re.I)
    if m:
        return max(1, min(9, int(m.group(1))))
    m2 = re.search(r"\b(\d+)\s*(?:pax|travelers?|guests?|people|person)\b", s, flags=re.I)
    if m2:
        n = int(m2.group(1))
        if 1 <= n <= 9:
            return n
    if re.search(r"\bcouple\b|\bpair\b|\btwo of us\b", s, flags=re.I):
        return 2
    lead = re.search(r"^\s*(\d+)\b", s)
    if lead:
        n = int(lead.group(1))
        if 1 <= n <= 9:
            return n
    return 2


_MONTH_NAME_TO_NUM: list[tuple[str, int]] = [
    ("january", 1),
    ("february", 2),
    ("march", 3),
    ("april", 4),
    ("may", 5),
    ("june", 6),
    ("july", 7),
    ("august", 8),
    ("september", 9),
    ("october", 10),
    ("november", 11),
    ("december", 12),
    ("jan", 1),
    ("feb", 2),
    ("mar", 3),
    ("apr", 4),
    ("jun", 6),
    ("jul", 7),
    ("aug", 8),
    ("sep", 9),
    ("oct", 10),
    ("nov", 11),
    ("dec", 12),
]


def _month_number_in_text(s: str) -> int | None:
    """First calendar month mentioned in ``s`` (word-boundary match; longer tokens first)."""
    s_low = s.lower()
    for name, mo in sorted(_MONTH_NAME_TO_NUM, key=lambda t: -len(t[0])):
        if re.search(rf"\b{re.escape(name)}\b", s_low):
            return mo
    return None


def _season_day_anchor(s_low: str) -> int:
    """Day-of-month anchor for early / mid / late phrases."""
    if re.search(r"\b(early|beginning|start|first\s+half)\b", s_low):
        return 7
    if re.search(r"\b(late|end|last\s+half)\b", s_low):
        return 23
    if re.search(r"\b(mid|middle)\b", s_low):
        return 15
    return 15


def _safe_date(y: int, month: int, day: int) -> date | None:
    try:
        return date(y, month, day)
    except ValueError:
        for d in range(day, 0, -1):
            try:
                return date(y, month, d)
            except ValueError:
                continue
    return None


def _bump_start_to_future(start: date, end: date, today: date) -> tuple[date, date]:
    """If ``start`` is before ``today``, shift the window into the next year(s) keeping span length."""
    span = max(0, (end - start).days)
    y = start.year
    candidate = _safe_date(y, start.month, start.day)
    if candidate is None:
        return start, end
    while candidate < today:
        y += 1
        candidate = _safe_date(y, start.month, start.day)
        if candidate is None:
            break
    if candidate is None:
        return start, end
    end_c = candidate + timedelta(days=span)
    return candidate, end_c


def _when_pill_dates(when_val: str, *, itinerary_days: int, year: int | None = None) -> tuple[date | None, date | None]:
    """Parse ``Mar 15–22`` / ``Mar 15-22`` style into start/end (end exclusive span by itinerary_days if needed)."""
    if not when_val or not when_val.strip():
        return None, None
    y = year or date.today().year
    m = re.match(
        r"(\w+)\s+(\d+)\s*[–-]\s*(\d+)",
        when_val.strip(),
        re.I,
    )
    month_map = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    if not m:
        return None, None
    mon_s, d1s, d2s = m.group(1).lower()[:3], m.group(2), m.group(3)
    mo = month_map.get(mon_s[:3])
    if mo is None:
        return None, None
    d1 = int(d1s)
    d2 = int(d2s)
    try:
        start = date(y, mo, d1)
        # Same month end day
        end = date(y, mo, d2)
        if end < start:
            end = date(y + 1, mo, d2)
        # Prefer itinerary span when longer
        if itinerary_days > 1:
            alt_end = start + timedelta(days=itinerary_days - 1)
            if alt_end > end:
                end = alt_end
        return start, end
    except ValueError:
        return None, None


def _guess_dates_from_vague_when(
    when_val: str,
    *,
    itinerary_days: int,
    today: date | None = None,
) -> tuple[date | None, date | None]:
    """
    Best-effort concrete dates for phrases like ``mid March``, ``late April``, ``March``,
    or ``Flexible`` so OTA search URLs always get ISO dates.
    """
    if not when_val or not when_val.strip():
        return None, None
    t = today or date.today()
    raw = when_val.strip()
    s_low = raw.lower()

    if re.search(
        r"\b(flexible|anytime|any\s*time|tbd|not\s*sure|whenever|open)\b",
        s_low,
    ):
        start = t + timedelta(days=21)
        end = start + timedelta(days=max(1, itinerary_days) - 1)
        return start, end

    mo = _month_number_in_text(raw)
    if mo is None:
        return None, None

    day_anchor = _season_day_anchor(s_low)
    y = t.year
    start = _safe_date(y, mo, day_anchor)
    if start is None:
        return None, None
    end = start + timedelta(days=max(1, itinerary_days) - 1)
    start, end = _bump_start_to_future(start, end, t)
    return start, end


def _default_search_dates(itinerary_days: int, today: date | None = None) -> tuple[date, date]:
    """Last resort when profile has no usable ``when``."""
    t = today or date.today()
    start = t + timedelta(days=21)
    end = start + timedelta(days=max(1, itinerary_days) - 1)
    return start, end


def booking_search_outline_llm_enabled() -> bool:
    """When false, multi-stay split uses itinerary heuristics only (no advisor/structured LLM call)."""
    return os.environ.get("BOOKING_SEARCH_OUTLINE_LLM", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _area_token_from_day_label(label: str) -> str:
    """Pull a location-ish token from a lazy-itinerary day title (arrow / middle-dot heuristics)."""
    s = " ".join(str(label or "").split())
    if not s:
        return ""
    if "→" in s:
        s = s.split("→", 1)[1].strip()
    elif re.search(r"->", s):
        s = re.split(r"->", s, 1, flags=re.I)[1].strip()
    parts = [p.strip() for p in s.split("·") if p.strip()]
    if len(parts) >= 2:
        return parts[-1]
    if parts:
        return parts[0]
    return s


def _image_fallback_from_dest(dest: dict[str, Any]) -> str:
    hotels = dest.get("hotels") or []
    if isinstance(hotels, list) and hotels:
        h0 = hotels[0]
        if isinstance(h0, dict):
            im = str(h0.get("image") or "").strip()
            if im:
                return im
    return ""


def _hotel_row_stub(area: str, nights: int, *, image: str, name: str = "Hotel placeholder") -> dict[str, Any]:
    return {
        "name": name,
        "area": area.strip() or "Stay area TBD",
        "price": "",
        "nights": max(1, int(nights)),
        "image": image,
        "best": True,
    }


def _normalize_hotel_rows_from_card(hotels: list[Any], image_fallback: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for h in hotels:
        if not isinstance(h, dict):
            continue
        area = str(h.get("area") or "").strip()
        nights = max(1, int(h.get("nights") or 1))
        img = str(h.get("image") or "").strip() or image_fallback
        out.append(
            {
                "name": str(h.get("name") or "Hotel placeholder"),
                "area": area or "Stay",
                "price": str(h.get("price") or ""),
                "nights": nights,
                "image": img,
                "best": bool(h.get("best", True)),
            }
        )
    return out


def _fit_stays_to_trip_nights(
    rows: list[dict[str, Any]], trip_nights: int, *, image_fallback: str
) -> list[dict[str, Any]]:
    """
    Normalize stay rows so their nights exactly match ``trip_nights``.

    Guarantees:
    - at least one stay row when ``trip_nights`` > 0
    - every stay has ``nights >= 1``
    - sum(nights) == ``trip_nights``
    """
    n = max(1, int(trip_nights))
    clean: list[dict[str, Any]] = []
    for i, h in enumerate(rows):
        if not isinstance(h, dict):
            continue
        area = str(h.get("area") or h.get("label") or "").strip() or "Stay"
        ni = h.get("nights")
        nights = int(ni) if isinstance(ni, (int, float)) else 1
        clean.append(
            {
                "name": str(h.get("name") or "Hotel placeholder"),
                "area": area,
                "price": str(h.get("price") or ""),
                "nights": max(1, nights),
                "image": str(h.get("image") or "").strip() or image_fallback,
                "best": bool(h.get("best", True)),
                "_idx": i,
            }
        )
    if not clean:
        return [_hotel_row_stub("Stay", n, image=image_fallback)]
    if len(clean) > n:
        clean = clean[:n]
    base = [1 for _ in clean]
    remaining = n - len(base)
    wishes = [max(1, int(h.get("nights") or 1)) for h in clean]
    for i in range(len(base)):
        if remaining <= 0:
            break
        add = max(0, wishes[i] - 1)
        take = min(add, remaining)
        base[i] += take
        remaining -= take
    i = 0
    while remaining > 0 and base:
        base[i % len(base)] += 1
        remaining -= 1
        i += 1
    out: list[dict[str, Any]] = []
    for idx, h in enumerate(clean):
        out.append(
            {
                "name": h["name"],
                "area": h["area"],
                "price": h["price"],
                "nights": base[idx],
                "image": h["image"],
                "best": h["best"],
            }
        )
    return out


def _stays_from_json_outline(
    dest: dict[str, Any], trip_nights: int, image_fallback: str
) -> tuple[list[dict[str, Any]] | None, str]:
    raw = dest.get("booking_search_outline")
    if not isinstance(raw, dict):
        return None, ""
    stays = raw.get("stays")
    if not isinstance(stays, list) or not stays:
        return None, ""
    rows: list[dict[str, Any]] = []
    for x in stays:
        if not isinstance(x, dict):
            continue
        area = str(x.get("area") or x.get("label") or "").strip()
        ni = x.get("nights")
        nights = int(ni) if isinstance(ni, (int, float)) else 0
        if not area or nights < 1:
            continue
        rows.append(_hotel_row_stub(area, nights, image=image_fallback))
    if not rows:
        return None, ""
    return _fit_stays_to_trip_nights(rows, trip_nights, image_fallback=image_fallback), "json_outline"


def _stays_from_itinerary_heuristic(
    dest: dict[str, Any],
    itinerary_rows: list[dict[str, Any]],
    trip_nights: int,
    image_fallback: str,
) -> tuple[list[dict[str, Any]], str]:
    name = str(dest.get("name") or "Destination")
    country = str(dest.get("country") or "")
    base_area = f"{name}, {country}".strip(", ")
    if not itinerary_rows or trip_nights <= 0:
        return [_hotel_row_stub(base_area, max(1, trip_nights), image=image_fallback)], "heuristic_default"
    rows = sorted(itinerary_rows, key=lambda r: int(r.get("day") or 0))
    groups: list[tuple[str, int]] = []
    i = 0
    while i < len(rows):
        tok = _area_token_from_day_label(str(rows[i].get("label") or ""))
        if not tok:
            tok = base_area
        j = i + 1
        while j < len(rows):
            t2 = _area_token_from_day_label(str(rows[j].get("label") or ""))
            if not t2:
                t2 = tok
            if t2.strip().lower() != tok.strip().lower():
                break
            j += 1
        groups.append((tok, j - i))
        i = j
    hotels = [_hotel_row_stub(area, cnt, image=image_fallback) for area, cnt in groups]
    hotels = _fit_stays_to_trip_nights(hotels, trip_nights, image_fallback=image_fallback)
    return hotels, "heuristic_itinerary"


def _itinerary_suggests_multi_base(itinerary_rows: list[dict[str, Any]]) -> bool:
    toks = [
        _area_token_from_day_label(str(r.get("label") or "")).strip().lower()
        for r in sorted(itinerary_rows, key=lambda x: int(x.get("day") or 0))
    ]
    toks = [t for t in toks if t]
    return len(set(toks)) >= 2


def run_booking_stays_outline_llm(
    *,
    dest: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    itinerary_rows: list[dict[str, Any]],
    trip_nights: int,
    start_iso: str,
    end_iso: str,
    session_id: str,
    image_fallback: str,
) -> list[dict[str, Any]] | None:
    if trip_nights < 2 or not itinerary_rows:
        return None
    load_env_from_roots()
    key = get_api_key()
    if not key:
        return None
    name = str(dest.get("name") or "Destination")
    country = str(dest.get("country") or "")
    dest_label = f"{name}, {country}".strip(", ")
    compact = [
        {"day": r.get("day"), "label": str(r.get("label") or "")[:160]}
        for r in sorted(itinerary_rows, key=lambda x: int(x.get("day") or 0))
    ]
    who_hint = ""
    for p in profile_pills:
        if isinstance(p, dict) and str(p.get("key") or "") == "who":
            who_hint = str(p.get("value") or "").strip()[:120]
            break
    prompt = f"""You split a trip into consecutive lodging stays for hotel search (one Expedia search per stay).

Return ONLY one JSON object (no markdown fences, no prose):
{{
  "stays": [
    {{"area": "Specific place, region, country — good Expedia destination query", "nights": <int>}}
  ]
}}

Rules:
- The sum of nights MUST equal {trip_nights} exactly.
- Order stays in trip order (first stay after arrival; last stay ends the last trip night).
- Use 2+ stays when days clearly move between distinct towns/bases. Use 1 stay only if every night is the same town.
- area: searchable on Expedia (include country when helpful).

Travelers: {who_hint or "not specified"}
Destination: {dest_label}
Dates: {start_iso} → {end_iso}

Day-by-day:
{json.dumps(compact, ensure_ascii=False)}
""".strip()
    model = structured_followup_model_id()
    rid = f"booking_outline:{str(dest.get('id') or '')[:24]}"
    try:
        with basic_client(key) as client:
            raw = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=model,
                **cursor_run_persist_kwargs_for_lane(session_id, "booking_outline", trace_path=f"{rid}.txt"),
            )
    except Exception as exc:
        booking_search_trace("outline_llm_exc", error=str(exc)[:400], dest_id=str(dest.get("id") or ""))
        return None
    parsed = _extract_json_object(raw or "")
    if not parsed:
        booking_search_trace("outline_llm_no_json", dest_id=str(dest.get("id") or ""))
        return None
    stays = parsed.get("stays")
    if not isinstance(stays, list) or not stays:
        return None
    rows: list[dict[str, Any]] = []
    for x in stays:
        if not isinstance(x, dict):
            continue
        area = str(x.get("area") or "").strip()
        ni = x.get("nights")
        nights = int(ni) if isinstance(ni, (int, float)) else 0
        if not area or nights < 1:
            continue
        rows.append(_hotel_row_stub(area, nights, image=image_fallback))
    if not rows:
        return None
    tot = sum(int(r["nights"]) for r in rows)
    if tot != trip_nights:
        booking_search_trace(
            "outline_llm_nights_mismatch",
            expected=trip_nights,
            got=tot,
            dest_id=str(dest.get("id") or ""),
        )
        return None
    booking_search_trace(
        "outline_llm_ok",
        dest_id=str(dest.get("id") or ""),
        stays=len(rows),
        trip_nights=trip_nights,
    )
    return rows


def booking_search_param_llm_enabled() -> bool:
    """Enable advisor LLM synthesis of Expedia search parameter intent."""
    return os.environ.get("BOOKING_SEARCH_PARAM_LLM", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _normalize_booking_search_intent(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    fl = raw.get("flight")
    if isinstance(fl, dict):
        f: dict[str, Any] = {}
        stops = str(fl.get("stops") or "").strip().lower()
        if stops in ("any", "nonstop", "max_1_stop"):
            f["stops"] = stops
        cabin = str(fl.get("cabin") or "").strip().lower()
        if cabin in ("economy", "premium_economy", "business", "first"):
            f["cabin"] = cabin
        carry_on = str(fl.get("carry_on") or "").strip().lower()
        if carry_on in ("required", "optional"):
            f["carry_on"] = carry_on
        if f:
            out["flight"] = f

    ht = raw.get("hotel")
    if isinstance(ht, dict):
        h: dict[str, Any] = {}
        sort = str(ht.get("sort") or "").strip().upper()
        if sort in ("RECOMMENDED", "PRICE_LOW_TO_HIGH", "PRICE_HIGH_TO_LOW", "GUEST_RATING"):
            h["sort"] = sort
        guest_rating_min = ht.get("guest_rating_min")
        if isinstance(guest_rating_min, (int, float)):
            h["guest_rating_min"] = max(0.0, min(5.0, float(guest_rating_min)))
        price_max = ht.get("price_max_usd")
        if isinstance(price_max, (int, float)) and int(price_max) > 0:
            h["price_max_usd"] = int(price_max)
        price_min = ht.get("price_min_usd")
        if isinstance(price_min, (int, float)) and int(price_min) >= 0:
            h["price_min_usd"] = int(price_min)
        star_min = ht.get("star_min")
        if isinstance(star_min, (int, float)):
            h["star_min"] = max(0, min(5, int(star_min)))
        traveler_type = str(ht.get("traveler_type") or "").strip().lower()
        if traveler_type in (
            "family_friendly",
            "business_friendly",
            "beach",
            "adventure",
            "couples",
            "lgbtq_welcoming",
        ):
            h["traveler_type"] = traveler_type
        meal_plan = str(ht.get("meal_plan") or "").strip().upper()
        if meal_plan in ("FREE_BREAKFAST",):
            h["meal_plan"] = meal_plan
        am_raw = ht.get("amenities")
        if isinstance(am_raw, list):
            allow = {"PETS", "POOL", "FREE_WIFI", "WIFI", "AIR_CONDITIONING", "GYM", "SPA", "KITCHEN"}
            am = [str(x).strip().upper() for x in am_raw if str(x).strip().upper() in allow]
            if am:
                h["amenities"] = am[:4]
        if h:
            out["hotel"] = h
    return out


def run_booking_search_params_llm(
    *,
    dest: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    itinerary_rows: list[dict[str, Any]],
    criteria_lines: list[str],
    start_iso: str,
    end_iso: str,
    trip_nights: int,
    session_id: str,
) -> dict[str, Any] | None:
    if not booking_search_param_llm_enabled():
        return None
    if not (session_id or "").strip():
        return None
    load_env_from_roots()
    key = get_api_key()
    if not key:
        return None
    model = structured_followup_model_id()
    name = str(dest.get("name") or "Destination")
    country = str(dest.get("country") or "")
    who = ""
    for p in profile_pills:
        if isinstance(p, dict) and str(p.get("key") or "") == "who":
            who = str(p.get("value") or "").strip()
            break
    compact_days = [
        {"day": r.get("day"), "label": str(r.get("label") or "")[:140]}
        for r in sorted(itinerary_rows, key=lambda x: int(x.get("day") or 0))
    ][:14]
    schema = {
        "type": "object",
        "properties": {
            "flight": {
                "type": "object",
                "properties": {
                    "stops": {"type": "string", "enum": ["any", "nonstop", "max_1_stop"]},
                    "cabin": {"type": "string", "enum": ["economy", "premium_economy", "business", "first"]},
                    "carry_on": {"type": "string", "enum": ["required", "optional"]},
                },
            },
            "hotel": {
                "type": "object",
                "properties": {
                    "sort": {
                        "type": "string",
                        "enum": ["RECOMMENDED", "PRICE_LOW_TO_HIGH", "PRICE_HIGH_TO_LOW", "GUEST_RATING"],
                    },
                    "star_min": {"type": "integer", "minimum": 0, "maximum": 5},
                    "guest_rating_min": {"type": "number", "minimum": 0, "maximum": 5},
                    "price_min_usd": {"type": "integer", "minimum": 0},
                    "price_max_usd": {"type": "integer", "minimum": 1},
                    "meal_plan": {"type": "string", "enum": ["FREE_BREAKFAST"]},
                    "amenities": {"type": "array", "items": {"type": "string"}},
                    "traveler_type": {"type": "string"},
                },
            },
            "rationale": {"type": "string"},
        },
    }
    prompt = f"""You produce Expedia booking-search parameter intent from traveler profile + itinerary.

Return ONLY one JSON object (no markdown fences, no prose) matching this schema:
{json.dumps(schema, ensure_ascii=True)}

Destination: {name}, {country}
Travelers: {who or "not specified"}
Dates: {start_iso} -> {end_iso}
Trip nights: {trip_nights}

Profile pills:
{json.dumps(profile_pills, ensure_ascii=False)[:7000]}

Criteria lines:
{json.dumps(criteria_lines, ensure_ascii=False)[:7000]}

Itinerary compact:
{json.dumps(compact_days, ensure_ascii=False)}

Guidance:
- Keep filters broad enough to return results, but aligned to profile constraints.
- Use nonstop only when strongly implied; otherwise prefer max 1 stop.
- For hotels, set practical budget/rating bounds when profile hints exist; avoid over-constraining.
""".strip()
    try:
        with basic_client(key) as client:
            raw = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=model,
                **cursor_run_persist_kwargs_for_lane(
                    session_id,
                    "booking_params_intent",
                    trace_path=f"booking_params:{str(dest.get('id') or '')[:24]}.txt",
                ),
            )
    except Exception as exc:
        booking_search_trace("params_llm_exc", dest_id=str(dest.get("id") or ""), error=str(exc)[:400])
        return None
    parsed = _extract_json_object(raw or "")
    if not isinstance(parsed, dict):
        booking_search_trace("params_llm_no_json", dest_id=str(dest.get("id") or ""))
        return None
    intent = _normalize_booking_search_intent(parsed)
    if not intent:
        return None
    booking_search_trace(
        "params_llm_ok",
        dest_id=str(dest.get("id") or ""),
        has_flight=bool(intent.get("flight")),
        has_hotel=bool(intent.get("hotel")),
    )
    return intent


def resolve_booking_hotels_plan(
    dest: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    itinerary_rows: list[dict[str, Any]],
    trip_nights: int,
    start_iso: str,
    end_iso: str,
    session_id: str | None,
) -> tuple[list[dict[str, Any]], str]:
    """
    Build ordered hotel rows for booking (multi-city splits). Prefer explicit JSON on card, then
    multi-row placeholders, itinerary clustering, then optional advisor LLM outline.
    """
    did = str(dest.get("id") or "").strip()
    sid = (session_id or "").strip()
    if sid and did:
        cached = get_booking_hotels_plan(sid, did)
        if cached:
            booking_search_trace(
                "booking_hotels_plan",
                source="session_cache",
                rows=len(cached),
                trip_nights=trip_nights,
            )
            return cached, "session_cache"

    image_fb = _image_fallback_from_dest(dest)
    ex = dest.get("hotels") or []
    ex_list = [x for x in ex if isinstance(x, dict)] if isinstance(ex, list) else []

    got, src = _stays_from_json_outline(dest, trip_nights, image_fb)
    if got:
        booking_search_trace("booking_hotels_plan", source=src, rows=len(got), trip_nights=trip_nights)
        return got, src

    if len(ex_list) >= 2:
        normalized = _normalize_hotel_rows_from_card(ex_list, image_fb)
        fitted = _fit_stays_to_trip_nights(normalized, trip_nights, image_fallback=image_fb)
        booking_search_trace("booking_hotels_plan", source="card_multi_fitted", rows=len(fitted), trip_nights=trip_nights)
        return fitted, "card_multi_fitted"

    heur, hsrc = _stays_from_itinerary_heuristic(dest, itinerary_rows, trip_nights, image_fb)
    if len(heur) >= 2:
        booking_search_trace("booking_hotels_plan", source=hsrc, rows=len(heur), trip_nights=trip_nights)
        return heur, hsrc

    want_llm = (
        booking_search_outline_llm_enabled()
        and bool((session_id or "").strip())
        and trip_nights >= 3
        and (
            _itinerary_suggests_multi_base(itinerary_rows)
            or (len(heur) == 1 and trip_nights >= 4 and len(itinerary_rows) >= 4)
        )
    )
    if want_llm:
        llm_rows = run_booking_stays_outline_llm(
            dest=dest,
            profile_pills=profile_pills,
            itinerary_rows=itinerary_rows,
            trip_nights=trip_nights,
            start_iso=start_iso,
            end_iso=end_iso,
            session_id=str(session_id),
            image_fallback=image_fb,
        )
        if llm_rows and len(llm_rows) >= 1:
            llm_rows = _fit_stays_to_trip_nights(llm_rows, trip_nights, image_fallback=image_fb)
            src_llm = "advisor_llm" if len(llm_rows) >= 2 else "advisor_llm_single"
            booking_search_trace("booking_hotels_plan", source=src_llm, rows=len(llm_rows), trip_nights=trip_nights)
            return llm_rows, src_llm

    booking_search_trace("booking_hotels_plan", source=hsrc, rows=len(heur), trip_nights=trip_nights)
    return heur, hsrc


def build_booking_criteria(
    dest: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    *,
    date_hints: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Human-readable lines + parsed dates / adults for search steps."""
    lines: list[str] = []
    who = "2 adults"
    when_s = ""
    from_s = ""
    for p in profile_pills:
        if not isinstance(p, dict):
            continue
        k = str(p.get("key") or "")
        v = str(p.get("value") or "").strip()
        if k == "who" and v:
            who = v
        if k == "when" and v:
            when_s = v
        if k == "from" and v:
            from_s = v

    name = str(dest.get("name") or "Destination")
    country = str(dest.get("country") or "")
    dest_label = f"{name}, {country}".strip(", ")

    itinerary = dest.get("itinerary") or []
    n_days = len(itinerary) if isinstance(itinerary, list) and len(itinerary) > 0 else 7

    start_d: date | None = None
    end_d: date | None = None
    date_guess_reason: str | None = None
    dh = date_hints or {}
    if isinstance(dh.get("start_iso"), str) and dh["start_iso"].strip():
        try:
            start_d = date.fromisoformat(dh["start_iso"].strip()[:10])
        except ValueError:
            start_d = None
    if isinstance(dh.get("end_iso"), str) and dh["end_iso"].strip():
        try:
            end_d = date.fromisoformat(dh["end_iso"].strip()[:10])
        except ValueError:
            end_d = None

    exact_start, exact_end = (None, None)
    if start_d is None or end_d is None:
        exact_start, exact_end = _when_pill_dates(when_s, itinerary_days=n_days)
        start_d = start_d or exact_start
        end_d = end_d or exact_end

    if start_d is None or end_d is None:
        gv, ge = _guess_dates_from_vague_when(when_s, itinerary_days=n_days)
        if gv is not None and ge is not None:
            start_d = start_d or gv
            end_d = end_d or ge
            if exact_start is None and exact_end is None:
                date_guess_reason = "vague"

    if start_d is None:
        start_d, end_d = _default_search_dates(n_days)
        date_guess_reason = "default"
    elif end_d is None:
        end_d = start_d + timedelta(days=max(1, n_days) - 1)

    shifted_past_window = False
    if start_d is not None and end_d is not None and start_d < date.today():
        start_d, end_d = _bump_start_to_future(start_d, end_d, date.today())
        shifted_past_window = True

    adults = _parse_adults_from_who(who)
    ao_raw = dh.get("adults")
    if isinstance(ao_raw, bool):
        ao_raw = None
    if isinstance(ao_raw, (int, float)):
        ai = int(ao_raw)
        if 1 <= ai <= 9:
            adults = ai
    elif isinstance(ao_raw, str) and ao_raw.strip().isdigit():
        ai = int(ao_raw.strip())
        if 1 <= ai <= 9:
            adults = ai
    if from_s:
        lines.append(f"Travelers: {who} · departing from {from_s}")
    else:
        lines.append(f"Travelers: {who}")
    if when_s:
        lines.append(f"Trip window (profile): {when_s}")
    if start_d and end_d:
        lines.append(f"Search dates: {start_d.isoformat()} → {end_d.isoformat()} ({(end_d - start_d).days + 1} days)")
    if date_guess_reason == "vague" and when_s:
        lines.append(
            f"Dates for search are our best guess from “{when_s}” (you can narrow this later)."
        )
    elif date_guess_reason == "default":
        lines.append(
            "No specific trip window — using a placeholder ~3 weeks out so search URLs still work."
        )
    if shifted_past_window:
        lines.append("Profile window was already in the past — search dates moved to the next occurrence.")

    itinerary_rows = [x for x in itinerary if isinstance(x, dict)] if isinstance(itinerary, list) else []
    trip_nights = (
        (end_d - start_d).days + 1
        if start_d is not None and end_d is not None
        else max(1, len(itinerary_rows) or n_days)
    )
    start_iso = start_d.isoformat() if start_d else ""
    end_iso = end_d.isoformat() if end_d else ""
    hotels_resolved, outline_src = resolve_booking_hotels_plan(
        dest,
        profile_pills,
        itinerary_rows,
        trip_nights,
        start_iso,
        end_iso,
        session_id,
    )
    if session_id and hotels_resolved:
        set_booking_hotels_plan(session_id, str(dest.get("id") or ""), hotels_resolved)
    if outline_src.startswith("advisor"):
        lines.append("Lodging: stays split from your day-by-day plan (advisor outline).")
    elif outline_src == "json_outline":
        lines.append("Lodging: using booking_search_outline from the destination card.")

    flights = dest.get("flights") or []
    route_hint = ""
    if isinstance(flights, list) and flights:
        f0 = flights[0] if isinstance(flights[0], dict) else {}
        route_hint = _strip_route_date_noise(str(f0.get("route") or "").strip())
    if route_hint:
        lines.append(f"Flight search follows itinerary route: {route_hint}")
    else:
        rt = start_d and end_d and end_d > start_d
        lines.append(
            f"Flight search: {'round trip (outbound + return on last trip day)' if rt else 'one-way'} "
            f"toward {dest_label} (Expedia catalog template)."
        )
    if start_d:
        lines.append(f"Outbound search date: {start_d.isoformat()}")
    if start_d and end_d and end_d > start_d:
        lines.append(f"Return search date: {end_d.isoformat()}")

    if hotels_resolved:
        lines.append("Lodging: one Expedia Hotel-Search per planned stay (area + stay dates).")
        cursor = start_d
        for i, h in enumerate(hotels_resolved):
            if not isinstance(h, dict):
                continue
            area = str(h.get("area") or name)
            nights = max(1, int(h.get("nights") or 1))
            if cursor is not None:
                checkout = cursor + timedelta(days=nights)
                lines.append(
                    f"  Stay {i + 1}: {area} · {nights} night(s) · {cursor.isoformat()} → {checkout.isoformat()}"
                )
                cursor = checkout
            else:
                lines.append(f"  Stay {i + 1}: {area} · {nights} night(s)")
    else:
        lines.append("Lodging: no stays on card — hotel steps will be skipped.")

    search_intent: dict[str, Any] = {}
    did = str(dest.get("id") or "").strip()
    sid = (session_id or "").strip()
    if sid and did:
        cached_intent = get_booking_search_intent(sid, did)
        if isinstance(cached_intent, dict):
            search_intent = cached_intent
            booking_search_trace(
                "booking_search_intent",
                source="session_cache",
                destination_id=did,
                has_flight=bool(search_intent.get("flight")),
                has_hotel=bool(search_intent.get("hotel")),
            )
        else:
            generated = run_booking_search_params_llm(
                dest=dest,
                profile_pills=profile_pills,
                itinerary_rows=itinerary_rows,
                criteria_lines=lines,
                start_iso=start_iso,
                end_iso=end_iso,
                trip_nights=trip_nights,
                session_id=sid,
            )
            if isinstance(generated, dict) and generated:
                search_intent = generated
                set_booking_search_intent(sid, did, search_intent)
                booking_search_trace(
                    "booking_search_intent",
                    source="advisor_llm",
                    destination_id=did,
                    has_flight=bool(search_intent.get("flight")),
                    has_hotel=bool(search_intent.get("hotel")),
                )

    lines.append("Source: illustrative Expedia web snapshot (not a live quote or availability guarantee).")

    return {
        "criteria_lines": lines,
        "adults": adults,
        "start_date": start_d.isoformat() if start_d else None,
        "end_date": end_d.isoformat() if end_d else None,
        "route_hint": route_hint,
        "hotel_count": len(hotels_resolved),
        "hotels_for_session": hotels_resolved,
        "booking_outline_source": outline_src,
        "search_intent": search_intent if search_intent else None,
    }


# Dropped from booking Hotel-Search URLs (golden catalog may still contain them).
_BOOKING_HOTEL_QUERY_DROP_KEYS = frozenset(
    {
        "regionId",
        "typeaheadCollationId",
        "semdtl",
    }
)


def _strip_booking_hotel_junk_params(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(k, v) for k, v in pairs if k not in _BOOKING_HOTEL_QUERY_DROP_KEYS]


def _catalog_path(env_key: str, default_relative: str) -> Path | None:
    raw = (os.environ.get(env_key) or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = _REPO_ROOT / raw
        if p.is_file():
            return p
    d = _EXPEDIA_CLI / default_relative
    if d.is_file():
        return d
    return None


def _build_hotel_url(
    destination_label: str,
    check_in: date,
    check_out: date,
    adults: int,
    *,
    rooms: int = 1,
    search_intent: dict[str, Any] | None = None,
) -> str:
    _ensure_cli_path()
    from expedia_listing_cli.filter_catalog import load_catalog_json, merge_catalog_query
    from expedia_listing_cli.url_builder import build_hotel_search_url

    cat_path = _catalog_path("EXPEDIA_HOTEL_SEARCH_CATALOG_JSON", "expedia_hotel_search_catalog.json")
    if cat_path and cat_path.is_file():
        catalog = load_catalog_json(cat_path)
        use_rw = os.environ.get("EXPEDIA_USE_REWARDS", "").strip().lower() in ("1", "true", "yes")
        pairs = merge_catalog_query(
            catalog,
            destination=destination_label,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            rooms=rooms,
            region_id=None,
            flexibility=os.environ.get("EXPEDIA_FLEXIBILITY", "0_DAY"),
            sort=os.environ.get("EXPEDIA_SORT", "RECOMMENDED"),
            typeahead_collation_id=None,
            category_search=os.environ.get("EXPEDIA_CATEGORY_SEARCH", "any_option"),
            use_rewards=use_rw,
        )
        pairs = _strip_booking_hotel_junk_params(pairs)
        pairs = _apply_hotel_search_intent_pairs(pairs, search_intent)
        root = (
            os.environ.get("EXPEDIA_HOTEL_SEARCH_BASE") or catalog.base_url or "https://www.expedia.com"
        ).rstrip("/")
        path = catalog.path if catalog.path.startswith("/") else f"/{catalog.path}"
        full = f"{root}{path}?{urlencode(pairs)}"
        booking_search_trace(
            "hotel_url_built",
            url=full,
            destination_query=destination_label.strip(),
            source="catalog",
            intent_hotel=bool(isinstance(search_intent, dict) and isinstance(search_intent.get("hotel"), dict)),
        )
        return full

    fallback = build_hotel_search_url(
        destination_label,
        check_in,
        check_out,
        adults=adults,
        rooms=rooms,
        region_id=None,
        typeahead_collation_id=None,
        catalog_path=None,
    )
    booking_search_trace(
        "hotel_url_built",
        url=fallback,
        destination_query=destination_label.strip(),
        source="url_builder_fallback",
    )
    return fallback


def _profile_pill_value(profile_pills: list[dict[str, Any]], key: str) -> str | None:
    for p in profile_pills:
        if not isinstance(p, dict):
            continue
        if str(p.get("key") or "") != key:
            continue
        v = str(p.get("value") or "").strip()
        return v or None
    return None


def _hotel_search_destination_label(dest: dict[str, Any], hotel_area: str) -> str:
    """
    Expedia ``destination=`` typeahead string: always anchor on the **card** city/region
    (``name`` + ``country``). Optionally prefix a neighborhood when it adds detail and is
    not redundant (avoids ``Condesa, Mexico`` without ``Mexico City``).
    """
    name = str(dest.get("name") or "").strip()
    country = str(dest.get("country") or "").strip()
    area = (hotel_area or "").strip()
    core = f"{name}, {country}".strip(", ").strip()

    junk = frozenset(
        {
            "",
            "city",
            "downtown",
            "centre",
            "center",
            "n/a",
            "tbd",
            "—",
            "-",
            "hotel zone",
            "unknown",
        }
    )
    al = area.lower()
    nl = name.lower()
    if area and al not in junk and nl and al != nl and not al.startswith(nl) and not nl.startswith(al):
        return f"{area}, {core}" if core else area
    return core if core else (area or country or name)


def _strip_route_date_noise(text: str) -> str:
    """
    Remove trailing month/day fragments from route labels before using them as
    Expedia typeahead locations (e.g. ``"Bali, Indonesia · May 29"``).
    """
    s = " ".join(str(text or "").split())
    if not s:
        return ""
    month_pat = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
    s = re.sub(rf"\s*(?:[·\-|,]\s*){month_pat}\s+\d{{1,2}}(?:,\s*\d{{2,4}})?\s*$", "", s, flags=re.I)
    s = re.sub(r"\s*(?:[·\-|,]\s*)\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*$", "", s, flags=re.I)
    s = re.sub(r"\s*(?:[·\-|,]\s*)\d{4}-\d{2}-\d{2}\s*$", "", s, flags=re.I)
    return s.strip(" ,·-|")


def _normalize_flight_location_label(text: str) -> str:
    """
    Normalize free-form destination labels into Expedia-friendly typeahead text.

    Examples:
    - ``"Ambergris Caye + Cayo District, Belize · Jul 15"`` -> ``"Ambergris Caye, Cayo District, Belize"``
    - ``"BZE to Seattle"`` (non-destination noise) stays detectable by caller for fallback.
    """
    s = _strip_route_date_noise(text)
    if not s:
        return ""
    s = re.sub(r"\s*\+\s*", ", ", s)
    s = re.sub(r"\s*/\s*", ", ", s)
    s = re.sub(r"\s*,\s*", ", ", s)
    s = re.sub(r"(,\s*){2,}", ", ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ,")


def _is_broad_area_phrase(text: str) -> bool:
    t = str(text or "").strip().lower()
    if not t:
        return False
    return any(
        k in t
        for k in (
            "valley",
            "district",
            "region",
            "coast",
            "interior",
            "circuit",
            "zone",
            "favorites",
            "shore",
            "county",
            "area",
        )
    )


def _pick_flight_arrival_fragment(raw_tail: str, country_hint: str) -> str:
    """
    From multi-stop labels (e.g. ``A + B, Country``), pick one arrival place that is
    most likely flight-searchable on Expedia.
    """
    s = _strip_route_date_noise(raw_tail)
    if not s:
        return ""
    country = str(country_hint or "").strip()
    left = s
    if country and re.search(rf",\s*{re.escape(country)}\s*$", s, flags=re.I):
        left = re.sub(rf",\s*{re.escape(country)}\s*$", "", s, flags=re.I).strip(" ,")
    pieces = [p.strip(" ,") for p in re.split(r"\s*(?:\+|/|&)\s*", left) if p.strip(" ,")]
    if not pieces:
        base = _normalize_flight_location_label(s)
        return base
    best = pieces[-1]
    best_score = -999
    for i, p in enumerate(pieces):
        score = 0
        if _is_broad_area_phrase(p):
            score -= 3
        if re.search(r"\([A-Z]{3}\)", p):
            score += 3
        if len(p.split()) <= 3:
            score += 1
        # Prefer later fragments when scores tie (often city after broad region).
        score += i
        if score >= best_score:
            best_score = score
            best = p
    out = best
    if country and not re.search(rf"\b{re.escape(country)}\b", out, flags=re.I):
        out = f"{out}, {country}"
    return _normalize_flight_location_label(out)


def _update_flight_filters_json(filters_raw: str, intent_flight: dict[str, Any]) -> str:
    try:
        arr = json.loads(filters_raw)
        if not isinstance(arr, list):
            return filters_raw
    except Exception:
        return filters_raw
    out: list[dict[str, Any]] = []
    for x in arr:
        if not isinstance(x, dict):
            continue
        keys = set(x.keys())
        if "numOfStopFilterValue" in keys:
            continue
        if "preferredCabinFilterValue" in keys:
            continue
        if "carryOnBagBasedFilterValue" in keys:
            continue
        out.append(x)

    stops = str(intent_flight.get("stops") or "").strip().lower()
    if stops == "nonstop":
        out.append({"numOfStopFilterValue": {"stopInfo": {"numberOfStops": 0, "stopFilterOperation": "EQUAL"}}})

    cabin = str(intent_flight.get("cabin") or "").strip().lower()
    cabin_code = {
        "economy": "BASIC_COACH",
        "premium_economy": "PREMIUM_COACH",
        "business": "BUSINESS",
        "first": "FIRST",
    }.get(cabin)
    if cabin_code:
        out.append({"preferredCabinFilterValue": {"cabinCode": cabin_code}})

    carry_on = str(intent_flight.get("carry_on") or "").strip().lower()
    if carry_on == "required":
        out.append({"carryOnBagBasedFilterValue": {"carryOnBag": "CARRY_ON_BAG"}})

    return json.dumps(out, separators=(",", ":"), ensure_ascii=False)


def _apply_flight_search_intent_pairs(
    pairs: list[tuple[str, str]], search_intent: dict[str, Any] | None
) -> list[tuple[str, str]]:
    intent_f = search_intent.get("flight") if isinstance(search_intent, dict) else None
    if not isinstance(intent_f, dict):
        return pairs
    out: list[tuple[str, str]] = []
    for k, v in pairs:
        if k == "filters":
            out.append((k, _update_flight_filters_json(v, intent_f)))
        else:
            out.append((k, v))
    return out


def _apply_hotel_search_intent_pairs(
    pairs: list[tuple[str, str]], search_intent: dict[str, Any] | None
) -> list[tuple[str, str]]:
    intent_h = search_intent.get("hotel") if isinstance(search_intent, dict) else None
    if not isinstance(intent_h, dict):
        return pairs

    def _drop(keys: set[str], src: list[tuple[str, str]]) -> list[tuple[str, str]]:
        return [(k, v) for (k, v) in src if k not in keys]

    out = list(pairs)
    sort = str(intent_h.get("sort") or "").strip().upper()
    if sort:
        out = _drop({"sort"}, out)
        out.append(("sort", sort))

    tr = str(intent_h.get("traveler_type") or "").strip().lower()
    if tr:
        out = _drop({"travelerType"}, out)
        out.append(("travelerType", tr))

    meal = str(intent_h.get("meal_plan") or "").strip().upper()
    if meal:
        out = _drop({"mealPlan"}, out)
        out.append(("mealPlan", meal))

    am = intent_h.get("amenities")
    if isinstance(am, list) and am:
        out = _drop({"amenities"}, out)
        for a in am[:4]:
            aa = str(a).strip().upper()
            if aa:
                out.append(("amenities", aa))

    gr = intent_h.get("guest_rating_min")
    if isinstance(gr, (int, float)):
        out = _drop({"guestRating"}, out)
        out.append(("guestRating", str(max(0, min(50, int(round(float(gr) * 10)))))))

    star_min = intent_h.get("star_min")
    if isinstance(star_min, (int, float)):
        out = _drop({"star"}, out)
        sm = int(star_min)
        if sm >= 5:
            star_vals = ["50"]
        elif sm >= 4:
            star_vals = ["40", "50"]
        elif sm >= 3:
            star_vals = ["30", "40", "50"]
        else:
            star_vals = []
        for sv in star_vals:
            out.append(("star", sv))

    pmax = intent_h.get("price_max_usd")
    pmin = intent_h.get("price_min_usd")
    if isinstance(pmax, (int, float)) and int(pmax) > 0:
        mx = int(pmax)
        mn = int(pmin) if isinstance(pmin, (int, float)) and int(pmin) >= 0 else max(0, int(mx * 0.66))
        if mn > mx:
            mn = max(0, mx - 100)
        out = _drop({"price"}, out)
        out.append(("price", str(mn)))
        out.append(("price", str(mx)))

    return out


def _flight_arrival_location_label(dest: dict[str, Any], route_hint: str) -> str:
    """
    Build a typeahead-style ``to:`` label for leg1. Prefer ``Route → Arrival`` from the card;
    else ``City, Country``.
    """
    name = str(dest.get("name") or "").strip()
    country = str(dest.get("country") or "").strip()
    canonical = _normalize_flight_location_label(f"{name}, {country}".strip(", "))
    rh = (route_hint or "").strip()
    for sep in ("\u2192", "→", "->"):
        if sep in rh:
            tail = rh.split(sep, 1)[-1].strip()
            tail = re.sub(r"\s+(direct|nonstop|one[- ]?way)\s*$", "", tail, flags=re.I)
            tail = _pick_flight_arrival_fragment(tail, country)
            if tail:
                # Reject obvious route-noise tails (e.g., "BZE to Seattle"), fallback to canonical destination.
                if re.search(r"\bto\b", tail, flags=re.I) and canonical:
                    return canonical
                return tail
    if canonical:
        return canonical
    return _normalize_flight_location_label(name or country or "")


def _apply_flight_leg1_overrides(
    leg1: str,
    *,
    depart: date,
    from_label: str | None,
    to_label: str | None,
) -> str:
    """Rewrite catalog leg1: optional ``from`` / ``to`` (Expedia comma-separated segments) + departure date."""
    s = leg1
    if from_label and from_label.strip():
        s = re.sub(
            r"from:(.+?),to:",
            lambda _m: f"from:{from_label.strip()},to:",
            s,
            count=1,
        )
    if to_label and to_label.strip():
        s = re.sub(
            r"to:(.+?),departure:",
            lambda _m: f"to:{to_label.strip()},departure:",
            s,
            count=1,
        )
    dstr = f"{depart.month}/{depart.day}/{depart.year}TANYT"
    s = re.sub(r"departure:[^,]+", f"departure:{dstr}", s, count=1)
    return s


def _build_flight_search_url(
    *,
    depart: date,
    return_d: date | None,
    adults: int,
    from_label: str | None = None,
    to_label: str | None = None,
    flight_leg: str = "outbound",
    search_intent: dict[str, Any] | None = None,
) -> tuple[str | None, bool]:
    """
    Build Flights-Search URL from catalog.

    ``flight_leg``:
    - ``outbound`` — home → destination on ``depart`` (one-way by default, or round-trip when
      ``BOOKING_SEARCH_FLIGHT_FORCE_ONEWAY`` is off and the catalog has ``leg2``).
    - ``return`` — destination → home on ``return_d`` (always one-way ``leg1`` only; no ``leg2``).

    By default (``BOOKING_SEARCH_FLIGHT_FORCE_ONEWAY``), outbound builds **one-way** URLs so one HTML
    snapshot matches outbound cards. A **separate** ``return`` call shows the return-leg search.
    """
    leg = (flight_leg or "outbound").strip().lower()
    if leg not in ("outbound", "return"):
        leg = "outbound"

    cat_path = _catalog_path("EXPEDIA_FLIGHT_SEARCH_CATALOG_JSON", "expedia_flight_search_catalog.json")
    if not cat_path or not cat_path.is_file():
        return None, False
    _ensure_cli_path()
    from expedia_listing_cli.filter_catalog import load_catalog_json

    catalog = load_catalog_json(cat_path)
    pair_keys = {k for k, _ in catalog.query_param_pairs}
    is_return_leg = leg == "return"
    if is_return_leg:
        if not return_d or return_d <= depart:
            return None, False
        use_round = False
    elif _flight_force_oneway_url():
        use_round = False
    else:
        use_round = bool(return_d and return_d > depart and "leg2" in pair_keys)
    new_pairs: list[tuple[str, str]] = []
    for k, v in catalog.query_param_pairs:
        if k == "leg2" and not use_round:
            continue
        if k == "leg1":
            leg1_raw = v
            if is_return_leg:
                assert return_d is not None
                leg1_new = _apply_flight_leg1_overrides(
                    leg1_raw,
                    depart=return_d,
                    from_label=to_label,
                    to_label=from_label,
                )
                booking_search_trace(
                    "flight_leg1_rewrite_return",
                    leg1_catalog_sample=leg1_raw[:220],
                    leg1_after_rewrite=leg1_new[:400],
                    from_label_dest=to_label,
                    to_label_home=from_label,
                    depart_iso=return_d.isoformat(),
                )
            else:
                leg1_new = _apply_flight_leg1_overrides(
                    leg1_raw,
                    depart=depart,
                    from_label=from_label,
                    to_label=to_label,
                )
                booking_search_trace(
                    "flight_leg1_rewrite",
                    leg1_catalog_sample=leg1_raw[:220],
                    leg1_after_rewrite=leg1_new[:400],
                    from_label=from_label,
                    to_label=to_label,
                    depart_iso=depart.isoformat(),
                )
            new_pairs.append((k, leg1_new))
        elif k == "leg2" and use_round:
            assert return_d is not None
            leg2_new = _apply_flight_leg2_overrides(
                v,
                return_d=return_d,
                from_label_home=from_label,
                to_label_dest=to_label,
            )
            booking_search_trace(
                "flight_leg2_rewrite",
                leg2_after_rewrite=leg2_new[:400],
                return_iso=return_d.isoformat(),
            )
            new_pairs.append((k, leg2_new))
        elif k == "passengers":
            new_pairs.append((k, f"adults:{adults},children:0,infantinlap:N"))
        elif k == "trip":
            new_pairs.append((k, "roundtrip" if use_round else "oneway"))
        else:
            new_pairs.append((k, v))
    new_pairs = _apply_flight_search_intent_pairs(new_pairs, search_intent)
    root = catalog.base_url.rstrip("/")
    path = catalog.path if catalog.path.startswith("/") else f"/{catalog.path}"
    full = f"{root}{path}?{urlencode(new_pairs)}"
    mode = "roundtrip" if use_round else "oneway"
    booking_search_trace(
        "flight_url_built",
        url=full,
        trip_mode=mode,
        adults=adults,
        use_round_trip=use_round,
        flight_force_oneway=_flight_force_oneway_url(),
        flight_leg=leg,
        intent_flight=bool(isinstance(search_intent, dict) and isinstance(search_intent.get("flight"), dict)),
    )
    return full, use_round


def _fetch_html(url: str) -> str:
    _ensure_cli_path()
    from expedia_listing_cli.browser_fetch import fetch_page_html
    from expedia_listing_cli.extract_heuristic import extract_candidate_links, extract_price_like_strings

    headless = os.environ.get("HEADLESS", "1").strip().lower() not in ("0", "false", "no")
    booking_search_trace("html_fetch_start", url=url, headless=headless)
    t0 = time.perf_counter()
    html = fetch_page_html(url, headless=headless)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    head = " ".join(html[:4000].split())[:1500]
    booking_search_trace(
        "html_fetch_done",
        url=url,
        html_len=len(html),
        fetch_elapsed_ms=elapsed_ms,
        html_preview=head,
    )
    retry_enabled = os.environ.get("BOOKING_SEARCH_RETRY_WEAK_FETCH", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    if retry_enabled:
        weak = False
        reason = ""
        if "/Flights-Search?" in url:
            p0 = extract_price_like_strings(html, limit=12)
            non_zero = [x for x in p0 if _price_digits(x) > 0]
            weak = len(non_zero) == 0
            reason = f"flight_non_zero_prices={len(non_zero)}"
        elif "/Hotel-Search?" in url:
            links0 = extract_candidate_links(html, limit=8)
            weak = len(links0) == 0
            reason = f"hotel_links={len(links0)}"
        if weak:
            booking_search_trace("html_fetch_retry_start", url=url, retry_reason=reason)
            t1 = time.perf_counter()
            html2 = fetch_page_html(url, headless=headless)
            elapsed2_ms = int((time.perf_counter() - t1) * 1000)
            head2 = " ".join(html2[:4000].split())[:1500]
            booking_search_trace(
                "html_fetch_retry_done",
                url=url,
                html_len=len(html2),
                fetch_elapsed_ms=elapsed2_ms,
                html_preview=head2,
            )
            # Keep second snapshot when it looks richer than the first.
            if "/Flights-Search?" in url:
                p2 = extract_price_like_strings(html2, limit=12)
                non_zero2 = [x for x in p2 if _price_digits(x) > 0]
                if len(non_zero2) > 0 or len(html2) > len(html):
                    booking_search_trace(
                        "html_fetch_retry_take_second",
                        url=url,
                        first_html_len=len(html),
                        second_html_len=len(html2),
                        first_non_zero_prices=len(non_zero),
                        second_non_zero_prices=len(non_zero2),
                    )
                    html = html2
            elif "/Hotel-Search?" in url:
                links2 = extract_candidate_links(html2, limit=8)
                if len(links2) > 0 or len(html2) > len(html):
                    booking_search_trace(
                        "html_fetch_retry_take_second",
                        url=url,
                        first_html_len=len(html),
                        second_html_len=len(html2),
                        first_links=0,
                        second_links=len(links2),
                    )
                    html = html2
    return html


def _price_digits(s: str | None) -> int:
    if not s:
        return 0
    digits = re.sub(r"[^\d]", "", str(s))
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def _normalize_price_display(s: str) -> str:
    """Strip trailing punctuation noise from scraped dollar tokens (e.g. ``$400,``)."""
    t = str(s or "").strip()
    return re.sub(r"[\s,]+$", "", t)


def _absolute_expedia_href(href: str) -> str:
    h = (href or "").strip()
    if not h:
        return ""
    if h.startswith("http"):
        return h
    if h.startswith("//"):
        return f"https:{h}"
    return f"https://www.expedia.com{h if h.startswith('/') else '/' + h}"


def _flight_card_labels_from_page(
    raw_title: str,
    *,
    route_fallback: str,
    to_label: str | None,
) -> tuple[str, str, str]:
    """
    Expedia flight SRP ``<title>`` is often ``SEA to PDX flights`` — use that for **route**, not the card ``route_hint`` (often ``TBD``).
    Returns ``(airline_line, route_line, route_source)``.
    """
    t = re.sub(r"\s*\|\s*Expedia.*$", "", (raw_title or ""), flags=re.I).strip()
    m = re.match(r"^(.+?)\s+to\s+(.+?)\s+flights\s*$", t, flags=re.I)
    rf = (route_fallback or "").strip()
    junk = frozenset(("", "TBD", "—", "tbd", "-"))
    if m:
        a, b = m.group(1).strip(), m.group(2).strip()
        return "Sample fare (Expedia)", f"{a} → {b}", "page_title"
    if rf and rf not in junk:
        headline = t[:120] if (4 <= len(t) <= 160 and "flights" not in t.lower()) else "Flight options (Expedia)"
        return headline, rf, "criteria_card"
    dest = (to_label or "").strip()
    route_line = f"To {dest}" if dest else "Outbound"
    headline = t[:120] if len(t) >= 4 else "Flight options (Expedia)"
    return headline, route_line, "destination_fallback"


def _hotel_area_display(area: str, *, dest_name: str, dest_country: str) -> str:
    """Replace itinerary placeholders like ``City`` with the destination card label."""
    a = (area or "").strip()
    low = a.lower()
    junk = frozenset(
        {"", "city", "downtown", "centre", "center", "n/a", "tbd", "—", "-", "hotel zone", "unknown", "stay"}
    )
    if low in junk:
        core = f"{(dest_name or '').strip()}, {(dest_country or '').strip()}".strip(", ").strip()
        return core if core else (dest_name or "Destination")
    return a


def _apply_flight_leg2_overrides(
    leg2: str,
    *,
    return_d: date,
    from_label_home: str | None,
    to_label_dest: str | None,
) -> str:
    """Return leg: **from** destination area, **to** home airport, on ``return_d``."""
    s = leg2
    if to_label_dest and to_label_dest.strip():
        s = re.sub(
            r"from:(.+?),to:",
            lambda _m: f"from:{to_label_dest.strip()},to:",
            s,
            count=1,
        )
    if from_label_home and from_label_home.strip():
        s = re.sub(
            r"to:(.+?),departure:",
            lambda _m: f"to:{from_label_home.strip()},departure:",
            s,
            count=1,
        )
    dstr = f"{return_d.month}/{return_d.day}/{return_d.year}TANYT"
    s = re.sub(r"departure:[^,]+", f"departure:{dstr}", s, count=1)
    return s


def _hotel_option_from_snapshot(
    html: str,
    *,
    area: str,
    nights: int,
    image_fallback: str,
    dest_name: str = "",
    dest_country: str = "",
    exclude_price_values: set[int] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _ensure_cli_path()
    from expedia_listing_cli.extract_heuristic import (
        extract_candidate_links,
        extract_hotel_srp_image_url,
        extract_og_image_url,
        extract_price_like_strings,
    )

    def _image_near_listing_href(raw_html: str, listing_href: str) -> str:
        """
        Try to recover a relevant SRP thumbnail by scanning around the chosen listing link.
        """
        href_raw = (listing_href or "").strip()
        if not href_raw:
            return ""
        candidates = [href_raw]
        href_abs = _absolute_expedia_href(href_raw)
        if href_abs:
            candidates.append(href_abs)
        # Relative fragment is often what appears in HTML attributes.
        mrel = re.search(r"https?://[^/]+(/[^?#]+)", href_raw)
        if mrel:
            candidates.append(mrel.group(1))
        if href_abs:
            mrel2 = re.search(r"https?://[^/]+(/[^?#]+)", href_abs)
            if mrel2:
                candidates.append(mrel2.group(1))
        # De-dupe while preserving order.
        seen: set[str] = set()
        needles = [x for x in candidates if x and not (x in seen or seen.add(x))]
        for nd in needles:
            ix = raw_html.find(nd)
            if ix < 0:
                continue
            lo = max(0, ix - 6000)
            hi = min(len(raw_html), ix + 12000)
            chunk = raw_html[lo:hi]
            # Prefer travel-assets hosted imagery near this listing.
            m = re.search(
                r"https?://[^\"'\s>]*travel-assets[^\"'\s>]*\.(?:jpe?g|png|webp)",
                chunk,
                flags=re.I,
            )
            if m:
                return m.group(0)
            # Fallback to any image-like URL in this local chunk.
            m2 = re.search(r"https?://[^\"'\s>]+\.(?:jpe?g|png|webp)", chunk, flags=re.I)
            if m2:
                return m2.group(0)
        return ""

    links = extract_candidate_links(html, limit=8)
    prices = extract_price_like_strings(html)
    excludes = exclude_price_values or set()
    non_zero = [x for x in prices if _price_digits(x) > 0]
    preferred = [x for x in non_zero if _price_digits(x) >= 50 and _price_digits(x) not in excludes]
    if preferred:
        price_display = preferred[0]
        price_src = "non_tiny_non_excluded"
    elif non_zero:
        price_display = non_zero[0]
        price_src = "non_zero"
    elif prices:
        price_display = prices[0]
        price_src = "any_token"
    else:
        price_display = "See Expedia"
        price_src = "fallback"

    price_display = _normalize_price_display(price_display)

    title = "Lodging results"
    href = ""
    name_src = "fallback"
    link_src = "none"
    if links:
        raw_title = (links[0].get("text") or title).strip()
        raw_title = re.sub(r"^\s*Opens\s+", "", raw_title, flags=re.I)
        raw_title = re.sub(r"\s+in\s+new\s+tab\s*$", "", raw_title, flags=re.I)
        title = raw_title[:120] or title
        href = (links[0].get("href") or "").strip()
        if href:
            link_src = "first_candidate_link"
        if title and title != "Lodging results" and title != "Hotel listing":
            name_src = "listing_link_text"

    og = (extract_og_image_url(html) or "").strip()
    srp_img = (extract_hotel_srp_image_url(html) or "").strip()
    near_href_img = _image_near_listing_href(html, href)
    if og:
        image_used = og
        image_src = "og_image"
    elif srp_img:
        image_used = srp_img
        image_src = "srp_html_thumb"
    elif near_href_img:
        image_used = near_href_img
        image_src = "near_listing_href"
    elif (image_fallback or "").strip():
        image_used = image_fallback.strip()
        image_src = "itinerary_card"
    else:
        image_used = ""
        image_src = "none"

    area_display = _hotel_area_display(area, dest_name=dest_name, dest_country=dest_country)
    area_src = "destination_card" if area_display != (area or "").strip() else "destination_hotel_row"

    href_abs = _absolute_expedia_href(href)

    booking_search_trace(
        "hotel_parse_heuristic",
        link_count=len(links),
        price_candidates=prices[:12],
        non_zero_candidates=non_zero[:12],
        excluded_price_values=sorted(excludes),
        chosen_title=title,
        chosen_price=price_display,
        price_source=price_src,
        name_source=name_src,
        listing_url_source=link_src,
        image_source=image_src,
        listing_href_preview=(href_abs[:240] if href_abs else ""),
        og_image_head=_short(og, 120),
        srp_image_head=_short(srp_img, 120),
        near_href_image_head=_short(near_href_img, 120),
    )
    booking_search_trace(
        "hotel_card_fields",
        name=title,
        area=area_display,
        nights=nights,
        price=price_display,
        has_listing_url=bool(href_abs),
        image_source=image_src,
    )

    option = {
        "name": title,
        "area": area_display,
        "price": price_display if price_display.startswith("$") else f"{price_display}/night est.",
        "nights": nights,
        "image": image_used,
        "best": True,
        "listing_url": href_abs,
    }
    profile: dict[str, Any] = {
        "name": {"source": name_src},
        "area": {"source": area_src},
        "nights": {"source": "destination_hotel_row"},
        "price": {"source": price_src},
        "image": {"source": image_src},
        "listing_url": {"source": link_src},
    }
    return option, profile


def _flight_option_from_snapshot(
    html: str,
    *,
    route_fallback: str,
    to_label: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _ensure_cli_path()
    from expedia_listing_cli.extract_heuristic import (
        extract_document_title,
        extract_flight_airline_filter_hints,
        extract_flight_duration_guess,
        extract_price_like_strings,
    )

    prices = extract_price_like_strings(html)
    non_zero = [x for x in prices if _price_digits(x) > 0]
    non_tiny = [x for x in non_zero if _price_digits(x) >= 50]
    if non_tiny:
        price_display = non_tiny[0]
        price_src = "non_tiny"
    elif non_zero:
        price_display = non_zero[0]
        price_src = "non_zero"
    elif prices:
        price_display = prices[0]
        price_src = "any_token"
    else:
        price_display = "See Expedia"
        price_src = "fallback"

    price_display = _normalize_price_display(price_display)

    raw_title = extract_document_title(html) or ""
    airline_line, route_line, route_src = _flight_card_labels_from_page(
        raw_title,
        route_fallback=route_fallback,
        to_label=to_label,
    )
    airline_src = "page_title_split"
    airline_code = ""
    hints = extract_flight_airline_filter_hints(html, limit=24)
    if hints:

        def _row_price_val(s: str) -> int:
            d = re.sub(r"[^\d]", "", str(s))
            return int(d) if d else 9_999_999

        h_best = min(hints, key=lambda r: _row_price_val(r.get("min_price") or ""))
        airline_line = (h_best.get("airline") or airline_line)[:120]
        airline_src = f"expedia_{h_best.get('source', 'listing_hint')}"
        airline_code = (h_best.get("iata") or "").strip().upper()[:3]

    dur = extract_flight_duration_guess(html)
    duration_display = dur if dur else "—"
    dur_src = "visible_text" if dur else "none"

    booking_search_trace(
        "flight_parse_heuristic",
        price_candidates=prices[:12],
        non_zero_candidates=non_zero[:12],
        chosen_price=price_display,
        price_source=price_src,
        route_fallback=route_fallback,
        route_resolved=_short(route_line, 120),
        airline_source=airline_src,
        page_title_head=_short(raw_title, 120),
        duration_guess=duration_display,
        duration_source=dur_src,
    )
    booking_search_trace(
        "flight_card_fields",
        airline_head=_short(airline_line, 120),
        airline_code=airline_code or None,
        route=route_line,
        duration=duration_display,
        price=price_display,
    )

    option: dict[str, Any] = {
        "airline": airline_line[:120],
        "route": route_line[:160],
        "duration": duration_display,
        "price": price_display if price_display.startswith("$") else str(price_display),
        "best": True,
    }
    if airline_code:
        option["airline_code"] = airline_code
    profile: dict[str, Any] = {
        "airline": {"source": airline_src},
        "route": {"source": route_src},
        "duration": {"source": dur_src},
        "price": {"source": price_src},
    }
    if airline_code:
        profile["airline_code"] = {"source": airline_src, "iata": airline_code}
    return option, profile


def run_booking_search_step(
    dest: dict[str, Any],
    *,
    step: str,
    hotel_index: int | None,
    profile_pills: list[dict[str, Any]],
    date_hints: dict[str, Any] | None = None,
    session_id: str | None = None,
    flight_leg: str | None = None,
) -> dict[str, Any]:
    """
    ``step``: ``flight`` | ``hotel``.
    ``hotel_index``: required when step is ``hotel`` (0-based).
    ``flight_leg``: for ``step=flight``, ``outbound`` (default) or ``return`` (second one-way search,
    destination → home on the trip end date).
    """
    if not booking_search_enabled():
        return {"ok": False, "error": "Booking search disabled (BOOKING_SEARCH_ENABLED=0)."}

    dest_id = str(dest.get("id") or "")
    booking_search_trace("step_begin", destination_id=dest_id, step=step, hotel_index=hotel_index)

    meta = build_booking_criteria(dest, profile_pills, date_hints=date_hints, session_id=session_id)
    start_s = meta.get("start_date")
    end_s = meta.get("end_date")
    if not start_s:
        booking_search_trace(
            "step_skip_no_dates",
            destination_id=dest_id,
            step=step,
            error="no_start_date",
        )
        return {"ok": False, "error": "Could not resolve trip dates — set profile When or pass date_hints."}
    start_d = date.fromisoformat(start_s)
    end_d = date.fromisoformat(end_s) if end_s else start_d + timedelta(days=6)
    adults = int(meta.get("adults") or 2)

    name = str(dest.get("name") or "Stay")
    country = str(dest.get("country") or "")
    route_hint = str(meta.get("route_hint") or "")
    search_intent = meta.get("search_intent") if isinstance(meta.get("search_intent"), dict) else None
    if not isinstance(search_intent, dict):
        search_intent = get_booking_search_intent(session_id or "", dest_id)
    fl_leg = (flight_leg or "outbound").strip().lower()
    if fl_leg not in ("outbound", "return"):
        fl_leg = "outbound"

    try:
        if step == "flight":
            from_l = _profile_pill_value(profile_pills, "from")
            to_l = _flight_arrival_location_label(dest, route_hint)
            if not (to_l or "").strip():
                to_l = None
            booking_search_trace(
                "flight_labels",
                destination_id=dest_id,
                from_profile=from_l,
                to_resolved=to_l,
                route_hint_card=route_hint or "",
                adults=adults,
                flight_leg=fl_leg,
            )
            return_home = end_d if end_d > start_d else None
            if fl_leg == "return" and (not return_home or return_home <= start_d):
                booking_search_trace(
                    "step_skip_return_no_window",
                    destination_id=dest_id,
                    error="no_multi_day_window",
                )
                return {
                    "ok": False,
                    "error": "Return flight search needs a trip end date after the start date.",
                    "step": step,
                    "flight_leg": fl_leg,
                }
            route_llm = route_hint or f"To {name}"
            if fl_leg == "return":
                home_bit = (from_l or "").strip() or "Home"
                dest_bit = (to_l or "").strip() or name
                route_llm = f"{dest_bit} → {home_bit}"
            url, round_trip = _build_flight_search_url(
                depart=start_d,
                return_d=return_home,
                adults=adults,
                from_label=from_l,
                to_label=to_l,
                flight_leg=fl_leg,
                search_intent=search_intent,
            )
            if not url:
                booking_search_trace("step_fail", destination_id=dest_id, step=step, error="flight_catalog_missing")
                return {
                    "ok": False,
                    "error": "Flight catalog missing. Add expedia-listing-cli/expedia_flight_search_catalog.json "
                    "or set EXPEDIA_FLIGHT_SEARCH_CATALOG_JSON.",
                    "step": step,
                }
            html = _fetch_html(url)
            fo: dict[str, Any]
            flight_profile: dict[str, Any]
            llm_meta: dict[str, Any] | None = None
            from src.wanderly.flight_listing_llm import flight_llm_parse_enabled, run_flight_listing_llm_parse

            if flight_llm_parse_enabled():
                crit_lines = meta.get("criteria_lines") if isinstance(meta.get("criteria_lines"), list) else []
                fo_llm, prof_llm, llm_meta = run_flight_listing_llm_parse(
                    html,
                    search_url=url,
                    route_hint=route_llm,
                    to_label=to_l if fl_leg == "outbound" else from_l,
                    profile_pills=profile_pills,
                    criteria_lines=[str(x) for x in crit_lines if str(x).strip()],
                    session_id=session_id,
                )
                if fo_llm and prof_llm:
                    fo, flight_profile = fo_llm, prof_llm
                    booking_search_trace(
                        "flight_parse_llm_ok",
                        destination_id=dest_id,
                        excerpt_chars=llm_meta.get("excerpt_chars"),
                        listings=len(llm_meta.get("listings") or []),
                        best_index=llm_meta.get("best_index"),
                        advisor_rank=bool(llm_meta.get("advisor_rank")),
                    )
                else:
                    fo, flight_profile = _flight_option_from_snapshot(
                        html,
                        route_fallback=route_llm,
                        to_label=to_l if fl_leg == "outbound" else from_l,
                    )
                    booking_search_trace(
                        "flight_parse_llm_fallback",
                        destination_id=dest_id,
                        llm_err=_short(str((llm_meta or {}).get("llm_err") or ""), 240),
                    )
            else:
                fo, flight_profile = _flight_option_from_snapshot(
                    html,
                    route_fallback=route_llm,
                    to_label=to_l if fl_leg == "outbound" else from_l,
                )
            # Same URL we fetched (catalog-built search). Flight SERPs rarely expose stable per-fare hrefs in static HTML,
            # unlike hotel Hotel-Information links — so we attach the search URL explicitly rather than parsing offers.
            fo["search_url"] = url
            fo["round_trip"] = round_trip
            fo["flight_leg"] = fl_leg
            flight_profile["search_url"] = {"source": "catalog_flights_search_url"}
            out_flight: dict[str, Any] = {
                "ok": True,
                "step": step,
                "flight": fo,
                "search_url": url,
                "round_trip": round_trip,
                "flight_leg": fl_leg,
            }
            if booking_search_api_debug_enabled():
                dbg: dict[str, Any] = {
                    "step": "flight",
                    "field_sources": flight_profile,
                    "values_preview": {k: _short(str(v), 100) for k, v in fo.items() if k != "best"},
                }
                if llm_meta:
                    dbg["flight_llm_meta"] = {
                        k: v
                        for k, v in llm_meta.items()
                        if k != "listings" and not k.startswith("_")
                    }
                out_flight["debug_parse"] = dbg
            booking_search_trace(
                "step_ok",
                destination_id=dest_id,
                step=step,
                search_url=url,
                result_flight=fo,
                parse_field_sources=flight_profile,
            )
            return out_flight

        if step == "hotel":
            if hotel_index is None:
                return {"ok": False, "error": "hotel_index required for hotel step."}
            hotels_plan = meta.get("hotels_for_session")
            if not isinstance(hotels_plan, list) or not hotels_plan:
                hotels_plan = get_booking_hotels_plan(session_id or "", dest_id) or []
            if not isinstance(hotels_plan, list) or hotel_index >= len(hotels_plan):
                return {"ok": False, "error": "Invalid hotel_index for this destination."}
            h = hotels_plan[hotel_index]
            if not isinstance(h, dict):
                return {"ok": False, "error": "Invalid hotel entry."}
            area = str(h.get("area") or name)
            nights = max(1, int(h.get("nights") or 1))
            image_fallback = str(h.get("image") or "")
            # Stagger stays across trip
            check_in = start_d
            for i in range(hotel_index):
                hi = hotels_plan[i]
                if isinstance(hi, dict):
                    check_in = check_in + timedelta(days=max(1, int(hi.get("nights") or 1)))
            check_out = check_in + timedelta(days=nights)
            dest_label = _hotel_search_destination_label(dest, area)
            booking_search_trace(
                "hotel_search_labels",
                destination_id=dest_id,
                hotel_index=hotel_index,
                hotel_area_raw=area,
                card_name=name,
                card_country=country,
                destination_query=dest_label,
                check_in=check_in.isoformat(),
                check_out=check_out.isoformat(),
            )
            url = _build_hotel_url(dest_label, check_in, check_out, adults, search_intent=search_intent)
            q_prices = parse_qs(urlparse(url).query).get("price", [])
            exclude_vals = {_price_digits(v) for v in q_prices if _price_digits(v) > 0}
            html = _fetch_html(url)
            ho: dict[str, Any]
            hotel_profile: dict[str, Any]
            h_llm_meta: dict[str, Any] | None = None
            from src.wanderly.hotel_listing_llm import hotel_llm_parse_enabled, run_hotel_listing_llm_parse

            crit_lines = meta.get("criteria_lines") if isinstance(meta.get("criteria_lines"), list) else []
            if hotel_llm_parse_enabled():
                ho_llm, prof_llm, h_llm_meta = run_hotel_listing_llm_parse(
                    html,
                    search_url=url,
                    dest_label=dest_label,
                    area_hint=area,
                    nights=nights,
                    profile_pills=profile_pills,
                    criteria_lines=[str(x) for x in crit_lines if str(x).strip()],
                    session_id=session_id,
                    image_fallback=image_fallback,
                )
                if ho_llm and prof_llm:
                    ho, hotel_profile = ho_llm, prof_llm
                    booking_search_trace(
                        "hotel_parse_llm_ok",
                        destination_id=dest_id,
                        hotel_index=hotel_index,
                        excerpt_chars=(h_llm_meta or {}).get("excerpt_chars"),
                        listings=len((h_llm_meta or {}).get("listings") or []),
                        best_index=(h_llm_meta or {}).get("best_index"),
                        advisor_rank=bool((h_llm_meta or {}).get("advisor_rank")),
                    )
                else:
                    ho, hotel_profile = _hotel_option_from_snapshot(
                        html,
                        area=area,
                        nights=nights,
                        image_fallback=image_fallback,
                        dest_name=name,
                        dest_country=country,
                        exclude_price_values=exclude_vals,
                    )
                    booking_search_trace(
                        "hotel_parse_llm_fallback",
                        destination_id=dest_id,
                        hotel_index=hotel_index,
                        llm_err=_short(str((h_llm_meta or {}).get("llm_err") or ""), 240),
                    )
            else:
                ho, hotel_profile = _hotel_option_from_snapshot(
                    html,
                    area=area,
                    nights=nights,
                    image_fallback=image_fallback,
                    dest_name=name,
                    dest_country=country,
                    exclude_price_values=exclude_vals,
                )
            out_hotel: dict[str, Any] = {
                "ok": True,
                "step": step,
                "hotel_index": hotel_index,
                "hotel": ho,
                "search_url": url,
            }
            if booking_search_api_debug_enabled():
                dbg_h: dict[str, Any] = {
                    "step": "hotel",
                    "hotel_index": hotel_index,
                    "field_sources": hotel_profile,
                    "values_preview": {k: _short(str(v), 100) for k, v in ho.items() if k != "best"},
                }
                if h_llm_meta:
                    dbg_h["hotel_llm_meta"] = {
                        k: v
                        for k, v in h_llm_meta.items()
                        if k != "listings" and not str(k).startswith("_")
                    }
                out_hotel["debug_parse"] = dbg_h
            booking_search_trace(
                "step_ok",
                destination_id=dest_id,
                step=step,
                hotel_index=hotel_index,
                search_url=url,
                result_hotel=ho,
                parse_field_sources=hotel_profile,
            )
            return out_hotel

        return {"ok": False, "error": f"Unknown step {step!r}"}
    except Exception as exc:
        booking_search_trace(
            "step_exception",
            destination_id=dest_id,
            step=step,
            hotel_index=hotel_index,
            error=str(exc)[:800],
        )
        return {"ok": False, "error": str(exc)[:800], "step": step}
