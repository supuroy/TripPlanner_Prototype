"""Smoke / refine path must persist itinerary + transport_plan (not strip on Pydantic validate)."""

from __future__ import annotations

from src.wanderly.schemas import WanderlyWorkspaceDTO
from src.wanderly.smoke_service import _enrich_workspace_destinations_transport, _normalize_parsed


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
