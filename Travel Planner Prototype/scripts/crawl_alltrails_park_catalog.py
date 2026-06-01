#!/usr/bin/env python3
"""
Build a minimal trail catalog for one AllTrails park listing page.

Pass any AllTrails ``/parks/{country}/{region}/{slug}`` URL. Park code, display name,
region, country, and output path are inferred from the URL unless you override them.

Primary path (no Apify quota): **Playwright** — loads the park page, scrolls / clicks
``Show more``, extracts trail URLs + listing metadata.

Alternate: Apify ``parseforge/alltrails-scraper`` (offline, high ``maxItems``).

Fallback: saved HTML file or regex HTTP (often 403 / incomplete).

Usage:
  cd "Travel Planner Prototype"
  python scripts/crawl_alltrails_park_catalog.py \\
    "https://www.alltrails.com/parks/us/california/yosemite-national-park" \\
    --method http

  python scripts/crawl_alltrails_park_catalog.py \\
    --park-url "https://www.alltrails.com/parks/us/wyoming/yellowstone-national-park"

Playwright: ``pip install playwright`` and ``playwright install chromium`` (once per machine).

Apify path requires APIFY_API_TOKEN when ``--method apify``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TRAIL_PATH_RE = re.compile(
    r"https?://(?:www\.)?alltrails\.com/(?:explore/)?trail/([a-z]{2})/([^/\s\"'?]+)/([^/\s\"'?]+)",
    re.I,
)
_TRAIL_REL_RE = re.compile(
    r"/(?:explore/)?trail/([a-z]{2})/([^/\s\"'?]+)/([^/\s\"'?]+)",
    re.I,
)


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


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return (s or "trail")[:80]


def _trail_id_from_url(url: str, park_code: str) -> str:
    m = _TRAIL_PATH_RE.search(url or "")
    if not m:
        return f"{park_code}:{_slugify(url)}"
    slug = m.group(3).lower()
    return f"{park_code}:{slug}"


def _normalize_url(url: str) -> str:
    u = (url or "").strip().split("?")[0].rstrip("/")
    if u and not u.startswith("http"):
        u = urljoin("https://www.alltrails.com", u)
    return u


def _compact_entry_from_listing_row(
    item: dict[str, Any],
    *,
    park_code: str,
    park_name: str,
    region: str,
    country: str,
) -> dict[str, Any] | None:
    """Normalize Apify, Playwright, or minimal {url, name} rows into catalog entries."""
    from src.wanderly.alltrails_provider import normalize_trail_record

    if str(item.get("url") or "").strip() and "/trail/" in str(item.get("url")).lower():
        url = _normalize_url(str(item.get("url") or ""))
        name = str(item.get("name") or "").strip()
        length_mi = item.get("length_mi")
        difficulty = item.get("difficulty")
        route_type = item.get("route_type")
        rating = item.get("rating")
        alltrails_id = item.get("alltrails_id")
        elev = item.get("elevation_gain_ft")
    else:
        rec = normalize_trail_record(item, include_raw=False)
        url = _normalize_url(str(rec.get("url") or ""))
        name = str(rec.get("name") or "").strip()
        length_mi = rec.get("length_mi")
        difficulty = rec.get("difficulty")
        route_type = rec.get("route_type")
        rating = rec.get("rating")
        alltrails_id = rec.get("alltrails_id")
        elev = rec.get("elevation_gain_ft")

    if "/trail/" not in url.lower():
        return None
    if not name:
        return None
    trail_id = _trail_id_from_url(url, park_code)
    tags: list[str] = []
    if isinstance(length_mi, (int, float)) and float(length_mi) >= 8:
        tags.append("long")
    if isinstance(length_mi, (int, float)) and float(length_mi) >= 15:
        tags.append("backpacking_candidate")
    return {
        "trail_id": trail_id,
        "name": name[:200],
        "park_code": park_code,
        "park_name": park_name,
        "region": region,
        "country": country,
        "distance_mi": length_mi,
        "elevation_gain_ft": elev,
        "difficulty": difficulty,
        "route_type": route_type,
        "source_url": url,
        "alltrails_id": alltrails_id,
        "rating": rating,
        "tags": tags,
    }


# Back-compat alias
_compact_entry_from_apify_row = _compact_entry_from_listing_row


def _fetch_via_apify(park_url: str, max_items: int, timeout_s: float) -> list[dict[str, Any]]:
    from src.wanderly.alltrails_provider import _run_apify_search, apify_token

    if not apify_token():
        raise RuntimeError("APIFY_API_TOKEN is not set.")
    run_input = {"startUrl": park_url.strip(), "maxItems": max(10, min(max_items, 500))}
    print(f"[apify] startUrl={park_url!r} maxItems={run_input['maxItems']}", flush=True)
    return _run_apify_search(run_input, timeout_s=timeout_s)


def _fetch_via_html(park_url: str) -> list[dict[str, Any]]:
    """Best-effort link harvest from static HTML (may miss paginated trails)."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(timeout=60.0, follow_redirects=True, headers=headers) as client:
        res = client.get(park_url)
        res.raise_for_status()
        html = res.text
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for m in _TRAIL_PATH_RE.finditer(html):
        url = m.group(0).split("?")[0]
        if url in seen:
            continue
        seen.add(url)
        rows.append({"url": url, "name": m.group(3).replace("-", " ").title()})
    for m in _TRAIL_REL_RE.finditer(html):
        url = _normalize_url(m.group(0))
        if url in seen:
            continue
        seen.add(url)
        rows.append({"url": url, "name": m.group(3).replace("-", " ").title()})
    print(f"[html] extracted {len(rows)} unique trail URLs from page HTML", flush=True)
    return rows


def _dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url: dict[str, dict[str, Any]] = {}
    for e in entries:
        url = str(e.get("source_url") or "").strip().lower()
        if not url:
            continue
        prev = by_url.get(url)
        if not prev or (e.get("distance_mi") and not prev.get("distance_mi")):
            by_url[url] = e
    return sorted(by_url.values(), key=lambda x: str(x.get("name") or "").lower())


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(
        description="Crawl AllTrails park page into trail catalog JSON.",
        epilog=(
            "Examples:\n"
            "  python scripts/crawl_alltrails_park_catalog.py "
            '"https://www.alltrails.com/parks/us/california/yosemite-national-park"\n'
            "  python scripts/crawl_alltrails_park_catalog.py --park-url <url> --method playwright"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "park_url_pos",
        nargs="?",
        help="AllTrails park page URL (same as --park-url)",
    )
    parser.add_argument(
        "--park-url",
        dest="park_url_opt",
        default=None,
        help="AllTrails /parks/... listing URL",
    )
    parser.add_argument(
        "--park-code",
        default=None,
        help="Catalog trail_id prefix (default: inferred, often NPS parkCode)",
    )
    parser.add_argument("--park-name", default=None, help="Display name (default: from URL slug)")
    parser.add_argument("--region", default=None, help="State/province label (default: from URL)")
    parser.add_argument("--country", default=None, help="Country label (default: from URL)")
    parser.add_argument("--max-items", type=int, default=320, help="Apify maxItems cap (10-500)")
    parser.add_argument("--timeout-s", type=float, default=300.0)
    parser.add_argument(
        "--method",
        choices=("http", "playwright", "apify", "html", "apify_then_html", "html_file"),
        default="http",
    )
    parser.add_argument(
        "--no-curl-cffi",
        action="store_true",
        help="HTTP method only: skip curl_cffi TLS impersonation attempt",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser with UI (debugging)",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=80,
        help="Playwright scroll/show-more rounds",
    )
    parser.add_argument(
        "--expected-min",
        type=int,
        default=50,
        help="Playwright: stop early once trail link count is stable and >= this (0 = ignore)",
    )
    parser.add_argument(
        "--storage-state",
        type=Path,
        default=ROOT / ".cache" / "alltrails_storage.json",
        help="Playwright cookies/localStorage JSON (reuse after manual captcha)",
    )
    parser.add_argument(
        "--save-storage",
        type=Path,
        default=None,
        help="Write storage state after crawl (for reuse without captcha)",
    )
    parser.add_argument(
        "--pause-for-captcha",
        action="store_true",
        help="Headed: wait for you to solve DataDome/captcha before scraping",
    )
    parser.add_argument(
        "--connect-cdp",
        default=None,
        metavar="URL",
        help="Attach to Chrome started with --remote-debugging-port=9222 (use your real session)",
    )
    parser.add_argument(
        "--channel",
        default="chrome",
        help="Playwright browser channel (chrome, msedge) or 'chromium' for bundled",
    )
    parser.add_argument(
        "--html-file",
        type=Path,
        default=None,
        help="Parse trail links from a saved park page HTML (use when live fetch returns 403)",
    )
    parser.add_argument(
        "--apify-json",
        type=Path,
        default=None,
        help="Normalize trails from a saved Apify dataset JSON file (list of objects)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSON (default: data/trail_catalog/parks/{park_code}.json)",
    )
    args = parser.parse_args()

    park_url_raw = (args.park_url_opt or args.park_url_pos or "").strip()
    if not park_url_raw:
        parser.error(
            "provide an AllTrails park URL as the first argument or via --park-url "
            '(e.g. "https://www.alltrails.com/parks/us/california/yosemite-national-park")'
        )

    from src.wanderly.alltrails_park_url import resolve_catalog_fields

    try:
        meta = resolve_catalog_fields(
            park_url_raw,
            park_code=args.park_code,
            park_name=args.park_name,
            region=args.region,
            country=args.country,
        )
    except ValueError as exc:
        parser.error(str(exc))

    park_url = meta.park_url
    park_code = meta.park_code
    park_name = meta.park_name
    region = meta.region
    country = meta.country
    out_path = args.out or (ROOT / "data" / "trail_catalog" / "parks" / f"{park_code}.json")

    print(
        f"[catalog] {park_name!r} code={park_code!r} region={region!r} url={park_url}",
        flush=True,
    )

    raw_items: list[dict[str, Any]] = []
    method_used = args.method

    if args.apify_json and args.apify_json.is_file():
        loaded = json.loads(args.apify_json.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            raw_items = [x for x in loaded if isinstance(x, dict)]
        elif isinstance(loaded, dict):
            items = loaded.get("items") or loaded.get("data")
            raw_items = [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
        method_used = "apify_json"
        print(f"[apify_json] {args.apify_json} -> {len(raw_items)} rows", flush=True)

    if args.method == "http" and not raw_items:
        try:
            from src.wanderly.alltrails_park_http import crawl_park_trails_http

            raw_items = crawl_park_trails_http(
                park_url,
                try_curl_cffi=not args.no_curl_cffi,
            )
            method_used = "http"
        except Exception as exc:
            print(f"[http] failed: {exc!r}", flush=True)
            return 1

    if args.method == "playwright" and not raw_items:
        try:
            from src.wanderly.alltrails_park_crawl import crawl_park_trails_playwright

            storage = args.storage_state if args.storage_state.is_file() else None
            channel = args.channel if args.channel and args.channel != "chromium" else None
            if args.channel == "chromium":
                channel = None
            save_storage = args.save_storage
            if save_storage is None and args.pause_for_captcha:
                save_storage = args.storage_state
            raw_items = crawl_park_trails_playwright(
                park_url,
                headless=not args.headed,
                max_rounds=args.max_rounds,
                expected_min=max(0, args.expected_min),
                storage_state_path=str(storage) if storage else None,
                save_storage_state_path=str(save_storage) if save_storage else None,
                pause_for_captcha=args.pause_for_captcha,
                browser_channel=channel,
                connect_cdp_url=args.connect_cdp or None,
            )
            method_used = "playwright"
        except Exception as exc:
            print(f"[playwright] failed: {exc!r}", flush=True)
            return 1

    if args.method in ("apify", "apify_then_html") and not raw_items:
        try:
            raw_items = _fetch_via_apify(park_url, args.max_items, args.timeout_s)
            print(f"[apify] returned {len(raw_items)} rows", flush=True)
        except Exception as exc:
            print(f"[apify] failed: {exc!r}", flush=True)
            if args.method == "apify":
                return 1
            method_used = "html_fallback"

    if args.html_file and args.html_file.is_file():
        from src.wanderly.alltrails_park_http import parse_trails_from_html

        html = args.html_file.read_text(encoding="utf-8", errors="replace")
        raw_items = parse_trails_from_html(html)
        method_used = "html_file"
        print(f"[html_file] {args.html_file} -> {len(raw_items)} trails", flush=True)

    if not raw_items and args.method in ("html", "apify_then_html"):
        try:
            raw_items = _fetch_via_html(park_url)
            method_used = "html" if method_used != "apify_then_html" else "html_fallback"
        except Exception as exc:
            print(f"[html] failed: {exc!r}", flush=True)
            if not args.html_file:
                return 1

    entries: list[dict[str, Any]] = []
    for item in raw_items:
        row = _compact_entry_from_listing_row(
            item,
            park_code=park_code,
            park_name=park_name,
            region=region,
            country=country,
        )
        if row:
            entries.append(row)

    entries = _dedupe_entries(entries)
    doc: dict[str, Any] = {
        "catalog_version": 1,
        "written_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "park_url": park_url,
            "method": method_used,
        },
        "park_code": park_code,
        "park_name": park_name,
        "region": region,
        "country": country,
        "trail_count": len(entries),
        "trails": entries,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    manifest_path = out_path.parent.parent / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    parks = manifest.get("parks")
    if not isinstance(parks, list):
        parks = []
    park_row = {
        "park_code": park_code,
        "park_name": park_name,
        "trail_count": len(entries),
        "file": str(out_path.relative_to(ROOT)).replace("\\", "/"),
        "source_url": park_url,
        "updated_at_utc": doc["written_at_utc"],
    }
    parks = [p for p in parks if isinstance(p, dict) and p.get("park_code") != park_code]
    parks.append(park_row)
    manifest["parks"] = sorted(parks, key=lambda p: str(p.get("park_code") or ""))
    manifest["updated_at_utc"] = doc["written_at_utc"]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    print(f"\nWrote {len(entries)} trails -> {out_path}")
    print(f"Updated manifest -> {manifest_path}")
    if len(entries) < 10:
        print(
            "WARNING: very few trails — check URL, captcha/blocking, or use "
            "--method playwright / --html-file / Apify.",
        )
    elif args.expected_min > 0 and len(entries) < args.expected_min:
        print(
            f"NOTE: {len(entries)} trails loaded (below --expected-min {args.expected_min}); "
            "listing may be partial.",
        )
    if entries:
        print("sample:", json.dumps(entries[0], ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
