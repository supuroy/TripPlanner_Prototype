"""Map Cursor SSE events to traveler-facing captions during booking LLM calls."""

from __future__ import annotations

from typing import Any


def _run_status_caption(status: str) -> str | None:
    u = status.upper()
    if u == "CREATING":
        return "Connecting to the advisor…"
    if u == "RUNNING":
        return "Advisor is working on your search criteria…"
    if u in ("FINISHED", "COMPLETED"):
        return "Finishing up…"
    return None


def booking_stream_event_caption(
    ev: dict[str, Any],
    *,
    assistant_tail: dict[str, str],
    thinking_tail: dict[str, str],
) -> str:
    """Return a short status line for booking UI, or empty string to skip."""
    if ev.get("type") == "phase" and isinstance(ev.get("message"), str):
        if "parsing" in ev["message"].lower():
            thinking_tail["text"] = ""
        return ev["message"].strip()

    if ev.get("type") == "thinking_delta" and isinstance(ev.get("delta"), str):
        thinking_tail["text"] = (thinking_tail.get("text") or "") + ev["delta"]
        t = " ".join((thinking_tail.get("text") or "").split()).strip()
        if not t:
            return ""
        windowed = t[-320:] if len(t) > 320 else t
        return f"Considering options — {windowed}"

    if ev.get("type") == "run_status" and isinstance(ev.get("status"), str):
        return _run_status_caption(ev["status"]) or ""

    if ev.get("type") == "tool_call":
        return "Gathering extra context for this search…"

    if ev.get("type") == "run_result" and isinstance(ev.get("status"), str):
        return _run_status_caption(ev["status"]) or ""

    if ev.get("type") == "assistant_delta" and isinstance(ev.get("delta"), str):
        assistant_tail["text"] = (assistant_tail.get("text") or "") + ev["delta"]
        one_line = " ".join((assistant_tail.get("text") or "").split()).strip()
        if not one_line:
            return ""
        looks_json = (
            '"listings"' in one_line
            or '"stays"' in one_line
            or '"start_iso"' in one_line
            or '"flight"' in one_line
            or '"hotel"' in one_line
            or ('"best_index"' in one_line)
        )
        if looks_json:
            tail = one_line[-420:] if len(one_line) > 420 else one_line
            return f"Parsing Expedia results — {tail}"
        prose = one_line[-500:] if len(one_line) > 500 else one_line
        return f"Advisor drafting — {prose}"

    return ""
