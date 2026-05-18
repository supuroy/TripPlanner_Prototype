"""Tests for WANDERLY_ITINERARY_COMPACT_PROMPT and slim traveler context."""

from __future__ import annotations

import json

from src.integrations.cursor_agent_client import effective_followup_prompt_for_reuse
from src.wanderly import itinerary_lazy as lazy
from src.wanderly.itinerary_prompt_variants import (
    build_itinerary_context_json,
    build_itinerary_context_payload,
    itinerary_compact_prompt_enabled,
    slim_traveler_from_profile_pills,
)


def test_itinerary_compact_prompt_enabled(monkeypatch):
    monkeypatch.delenv("WANDERLY_ITINERARY_COMPACT_PROMPT", raising=False)
    assert itinerary_compact_prompt_enabled() is False
    monkeypatch.setenv("WANDERLY_ITINERARY_COMPACT_PROMPT", "1")
    assert itinerary_compact_prompt_enabled() is True


def test_slim_traveler_from_profile_pills():
    pills = [
        {"key": "from", "value": "Seattle, WA", "from": "user"},
        {"key": "who", "value": "2 adults", "from": "user"},
        {"key": "tags", "value": "ignore me", "from": "user"},
    ]
    t = slim_traveler_from_profile_pills(pills)
    assert t == {"from": "Seattle, WA", "who": "2 adults"}
    assert "tags" not in t


def test_compact_prompt_smaller_than_legacy(monkeypatch):
    monkeypatch.setenv("WANDERLY_ITINERARY_COMPACT_PROMPT", "0")
    dest = {"id": "tofino-bc", "name": "Tofino", "country": "Canada", "hook": "Coast.", "vibe": ["coastal"]}
    pills = [{"key": "from", "value": "Seattle, WA"}, {"key": "who", "value": "2 adults"}]
    legacy_prompt, _, legacy_prefix = lazy._itinerary_prompt_and_days(
        destination=dest, profile_pills=pills, days=4
    )

    monkeypatch.setenv("WANDERLY_ITINERARY_COMPACT_PROMPT", "1")
    compact_prompt, _, compact_prefix = lazy._itinerary_prompt_and_days(
        destination=dest, profile_pills=pills, days=4
    )

    assert len(compact_prompt) < len(legacy_prompt) * 0.75
    assert len(compact_prefix) < len(legacy_prefix) * 0.75
    payload = build_itinerary_context_payload(destination=dest, profile_pills=pills, compact=True)
    assert "traveler" in payload
    assert "profile_pills" not in payload


def test_compact_context_uses_traveler_not_profile_pills(monkeypatch):
    monkeypatch.setenv("WANDERLY_ITINERARY_COMPACT_PROMPT", "1")
    dest = {"id": "x", "name": "Y", "country": "Canada", "hook": "", "vibe": []}
    pills = [{"key": "from", "value": "SEA"}]
    ctx = json.loads(build_itinerary_context_json(destination=dest, profile_pills=pills))
    assert ctx["traveler"]["from"] == "SEA"


def test_compact_reuse_strip_matches_prefix(monkeypatch):
    monkeypatch.setenv("WANDERLY_ITINERARY_COMPACT_PROMPT", "1")
    dest = {"id": "tofino-bc", "name": "Tofino", "country": "Canada", "hook": "Coast.", "vibe": []}
    pills = [{"key": "from", "value": "Seattle, WA"}]
    full, _, prefix = lazy._itinerary_prompt_and_days(destination=dest, profile_pills=pills, days=4)
    follow = effective_followup_prompt_for_reuse(
        full_prompt=full,
        persistent_instruction_prefix=prefix,
        existing_agent_id="agent-abc",
    )
    assert follow != full
    assert len(follow) < len(full) * 0.45
    assert "Context JSON:" in follow
