"""
Deterministic flight SRP row scoring: price band (or relative price), stops, duration.

Used for rule-pick winners and top-3 alternatives (heuristic + LLM paths).
"""

from __future__ import annotations

import os
import re
from typing import Any

from src.wanderly.flight_listing_extract import parse_duration_minutes, stops_count
from src.wanderly.hotel_listing_score import _extract_usd_ranges, parse_price_display_usd


def flight_rule_pick_enabled() -> bool:
    raw = (os.environ.get("BOOKING_SEARCH_FLIGHT_RULE_PICK") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def flight_band_from_budget_guidance(bg: dict[str, Any] | None) -> tuple[int, int] | None:
    """Parse per-traveler USD band from ``flight_band_short`` / ``flight_style``."""
    if not isinstance(bg, dict):
        return None
    for key in ("flight_band_short", "flight_style", "trip_ballpark_usd_pp"):
        raw = bg.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        pairs = _extract_usd_ranges(raw)
        cand = [(lo, hi) for lo, hi in pairs if 50 <= lo <= hi <= 25_000]
        if not cand:
            continue
        cand.sort(key=lambda p: (p[1] - p[0], p[0]))
        lo, hi = cand[0]
        if hi - lo < 25:
            hi = lo + 50
        return int(lo), int(hi)
    return None


def _stops_bonus(stops: str) -> float:
    n = stops_count(stops)
    if n is None:
        return 0.0
    if n == 0:
        return 14.0
    if n == 1:
        return 5.0
    if n == 2:
        return -10.0
    return -14.0


def _duration_bonus(duration: str) -> float:
    mins = parse_duration_minutes(duration)
    if mins is None:
        return 0.0
    if mins <= 180:
        return 14.0
    if mins <= 360:
        return 14.0 - (mins - 180) / 180.0 * 6.0
    if mins <= 720:
        return 8.0 - (mins - 360) / 360.0 * 6.0
    if mins <= 1080:
        return 2.0 - (mins - 720) / 360.0 * 2.0
    return -2.0


def _times_bonus(row: dict[str, Any]) -> float:
    dep = str(row.get("departure_time") or "").strip()
    arr = str(row.get("arrival_time") or "").strip()
    if dep and arr:
        return 3.0
    if dep or arr:
        return 1.0
    return 0.0


def _price_band_component(p: int | None, *, price_lo: int, price_hi: int) -> float:
    if p is None:
        return 8.0
    mid = 0.5 * (price_lo + price_hi)
    width = max(1.0, float(price_hi - price_lo))
    if p < price_lo:
        gap = float(price_lo - p)
        return 55.0 - min(45.0, (gap / max(1.0, float(price_lo))) * 50.0)
    if p > price_hi:
        gap = float(p - price_hi)
        return max(5.0, 48.0 - (gap / max(1.0, float(price_hi))) * 55.0)
    dist = abs(float(p) - mid)
    return 92.0 - min(40.0, (dist / (width * 0.5)) * 38.0)


def _relative_price_component(p: int, *, min_p: int, max_p: int) -> float:
    """Cheaper fares score higher, but capped so stops/duration can outweigh small savings."""
    span = max(1, max_p - min_p)
    return 35.0 * (1.0 - (float(p - min_p) / float(span)))


def score_flight_listing_row(
    row: dict[str, Any],
    *,
    price_lo: int | None = None,
    price_hi: int | None = None,
    route_hint: str = "",
    relative_prices: tuple[int, int] | None = None,
) -> tuple[float, int | None]:
    """Higher is better. Second value is parsed USD (or None)."""
    p = parse_price_display_usd(str(row.get("price_display") or row.get("price") or ""))

    band_ok = (
        price_lo is not None
        and price_hi is not None
        and price_hi >= price_lo > 0
    )
    if band_ok:
        base = _price_band_component(p, price_lo=int(price_lo), price_hi=int(price_hi))
    elif p is not None and relative_prices is not None:
        mn, mx = relative_prices
        base = _relative_price_component(p, min_p=mn, max_p=mx)
    elif p is not None:
        base = 40.0
    else:
        base = 5.0

    base += _stops_bonus(str(row.get("stops") or ""))
    base += _duration_bonus(str(row.get("duration") or ""))
    base += _times_bonus(row)

    rh = (route_hint or "").lower()
    rt = str(row.get("route") or "").lower()
    if rh and rt and any(w for w in re.split(r"[^\w]+", rh) if len(w) >= 3 and w in rt):
        base += 2.0
    return float(base), p


def pick_flight_listing_index_rule_based(
    listings: list[dict[str, Any]],
    *,
    price_lo: int,
    price_hi: int,
    route_hint: str = "",
) -> tuple[int | None, dict[str, Any]]:
    """Pick best row when a USD band is known."""
    return _pick_flight_listing_index_impl(
        listings,
        price_lo=price_lo,
        price_hi=price_hi,
        route_hint=route_hint,
    )


def pick_flight_listing_index(
    listings: list[dict[str, Any]],
    *,
    price_lo: int | None = None,
    price_hi: int | None = None,
    route_hint: str = "",
) -> tuple[int | None, dict[str, Any]]:
    """
    Pick winner using price band when valid; otherwise relative price + stops + duration.
    """
    band_ok = price_lo is not None and price_hi is not None and price_hi >= price_lo > 0
    return _pick_flight_listing_index_impl(
        listings,
        price_lo=price_lo if band_ok else None,
        price_hi=price_hi if band_ok else None,
        route_hint=route_hint,
    )


def _pick_flight_listing_index_impl(
    listings: list[dict[str, Any]],
    *,
    price_lo: int | None,
    price_hi: int | None,
    route_hint: str = "",
) -> tuple[int | None, dict[str, Any]]:
    dbg: dict[str, Any] = {
        "price_lo": price_lo,
        "price_hi": price_hi,
        "pick_mode": "band" if price_lo and price_hi else "relative",
        "rows": [],
    }
    if not listings:
        return None, {**dbg, "reason": "empty_listings"}

    band_ok = price_lo is not None and price_hi is not None and price_hi >= price_lo > 0
    priced: list[int] = []
    for row in listings:
        if not isinstance(row, dict):
            continue
        p = parse_price_display_usd(str(row.get("price_display") or row.get("price") or ""))
        if p is not None:
            priced.append(p)
    rel = (min(priced), max(priced)) if priced else None

    scored: list[tuple[int, float, int | None, dict[str, Any]]] = []
    for i, row in enumerate(listings):
        if not isinstance(row, dict):
            continue
        sc, usd = score_flight_listing_row(
            row,
            price_lo=price_lo if band_ok else None,
            price_hi=price_hi if band_ok else None,
            route_hint=route_hint,
            relative_prices=rel if not band_ok else None,
        )
        scored.append((i, sc, usd, row))

    if not scored:
        return None, {**dbg, "reason": "no_rows"}

    rows_out: list[dict[str, Any]] = []
    for i, sc, usd, row in scored:
        rows_out.append(
            {
                "i": i,
                "score": round(sc, 2),
                "usd": usd if usd is not None else "—",
                "airline": str(row.get("airline") or "")[:80],
                "price_display": str(row.get("price_display") or "")[:24],
                "stops": str(row.get("stops") or "")[:24],
                "duration": str(row.get("duration") or "")[:16],
                "route": str(row.get("route") or "")[:60],
            }
        )
    rows_out.sort(
        key=lambda r: (
            -float(r.get("score") or 0),
            int(r.get("usd")) if str(r.get("usd")).isdigit() else 999_999,
            int(r.get("i") if r.get("i") is not None else 999),
        )
    )
    dbg["rows"] = rows_out

    if band_ok:
        priced_rows = [t for t in scored if t[2] is not None]
        if not priced_rows:
            pool = scored
        else:
            pool = priced_rows
    else:
        pool = scored

    best_i, best_sc, _, best_row = max(pool, key=lambda t: (t[1], -(t[2] or 999_999)))
    dbg["winner_index"] = best_i
    dbg["winner_score"] = round(best_sc, 2)
    dbg["winner_airline"] = str(best_row.get("airline") or "")[:80]
    dbg["winner_stops"] = str(best_row.get("stops") or "")[:24]
    dbg["winner_duration"] = str(best_row.get("duration") or "")[:24]
    return best_i, dbg
