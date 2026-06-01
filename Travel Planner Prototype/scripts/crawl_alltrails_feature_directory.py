#!/usr/bin/env python3
"""
Discover trail features from the AllTrails hub and crawl each into ``features/``.

Hub: https://www.alltrails.com/directory/trail-features

Chrome CDP (recommended):

  cd "Travel Planner Prototype"
  python scripts/crawl_alltrails_feature_directory.py \\
    --connect-cdp http://127.0.0.1:9222 --limit 5

Discover only:

  python scripts/crawl_alltrails_feature_directory.py --discover-only \\
    --connect-cdp http://127.0.0.1:9222

Crawl explicit slugs (skip hub):

  python scripts/crawl_alltrails_feature_directory.py \\
    --features waterfall camping hot-springs \\
    --connect-cdp http://127.0.0.1:9222
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
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


def _catalog_path_for_feature(root: Path, feature_slug: str) -> Path:
    return root / "data" / "trail_catalog" / "features" / f"{feature_slug.lower()}.json"


def _should_skip_existing(path: Path, *, min_trails: int) -> bool:
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
    from src.wanderly.alltrails_feature_directory import (
        TRAIL_FEATURES_HUB_URL,
        feature_listing_url_for_slug,
        parse_feature_listing_url,
    )

    parser = argparse.ArgumentParser(
        description="Batch crawl AllTrails trail-feature listings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "hub_url",
        nargs="?",
        default=None,
        help=f"Trail-features hub URL (default: {TRAIL_FEATURES_HUB_URL})",
    )
    parser.add_argument(
        "--features",
        nargs="*",
        default=None,
        help="Explicit feature slugs to crawl (skips hub discovery)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max features to crawl from hub")
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--skip-existing-min", type=int, default=10)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--pause-for-captcha", action="store_true")
    parser.add_argument("--max-rounds", type=int, default=80)
    parser.add_argument("--expected-min", type=int, default=10)
    parser.add_argument("--max-directory-pages", type=int, default=1, dest="max_pages")
    parser.add_argument("--city-limit", type=int, default=50, help="0 = no limit")
    parser.add_argument("--discovery", choices=("cities", "direct"), default="cities")
    parser.add_argument("--delay-between-cities-s", type=float, default=1.0)
    parser.add_argument("--directory-scroll-rounds", type=int, default=15)
    parser.add_argument("--delay-s", type=float, default=2.0)
    parser.add_argument("--no-resolve-park", action="store_true")
    parser.add_argument(
        "--storage-state",
        type=Path,
        default=ROOT / ".cache" / "alltrails_storage.json",
    )
    parser.add_argument("--save-storage", type=Path, default=None)
    parser.add_argument("--connect-cdp", default=None, metavar="URL")
    parser.add_argument("--use-current-tab", action="store_true")
    parser.add_argument("--channel", default="chrome")
    parser.add_argument(
        "--discover-out",
        type=Path,
        default=ROOT / ".cache" / "alltrails_directory_features.json",
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        default=ROOT / ".cache" / "alltrails_feature_batch_report.json",
    )
    args = parser.parse_args()

    storage = str(args.storage_state) if args.storage_state.is_file() else None
    save_storage = args.save_storage
    if save_storage is None and args.pause_for_captcha:
        save_storage = args.storage_state
    channel = args.channel if args.channel and args.channel != "chromium" else None
    if args.channel == "chromium":
        channel = None
    use_current_tab = args.use_current_tab or bool(args.connect_cdp)

    from src.wanderly.alltrails_feature_crawl import (
        crawl_feature_directory_playwright,
        crawl_features_batch_playwright,
    )
    from src.wanderly.trail_catalog.resolve_park import enrich_feature_trail_rows
    from src.wanderly.trail_catalog.write_feature import write_feature_catalog_files

    if args.features:
        feature_rows = [
            {
                "url": feature_listing_url_for_slug(s),
                "slug": s.strip().lower(),
                "name": s.replace("-", " ").title(),
            }
            for s in args.features
            if (s or "").strip()
        ]
    else:
        hub = (args.hub_url or TRAIL_FEATURES_HUB_URL).strip()
        feature_rows = crawl_feature_directory_playwright(
            hub,
            limit=args.limit,
            headless=not args.headed,
            directory_scroll_rounds=args.directory_scroll_rounds,
            storage_state_path=storage,
            save_storage_state_path=str(save_storage) if save_storage else None,
            pause_for_captcha=args.pause_for_captcha,
            browser_channel=channel,
            connect_cdp_url=args.connect_cdp or None,
            use_current_tab=use_current_tab,
        )

    payload = {
        "written_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "feature_count": len(feature_rows),
        "features": feature_rows,
    }
    args.discover_out.parent.mkdir(parents=True, exist_ok=True)
    args.discover_out.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Discovered {len(feature_rows)} features -> {args.discover_out}")

    if args.discover_only:
        for f in feature_rows[:15]:
            print(f"  {f.get('slug') or '?'} | {f.get('url')}")
        if len(feature_rows) > 15:
            print(f"  ... and {len(feature_rows) - 15} more")
        return 0

    catalog_root = ROOT / "data" / "trail_catalog"
    url_index = None
    if not args.no_resolve_park:
        from src.wanderly.trail_catalog.resolve_park import build_trail_url_to_park_index

        url_index = build_trail_url_to_park_index(catalog_root)
        print(f"[resolve] park catalog index: {len(url_index)} trail URLs", flush=True)

    written: list[dict[str, Any]] = []
    skipped: list[str] = []

    def should_crawl(slug: str, _url: str) -> bool:
        if not args.skip_existing:
            return True
        path = _catalog_path_for_feature(ROOT, slug)
        if _should_skip_existing(path, min_trails=args.skip_existing_min):
            print(f"[skip] existing {path}", flush=True)
            skipped.append(slug)
            return False
        return True

    def on_feature(meta: Any, raw_rows: list[dict[str, Any]]) -> None:
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
                for i, r in enumerate(raw_rows)
                if r.get("url")
            ]
        else:
            entries = enrich_feature_trail_rows(
                raw_rows,
                feature_slug=meta.feature_slug,
                catalog_root=catalog_root,
                url_index=url_index,
            )
        out_path = _catalog_path_for_feature(ROOT, meta.feature_slug)
        path = write_feature_catalog_files(
            ROOT,
            feature_slug=meta.feature_slug,
            feature_name=meta.feature_name,
            listing_url=meta.listing_url,
            entries=entries,
            method="playwright_feature_batch",
            out_path=out_path,
        )
        resolved = sum(1 for e in entries if e.get("park_code"))
        print(
            f"[wrote] {meta.feature_slug}: {len(entries)} trails "
            f"({resolved} park_code) -> {path}",
            flush=True,
        )
        written.append(
            {
                "feature_slug": meta.feature_slug,
                "trail_count": len(entries),
                "park_code_resolved": resolved,
                "file": str(path.relative_to(ROOT)).replace("\\", "/"),
            }
        )

    urls = [str(f["url"]) for f in feature_rows if f.get("url")]
    city_limit = args.city_limit if args.city_limit > 0 else None
    crawl_features_batch_playwright(
        urls,
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
        delay_between_features_s=args.delay_s,
        on_feature_crawled=on_feature,
        should_crawl_feature=should_crawl,
        use_current_tab=use_current_tab,
    )

    report = {
        "written_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "written": written,
        "skipped_slugs": skipped,
    }
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Batch done: wrote {len(written)}, skipped {len(skipped)} -> {args.report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
