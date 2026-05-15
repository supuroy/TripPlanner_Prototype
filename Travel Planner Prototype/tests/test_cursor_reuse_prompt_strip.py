from __future__ import annotations

import os

import pytest


def test_effective_followup_strips_when_reusing(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.integrations.cursor_agent_client import effective_followup_prompt_for_reuse

    monkeypatch.delenv("CURSOR_REUSE_FOLLOWUP_HINT", raising=False)
    monkeypatch.setenv("CURSOR_REUSE_STRIP_STATIC_INSTRUCTIONS", "1")
    long_rules = "RULE_LINE\n" * 30
    full = long_rules + "\n\n" + "x" * 20
    out = effective_followup_prompt_for_reuse(
        full_prompt=full,
        persistent_instruction_prefix=long_rules + "\n\n",
        existing_agent_id="agent-1",
    )
    assert "x" * 20 in out
    assert len(out) < len(full)
    assert "Follow-up on this same Cloud Agent thread" in out


def test_effective_followup_no_strip_without_agent() -> None:
    from src.integrations.cursor_agent_client import effective_followup_prompt_for_reuse

    full = "STATIC\n\ntail content here ok"
    out = effective_followup_prompt_for_reuse(
        full_prompt=full,
        persistent_instruction_prefix="STATIC\n\n",
        existing_agent_id="",
    )
    assert out == full


def test_effective_followup_no_strip_when_prefix_mismatch() -> None:
    from src.integrations.cursor_agent_client import effective_followup_prompt_for_reuse

    full = "OTHER\n\ntail"
    out = effective_followup_prompt_for_reuse(
        full_prompt=full,
        persistent_instruction_prefix="STATIC\n\n",
        existing_agent_id="a1",
    )
    assert out == full


def test_effective_followup_no_strip_when_tail_too_short(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.integrations import cursor_agent_client as m

    monkeypatch.setenv("CURSOR_REUSE_STRIP_STATIC_INSTRUCTIONS", "1")
    full = "P\n\nshort"
    out = m.effective_followup_prompt_for_reuse(
        full_prompt=full,
        persistent_instruction_prefix="P\n\n",
        existing_agent_id="a1",
    )
    assert out == full


def test_effective_followup_respects_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.integrations import cursor_agent_client as m

    monkeypatch.setenv("CURSOR_REUSE_STRIP_STATIC_INSTRUCTIONS", "0")
    full = "P\n\n" + "y" * 20
    out = m.effective_followup_prompt_for_reuse(
        full_prompt=full,
        persistent_instruction_prefix="P\n\n",
        existing_agent_id="a1",
    )
    assert out == full


def test_effective_followup_custom_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.integrations import cursor_agent_client as m

    monkeypatch.setenv("CURSOR_REUSE_STRIP_STATIC_INSTRUCTIONS", "1")
    monkeypatch.setenv("CURSOR_REUSE_FOLLOWUP_HINT", "CUSTOM_HINT_ONLY")
    full = "P\n\n" + "z" * 20
    out = m.effective_followup_prompt_for_reuse(
        full_prompt=full,
        persistent_instruction_prefix="P\n\n",
        existing_agent_id="a1",
    )
    assert out.startswith("CUSTOM_HINT_ONLY")
    assert "z" * 20 in out
