#!/usr/bin/env python3
"""
Prune ``.cache/wanderly_session_snapshots.json`` to sessions still registered in
``wanderly_trips/manifest.json`` (plus optional empty-session purge).

This file is NOT a soft-delete store: each session key is a full snapshot. The file
grows large because old browser sessions stay forever and each snapshot duplicates
``ui_destinations`` + ``last_destinations`` with full itineraries and chat history.

Usage:
  python scripts/cleanup_wanderly_session_snapshots.py --dry-run
  python scripts/cleanup_wanderly_session_snapshots.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / ".cache" / "wanderly_session_snapshots.json"
MANIFEST = ROOT / ".cache" / "wanderly_trips" / "manifest.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_session_ids() -> set[str]:
    if not MANIFEST.is_file():
        return set()
    raw = _load_json(MANIFEST)
    trips = raw.get("trips") if isinstance(raw, dict) else None
    if not isinstance(trips, list):
        return set()
    out: set[str] = set()
    for row in trips:
        if isinstance(row, dict):
            sid = str(row.get("session_id") or "").strip()
            if sid:
                out.add(sid)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print plan only; do not write.")
    parser.add_argument(
        "--drop-empty",
        action="store_true",
        help="Also remove kept manifest sessions with no ui_destinations and no chat.",
    )
    args = parser.parse_args()

    if not SNAPSHOTS.is_file():
        print(f"No snapshots file at {SNAPSHOTS}")
        return 0

    doc = _load_json(SNAPSHOTS)
    by = doc.get("by_session")
    if not isinstance(by, dict):
        print("Invalid snapshots format (missing by_session).")
        return 1

    keep_ids = _manifest_session_ids()
    if not keep_ids:
        print("Warning: manifest has no session_ids; aborting to avoid wiping everything.")
        return 1

    before_bytes = SNAPSHOTS.stat().st_size
    before_n = len(by)

    kept: dict = {}
    dropped: list[str] = []
    for sid, blob in by.items():
        if sid not in keep_ids:
            dropped.append(sid)
            continue
        if args.drop_empty and isinstance(blob, dict):
            ui = blob.get("ui_destinations") or []
            ch = blob.get("chat") or []
            if not ui and not ch:
                dropped.append(sid)
                continue
        kept[sid] = blob

    print(f"Sessions before: {before_n}  after: {len(kept)}  dropped: {len(dropped)}")
    print(f"Size before: {before_bytes:,} bytes")
    for sid in dropped:
        print(f"  drop {sid}")

    if args.dry_run:
        est = sum(len(json.dumps(v, ensure_ascii=True)) for v in kept.values())
        print(f"(dry-run) rough kept payload ~{est:,} chars; would write {len(kept)} sessions")
        return 0

    backup = SNAPSHOTS.with_suffix(".json.bak")
    shutil.copy2(SNAPSHOTS, backup)
    out_doc = {"v": doc.get("v", 1), "by_session": kept}
    text = json.dumps(out_doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    SNAPSHOTS.write_text(text, encoding="utf-8")
    after_bytes = SNAPSHOTS.stat().st_size
    print(f"Wrote {SNAPSHOTS}")
    print(f"Backup: {backup}")
    print(f"Size after: {after_bytes:,} bytes ({100 * after_bytes / max(before_bytes, 1):.1f}% of before)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
