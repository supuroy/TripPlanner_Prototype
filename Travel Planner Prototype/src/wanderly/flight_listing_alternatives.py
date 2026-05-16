"""Top-N flight picks for UI (ranked_picks + selected_rank on the returned flight card)."""

from __future__ import annotations

from typing import Any

from src.wanderly.flight_listing_extract import llm_listing_row_to_flight_option

MAX_FLIGHT_RANKED_PICKS = 3

_PICK_FIELDS = (
    "airline",
    "route",
    "duration",
    "price",
    "stops",
    "departure_time",
    "arrival_time",
    "airline_logo_url",
    "pick_score",
)


def ordered_listing_indices_from_rule_rows(
    rule_rows: list[dict[str, Any]],
    *,
    winner_index: int | None,
    max_picks: int = MAX_FLIGHT_RANKED_PICKS,
) -> list[int]:
    rows = [r for r in rule_rows if isinstance(r, dict)]
    rows.sort(
        key=lambda r: (
            -float(r.get("score") or 0),
            int(r.get("usd") if str(r.get("usd")).isdigit() else 999_999),
            int(r.get("i") if r.get("i") is not None else 999),
        )
    )
    out: list[int] = []
    seen: set[int] = set()
    if winner_index is not None:
        try:
            wi = int(winner_index)
            if wi >= 0:
                out.append(wi)
                seen.add(wi)
        except (TypeError, ValueError):
            pass
    for r in rows:
        try:
            i = int(r.get("i"))
        except (TypeError, ValueError):
            continue
        if i < 0 or i in seen:
            continue
        seen.add(i)
        out.append(i)
        if len(out) >= max_picks:
            break
    return out[:max_picks]


def listing_row_to_ranked_pick(row: dict[str, Any], *, rank: int) -> dict[str, Any]:
    fo = llm_listing_row_to_flight_option(row)
    pick: dict[str, Any] = {"rank": int(rank)}
    for k in _PICK_FIELDS:
        if k in fo and fo[k] not in (None, ""):
            pick[k] = fo[k]
    return pick


def build_llm_ranked_flight_picks(
    listings_dump: list[dict[str, Any]],
    pick_indices: list[int],
    *,
    rule_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    score_by_i: dict[int, float] = {}
    for r in rule_rows or []:
        if not isinstance(r, dict):
            continue
        try:
            score_by_i[int(r.get("i"))] = float(r.get("score") or 0)
        except (TypeError, ValueError):
            continue
    picks: list[dict[str, Any]] = []
    for rank, i in enumerate(pick_indices[:MAX_FLIGHT_RANKED_PICKS], start=1):
        if i < 0 or i >= len(listings_dump):
            continue
        row = dict(listings_dump[i])
        if i in score_by_i:
            row["pick_score"] = score_by_i[i]
        picks.append(listing_row_to_ranked_pick(row, rank=rank))
    return picks


def apply_pick_to_flight_option(fo: dict[str, Any], pick: dict[str, Any]) -> dict[str, Any]:
    out = dict(fo)
    for k in _PICK_FIELDS:
        if k in pick and pick[k] not in (None, ""):
            out[k] = pick[k]
    out["best"] = True
    return out


def attach_ranked_picks_to_flight_option(
    fo: dict[str, Any],
    ranked_picks: list[dict[str, Any]],
    *,
    selected_rank: int = 1,
) -> dict[str, Any]:
    picks = []
    for i, p in enumerate(ranked_picks[:MAX_FLIGHT_RANKED_PICKS]):
        if not isinstance(p, dict):
            continue
        row = dict(p)
        row["rank"] = int(row.get("rank") or (i + 1))
        picks.append(row)
    if not picks:
        return fo
    out = dict(fo)
    out["ranked_picks"] = picks
    try:
        sr = int(selected_rank)
    except (TypeError, ValueError):
        sr = 1
    if sr < 1 or sr > len(picks):
        sr = 1
    out["selected_rank"] = sr
    sel = next((p for p in picks if int(p.get("rank") or 0) == sr), picks[0])
    out = apply_pick_to_flight_option(out, sel)
    out["best"] = True
    return out
