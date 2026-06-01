#!/usr/bin/env python3
"""
Build ``data/trail_catalog/trail_index.json`` by merging park + feature + region rows.

Re-run after region crawl finishes (or any time catalogs change). Idempotent.

  cd "Travel Planner Prototype"
  python scripts/build_trail_canonical_index.py
  python scripts/build_trail_canonical_index.py --stats-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build canonical trail index from all catalog dimensions.")
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Print merge stats without writing trail_index.json",
    )
    args = parser.parse_args()

    from src.wanderly.trail_catalog.canonical import build_trail_index_doc, write_trail_index

    catalog_root = ROOT / "data" / "trail_catalog"
    doc = build_trail_index_doc(catalog_root)
    summary = {
        "unique_trail_count": doc.get("unique_trail_count"),
        "scan_stats": doc.get("scan_stats"),
        "region_files": len(list((catalog_root / "regions").glob("*.json")))
        if (catalog_root / "regions").is_dir()
        else 0,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=True), flush=True)
    if args.stats_only:
        return 0
    path = write_trail_index(catalog_root)
    print(f"Wrote {doc.get('unique_trail_count')} canonical trails -> {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
