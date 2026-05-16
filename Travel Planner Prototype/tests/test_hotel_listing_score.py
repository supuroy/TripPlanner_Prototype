"""Tests for deterministic hotel listing scoring after LLM parse."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.wanderly.hotel_listing_score import (
    append_hotel_final_pick_log,
    nightly_band_from_budget_guidance,
    parse_price_display_usd,
    pick_hotel_listing_index_rule_based,
    score_hotel_listing_row,
)
from src.wanderly.ota_booking_search import _resolve_hotel_scoring_nightly_band


@pytest.fixture(autouse=True)
def _disable_hotel_rule_pick_file_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid appending to .cache/hotel_rule_pick.log during unit tests."""
    monkeypatch.setenv("BOOKING_SEARCH_HOTEL_RULE_PICK_FILE", "0")


def test_parse_price_display_usd() -> None:
    assert parse_price_display_usd("$189") == 189
    assert parse_price_display_usd("$1,234") == 1234
    assert parse_price_display_usd("  ") is None


def test_nightly_band_from_budget_guidance_lodging_short() -> None:
    bg = {"lodging_band_short": "mid guesthouse ~$90–180/n"}
    assert nightly_band_from_budget_guidance(bg) == (90, 180)


def test_nightly_band_from_budget_guidance_style_fallback() -> None:
    bg = {"lodging_style": "boutique $120-210 per night"}
    assert nightly_band_from_budget_guidance(bg) == (120, 210)


def test_pick_prefers_in_band_midpoint() -> None:
    rows = [
        {"name": "Lux", "price_display": "$899", "area_or_neighborhood": "", "listing_url": "https://www.expedia.com/h1"},
        {"name": "Mid", "price_display": "$165", "area_or_neighborhood": "", "listing_url": "https://www.expedia.com/h2"},
        {"name": "Budget", "price_display": "$62", "area_or_neighborhood": "", "listing_url": ""},
    ]
    idx, dbg = pick_hotel_listing_index_rule_based(rows, nightly_lo=90, nightly_hi=220, area_hint="")
    assert idx == 1
    assert dbg.get("picked_index") == 1


def test_pick_tie_prefers_lower_price() -> None:
    rows = [
        {"name": "A", "price_display": "$150", "listing_url": ""},
        {"name": "B", "price_display": "$150", "listing_url": ""},
    ]
    idx, _dbg = pick_hotel_listing_index_rule_based(rows, nightly_lo=100, nightly_hi=200, area_hint="")
    assert idx == 0


def test_pick_no_parseable_prices_returns_none() -> None:
    rows = [
        {"name": "A", "price_display": "See property"},
        {"name": "B", "price_display": ""},
    ]
    idx, dbg = pick_hotel_listing_index_rule_based(rows, nightly_lo=100, nightly_hi=200, area_hint="")
    assert idx is None
    assert dbg.get("reason") == "no_parseable_prices"


def test_area_overlap_bonus() -> None:
    row = {
        "name": "X",
        "price_display": "$160",
        "area_or_neighborhood": "Old Town waterfront",
        "listing_url": "https://www.expedia.com/x",
    }
    s0, _ = score_hotel_listing_row(row, nightly_lo=120, nightly_hi=200, area_hint="")
    s1, _ = score_hotel_listing_row(row, nightly_lo=120, nightly_hi=200, area_hint="waterfront")
    assert s1 > s0


def test_resolve_band_priority_search_intent() -> None:
    lo, hi, src = _resolve_hotel_scoring_nightly_band(
        hint_tuple=(110, 190),
        budget_guidance={"lodging_band_short": "$500-900/n"},
        profile_pills=[{"key": "budget", "value": "~$2-3k total"}],
        calendar_nights=7,
        adults=2,
    )
    assert (lo, hi, src) == (110, 190, "search_intent")


def test_resolve_falls_back_to_budget_guidance() -> None:
    lo, hi, src = _resolve_hotel_scoring_nightly_band(
        hint_tuple=None,
        budget_guidance={"lodging_band_short": "$95-175/n"},
        profile_pills=[],
        calendar_nights=5,
        adults=2,
    )
    assert (lo, hi, src) == (95, 175, "budget_guidance")


def test_resolve_profile_trip_derived() -> None:
    pills = [{"key": "budget", "value": "~$2-3k total"}]
    lo, hi, src = _resolve_hotel_scoring_nightly_band(
        hint_tuple=None,
        budget_guidance=None,
        profile_pills=pills,
        calendar_nights=7,
        adults=2,
    )
    assert src == "profile_trip_derived"
    assert lo is not None and hi is not None and hi > lo


def test_rule_pick_log_file_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule-pick block is appended when file logging is on and path is set."""
    logf = tmp_path / "hrp_test.log"
    monkeypatch.delenv("BOOKING_SEARCH_HOTEL_RULE_PICK_FILE", raising=False)
    monkeypatch.setenv("BOOKING_SEARCH_HOTEL_RULE_PICK_LOG_PATH", str(logf))
    rows = [
        {"name": "A", "price_display": "$150", "listing_url": "https://www.expedia.com/a"},
    ]
    idx, _dbg = pick_hotel_listing_index_rule_based(
        rows, nightly_lo=100, nightly_hi=200, band_source="test", session_id="sid-99"
    )
    assert idx == 0
    text = logf.read_text(encoding="utf-8")
    assert "hotel_rule_pick" in text
    assert "sid-99" in text
    assert "winner: index 0" in text


def test_hotel_final_pick_log_file_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[hotel_final] block is appended when file logging is on."""
    logf = tmp_path / "hrp_final_test.log"
    monkeypatch.delenv("BOOKING_SEARCH_HOTEL_RULE_PICK_FILE", raising=False)
    monkeypatch.setenv("BOOKING_SEARCH_HOTEL_RULE_PICK_LOG_PATH", str(logf))
    append_hotel_final_pick_log(
        selection_path="heuristic_snapshot (after LLM failure: empty_listings)",
        ho={
            "name": "Test Inn",
            "area": "Downtown",
            "price": "$120",
            "listing_url": "https://www.expedia.com/hotel.test",
        },
        hotel_profile={"name": {"source": "heuristic"}, "price": {"source": "heuristic"}},
        session_id="sid-final",
        dest_id="d1",
        hotel_index=0,
        dest_label="Paris",
        h_llm_meta={"llm_ok": False, "llm_err": "empty_listings"},
        heuristic_diag={
            "path": "heuristic_html_snapshot",
            "pick_summary": "Heuristic test summary.",
            "ordered_candidates": [
                {"final_rank": 0, "title": "Test Inn", "href": "https://www.expedia.com/hotel.test"},
                {"final_rank": 1, "title": "Other Hotel", "href": "https://www.expedia.com/hotel.other"},
            ],
            "budget_pick": {"applied": False, "rows": []},
            "price_tokens_sample": ["$120", "$99"],
        },
    )
    text = logf.read_text(encoding="utf-8")
    assert "[hotel_final]" in text
    assert "heuristic_snapshot" in text
    assert "Test Inn" in text
    assert "sid-final" in text
    assert "[candidates] HTML heuristic" in text
    assert "Other Hotel" in text


def test_hotel_final_pick_log_llm_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logf = tmp_path / "hrp_llm_final.log"
    monkeypatch.delenv("BOOKING_SEARCH_HOTEL_RULE_PICK_FILE", raising=False)
    monkeypatch.setenv("BOOKING_SEARCH_HOTEL_RULE_PICK_LOG_PATH", str(logf))
    append_hotel_final_pick_log(
        selection_path="llm_listings + rule_pick",
        ho={
            "name": "Winner Hotel",
            "llm_best_index": 1,
            "listings_parsed": 2,
            "listing_url": "https://www.expedia.com/winner",
        },
        session_id="sid-llm",
        dest_id="d9",
        hotel_index=0,
        dest_label="Rome",
        h_llm_meta={
            "llm_ok": True,
            "rule_pick": True,
            "pick_method": "rule_pick",
            "pick_summary": "rule_pick chose index 1.",
            "listings": [
                {"name": "A", "price_display": "$200", "area_or_neighborhood": "X", "listing_url": "http://a"},
                {"name": "Winner Hotel", "price_display": "$150", "area_or_neighborhood": "Y", "listing_url": "http://b"},
            ],
            "rule_pick_rows": [
                {"i": 0, "score": 10.0, "usd": 200, "name": "A", "url": "http://a"},
                {"i": 1, "score": 90.0, "usd": 150, "name": "Winner Hotel", "url": "http://b"},
            ],
        },
    )
    text = logf.read_text(encoding="utf-8")
    assert "[candidates] LLM-parsed rows" in text
    assert "[scores] rule-based scores" in text
    assert "<- card" in text
    assert "<- rule winner" in text


def test_hotel_final_pick_includes_considered_anchors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logf = tmp_path / "hrp_considered.log"
    monkeypatch.delenv("BOOKING_SEARCH_HOTEL_RULE_PICK_FILE", raising=False)
    monkeypatch.setenv("BOOKING_SEARCH_HOTEL_RULE_PICK_LOG_PATH", str(logf))
    append_hotel_final_pick_log(
        selection_path="llm_listings + rule_pick",
        ho={"name": "Winner", "listing_url": "https://www.expedia.com/a/X.h1.Hotel-Information?q=1"},
        session_id="sid-x",
        dest_id="d1",
        hotel_index=0,
        dest_label="Test",
        h_llm_meta={"llm_ok": True, "listings": [{"name": "Winner", "price_display": "$100"}], "rule_pick": True},
        page_anchor_candidates=[
            {"rank": 0, "title": "Other", "href": "/Z.h2.Hotel-Information"},
            {"rank": 1, "title": "Winner", "href": "/a/X.h1.Hotel-Information"},
        ],
    )
    text = logf.read_text(encoding="utf-8")
    assert "[considered]" in text
    assert "html_anchor_count=2" in text
    assert "llm_listing_row_count=1" in text
    assert "matches returned card URL" in text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
