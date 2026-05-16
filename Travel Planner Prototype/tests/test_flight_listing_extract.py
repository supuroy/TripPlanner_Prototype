"""Heuristic flight SRP row extraction from saved HTML snippets."""

from __future__ import annotations

import sys
from pathlib import Path

_CLI = Path(__file__).resolve().parents[2] / "expedia-listing-cli"
if str(_CLI) not in sys.path:
    sys.path.insert(0, str(_CLI))

from src.wanderly.flight_listing_extract import extract_flight_listing_rows_from_html  # noqa: E402


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "flight_srp_snippet_hints.html"


def test_extract_flight_listing_rows_from_fixture() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    rows = extract_flight_listing_rows_from_html(html, limit=12)
    assert len(rows) >= 4
    airlines = {r["airline"] for r in rows}
    assert "Alaska Airlines" in airlines
    assert "Delta" in airlines or any("Delta" in a for a in airlines)
    prices = [r["price_display"] for r in rows if "$" in str(r.get("price_display") or "")]
    assert any("368" in p or "389" in p or "412" in p for p in prices)


def test_snippet_row_has_departure_arrival() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    rows = extract_flight_listing_rows_from_html(html, limit=12)
    timed = [r for r in rows if r.get("departure_time") and r.get("arrival_time")]
    assert len(timed) >= 2
    assert "→" in (timed[0].get("route") or "")
