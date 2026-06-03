"""Park-aware stock hero queries for in-park hike cards."""

from unittest.mock import patch

from src.wanderly.stock_photo_heroes import (
    _unsplash_query_candidates,
    build_stock_search_query,
    build_stock_search_query_from_dest,
    infer_hero_park_phrase,
)


def test_infer_yellowstone_from_destination_id():
    park = infer_hero_park_phrase(
        destination_id="yellowstone-shoshone-lake",
        name="Shoshone Lake loop",
        hook="Classic moderate loop",
    )
    assert park == "Yellowstone National Park"


def test_build_stock_query_includes_park():
    q = build_stock_search_query(
        "Shoshone Lake loop",
        "United States",
        None,
        park_phrase="Yellowstone National Park",
    )
    assert "Yellowstone" in q
    assert "Shoshone" in q


def test_build_stock_query_from_dest_yellowstone():
    dest = {
        "id": "yellowstone-bechler",
        "name": "Bechler River (Cascade Corner)",
        "country": "United States",
        "hook": "Point-to-point waterfalls",
    }
    q = build_stock_search_query_from_dest(dest, None)
    assert "Yellowstone" in q
    assert "Bechler" in q


def test_unsplash_candidates_park_first():
    cands = _unsplash_query_candidates(
        "Bechler River United States",
        "Bechler River (Cascade Corner)",
        "United States",
        park_phrase="Yellowstone National Park",
    )
    assert cands[0].startswith("Yellowstone")
    assert any("hiking" in c.lower() for c in cands[:6])


def test_hero_park_urls_outdoor_card():
    from src.wanderly.hero_enrichment import _hero_fallback_urls

    dest = {
        "grounding": {"park_code": "brca", "status": "catalog", "trail_id": "x"},
        "id": "trail:x",
        "name": "Under-the-Rim",
    }
    with patch("src.wanderly.park_hero_catalog.offline_park_hero_url", return_value="https://cdn.example.com/brca.jpg"):
        urls = _hero_fallback_urls(dest, gallery_max=3)
    assert urls == ["https://cdn.example.com/brca.jpg"]
