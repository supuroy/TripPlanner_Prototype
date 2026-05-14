"""Profile pill coercion strips bad location prose from ``from``."""

from __future__ import annotations

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
