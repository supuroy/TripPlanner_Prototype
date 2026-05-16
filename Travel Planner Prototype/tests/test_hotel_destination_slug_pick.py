"""Destination slug preference in hotel budget link reorder."""

from __future__ import annotations

from src.wanderly.ota_booking_search import (
    _destination_href_slug,
    _hotel_href_destination_penalty,
    _pick_hotel_link_for_budget,
)


def test_destination_slug_from_area() -> None:
    assert _destination_href_slug(area="Porto Moniz, Madeira, Portugal") == "porto-moniz"


def test_budget_pick_filters_to_local_hotels_when_slug_set() -> None:
    links = [
        {
            "href": "/Calheta-Hotels-Hotel-Jardim-Atlantico.h31956553.Hotel-Information",
            "text": "Calheta hotel",
        },
        {
            "href": "/Porto-Moniz-Hotels-Aqua-Natura-Bay.h2860664.Hotel-Information",
            "text": "Aqua Natura Bay",
        },
    ]
    html = "Calheta $218 nightly " + "x" * 200 + "Porto $343 nightly " + "y" * 200
    reordered, diag = _pick_hotel_link_for_budget(
        links,
        html,
        target_lo=180,
        target_hi=320,
        destination_slug="porto-moniz",
    )
    assert diag.get("destination_filter_applied")
    assert "Porto-Moniz-Hotels" in (reordered[0].get("href") or "")


def test_budget_pick_prefers_matching_destination_slug() -> None:
    links = [
        {
            "href": "/Calheta-Hotels-Hotel-Jardim-Atlantico.h31956553.Hotel-Information",
            "text": "Calheta hotel",
        },
        {
            "href": "/Porto-Moniz-Hotels-Aqua-Natura-Bay.h2860664.Hotel-Information",
            "text": "Aqua Natura Bay",
        },
    ]
    html = (
        "Calheta " + "$218 nightly " * 3 + "x" * 200
        + "Porto Moniz Aqua Natura $343 nightly " + "y" * 200
    )
    reordered, diag = _pick_hotel_link_for_budget(
        links,
        html,
        target_lo=180,
        target_hi=320,
        destination_slug="porto-moniz",
    )
    assert "Porto-Moniz-Hotels" in (reordered[0].get("href") or "")
    assert diag.get("winner_original_index") == 0
    assert diag.get("destination_filter_applied")


def test_href_penalty_values() -> None:
    assert _hotel_href_destination_penalty(
        "/Porto-Moniz-Hotels-X.h1.Hotel-Information", "porto-moniz"
    ) < 0
    assert _hotel_href_destination_penalty(
        "/Calheta-Hotels-X.h1.Hotel-Information", "porto-moniz"
    ) > 0
