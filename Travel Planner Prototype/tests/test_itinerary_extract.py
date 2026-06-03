"""Regression tests for lazy itinerary JSON extraction / normalization."""

from __future__ import annotations

import pytest

from src.wanderly.itinerary_lazy import _extract_itinerary_payload, _normalize_itinerary_rows


def test_extract_trunc_repair_kyoto_style_cutoff() -> None:
    """Regression: Kyoto logs showed wrapper_present=0 and DROPPED_ALL on day-1 items strings."""
    raw = (
        '{"itinerary":[{"day":1,"label":"Inbound sakura week","base":"Central Kyoto",'
        '"items":["Fly Seattle to Kansai","Train to Kyoto hotel","Gentle Kamogawa walk",'
        '"Izakaya near Kawaramachi","Rest after the long haul"],'
        '"day_transport":{"mode":"train","note":"Haruka or JR rapid; IC card for city legs."},'
        '"day_logistics":{"flight":{"segments":[{"from_search_label":"Seattle, Washington, United States",'
        '"to_search_label":"Osaka, Kansai, Japan","role":"inbound"}]},"lodging":{"destination_search_label":"Kyoto, K'
    )
    rows, _bg, _bh, _ncf, tp, _hooks, wrap = _extract_itinerary_payload(raw)
    assert len(rows) >= 1
    assert isinstance(rows[0], dict)
    assert rows[0].get("day") == 1
    assert _normalize_itinerary_rows(rows, max_days=6)
    assert wrap is not None or tp is not None or len(rows) >= 1


def test_extract_prefers_day_rows_over_nested_items_string_array() -> None:
    """Regression: Kyoto rebuilds parsed day-1 ``items`` strings as the itinerary (EMPTY / DROPPED_ALL)."""
    items_only = '["Fly Seattle to Kansai", "Train into Kyoto", "Walk", "Dinner", "Sleep"]'
    full = (
        '{"itinerary": ['
        '{"day": 1, "label": "Inbound to Kyoto", "items": ["Fly Seattle to Kansai", "Train into Kyoto"]},'
        '{"day": 2, "label": "Temples", "items": ["Kiyomizu", "Higashiyama"]}'
        '], "transport_plan": {"primary_mode": "mixed", "summary": "Rail + air", '
        '"commercial_flight_segments": []}}'
    )
    raw = f"fragment with {items_only} then valid {full}"
    rows, _bg, _bh, _ncf, tp, _hooks, wrap = _extract_itinerary_payload(raw)
    assert len(rows) == 2
    assert isinstance(rows[0], dict)
    assert rows[0].get("day") == 1
    assert wrap is not None
    assert tp is not None


def test_extract_preamble_before_object() -> None:
    raw = 'Sure — here you go.\n\n{"itinerary": [{"day": 1, "label": "Arrive", "items": ["a", "b"]}], "budget_guidance": {}}'
    rows, bg, bh, ncf, _tp, _hooks, _wrap = _extract_itinerary_payload(raw)
    assert len(rows) == 1
    assert bg is None  # empty budget object normalizes to None
    assert bh is None
    assert ncf is None


def test_extract_booking_search_hints_optional() -> None:
    raw = (
        '{"itinerary": [{"day": 1, "label": "Arrive", "items": ["a", "b"]}], '
        '"budget_guidance": {}, '
        '"booking_search_hints": {"flight_to_label": "Florence, Italy", "flight_return_from_label": "Florence, Italy"}}'
    )
    rows, bg, bh, ncf, _tp, _hooks, _wrap = _extract_itinerary_payload(raw)
    assert len(rows) == 1
    assert bh is not None
    assert bh.get("flight_to_label") == "Florence, Italy"
    assert ncf is None


def test_extract_needs_commercial_flight() -> None:
    raw = (
        '{"itinerary": [{"day": 1, "label": "Drive", "items": ["Car"]}], "budget_guidance": {}, '
        '"needs_commercial_flight": false}'
    )
    rows, _bg, _bh, ncf, _tp, _hooks, _wrap = _extract_itinerary_payload(raw)
    assert len(rows) == 1
    assert ncf is False


def test_extract_pretty_printed_wrapped_itinerary() -> None:
    raw = """{   "itinerary": [
     { "day": 1, "label": "Day one", "items": ["x", "y"] }
   ], "budget_guidance": {}
}"""
    rows, _bg, _bh, _ncf, _tp, _hooks, _wrap = _extract_itinerary_payload(raw)
    assert len(rows) == 1


def test_extract_transport_plan_ground_only() -> None:
    raw = (
        '{"itinerary": [{"day": 1, "label": "Drive", "items": ["Car"]}], "budget_guidance": {}, '
        '"transport_plan": {"primary_mode": "ground_only", "summary": "Road trip", '
        '"commercial_flight_segments": []}}'
    )
    rows, _bg, _bh, _ncf, tp, _hooks, _wrap = _extract_itinerary_payload(raw)
    assert len(rows) == 1
    assert tp is not None
    assert tp.get("primary_mode") == "ground_only"
    assert tp.get("commercial_flight_segments") == []


def test_extract_transport_alias_under_transport_key() -> None:
    raw = (
        '{"itinerary": [{"day": 1, "label": "Day 1", "items": ["BC Ferries"]}], '
        '"transport": {"primary_mode": "ground_only", "summary": "Ferry", "commercial_flight_segments": []}}'
    )
    _rows, _bg, _bh, _ncf, tp, _hooks, _wrap = _extract_itinerary_payload(raw)
    assert tp is not None
    assert tp.get("primary_mode") == "ground_only"


def test_extract_transport_plan_salvages_docstring_style_primary_mode() -> None:
    raw = (
        '{"itinerary": [{"day": 1, "label": "x", "items": ["y"]}], '
        '"transport_plan": {"primary_mode": "ground_only | commercial_air | mixed", '
        '"summary": "test", "commercial_flight_segments": []}}'
    )
    _rows, _bg, _bh, _ncf, tp, _hooks, _wrap = _extract_itinerary_payload(raw)
    assert tp is not None
    assert tp.get("primary_mode") == "ground_only"


def test_ensure_transport_plan_infers_ground_from_itinerary() -> None:
    from src.wanderly.itinerary_lazy import ensure_transport_plan_after_extract

    rows = [
        {
            "day": 1,
            "label": "Ferry",
            "base": "Nanaimo",
            "items": ["BC Ferries Horseshoe Bay to Departure Bay", "Drive Highway 4"],
        }
    ]
    tp, ncf, hints = ensure_transport_plan_after_extract(
        transport_plan=None,
        needs_commercial_flight=None,
        booking_search_hints={"flight_to_label": "X"},
        budget_guidance={"flight_ballpark": "no flights assumed"},
        itinerary_rows=rows,
        destination={"name": "Tofino", "hook": "Memorial Day road trip"},
        profile_pills=[{"key": "from", "value": "Seattle, WA (road trip start)"}],
        raw_wrapper=None,
    )
    assert tp is not None
    assert tp.get("primary_mode") == "ground_only"
    assert ncf is False
    assert hints is None


def test_ensure_transport_plan_reconciles_day_logistics_flights() -> None:
    """Regression: Bangkok card had day_logistics flights but ground_only transport_plan."""
    from src.wanderly.itinerary_lazy import ensure_transport_plan_after_extract

    rows = [
        {
            "day": 1,
            "label": "Arrival",
            "items": ["Check in"],
            "day_transport": {"mode": "taxi", "note": "Airport link"},
            "day_logistics": {
                "flight": {
                    "segments": [
                        {
                            "from_search_label": "Seattle, WA, United States",
                            "to_search_label": "Bangkok, Bangkok Province, Thailand",
                            "role": "inbound",
                        }
                    ]
                }
            },
        },
        {
            "day": 10,
            "label": "Departure",
            "items": ["Fly home"],
            "day_logistics": {
                "flight": {
                    "segments": [
                        {
                            "from_search_label": "Bangkok, Bangkok Province, Thailand",
                            "to_search_label": "Seattle, WA, United States",
                            "role": "return",
                        }
                    ]
                }
            },
        },
    ]
    tp, ncf, hints = ensure_transport_plan_after_extract(
        transport_plan={
            "primary_mode": "ground_only",
            "summary": "Ground-based travel (inferred from itinerary; model omitted transport_plan).",
            "commercial_flight_segments": [],
        },
        needs_commercial_flight=False,
        booking_search_hints=None,
        budget_guidance=None,
        itinerary_rows=rows,
        destination={"name": "Bangkok", "country": "Thailand"},
        profile_pills=[{"key": "from", "value": "Seattle, WA"}],
        raw_wrapper=None,
    )
    assert tp is not None
    assert tp.get("primary_mode") in ("commercial_air", "mixed")
    assert len(tp.get("commercial_flight_segments") or []) == 2
    assert ncf is True
    assert hints is not None
    assert hints.get("flight_to_label")


def test_refine_mixed_empty_coerces_with_movement_blob() -> None:
    from src.wanderly.booking_hooks import normalize_transport_plan, refine_transport_plan_with_movement_text

    tp = normalize_transport_plan(
        {"primary_mode": "mixed", "summary": "Island loop", "commercial_flight_segments": []}
    )
    assert tp is not None
    tp2 = refine_transport_plan_with_movement_text(tp, "BC FERRIES HIGHWAY 4 NANAIMO")
    assert tp2.get("primary_mode") == "ground_only"


def test_normalize_day_transport_optional() -> None:
    raw = [
        {
            "day": 1,
            "label": "Ferry day",
            "items": ["a", "b"],
            "day_transport": {"mode": "ferry", "note": "BC Ferries"},
        }
    ]
    out = _normalize_itinerary_rows(raw, max_days=3)
    assert len(out) == 1
    assert out[0].get("day_transport", {}).get("mode") == "ferry"


def test_normalize_activities_capital_a() -> None:
    raw = [{"day": 1, "label": "L", "Activities": ["one", "two"]}]
    out = _normalize_itinerary_rows(raw, max_days=3)
    assert len(out) == 1
    assert len(out[0]["items"]) >= 2


def test_normalize_fallback_items_from_label() -> None:
    raw = [{"day": 1, "label": "Solo explore day", "items": []}]
    out = _normalize_itinerary_rows(raw, max_days=3)
    assert len(out) == 1
    assert "Solo explore" in out[0]["items"][0] or out[0]["items"][0].startswith("Solo")


def test_process_lazy_itinerary_persists_surface_legs_logistics() -> None:
    from src.wanderly.itinerary_lazy import _persisted_logistics_day_count, _process_lazy_itinerary_raw

    raw = (
        '{"itinerary":[{"day":1,"label":"Ferry in","base":"Nanaimo","items":["a","b","c"],'
        '"day_logistics":{"flight":null,"surface_legs":[{"kind":"ferry","from_label":"Tsawwassen, BC, Canada",'
        '"to_label":"Nanaimo, BC, Canada","operator_hint":"BC Ferries"}]}}],'
        '"budget_guidance":{"trip_ballpark_usd_pp":"x","lodging_style":"x","flight_ballpark":"x","notes":"x",'
        '"trip_usd_pp_short":"x","flight_band_short":"x","lodging_band_short":"x"},'
        '"transport_plan":{"primary_mode":"ground_only","summary":"ferry","commercial_flight_segments":[]}}'
    )
    out, *_rest = _process_lazy_itinerary_raw(
        raw,
        trace_rid="test-rid",
        mode="json",
        dest_id="test-dest",
        n=4,
        profile_pills=[],
        destination={"id": "test-dest", "name": "Nanaimo"},
    )
    assert len(out) == 1
    assert _persisted_logistics_day_count(out) == 1
    assert out[0].get("day_logistics", {}).get("surface_legs")


def test_process_lazy_itinerary_without_day_logistics_zero_persisted() -> None:
    from src.wanderly.itinerary_lazy import _persisted_logistics_day_count, _process_lazy_itinerary_raw

    raw = (
        '{"itinerary":[{"day":1,"label":"Ferry in","base":"Nanaimo","items":["a","b","c"]}],'
        '"budget_guidance":{"trip_ballpark_usd_pp":"x","lodging_style":"x","flight_ballpark":"x","notes":"x",'
        '"trip_usd_pp_short":"x","flight_band_short":"x","lodging_band_short":"x"},'
        '"transport_plan":{"primary_mode":"ground_only","summary":"x","commercial_flight_segments":[]}}'
    )
    out, *_rest = _process_lazy_itinerary_raw(
        raw,
        trace_rid="test-rid2",
        mode="json",
        dest_id="test-dest",
        n=4,
        profile_pills=[],
        destination={"id": "test-dest", "name": "Nanaimo"},
    )
    assert len(out) == 1
    assert _persisted_logistics_day_count(out) == 0


def test_normalize_day_logistics_pet_friendly_false_preserved() -> None:
    from src.wanderly.day_logistics import normalize_day_logistics

    dl = normalize_day_logistics(
        {
            "flight": None,
            "lodging": {
                "destination_search_label": "Paris, France",
                "pet_friendly": False,
            },
            "surface_legs": [{"kind": "walk", "from_label": "A", "to_label": "B"}],
        }
    )
    assert dl is not None
    assert dl["lodging"]["pet_friendly"] is False


def test_enrich_lodging_fills_pet_guests_rooms_from_profile() -> None:
    from src.wanderly.day_logistics import enrich_itinerary_rows_lodging_from_profile

    rows = [
        {
            "day": 1,
            "label": "x",
            "items": ["a", "b"],
            "day_logistics": {
                "flight": None,
                "lodging": {
                    "destination_search_label": "Nanaimo, BC, Canada",
                    "check_in": "2026-05-21",
                    "check_out": "2026-05-22",
                },
                "surface_legs": [{"kind": "walk", "from_label": "A", "to_label": "B"}],
            },
        }
    ]
    pills = [{"key": "who", "value": "3 adults traveling with our dog"}]
    out = enrich_itinerary_rows_lodging_from_profile(rows, pills)
    lod = out[0]["day_logistics"]["lodging"]
    assert lod["pet_friendly"] is True
    assert lod["guests"]["adults"] == 3
    assert lod["rooms"] == 1
    assert lod["traveler_party"]["total"] == 3
    assert any("pet" in p.lower() for p in lod.get("room_preferences", []))


def test_enrich_lodging_parses_travelers_count_from_who() -> None:
    from src.wanderly.day_logistics import enrich_itinerary_rows_lodging_from_profile

    rows = [
        {
            "day": 1,
            "label": "x",
            "items": ["a", "b"],
            "day_logistics": {
                "flight": None,
                "lodging": {"destination_search_label": "Victoria, BC, Canada"},
                "surface_legs": [{"kind": "walk", "from_label": "A", "to_label": "B"}],
            },
        }
    ]
    pills = [{"key": "who", "value": "3 travelers with a dog"}]
    out = enrich_itinerary_rows_lodging_from_profile(rows, pills)
    lod = out[0]["day_logistics"]["lodging"]
    assert lod["guests"]["adults"] == 3
    assert lod["traveler_party"]["total"] == 3


def test_normalize_lodging_traveler_party_and_booking_factors() -> None:
    from src.wanderly.day_logistics import normalize_day_logistics

    dl = normalize_day_logistics(
        {
            "flight": None,
            "lodging": {
                "destination_search_label": "Nanaimo, BC, Canada",
                "guests": {"adults": 2, "children": 1, "infants_in_seat": 0},
                "booking_factors": ["  late arrival ", "ADA roll-in shower"],
            },
            "surface_legs": [{"kind": "walk", "from_label": "A", "to_label": "B"}],
        }
    )
    assert dl is not None
    assert dl["lodging"]["traveler_party"] == {"total": 3, "adults": 2, "children": 1, "infants_in_seat": 0}
    assert dl["lodging"]["booking_factors"][0] == "late arrival"


def test_normalize_lodging_guests_from_traveler_party_only() -> None:
    from src.wanderly.day_logistics import normalize_day_logistics

    dl = normalize_day_logistics(
        {
            "flight": None,
            "lodging": {
                "destination_search_label": "Seattle, WA, United States",
                "traveler_party": {"total": 4, "adults": 4, "children": 0, "infants_in_seat": 0},
            },
            "surface_legs": [{"kind": "walk", "from_label": "A", "to_label": "B"}],
        }
    )
    assert dl is not None
    assert dl["lodging"]["guests"]["adults"] == 4
    assert dl["lodging"]["traveler_party"]["total"] == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
