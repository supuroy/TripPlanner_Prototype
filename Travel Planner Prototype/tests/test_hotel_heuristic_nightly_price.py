"""Heuristic hotel card price: prefer per-night near property anchor (not global SRP totals)."""

from __future__ import annotations

from pathlib import Path

from src.wanderly.ota_booking_search import (
    _heuristic_listing_nightly_usd_hint,
    _hotel_option_from_snapshot,
)


_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hotel_srp_next_savoy_prices.html"


def test_nightly_hint_prefers_nightly_label_over_median_noise() -> None:
    html = _FIXTURE.read_text(encoding="utf-8")
    href = "/Funchal-Hotels-NEXT.h71119006.Hotel-Information?chkin=2026-09-15&chkout=2026-09-19&x_pwa=1"
    hint = _heuristic_listing_nightly_usd_hint(html, href, tl=180, th=320, exclude=set())
    assert hint == 447


def test_nightly_hint_without_label_uses_band_cap_not_median_of_totals() -> None:
    """When no ``nightly`` label, drop amounts above soft nightly cap vs intent band."""
    html = """<html><body>
<a href="/X.h1.Hotel-Information">Hotel</a>
<div>$1,170 $447 $2,215 total with taxes and fees</div>
</body></html>"""
    hint = _heuristic_listing_nightly_usd_hint(html, "/X.h1.Hotel-Information", tl=180, th=320, exclude=set())
    assert hint == 447


def test_hotel_option_from_snapshot_fixture_price() -> None:
    html = _FIXTURE.read_text(encoding="utf-8")
    opt, prof, _diag = _hotel_option_from_snapshot(
        html,
        area="Funchal",
        nights=4,
        image_fallback="",
        dest_name="Madeira",
        dest_country="Portugal",
        exclude_price_values=set(),
        target_nightly_usd=(180, 320),
    )
    assert "NEXT" in (opt.get("name") or "")
    assert str(opt.get("price") or "").startswith("$447")
    assert prof.get("price", {}).get("source") == "listing_nightly_near_anchor"
