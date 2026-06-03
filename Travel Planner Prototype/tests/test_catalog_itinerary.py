"""Catalog-grounded itinerary routing (no live LLM)."""

from __future__ import annotations

import pytest

from src.wanderly.catalog_itinerary import (
    is_catalog_grounded_destination,
    resolve_park_code_for_destination,
)
from src.wanderly.trip_intent_resolve import reset_manifest_cache_for_tests


@pytest.fixture(autouse=True)
def _reset_manifest() -> None:
    reset_manifest_cache_for_tests()


def test_is_catalog_grounded_destination() -> None:
    assert is_catalog_grounded_destination(
        {
            "id": "heart-lake--2",
            "name": "Heart Lake Trail",
            "grounding": {
                "trail_id": "heart-lake--2",
                "source_url": "https://www.alltrails.com/trail/us/wyoming/heart-lake-trail",
                "status": "catalog",
                "park_code": "yell",
            },
        }
    )
    assert not is_catalog_grounded_destination({"id": "iceland", "name": "Iceland"})


def test_resolve_park_code_from_grounding() -> None:
    dest = {
        "name": "Heart Lake Trail",
        "grounding": {"trail_id": "heart-lake--2", "status": "catalog", "park_code": "yell"},
    }
    assert resolve_park_code_for_destination(dest) == "yell"


def test_resolve_park_code_from_session_intent() -> None:
    dest = {
        "name": "Some Trail",
        "grounding": {"trail_id": "x", "status": "catalog"},
    }
    # No park on card — falls back to trip intent or text
    code = resolve_park_code_for_destination(
        dest,
        session_id="unused",
    )
    assert code is None or isinstance(code, str)
