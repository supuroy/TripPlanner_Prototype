"""Tests for backfilled walk surface_legs on hiking days."""

from __future__ import annotations

from src.wanderly.day_logistics import (
    repair_destination_camp_logistics,
    sync_walk_surface_legs_to_itinerary_rows,
)


def test_sync_walk_from_day_label_and_camp() -> None:
    rows = [
        {
            "day": 2,
            "label": "Heart Lake TH to 8H2 Sheridan Creek",
            "day_logistics": {
                "flight": None,
                "lodging": None,
                "camp_night": {"kind": "backcountry", "label": "8H2 Sheridan Creek campground"},
                "trail_segment": {
                    "name": "Heart Lake Trail",
                    "distance_mi": 9.1,
                    "elevation_gain_ft": 691,
                    "elevation_loss_ft": 1102,
                },
            },
        },
    ]
    out = sync_walk_surface_legs_to_itinerary_rows(rows)
    legs = out[0]["day_logistics"]["surface_legs"]
    assert any(l.get("kind") == "walk" for l in legs)
    walk = next(l for l in legs if l.get("kind") == "walk")
    assert walk["from_label"] == "Heart Lake TH"
    assert "8H2" in walk["to_label"]
    assert walk.get("trail_name") == "Heart Lake Trail"
    assert walk.get("distance_mi") == 9.1


def test_repair_destination_adds_walk_legs() -> None:
    dest = {
        "id": "yellowstone-heart-lake",
        "name": "Heart Lake",
        "itinerary": [
            {
                "day": 2,
                "label": "Heart Lake TH to 8H2 Sheridan Creek",
                "day_logistics": {
                    "camp_night": {"kind": "backcountry", "label": "8H2 Sheridan Creek campground"},
                    "trail_segment": {"name": "Heart Lake Trail", "distance_mi": 9.1, "elevation_gain_ft": 691, "elevation_loss_ft": 1102},
                },
            },
        ],
    }
    fixed = repair_destination_camp_logistics(dest)
    legs = fixed["itinerary"][0]["day_logistics"].get("surface_legs") or []
    assert any(l.get("kind") == "walk" for l in legs)
