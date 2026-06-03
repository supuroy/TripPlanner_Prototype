"""Tests for persisted AllTrails search context (V2 — verify, never guess URLs)."""

from __future__ import annotations

from src.wanderly.alltrails_search_context import (
    candidate_parent_alltrails_urls,
    derive_alltrails_search_context,
    extract_alltrails_url_from_text,
    infer_parent_alltrails_url,
    normalize_alltrails_search_context,
    preferred_alltrails_urls_from_destination,
    region_path_from_context,
    verified_parent_alltrails_url,
)
from src.wanderly.alltrails_provider import _extract_city_from_label, _item_matches_start_url


def test_normalize_keeps_core_context() -> None:
    ctx = normalize_alltrails_search_context(
        {
            "trail_name": "Heart Lake Trail",
            "park_name": "Yellowstone National Park",
            "region": "Wyoming",
            "country": "United States",
            "search_query": "Heart Lake Trail Yellowstone Wyoming",
            "disambiguation": "Not Castle Lake CA",
        }
    )
    assert ctx is not None
    assert "yellowstone" in ctx["search_query"].lower()
    assert region_path_from_context(ctx) == "us/wyoming"


def test_unverified_llm_url_is_not_persisted_as_authoritative() -> None:
    """A model-emitted URL with no verification must never become ``parent_alltrails_url``."""
    ctx = normalize_alltrails_search_context(
        {
            "trail_name": "Heart Lake Trail",
            "region": "Wyoming",
            "country": "United States",
            "parent_alltrails_url": "https://www.alltrails.com/trail/us/wyoming/heart-lake--2",
        }
    )
    assert ctx is not None
    assert "parent_alltrails_url" not in ctx
    assert ctx.get("verified") is not True
    # The hint is retained separately for debugging, but never used as authority.
    assert ctx.get("unverified_url_hint", "").endswith("heart-lake--2")


def test_catalog_verification_promotes_parent_url() -> None:
    ctx = normalize_alltrails_search_context(
        {
            "trail_name": "Bullfrog Lakes via Farewell Gap",
            "country": "United States",
            "verification_status": "catalog",
            "parent_alltrails_url": "https://www.alltrails.com/trail/us/california/bullfrog-lakes-via-farewell-gap",
        }
    )
    assert ctx is not None
    assert ctx.get("verified") is True
    assert ctx.get("verification_status") == "catalog"
    assert "bullfrog-lakes" in ctx["parent_alltrails_url"]
    assert "unverified_url_hint" not in ctx


def test_preferred_urls_from_catalog_grounding() -> None:
    dest = {
        "name": "Bullfrog Lakes",
        "grounding": {
            "status": "catalog",
            "source_url": "https://www.alltrails.com/trail/us/california/bullfrog-lakes-via-farewell-gap",
        },
    }
    urls = preferred_alltrails_urls_from_destination(dest)
    assert len(urls) == 1
    assert "bullfrog-lakes" in urls[0]


def test_verified_url_is_persisted() -> None:
    ctx = normalize_alltrails_search_context(
        {
            "trail_name": "Heart Lake Trail",
            "region": "Wyoming",
            "country": "United States",
            "verification_status": "verified",
            "parent_alltrails_url": "https://www.alltrails.com/trail/us/wyoming/heart-lake-trail",
            "alltrails_trail_id": "10012345",
        }
    )
    assert ctx is not None
    assert ctx["verified"] is True
    assert ctx["parent_alltrails_url"].endswith("heart-lake-trail")
    assert ctx["alltrails_trail_id"] == "10012345"


def test_no_url_guessing_without_verification() -> None:
    ctx = {
        "trail_name": "Heart Lake Trail",
        "region": "Wyoming",
        "country": "United States",
        "search_query": "Heart Lake Trail Yellowstone Wyoming",
    }
    assert candidate_parent_alltrails_urls(ctx) == []
    assert infer_parent_alltrails_url(ctx) is None


def test_preferred_urls_only_verified() -> None:
    unverified = {
        "name": "Heart Lake Trail",
        "alltrails_search_context": {
            "trail_name": "Heart Lake Trail",
            "parent_alltrails_url": "https://www.alltrails.com/trail/us/wyoming/heart-lake-trail",
        },
    }
    assert preferred_alltrails_urls_from_destination(unverified) == []
    assert verified_parent_alltrails_url(unverified) is None

    verified = {
        "name": "Heart Lake Trail",
        "alltrails_search_context": {
            "trail_name": "Heart Lake Trail",
            "verified": True,
            "verification_status": "verified",
            "parent_alltrails_url": "https://www.alltrails.com/trail/us/wyoming/heart-lake-trail",
        },
    }
    urls = preferred_alltrails_urls_from_destination(verified)
    assert urls == ["https://www.alltrails.com/trail/us/wyoming/heart-lake-trail"]
    assert verified_parent_alltrails_url(verified) == urls[0]


def test_derive_from_yellowstone_card() -> None:
    dest = {
        "id": "heart-lake-trail",
        "name": "Heart Lake Trail",
        "hook": "Yellowstone backcountry from 8N1 to 8H2",
        "country": "United States",
    }
    ctx = derive_alltrails_search_context(dest)
    assert ctx is not None
    assert ctx.get("region") == "Wyoming"
    assert "heart lake" in ctx["search_query"].lower()
    # Derived context is never auto-verified.
    assert ctx.get("verified") is not True
    assert "parent_alltrails_url" not in ctx


def test_extract_explore_trail_url() -> None:
    u = extract_alltrails_url_from_text(
        "See https://www.alltrails.com/explore/trail/us/wyoming/heart-lake--2 for stats."
    )
    assert u is not None
    assert "explore/trail/us/wyoming/heart-lake--2" in u


def test_extract_city_skips_backcountry_codes() -> None:
    assert _extract_city_from_label("Heart Lake Trailhead (8N1), Yellowstone NP") is None
    assert _extract_city_from_label("8H2 Sheridan Creek campsite") is None


def test_strict_url_match_rejects_california_slug() -> None:
    wy = "https://www.alltrails.com/explore/trail/us/wyoming/heart-lake--2"
    ca = "https://www.alltrails.com/trail/us/california/heart-lake-trail-from-castle-lake"
    assert _item_matches_start_url({"url": wy}, wy)
    assert not _item_matches_start_url({"url": ca}, wy)
