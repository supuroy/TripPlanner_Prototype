"""Refine shortlist must not shrink when rebuilding one named card."""

from __future__ import annotations

import json
from pathlib import Path

from src.wanderly.inline_itinerary import itinerary_primary_refine_message
from src.wanderly.refine_shortlist_guard import (
    is_trip_wide_itinerary_refine_message,
    match_focus_destination_id,
    orchestration_for_single_card_refine,
    resolve_refine_prior_destinations,
)
from src.wanderly import session_store as ss
from src.wanderly.session_store import get_session
from src.wanderly.smoke_service import _maybe_fill_missing_shortlist


def test_heart_lake_card_rebuild_is_not_trip_wide() -> None:
    msg = "rebuild the Heart Lake trail card"
    assert not is_trip_wide_itinerary_refine_message(msg)
    assert not itinerary_primary_refine_message(msg)


def test_all_destinations_still_trip_wide() -> None:
    msg = "rebuild the itinerary of all the destinations in this trip"
    assert is_trip_wide_itinerary_refine_message(msg)
    assert itinerary_primary_refine_message(msg)


def test_match_focus_from_message() -> None:
    prior = [
        {"id": "heart-lake-trail", "name": "Heart Lake Trail"},
        {"id": "bechler-river-trail", "name": "Bechler River Trail"},
    ]
    assert match_focus_destination_id("rebuild the Heart Lake trail card", prior) == "heart-lake-trail"


def test_orchestration_downgrades_to_focus_only() -> None:
    prior = [
        {"id": "heart-lake-trail", "name": "Heart Lake Trail"},
        {"id": "bechler-river-trail", "name": "Bechler River Trail"},
    ]
    orch = orchestration_for_single_card_refine(
        "rebuild the Heart Lake trail card",
        prior,
        {"workspace_action": "refine_workspace", "itinerary_refresh": "all"},
    )
    assert orch is not None
    assert orch.get("itinerary_refresh") == "focus_only"
    assert orch.get("focus_destination_ids") == ["heart-lake-trail"]


def test_resolve_prior_from_trip_disk(tmp_path: Path, monkeypatch) -> None:
    ss._sessions.clear()
    trips_dir = tmp_path / "trips"
    trips_dir.mkdir()
    monkeypatch.setenv("WANDERLY_TRIPS_DIR", str(trips_dir))
    sid = "sess-guard-001"
    trip_id = "trip-guard001"
    doc = {
        "v": 1,
        "trip_id": trip_id,
        "trip_name": "Test",
        "session_id": sid,
        "updated_at_ms": 1,
        "session": {
            "ui_destinations": [
                {"id": "heart-lake-trail", "name": "Heart Lake Trail"},
                {"id": "bechler-river-trail", "name": "Bechler River Trail"},
            ],
            "last_destinations": [],
            "profile_pills": [],
            "chat": [],
            "mode": "idle",
        },
    }
    (trips_dir / "trip-guard001.json").write_text(json.dumps(doc), encoding="utf-8")
    (trips_dir / "manifest.json").write_text(
        json.dumps(
            {
                "v": 1,
                "active_trip_id": trip_id,
                "trips": [{"session_id": sid, "trip_id": trip_id, "trip_name": "Test"}],
            }
        ),
        encoding="utf-8",
    )
    get_session(sid)
    prior = resolve_refine_prior_destinations(sid)
    assert len(prior) == 2
    assert get_session(sid).get("ui_destinations")


def test_maybe_fill_missing_shortlist_restores_omitted() -> None:
    prior = [
        {"id": "a", "name": "A", "dismissed": False},
        {"id": "b", "name": "B", "dismissed": False},
    ]
    merged = [{"id": "a", "name": "A refreshed"}]
    out, restored = _maybe_fill_missing_shortlist(prior, merged, "sess-x", allow_shortlist_shrink=False)
    assert restored == ["b"]
    assert len(out) == 2
