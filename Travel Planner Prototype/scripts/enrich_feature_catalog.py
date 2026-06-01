#!/usr/bin/env python3
"""
Offline enrichment for existing ``data/trail_catalog/features/*.json``.

Fills from data we already have (no new AllTrails fetch):
  - ``country_code``, ``state_slug``, ``region_path``, ``state_name`` from trail_id / source_url
  - ``park_code`` via park URL index + park_url + location_label heuristics

Example:

  cd "Travel Planner Prototype"
  python scripts/enrich_feature_catalog.py
  python scripts/enrich_feature_catalog.py --feature waterfall --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich feature catalog JSON from existing metadata.")
    parser.add_argument("--feature", action="append", dest="features", help="Feature slug(s); default all")
    parser.add_argument("--dry-run", action="store_true", help="Print stats only; do not write files")
    args = parser.parse_args()

    from src.wanderly.trail_catalog.enrich import enrich_all_feature_catalogs

    catalog_root = ROOT / "data" / "trail_catalog"
    summaries = enrich_all_feature_catalogs(
        catalog_root,
        slugs=args.features,
        dry_run=args.dry_run,
    )
    print(json.dumps(summaries, indent=2, ensure_ascii=True), flush=True)
    if not summaries:
        print("No feature catalogs found.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
