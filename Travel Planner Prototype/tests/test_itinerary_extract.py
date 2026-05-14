"""Regression tests for lazy itinerary JSON extraction / normalization."""

from __future__ import annotations

import pytest

from src.wanderly.itinerary_lazy import _extract_itinerary_payload, _normalize_itinerary_rows


def test_extract_preamble_before_object() -> None:
    raw = 'Sure — here you go.\n\n{"itinerary": [{"day": 1, "label": "Arrive", "items": ["a", "b"]}], "budget_guidance": {}}'
    rows, bg, bh, ncf, _tp = _extract_itinerary_payload(raw)
    assert len(rows) == 1
    assert bg is None  # empty budget object normalizes to None
    assert bh is None
    assert ncf is None


def test_extract_booking_search_hints_optional() -> None:
    raw = (
        '{"itinerary": [{"day": 1, "label": "Arrive", "items": ["a", "b"]}], '
        '"budget_guidance": {}, '
        '"booking_search_hints": {"flight_to_label": "Florence, Italy", "flight_return_from_label": "Florence, Italy"}}'
    )
    rows, bg, bh, ncf, _tp = _extract_itinerary_payload(raw)
    assert len(rows) == 1
    assert bh is not None
    assert bh.get("flight_to_label") == "Florence, Italy"
    assert ncf is None


def test_extract_needs_commercial_flight() -> None:
    raw = (
        '{"itinerary": [{"day": 1, "label": "Drive", "items": ["Car"]}], "budget_guidance": {}, '
        '"needs_commercial_flight": false}'
    )
    rows, _bg, _bh, ncf, _tp = _extract_itinerary_payload(raw)
    assert len(rows) == 1
    assert ncf is False


def test_extract_pretty_printed_wrapped_itinerary() -> None:
    raw = """{   "itinerary": [
     { "day": 1, "label": "Day one", "items": ["x", "y"] }
   ], "budget_guidance": {}
}"""
    rows, _bg, _bh, _ncf, _tp = _extract_itinerary_payload(raw)
    assert len(rows) == 1


def test_extract_transport_plan_ground_only() -> None:
    raw = (
        '{"itinerary": [{"day": 1, "label": "Drive", "items": ["Car"]}], "budget_guidance": {}, '
        '"transport_plan": {"primary_mode": "ground_only", "summary": "Road trip", '
        '"commercial_flight_segments": []}}'
    )
    rows, _bg, _bh, _ncf, tp = _extract_itinerary_payload(raw)
    assert len(rows) == 1
    assert tp is not None
    assert tp.get("primary_mode") == "ground_only"
    assert tp.get("commercial_flight_segments") == []


def test_normalize_day_transport_optional() -> None:
    raw = [
        {
            "day": 1,
            "label": "Ferry day",
            "items": ["a", "b"],
            "day_transport": {"mode": "ferry", "note": "BC Ferries"},
        }
    ]
    out = _normalize_itinerary_rows(raw, max_days=3)
    assert len(out) == 1
    assert out[0].get("day_transport", {}).get("mode") == "ferry"


def test_normalize_activities_capital_a() -> None:
    raw = [{"day": 1, "label": "L", "Activities": ["one", "two"]}]
    out = _normalize_itinerary_rows(raw, max_days=3)
    assert len(out) == 1
    assert len(out[0]["items"]) >= 2


def test_normalize_fallback_items_from_label() -> None:
    raw = [{"day": 1, "label": "Solo explore day", "items": []}]
    out = _normalize_itinerary_rows(raw, max_days=3)
    assert len(out) == 1
    assert "Solo explore" in out[0]["items"][0] or out[0]["items"][0].startswith("Solo")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
