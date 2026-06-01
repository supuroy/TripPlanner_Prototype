"""
Protect the destination shortlist during refine — hydrate prior cards and block accidental drops.
"""

from __future__ import annotations

import re
from typing import Any

from src.wanderly.itinerary_debug_log import log_itinerary_build
from src.wanderly.session_store import get_session, snapshot_destinations_for_model

_TRIP_WIDE_REFINE_RE = re.compile(
    r"\b("
    r"all|every|each|entire|whole|full"
    r")\b[\s\S]{0,48}\b("
    r"cards?|destinations?|trails?|itineraries?|shortlist|options?"
    r")\b",
    re.I,
)
_TRIP_WIDE_ALT_RE = re.compile(
    r"\b(cards?|destinations?|itineraries?)\b[\s\S]{0,32}\b("
    r"in|on|for|across"
    r")\b[\s\S]{0,24}\b(trip|shortlist|board)\b",
    re.I,
)
_NAMED_SINGLE_CARD_RE = re.compile(
    r"\b(?:rebuild|refresh|regenerate|redo|revise|update|rewrite)\b"
    r"[\s\S]{0,100}?\b(?:the\s+)?[a-z][\w\s\-']{2,56}?\s+(?:trail\s+)?card\b",
    re.I,
)
_NAMED_SINGLE_TRAIL_RE = re.compile(
    r"\b(?:rebuild|refresh|regenerate|redo|revise|update|rewrite)\b"
    r"[\s\S]{0,80}?\b(?:the\s+)?[a-z][\w\s\-']{2,56}?\s+trail\b",
    re.I,
)


def is_trip_wide_itinerary_refine_message(message: str) -> bool:
    """
    True when the traveler clearly wants every destination updated, not one named card.

    ``rebuild the Heart Lake trail card`` is **not** trip-wide.
    """
    ml = (message or "").strip().lower()
    if not ml:
        return False
    if _TRIP_WIDE_REFINE_RE.search(ml) or _TRIP_WIDE_ALT_RE.search(ml):
        return True
    if re.search(r"\ball\b", ml) and re.search(
        r"\b(day-by-day|day\s+plans?|itineraries?|schedules?)\b", ml
    ):
        if not (_NAMED_SINGLE_CARD_RE.search(ml) or _NAMED_SINGLE_TRAIL_RE.search(ml)):
            return True
    if _NAMED_SINGLE_CARD_RE.search(ml) or _NAMED_SINGLE_TRAIL_RE.search(ml):
        if not re.search(r"\b(all|every|each)\b", ml):
            return False
    return False


def _destinations_from_trip_disk(session_id: str) -> list[dict[str, Any]]:
    from src.wanderly.trip_disk_store import lookup_trip_id_for_session, read_trip_file

    tid = lookup_trip_id_for_session(session_id)
    if not tid:
        return []
    blob = read_trip_file(tid)
    if not blob:
        return []
    inner = blob.get("session")
    if not isinstance(inner, dict):
        return []
    ui = inner.get("ui_destinations")
    last = inner.get("last_destinations")
    ui_list = [dict(x) for x in ui if isinstance(x, dict)] if isinstance(ui, list) else []
    if ui_list:
        return ui_list
    return [dict(x) for x in last if isinstance(x, dict)] if isinstance(last, list) else []


def _mirror_prior_into_session(session_id: str, destinations: list[dict[str, Any]], *, source: str) -> None:
    if not destinations:
        return
    s = get_session(session_id)
    s["ui_destinations"] = [dict(d) for d in destinations]
    if not s.get("last_destinations"):
        s["last_destinations"] = [dict(d) for d in destinations]


def resolve_refine_prior_destinations(
    session_id: str,
    *,
    client_destinations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Full shortlist for refine merge — session RAM, then trip JSON on disk, then client mirror.
    """
    sid = (session_id or "").strip()
    if not sid:
        return []

    prior = snapshot_destinations_for_model(sid)
    if prior:
        return prior

    disk = _destinations_from_trip_disk(sid)
    if disk:
        _mirror_prior_into_session(sid, disk, source="trip_disk")
        log_itinerary_build(
            f"path=wanderly.refine_prior_hydrate sid={sid[:12]!r} source=trip_disk dest_n={len(disk)}"
        )
        return disk

    if client_destinations:
        clean = [dict(x) for x in client_destinations if isinstance(x, dict) and str(x.get("id") or "").strip()]
        if clean:
            _mirror_prior_into_session(sid, clean, source="client")
            log_itinerary_build(
                f"path=wanderly.refine_prior_hydrate sid={sid[:12]!r} source=client dest_n={len(clean)}"
            )
            return clean

    return []


def match_focus_destination_id(
    message: str,
    destinations: list[dict[str, Any]],
) -> str | None:
    """Best destination id mentioned in a single-card refine message."""
    ml = (message or "").lower()
    if not ml or not destinations:
        return None
    best_id: str | None = None
    best_score = 0
    for d in destinations:
        if not isinstance(d, dict) or d.get("dismissed"):
            continue
        did = str(d.get("id") or "").strip()
        name = str(d.get("name") or "").strip().lower()
        if not did:
            continue
        score = 0
        slug_phrase = did.replace("-", " ")
        if name and len(name) >= 4 and name in ml:
            score = len(name) + 20
        elif slug_phrase and len(slug_phrase) >= 4 and slug_phrase in ml:
            score = len(slug_phrase) + 12
        else:
            for token in re.split(r"[\s\-]+", slug_phrase):
                if len(token) >= 5 and token in ml:
                    score = max(score, len(token) + 6)
        if score > best_score:
            best_score = score
            best_id = did
    return best_id if best_score >= 10 else None


def orchestration_for_single_card_refine(
    user_message: str,
    prior_destinations: list[dict[str, Any]],
    orchestration: dict[str, Any] | None,
    *,
    focus_destination_id: str | None = None,
) -> dict[str, Any] | None:
    """Downgrade trip-wide refresh when the message names one trail/card."""
    if is_trip_wide_itinerary_refine_message(user_message):
        return orchestration
    focus = (focus_destination_id or "").strip() or match_focus_destination_id(
        user_message, prior_destinations
    )
    if not focus:
        return orchestration
    out = dict(orchestration) if isinstance(orchestration, dict) else {}
    out["workspace_action"] = "targeted_itinerary_edit"
    out["itinerary_refresh"] = "focus_only"
    out["focus_destination_ids"] = [focus]
    out.setdefault("itinerary_delivery", "lazy_build")
    return out


def merge_prior_for_shortlist_guard(
    session_id: str,
    prior_dests: list[dict[str, Any]],
    *,
    client_destinations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """If merge ``prior_dests`` is empty, recover from disk/client before applying model JSON."""
    if prior_dests:
        return prior_dests
    return resolve_refine_prior_destinations(session_id, client_destinations=client_destinations)
