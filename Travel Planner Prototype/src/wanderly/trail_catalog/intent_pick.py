"""Intent-scoped catalog search + trail pick (local search, optional LLM rerank)."""

from __future__ import annotations

import re
from typing import Any, Literal

from src.wanderly.trail_catalog.catalog_query import CatalogQuery, search_catalog_trails
from src.wanderly.trail_catalog.pipeline import llm_select_trails
from src.wanderly.trail_catalog.store import (
    heuristic_pick_trails,
    load_park_catalog,
    entry_by_trail_id,
    resolve_catalog_trail_entry,
)
from src.wanderly.trail_catalog_trace import dump_trail_catalog_debug, log_trail_catalog
from src.wanderly.trip_intent_resolve import TripIntentResolution

PickMethod = Literal["llm", "heuristic", "search_rank"]


def _parse_max_distance_mi(text: str) -> float:
    m = re.search(r"\bunder\s+(\d+(?:\.\d+)?)\s*(?:mi|mile|miles)\b", (text or "").lower())
    if m:
        return float(m.group(1))
    m = re.search(r"\b(?:max|<=|<)\s*(\d+(?:\.\d+)?)\s*(?:mi|mile|miles)\b", (text or "").lower())
    if m:
        return float(m.group(1))
    return 0.0


def _parse_difficulty(text: str) -> str:
    low = (text or "").lower()
    for label in ("easy", "moderate", "hard", "difficult"):
        if label in low:
            return "hard" if label in ("hard", "difficult") else label
    return ""


def _infer_feature_slugs(text: str) -> list[str]:
    low = (text or "").lower()
    slugs: list[str] = []
    if "waterfall" in low:
        slugs.append("waterfall")
    if "camping" in low or "campground" in low:
        slugs.append("camping")
    return slugs


def build_catalog_query_from_message(
    user_message: str,
    resolution: TripIntentResolution | None = None,
    *,
    pick_count: int = 3,
    search_pool: int = 25,
) -> CatalogQuery:
    """Build a unified catalog query from the user message + trip resolution."""
    msg = (user_message or "").strip()
    max_mi = _parse_max_distance_mi(msg)
    difficulty = _parse_difficulty(msg)
    feature_slugs = _infer_feature_slugs(msg)
    park_codes = list(resolution.resolved_park_codes if resolution else [])
    state_slugs: list[str] = []
    if resolution and resolution.primary_park_code and resolution.primary_park_code not in park_codes:
        park_codes.insert(0, resolution.primary_park_code)

    # Utah / state hints from message when no park resolved
    state_match = re.search(r"\b(?:in|near)\s+([a-z][a-z\s]{2,30}?)(?:\s+under|\s*$|,|\.)", msg.lower())
    if state_match:
        fragment = state_match.group(1).strip()
        if "utah" in fragment:
            state_slugs.append("utah")

    backpacking_only = bool(
        resolution and resolution.trip_product == "outdoor_backpacking"
    )
    return CatalogQuery(
        user_message=msg,
        park_codes=park_codes[:3],
        state_slugs=state_slugs[:2],
        feature_slugs=feature_slugs[:2],
        limit=max(search_pool, pick_count * 4),
        max_distance_mi=max_mi,
        difficulty=difficulty,
        backpacking_only=backpacking_only,
    )


def search_trails_for_intent(
    user_message: str,
    resolution: TripIntentResolution | None = None,
    *,
    pick_count: int = 3,
    session_id: str = "",
) -> dict[str, Any]:
    """Run ``search_catalog_trails`` with logging."""
    cq = build_catalog_query_from_message(user_message, resolution, pick_count=pick_count)
    log_trail_catalog(
        f"intent_search sid={(session_id or '')[:12]!r} "
        f"max_mi={cq.max_distance_mi} diff={cq.difficulty!r} features={cq.feature_slugs!r} "
        f"parks={cq.park_codes!r} states={cq.state_slugs!r}"
    )
    result = search_catalog_trails(cq)
    dump_trail_catalog_debug(
        f"intent_search_{(session_id or 'anon')[:12]}",
        {
            "session_id": session_id,
            "query": cq.__dict__,
            "scope": result.get("scope"),
            "count": result.get("count"),
            "deduped_from": result.get("deduped_from"),
            "trail_ids": [
                t.get("trail_id") for t in (result.get("trails") or [])[:10] if isinstance(t, dict)
            ],
        },
    )
    log_trail_catalog(
        f"intent_search_result count={result.get('count')} deduped_from={result.get('deduped_from')} "
        f"scope={result.get('scope')!r}"
    )
    return result


def intent_search_eligible(
    user_message: str,
    *,
    min_trails: int = 3,
    resolution: TripIntentResolution | None = None,
) -> tuple[bool, dict[str, Any]]:
    """True when intent search returns enough trails for the catalog lane."""
    preview = search_trails_for_intent(user_message, resolution, pick_count=min_trails, session_id="eligibility")
    count = int(preview.get("count") or 0)
    return count >= min_trails, preview


def heuristic_pick_intent_trails(candidates: list[dict[str, Any]], *, k: int) -> list[dict[str, Any]]:
    """Rank by search order (distance) with generic reasons."""
    out: list[dict[str, Any]] = []
    for row in candidates[:k]:
        if not isinstance(row, dict):
            continue
        reason = "Catalog intent search match"
        sources = row.get("catalog_sources")
        if isinstance(sources, list) and sources:
            reason = f"Matched via {sources[0]}"
        dist = row.get("distance_mi")
        if isinstance(dist, (int, float)):
            reason += f"; {float(dist):g} mi"
        out.append({**row, "reason": reason})
    return out


def pick_trails_for_intent(
    *,
    user_message: str,
    profile: dict[str, Any],
    resolution: TripIntentResolution | None,
    session_id: str,
    k: int = 3,
    use_llm: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Search catalog by intent, then LLM-rerank (optional) with heuristic fallback.

    Returns ``(picks, meta)`` where meta includes ``pick_method``, ``search``, ``allowed_count``.
    """
    search = search_trails_for_intent(user_message, resolution, pick_count=k, session_id=session_id)
    candidates = [t for t in (search.get("trails") or []) if isinstance(t, dict)]
    meta: dict[str, Any] = {
        "pick_method": "search_rank",
        "search": {
            "scope": search.get("scope"),
            "count": search.get("count"),
            "deduped_from": search.get("deduped_from"),
        },
        "allowed_count": len(candidates),
        "llm_attempted": False,
        "llm_ok": False,
    }
    if not candidates:
        log_trail_catalog(f"intent_pick_empty sid={(session_id or '')[:12]!r}")
        return [], meta

    picks: list[dict[str, Any]] = []
    if use_llm:
        meta["llm_attempted"] = True
        lane_code = (resolution.primary_park_code if resolution else None) or "intent"
        picks = llm_select_trails(
            park_code=lane_code,
            park_trails=candidates,
            user_message=user_message,
            profile=profile,
            k=k,
            session_id=session_id,
        )
        meta["llm_ok"] = bool(picks)
        if picks:
            meta["pick_method"] = "llm"

    if not picks:
        picks = heuristic_pick_intent_trails(candidates, k=k)
        if not meta.get("llm_ok"):
            meta["pick_method"] = "search_rank"

    dump_trail_catalog_debug(
        f"intent_pick_{(session_id or 'anon')[:12]}",
        {
            "session_id": session_id,
            "meta": meta,
            "picks": [
                {"trail_id": p.get("trail_id"), "name": p.get("name"), "reason": p.get("reason")}
                for p in picks
            ],
        },
    )
    log_trail_catalog(
        f"intent_pick method={meta['pick_method']} picks={len(picks)} "
        f"ids={[p.get('trail_id') for p in picks[:5]]!r} sid={(session_id or '')[:12]!r}"
    )
    return picks[:k], meta


def resolve_trail_entry_for_card(row: dict[str, Any]) -> dict[str, Any]:
    """Prefer full park catalog row; fall back to compact search row."""
    tid = str(row.get("trail_id") or row.get("canonical_trail_id") or "")
    pc = str(row.get("park_code") or "").strip().lower()
    url = str(row.get("source_url") or "").strip()
    if pc and tid:
        try:
            doc = load_park_catalog(pc)
            full = entry_by_trail_id(doc, tid)
            if not full:
                slug = tid.rsplit("-", 1)[-1] if tid.startswith("trail:") else tid
                full = entry_by_trail_id(doc, slug)
            if full:
                return {**full, **{k: v for k, v in row.items() if v is not None}}
        except (FileNotFoundError, ValueError):
            pass
    resolved = resolve_catalog_trail_entry(trail_id=tid, source_url=url, park_code_hint=pc or None)
    if resolved:
        owner_code, entry, _kind = resolved
        merged = {**entry, **{k: v for k, v in row.items() if v is not None}}
        merged.setdefault("park_code", owner_code)
        return merged
    return row
