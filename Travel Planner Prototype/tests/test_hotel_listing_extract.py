"""Tests for guest rating / review extraction from hotel SRP HTML."""

from __future__ import annotations

from src.wanderly.hotel_listing_extract import (
    enrich_hotel_listing_row_from_html,
    extract_guest_rating_reviews_near_href,
    extract_listing_image_near_href,
    is_placeholder_hotel_image_url,
)

_CARD = """
<div class="listing">
<a href="/Porto-Moniz-Hotels-Aqua-Natura-Bay.h2860664.Hotel-Information?chkin=2026-05-30">
Aqua Natura Bay
</a>
<span class="uitk-badge uitk-badge-base-large uitk-badge-positive">
<span class="uitk-badge-base-text" aria-hidden="true">9.4</span>
<span class="is-visually-hidden">9.4 out of 10</span>
</span>
<span aria-hidden="true">Wonderful</span>
<span aria-hidden="true">62 reviews</span>
<span aria-hidden="true">$343 nightly</span>
</div>
"""


def test_extract_guest_rating_and_reviews_near_href() -> None:
    out = extract_guest_rating_reviews_near_href(_CARD, "/Porto-Moniz-Hotels-Aqua-Natura-Bay.h2860664.Hotel-Information")
    assert out.get("guest_rating") == 9.4
    assert out.get("review_count") == 62
    assert out.get("rating_label") == "Wonderful"


def test_enrich_listing_row_from_html() -> None:
    row = {"name": "Aqua", "listing_url": "https://www.expedia.com/Porto-Moniz-Hotels-Aqua-Natura-Bay.h2860664.Hotel-Information"}
    out = enrich_hotel_listing_row_from_html(row, _CARD)
    assert out["guest_rating"] == 9.4
    assert out["review_count"] == 62


def test_extract_listing_image_near_href_trvl_media() -> None:
    html = """
    <a href="/Tofino-Hotels-Canadian-Princess-Lodge-Marina.h8166080.Hotel-Information">Canadian Princess</a>
    <img src="https://images.trvl-media.com/lodging/58000000/57410000/57403400/57403301/9423bf14.jpg?impolicy=resizecrop&ra=fit&rw=455&rh=455" />
    """
    url = extract_listing_image_near_href(
        html,
        "https://www.expedia.com/Tofino-Hotels-Canadian-Princess-Lodge-Marina.h8166080.Hotel-Information",
    )
    assert "trvl-media.com" in url
    assert is_placeholder_hotel_image_url("https://placehold.co/800x500/1a1a24/99aacc?text=Tofino")
