"""Tests for skipping commercial flight booking search on ground / road trips."""

from __future__ import annotations

from src.wanderly.ota_booking_search import booking_should_include_flight_search


def test_needs_commercial_flight_false_overrides_text() -> None:
    dest = {
        "needs_commercial_flight": False,
        "itinerary": [{"day": 1, "label": "Fly home day", "items": ["Airport drop-off"]}],
        "flights": [],
    }
    assert booking_should_include_flight_search(dest, []) is False


def test_needs_commercial_flight_true_overrides_ground_text() -> None:
    dest = {
        "needs_commercial_flight": True,
        "hook": "road trip",
        "itinerary": [],
        "flights": [],
    }
    assert booking_should_include_flight_search(dest, []) is True


def test_road_trip_no_flying_skips_flights() -> None:
    dest = {
        "id": "tofino",
        "name": "Tofino",
        "country": "Canada",
        "hook": "Pacific Rim road trip — no flying",
        "itinerary": [
            {
                "day": 1,
                "label": "Drive from Vancouver",
                "items": ["Rental car pickup", "Highway 4 toward the coast"],
            },
        ],
        "flights": [],
    }
    pills = [{"key": "from", "value": "Vancouver, BC (drive)"}]
    assert booking_should_include_flight_search(dest, pills) is False


def test_explicit_flight_in_itinerary_keeps_flights() -> None:
    dest = {
        "id": "mixed",
        "name": "Tofino",
        "country": "Canada",
        "hook": "Fly-drive combo",
        "itinerary": [
            {
                "day": 1,
                "label": "Arrival",
                "items": ["Flight to YYJ", "Pick up rental car", "Drive to Tofino"],
            },
        ],
        "flights": [],
    }
    pills = [{"key": "from", "value": "Seattle, WA"}]
    assert booking_should_include_flight_search(dest, pills) is True


def test_non_placeholder_flight_row_keeps_flights() -> None:
    dest = {
        "id": "x",
        "name": "Oaxaca",
        "country": "Mexico",
        "hook": "Beach week",
        "itinerary": [],
        "flights": [
            {
                "airline": "Alaska",
                "route": "SEA → OAX",
                "duration": "7h",
                "price": "$412",
                "best": True,
            },
        ],
    }
    assert booking_should_include_flight_search(dest, []) is True


def test_placeholder_only_and_ambiguous_defaults_to_flights() -> None:
    dest = {
        "id": "y",
        "name": "Paris",
        "country": "France",
        "hook": "City break",
        "itinerary": [{"day": 1, "label": "Arrival", "items": ["Check-in"]}],
        "flights": [
            {
                "airline": "Placeholder",
                "route": "TBD",
                "duration": "TBD",
                "price": "TBD",
                "best": True,
            },
        ],
    }
    assert booking_should_include_flight_search(dest, []) is True


def test_mixed_ground_and_air_hints_keeps_flights() -> None:
    """Conservative: if both signals appear, still run flight search."""
    dest = {
        "id": "z",
        "name": "Island hop",
        "country": "Canada",
        "hook": "Road trip with one flight segment",
        "itinerary": [
            {
                "day": 2,
                "label": "Inter-island",
                "items": ["Short flight to next island", "Then rental car"],
            },
        ],
        "flights": [],
    }
    assert booking_should_include_flight_search(dest, []) is True
