#!/usr/bin/env python3
"""
Crawl top trails from one AllTrails US region hub into ``data/trail_catalog/regions/``.

  python scripts/crawl_alltrails_region_catalog.py utah --connect-cdp http://127.0.0.1:9222
  python scripts/crawl_alltrails_region_catalog.py https://www.alltrails.com/us/utah --trail-limit 50
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    for p in (ROOT.parent / ".env", ROOT / ".env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    _load_dotenv()
    from src.wanderly.alltrails_region_directory import (
        normalize_us_region_listing_url,
        region_listing_url_for_slug,
    )
    from src.wanderly.alltrails_region_crawl import crawl_region_hub_trails_playwright
    from src.wanderly.trail_catalog.enrich import build_park_codes_by_state, enrich_region_trail_rows
    from src.wanderly.trail_catalog.write_region import (
        region_catalog_path,
        update_regions_manifest_entry,
        write_region_catalog_file,
    )
    from src.wanderly.trail_catalog.enrich import state_name_from_slug

    parser = argparse.ArgumentParser(description="Crawl one US region hub (top trails).")
    parser.add_argument("region", help="State slug (utah) or full /us/utah URL")
    parser.add_argument("--trail-limit", type=int, default=50)
    parser.add_argument("--max-rounds", type=int, default=60)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--connect-cdp", default=None)
    parser.add_argument("--use-current-tab", action="store_true")
    parser.add_argument("--channel", default="chrome")
    parser.add_argument("--storage-state", type=Path, default=ROOT / ".cache" / "alltrails_storage.json")
    args = parser.parse_args()

    raw = (args.region or "").strip()
    if raw.startswith("http"):
        hub_url = normalize_us_region_listing_url(raw)
        slug = hub_url.rstrip("/").split("/")[-1].lower()
    else:
        slug = raw.lower().replace("us-", "")
        hub_url = region_listing_url_for_slug(slug)

    storage = str(args.storage_state) if args.storage_state.is_file() else None
    channel = args.channel if args.channel and args.channel != "chromium" else None
    if args.channel == "chromium":
        channel = None

    rows = crawl_region_hub_trails_playwright(
        hub_url,
        trail_limit=args.trail_limit,
        max_rounds=args.max_rounds,
        headless=not args.headed,
        storage_state_path=storage,
        browser_channel=channel,
        connect_cdp_url=args.connect_cdp or None,
        use_current_tab=args.use_current_tab or bool(args.connect_cdp),
    )

    catalog_root = ROOT / "data" / "trail_catalog"
    entries = enrich_region_trail_rows(rows, state_slug=slug, catalog_root=catalog_root)
    region_name = state_name_from_slug(slug) or slug.replace("-", " ").title()
    out_path = write_region_catalog_file(
        ROOT,
        state_slug=slug,
        region_name=region_name,
        listing_url=hub_url,
        entries=entries,
        method="playwright_region_hub",
        out_path=region_catalog_path(catalog_root, slug),
    )
    parks_by_state = build_park_codes_by_state(catalog_root)
    rel = str(out_path.relative_to(ROOT)).replace("\\", "/")
    update_regions_manifest_entry(
        catalog_root / "regions_manifest.json",
        state_slug=slug,
        region_name=region_name,
        listing_url=hub_url,
        trail_count=len(entries),
        catalog_file=rel,
        park_codes=parks_by_state.get(slug),
    )
    resolved = sum(1 for e in entries if e.get("park_code"))
    print(
        f"Wrote {len(entries)} trails ({resolved} with park_code) -> {out_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
