"""Trip intent classification and manifest resolution (no live LLM)."""

from __future__ import annotations

import os

import pytest

from src.wanderly.trip_intent_llm import (
    TripIntentClassification,
    classify_trip_intent_llm,
    fast_path_trip_intent,
    is_outdoor_trip_product,
    trip_intent_llm_enabled,
)
from src.wanderly.trip_intent_resolve import (
    resolve_park_codes_from_hints,
    resolve_trip_intent,
    reset_manifest_cache_for_tests,
)


@pytest.fixture(autouse=True)
def _disable_trip_intent_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WANDERLY_TRIP_INTENT_LLM", "0")
    reset_manifest_cache_for_tests()


def test_fast_path_yellowstone_backpacking() -> None:
    fp = fast_path_trip_intent("4 days backpacking in Yellowstone National Park")
    assert fp is not None
    assert fp.trip_product == "outdoor_backpacking"
    assert any("yellowstone" in h.lower() for h in fp.destination_hints)


def test_classify_defaults_leisure_for_iceland() -> None:
    dto = classify_trip_intent_llm(
        user_message="Two weeks in Iceland, northern lights and hot springs",
        session_id="test-trip-intent",
    )
    assert dto.trip_product == "leisure"
    assert not is_outdoor_trip_product(dto.trip_product)


def test_resolve_yell_catalog_eligible() -> None:
    intent = TripIntentClassification(
        trip_product="outdoor_backpacking",
        destination_hints=["Yellowstone National Park"],
        confidence=0.9,
        rationale="test",
    )
    res = resolve_trip_intent(
        intent,
        user_message="backpacking in Yellowstone",
    )
    assert res.primary_park_code == "yell"
    assert res.catalog_eligible is True
    assert res.catalog_trail_count >= 5


def test_resolve_park_codes_from_message() -> None:
    codes = resolve_park_codes_from_hints([], user_message="hike in Yosemite")
    assert "yose" in codes


def test_trip_intent_llm_env_toggle() -> None:
    os.environ["WANDERLY_TRIP_INTENT_LLM"] = "0"
    assert trip_intent_llm_enabled() is False
