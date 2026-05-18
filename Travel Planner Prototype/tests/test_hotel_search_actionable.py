"""Hotel search destination + actionable result guards."""

from __future__ import annotations

from src.wanderly.ota_booking_search import (
    _card_hotels_look_like_stay_plan,
    _hotel_area_is_ota_result_noise,
    _hotel_option_is_actionable,
    _hotel_search_destination_label,
    _resolve_hotel_stay_search_area,
    resolve_booking_hotels_plan,
)


def test_hotel_area_noise_rejected_for_destination_label() -> None:
    dest = {"name": "Tofino", "country": "Canada"}
    label = _hotel_search_destination_label(dest, "0.53 mi from address")
    assert label == "Tofino, Canada"
    assert "mi from" not in label.lower()


def test_hotel_option_not_actionable_for_heuristic_shell() -> None:
    ho = {
        "name": "Lodging results",
        "area": "0.53 mi from address",
        "price": "$400",
        "listing_url": "",
        "nights": 1,
    }
    assert not _hotel_option_is_actionable(ho)
    assert _hotel_area_is_ota_result_noise("0.53 mi from address")


def test_hotel_option_actionable_with_listing() -> None:
    ho = {
        "name": "Travelodge by Wyndham Nanaimo",
        "area": "Nanaimo",
        "price": "$87 nightly",
        "listing_url": "https://www.expedia.com/Nanaimo-Hotels-Example.h1.Hotel-Information",
        "nights": 1,
    }
    assert _hotel_option_is_actionable(ho)


def test_hotel_option_actionable_with_srp_distance_area() -> None:
    """Expedia neighborhood distance on the card must not block actionable results."""
    ho = {
        "name": "Travelodge by Wyndham Nanaimo",
        "area": "0.53 mi from address",
        "price": "$87 nightly",
        "listing_url": "https://www.expedia.com/Nanaimo-Hotels-Example.h1.Hotel-Information",
        "nights": 1,
    }
    assert _hotel_option_is_actionable(ho)


def test_resolve_hotel_stay_search_area_from_lodging_moves() -> None:
    dest = {
        "name": "Tofino",
        "country": "Canada",
        "transport_plan": {
            "primary_mode": "mixed",
            "lodging_moves": [
                {"day_range": "1", "area": "Nanaimo"},
                {"day_range": "2", "area": "Tofino"},
            ],
        },
    }
    plan = [{"area": "0.53 mi from address", "nights": 1}, {"area": "Tofino", "nights": 1}]
    assert _resolve_hotel_stay_search_area(dest, plan, 0, "Tofino") == "Nanaimo"


def test_card_hotels_with_ota_merge_not_stay_plan() -> None:
    merged = [
        {
            "name": "Travelodge by Wyndham Nanaimo",
            "area": "0.53 mi from address",
            "price": "$87 nightly",
            "listing_url": "https://www.expedia.com/x",
            "nights": 1,
        },
        {"name": "Stay 2", "area": "Tofino", "nights": 1},
    ]
    assert not _card_hotels_look_like_stay_plan(merged)


def test_resolve_hotels_skips_contaminated_card_multi() -> None:
    dest = {
        "id": "tofino-bc",
        "name": "Tofino",
        "country": "Canada",
        "hotels": [
            {
                "name": "Travelodge by Wyndham Nanaimo",
                "area": "0.53 mi from address",
                "price": "$87 nightly",
                "listing_url": "https://www.expedia.com/x",
                "nights": 1,
            },
            {
                "name": "Planned stay 2",
                "area": "Tofino",
                "price": "Searching Expedia…",
                "nights": 1,
            },
        ],
    }
    rows, src = resolve_booking_hotels_plan(
        dest,
        [],
        [],
        trip_nights=2,
        start_iso="2026-05-21",
        end_iso="2026-05-22",
        session_id=None,
    )
    assert src != "card_multi_fitted"
    assert rows
    assert not _hotel_area_is_ota_result_noise(str(rows[0].get("area") or ""))
