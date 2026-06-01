#!/usr/bin/env python3
"""
Discover parks from the AllTrails **Top 50 Parks** section and crawl each catalog.

Default listing page: ``https://www.alltrails.com/directory/parks`` (Parks tab with
the curated Top 50 block — same UI as ``/us`` when Parks is selected).

Example with Chrome CDP (recommended):

  cd "Travel Planner Prototype"
  python scripts/crawl_alltrails_park_directory.py \\
    --connect-cdp http://127.0.0.1:9222 \\
    --skip-existing

Discover only (print the 50 park URLs):

  python scripts/crawl_alltrails_park_directory.py --discover-only \\
    --connect-cdp http://127.0.0.1:9222

Alphabetical directory pages (``--discovery all``) instead of Top 50 section:

  python scripts/crawl_alltrails_park_directory.py \\
    "https://www.alltrails.com/directory/parks/A/1" \\
    --discovery all --limit 50

Requires Playwright (AllTrails blocks plain HTTP).
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


def _catalog_path_for_park(root: Path, park_code: str) -> Path:
    return root / "data" / "trail_catalog" / "parks" / f"{park_code.lower()}.json"


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
    parser = argparse.ArgumentParser(
        description="Batch crawl AllTrails parks from directory listing page(s).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    from src.wanderly.alltrails_directory import TOP_50_PARKS_LISTING_URL

    parser.add_argument(
        "directory_urls",
        nargs="*",
        default=None,
        help=f"Parks listing URL(s); default: {TOP_50_PARKS_LISTING_URL}",
    )
    parser.add_argument(
        "--discovery",
        choices=("top-50", "all"),
        default="top-50",
        help="top-50: only the curated Top 50 Parks section (default); all: every park link on the page",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max parks to crawl (default: 50; Top 50 section already caps at 50)",
    )
    parser.add_argument("--discover-only", action="store_true", help="List park URLs only; do not crawl trails")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip parks whose catalog JSON already has enough trails",
    )
    parser.add_argument(
        "--skip-existing-min",
        type=int,
        default=10,
        help="With --skip-existing, minimum trail_count to treat catalog as complete",
    )
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--pause-for-captcha", action="store_true")
    parser.add_argument("--max-rounds", type=int, default=80, help="Scroll rounds per park listing")
    parser.add_argument(
        "--directory-scroll-rounds",
        type=int,
        default=3,
        help="Scroll rounds on listing page (Top 50 needs little scrolling; use 25 for --discovery all)",
    )
    parser.add_argument("--expected-min", type=int, default=50, help="Per-park early-stop trail link count")
    parser.add_argument("--delay-s", type=float, default=2.0, help="Pause between parks (seconds)")
    parser.add_argument(
        "--reuse-tab",
        action="store_true",
        help="Reuse one browser tab for all parks (uses less RAM only if Chrome is not via CDP)",
    )
    parser.add_argument(
        "--gc-every",
        type=int,
        default=5,
        help="Run Python gc every N parks (0=disable, default 5)",
    )
    parser.add_argument(
        "--subprocess-per-park",
        action="store_true",
        help="Crawl each park in a fresh Python process (slowest, lowest peak memory)",
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
        help="CDP: use the park listing already open in Chrome (recommended; avoids captcha on goto)",
    )
    parser.add_argument("--channel", default="chrome")
    parser.add_argument(
        "--discover-out",
        type=Path,
        default=ROOT / ".cache" / "alltrails_directory_parks.json",
        help="Write discovered park list JSON when using --discover-only",
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        default=ROOT / ".cache" / "alltrails_directory_batch_report.json",
    )
    args = parser.parse_args()
    listing_urls = args.directory_urls if args.directory_urls else [TOP_50_PARKS_LISTING_URL]
    use_current_tab = args.use_current_tab or bool(args.connect_cdp)

    from src.wanderly.alltrails_park_url import resolve_catalog_fields
    from src.wanderly.trail_catalog.write_park import write_park_catalog_files
    from scripts.crawl_alltrails_park_catalog import (  # noqa: PLC0415
        _compact_entry_from_listing_row,
        _dedupe_entries,
    )

    storage = str(args.storage_state) if args.storage_state.is_file() else None
    save_storage = args.save_storage
    if save_storage is None and args.pause_for_captcha:
        save_storage = args.storage_state
    channel = args.channel if args.channel and args.channel != "chromium" else None
    if args.channel == "chromium":
        channel = None

    if args.discover_only:
        from src.wanderly.alltrails_park_crawl import crawl_directory_parks_batch_playwright

        parks = crawl_directory_parks_batch_playwright(
            listing_urls,
            limit=max(1, args.limit),
            discovery=args.discovery,
            headless=not args.headed,
            directory_scroll_rounds=args.directory_scroll_rounds,
            storage_state_path=storage,
            save_storage_state_path=str(save_storage) if save_storage else None,
            pause_for_captcha=args.pause_for_captcha,
            browser_channel=channel,
            connect_cdp_url=args.connect_cdp or None,
            delay_between_parks_s=0,
            use_current_tab=use_current_tab,
        )
        payload = {
            "written_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "directory_urls": listing_urls,
            "discovery": args.discovery,
            "park_count": len(parks),
            "parks": parks,
        }
        args.discover_out.parent.mkdir(parents=True, exist_ok=True)
        args.discover_out.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"Wrote {len(parks)} park URLs -> {args.discover_out}")
        for p in parks[:10]:
            print(f"  {p.get('name') or '?'} | {p.get('url')}")
        if len(parks) > 10:
            print(f"  ... and {len(parks) - 10} more")
        return 0

    written: list[dict[str, Any]] = []
    skipped: list[str] = []

    def should_crawl(park_url: str) -> bool:
        if not args.skip_existing:
            return True
        meta = resolve_catalog_fields(park_url)
        out_path = _catalog_path_for_park(ROOT, meta.park_code)
        if _should_skip_existing(out_path, min_trails=args.skip_existing_min):
            print(f"[skip] existing catalog {out_path} ({meta.park_code})", flush=True)
            skipped.append(park_url)
            return False
        return True

    def on_park(park_url: str, raw_rows: list[dict[str, Any]], _listing_name: str) -> None:
        meta = resolve_catalog_fields(park_url)
        out_path = _catalog_path_for_park(ROOT, meta.park_code)
        entries: list[dict[str, Any]] = []
        for item in raw_rows:
            row = _compact_entry_from_listing_row(
                item,
                park_code=meta.park_code,
                park_name=meta.park_name,
                region=meta.region,
                country=meta.country,
            )
            if row:
                entries.append(row)
        entries = _dedupe_entries(entries)
        trail_count = len(entries)
        path = write_park_catalog_files(
            ROOT,
            park_url=meta.park_url,
            park_code=meta.park_code,
            park_name=meta.park_name,
            region=meta.region,
            country=meta.country,
            entries=entries,
            method="playwright_directory_batch",
            out_path=out_path,
        )
        del entries
        print(f"[wrote] {trail_count} trails -> {path}", flush=True)
        written.append(
            {
                "park_code": meta.park_code,
                "park_name": meta.park_name,
                "park_url": meta.park_url,
                "trail_count": trail_count,
                "file": str(path.relative_to(ROOT)).replace("\\", "/"),
            }
        )

    if args.subprocess_per_park:
        import subprocess

        from src.wanderly.alltrails_park_crawl import crawl_directory_parks_batch_playwright

        parks = crawl_directory_parks_batch_playwright(
            listing_urls,
            limit=max(1, args.limit),
            discovery=args.discovery,
            headless=not args.headed,
            directory_scroll_rounds=args.directory_scroll_rounds,
            storage_state_path=storage,
            save_storage_state_path=str(save_storage) if save_storage else None,
            pause_for_captcha=args.pause_for_captcha,
            browser_channel=channel,
            connect_cdp_url=args.connect_cdp or None,
        )
        py = sys.executable
        script = ROOT / "scripts" / "crawl_alltrails_park_catalog.py"
        summaries: list[dict[str, Any]] = []
        for idx, park in enumerate(parks, start=1):
            park_url = park["url"]
            if not should_crawl(park_url):
                summaries.append(
                    {
                        "park_url": park_url,
                        "park_name": park.get("name") or "",
                        "trail_count": 0,
                        "ok": True,
                        "skipped": True,
                        "error": None,
                    }
                )
                continue
            cmd = [py, str(script), park_url, "--method", "playwright"]
            if args.connect_cdp:
                cmd.extend(["--connect-cdp", args.connect_cdp])
            if storage:
                cmd.extend(["--storage-state", str(args.storage_state)])
            print(f"[subprocess] {idx}/{len(parks)} {park_url}", flush=True)
            proc = subprocess.run(cmd, cwd=str(ROOT))
            summaries.append(
                {
                    "park_url": park_url,
                    "park_name": park.get("name") or "",
                    "trail_count": -1,
                    "ok": proc.returncode == 0,
                    "error": None if proc.returncode == 0 else f"exit {proc.returncode}",
                }
            )
        report = {
            "written_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "directory_urls": listing_urls,
            "discovery": args.discovery,
            "limit": args.limit,
            "mode": "subprocess_per_park",
            "skipped_existing": skipped,
            "catalogs_written": written,
            "summaries": summaries,
        }
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        ok = sum(1 for s in summaries if s.get("ok"))
        print(f"\nDone (subprocess): {ok}/{len(summaries)} parks")
        print(f"Report -> {args.report_out}")
        return 0 if ok > 0 else 1

    from src.wanderly.alltrails_park_crawl import crawl_directory_and_parks_batch_playwright

    if args.discovery == "top-50" and args.limit != 50:
        print(f"NOTE: --discovery top-50 uses the page's Top 50 Parks list (capped at 50, not {args.limit})")

    summaries = crawl_directory_and_parks_batch_playwright(
        listing_urls,
        limit=max(1, args.limit),
        discovery=args.discovery,
        headless=not args.headed,
        max_rounds=args.max_rounds,
        expected_min=max(0, args.expected_min),
        directory_scroll_rounds=args.directory_scroll_rounds,
        storage_state_path=storage,
        save_storage_state_path=str(save_storage) if save_storage else None,
        pause_for_captcha=args.pause_for_captcha,
        browser_channel=channel,
        connect_cdp_url=args.connect_cdp or None,
        delay_between_parks_s=args.delay_s,
        on_park_crawled=on_park,
        should_crawl_park=should_crawl if args.skip_existing else None,
        fresh_page_per_park=not args.reuse_tab,
        compact_trails=True,
        gc_every_n_parks=max(0, args.gc_every),
        use_current_tab=use_current_tab,
    )

    report = {
        "written_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "directory_urls": listing_urls,
        "discovery": args.discovery,
        "limit": args.limit,
        "skipped_existing": skipped,
        "catalogs_written": written,
        "summaries": summaries,
    }
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    ok = sum(1 for s in summaries if s.get("ok"))
    print(f"\nDone: {ok}/{len(summaries)} parks crawled; {len(written)} catalogs written; {len(skipped)} skipped")
    print(f"Report -> {args.report_out}")
    return 0 if ok > 0 or written else 1


if __name__ == "__main__":
    raise SystemExit(main())
