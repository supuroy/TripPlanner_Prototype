#!/usr/bin/env python3
"""
Discover US regions from AllTrails ``/directory/regions/A/1``, then crawl each
``/us/{state}`` hub for up to 50 trails (level 2).

  cd "Travel Planner Prototype"
  python scripts/crawl_alltrails_region_directory.py --connect-cdp http://127.0.0.1:9222

Discover only (no trail crawl):

  python scripts/crawl_alltrails_region_directory.py --discover-only --limit 50

Single-region smoke:

  python scripts/crawl_alltrails_region_catalog.py utah --connect-cdp http://127.0.0.1:9222
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

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


def _should_skip_region(path: Path, *, min_trails: int) -> bool:
    if not path.is_file():
        return False
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    count = doc.get("trail_count")
    if isinstance(count, int) and count >= min_trails:
        return True
    trails = doc.get("trails")
    return isinstance(trails, list) and len(trails) >= min_trails


def main() -> int:
    _load_dotenv()
    from src.wanderly.alltrails_region_directory import normalize_region_directory_url
    from src.wanderly.alltrails_region_crawl import (
        crawl_regions_directory_and_trails_playwright,
        crawl_regions_directory_playwright,
    )
    from src.wanderly.trail_catalog.enrich import build_park_codes_by_state, enrich_region_trail_rows
    from src.wanderly.trail_catalog.enrich import state_name_from_slug
    from src.wanderly.trail_catalog.write_region import (
        merge_regions_with_park_catalog,
        region_catalog_path,
        update_regions_manifest_entry,
        write_region_catalog_file,
        write_regions_manifest,
    )

    parser = argparse.ArgumentParser(
        description="Discover US regions and optionally crawl top trails per region hub.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "directory_url",
        nargs="?",
        default="https://www.alltrails.com/directory/regions/A/1",
    )
    parser.add_argument("--limit", type=int, default=50, help="Max regions from directory")
    parser.add_argument("--trail-limit", type=int, default=50, help="Max trails per region hub")
    parser.add_argument("--max-pages", type=int, default=1, help="Directory A/N pages")
    parser.add_argument("--max-rounds", type=int, default=60, help="Scroll rounds per region hub")
    parser.add_argument(
        "--no-crawl-trails",
        action="store_true",
        help="Only write regions_manifest.json (level 1 discovery)",
    )
    parser.add_argument("--discover-only", action="store_true", help="Print discovery JSON only")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip region if regions/us-{state}.json already has enough trails",
    )
    parser.add_argument("--min-trails", type=int, default=40, help="Threshold for --skip-existing")
    parser.add_argument("--delay-between-regions-s", type=float, default=1.0)
    parser.add_argument("--discover-out", type=Path, default=None)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--pause-for-captcha", action="store_true")
    parser.add_argument("--storage-state", type=Path, default=ROOT / ".cache" / "alltrails_storage.json")
    parser.add_argument("--connect-cdp", default=None)
    parser.add_argument("--use-current-tab", action="store_true")
    parser.add_argument("--channel", default="chrome")
    args = parser.parse_args()

    listing = normalize_region_directory_url(args.directory_url)
    storage = str(args.storage_state) if args.storage_state.is_file() else None
    channel = args.channel if args.channel and args.channel != "chromium" else None
    if args.channel == "chromium":
        channel = None
    region_limit = args.limit if args.limit > 0 else None
    catalog_root = ROOT / "data" / "trail_catalog"
    parks_by_state = build_park_codes_by_state(catalog_root)
    manifest_path = catalog_root / "regions_manifest.json"

    def should_crawl_region(slug: str, _url: str) -> bool:
        if not args.skip_existing:
            return True
        path = region_catalog_path(catalog_root, slug)
        return not _should_skip_region(path, min_trails=args.min_trails)

    def on_region_crawled(summary: dict[str, Any]) -> None:
        slug = str(summary.get("slug") or "").lower()
        hub_url = str(summary.get("url") or "")
        name = str(summary.get("name") or state_name_from_slug(slug) or slug)
        rows = summary.get("trails")
        if not isinstance(rows, list):
            rows = []
        entries = enrich_region_trail_rows(rows, state_slug=slug, catalog_root=catalog_root)
        out_path = write_region_catalog_file(
            ROOT,
            state_slug=slug,
            region_name=name,
            listing_url=hub_url,
            entries=entries,
            method="playwright_region_hub_batch",
            out_path=region_catalog_path(catalog_root, slug),
        )
        rel = str(out_path.relative_to(ROOT)).replace("\\", "/")
        update_regions_manifest_entry(
            manifest_path,
            state_slug=slug,
            region_name=name,
            listing_url=hub_url,
            trail_count=len(entries),
            catalog_file=rel,
            park_codes=parks_by_state.get(slug),
        )

    if args.discover_only or args.no_crawl_trails:
        discovered = crawl_regions_directory_playwright(
            listing,
            limit=region_limit,
            max_pages=max(1, args.max_pages),
            headless=not args.headed,
            storage_state_path=storage,
            save_storage_state_path=str(args.storage_state) if args.pause_for_captcha else None,
            pause_for_captcha=args.pause_for_captcha,
            browser_channel=channel,
            connect_cdp_url=args.connect_cdp or None,
            use_current_tab=args.use_current_tab or bool(args.connect_cdp),
        )
        if args.discover_out:
            args.discover_out.parent.mkdir(parents=True, exist_ok=True)
            args.discover_out.write_text(
                json.dumps({"regions": discovered}, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
        if args.discover_only:
            print(json.dumps(discovered, indent=2, ensure_ascii=True)[:8000], flush=True)
            return 0
        merged = merge_regions_with_park_catalog(discovered, catalog_root)
        path = write_regions_manifest(
            ROOT,
            merged,
            listing_url=listing,
            method="playwright_regions_directory",
        )
        print(f"Wrote {len(merged)} regions (manifest only) -> {path}", flush=True)
        if args.no_crawl_trails:
            print("Tip: re-run without --no-crawl-trails to harvest trails per /us/{state}.", flush=True)
        return 0

    summaries = crawl_regions_directory_and_trails_playwright(
        listing,
        region_limit=region_limit,
        trail_limit=args.trail_limit,
        max_pages=max(1, args.max_pages),
        max_rounds=args.max_rounds,
        headless=not args.headed,
        storage_state_path=storage,
        save_storage_state_path=str(args.storage_state) if args.pause_for_captcha else None,
        pause_for_captcha=args.pause_for_captcha,
        browser_channel=channel,
        connect_cdp_url=args.connect_cdp or None,
        use_current_tab=args.use_current_tab or bool(args.connect_cdp),
        delay_between_regions_s=args.delay_between_regions_s,
        should_crawl_region=should_crawl_region,
        on_region_crawled=on_region_crawled,
    )
    total_trails = sum(int(s.get("trail_count") or 0) for s in summaries)
    print(
        f"Done: {len(summaries)} regions, {total_trails} trails -> "
        f"{catalog_root / 'regions'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
