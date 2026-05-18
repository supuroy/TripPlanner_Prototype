"""LLM telemetry helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.wanderly import llm_telemetry as lt


def test_prompt_size_fields():
    sizes = lt.prompt_size_fields("hello", persistent_instruction_prefix="sys")
    assert sizes["prompt_chars"] == 5
    assert sizes["prefix_chars"] == 3
    assert sizes["total_input_chars"] == 8


def test_record_start_end_jsonl(tmp_path, monkeypatch):
    log_file = tmp_path / "llm_telemetry.jsonl"
    monkeypatch.setattr(lt, "_LOG_DIR", tmp_path)
    monkeypatch.setattr(lt, "_LOG_FILE", log_file)
    monkeypatch.setenv("WANDERLY_LLM_TELEMETRY", "1")

    span = lt.record_llm_start(
        activity=lt.ACTIVITY_ITINERARY_BUILDER,
        sub_activity="smoke_turn",
        prompt_text="User request:\nParis",
        persistent_instruction_prefix="You are a planner.",
        meta=lt.telemetry_meta(session_id="sess-abc"),
    )
    assert len(span) == 16

    import time

    t0 = time.perf_counter()
    time.sleep(0.01)
    lt.record_llm_end(
        span,
        activity=lt.ACTIVITY_ITINERARY_BUILDER,
        sub_activity="smoke_turn",
        started_at_perf=t0,
        prompt_text="User request:\nParis",
        persistent_instruction_prefix="You are a planner.",
        response_text='{"ok": true}',
    )

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    start = json.loads(lines[0])
    end = json.loads(lines[1])
    assert start["event"] == "start"
    assert end["event"] == "end"
    assert start["span_id"] == end["span_id"]
    assert start["activity"] == "itinerary_builder"
    assert end["status"] == "ok"
    assert end["duration_ms"] >= 5
    assert start["total_input_chars"] > 0
    assert end["response_chars"] > 0


def test_telemetry_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("WANDERLY_LLM_TELEMETRY", "0")
    log_file = tmp_path / "llm_telemetry.jsonl"
    monkeypatch.setattr(lt, "_LOG_FILE", log_file)
    span = lt.record_llm_start(
        activity=lt.ACTIVITY_ADVISOR,
        sub_activity="plan_review",
        prompt_text="x",
    )
    assert span == ""
    assert not log_file.exists()
