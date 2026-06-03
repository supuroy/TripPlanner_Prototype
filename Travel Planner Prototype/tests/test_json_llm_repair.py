"""JSON extraction and repair for LLM workspace payloads."""

from __future__ import annotations

from src.wanderly.json_llm_repair import extract_first_json_object, repair_llm_json_blob
from src.wanderly.smoke_service import _extract_json_object, _normalize_parsed


THAILAND_WORKSPACE = {
    "assistant_markdown": "Here are 3 curated Thailand options for your December trip.",
    "profile_pills": [
        {"key": "when", "label": "When", "value": "December", "icon": "📅", "editable": True},
        {"key": "who", "label": "Who", "value": "Travelers", "icon": "👥", "editable": True},
        {"key": "from", "label": "Flying from", "value": "Seattle, WA", "icon": "✈️", "editable": True},
        {"key": "vibe", "label": "Vibe", "value": "Urban culture + natural escapes", "icon": "✨", "editable": True},
        {"key": "trip_style", "label": "Trip style", "value": "General leisure", "icon": "🥾", "editable": True},
        {"key": "budget", "label": "Budget", "value": "$15,000", "icon": "💰", "editable": True},
        {"key": "duration", "label": "Duration", "value": "2 weeks", "icon": "⏱", "editable": True},
    ],
    "destinations": [
        {
            "id": "bangkok",
            "name": "Bangkok",
            "country": "Thailand",
            "hook": "Non-stop street life, golden temples, floating markets.",
            "score": 90,
        },
        {
            "id": "chiang-mai",
            "name": "Chiang Mai",
            "country": "Thailand",
            "hook": "Temple-dotted old city ringed by jungle mountains.",
            "score": 85,
        },
        {
            "id": "krabi",
            "name": "Krabi",
            "country": "Thailand",
            "hook": "Towering limestone karsts and island-hopping by longtail boat.",
            "score": 82,
        },
    ],
}


def test_extract_first_json_object_duplicated_back_to_back() -> None:
    """Regression: model emitted the same workspace JSON twice with no separator."""
    import json

    blob = json.dumps(THAILAND_WORKSPACE, ensure_ascii=False)
    raw = blob + blob
    out = extract_first_json_object(raw)
    assert out is not None
    assert out["assistant_markdown"] == THAILAND_WORKSPACE["assistant_markdown"]
    assert len(out.get("destinations") or []) == 3
    assert out["destinations"][0]["id"] == "bangkok"


def test_smoke_extract_json_object_uses_first_object_only() -> None:
    import json

    raw = json.dumps(THAILAND_WORKSPACE) + json.dumps(THAILAND_WORKSPACE)
    out = _extract_json_object(raw)
    assert out is not None
    dto = _normalize_parsed(out)
    assert len(dto.destinations) == 3
    assert dto.destinations[0].get("name") == "Bangkok"


def test_repair_llm_json_blob_editable_typo() -> None:
    raw = (
        '{"assistant_markdown":"Hi","profile_pills":['
        '{"key":"from","label":"Flying from","value":"Seattle, WA","icon":"✈️","editable":able":true}'
        '],"destinations":[]}'
    )
    fixed = repair_llm_json_blob(raw)
    out = extract_first_json_object(fixed)
    assert out is not None
    pills = out.get("profile_pills") or []
    assert pills[0].get("editable") is True
