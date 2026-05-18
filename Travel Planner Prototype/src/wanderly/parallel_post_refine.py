"""Feature flag: overlap profile listener, hero enrichment, and plan review with itinerary builds."""

from __future__ import annotations

import os


def parallel_post_refine_enabled() -> bool:
    """When true, profile_listener runs in background after refine/smoke parse (SSE ``done`` is early)."""
    raw = (os.environ.get("WANDERLY_PARALLEL_POST_REFINE") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


# Internal: thread handle attached to planner return dict; stripped before JSON responses.
PROFILE_LISTENER_THREAD_KEY = "__profile_listener_thread"
