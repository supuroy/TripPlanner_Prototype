#!/usr/bin/env python3
"""
Offline enrich park hero images from AllTrails park pages (Chrome CDP recommended).

Reads ``data/trail_catalog/manifest.json`` and writes ``data/trail_catalog/park_heroes.json``.

Example (Chrome remote debugging on port 9222):

  cd "Travel Planner Prototype"
  python scripts/enrich_park_heroes_alltrails.py \\
    --connect-cdp http://127.0.0.1:9222 \\
    --skip-existing

Single park:

  python scripts/enrich_park_heroes_alltrails.py --park-code brca \\
    --connect-cdp http://127.0.0.1:9222
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
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


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Enrich AllTrails park hero images offline.")
    parser.add_argument(
        "--connect-cdp",
        default=None,
        metavar="URL",
        help="Chrome CDP URL (e.g. http://127.0.0.1:9222)",
    )
    parser.add_argument("--park-code", default=None, help="Only enrich one park code (e.g. brca)")
    parser.add_argument("--limit", type=int, default=0, help="Max parks to process (0=all)")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip parks that already have hero_image_url in park_heroes.json",
    )
    parser.add_argument("--delay-s", type=float, default=2.0, help="Pause between park fetches")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--pause-for-captcha", action="store_true")
    parser.add_argument(
        "--storage-state",
        type=Path,
        default=ROOT / ".cache" / "alltrails_storage.json",
    )
    parser.add_argument("--channel", default="chrome")
    parser.add_argument(
        "--report-out",
        type=Path,
        default=ROOT / ".cache" / "park_hero_enrich_report.json",
    )
    args = parser.parse_args()

    from src.wanderly.alltrails_park_hero import CdpParkHeroSession, fetch_park_hero_playwright
    from src.wanderly.park_hero_catalog import (
        load_manifest_parks,
        load_park_heroes_doc,
        offline_park_hero_url,
        save_park_heroes_doc,
    )

    targets = load_manifest_parks()
    if args.park_code:
        pc = args.park_code.strip().lower()
        targets = [p for p in targets if str(p.get("park_code") or "").lower() == pc]
        if not targets:
            print(f"No manifest row for park_code={pc!r}", file=sys.stderr)
            return 1

    doc = load_park_heroes_doc(force_reload=True)
    parks_out: dict[str, Any] = dict(doc.get("parks") or {})

    storage = str(args.storage_state) if args.storage_state.is_file() else None
    channel = args.channel if args.channel and args.channel != "chromium" else None
    if args.channel == "chromium":
        channel = None

    report: list[dict[str, Any]] = []
    processed = 0
    ok = 0

    for row in targets:
        if args.limit and processed >= args.limit:
            break
        pc = str(row.get("park_code") or "").strip().lower()
        url = str(row.get("source_url") or "").strip()
        name = str(row.get("park_name") or pc).strip()
        if not pc or not url:
            continue
        if args.skip_existing and offline_park_hero_url(pc):
            report.append({"park_code": pc, "status": "skipped_existing"})
            continue

        processed += 1
        print(f"[park_hero] {processed}: {pc} — {name}", flush=True)
        result = fetch_park_hero_playwright(
            url,
            connect_cdp_url=args.connect_cdp,
            headless=not args.headed,
            storage_state_path=storage,
            browser_channel=channel,
            pause_for_captcha=args.pause_for_captcha,
        )
        entry: dict[str, Any] = {
            "park_code": pc,
            "park_name": name,
            "source_url": url,
            "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "method": result.get("method"),
            "error": result.get("error"),
        }
        hero = result.get("hero_image_url")
        if result.get("ok") and hero:
            entry["hero_image_url"] = hero
            parks_out[pc] = entry
            ok += 1
            report.append({"park_code": pc, "status": "ok", "hero_image_url": hero})
            print(f"  ok: {hero[:90]}…", flush=True)
        else:
            parks_out[pc] = {**parks_out.get(pc, {}), **entry}
            report.append({"park_code": pc, "status": "fail", "error": result.get("error")})
            print(f"  fail: {result.get('error')}", flush=True)

        doc["parks"] = parks_out
        save_park_heroes_doc(doc)

        if args.delay_s > 0 and processed < len(targets):
            time.sleep(args.delay_s)

    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nDone: {ok}/{processed} heroes saved → {ROOT / 'data/trail_catalog/park_heroes.json'}")
    print(f"Report: {args.report_out}")
    return 0 if ok > 0 or processed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
