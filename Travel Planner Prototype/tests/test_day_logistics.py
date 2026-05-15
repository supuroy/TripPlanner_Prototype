"""Tests for per-day ``day_logistics`` normalization."""

from __future__ import annotations

from src.wanderly.day_logistics import normalize_day_logistics
from src.wanderly.itinerary_lazy import _normalize_itinerary_rows


def test_normalize_day_logistics_ground_day_ferry_surface() -> None:
    raw = {
        "flight": None,
        "lodging": {
            "destination_search_label": "Nanaimo, BC, Canada",
            "check_in": "2026-05-21",
            "check_out": "2026-05-22",
            "rooms": 1,
            "guests": {"adults": 3, "children": 0},
            "pet_friendly": True,
        },
        "surface_legs": [
            {
                "kind": "ferry",
                "from_label": "Tsawwassen, BC, Canada",
                "to_label": "Nanaimo, BC, Canada",
                "operator_hint": "BC Ferries",
            }
        ],
    }
    out = normalize_day_logistics(raw)
    assert out is not None
    assert out["flight"] is None
    assert out["lodging"]["destination_search_label"] == "Nanaimo, BC, Canada"
    assert out["lodging"]["check_in"] == "2026-05-21"
    assert out["lodging"]["pet_friendly"] is True
    assert len(out["surface_legs"]) == 1
    assert out["surface_legs"][0]["kind"] == "ferry"


def test_normalize_day_logistics_rejects_bad_date() -> None:
    raw = {"flight": None, "lodging": {"destination_search_label": "X", "check_in": "not-a-date"}}
    out = normalize_day_logistics(raw)
    assert out is not None
    assert "check_in" not in (out.get("lodging") or {})


def test_normalize_itinerary_rows_attaches_day_logistics() -> None:
    raw = [
        {
            "day": 1,
            "label": "Arrive",
            "items": ["a", "b"],
            "day_logistics": {
                "flight": None,
                "lodging": {"destination_search_label": "Test City, Italy", "rooms": 2},
            },
        }
    ]
    out = _normalize_itinerary_rows(raw, max_days=7)
    assert len(out) == 1
    assert out[0].get("day_logistics") is not None
    assert out[0]["day_logistics"]["flight"] is None
    assert out[0]["day_logistics"]["lodging"]["destination_search_label"] == "Test City, Italy"
