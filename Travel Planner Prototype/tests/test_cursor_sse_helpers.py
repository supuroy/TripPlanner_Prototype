"""Unit tests for Cursor SSE retry helpers (no live HTTP)."""

from src.integrations.cursor_agent_client import assistant_text_from_run_json, _sse_retry_sleep_seconds


def test_sse_retry_sleep_bounded_exponential() -> None:
    assert _sse_retry_sleep_seconds(0) == 1.25
    assert _sse_retry_sleep_seconds(1) == 2.5
    assert _sse_retry_sleep_seconds(7) == 12.0


def test_assistant_text_from_run_json_top_level_keys() -> None:
    assert assistant_text_from_run_json({"assistant_message": "  hi  "}) == "hi"
    assert assistant_text_from_run_json({"result": "x"}) == "x"


def test_assistant_text_from_run_json_messages_array() -> None:
    blob = {
        "messages": [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "answer"},
        ]
    }
    assert assistant_text_from_run_json(blob) == "answer"


def test_assistant_text_from_run_json_nested_output() -> None:
    blob = {"output": {"text": "nested"}}
    assert assistant_text_from_run_json(blob) == "nested"
