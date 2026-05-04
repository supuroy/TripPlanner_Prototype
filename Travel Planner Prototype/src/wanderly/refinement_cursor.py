"""
Dedicated **refinement** Cursor Cloud Agent tier (speed/quality between full planner and advisor).

Use ``CURSOR_REFINEMENT_MODEL_ID`` (see ``Travel Planner Prototype/.env.example``). When unset,
call sites keep their previous behavior (main planner for refine; advisor for cheap JSON paths).

**Model tiers (recommended):**
- Full planner: ``CURSOR_MODEL_ID`` — initial smoke / heavy planning.
- Refinement: ``CURSOR_REFINEMENT_MODEL_ID`` — editing an existing workspace, plan QA, structured
  follow-ups, future itinerary/destination Q&A in natural language.
- Advisor: ``CURSOR_ADVISOR_MODEL_ID`` — fastest parallel profile listener only (see
  ``cursor_advisor.run_profile_listener_cursor``).

Persistence reuses the same Cloud Agent per Wanderly session keyed by model id
(``cursor_run_persist_kwargs(session_id, model_id)`` → lane ``m:<id>``), separate from the
``planner`` lane used for initial smoke turns.
"""

from __future__ import annotations

import os


def refinement_model_id() -> str | None:
    """Mid-tier model id from GET /v1/models; unset means callers fall back to their legacy model."""
    mid = (os.environ.get("CURSOR_REFINEMENT_MODEL_ID") or "").strip()
    return mid or None
