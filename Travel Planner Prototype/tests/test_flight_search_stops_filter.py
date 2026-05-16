"""Flight URL filters: default any stops (no hard nonstop on catalog URLs)."""

from __future__ import annotations

import json

from src.wanderly.ota_booking_search import (
    _apply_flight_search_intent_pairs,
    _update_flight_filters_json,
    _update_flight_options_for_stops,
)

_CATALOG_FILTERS = (
    '[{"numOfStopFilterValue":{"stopInfo":{"numberOfStops":0,"stopFilterOperation":"EQUAL"}}},'
    '{"preferredCabinFilterValue":{"cabinCode":"BASIC_COACH"}}]'
)


def test_strips_catalog_nonstop_when_stops_any() -> None:
    out = _update_flight_filters_json(_CATALOG_FILTERS, {"stops": "any"})
    arr = json.loads(out)
    assert not any("numOfStopFilterValue" in x for x in arr if isinstance(x, dict))


def test_keeps_nonstop_only_when_explicit() -> None:
    out = _update_flight_filters_json(_CATALOG_FILTERS, {"stops": "nonstop"})
    arr = json.loads(out)
    assert any("numOfStopFilterValue" in x for x in arr if isinstance(x, dict))


def test_default_empty_intent_strips_nonstop() -> None:
    out = _update_flight_filters_json(_CATALOG_FILTERS, {})
    arr = json.loads(out)
    assert not any("numOfStopFilterValue" in x for x in arr if isinstance(x, dict))


def test_options_relax_maxhops_for_any() -> None:
    opts = "carrier:,cabinclass:,maxhops:1,nopenalty:N"
    assert "maxhops:3" in _update_flight_options_for_stops(opts, "any")
    assert "maxhops:1" in _update_flight_options_for_stops(opts, "max_1_stop")


def test_apply_pairs_without_flight_intent_strips_nonstop() -> None:
    pairs = [
        ("filters", _CATALOG_FILTERS),
        ("options", "carrier:,cabinclass:,maxhops:1,nopenalty:N"),
        ("trip", "oneway"),
    ]
    out = dict(_apply_flight_search_intent_pairs(pairs, None))
    arr = json.loads(out["filters"])
    assert not any("numOfStopFilterValue" in x for x in arr if isinstance(x, dict))
    assert "maxhops:3" in out["options"]
