"""Sanitization of Expedia flight ``to:`` / ``from:`` typeahead labels (LLM junk suffixes)."""

from __future__ import annotations

from src.wanderly.ota_booking_search import (
    _flight_to_label_for_booking,
    _normalize_dest_booking_search_hints,
    _normalize_flight_location_label,
)


def test_strip_based_suffix_from_flight_hint() -> None:
    dest = {
        "id": "d1",
        "name": "San Juan",
        "country": "Puerto Rico",
        "booking_search_hints": {"flight_to_label": "Seattle-based"},
    }
    assert _flight_to_label_for_booking(dest, "") == "Seattle"


def test_strip_based_before_comma_in_hint() -> None:
    dest = {
        "id": "d2",
        "booking_search_hints": {"flight_to_label": "Seattle-based, United States"},
    }
    hints = _normalize_dest_booking_search_hints(dest)
    assert hints is not None
    assert hints.get("flight_to_label") == "Seattle, United States"


def test_normalize_flight_location_label_strips_suffix() -> None:
    assert _normalize_flight_location_label("Seattle-based · Mar 3") == "Seattle"
