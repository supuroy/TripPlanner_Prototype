"""Tests for deterministic backpacking itinerary QA."""

from __future__ import annotations

from src.wanderly.backpacking_itinerary_qa import (
    apply_backpacking_itinerary_qa,
    normalize_chained_day_label,
    validate_backpacking_itinerary,
)
from src.wanderly.day_logistics import repair_destination_camp_logistics


def _backpacking_pills() -> list[dict]:
    return [{"key": "trip_style", "label": "Trip style", "value": "Backpacking / multi-day hike"}]


def test_label_chained_detected() -> None:
    dest = {
        "id": "yellowstone-heart-lake",
        "name": "Heart Lake",
        "hook": "Yellowstone backpacking",
        "itinerary": [
            {
                "day": 4,
                "label": "8C6 to South Entrance → Day 5 Grand Teton",
                "day_logistics": {
                    "surface_legs": [
                        {"kind": "walk", "from_label": "8C6", "to_label": "South Entrance"},
                    ],
                },
            },
        ],
    }
    qa = validate_backpacking_itinerary(dest, profile_pills=_backpacking_pills())
    codes = {x["code"] for x in qa["issues"]}
    assert "LABEL_CHAINED" in codes


def test_normalize_chained_label() -> None:
    assert normalize_chained_day_label("A to B → C to D") == "A to B"


def test_break_in_chain_detected() -> None:
    dest = {
        "id": "yellowstone-test",
        "name": "Test Loop",
        "hook": "Yellowstone",
        "itinerary": [
            {
                "day": 2,
                "label": "TH to Camp A",
                "day_logistics": {
                    "camp_night": {"kind": "backcountry", "label": "Camp A"},
                    "surface_legs": [{"kind": "walk", "from_label": "TH", "to_label": "Camp A"}],
                },
            },
            {
                "day": 3,
                "label": "Wrong start to Camp B",
                "day_logistics": {
                    "camp_night": {"kind": "backcountry", "label": "Camp B"},
                    "surface_legs": [{"kind": "walk", "from_label": "Other TH", "to_label": "Camp B"}],
                },
            },
        ],
    }
    qa = validate_backpacking_itinerary(dest, profile_pills=_backpacking_pills())
    assert qa["ok"] is False
    assert any(x["code"] == "BREAK_IN_CHAIN" for x in qa["issues"])


def test_repair_attaches_itinerary_qa() -> None:
    dest = {
        "id": "yellowstone-heart-lake",
        "name": "Heart Lake",
        "itinerary": [
            {
                "day": 2,
                "label": "Heart Lake TH to 8H2 Sheridan Creek",
                "day_logistics": {
                    "camp_night": {"kind": "backcountry", "label": "8H2 Sheridan Creek campground"},
                    "trail_segment": {"name": "Heart Lake Trail", "distance_mi": 9.1},
                },
            },
        ],
    }
    fixed = repair_destination_camp_logistics(dest)
    assert "itinerary_qa" in fixed
    assert isinstance(fixed["itinerary_qa"], dict)


def test_apply_normalizes_chained_labels() -> None:
    dest = {
        "id": "x",
        "name": "X",
        "itinerary": [{"day": 2, "label": "A to B → C to D", "day_logistics": {}}],
    }
    out = apply_backpacking_itinerary_qa(dest, profile_pills=_backpacking_pills())
    assert out["itinerary"][0]["label"] == "A to B"
