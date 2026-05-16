"""Heuristic budget pick uses unified price + rating + review scoring."""

from __future__ import annotations

from src.wanderly.hotel_listing_extract import extract_guest_rating_reviews_near_href
from src.wanderly.ota_booking_search import _pick_hotel_link_for_budget

_CARD = """
<a href="/Porto-Moniz-Hotels-Cheap-Stay.h100.Hotel-Information?chkin=2026-05-30">
Cheap Stay
</a>
<span class="uitk-badge-base-text" aria-hidden="true">8.5</span>
<span class="is-visually-hidden">8.5 out of 10</span>
<span aria-hidden="true">3 reviews</span>
<span aria-hidden="true">$200 nightly</span>
<a href="/Porto-Moniz-Hotels-Better-Stay.h200.Hotel-Information?chkin=2026-05-30">
Better Stay
</a>
<span class="uitk-badge-base-text" aria-hidden="true">9.6</span>
<span class="is-visually-hidden">9.6 out of 10</span>
<span aria-hidden="true">120 reviews</span>
<span aria-hidden="true">$215 nightly</span>
"""


def test_budget_pick_prefers_higher_rating_at_similar_price() -> None:
    links = [
        {"href": "/Porto-Moniz-Hotels-Cheap-Stay.h100.Hotel-Information", "text": "Cheap Stay"},
        {"href": "/Porto-Moniz-Hotels-Better-Stay.h200.Hotel-Information", "text": "Better Stay"},
    ]
    reordered, diag = _pick_hotel_link_for_budget(
        links,
        _CARD,
        target_lo=180,
        target_hi=320,
        destination_slug="porto-moniz",
        area_hint="Porto Moniz",
    )
    assert diag.get("scoring_method") == "unified_rule_score"
    assert "Better-Stay" in (reordered[0].get("href") or "")
    rows = diag.get("rows") or []
    assert len(rows) >= 2
    assert float(rows[1]["score"]) > float(rows[0]["score"])


def test_extract_and_score_fields_present_in_diag_rows() -> None:
    href = "/Porto-Moniz-Hotels-Better-Stay.h200.Hotel-Information"
    ext = extract_guest_rating_reviews_near_href(_CARD, href)
    assert ext.get("guest_rating") == 9.6
    assert isinstance(ext.get("review_count"), int)
