"""Tests for trail route waypoint collection (no live geocoding)."""

from __future__ import annotations

from src.wanderly.route_waypoints import (
    _coords_from_trail_lookups,
    _finalize_stops,
    _lookup_entry_matches_label,
    _nps_coord_lookups,
    _resolve_nps_coords,
    _trail_dict_from_lookup_entry,
    collect_hike_legs,
    collect_route_stops,
    _itinerary_has_walk_legs,
    hydrate_destination_for_route_map,
    resolve_waypoint,
)


def test_hydrate_destination_pulls_nps_from_session() -> None:
    client = {"id": "heart", "name": "Heart Lake", "itinerary": []}
    session = {
        "ui_destinations": [
            {
                "id": "heart",
                "nps_outdoor": {"camp_matches": [{"plan_camp_label": "8H2", "latitude": 44.1, "longitude": -110.5}]},
                "itinerary": [{"day": 1, "day_logistics": {"surface_legs": [{"kind": "walk", "from_label": "A", "to_label": "B"}]}}],
            },
        ],
    }
    merged = hydrate_destination_for_route_map(client, session)
    assert merged.get("nps_outdoor") is not None
    assert _itinerary_has_walk_legs(merged.get("itinerary"))


def test_collect_hike_legs_inferred_from_day_labels() -> None:
    """Heart Lake-style plans often omit surface_legs but have ``A to B`` day labels."""
    itinerary = [
        {
            "day": 2,
            "label": "Heart Lake TH to 8H2 Sheridan Creek",
            "day_logistics": {
                "camp_night": {"kind": "backcountry", "label": "8H2 Sheridan Creek campground"},
            },
        },
        {
            "day": 3,
            "label": "8H2 Sheridan Creek to 8C6 Snake River Ford → extra noise",
            "day_logistics": {
                "camp_night": {"kind": "backcountry", "label": "8C6 Snake River Ford campground"},
            },
        },
    ]
    legs = collect_hike_legs(itinerary)
    assert len(legs) == 2
    assert legs[0]["from_label"] == "Heart Lake TH"
    assert "8H2" in (legs[0]["to_label"] or "")
    assert legs[0].get("source") == "inferred_label"
    assert "8C6" in (legs[1]["to_label"] or "")


def test_collect_hike_legs() -> None:
    itinerary = [
        {
            "day": 2,
            "day_logistics": {
                "surface_legs": [
                    {
                        "kind": "walk",
                        "from_label": "Heart Lake trailhead",
                        "to_label": "8H2 Sheridan Creek",
                        "distance_mi": 8.5,
                    },
                ],
            },
        },
    ]
    legs = collect_hike_legs(itinerary)
    assert len(legs) == 1
    assert legs[0]["from_label"] == "Heart Lake trailhead"
    assert legs[0]["to_label"] == "8H2 Sheridan Creek"
    assert legs[0]["distance_mi"] == 8.5


def test_collect_camps_and_trailhead() -> None:
    itinerary = [
        {
            "day": 1,
            "label": "Day 1",
            "day_logistics": {
                "surface_legs": [{"kind": "walk", "from_label": "Grant Village", "to_label": "Coyote (8S3)"}],
                "camp_night": {"kind": "backcountry", "label": "Coyote (8S3)"},
            },
        },
        {
            "day": 2,
            "label": "Day 2",
            "day_logistics": {
                "camp_night": {"kind": "backcountry", "label": "Outlet (8S1)"},
            },
        },
    ]
    stops = collect_route_stops(itinerary)
    labels = [s["label"] for s in stops]
    assert "Coyote (8S3)" in labels
    assert "Outlet (8S1)" in labels
    assert "Grant Village" in labels


def test_finalize_stops_merges_campsite_variant() -> None:
    stops = _finalize_stops(
        [
            {"day": 2, "label": "8H2 Sheridan Creek campground", "kind": "camp"},
            {"day": 0, "label": "Sheridan Creek campsite", "kind": "camp"},
        ],
    )
    assert len(stops) == 1
    assert "8H2" in stops[0]["label"]
    assert stops[0]["day"] == 2


def test_nps_coords_by_place_name() -> None:
    matches = [
        {
            "plan_camp_label": "8H2 Sheridan Creek campground",
            "latitude": 44.5,
            "longitude": -110.6,
        },
    ]
    by_label, by_site, by_place = _nps_coord_lookups(matches)
    pair = _resolve_nps_coords(
        "Sheridan Creek campsite",
        by_label=by_label,
        by_site=by_site,
        by_place=by_place,
    )
    assert pair == (44.5, -110.6)


def test_trail_lookup_skips_wrong_park_area() -> None:
    lookups = {
        "2:1": {
            "name": "Monument Hill Trail",
            "location_label": "Hot Springs State Park, Wyoming",
            "from_label": "Heart Lake Trailhead (8N1)",
            "to_label": "8H2 Sheridan Creek campground",
            "latitude": 43.65825,
            "longitude": -108.19068,
        },
    }
    pair = _coords_from_trail_lookups(
        "8H2 Sheridan Creek campground",
        trail_lookups=lookups,
        park="Yellowstone National Park",
        country="United States",
        cache={},
    )
    assert pair is None


def test_resolve_waypoint_falls_through_when_trail_lookup_out_of_bounds(monkeypatch) -> None:
    monkeypatch.setenv("WANDERLY_GEOCODE_DISABLE", "1")
    lookups = {
        "2:1": {
            "name": "Monument Hill Trail",
            "location_label": "Yellowstone National Park, Wyoming",
            "from_label": "Heart Lake Trailhead (8N1)",
            "to_label": "8H2 Sheridan Creek campground",
            "latitude": 43.65825,
            "longitude": -108.19068,
        },
    }
    wp = resolve_waypoint(
        "8H2 Sheridan Creek campground",
        park="Yellowstone National Park",
        country="United States",
        nps_by_label={},
        nps_by_site={},
        nps_by_place={},
        trail_lookups=lookups,
        cache={},
        bounds_key="yellowstone",
    )
    assert wp["source"] == "unresolved"
    assert wp["lat"] is None


def test_flattened_trail_lookup_entry_geocodes() -> None:
    """Saved ``trail_lookups`` store trail fields at top level (no nested ``trail``)."""
    lookups = {
        "2:0": {
            "name": "Shoshone Lake via DeLacy Creek Trail",
            "location_label": "Yellowstone National Park, Wyoming",
            "area": "Yellowstone National Park",
            "state": "Wyoming",
            "from_label": "DeLacy Creek Trailhead (7K2)",
            "to_label": "8S3 Coyote campsite",
            "latitude": 44.42,
            "longitude": -110.55,
        },
    }
    assert _trail_dict_from_lookup_entry(lookups["2:0"]).get("name")
    assert _lookup_entry_matches_label(lookups["2:0"], "8S3 Coyote campsite")
    pair = _coords_from_trail_lookups(
        "8S3 Coyote campsite",
        trail_lookups=lookups,
        park="Yellowstone National Park",
        country="United States",
        cache={},
    )
    assert pair == (44.42, -110.55)


def test_resolve_waypoint_uses_flattened_trail_coords() -> None:
    lookups = {
        "2:0": {
            "name": "Shoshone Lake via DeLacy Creek Trail",
            "from_label": "DeLacy Creek Trailhead (7K2)",
            "to_label": "8S3 Coyote campsite",
            "latitude": 44.42,
            "longitude": -110.55,
        },
    }
    wp = resolve_waypoint(
        "DeLacy Creek Trailhead (7K2)",
        park="Yellowstone National Park",
        country="United States",
        nps_by_label={},
        nps_by_site={},
        nps_by_place={},
        trail_lookups=lookups,
        cache={},
        bounds_key="yellowstone",
    )
    assert wp["source"] == "trail_lookup"
    assert wp["lat"] == 44.42


def test_dedupe_same_day_camp() -> None:
    itinerary = [
        {
            "day": 1,
            "day_logistics": {
                "camp_night": {"kind": "backcountry", "label": "Coyote (8S3)"},
            },
        },
    ]
    stops = collect_route_stops(itinerary)
    assert len([s for s in stops if "Coyote" in s["label"]]) == 1
