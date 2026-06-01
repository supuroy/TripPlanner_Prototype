"""Tests for NPS outdoor enrich (no live API calls)."""

from __future__ import annotations

from src.wanderly.nps_provider import (
    _parse_camp_label,
    dedupe_camp_labels,
    match_campgrounds_to_plan,
    normalize_alert,
    normalize_campground,
    normalize_park_fees,
    resolve_park_codes,
)


def test_resolve_yosemite_alias() -> None:
    codes, how = resolve_park_codes(
        destination_name="Yosemite National Park",
        destination_country="USA",
    )
    assert "yose" in codes
    assert how == "alias_match"


def test_heart_lake_inherits_yellowstone_from_trip() -> None:
    codes, how = resolve_park_codes(
        destination_name="Heart Lake",
        destination_country="USA",
        trip_name="yellowstone backpacking trip",
    )
    assert codes == ["yell"]
    assert how in ("trip_zone_match", "trip_context_inherit")


def test_snake_river_ford_inherits_yellowstone_from_trip() -> None:
    codes, how = resolve_park_codes(
        destination_name="Snake River Ford",
        destination_country="USA",
        trip_name="yellowstone backpacking trip",
    )
    assert codes == ["yell"]
    assert how in ("trip_zone_match", "trip_context_inherit")


def test_backcountry_site_code_parsing() -> None:
    label, is_bc = _parse_camp_label("8H2 Sheridan Creek campground")
    assert is_bc is True
    assert "Sheridan Creek" in label


def test_parenthetical_site_code_parsing() -> None:
    label, is_bc = _parse_camp_label("Basin Bay Point (8R5)")
    assert is_bc is True
    assert "8R5" not in label
    assert "Basin Bay Point" in label


def test_shoshone_camps_backcountry_status() -> None:
    matches = match_campgrounds_to_plan(
        [{"name": "Madison Campground"}],
        ["Coyote (8S3)", "Outlet (8S1)"],
        park_codes=["yell"],
    )
    assert matches[0].get("status") == "backcountry_not_in_api"
    assert "recreation.gov" in (matches[0].get("notes") or "").lower()


def test_dedupe_camp_labels_by_site_code() -> None:
    out = dedupe_camp_labels(
        ["Coyote (8S3)", "Coyote campsite", "Outlet (8S1)", "Outlet campsite"],
    )
    assert out == ["Coyote (8S3)", "Outlet (8S1)"]


def test_backcountry_site_gets_explicit_status() -> None:
    matches = match_campgrounds_to_plan(
        [{"name": "Madison Campground"}],
        ["8C6 Snake River Ford campground"],
        park_codes=["yell"],
    )
    assert matches[0].get("status") == "backcountry_not_in_api"
    assert "backcountry" in (matches[0].get("notes") or "").lower()


def test_resolve_wct_canada_out_of_scope() -> None:
    codes, how = resolve_park_codes(
        destination_name="West Coast Trail (Gordon River Half)",
        destination_country="Canada",
    )
    assert codes == []
    assert how == "canadian_park_out_of_nps_scope"


def test_normalize_campground_coords() -> None:
    cg = normalize_campground(
        {
            "name": "Indian Creek Campground",
            "latitude": "44.1234",
            "longitude": "-110.5678",
        }
    )
    assert cg.get("latitude") == 44.1234
    assert cg.get("longitude") == -110.5678


def test_match_campgrounds_passes_coords() -> None:
    cgs = [
        {
            "name": "Indian Creek Campground",
            "latitude": 44.1,
            "longitude": -110.5,
        },
    ]
    matches = match_campgrounds_to_plan(
        cgs,
        ["8H2 Sheridan Creek campground"],
        park_codes=["yell"],
        min_score=0.1,
    )
    assert matches[0].get("latitude") == 44.1
    assert matches[0].get("longitude") == -110.5


def test_match_campgrounds() -> None:
    cgs = [
        {"name": "Upper Pines Campground", "reservation_url": "https://example.com/book"},
        {"name": "North Pines Campground", "reservation_url": "https://example.com/np"},
    ]
    matches = match_campgrounds_to_plan(cgs, ["Upper Pines", "Unknown Backcountry Zone"])
    assert matches[0].get("plan_camp_label") == "Upper Pines"
    assert "Upper Pines" in (matches[0].get("name") or "")
    assert matches[1].get("status") in ("not_found_in_nps", "backcountry_not_in_api")


def test_normalize_alert_and_fees() -> None:
    a = normalize_alert(
        {
            "title": "Trail closure",
            "description": "Ice on trail",
            "category": "Closure",
            "url": "https://www.nps.gov/yose/planyourvisit/conditions.htm",
            "parkCode": "yose",
        }
    )
    assert a["title"] == "Trail closure"
    f = normalize_park_fees(
        {
            "fullName": "Yosemite National Park",
            "parkCode": "yose",
            "url": "https://www.nps.gov/yose/",
            "entranceFees": [{"title": "Per vehicle", "cost": "$35", "description": "7 days"}],
            "entrancePasses": [{"title": "America the Beautiful", "cost": "$80", "description": "Annual"}],
        }
    )
    assert f["park_code"] == "yose"
    assert f["entrance_fees"][0]["cost"] == "$35"
