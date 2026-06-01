"""Grounded trail catalog prototype pipeline (picker + itinerary LLM)."""

from __future__ import annotations

import json
import os
from typing import Any

from src.integrations.cursor_agent_client import (
    basic_client,
    get_api_key,
    load_env_from_roots,
    run_prompt_collect_assistant_text,
)
from src.wanderly.flight_listing_llm import parse_llm_json_object
from src.wanderly.session_store import cursor_run_persist_kwargs
from src.wanderly.trail_catalog.store import clean_trail_name, entry_by_trail_id
from src.wanderly.trail_catalog.validate import validate_itinerary_against_catalog, validate_trail_ids

_SELECT_PROMPT = """
You are a trail picker for U.S. national park backpacking trips.
You MUST choose trail_id values ONLY from the provided park_trails list.
Do not invent trails, URLs, or ids.

Return ONLY one JSON object (no markdown):
{
  "park_code": "<park code>",
  "selections": [
    {"trail_id": "<exact trail_id from list>", "reason": "<one sentence why it fits>"}
  ]
}
""".strip()

_ITINERARY_PROMPT = """
You are Wanderly building a catalog-grounded backpacking itinerary for ONE trail.

Return ONLY one JSON object (no markdown):
{
  "grounding": {
    "trail_id": "<must match selected_trail.trail_id>",
    "source_url": "<must match selected_trail.source_url>",
    "status": "catalog"
  },
  "itinerary": [
    {
      "day": 1,
      "label": "Short day title",
      "items": ["3-5 bullets"],
      "day_logistics": {
        "surface_legs": [
          {"kind": "walk", "from_label": "Trailhead A", "to_label": "Camp 1"}
        ],
        "trail_segment": {
          "name": "<segment on this trail>",
          "distance_mi": 0.0,
          "elevation_gain_ft": 0,
          "elevation_loss_ft": 0
        }
      }
    }
  ],
  "outdoor_adventure": {
    "trail_name": "<catalog trail name>",
    "route_type": "out_and_back | loop | point_to_point",
    "camp_nights": [
      {"night": 1, "label": "Backcountry camp name", "notes": "optional"}
    ],
    "permit_notes": "Yellowstone backcountry permit required; cite official process briefly."
  },
  "planning_notes": "1-3 sentences on fit, season, difficulty — no invented mileage beyond catalog scale."
}

Rules:
- Use ONLY the selected_trail record for the parent trail name and source_url.
- Exactly N days in itinerary[] (days 1..N).
- Multi-day hike: include walk legs and trail_segment on hiking days; name backcountry camps on overnight rows.
- Do NOT invent a different trail or AllTrails URL than selected_trail.
- Scale daily mileage to fit the trip length and catalog distance_mi (do not claim to hike the full trail in one day unless it is a short trail).
- No commercial flights or hotels unless user explicitly needs gateway travel; focus on backcountry days.
""".strip()


def _light_model() -> str | None:
    return (
        os.environ.get("CURSOR_LIGHT_MODEL_FOR_ITINERARY")
        or os.environ.get("CURSOR_LIGHT_MODEL_FOR_IMAGES")
        or ""
    ).strip() or None


def _call_llm_json(
    *,
    system: str,
    payload: dict[str, Any],
    session_id: str,
    trace_path: str,
) -> dict[str, Any] | None:
    load_env_from_roots()
    key = get_api_key()
    prompt = system + "\n\n---\n\n" + json.dumps(payload, ensure_ascii=True, indent=2)
    light = _light_model()
    with basic_client(key) as client:
        raw = run_prompt_collect_assistant_text(
            client,
            prompt,
            model_id_override=light,
            persistent_instruction_prefix=system + "\n\n---\n\n",
            **cursor_run_persist_kwargs(session_id, light, trace_path=trace_path),
        )
    return parse_llm_json_object(raw)


def llm_select_trails(
    *,
    park_code: str,
    park_trails: list[dict[str, Any]],
    user_message: str,
    profile: dict[str, Any],
    k: int,
    session_id: str = "grounded-prototype",
) -> list[dict[str, Any]]:
    payload = {
        "park_code": park_code,
        "pick_count": k,
        "user_message": user_message,
        "profile": profile,
        "park_trails": park_trails,
    }
    allowed = {str(t.get("trail_id") or "").lower() for t in park_trails}
    for attempt in range(2):
        parsed = _call_llm_json(
            system=_SELECT_PROMPT,
            payload=payload,
            session_id=session_id,
            trace_path=f"grounded_trail.select.a{attempt}.json",
        )
        if not parsed:
            continue
        selections = parsed.get("selections")
        if not isinstance(selections, list):
            continue
        ids = [str(s.get("trail_id") or "") for s in selections if isinstance(s, dict)]
        bad = validate_trail_ids(ids, allowed)
        if bad and attempt == 0:
            payload["validation_error"] = (
                f"Invalid trail_id(s) not in park list: {bad}. Return only ids from park_trails."
            )
            continue
        out: list[dict[str, Any]] = []
        for sel in selections:
            if not isinstance(sel, dict):
                continue
            tid = str(sel.get("trail_id") or "")
            row = next((t for t in park_trails if str(t.get("trail_id") or "").lower() == tid.lower()), None)
            if row:
                out.append({**row, "reason": str(sel.get("reason") or "").strip()})
            if len(out) >= k:
                break
        if out:
            return out[:k]
    return []


def destination_card_from_trail(
    entry: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    name = clean_trail_name(str(entry.get("name") or ""))
    tid = str(entry.get("trail_id") or "")
    return {
        "id": tid,
        "name": name,
        "country": entry.get("country") or "United States",
        "region": entry.get("region") or "Wyoming",
        "hook": reason or f"Catalog-grounded {name} backpacking option.",
        "grounding": {
            "trail_id": tid,
            "source_url": entry.get("source_url"),
            "status": "catalog",
            "catalog_version": 1,
        },
        "tags": list(entry.get("tags") or []),
        "distance_mi": entry.get("distance_mi"),
        "difficulty": entry.get("difficulty"),
    }


def llm_build_itinerary(
    *,
    selected_trail: dict[str, Any],
    destination: dict[str, Any],
    user_message: str,
    profile: dict[str, Any],
    days: int,
    nights: int,
    session_id: str = "grounded-prototype",
) -> dict[str, Any] | None:
    payload = {
        "days": days,
        "nights": nights,
        "user_message": user_message,
        "profile": profile,
        "destination": destination,
        "selected_trail": {
            "trail_id": selected_trail.get("trail_id"),
            "name": clean_trail_name(str(selected_trail.get("name") or "")),
            "source_url": selected_trail.get("source_url"),
            "distance_mi": selected_trail.get("distance_mi"),
            "difficulty": selected_trail.get("difficulty"),
            "route_type": selected_trail.get("route_type"),
            "tags": selected_trail.get("tags"),
            "park_name": selected_trail.get("park_name"),
        },
    }
    system = _ITINERARY_PROMPT.replace("Exactly N days", f"Exactly {days} days")
    return _call_llm_json(
        system=system,
        payload=payload,
        session_id=f"{session_id}-{selected_trail.get('trail_id')}",
        trace_path="grounded_trail.itinerary.json",
    )


def full_catalog_entry(doc: dict[str, Any], trail_id: str) -> dict[str, Any] | None:
    return entry_by_trail_id(doc, trail_id)
