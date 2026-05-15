"""Tests for structured booking hooks (lodging_moves, commercial_flight_segments)."""

from __future__ import annotations

from src.wanderly.booking_hooks import (
    commercial_flight_segments_for_booking,
    lodging_moves_for_booking,
    parse_day_range_nights,
)
from src.wanderly.ota_booking_search import resolve_booking_hotels_plan


def test_parse_day_range_nights() -> None:
    assert parse_day_range_nights("1-3") == 3
    assert parse_day_range_nights("4") == 1
    assert parse_day_range_nights("") == 1


def test_resolve_hotels_from_lodging_moves() -> None:
    dest = {
        "id": "x",
        "name": "Tuscany",
        "country": "Italy",
        "transport_plan": {
            "primary_mode": "mixed",
            "summary": "Fly in, two bases",
            "commercial_flight_segments": [
                {
                    "role": "inbound",
                    "from_search_label": "Seattle, WA",
                    "to_search_label": "Florence, Italy",
                }
            ],
            "lodging_moves": [
                {"day_range": "1-4", "area": "Florence, Italy"},
                {"day_range": "5-7", "area": "Siena, Italy"},
            ],
        },
    }
    rows, src = resolve_booking_hotels_plan(
        dest,
        [],
        [{"day": 1, "label": "Florence", "items": ["a"]}],
        trip_nights=7,
        start_iso="2026-09-01",
        end_iso="2026-09-07",
        session_id=None,
    )
    assert src == "transport_lodging_moves"
    assert len(rows) == 2
    assert rows[0]["area"] == "Florence, Italy"
    assert rows[1]["area"] == "Siena, Italy"
    assert sum(int(r["nights"]) for r in rows) == 7


def test_commercial_flight_segments_for_booking() -> None:
    dest = {
        "booking_hooks": {
            "version": 1,
            "transport": {
                "primary_mode": "mixed",
                "commercial_flight_segments": [
                    {
                        "role": "inbound",
                        "from_search_label": "",
                        "to_search_label": "Florence, Italy",
                        "day_hint": 0,
                    },
                    {
                        "role": "return",
                        "from_search_label": "Florence, Italy",
                        "to_search_label": "Seattle, WA",
                        "day_hint": 6,
                    },
                ],
            },
        }
    }
    pills = [{"key": "from", "value": "Seattle, WA"}]
    segs = commercial_flight_segments_for_booking(dest, pills)
    assert len(segs) == 2
    assert segs[0]["from_search_label"] == "Seattle, WA"
    assert segs[0]["to_search_label"] == "Florence, Italy"
    assert segs[1]["role"] == "return"
    assert segs[1]["day_offset"] == 6


def test_lodging_moves_from_booking_hooks() -> None:
    dest = {
        "booking_hooks": {
            "version": 1,
            "transport": {
                "primary_mode": "commercial_air",
                "lodging_moves": [{"day_range": "1-3", "area": "Paris, France"}],
            },
        }
    }
    moves = lodging_moves_for_booking(dest)
    assert len(moves) == 1
    assert moves[0]["area"] == "Paris, France"
