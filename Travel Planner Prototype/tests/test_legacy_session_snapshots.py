from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from src.wanderly import legacy_session_snapshots


@pytest.fixture
def snap_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "wanderly_session_snapshots.json"
    monkeypatch.setenv("WANDERLY_SESSION_SNAPSHOTS_PATH", str(p))
    yield p


def test_hydrate_picks_only_rich_session(snap_path: Path) -> None:
    sid_new = str(uuid.uuid4())
    store: dict = {}

    def get_session(s: str):
        if s not in store:
            store[s] = {"chat": [], "last_destinations": [], "mode": "idle", "profile_pills": [], "ui_destinations": []}
        return store[s]

    doc = {
        "v": 1,
        "by_session": {
            "empty-a": {
                "chat": [],
                "last_destinations": [],
                "ui_destinations": [],
                "updated_at": "2026-05-10T00:00:00Z",
            },
            "rich-b": {
                "chat": [],
                "last_destinations": [{"id": "x", "name": "Testland"}],
                "ui_destinations": [{"id": "x", "name": "Testland"}],
                "profile_pills": [],
                "updated_at": "2026-05-15T00:00:00Z",
            },
        },
    }
    snap_path.write_text(json.dumps(doc), encoding="utf-8")
    ok = legacy_session_snapshots.maybe_hydrate_empty_session_from_snapshots(get_session, sid_new)
    assert ok is True
    assert len(get_session(sid_new)["ui_destinations"]) == 1


def test_exact_session_match_over_multi(snap_path: Path) -> None:
    sid = "cafecafe-cafe-cafe-cafe-cafecafecafe"
    store: dict = {}

    def get_session(s: str):
        if s not in store:
            store[s] = {"chat": [], "last_destinations": [], "mode": "idle", "profile_pills": [], "ui_destinations": []}
        return store[s]

    doc = {
        "v": 1,
        "by_session": {
            sid: {
                "chat": [],
                "last_destinations": [{"id": "mine", "name": "Exact"}],
                "ui_destinations": [],
                "updated_at": "2026-05-01T00:00:00Z",
            },
            "other": {
                "chat": [],
                "last_destinations": [{"id": "other", "name": "Other"}],
                "ui_destinations": [],
                "updated_at": "2026-05-20T00:00:00Z",
            },
        },
    }
    snap_path.write_text(json.dumps(doc), encoding="utf-8")
    ok = legacy_session_snapshots.maybe_hydrate_empty_session_from_snapshots(get_session, sid)
    assert ok is True
    assert get_session(sid)["last_destinations"][0]["id"] == "mine"
