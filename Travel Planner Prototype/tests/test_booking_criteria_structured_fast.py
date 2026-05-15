"""Structured itinerary fast path skips advisor LLMs in ``build_booking_criteria``."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from src.wanderly import ota_booking_search as obs


def test_structured_lodging_date_span_from_day_logistics() -> None:
    rows = [
        {
            "day": 1,
            "day_logistics": {
                "lodging": {"check_in": "2026-07-01", "check_out": "2026-07-04", "guests": {"adults": 2}},
            },
        },
        {"day": 2, "day_logistics": {"lodging": None}},
    ]
    span = obs._structured_lodging_date_span(rows)
    assert span is not None
    assert span == (date(2026, 7, 1), date(2026, 7, 3))


def test_build_booking_criteria_skips_params_llm_when_hooks_and_moves() -> None:
    dest = {
        "id": "dest-structured",
        "name": "Phuket",
        "country": "Thailand",
        "itinerary": [{"day": 1, "label": "Arrive", "base": "Phuket"}],
        "booking_hooks": {
            "version": 1,
            "transport": {
                "primary_mode": "commercial_air",
                "summary": "Fly in",
                "commercial_flight_segments": [
                    {"from_search_label": "Seattle, WA, United States", "to_search_label": "Phuket, Thailand", "role": "inbound"}
                ],
                "lodging_moves": [{"day_range": "1-5", "area": "Phuket, Thailand", "note": "beach base"}],
            },
            "search_preferences": {
                "flight": {"stops": "nonstop", "cabin": "economy", "carry_on": "optional"},
                "hotel": {"sort": "RECOMMENDED", "guest_rating_min": 4.0},
            },
        },
    }
    pills = [
        {"key": "who", "label": "Who", "value": "2 adults"},
        {"key": "when", "label": "When", "value": "July 2026"},
        {"key": "from", "label": "From", "value": "Seattle"},
    ]
    dh = {"start_iso": "2026-07-01", "end_iso": "2026-07-05", "adults": 2}

    calls: list[str] = []

    def _note(name: str):
        def _inner(*a: object, **k: object) -> object:
            calls.append(name)
            return None

        return _inner

    with (
        patch.object(obs, "run_booking_unified_bundle_llm", side_effect=_note("unified")),
        patch.object(obs, "run_booking_travel_window_llm", side_effect=_note("travel_window")),
        patch.object(obs, "run_booking_search_params_llm", side_effect=_note("params_llm")),
    ):
        out = obs.build_booking_criteria(
            dest,
            pills,
            date_hints=dh,
            session_id="sess-hooks-1",
            skip_parallel_prefetch=True,
        )

    assert "unified" not in calls
    assert "travel_window" not in calls
    assert "params_llm" not in calls
    assert out.get("start_date") == "2026-07-01"
    assert out.get("end_date") == "2026-07-05"
    intent = out.get("search_intent")
    assert isinstance(intent, dict)
    assert intent.get("flight", {}).get("stops") == "nonstop"
    assert intent.get("hotel", {}).get("sort") == "RECOMMENDED"


def test_build_booking_criteria_fast_path_from_lodging_iso_without_moves() -> None:
    dest = {
        "id": "dest-iso",
        "name": "Test Isle",
        "country": "Greece",
        "itinerary": [
            {
                "day": 1,
                "day_logistics": {
                    "lodging": {
                        "destination_search_label": "Naxos, Greece",
                        "check_in": "2026-08-10",
                        "check_out": "2026-08-14",
                        "guests": {"adults": 2},
                    },
                },
            }
        ],
        "booking_hooks": {
            "version": 1,
            "transport": {
                "primary_mode": "commercial_air",
                "summary": "Fly",
                "commercial_flight_segments": [
                    {"from_search_label": "Boston, MA, United States", "to_search_label": "Athens, Greece", "role": "inbound"}
                ],
                "lodging_moves": [],
            },
        },
    }
    pills = [{"key": "who", "label": "Who", "value": "2 adults"}]
    dh = {"start_iso": "2026-08-10", "end_iso": "2026-08-13", "adults": 2}

    calls: list[str] = []

    def _note(name: str):
        def _inner(*a: object, **k: object) -> object:
            calls.append(name)
            return None

        return _inner

    with (
        patch.object(obs, "run_booking_unified_bundle_llm", side_effect=_note("unified")),
        patch.object(obs, "run_booking_search_params_llm", side_effect=_note("params_llm")),
    ):
        out = obs.build_booking_criteria(
            dest,
            pills,
            date_hints=dh,
            session_id="sess-iso-1",
            skip_parallel_prefetch=True,
        )

    assert "params_llm" not in calls
    assert out.get("start_date") == "2026-08-10"
    assert out.get("end_date") == "2026-08-13"
