"""Park bounding-box validation for waypoint resolution."""

from __future__ import annotations

from src.wanderly.geo_bounds import (
    apply_geo_bounds_to_waypoint,
    coords_within_bounds,
    infer_park_bounds_key,
    validate_coords_for_park,
)


def test_yellowstone_bounds() -> None:
    assert coords_within_bounds(44.5, -110.8, "yellowstone")
    assert not coords_within_bounds(41.2, -122.3, "yellowstone")


def test_apply_geo_bounds_rejects_out_of_park() -> None:
    wp = apply_geo_bounds_to_waypoint(
        {"label": "Wrong Heart Lake", "lat": 41.23, "lng": -122.38, "source": "trail_lookup"},
        bounds_key="yellowstone",
    )
    assert wp["lat"] is None
    assert wp["source"] == "rejected_out_of_bounds"


def test_infer_bounds_from_destination_id() -> None:
    assert infer_park_bounds_key(destination_id="yellowstone-heart-lake") == "yellowstone"


def test_infer_bounds_from_yosemite_name() -> None:
    assert infer_park_bounds_key(destination_name="Yosemite backpacking") == "yosemite"


def test_validate_coords_message() -> None:
    ok, msg = validate_coords_for_park(41.0, -122.0, bounds_key="yellowstone")
    assert ok is False
    assert msg
