"""Profile pill coercion strips bad location prose from ``from``."""

from __future__ import annotations

from src.wanderly.cursor_advisor import merge_profile_pills_advisor_precedence
from src.wanderly.mapper import map_workspace_to_wanderly
from src.wanderly.schemas import coerce_profile_pill


def test_coerce_from_strips_based_suffix() -> None:
    raw = {
        "key": "from",
        "label": "Flying from",
        "value": "Seattle-based",
        "icon": "✈️",
        "editable": True,
    }
    dto = coerce_profile_pill(raw)
    assert dto is not None
    assert dto.value == "Seattle"


def test_coerce_other_pills_unchanged() -> None:
    raw = {
        "key": "vibe",
        "label": "Vibe",
        "value": "Slow mornings, coffee-based days",
        "icon": "✨",
        "editable": True,
    }
    dto = coerce_profile_pill(raw)
    assert dto is not None
    assert dto.value == "Slow mornings, coffee-based days"


def test_merge_advisor_precedence_coerces_from_pill() -> None:
    base = [{"key": "from", "label": "Flying from", "value": "Seattle (SEA)", "icon": "✈️"}]
    adv = [{"key": "from", "label": "Flying from", "value": "Seattle-based", "icon": "✈️"}]
    out = merge_profile_pills_advisor_precedence(base, adv)
    from_p = next(x for x in out if x["key"] == "from")
    assert from_p["value"] == "Seattle"


def test_mapper_sanitizes_traveler_origin_for_from_pill() -> None:
    w = map_workspace_to_wanderly(
        {
            "traveler_profile": {
                "traveler_origin": "Seattle-based",
                "group_size": 2,
                "group_type": "adults",
            },
            "itinerary_options": [],
        }
    )
    from_p = next(p for p in w["profile_pills"] if p["key"] == "from")
    assert from_p["value"] == "Seattle"
