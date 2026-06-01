"""Tests for AllTrails V2 verify + evidence-reading lookup (no live Apify, no URL guessing)."""

from __future__ import annotations

import pytest

import src.wanderly.alltrails_provider as ap
from src.wanderly.alltrails_provider import (
    _best_verified_match,
    build_trail_search_query,
    lookup_trail,
    normalize_trail_record,
    verify_parent_trail,
)

_WY = {
    "name": "Heart Lake Trail",
    "area": "Yellowstone National Park",
    "state": "Wyoming",
    "country": "United States",
    "url": "https://www.alltrails.com/trail/us/wyoming/heart-lake-trail",
    "id": "10012345",
    "latitude": 44.564,
    "longitude": -110.397,
    "rating": 4.6,
}
_CA = {
    "name": "Heart Lake Trail from Castle Lake",
    "state": "California",
    "url": "https://www.alltrails.com/trail/us/california/heart-lake-trail-from-castle-lake",
    "rating": 4.9,
}
_MT = {
    "name": "Heart Lake",
    "state": "Montana",
    "url": "https://www.alltrails.com/trail/us/montana/heart-lake",
    "rating": 4.8,
}
_BC = {
    "name": "Heart Lake Trail",
    "state": "British Columbia",
    "url": "https://www.alltrails.com/trail/canada/british-columbia/heart-lake",
    "rating": 4.7,
}


def test_build_trail_search_query_includes_park_context() -> None:
    q = build_trail_search_query(
        trail_name="Heart Lake Trail",
        from_label="Heart Lake TH",
        to_label="8H2 Sheridan Creek campground",
        destination_name="Heart Lake Trail",
        destination_country="United States",
        destination_id="heart-lake-trail",
    )
    assert "Heart Lake" in q


def test_normalize_trail_record() -> None:
    raw = {
        "name": "Mist Trail",
        "url": "https://www.alltrails.com/trail/us/california/mist",
        "lengthMiles": 7.0,
        "elevationGainFt": 2099,
        "difficulty": "Hard",
        "description": "Trail closed due to ice near the falls.",
    }
    out = normalize_trail_record(raw)
    assert out["name"] == "Mist Trail"
    assert out["length_mi"] == 7.0
    assert out["alerts"]


def test_best_verified_match_picks_wyoming_rejects_homonyms() -> None:
    items = [_CA, _MT, _BC, _WY]
    best, score = _best_verified_match(
        items, trail_name="Heart Lake Trail", expected_region_path="us/wyoming"
    )
    assert best is not None
    assert "us/wyoming" in (best.get("url") or "")
    assert score >= ap._VERIFY_MIN_SCORE


def test_best_verified_match_no_match_in_region() -> None:
    best, _ = _best_verified_match(
        [_CA, _MT, _BC], trail_name="Heart Lake Trail", expected_region_path="us/wyoming"
    )
    assert best is None


def test_best_verified_match_rejects_monument_hill_for_heart_lake() -> None:
    monument = {
        "name": "Monument Hill Trail",
        "url": "https://www.alltrails.com/trail/us/wyoming/monument-hill",
        "state": "Wyoming",
        "area": "Hot Springs State Park",
    }
    best, _ = _best_verified_match(
        [monument], trail_name="Heart Lake Trail", expected_region_path="us/wyoming"
    )
    assert best is None


def test_verify_parent_trail_direct_catalog_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """First strategy fetches the canonical trail page — not only park-wide search."""
    monkeypatch.setattr(ap, "apify_token", lambda: "tok")
    calls: list[dict] = []

    def fake_search(run_input, **kw):
        calls.append(run_input)
        if run_input.get("startUrl", "").endswith("heart-lake-trail"):
            return [_WY]
        return [_CA, _MT, _BC]

    monkeypatch.setattr(ap, "_run_apify_search", fake_search)
    res = verify_parent_trail(
        trail_name="Heart Lake Trail",
        park_name="Yellowstone National Park",
        region="Wyoming",
        country="United States",
        destination_id="heart-lake-trail",
    )
    assert res["status"] == "verified"
    assert "heart-lake-trail" in (res["parent_alltrails_url"] or "")
    assert len(calls) == 1
    assert calls[0].get("startUrl", "").endswith("heart-lake-trail")


def test_verify_parent_trail_matches_wyoming(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap, "apify_token", lambda: "tok")
    calls: list[dict] = []

    def fake_search(run_input, **kw):
        calls.append(run_input)
        return [_CA, _MT, _BC, _WY]

    monkeypatch.setattr(ap, "_run_apify_search", fake_search)
    res = verify_parent_trail(
        trail_name="Heart Lake Trail",
        park_name="Yellowstone National Park",
        region="Wyoming",
        country="United States",
        search_query="Heart Lake Trail Yellowstone Wyoming",
    )
    assert res["status"] == "verified"
    assert "us/wyoming" in (res["parent_alltrails_url"] or "")
    assert res["alltrails_trail_id"] == "10012345"
    # First successful strategy short-circuits — well under the hard cap.
    assert len(calls) <= ap._MAX_VERIFY_ACTOR_CALLS


def test_verify_parent_trail_caps_actor_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap, "apify_token", lambda: "tok")
    calls: list[dict] = []

    def fake_search(run_input, **kw):
        calls.append(run_input)
        return [_CA, _MT, _BC]  # never a Wyoming match

    monkeypatch.setattr(ap, "_run_apify_search", fake_search)
    res = verify_parent_trail(
        trail_name="Heart Lake Trail",
        park_name="Yellowstone National Park",
        region="Wyoming",
        country="United States",
        anchor_latitude=44.5,
        anchor_longitude=-110.4,
    )
    assert res["status"] == "unverified"
    assert len(calls) <= ap._MAX_VERIFY_ACTOR_CALLS


def test_verify_provider_unavailable_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap, "apify_token", lambda: "")
    res = verify_parent_trail(trail_name="Heart Lake Trail", region="Wyoming", country="United States")
    assert res["status"] == "provider_unavailable"


def _verified_dest() -> dict:
    return {
        "id": "heart-lake-trail",
        "name": "Heart Lake Trail",
        "country": "United States",
        "alltrails_search_context": {
            "trail_name": "Heart Lake Trail",
            "park_name": "Yellowstone National Park",
            "region": "Wyoming",
            "country": "United States",
            "verified": True,
            "verification_status": "verified",
            "parent_alltrails_url": "https://www.alltrails.com/trail/us/wyoming/heart-lake-trail",
            "anchor_latitude": 44.564,
            "anchor_longitude": -110.397,
        },
    }


def test_lookup_reads_verified_evidence_no_actor_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise AssertionError("Apify must not be called when verified evidence exists")

    monkeypatch.setattr(ap, "_run_apify_search", boom)
    res = lookup_trail(
        trail_name="Heart Lake Trail",
        from_label="Heart Lake TH",
        to_label="8H2 Sheridan Creek campground",
        destination=_verified_dest(),
        expected_distance_mi=9.1,
        use_cache=False,
    )
    assert res["ok"] is True
    assert res["verified"] is True
    assert res["verification_status"] == "verified"
    assert "us/wyoming" in (res["parent_trail_url"] or "")
    assert res["trail"]["length_mi"] == 9.1


def test_verified_parent_reused_across_legs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap, "_run_apify_search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no actor")))
    dest = _verified_dest()
    legs = [
        ("Heart Lake TH", "8H2 Sheridan Creek", 9.1),
        ("8H2 Sheridan Creek", "8C6 Snake River Ford", 6.4),
    ]
    urls = set()
    for fr, to, dist in legs:
        res = lookup_trail(
            trail_name="Heart Lake Trail",
            from_label=fr,
            to_label=to,
            destination=dest,
            expected_distance_mi=dist,
            use_cache=False,
        )
        assert res["verified"] is True
        urls.add(res["parent_trail_url"])
    assert urls == {"https://www.alltrails.com/trail/us/wyoming/heart-lake-trail"}


def test_lookup_unverified_fallback_is_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap, "apify_token", lambda: "tok")
    monkeypatch.setattr(ap, "_run_apify_search", lambda *a, **k: [_CA, _MT, _BC])
    dest = {
        "id": "heart-lake-trail",
        "name": "Heart Lake Trail",
        "country": "United States",
        "alltrails_search_context": {
            "trail_name": "Heart Lake Trail",
            "park_name": "Yellowstone National Park",
            "region": "Wyoming",
            "country": "United States",
            "search_query": "Heart Lake Trail Yellowstone Wyoming",
        },
    }
    res = lookup_trail(
        trail_name="Heart Lake Trail",
        from_label="Heart Lake TH",
        to_label="8H2 Sheridan Creek campground",
        destination=dest,
        expected_distance_mi=9.1,
        use_cache=False,
    )
    assert res["ok"] is True
    assert res["verified"] is False
    assert res["verification_status"] == "unverified"
    assert res["trail"]["length_mi"] == 9.1
    assert res["trail"]["name"]


def test_lookup_provider_unavailable_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap, "apify_token", lambda: "")
    dest = {
        "id": "heart-lake-trail",
        "name": "Heart Lake Trail",
        "alltrails_search_context": {
            "trail_name": "Heart Lake Trail",
            "region": "Wyoming",
            "country": "United States",
            "search_query": "Heart Lake Trail Yellowstone Wyoming",
        },
    }
    res = lookup_trail(
        trail_name="Heart Lake Trail",
        destination=dest,
        expected_distance_mi=9.1,
        use_cache=False,
    )
    assert res["ok"] is True
    assert res["verified"] is False
    assert res["verification_status"] == "provider_unavailable"
