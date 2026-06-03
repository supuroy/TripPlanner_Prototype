"""Intent catalog lane (local search + pick)."""

from __future__ import annotations

import pytest

from src.wanderly.trail_catalog.intent_pick import (
    build_catalog_query_from_message,
    pick_trails_for_intent,
    search_trails_for_intent,
)
from src.wanderly.trip_intent_llm import TripIntentClassification
from src.wanderly.trip_intent_resolve import resolve_trip_intent


def test_build_catalog_query_utah_waterfall() -> None:
    cq = build_catalog_query_from_message(
        "3 moderate waterfall hikes in Utah under 7 miles",
        None,
        pick_count=3,
    )
    assert cq.max_distance_mi == 7.0
    assert cq.difficulty == "moderate"
    assert "waterfall" in cq.feature_slugs
    assert "utah" in cq.state_slugs


def test_search_trails_for_intent_utah_waterfall() -> None:
    result = search_trails_for_intent(
        "3 moderate waterfall hikes in Utah under 7 miles",
        session_id="test",
        pick_count=3,
    )
    assert int(result.get("count") or 0) >= 3
    trails = result.get("trails")
    assert isinstance(trails, list)
    assert str(trails[0].get("trail_id") or "").startswith("trail:")


def test_pick_trails_for_intent_without_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.wanderly.trail_catalog.intent_pick.llm_select_trails",
        lambda **_: [],
    )
    picks, meta = pick_trails_for_intent(
        user_message="3 moderate waterfall hikes in Utah under 7 miles",
        profile={"trip_style": "hiking"},
        resolution=None,
        session_id="test-no-llm",
        k=3,
        use_llm=True,
    )
    assert len(picks) == 3
    assert meta["pick_method"] in ("search_rank", "heuristic")
    assert all(str(p.get("trail_id") or "").startswith("trail:") for p in picks)


def test_resolve_trip_intent_intent_lane_for_utah_waterfall() -> None:
    intent = TripIntentClassification(
        trip_product="outdoor_general",
        destination_hints=["Utah"],
        confidence=0.9,
        rationale="waterfall day hikes",
    )
    res = resolve_trip_intent(intent, user_message="3 moderate waterfall hikes in Utah under 7 miles")
    assert res.catalog_eligible is True
    assert res.catalog_lane == "intent"
    assert res.catalog_trail_count >= 3


def test_intent_catalog_cards_lazy_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke turn returns catalog-grounded cards; itineraries build lazily (same as leisure flow)."""
    from src.wanderly.catalog_smoke_turn import build_intent_catalog_workspace
    from src.wanderly.trip_intent_resolve import TripIntentResolution

    monkeypatch.setattr(
        "src.wanderly.trail_catalog.intent_pick.llm_select_trails",
        lambda **_: [],
    )
    resolution = TripIntentResolution(
        trip_product="outdoor_general",
        destination_hints=["Utah"],
        catalog_eligible=True,
        catalog_lane="intent",
        catalog_trail_count=3,
        intent_scope={"state_slugs": ["utah"], "feature_slugs": ["waterfall"]},
    )
    dto = build_intent_catalog_workspace(
        user_message="3 moderate waterfall hikes in Utah under 7 miles",
        session_id="test-lazy-cards",
        resolution=resolution,
    )
    assert len(dto.destinations) >= 3
    for dest in dto.destinations:
        d = dest if isinstance(dest, dict) else dest.model_dump()
        assert d.get("itinerary_dirty") is True
        assert not (d.get("itinerary") or [])
        g = d.get("grounding")
        assert isinstance(g, dict)
        assert str(g.get("status") or "").lower() == "catalog"
        assert str(g.get("trail_id") or "").startswith("trail:")
        assert str(g.get("park_code") or "").strip()
