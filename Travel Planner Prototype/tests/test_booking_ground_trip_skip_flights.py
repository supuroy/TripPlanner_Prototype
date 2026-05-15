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


def test_no_flight_phrase_does_not_enable_commercial_air() -> None:
    """``\\bflight\\b`` must not match the noun inside ``no flight`` / ``without flights``."""
    dest = {
        "id": "tofino",
        "name": "Tofino",
        "country": "Canada",
        "hook": "Pacific Rim road trip",
        "itinerary": [
            {
                "day": 1,
                "label": "Getting there",
                "items": ["No flight — drive from Seattle", "Ferry or highway option"],
            },
        ],
        "flights": [],
    }
    pills = [{"key": "from", "value": "Seattle (drive from home)"}]
    assert booking_should_include_flight_search(dest, pills) is False


def test_needs_commercial_flight_string_false_skips() -> None:
    dest = {"needs_commercial_flight": "false", "itinerary": [], "flights": [{"airline": "Delta", "route": "SEA-YYJ"}]}
    assert booking_should_include_flight_search(dest, []) is False


def test_drive_route_skeleton_row_skips_flights() -> None:
    dest = {
        "id": "tofino-bc",
        "name": "Tofino",
        "country": "Canada",
        "hook": "Drive the island",
        "itinerary": [],
        "flights": [
            {
                "airline": "",
                "route": "Seattle (drive from home) — Tofino, Canada",
                "duration": "TBD",
                "price": "TBD",
                "best": True,
            },
        ],
    }
    assert booking_should_include_flight_search(dest, []) is False


def test_transport_plan_ground_only_wins_over_needs_true() -> None:
    """Structured ``transport_plan`` is authoritative vs the compact duplicate flag."""
    dest = {
        "needs_commercial_flight": True,
        "transport_plan": {
            "primary_mode": "ground_only",
            "summary": "Drive + ferry only",
            "commercial_flight_segments": [],
        },
        "itinerary": [{"day": 1, "label": "Arrive", "items": ["Flight wording in prose but no tickets"]}],
    }
    assert booking_should_include_flight_search(dest, []) is False


def test_transport_plan_commercial_air_skips_heuristic_parse() -> None:
    dest = {
        "transport_plan": {
            "primary_mode": "commercial_air",
            "summary": "Fly in",
            "commercial_flight_segments": [],
        },
        "hook": "ambiguous",
        "itinerary": [],
        "flights": [],
    }
    assert booking_should_include_flight_search(dest, []) is True


def test_transport_plan_mixed_empty_segments_falls_through() -> None:
    """``mixed`` without listed segments is ambiguous — fall back to heuristics."""
    dest = {
        "transport_plan": {"primary_mode": "mixed", "summary": "?", "commercial_flight_segments": []},
        "itinerary": [
            {
                "day": 1,
                "label": "Arrival",
                "items": ["Flight to YYJ", "Pick up rental car"],
            },
        ],
        "flights": [],
    }
    assert booking_should_include_flight_search(dest, []) is True


def test_booking_prepare_fingerprint_includes_flight_gate() -> None:
    """Prepare cache key must change when ``transport_plan`` / flags change without itinerary row edits."""
    from src.wanderly.ota_booking_search import booking_prepare_request_fingerprint

    dest = {
        "id": "t",
        "name": "Tofino",
        "country": "Canada",
        "itinerary": [{"day": 1, "label": "Drive", "base": "Tofino", "items": ["a", "b"]}],
    }
    fp_before = booking_prepare_request_fingerprint(dest, [], None)
    dest2 = {
        **dest,
        "needs_commercial_flight": False,
        "transport_plan": {
            "primary_mode": "ground_only",
            "summary": "Road trip",
            "commercial_flight_segments": [],
        },
    }
    fp_after = booking_prepare_request_fingerprint(dest2, [], None)
    assert fp_before != fp_after
