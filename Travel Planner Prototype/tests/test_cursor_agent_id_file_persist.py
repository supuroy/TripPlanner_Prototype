from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def agent_id_store_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "cursor_agent_ids.json"
    monkeypatch.setenv("WANDERLY_CURSOR_AGENT_IDS_PATH", str(p))
    monkeypatch.setenv("WANDERLY_CURSOR_AGENT_IDS_PERSIST", "1")
    from src.wanderly import session_store as ss

    ss.reset_cursor_agent_file_cache_for_tests()
    ss._sessions.clear()
    yield p
    ss.reset_cursor_agent_file_cache_for_tests()
    ss._sessions.clear()


def test_cursor_agent_id_round_trip_file(agent_id_store_path: Path) -> None:
    from src.wanderly import session_store as ss

    ss.set_cursor_agent_id("sess-a", "planner", "agent-111")
    assert ss.get_cursor_agent_id("sess-a", "planner") == "agent-111"

    raw = json.loads(agent_id_store_path.read_text(encoding="utf-8"))
    assert raw.get("v") == 1
    assert raw["by_session"]["sess-a"]["planner"] == "agent-111"


def test_cursor_agent_id_survives_process_restart_simulation(agent_id_store_path: Path) -> None:
    from src.wanderly import session_store as ss

    ss.set_cursor_agent_id("sess-b", "itinerary_lazy", "agent-222")
    ss.reset_cursor_agent_file_cache_for_tests()
    ss._sessions.clear()

    assert ss.get_cursor_agent_id("sess-b", "itinerary_lazy") == "agent-222"


def test_cursor_agent_clear_removes_from_file(agent_id_store_path: Path) -> None:
    from src.wanderly import session_store as ss

    ss.set_cursor_agent_id("sess-c", "planner", "agent-333")
    ss.clear_cursor_agent_id("sess-c", "planner")
    assert ss.get_cursor_agent_id("sess-c", "planner") is None

    raw = json.loads(agent_id_store_path.read_text(encoding="utf-8"))
    assert "sess-c" not in raw.get("by_session", {})


def test_cursor_agent_persist_off_skips_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "no_write.json"
    monkeypatch.setenv("WANDERLY_CURSOR_AGENT_IDS_PATH", str(p))
    monkeypatch.setenv("WANDERLY_CURSOR_AGENT_IDS_PERSIST", "0")
    from src.wanderly import session_store as ss

    ss.reset_cursor_agent_file_cache_for_tests()
    ss._sessions.clear()
    ss.set_cursor_agent_id("sess-d", "planner", "agent-444")
    assert ss.get_cursor_agent_id("sess-d", "planner") == "agent-444"
    assert not p.exists()

    ss.reset_cursor_agent_file_cache_for_tests()
    ss._sessions.clear()
    assert ss.get_cursor_agent_id("sess-d", "planner") is None
