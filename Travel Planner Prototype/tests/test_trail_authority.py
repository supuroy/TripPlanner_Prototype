"""Generic backpacking rules only — no catalog registry."""

from __future__ import annotations

from src.wanderly.trail_authority import (
    authoritative_trail_prompt_block,
    enforce_trail_authority_for_destination,
    enrich_destination_alltrails_authority,
)


def test_authoritative_prompt_includes_alltrails_context_block() -> None:
    block = authoritative_trail_prompt_block(
        {"name": "Heart Lake Trail", "hook": "Yellowstone", "country": "United States"}
    )
    assert "alltrails_search_context" in block.lower()
    assert "backpacking" in block.lower()


def test_enrich_stamps_alltrails_search_context() -> None:
    dest = enrich_destination_alltrails_authority(
        {"id": "heart-lake-trail", "name": "Heart Lake Trail", "country": "United States"}
    )
    ctx = dest.get("alltrails_search_context")
    assert isinstance(ctx, dict)
    assert ctx.get("trail_name")


def test_enrich_catalog_grounding_sets_parent_url() -> None:
    url = "https://www.alltrails.com/trail/us/california/bullfrog-lakes-via-farewell-gap"
    dest = enrich_destination_alltrails_authority(
        {
            "id": "seki:bullfrog-lakes",
            "name": "Bullfrog Lakes via Farewell Gap",
            "country": "United States",
            "grounding": {"status": "catalog", "source_url": url, "trail_id": "seki:bullfrog-lakes"},
        }
    )
    ctx = dest.get("alltrails_search_context")
    assert isinstance(ctx, dict)
    assert ctx.get("parent_alltrails_url") == url
    assert ctx.get("verified") is True


def test_point_to_point_return_warning() -> None:
    rows = [
        {
            "day": 1,
            "label": "Trailhead to Camp A",
            "day_logistics": {
                "surface_legs": [
                    {
                        "kind": "walk",
                        "from_label": "Dyea Trailhead",
                        "to_label": "Camp A",
                    }
                ]
            },
        },
        {
            "day": 2,
            "label": "Camp A to North Trailhead",
            "day_logistics": {
                "surface_legs": [
                    {
                        "kind": "walk",
                        "from_label": "Camp A",
                        "to_label": "Dyea",
                    }
                ]
            },
        },
    ]
    _, warnings = enforce_trail_authority_for_destination(rows, {"name": "Demo Trail"})
    assert any("trailhead" in w.lower() for w in warnings)
