"""Tests for itinerary waypoint sheet (no zone fallbacks)."""

from __future__ import annotations

import os

from src.wanderly.itinerary_waypoint_sheet import build_itinerary_waypoint_sheet
from src.wanderly.route_waypoints import resolve_waypoint


def test_resolve_waypoint_unresolved_without_sources(monkeypatch) -> None:
    monkeypatch.setenv("WANDERLY_GEOCODE_DISABLE", "1")
    wp = resolve_waypoint(
        "8H2 Sheridan Creek campground",
        park="Yellowstone National Park",
        country="United States",
        nps_by_label={},
        nps_by_site={},
        nps_by_place={},
        trail_lookups=None,
        cache={},
    )
    assert wp["source"] == "unresolved"
    assert wp["lat"] is None


def test_waypoint_sheet_structure(monkeypatch) -> None:
    monkeypatch.setenv("WANDERLY_GEOCODE_DISABLE", "1")
    out = build_itinerary_waypoint_sheet(
        destination_id="yellowstone-heart-lake",
        destination_name="Heart Lake",
        destination_country="United States",
        destination_hook="Yellowstone",
        itinerary=[
            {
                "day": 2,
                "label": "Heart Lake TH to 8H2 Sheridan Creek",
                "day_logistics": {
                    "camp_night": {"kind": "backcountry", "label": "8H2 Sheridan Creek campground"},
                    "surface_legs": [
                        {
                            "kind": "walk",
                            "from_label": "Heart Lake TH",
                            "to_label": "8H2 Sheridan Creek campground",
                        },
                    ],
                },
            },
        ],
        profile_pills=[{"key": "trip_style", "value": "Backpacking / multi-day hike"}],
    )
    assert len(out["days"]) == 1
    day = out["days"][0]
    assert day["hike_start"]["label"] == "Heart Lake TH"
    assert day["hike_end"]["label"]
    assert "csv" in out["export"]
    assert out["unresolved_count"] >= 1
