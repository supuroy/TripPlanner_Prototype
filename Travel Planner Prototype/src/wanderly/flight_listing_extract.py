"""Normalize Expedia flight SRP heuristic hints into listing rows for scoring."""

from __future__ import annotations

import re
from typing import Any


def parse_duration_minutes(duration: str) -> int | None:
    """Parse ``2h 15m``, ``14h 30m``, ``2h+``, ``14h`` into total minutes."""
    s = (duration or "").strip().lower()
    if not s or s in ("—", "-", "n/a"):
        return None
    m = re.search(r"(\d+)\s*h(?:r)?\s*(\d+)\s*m(?:in)?", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"(\d+)\s*h(?:r)?\s*\+", s)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*h(?:r)?(?:\s|$)", s)
    if m and not re.search(r"\d+\s*m", s):
        return int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*m(?:in)?(?:\s|$)", s)
    if m:
        return int(m.group(1))
    return None


def stops_count(stops: str) -> int | None:
    """0 = nonstop, 1+ = stops, None if unknown."""
    s = (stops or "").strip().lower()
    if not s:
        return None
    if "nonstop" in s or "non-stop" in s or s == "direct":
        return 0
    m = re.search(r"(\d+)\s*stop", s)
    if m:
        return int(m.group(1))
    if re.search(r"\b1\s*stop\b", s):
        return 1
    return None


def _stops_label(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if re.search(r"\bnon\s*stop\b", s, re.I):
        return "Nonstop"
    m = re.search(r"(\d+)\s*stop", s, re.I)
    if m:
        n = int(m.group(1))
        return "Nonstop" if n == 0 else f"{n} stop" if n == 1 else f"{n} stops"
    return s[:40]


def hint_row_to_listing(hint: dict[str, str], *, index: int) -> dict[str, Any]:
    """Map one ``extract_flight_airline_filter_hints`` row to a listing dict."""
    airline = str(hint.get("airline") or "").strip()[:120]
    price = str(hint.get("min_price") or "").strip()[:32]
    dep = str(hint.get("departing") or "").strip()[:24]
    arr = str(hint.get("arriving") or "").strip()[:24]
    route = ""
    if dep and arr:
        route = f"{dep} → {arr}"[:160]
    row: dict[str, Any] = {
        "i": index,
        "airline": airline or "—",
        "price_display": price or "See Expedia",
        "departure_time": dep,
        "arrival_time": arr,
        "route": route,
        "duration": str(hint.get("duration") or "").strip()[:40],
        "stops": _stops_label(str(hint.get("stops") or "")),
        "source": str(hint.get("source") or "heuristic")[:40],
    }
    iata = str(hint.get("iata") or "").strip().upper()[:3]
    if iata:
        row["airline_code"] = iata
    return row


def enrich_flight_listing_row_from_html(row: dict[str, Any], html: str) -> dict[str, Any]:
    """Fill missing duration/stops on a listing row from page-level HTML."""
    out = dict(row)
    if not str(out.get("duration") or "").strip():
        try:
            from expedia_listing_cli.extract_heuristic import extract_flight_duration_guess

            dur = extract_flight_duration_guess(html)
            if dur:
                out["duration"] = dur[:40]
        except Exception:
            pass
    return out


def enrich_flight_listings_from_html(rows: list[dict[str, Any]], html: str) -> list[dict[str, Any]]:
    return [enrich_flight_listing_row_from_html(r, html) for r in rows if isinstance(r, dict)]


def extract_flight_listing_rows_from_html(html: str, *, limit: int = 24) -> list[dict[str, Any]]:
    """
    Best-effort structured rows from flight SRP HTML (checkbox filters + listing snippets).
    """
    from expedia_listing_cli.extract_heuristic import extract_flight_airline_filter_hints

    hints = extract_flight_airline_filter_hints(html, limit=limit)
    rows = [hint_row_to_listing(h, index=i) for i, h in enumerate(hints)]
    return enrich_flight_listings_from_html(rows, html)


def llm_listing_row_to_flight_option(row: dict[str, Any]) -> dict[str, Any]:
    """Map one parsed LLM listing row to a flight card-shaped dict."""
    price = str(row.get("price_display") or row.get("price") or "").strip() or "See Expedia"
    fo: dict[str, Any] = {
        "airline": str(row.get("airline") or "—")[:120],
        "route": str(row.get("route") or "—")[:180],
        "duration": str(row.get("duration") or "—")[:40],
        "price": price[:32],
        "best": True,
        "stops": str(row.get("stops") or "")[:80],
        "departure_time": str(row.get("departure_time") or "")[:24],
        "arrival_time": str(row.get("arrival_time") or "")[:24],
    }
    if row.get("pick_score") is not None:
        try:
            fo["pick_score"] = round(float(row["pick_score"]), 2)
        except (TypeError, ValueError):
            pass
    logo = str(row.get("airline_logo_url") or "").strip()
    if logo.startswith("http"):
        fo["airline_logo_url"] = logo[:800]
    return fo
