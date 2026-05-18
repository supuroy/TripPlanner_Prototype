"""Live progress events for booking search (SSE to the Wanderly UI)."""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from typing import Any

from src.integrations.cursor_agent_client import run_prompt_collect_assistant_text, run_prompt_collect_assistant_text_streaming
from src.wanderly.booking_stream_caption import booking_stream_event_caption

BookingEmitFn = Callable[[dict[str, Any]], None]

_booking_emit_ctx: contextvars.ContextVar[BookingEmitFn | None] = contextvars.ContextVar(
    "booking_emit", default=None
)


def set_booking_emit(emit: BookingEmitFn | None) -> contextvars.Token:
    return _booking_emit_ctx.set(emit)


def reset_booking_emit(token: contextvars.Token) -> None:
    _booking_emit_ctx.reset(token)


def current_booking_emit() -> BookingEmitFn | None:
    return _booking_emit_ctx.get()


def booking_progress(
    message: str,
    *,
    step_key: str | None = None,
    step_status: str | None = None,
    detail: str | None = None,
    emit: BookingEmitFn | None = None,
) -> None:
    """Emit a traveler-facing progress line (``type: progress``)."""
    fn = emit or current_booking_emit()
    if not fn:
        return
    msg = (message or "").strip()
    if not msg:
        return
    payload: dict[str, Any] = {"type": "progress", "message": msg}
    if step_key:
        payload["step_key"] = step_key
    if step_status:
        payload["step_status"] = step_status
    if detail and detail.strip():
        payload["detail"] = detail.strip()[:600]
    fn(payload)


def booking_phase(message: str, *, emit: BookingEmitFn | None = None) -> None:
    """Emit a phase banner (same shape as itinerary stream)."""
    fn = emit or current_booking_emit()
    if not fn:
        return
    msg = (message or "").strip()
    if msg:
        fn({"type": "phase", "message": msg})


def booking_stream_preview(message: str, *, emit: BookingEmitFn | None = None) -> None:
    """Emit a rolling LLM tail for the progress strip."""
    fn = emit or current_booking_emit()
    if not fn:
        return
    msg = (message or "").strip()
    if not msg:
        return
    fn({"type": "stream_preview", "message": msg[:900]})


def booking_llm_collect(
    client: Any,
    prompt: str,
    *,
    emit: BookingEmitFn | None = None,
    phase_start: str | None = None,
    **kwargs: Any,
) -> str:
    """Run an LLM call; stream captions to ``emit`` when set."""
    fn = emit or current_booking_emit()
    if phase_start:
        booking_phase(phase_start, emit=fn)
    if not fn:
        return run_prompt_collect_assistant_text(client, prompt, **kwargs)

    assistant_tail = {"text": ""}
    thinking_tail = {"text": ""}
    last_preview = ""

    def _forward(ev: dict[str, Any]) -> None:
        nonlocal last_preview
        cap = booking_stream_event_caption(
            ev, assistant_tail=assistant_tail, thinking_tail=thinking_tail
        )
        if cap and cap != last_preview:
            last_preview = cap
            booking_stream_preview(cap, emit=fn)

    return run_prompt_collect_assistant_text_streaming(
        client, prompt, emit=_forward, **kwargs
    )
