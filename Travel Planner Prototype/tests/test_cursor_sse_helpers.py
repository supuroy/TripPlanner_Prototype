"""Unit tests for Cursor SSE retry helpers (no live HTTP)."""

from src.integrations.cursor_agent_client import (
    _is_recoverable_cursor_stream_sse_message,
    assistant_text_from_run_json,
    _sse_retry_sleep_seconds,
)


def test_recoverable_stream_sse_message_no_longer_available() -> None:
    assert _is_recoverable_cursor_stream_sse_message("Run stream is no longer available")
    assert _is_recoverable_cursor_stream_sse_message("STREAM ENDED")
    assert not _is_recoverable_cursor_stream_sse_message("Invalid API key")
    assert not _is_recoverable_cursor_stream_sse_message("")


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


def test_assistant_text_from_run_json_response_dict() -> None:
    blob = {"response": {"assistant_message": "via response"}}
    assert assistant_text_from_run_json(blob) == "via response"


def test_assistant_text_from_run_json_response_str() -> None:
    blob = {"response": " plain "}
    assert assistant_text_from_run_json(blob) == "plain"
