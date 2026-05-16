"""Tests for hotel SRP HTML excerpting before LLM parse."""

from __future__ import annotations

from pathlib import Path

from src.wanderly.hotel_listing_llm import compact_playwright_hotel_html

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hotel_srp_next_savoy_prices.html"
PORTO = Path(__file__).resolve().parents[1] / ".cache" / "porto_moniz_srp.html"


def test_compact_excerpt_includes_hotel_information_links() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    ex = compact_playwright_hotel_html(html, max_chars=90_000)
    assert "hotel-information" in ex.lower()
    assert "savoy" in ex.lower() or "Hotel-Information" in ex


def test_compact_porto_moniz_includes_listing_cards_not_only_filters() -> None:
    if not PORTO.is_file():
        return
    html = PORTO.read_text(encoding="utf-8", errors="replace")
    ex = compact_playwright_hotel_html(html, max_chars=90_000)
    low = ex.lower()
    assert low.count("hotel-information") >= 3
    assert "aqua natura" in low or "porto-moniz-hotels" in low
    assert low.count("sort-and-filter") <= 2
