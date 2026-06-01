#!/usr/bin/env python3
"""
CLI prototype: grounded trail catalog → pick trails → build backpacking itineraries.

Follows the hybrid plan (park filter → LLM picker → catalog-grounded itinerary LLM).

Example:
  cd "Travel Planner Prototype"
  python scripts/grounded_trail_prototype.py \\
    --message "4 days 3 nights backpacking in Yellowstone" \\
    --days 4 --nights 3 --itineraries 2

Dry-run (no Cursor API — heuristic trail pick only):
  python scripts/grounded_trail_prototype.py --dry-run --message "..."
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.wanderly.trail_catalog.pipeline import (  # noqa: E402
    destination_card_from_trail,
    full_catalog_entry,
    llm_build_itinerary,
    llm_select_trails,
)
from src.wanderly.trail_catalog.store import (  # noqa: E402
    heuristic_pick_trails,
    load_park_trails_compact,
    prefilter_backpacking_candidates,
    resolve_park_code_from_text,
)
from src.wanderly.trail_catalog.validate import validate_itinerary_against_catalog  # noqa: E402

_DEFAULT_PROFILE = {
    "traveler_origin": "Seattle, WA",
    "group_size": 2,
    "group_type": "couple",
    "pace_preference": "moderate",
    "trip_style": "backpacking",
    "preferred_activities": ["hiking", "backcountry camping"],
}


def _print_phase(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def run_prototype(
    *,
    user_message: str,
    park_code: str | None,
    days: int,
    nights: int,
    itineraries: int,
    dry_run: bool,
    catalog_path: Path | None,
    profile: dict[str, Any],
    out_path: Path | None,
) -> dict[str, Any]:
    resolved_park = park_code or resolve_park_code_from_text(user_message)
    if not resolved_park:
        raise SystemExit("Could not resolve park_code from message; pass --park yell")

    _print_phase(f"Park filter: {resolved_park}")
    doc, compact = load_park_trails_compact(resolved_park, catalog_path=catalog_path)
    print(f"Catalog: {len(compact)} trails in {doc.get('park_name')}")

    candidates = prefilter_backpacking_candidates(compact)
    print(f"Backpacking pre-filter: {len(candidates)} candidates (from {len(compact)})")

    _print_phase(f"Trail picker (top {itineraries})")
    if dry_run:
        picks = heuristic_pick_trails(candidates, k=itineraries)
        for p in picks:
            p["reason"] = "(dry-run heuristic pick)"
        print("DRY-RUN: skipped LLM picker")
    else:
        picks = llm_select_trails(
            park_code=resolved_park,
            park_trails=candidates,
            user_message=user_message,
            profile=profile,
            k=itineraries,
        )
        if not picks:
            print("LLM picker returned nothing; falling back to heuristic picks", flush=True)
            picks = heuristic_pick_trails(candidates, k=itineraries)
            for p in picks:
                p.setdefault("reason", "(heuristic fallback)")

    for i, p in enumerate(picks, 1):
        print(f"  {i}. {p.get('trail_id')} | {p.get('name')} | {p.get('distance_mi')} mi")
        print(f"     {p.get('source_url')}")
        print(f"     {p.get('reason', '')}")

    results: list[dict[str, Any]] = []
    for pick in picks:
        tid = str(pick.get("trail_id") or "")
        full = full_catalog_entry(doc, tid) or pick
        dest = destination_card_from_trail(full, reason=str(pick.get("reason") or ""))

        _print_phase(f"Itinerary: {dest.get('name')} ({days}d / {nights}n)")
        if dry_run:
            itin = {
                "grounding": dest.get("grounding"),
                "itinerary": [],
                "outdoor_adventure": {"trail_name": dest.get("name")},
                "planning_notes": "DRY-RUN: no LLM itinerary built.",
            }
            validation_errors = ["dry-run: itinerary not built"]
        else:
            itin = llm_build_itinerary(
                selected_trail=full,
                destination=dest,
                user_message=user_message,
                profile=profile,
                days=days,
                nights=nights,
            )
            if not itin:
                print("  ERROR: itinerary LLM returned no JSON", flush=True)
                validation_errors = ["itinerary LLM parse failed"]
            else:
                validation_errors = validate_itinerary_against_catalog(itin, catalog_entry=full)
                if validation_errors:
                    print("  Validation:", "; ".join(validation_errors), flush=True)
                else:
                    print("  Validation: OK", flush=True)
                days_out = itin.get("itinerary") if isinstance(itin.get("itinerary"), list) else []
                print(f"  Days planned: {len(days_out)}", flush=True)
                for row in days_out[:4]:
                    if isinstance(row, dict):
                        print(f"    Day {row.get('day')}: {row.get('label')}", flush=True)

        results.append(
            {
                "destination": dest,
                "catalog_entry": full,
                "itinerary": itin,
                "validation_errors": validation_errors,
            }
        )

    run_doc = {
        "prototype": "grounded_trail_catalog",
        "run_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user_message": user_message,
        "park_code": resolved_park,
        "days": days,
        "nights": nights,
        "dry_run": dry_run,
        "catalog_trail_count": len(compact),
        "candidate_count": len(candidates),
        "results": results,
    }

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(run_doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"\nWrote {out_path}")

    return run_doc


def main() -> int:
    parser = argparse.ArgumentParser(description="Grounded trail catalog CLI prototype.")
    parser.add_argument(
        "--message",
        default="4 days 3 nights backpacking trip in Yellowstone National Park",
        help="User trip request",
    )
    parser.add_argument("--park", default=None, help="Park code override (e.g. yell)")
    parser.add_argument("--days", type=int, default=4)
    parser.add_argument("--nights", type=int, default=3)
    parser.add_argument("--itineraries", type=int, default=2, help="How many catalog trails / itineraries")
    parser.add_argument("--dry-run", action="store_true", help="Heuristic picks only; no Cursor LLM")
    parser.add_argument("--catalog", type=Path, default=None, help="Path to park catalog JSON")
    parser.add_argument("--profile-json", type=Path, default=None, help="Optional traveler profile JSON")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / ".cache" / "grounded_trail_prototype_last.json",
    )
    args = parser.parse_args()

    profile = dict(_DEFAULT_PROFILE)
    if args.profile_json and args.profile_json.is_file():
        loaded = json.loads(args.profile_json.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            profile.update(loaded.get("profile") or loaded)

    try:
        run_prototype(
            user_message=args.message.strip(),
            park_code=args.park,
            days=args.days,
            nights=args.nights,
            itineraries=max(1, args.itineraries),
            dry_run=args.dry_run,
            catalog_path=args.catalog,
            profile=profile,
            out_path=args.out,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
