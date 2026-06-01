"""Tests for outdoor adventure profile + schema helpers."""

from __future__ import annotations

from src.wanderly.day_logistics import normalize_day_logistics
from src.wanderly.outdoor_adventure import (
    infer_trip_style_from_text,
    is_backpacking_or_multiday_hike,
    is_outdoor_adventure_profile,
    normalize_camp_night,
    normalize_outdoor_adventure,
    outdoor_itinerary_prompt_block,
)


def test_infer_backpacking_from_message() -> None:
    assert infer_trip_style_from_text("I want a backpacking trip in the Olympics") == "Backpacking / multi-day hike"


def test_is_outdoor_adventure_profile() -> None:
    pills = [{"key": "trip_style", "value": "Backpacking / multi-day hike"}]
    assert is_outdoor_adventure_profile(pills)
    assert is_backpacking_or_multiday_hike(pills)
    block = outdoor_itinerary_prompt_block(pills)
    assert "OUTDOOR" in block
    assert '"kind": "walk"' in block
    assert "surface_legs" in block
    assert "items" in block  # must not replace structured legs
    assert not is_outdoor_adventure_profile([{"key": "trip_style", "value": "General leisure"}])


def test_normalize_outdoor_adventure_and_camp_night() -> None:
    raw = {
        "trip_style": "Backpacking / multi-day hike",
        "permit_requirements": [{"name": "Wilderness permit", "required": True}],
        "camp_nights": [{"kind": "backcountry", "label": "Sol Duc camp", "permit_required": True}],
    }
    out = normalize_outdoor_adventure(raw)
    assert out is not None
    assert out["trip_style"] == "Backpacking / multi-day hike"
    assert len(out["permit_requirements"]) == 1
    assert normalize_camp_night({"kind": "invalid", "label": "X"})["kind"] == "other"


def test_normalize_trail_segment_elevation_loss() -> None:
    from src.wanderly.outdoor_adventure import normalize_trail_segment

    seg = normalize_trail_segment(
        {"name": "West Coast Trail", "distance_mi": 8.5, "elevation_gain_ft": 1200, "elevation_loss_ft": 900}
    )
    assert seg is not None
    assert seg["elevation_loss_ft"] == 900


def test_day_logistics_walk_leg_trail_fields() -> None:
    dl = normalize_day_logistics(
        {
            "flight": None,
            "lodging": None,
            "surface_legs": [
                {
                    "kind": "walk",
                    "from_label": "A",
                    "to_label": "B",
                    "trail_name": "West Coast Trail",
                    "distance_mi": 7.2,
                    "elevation_gain_ft": 800,
                    "elevation_loss_ft": 600,
                }
            ],
        }
    )
    assert dl is not None
    leg = dl["surface_legs"][0]
    assert leg["trail_name"] == "West Coast Trail"
    assert leg["distance_mi"] == 7.2


def test_day_logistics_accepts_camp_night_without_lodging() -> None:
    dl = normalize_day_logistics(
        {
            "flight": None,
            "lodging": None,
            "camp_night": {"kind": "backcountry", "label": "High camp"},
            "surface_legs": [{"kind": "walk", "from_label": "Trailhead", "to_label": "High camp"}],
        }
    )
    assert dl is not None
    assert dl["camp_night"]["label"] == "High camp"
