"""Tests for hotel listing LLM post-parse price field normalization."""

from __future__ import annotations

from src.wanderly.hotel_listing_llm import _coerce_hotel_row_price_display_nightly


def test_coerce_moves_stay_total_out_of_price_display() -> None:
    row = {"name": "X", "price_display": "$2,215 total", "price_notes": None}
    out = _coerce_hotel_row_price_display_nightly(row)
    assert out["price_display"] == ""
    assert "2,215" in (out.get("price_notes") or "")


def test_coerce_preserves_plain_nightly() -> None:
    row = {"name": "X", "price_display": "$447 nightly", "price_notes": None}
    out = _coerce_hotel_row_price_display_nightly(row)
    assert out["price_display"] == "$447 nightly"


def test_coerce_merges_existing_price_notes() -> None:
    row = {"price_display": "$99 total with taxes and fees", "price_notes": "Pool"}
    out = _coerce_hotel_row_price_display_nightly(row)
    assert out["price_display"] == ""
    assert "Pool" in (out.get("price_notes") or "")
    assert "99" in (out.get("price_notes") or "")
