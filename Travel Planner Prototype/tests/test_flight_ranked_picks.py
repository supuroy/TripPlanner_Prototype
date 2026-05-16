"""Ranked flight picks (top 3) for alternatives."""

from __future__ import annotations

from src.wanderly.flight_listing_alternatives import (
    attach_ranked_picks_to_flight_option,
    build_llm_ranked_flight_picks,
    ordered_listing_indices_from_rule_rows,
)


def test_ordered_indices_winner_first() -> None:
    rows = [
        {"i": 2, "score": 70.0, "usd": 412},
        {"i": 0, "score": 66.0, "usd": 445},
        {"i": 1, "score": 88.0, "usd": 389},
    ]
    ix = ordered_listing_indices_from_rule_rows(rows, winner_index=1)
    assert ix[0] == 1
    assert len(ix) == 3


def test_build_ranked_picks_and_selection_swap() -> None:
    listings = [
        {"airline": "Alaska", "price_display": "$412", "route": "SEA → SGU", "stops": "Nonstop"},
        {"airline": "Delta", "price_display": "$389", "route": "SEA → SGU", "stops": "Nonstop"},
        {"airline": "United", "price_display": "$445", "route": "SEA → SGU", "stops": "1 stop"},
    ]
    rule_rows = [
        {"i": 0, "score": 70.0, "usd": 412},
        {"i": 1, "score": 88.0, "usd": 389},
        {"i": 2, "score": 60.0, "usd": 445},
    ]
    picks = build_llm_ranked_flight_picks(listings, [1, 0, 2], rule_rows=rule_rows)
    assert len(picks) == 3
    assert picks[0]["rank"] == 1
    assert picks[0]["airline"] == "Delta"

    fo = attach_ranked_picks_to_flight_option({"best": True}, picks, selected_rank=1)
    assert fo["airline"] == "Delta"
    assert fo["selected_rank"] == 1

    fo2 = attach_ranked_picks_to_flight_option(fo, picks, selected_rank=2)
    assert fo2["airline"] == "Alaska"
    assert fo2["selected_rank"] == 2
