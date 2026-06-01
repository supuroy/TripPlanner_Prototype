#!/usr/bin/env python3
"""Dump one destination card from trip disk or session snapshots for debugging."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _find_card(blob: dict, dest_id: str) -> dict | None:
    session = blob.get("session") if isinstance(blob.get("session"), dict) else blob
    for field in ("ui_destinations", "last_destinations"):
        for d in session.get(field) or []:
            if isinstance(d, dict) and str(d.get("id") or "") == dest_id:
                return {"source_field": field, "card": d}
    by = blob.get("by_session")
    if isinstance(by, dict):
        for sid, entry in by.items():
            for field in ("ui_destinations", "last_destinations"):
                for d in entry.get(field) or []:
                    if isinstance(d, dict) and str(d.get("id") or "") == dest_id:
                        return {"session_id": sid, "source_field": field, "card": d}
    return None


def _atc_summary(ctx: Any) -> dict[str, Any]:
    if not isinstance(ctx, dict):
        return {"present": False}
    parent = str(ctx.get("parent_alltrails_url") or "").strip()
    return {
        "present": True,
        "trail_name": ctx.get("trail_name"),
        "verification_status": ctx.get("verification_status"),
        "verified": ctx.get("verified"),
        "has_parent_url": bool(parent),
        "parent_alltrails_url": parent[:120] if parent else None,
        "search_query": (str(ctx.get("search_query") or "")[:120] or None),
    }


def _tail_log_lines(path: Path, *, patterns: list[str], limit: int = 5) -> list[str]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    matched: list[str] = []
    for line in reversed(lines):
        low = line.lower()
        if any(p.lower() in low for p in patterns):
            matched.append(line.strip())
            if len(matched) >= limit:
                break
    return list(reversed(matched))


def _parse_log_timestamp(line: str) -> str | None:
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T[\d:.]+Z?)", line)
    return m.group(1) if m else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect destination card + recent AllTrails / itinerary build log lines.",
    )
    parser.add_argument("--dest-id", default="heart-lake-trail")
    parser.add_argument("--trip-id", default="trip-0303b791485e")
    parser.add_argument(
        "--log-filter",
        default="Heart Lake",
        help="Substring for alltrails_debug.log tail (default: Heart Lake)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / ".cache" / "_inspect_destination_card.json",
    )
    args = parser.parse_args()

    trip_path = ROOT / ".cache" / "wanderly_trips" / f"{args.trip_id}.json"
    snap_path = ROOT / ".cache" / "wanderly_session_snapshots.json"
    alltrails_log = ROOT / ".cache" / "alltrails_debug.log"
    itin_log = ROOT / ".cache" / "itinerary_debug.log"
    build_dump = ROOT / ".cache" / "_itinerary_build_debug" / f"{args.dest_id}_latest.json"

    report: dict[str, Any] = {
        "destination_id": args.dest_id,
        "trip_file": str(trip_path),
        "snapshots_file": str(snap_path),
        "inspected_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    trip_updated_ms: int | None = None
    if trip_path.is_file():
        trip_doc = json.loads(trip_path.read_text(encoding="utf-8"))
        trip_updated_ms = trip_doc.get("updated_at_ms")
        if isinstance(trip_updated_ms, (int, float)):
            report["trip_updated_at_utc"] = datetime.fromtimestamp(
                float(trip_updated_ms) / 1000.0, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        hit = _find_card(trip_doc, args.dest_id)
        report["from_trip_disk"] = hit
        if hit and isinstance(hit.get("card"), dict):
            report["trip_disk_atc"] = _atc_summary(hit["card"].get("alltrails_search_context"))
    else:
        report["from_trip_disk"] = None

    if snap_path.is_file():
        hit = _find_card(json.loads(snap_path.read_text(encoding="utf-8")), args.dest_id)
        report["from_session_snapshots"] = hit
        if hit and isinstance(hit.get("card"), dict):
            report["snapshot_atc"] = _atc_summary(hit["card"].get("alltrails_search_context"))
    else:
        report["from_session_snapshots"] = None

    if report.get("from_trip_disk") and isinstance(report["from_trip_disk"].get("card"), dict):
        card = report["from_trip_disk"]["card"]
        report["summary"] = {
            "keys": sorted(card.keys()),
            "alltrails_search_context": card.get("alltrails_search_context"),
            "trail_lookups_count": len(card.get("trail_lookups") or {}),
            "itinerary_days": len(card.get("itinerary") or []),
            "has_waypoint_sheet": bool(card.get("waypoint_sheet")),
            "has_nps_outdoor": bool(card.get("nps_outdoor")),
        }

    report["logs"] = {
        "alltrails_debug_tail": _tail_log_lines(
            alltrails_log,
            patterns=[args.log_filter, "verify_", args.dest_id],
            limit=8,
        ),
        "itinerary_build_tail": _tail_log_lines(
            itin_log,
            patterns=["build_itinerary_stream", "ALLTRAILS_", args.dest_id],
            limit=8,
        ),
    }

    verify_lines = report["logs"]["alltrails_debug_tail"]
    if verify_lines:
        report["last_verify_log_timestamp"] = _parse_log_timestamp(verify_lines[-1])
    build_lines = report["logs"]["itinerary_build_tail"]
    if build_lines:
        report["last_build_log_timestamp"] = _parse_log_timestamp(build_lines[-1])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    print(f"Wrote {args.out}")
    print()
    if report.get("trip_disk_atc"):
        print("trip_disk_atc:", json.dumps(report["trip_disk_atc"], indent=2))
    if report.get("snapshot_atc"):
        print("snapshot_atc:", json.dumps(report["snapshot_atc"], indent=2))
    if report.get("summary"):
        print("summary:", json.dumps(report["summary"], indent=2))
    if report.get("itinerary_build_dump"):
        dump = report["itinerary_build_dump"]
        if isinstance(dump, dict) and "error" not in dump:
            print("itinerary_build_dump:", json.dumps(
                {
                    "written_at_utc": dump.get("written_at_utc"),
                    "llm_wrapper": dump.get("llm_wrapper"),
                    "after_verify": dump.get("after_verify"),
                    "http": dump.get("http"),
                },
                indent=2,
            ))
        else:
            print("itinerary_build_dump:", json.dumps(dump, indent=2))
    elif report.get("itinerary_build_dump_path"):
        print(f"itinerary_build_dump: (none — set WANDERLY_ALLTRAILS_DEBUG=1 and rebuild)")
        print(f"  expected: {report['itinerary_build_dump_path']}")
    print()
    print("--- alltrails_debug.log (tail) ---")
    for line in report["logs"]["alltrails_debug_tail"] or ["(no matching lines)"]:
        print(line)
    print()
    print("--- itinerary_debug.log (tail) ---")
    for line in report["logs"]["itinerary_build_tail"] or ["(no matching lines)"]:
        print(line)
    if report.get("trip_updated_at_utc") and report.get("last_verify_log_timestamp"):
        print()
        print(f"trip_updated_at_utc: {report['trip_updated_at_utc']}")
        print(f"last_verify_log_timestamp: {report['last_verify_log_timestamp']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
