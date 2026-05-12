"""Tests for profile budget → nightly hotel search intent overlay."""

from __future__ import annotations

import pytest

from src.wanderly.ota_booking_search import (
    _merge_profile_budget_into_hotel_search_intent,
    _nightly_hotel_price_min_max_usd,
    _parse_trip_budget_usd_bounds_from_profile_pills,
)


def test_parse_range_k_total() -> None:
    pills = [{"key": "budget", "value": "~$2-3k total"}]
    lo, hi = _parse_trip_budget_usd_bounds_from_profile_pills(pills, adults=2)
    assert lo == 2000 and hi == 3000


def test_parse_range_per_person() -> None:
    pills = [{"key": "budget", "value": "~$2-3k total pp"}]
    lo, hi = _parse_trip_budget_usd_bounds_from_profile_pills(pills, adults=2)
    assert lo == 4000 and hi == 6000


def test_nightly_bounds_seven_nights() -> None:
    lo, hi = _nightly_hotel_price_min_max_usd(2000, 3000, calendar_nights=7)
    assert lo is not None and hi is not None
    assert lo < hi <= 8000
    # ~3000 * 0.56 / 7 ≈ 240 max order of magnitude
    assert hi <= 450


def test_merge_caps_loose_llm_max() -> None:
    pills = [{"key": "budget", "value": "~$2-3k total"}]
    intent = {"hotel": {"price_min_usd": 50, "price_max_usd": 900}}
    out = _merge_profile_budget_into_hotel_search_intent(
        intent, pills, calendar_nights=7, adults=2
    )
    h = out.get("hotel") or {}
    assert h.get("price_max_usd", 9999) < 500
    assert h.get("price_min_usd", 0) < h.get("price_max_usd", 9999)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
