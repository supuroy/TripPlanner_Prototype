"""Tests for AllTrails park URL parsing."""

from src.wanderly.alltrails_park_url import (
    infer_park_code_from_slug,
    infer_park_name_from_slug,
    parse_alltrails_park_url,
    resolve_catalog_fields,
)


def test_parse_yosemite_url() -> None:
    meta = parse_alltrails_park_url(
        "https://www.alltrails.com/parks/us/california/yosemite-national-park"
    )
    assert meta.park_code == "yose"
    assert meta.park_name == "Yosemite National Park"
    assert meta.region == "California"
    assert meta.country == "United States"
    assert meta.park_url.endswith("/us/california/yosemite-national-park")


def test_parse_yellowstone_url() -> None:
    meta = parse_alltrails_park_url(
        "https://www.alltrails.com/parks/us/wyoming/yellowstone-national-park"
    )
    assert meta.park_code == "yell"
    assert meta.park_name == "Yellowstone National Park"


def test_resolve_overrides() -> None:
    meta = resolve_catalog_fields(
        "https://www.alltrails.com/parks/us/california/yosemite-national-park",
        park_code="custom",
        park_name="Custom Name",
    )
    assert meta.park_code == "custom"
    assert meta.park_name == "Custom Name"
    assert meta.region == "California"


def test_canadian_park_fallback_code() -> None:
    assert infer_park_code_from_slug("banff-national-park", country_code="ca") == "banff"
    assert infer_park_name_from_slug("banff-national-park") == "Banff National Park"
