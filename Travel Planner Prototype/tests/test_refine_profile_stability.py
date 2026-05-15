"""Refine-turn profile pill stability (itinerary-only messages should not churn traveler pills)."""

from __future__ import annotations

from src.wanderly.smoke_service import itinerary_primary_refine_message


def test_itinerary_primary_refresh_cards() -> None:
    assert itinerary_primary_refine_message("Please refresh the itinerary for each destination card")


def test_itinerary_primary_reverse_word_order() -> None:
    assert itinerary_primary_refine_message("Itinerary: please refresh all day-by-day plans")


def test_not_itinerary_primary_when_explicit_profile_edit() -> None:
    assert not itinerary_primary_refine_message(
        "Refresh the itinerary and update my vibe to pure beach relax"
    )


def test_not_itinerary_primary_budget_change() -> None:
    assert not itinerary_primary_refine_message("Refresh cards and set budget to $4000")


def test_not_itinerary_primary_vibe_only() -> None:
    assert not itinerary_primary_refine_message("Change my vibe to foodie cities")


def test_not_itinerary_primary_empty_message() -> None:
    assert not itinerary_primary_refine_message("")
    assert not itinerary_primary_refine_message("   ")
