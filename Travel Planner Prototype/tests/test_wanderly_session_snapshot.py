from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def snapshot_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    snap = tmp_path / "sessions.json"
    agents = tmp_path / "agents.json"
    monkeypatch.setenv("WANDERLY_SESSION_SNAPSHOT_PATH", str(snap))
    monkeypatch.setenv("WANDERLY_CURSOR_AGENT_IDS_PATH", str(agents))
    monkeypatch.setenv("WANDERLY_SESSION_SNAPSHOT_PERSIST", "1")
    monkeypatch.setenv("WANDERLY_CURSOR_AGENT_IDS_PERSIST", "1")
    from src.wanderly import session_store as ss

    ss.reset_cursor_agent_file_cache_for_tests()
    ss._sessions.clear()
    yield snap, agents
    ss.reset_cursor_agent_file_cache_for_tests()
    ss._sessions.clear()


def test_session_snapshot_persist_and_hydrate(snapshot_paths: tuple[Path, Path]) -> None:
    from src.wanderly import session_store as ss

    sid = "sess-h1"
    s = ss.get_session(sid)
    s["ui_destinations"] = [{"id": "x", "name": "Test"}]
    s["profile_pills"] = [{"key": "when", "label": "When", "value": "Soon", "icon": "📅"}]
    ss.persist_wanderly_session_snapshot(sid)
    assert snapshot_paths[0].is_file()

    ss._sessions.clear()
    assert ss.hydrate_wanderly_session_from_disk(sid) is True
    s2 = ss.get_session(sid)
    assert len(s2["ui_destinations"]) == 1
    assert s2["ui_destinations"][0]["id"] == "x"
    assert len(s2["profile_pills"]) == 1


def test_start_new_trip_clears_snapshot_and_agents(snapshot_paths: tuple[Path, Path]) -> None:
    from src.wanderly import session_store as ss

    sid = "sess-old"
    s = ss.get_session(sid)
    s["ui_destinations"] = [{"id": "a"}]
    ss.set_cursor_agent_id(sid, "planner", "agent-999")
    ss.persist_wanderly_session_snapshot(sid)

    new_sid, wly = ss.start_new_wanderly_trip_session(sid)
    assert new_sid != sid
    assert wly["destinations"] == []

    raw = json.loads(snapshot_paths[0].read_text(encoding="utf-8"))
    assert sid not in raw.get("by_session", {})

    agents = json.loads(snapshot_paths[1].read_text(encoding="utf-8"))
    assert sid not in agents.get("by_session", {})

    assert ss.hydrate_wanderly_session_from_disk(sid) is False


def test_session_snapshot_trip_meta_round_trip(snapshot_paths: tuple[Path, Path]) -> None:
    from src.wanderly import session_store as ss

    sid = "sess-meta-1"
    s = ss.get_session(sid)
    s["trip_meta"] = {"trip_id": "trip-uuid-1", "trip_name": "Spring break"}
    s["ui_destinations"] = [{"id": "a"}]
    ss.persist_wanderly_session_snapshot(sid)
    ss._sessions.clear()
    assert ss.hydrate_wanderly_session_from_disk(sid) is True
    s2 = ss.get_session(sid)
    assert s2.get("trip_meta") == {"trip_id": "trip-uuid-1", "trip_name": "Spring break"}


def test_hydrate_skips_when_ui_already_warm(snapshot_paths: tuple[Path, Path]) -> None:
    from src.wanderly import session_store as ss

    sid = "sess-warm"
    s = ss.get_session(sid)
    s["ui_destinations"] = [{"id": "live"}]
    ss.persist_wanderly_session_snapshot(sid)
    s["ui_destinations"] = [{"id": "live"}, {"id": "extra"}]
    assert ss.hydrate_wanderly_session_from_disk(sid) is False
    assert len(ss.get_session(sid)["ui_destinations"]) == 2


def test_wanderly_destinations_for_session_merges_itinerary_from_last(
    snapshot_paths: tuple[Path, Path],
) -> None:
    from src.wanderly import session_store as ss

    sid = "sess-merge-itin"
    s = ss.get_session(sid)
    s["ui_destinations"] = [{"id": "lisbon", "name": "Lisbon", "itinerary": []}]
    s["last_destinations"] = [
        {"id": "lisbon", "name": "Lisbon", "itinerary": [{"day": 1, "label": "Day 1", "items": ["Walk"]}]}
    ]
    out = ss.wanderly_destinations_for_session(s)
    assert len(out) == 1
    assert len(out[0]["itinerary"]) == 1
    assert out[0]["itinerary"][0]["day"] == 1


def test_patch_session_destination_itinerary_updates_ui_and_last(
    snapshot_paths: tuple[Path, Path],
) -> None:
    from src.wanderly import session_store as ss

    sid = "sess-patch-itin"
    s = ss.get_session(sid)
    s["ui_destinations"] = [{"id": "x", "itinerary": [], "itinerary_dirty": True}]
    s["last_destinations"] = [{"id": "x", "itinerary": [], "itinerary_dirty": True}]
    ss.patch_session_destination_itinerary(
        sid,
        "x",
        itinerary=[{"day": 1, "label": "Day 1", "items": ["Museum"]}],
        budget_guidance={"notes": "mock"},
        needs_commercial_flight=False,
        transport_plan={"primary_mode": "ground_only", "summary": "drive", "commercial_flight_segments": []},
    )
    s2 = ss.get_session(sid)
    assert len(s2["ui_destinations"][0]["itinerary"]) == 1
    assert s2["ui_destinations"][0]["itinerary_dirty"] is False
    assert s2["last_destinations"][0]["itinerary_dirty"] is False
    assert s2["last_destinations"][0]["budget_guidance"]["notes"] == "mock"
    assert s2["ui_destinations"][0]["needs_commercial_flight"] is False
    assert s2["ui_destinations"][0]["transport_plan"]["primary_mode"] == "ground_only"
