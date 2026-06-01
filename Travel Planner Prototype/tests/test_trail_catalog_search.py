"""Trail catalog search helpers (no LLM)."""

from __future__ import annotations

from src.wanderly.trail_catalog.search import (
    search_feature_trails,
    search_manifest_features,
    search_manifest_parks,
    search_trails,
)
from src.wanderly.trip_intent_resolve import reset_manifest_cache_for_tests


def setup_function() -> None:
    reset_manifest_cache_for_tests()


def test_search_yellowstone_park() -> None:
    parks = search_manifest_parks("yellowstone", limit=3)
    assert any(str(p.get("park_code") or "").lower() == "yell" for p in parks)


def test_search_trails_yell() -> None:
    rows = search_trails("yell", query="heart", limit=10)
    assert rows
    assert all(str(r.get("trail_id") or "") for r in rows)


def test_search_manifest_features_waterfall() -> None:
    features = search_manifest_features("water", limit=5)
    assert any(str(f.get("feature_slug") or "") == "waterfall" for f in features)


def test_search_feature_trails_waterfall() -> None:
    rows = search_feature_trails("waterfall", query="zion", limit=10)
    assert rows
    assert all(str(r.get("trail_id") or "").startswith("waterfall:") for r in rows)
