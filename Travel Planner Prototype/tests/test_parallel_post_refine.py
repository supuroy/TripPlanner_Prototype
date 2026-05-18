"""Tests for WANDERLY_PARALLEL_POST_REFINE flag and profile-listener scheduling."""

from __future__ import annotations

import threading
from unittest.mock import patch

from src.wanderly.parallel_post_refine import (
    PROFILE_LISTENER_THREAD_KEY,
    parallel_post_refine_enabled,
)
from src.wanderly import smoke_service as ss


def test_parallel_post_refine_enabled(monkeypatch):
    monkeypatch.delenv("WANDERLY_PARALLEL_POST_REFINE", raising=False)
    assert parallel_post_refine_enabled() is False
    monkeypatch.setenv("WANDERLY_PARALLEL_POST_REFINE", "1")
    assert parallel_post_refine_enabled() is True
    monkeypatch.setenv("WANDERLY_PARALLEL_POST_REFINE", "off")
    assert parallel_post_refine_enabled() is False


def test_finish_profile_listener_sync_parallel_starts_thread(monkeypatch):
    monkeypatch.setenv("WANDERLY_PARALLEL_POST_REFINE", "1")
    out = {"wanderly": {"profile_pills": [], "destinations": []}}
    emitted: list[dict] = []

    def fake_bg(sid, um, o, emit):
        assert sid == "sess-1"
        assert um == "hello"

    with patch.object(ss, "_profile_listener_background", side_effect=fake_bg):
        with patch.object(ss, "parallel_post_refine_enabled", return_value=True):
            result = ss._finish_profile_listener_sync(
                "sess-1",
                "hello",
                out,
                lambda obj: emitted.append(obj),
                freeze_profile=False,
            )
    assert PROFILE_LISTENER_THREAD_KEY in result
    job = result.pop(PROFILE_LISTENER_THREAD_KEY)
    assert isinstance(job, threading.Thread)
    job.join(timeout=2.0)
    assert any(e.get("type") == "phase" for e in emitted)


def test_finish_profile_listener_sync_serial_merges(monkeypatch):
    monkeypatch.setenv("WANDERLY_PARALLEL_POST_REFINE", "0")
    out = {"wanderly": {"profile_pills": [{"key": "budget", "value": "mid"}], "destinations": []}}
    listener = ([{"key": "budget", "value": "high"}], "note")
    with patch.object(ss, "_run_profile_listener_after_planner", return_value=listener):
        with patch.object(ss, "parallel_post_refine_enabled", return_value=False):
            with patch.object(ss, "_merge_advisor_listener_tuple", return_value={"merged": True}) as merge:
                result = ss._finish_profile_listener_sync(
                    "sess-2",
                    "msg",
                    out,
                    None,
                    freeze_profile=False,
                )
    merge.assert_called_once()
    assert result == {"merged": True}
    assert PROFILE_LISTENER_THREAD_KEY not in result
