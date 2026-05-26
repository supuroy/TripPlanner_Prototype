"""Smoke / refine path must persist itinerary + transport_plan (not strip on Pydantic validate)."""

from __future__ import annotations

from src.wanderly.schemas import WanderlyWorkspaceDTO
from src.wanderly.smoke_service import (
    _enrich_workspace_destinations_transport,
    _extract_json_object,
    _merge_dest_ui_flags,
    _normalize_parsed,
)


def test_merge_dest_ui_flags_preserves_prior_pins_when_model_emits_false() -> None:
    """Regression (28b65b5): refine JSON often includes pinned:false on every row; that must not wipe bookmarks."""
    prior = [
        {"id": "kings", "name": "Kings", "pinned": True, "dismissed": False},
        {"id": "mammoth", "name": "Mammoth", "pinned": True, "dismissed": False},
    ]
    model_rows = [
        {"id": "kings", "name": "Kings", "pinned": False, "dismissed": False},
        {"id": "mammoth", "name": "Mammoth", "pinned": False, "dismissed": False},
    ]
    out = _merge_dest_ui_flags(prior, model_rows)
    assert len(out) == 2
    assert out[0]["pinned"] is True and out[1]["pinned"] is True


def test_merge_dest_ui_flags_model_may_clear_dismissed() -> None:
    prior = [{"id": "x", "pinned": False, "dismissed": True}]
    model_rows = [{"id": "x", "pinned": False, "dismissed": False}]
    out = _merge_dest_ui_flags(prior, model_rows)
    assert out[0]["dismissed"] is False


def test_extract_json_object_repairs_editable_typo() -> None:
    """Regression: Gemini sometimes emits ``"editable":able":true`` which is not valid JSON."""
    raw = (
        '{"assistant_markdown":"Hi","profile_pills":['
        '{"key":"from","label":"Flying from","value":"Seattle, WA","icon":"✈️","editable":able":true}'
        '],"destinations":[]}'
    )
    out = _extract_json_object(raw)
    assert out is not None
    pills = out.get("profile_pills") or []
    assert len(pills) == 1
    assert pills[0].get("editable") is True


def test_normalize_parsed_overlays_itinerary_and_transport_from_model_json() -> None:
    raw = {
        "assistant_markdown": "Updated loop.",
        "destinations": [
            {
                "id": "tofino-bc",
                "name": "Tofino",
                "country": "Canada",
                "hook": "Memorial Day",
                "score": 98,
                "itinerary": [
                    {
                        "day": 1,
                        "label": "Seattle → Nanaimo",
                        "items": ["BC Ferries Horseshoe Bay to Departure Bay"],
                    }
                ],
                "transport_plan": {
                    "primary_mode": "ground_only",
                    "summary": "Drive and ferries only",
                    "commercial_flight_segments": [],
                },
                "needs_commercial_flight": False,
            }
        ],
    }
    dto = _normalize_parsed(raw)
    row = dto.destinations[0]
    assert len(row.get("itinerary") or []) == 1
    assert (row.get("transport_plan") or {}).get("primary_mode") == "ground_only"
    assert row.get("needs_commercial_flight") is False


def test_enrich_transport_infers_ground_when_model_omits_transport_plan() -> None:
    raw = {
        "assistant_markdown": "Road trip.",
        "destinations": [
            {
                "id": "tofino-bc",
                "name": "Tofino",
                "country": "Canada",
                "hook": "Test",
                "score": 90,
                "itinerary": [
                    {
                        "day": 1,
                        "label": "Day 1",
                        "items": ["Peace Arch crossing", "BC Ferries", "Highway 4 to Tofino"],
                    }
                ],
                "budget_guidance": {
                    "flight_ballpark": "Not used—Seattle road start; ferries + fuel.",
                },
            }
        ],
    }
    dto = _normalize_parsed(raw)
    base = WanderlyWorkspaceDTO(
        assistant_markdown=dto.assistant_markdown,
        profile_pills=[],
        destinations=dto.destinations,
    )
    out = _enrich_workspace_destinations_transport(base)
    tp = out.destinations[0].get("transport_plan")
    assert isinstance(tp, dict)
    assert tp.get("primary_mode") == "ground_only"
    assert out.destinations[0].get("needs_commercial_flight") is False
