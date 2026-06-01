"""Park-aware stock hero queries for in-park hike cards."""

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


def test_hero_urls_from_trail_lookups():
    from src.wanderly.hero_enrichment import _hero_urls_from_trail_lookups

    dest = {
        "trail_lookups": {
            "leg-1": {"image_url": "https://cdn.alltrails.com/example.jpg"},
        },
    }
    urls = _hero_urls_from_trail_lookups(dest)
    assert urls == ["https://cdn.alltrails.com/example.jpg"]
