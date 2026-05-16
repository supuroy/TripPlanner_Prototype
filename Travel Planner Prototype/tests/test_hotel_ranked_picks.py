"""Ranked hotel picks (top 3) for UI alternatives."""

from __future__ import annotations

from src.wanderly.hotel_listing_alternatives import (
    attach_ranked_picks_to_hotel_option,
    build_llm_ranked_picks,
    ordered_listing_indices_from_rule_rows,
)


def test_ordered_indices_winner_first() -> None:
    rows = [
        {"i": 2, "score": 70.0, "usd": 162},
        {"i": 0, "score": 66.0, "usd": 343},
        {"i": 1, "score": 56.0, "usd": 94},
    ]
    ix = ordered_listing_indices_from_rule_rows(rows, winner_index=0)
    assert ix[0] == 0
    assert len(ix) == 3
    assert set(ix) == {0, 1, 2}


def test_build_llm_ranked_picks_and_selection_swap() -> None:
    listings = [
        {
            "name": "Hotel A",
            "price_display": "$200",
            "area_or_neighborhood": "City",
            "listing_url": "https://www.expedia.com/a.h1.Hotel-Information",
        },
        {
            "name": "Hotel B",
            "price_display": "$150",
            "area_or_neighborhood": "City",
            "listing_url": "https://www.expedia.com/b.h2.Hotel-Information",
        },
        {
            "name": "Hotel C",
            "price_display": "$180",
            "area_or_neighborhood": "City",
            "listing_url": "https://www.expedia.com/c.h3.Hotel-Information",
        },
    ]
    picks = build_llm_ranked_picks(listings, [0, 1, 2], nights=2, area_hint="City, Country")
    assert len(picks) == 3
    assert picks[0]["rank"] == 1
    assert picks[0]["name"] == "Hotel A"

    ho = attach_ranked_picks_to_hotel_option(
        {"nights": 2, "best": True},
        picks,
        selected_rank=1,
    )
    assert ho["name"] == "Hotel A"
    assert ho["selected_rank"] == 1
    assert len(ho["ranked_picks"]) == 3

    ho2 = attach_ranked_picks_to_hotel_option(ho, picks, selected_rank=2)
    assert ho2["name"] == "Hotel B"
    assert ho2["selected_rank"] == 2
    assert ho2["ranked_picks"][1]["name"] == "Hotel B"
