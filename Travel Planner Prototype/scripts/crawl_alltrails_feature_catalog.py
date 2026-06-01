#!/usr/bin/env python3
"""
Crawl one AllTrails **trail feature** listing into ``data/trail_catalog/features/``.

Example URLs:
  https://www.alltrails.com/directory/trail-features/waterfall/A/1
  https://www.alltrails.com/directory/trail-features/camping/A/1

Recommended (Chrome CDP — same as park catalog):

  cd "Travel Planner Prototype"
  python scripts/crawl_alltrails_feature_catalog.py waterfall \\
    --connect-cdp http://127.0.0.1:9222

  python scripts/crawl_alltrails_feature_catalog.py \\
    "https://www.alltrails.com/directory/trail-features/hot-springs/A/1" \\
    --connect-cdp http://127.0.0.1:9222 --max-pages 3

Requires: ``pip install playwright`` (and ``playwright install chromium`` if not using CDP).
"""

from __future__ import annotations

import argparse
import json
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


def _catalog_path_for_feature(root: Path, feature_slug: str) -> Path:
    return root / "data" / "trail_catalog" / "features" / f"{feature_slug.lower()}.json"


def main() -> int:
    _load_dotenv()
    from src.wanderly.alltrails_feature_directory import (
        feature_listing_url_for_slug,
        parse_feature_listing_url,
    )

    parser = argparse.ArgumentParser(
        description="Crawl one AllTrails trail-feature listing page into the offline catalog.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "feature",
        help="Feature slug (e.g. waterfall) or full listing URL",
    )
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--pause-for-captcha", action="store_true")
    parser.add_argument("--max-rounds", type=int, default=80, help="Scroll rounds on listing")
    parser.add_argument("--expected-min", type=int, default=10, help="Early-stop trail count per city page")
    parser.add_argument(
        "--max-directory-pages",
        type=int,
        default=1,
        dest="max_pages",
        help="Directory A/N pages to scan for city links (default 1 = Top 50 + letter page)",
    )
    parser.add_argument(
        "--city-limit",
        type=int,
        default=50,
        help="Max city listing pages to crawl per feature (default 50; 0 = no limit)",
    )
    parser.add_argument(
        "--discovery",
        choices=("cities", "direct"),
        default="cities",
        help="cities: directory -> /us/.../feature pages (default); direct: scroll directory only",
    )
    parser.add_argument("--delay-between-cities-s", type=float, default=1.0)
    parser.add_argument(
        "--no-resolve-park",
        action="store_true",
        help="Skip park_code join against existing parks/*.json",
    )
    parser.add_argument(
        "--storage-state",
        type=Path,
        default=ROOT / ".cache" / "alltrails_storage.json",
    )
    parser.add_argument("--save-storage", type=Path, default=None)
    parser.add_argument("--connect-cdp", default=None, metavar="URL")
    parser.add_argument(
        "--use-current-tab",
        action="store_true",
        help="CDP: use the feature listing already open in Chrome",
    )
    parser.add_argument("--channel", default="chrome")
    parser.add_argument(
        "--discover-out",
        type=Path,
        default=None,
        help="Optional JSON dump of raw listing rows (debug)",
    )
    args = parser.parse_args()

    raw = (args.feature or "").strip()
    if raw.startswith("http"):
        meta = parse_feature_listing_url(raw)
    else:
        meta = parse_feature_listing_url(feature_listing_url_for_slug(raw))

    storage = str(args.storage_state) if args.storage_state.is_file() else None
    save_storage = args.save_storage
    if save_storage is None and args.pause_for_captcha:
        save_storage = args.storage_state
    channel = args.channel if args.channel and args.channel != "chromium" else None
    if args.channel == "chromium":
        channel = None

    from src.wanderly.alltrails_feature_crawl import crawl_feature_trails_playwright
    from src.wanderly.trail_catalog.resolve_park import enrich_feature_trail_rows
    from src.wanderly.trail_catalog.write_feature import write_feature_catalog_files

    print(f"Crawling feature {meta.feature_slug!r} -> {meta.listing_url}", flush=True)
    city_limit = args.city_limit if args.city_limit > 0 else None
    rows = crawl_feature_trails_playwright(
        meta.listing_url,
        headless=not args.headed,
        max_rounds=args.max_rounds,
        expected_min=args.expected_min,
        max_pages=max(1, args.max_pages),
        city_limit=city_limit,
        discovery=args.discovery,
        delay_between_cities_s=args.delay_between_cities_s,
        storage_state_path=storage,
        save_storage_state_path=str(save_storage) if save_storage else None,
        pause_for_captcha=args.pause_for_captcha,
        browser_channel=channel,
        connect_cdp_url=args.connect_cdp or None,
        use_current_tab=args.use_current_tab or bool(args.connect_cdp),
    )

    if args.discover_out:
        args.discover_out.parent.mkdir(parents=True, exist_ok=True)
        args.discover_out.write_text(
            json.dumps({"feature": meta.feature_slug, "rows": rows}, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote raw rows -> {args.discover_out}")

    catalog_root = ROOT / "data" / "trail_catalog"
    if args.no_resolve_park:
        entries = [
            {
                "trail_id": f"{meta.feature_slug}:{i}",
                "source_url": r.get("url"),
                "name": r.get("name"),
                "difficulty": r.get("difficulty"),
                "length_mi": r.get("length_mi"),
                "rating": r.get("rating"),
                "location_label": r.get("location_label"),
                "park_url": r.get("park_url"),
            }
            for i, r in enumerate(rows)
            if r.get("url")
        ]
    else:
        entries = enrich_feature_trail_rows(
            rows,
            feature_slug=meta.feature_slug,
            catalog_root=catalog_root,
        )

    out_path = _catalog_path_for_feature(ROOT, meta.feature_slug)
    path = write_feature_catalog_files(
        ROOT,
        feature_slug=meta.feature_slug,
        feature_name=meta.feature_name,
        listing_url=meta.listing_url,
        entries=entries,
        method="playwright_feature",
        out_path=out_path,
    )
    resolved = sum(1 for e in entries if e.get("park_code"))
    print(
        f"Wrote {len(entries)} trails ({resolved} with park_code) -> {path}",
        flush=True,
    )
    if not args.no_resolve_park:
        from src.wanderly.trail_catalog.enrich import enrich_feature_catalog_file

        stats = enrich_feature_catalog_file(path, catalog_root=catalog_root)
        print(
            f"Enriched: park_code {stats.get('park_code_before')} -> {stats.get('park_code_after')}, "
            f"state_slug on {stats.get('state_slug_after')}/{stats.get('total')} rows",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
