"""Validate inline day-by-day itineraries from refine JSON and decide lazy-build fallbacks."""

from __future__ import annotations

import os
import re
from typing import Any

_ITIN_PRIMARY_REFINE_ASKS_ITIN = re.compile(
    r"\b(update|refresh|rebuild|regenerate|rewrite|redo|revise)\b[\s\S]{0,220}\b"
    r"(itineraries?|itinerary|day-by-day|day\s+plans?|schedule|cards?|shortlist|destination\s+cards?)\b",
    re.I,
)
_ITIN_PRIMARY_REFINE_ASKS_ITIN_ALT = re.compile(
    r"\b(itineraries?|itinerary|day-by-day)\b[\s\S]{0,220}\b"
    r"(update|refresh|rebuild|regenerate|rewrite|redo|revise|for\s+each|all|every)\b",
    re.I,
)

_ITINERARY_INVALIDATE_MESSAGE_RE = re.compile(
    r"\b(itinerary|schedule|day-by-day|day\s*\d|days\s*\d|replace\s+day|change\s+day|"
    r"revise(\s+the)?\s+plan|update\s+the\s+plan|update\s+(the\s+)?(itinerary|trip)|edit\s+days|"
    r"swap\s+day|adjust\s+days|move\s+day|replan|redo\s+days|rebuild|regenerate|"
    r"refresh\s+(the\s+)?(itinerary|plan)|shorten|extend|fewer\s+days|more\s+days)\b",
    re.I,
)

_COMPARE_ONLY_RE = re.compile(
    r"\b(compare|comparison|contrast|versus|vs\.?|which\s+is\s+better|help\s+me\s+choose|"
    r"pros\s+and\s+cons)\b",
    re.I,
)

_EDIT_CUE_RE = re.compile(
    r"\b(change|update|modify|adjust|replace|remove|add|delete|swap|shift|reschedule|"
    r"replan|redo|tweak|revise|shorten|extend|refresh|rebuild|regenerate)\b",
    re.I,
)

_CRITICAL_PROFILE_KEYS = frozenset({"when", "who", "from", "budget", "duration", "trip_style"})

_REFINE_INLINE_ITINERARY_BLOCK = """
**Inline itinerary mode (this turn):** The traveler asked to rebuild day-by-day plan(s). For each
destination id listed under **inline_itinerary_destination_ids** in the context JSON, you MUST include
a complete ``itinerary`` array in your ``destinations`` JSON (every day with human-readable ``items``).
Also include that row's ``transport_plan``, ``needs_commercial_flight``, and ``booking_search_hints`` when
commercial flights apply — same quality as the lazy itinerary builder. If you cannot produce a complete
plan for an id, omit ``itinerary`` on that row (the server will run a dedicated itinerary build).
Still return the full non-dismissed shortlist (same ids). Other destinations may omit ``itinerary``.
""".strip()


def refine_inline_itinerary_instructions() -> str:
    return _REFINE_INLINE_ITINERARY_BLOCK


def inline_refine_enabled() -> bool:
    """Feature flag — default on. Set ``WANDERLY_INLINE_ITINERARY=0`` to disable inline-in-refine."""
    raw = (os.environ.get("WANDERLY_INLINE_ITINERARY") or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def resolve_itinerary_delivery(orchestration: dict[str, Any] | None) -> str:
    """``inline_in_refine`` | ``lazy_build`` | ``none`` | ``auto``."""
    if not isinstance(orchestration, dict):
        return "auto"
    raw = str(orchestration.get("itinerary_delivery") or "auto").strip().lower()
    if raw in ("inline_in_refine", "lazy_build", "none"):
        return raw
    return "auto"


def itinerary_primary_refine_message(message: str) -> bool:
    """Trip-wide itinerary rebuild (not a single named card, not a profile-only edit)."""
    from src.wanderly.refine_shortlist_guard import is_trip_wide_itinerary_refine_message

    m = (message or "").strip()
    if not m:
        return False
    ml = m.lower()
    asks_itin = bool(
        _ITIN_PRIMARY_REFINE_ASKS_ITIN.search(ml) or _ITIN_PRIMARY_REFINE_ASKS_ITIN_ALT.search(ml)
    )
    if not asks_itin:
        return False
    asks_profile = bool(
        re.search(
            r"\b(set|change|update|edit)\s+(my\s+)?(budget|duration|when|dates|origin|airport|travelers?|vibe|pace)\b",
            ml,
            re.I,
        )
        or re.search(r"\b(budget|duration)\s+(to|at)\s+", ml, re.I)
        or re.search(r"\b(update|edit)\s+(my\s+)?(travel|traveler)\s+profile\b", ml, re.I)
        or re.search(r"\b(profile\s+pills?|sidebar\s+profile)\b", ml, re.I)
    )
    if asks_profile:
        return False
    return is_trip_wide_itinerary_refine_message(m)


def is_plan_compare_only_question(message: str) -> bool:
    m = (message or "").strip()
    if not m:
        return False
    if _ITINERARY_INVALIDATE_MESSAGE_RE.search(m):
        return False
    if not _COMPARE_ONLY_RE.search(m):
        return False
    return not bool(_EDIT_CUE_RE.search(m))


def _profile_critical_signature(pills: list[dict[str, Any]]) -> str:
    by_key: dict[str, str] = {}
    for p in pills:
        if not isinstance(p, dict):
            continue
        k = str(p.get("key") or "").strip()
        if k in _CRITICAL_PROFILE_KEYS:
            by_key[k] = " ".join(str(p.get("value") or "").split())
    return "|".join(f"{k}={by_key.get(k, '')}" for k in sorted(_CRITICAL_PROFILE_KEYS))


def should_consider_itinerary_rebuild(
    user_message: str,
    prior_pills: list[dict[str, Any]],
    incoming_pills: list[dict[str, Any]] | None,
) -> bool:
    if is_plan_compare_only_question(user_message):
        return False
    if itinerary_primary_refine_message(user_message):
        return True
    if _ITINERARY_INVALIDATE_MESSAGE_RE.search(user_message or ""):
        return True
    if incoming_pills and _profile_critical_signature(prior_pills) != _profile_critical_signature(incoming_pills):
        return True
    return False


def candidate_rebuild_destination_ids(
    *,
    orchestration: dict[str, Any] | None,
    user_message: str,
    prior_pills: list[dict[str, Any]],
    incoming_pills: list[dict[str, Any]] | None,
    valid_ids: set[str],
    focus_destination_id: str | None,
) -> list[str]:
    """Destination ids that may need inline or lazy itinerary rebuild after refine."""
    if not valid_ids:
        return []
    delivery = resolve_itinerary_delivery(orchestration)
    if delivery == "none":
        return []
    orch = orchestration if isinstance(orchestration, dict) else {}
    action = str(orch.get("workspace_action") or "").strip()
    if action == "compare_places":
        return []
    refresh = str(orch.get("itinerary_refresh") or "auto").strip().lower()
    if refresh == "none":
        return []
    if delivery == "lazy_build":
        if refresh == "all":
            return sorted(valid_ids)
        if refresh == "focus_only":
            return _focus_ids_from_orchestration(orch, valid_ids, focus_destination_id)
        if should_consider_itinerary_rebuild(user_message, prior_pills, incoming_pills):
            focus = _focus_ids_from_orchestration(orch, valid_ids, focus_destination_id)
            if focus:
                return focus
            if action == "targeted_itinerary_edit" and focus_destination_id and focus_destination_id in valid_ids:
                return [focus_destination_id]
            return sorted(valid_ids)
        return []
    # inline_in_refine or auto — same candidate set; inline attempt happens at validate time
    if refresh == "all":
        return sorted(valid_ids)
    if refresh == "focus_only":
        ids = _focus_ids_from_orchestration(orch, valid_ids, focus_destination_id)
        return ids if ids else []
    if not should_consider_itinerary_rebuild(user_message, prior_pills, incoming_pills):
        return []
    if action == "targeted_itinerary_edit":
        focus = _focus_ids_from_orchestration(orch, valid_ids, focus_destination_id)
        if focus:
            return focus
        if focus_destination_id and focus_destination_id in valid_ids:
            return [focus_destination_id]
    if action in ("refine_workspace", "explicit_remove_destinations"):
        return sorted(valid_ids)
    focus = _focus_ids_from_orchestration(orch, valid_ids, focus_destination_id)
    if focus:
        return focus
    if focus_destination_id and focus_destination_id in valid_ids:
        return [focus_destination_id]
    return sorted(valid_ids)


def candidates_for_inline_attempt(
    *,
    orchestration: dict[str, Any] | None,
    valid_ids: set[str],
    focus_destination_id: str | None,
) -> list[str]:
    """
    At most one destination may receive the heavy inline-itinerary block in refine.

    Never inline for ``refine_workspace`` / trip-wide refresh — those use slim refine + lazy build.
    """
    if not inline_refine_enabled() or not valid_ids:
        return []
    delivery = resolve_itinerary_delivery(orchestration)
    if delivery in ("none", "lazy_build"):
        return []
    orch = orchestration if isinstance(orchestration, dict) else {}
    action = str(orch.get("workspace_action") or "").strip()
    if action != "targeted_itinerary_edit":
        return []
    refresh = str(orch.get("itinerary_refresh") or "auto").strip().lower()
    if refresh == "all":
        return []
    focus = _focus_ids_from_orchestration(orch, valid_ids, focus_destination_id)
    if len(focus) != 1:
        return []
    if delivery == "inline_in_refine":
        return focus
    if delivery == "auto" and refresh in ("focus_only",):
        return focus
    return []


def needs_trip_wide_itinerary_confirm(orchestration: dict[str, Any] | None) -> bool:
    """True when the UI should confirm before running a trip-wide itinerary rebuild."""
    if not isinstance(orchestration, dict):
        return False
    action = str(orchestration.get("workspace_action") or "").strip()
    refresh = str(orchestration.get("itinerary_refresh") or "auto").strip().lower()
    if refresh == "none":
        return False
    if refresh == "all":
        return True
    if action == "refine_workspace" and refresh != "focus_only":
        return True
    if action == "explicit_remove_destinations":
        return True
    return False


def _focus_ids_from_orchestration(
    orch: dict[str, Any],
    valid_ids: set[str],
    focus_destination_id: str | None,
) -> list[str]:
    raw = orch.get("focus_destination_ids")
    ids: list[str] = []
    if isinstance(raw, list):
        for x in raw:
            s = str(x or "").strip()
            if s and s in valid_ids and s not in ids:
                ids.append(s)
    fid = (focus_destination_id or "").strip()
    if fid and fid in valid_ids and fid not in ids:
        ids.append(fid)
    return ids


def _day_has_substantive_items(day: dict[str, Any]) -> bool:
    items_in = day.get("items")
    if not isinstance(items_in, list):
        return False
    for itm in items_in:
        if isinstance(itm, str) and itm.strip():
            return True
        if isinstance(itm, dict):
            t = str(
                itm.get("text")
                or itm.get("item")
                or itm.get("title")
                or itm.get("label")
                or ""
            ).strip()
            if t:
                return True
    return False


def destination_has_complete_inline_itinerary(row: dict[str, Any]) -> bool:
    """True when refine returned enough day-by-day + logistics signal to skip lazy build."""
    it = row.get("itinerary")
    if not isinstance(it, list) or len(it) < 1:
        return False
    substantive_days = sum(1 for day in it if isinstance(day, dict) and _day_has_substantive_items(day))
    if substantive_days < 1:
        return False
    tp = row.get("transport_plan")
    if isinstance(tp, dict) and str(tp.get("primary_mode") or "").strip():
        return True
    for day in it:
        if isinstance(day, dict) and day.get("day_logistics"):
            return True
    return substantive_days >= 2


def partition_inline_vs_lazy(
    destinations: list[dict[str, Any]],
    candidate_ids: list[str],
    *,
    try_inline: bool,
) -> tuple[list[str], list[str]]:
    """
    Returns ``(inline_applied_ids, lazy_needed_ids)``.

    When ``try_inline`` is false, every candidate is lazy-needed.
    """
    if not candidate_ids:
        return [], []
    by_id = {str(d.get("id") or "").strip(): d for d in destinations if isinstance(d, dict)}
    applied: list[str] = []
    lazy: list[str] = []
    for cid in candidate_ids:
        row = by_id.get(cid)
        if try_inline and row and destination_has_complete_inline_itinerary(row):
            applied.append(cid)
        else:
            lazy.append(cid)
    return applied, lazy


def apply_itinerary_dirty_flags(
    destinations: list[dict[str, Any]],
    *,
    inline_applied_ids: set[str],
    lazy_needed_ids: set[str],
) -> list[dict[str, Any]]:
    """Set ``itinerary_dirty`` on committed destination rows."""
    out: list[dict[str, Any]] = []
    for d in destinations:
        if not isinstance(d, dict):
            continue
        row = dict(d)
        did = str(row.get("id") or "").strip()
        if did in inline_applied_ids:
            row["itinerary_dirty"] = False
        elif did in lazy_needed_ids:
            row["itinerary_dirty"] = True
        out.append(row)
    return out
