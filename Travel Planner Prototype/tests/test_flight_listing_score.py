"""Flight listing rule-based scoring."""

from __future__ import annotations

from src.wanderly.flight_listing_score import pick_flight_listing_index_rule_based, score_flight_listing_row


def test_score_prefers_in_band_nonstop() -> None:
    in_band_nonstop = {
        "price_display": "$400",
        "stops": "Nonstop",
        "departure_time": "8:00am",
        "arrival_time": "11:00am",
    }
    expensive = {"price_display": "$900", "stops": "Nonstop"}
    sc1, _ = score_flight_listing_row(in_band_nonstop, price_lo=350, price_hi=450)
    sc2, _ = score_flight_listing_row(expensive, price_lo=350, price_hi=450)
    assert sc1 > sc2


def test_duration_bonus_prefers_shorter() -> None:
    short = {"price_display": "$400", "stops": "Nonstop", "duration": "2h 15m"}
    long = {"price_display": "$400", "stops": "Nonstop", "duration": "14h 30m"}
    sc_s, _ = score_flight_listing_row(short, price_lo=350, price_hi=450)
    sc_l, _ = score_flight_listing_row(long, price_lo=350, price_hi=450)
    assert sc_s > sc_l


def test_pick_winner_mid_band() -> None:
    listings = [
        {"airline": "A", "price_display": "$900", "stops": "Nonstop", "duration": "2h"},
        {"airline": "B", "price_display": "$400", "stops": "Nonstop", "duration": "2h 10m"},
        {"airline": "C", "price_display": "$380", "stops": "1 stop", "duration": "2h"},
    ]
    idx, dbg = pick_flight_listing_index_rule_based(listings, price_lo=350, price_hi=450)
    assert idx == 1
    assert dbg.get("winner_airline") == "B"
    assert len(dbg.get("rows") or []) == 3


def test_relative_pick_prefers_nonstop_shorter() -> None:
    from src.wanderly.flight_listing_score import pick_flight_listing_index

    listings = [
        {"airline": "Cheap long", "price_display": "$350", "stops": "2 stops", "duration": "18h"},
        {"airline": "Best", "price_display": "$420", "stops": "Nonstop", "duration": "2h 30m"},
    ]
    idx, _ = pick_flight_listing_index(listings, price_lo=None, price_hi=None)
    assert idx == 1
