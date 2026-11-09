from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from src.wanderly import trip_disk_store

TEST_OWNER = "user:test"


@pytest.fixture
def trips_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "wanderly_trips"
    monkeypatch.setenv("WANDERLY_TRIPS_DIR", str(d))
    trip_disk_store.reset_for_tests()
    yield d
    trip_disk_store.reset_for_tests()


def _fake_get_session(store: dict):
    def get_session(sid: str):
        if sid not in store:
            store[sid] = {"chat": [], "last_destinations": [], "mode": "idle"}
        return store[sid]

    return get_session


def test_lookup_trip_id_for_session(trips_dir: Path) -> None:
    store: dict = {}
    gs = _fake_get_session(store)
    meta = trip_disk_store.bootstrap(None, None, gs, TEST_OWNER)
    sid = meta["session_id"]
    tid = meta["trip_id"]
    assert trip_disk_store.lookup_trip_id_for_session(sid) == tid
    assert trip_disk_store.lookup_trip_id_for_session("missing-session") is None


def test_bootstrap_creates_manifest_and_trip_file(trips_dir: Path) -> None:
    store: dict = {}
    gs = _fake_get_session(store)
    sid = str(uuid.uuid4())
    out = trip_disk_store.bootstrap(sid, None, gs, TEST_OWNER)
    assert out["session_id"] == sid
    assert out["trip_id"]
    assert (trips_dir / "manifest.json").is_file()
    tid = out["trip_id"]
    assert (trips_dir / f"{tid}.json").is_file()


def test_persist_round_trip(trips_dir: Path) -> None:
    store: dict = {}
    gs = _fake_get_session(store)
    sid = str(uuid.uuid4())
    meta = trip_disk_store.bootstrap(sid, None, gs, TEST_OWNER)
    tid = meta["trip_id"]
    s = gs(sid)
    s["ui_destinations"] = [{"id": "x", "name": "Test"}]
    s["profile_pills"] = [{"key": "when", "label": "When", "value": "June", "icon": "📅"}]
    assert trip_disk_store.persist_trip(tid, sid, gs, TEST_OWNER) is True
    raw = json.loads((trips_dir / f"{tid}.json").read_text(encoding="utf-8"))
    assert raw["session"]["ui_destinations"][0]["id"] == "x"


def test_persist_repairs_manifest_when_row_missing(trips_dir: Path) -> None:
    store: dict = {}
    gs = _fake_get_session(store)
    sid = str(uuid.uuid4())
    meta = trip_disk_store.bootstrap(sid, None, gs, TEST_OWNER)
    tid = meta["trip_id"]
    mf = trip_disk_store.read_manifest()
    mf["trips"] = []
    mf["active_trip_id"] = ""
    trip_disk_store.write_manifest(mf)
    gs(sid)["ui_destinations"] = [{"id": "d1", "name": "Tokyo", "hotels": [{"name": "Hotel Zen"}]}]
    assert trip_disk_store.persist_trip(tid, sid, gs, TEST_OWNER) is True
    m2 = trip_disk_store.read_manifest()
    assert trip_disk_store.manifest_contains_trip_id(tid)
    raw = json.loads((trips_dir / f"{tid}.json").read_text(encoding="utf-8"))
    assert raw["session"]["ui_destinations"][0].get("hotels")


def test_persist_no_repair_when_trip_file_session_mismatch(trips_dir: Path) -> None:
    store: dict = {}
    gs = _fake_get_session(store)
    sid = str(uuid.uuid4())
    meta = trip_disk_store.bootstrap(sid, None, gs, TEST_OWNER)
    tid = meta["trip_id"]
    p = trips_dir / f"{tid}.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["session_id"] = "other-session-id"
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    mf = trip_disk_store.read_manifest()
    mf["trips"] = []
    mf["active_trip_id"] = ""
    trip_disk_store.write_manifest(mf)
    gs(sid)["ui_destinations"] = [{"id": "d1", "hotels": [{"name": "X"}]}]
    assert trip_disk_store.persist_trip(tid, sid, gs, TEST_OWNER) is False


def test_activate_second_trip_isolated_sessions(trips_dir: Path) -> None:
    store: dict = {}
    gs = _fake_get_session(store)
    sid_a = str(uuid.uuid4())
    trip_disk_store.bootstrap(sid_a, None, gs, TEST_OWNER)
    m1 = trip_disk_store.read_manifest()
    tid_a = m1["active_trip_id"]
    meta_b = trip_disk_store.create_trip("Paris weekend", gs, TEST_OWNER)
    tid_b = meta_b["trip_id"]
    sid_b = meta_b["session_id"]
    gs(sid_b)["ui_destinations"] = [{"id": "paris", "name": "Paris"}]
    trip_disk_store.persist_trip(tid_b, sid_b, gs, TEST_OWNER)

    trip_disk_store.activate_trip(tid_a, gs, TEST_OWNER)
    assert trip_disk_store.read_manifest()["active_trip_id"] == tid_a
    assert gs(sid_a).get("ui_destinations") == []

    trip_disk_store.activate_trip(tid_b, gs, TEST_OWNER)
    assert gs(sid_b).get("ui_destinations")[0]["id"] == "paris"


def test_rename_trip_updates_manifest_and_trip_file(trips_dir: Path) -> None:
    store: dict = {}
    gs = _fake_get_session(store)
    meta = trip_disk_store.bootstrap(None, None, gs, TEST_OWNER)
    tid = meta["trip_id"]
    out = trip_disk_store.rename_trip(tid, "Summer in Japan", gs, TEST_OWNER)
    assert out is not None
    assert out["trip_name"] == "Summer in Japan"
    m = trip_disk_store.read_manifest()
    assert m["trips"][0]["trip_name"] == "Summer in Japan"
    raw = json.loads((trips_dir / f"{tid}.json").read_text(encoding="utf-8"))
    assert raw["trip_name"] == "Summer in Japan"


def test_delete_trip_switches_active(trips_dir: Path) -> None:
    store: dict = {}
    gs = _fake_get_session(store)
    meta_a = trip_disk_store.bootstrap(None, None, gs, TEST_OWNER)
    tid_a = meta_a["trip_id"]
    meta_b = trip_disk_store.create_trip("Second", gs, TEST_OWNER)
    tid_b = meta_b["trip_id"]
    del_out = trip_disk_store.delete_trip(tid_a, gs, TEST_OWNER)
    assert del_out is not None
    assert del_out["trip_id"] == tid_b
    assert tid_a not in {str(t.get("trip_id")) for t in trip_disk_store.read_manifest().get("trips") or []}
    assert not (trips_dir / f"{tid_a}.json").is_file()


def test_reconcile_manifest_orphan_trip_files(trips_dir: Path) -> None:
    orphan_tid = "trip-orphanabc123"
    orphan_path = trips_dir / f"{orphan_tid}.json"
    orphan_path.write_text(
        json.dumps(
                {
                    "v": 1,
                    "trip_id": orphan_tid,
                    "trip_name": "Memorial Day",
                    "session_id": "sess-orphan-1",
                    "owner_sub": TEST_OWNER,
                    "updated_at_ms": 1_700_000_000_000,
                    "session": {"chat": [], "last_destinations": [], "mode": "idle"},
                }
        ),
        encoding="utf-8",
    )
    trips_dir.joinpath("manifest.json").write_text(
        json.dumps({"v": 1, "active_trip_id": "trip-main1", "trips": []}),
        encoding="utf-8",
    )
    (trips_dir / "trip-main1.json").write_text(
        json.dumps(
                {
                    "v": 1,
                    "trip_id": "trip-main1",
                    "trip_name": "Summer",
                    "session_id": "sess-main-1",
                    "owner_sub": TEST_OWNER,
                    "session": {"chat": [], "last_destinations": [], "mode": "idle"},
                }
        ),
        encoding="utf-8",
    )
    assert trip_disk_store.reconcile_manifest_orphan_trip_files() is True
    m = trip_disk_store.read_manifest()
    ids = {str(t.get("trip_id")) for t in m.get("trips") or []}
    assert orphan_tid in ids
    assert "trip-main1" in ids


def test_delete_last_trip_creates_fresh_default(trips_dir: Path) -> None:
    store: dict = {}
    gs = _fake_get_session(store)
    meta = trip_disk_store.bootstrap(None, None, gs, TEST_OWNER)
    tid = meta["trip_id"]
    out = trip_disk_store.delete_trip(tid, gs, TEST_OWNER)
    assert out is not None
    assert out["trip_id"] != tid
    m = trip_disk_store.read_manifest()
    assert len(m["trips"]) == 1
    assert (trips_dir / f"{out['trip_id']}.json").is_file()
