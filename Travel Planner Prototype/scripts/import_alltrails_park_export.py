#!/usr/bin/env python3
"""
Import trail catalog rows from a browser export (no automated fetch).

Use when Apify, Playwright, and direct HTTP all return 403.

Typical workflow:
  1. Open the AllTrails park page in Chrome (logged in / captcha solved in browser).
  2. DevTools -> Network -> filter Fetch/XHR -> scroll / click Show more on the page.
  3. Click a large JSON response -> Copy response -> save as yellowstone.json
  4. Run this script.

Also accepts:
  --html-file  saved page from Ctrl+S (may only include first page of trails)
  --urls-file  one AllTrails trail URL per line
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.wanderly.alltrails_park_http import parse_trails_from_html  # noqa: E402

_TRAIL_URL_IN_STR = re.compile(
    r"https?://(?:www\.)?alltrails\.com/(?:explore/)?trail/[a-z]{2}/[^/\s\"']+/[^/\s\"']+",
    re.I,
)


def _walk_json(node: Any, out: list[dict[str, Any]], seen_urls: set[str]) -> None:
    if isinstance(node, dict):
        url = (
            node.get("url")
            or node.get("trailUrl")
            or node.get("link")
            or node.get("slug")
        )
        if isinstance(url, str) and "/trail/" in url:
            if not url.startswith("http"):
                url = f"https://www.alltrails.com/{url.lstrip('/')}"
            url = url.split("?")[0].rstrip("/")
            name = (
                node.get("name")
                or node.get("trailName")
                or node.get("title")
                or ""
            )
            if isinstance(name, str) and url.lower() not in seen_urls:
                seen_urls.add(url.lower())
                length_mi = None
                for key in ("lengthMiles", "length_miles", "length_mi", "distance", "length"):
                    v = node.get(key)
                    if isinstance(v, (int, float)) and v > 0:
                        length_mi = float(v) if v > 20 else float(v)  # miles vs meters heuristic below
                        if key == "distance" and v > 50:
                            length_mi = round(float(v) * 0.000621371, 2)
                        elif key != "distance":
                            length_mi = float(v)
                        break
                diff = node.get("difficulty") or node.get("difficultyRating")
                out.append(
                    {
                        "url": url,
                        "name": str(name).strip()[:200],
                        "difficulty": str(diff).strip() if diff else None,
                        "length_mi": length_mi,
                        "rating": node.get("rating") or node.get("avgRating"),
                    }
                )
        for v in node.values():
            _walk_json(v, out, seen_urls)
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, out, seen_urls)


def parse_trails_from_json_blob(data: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(data, dict):
        direct = data.get("trails") or data.get("items") or data.get("results")
        if isinstance(direct, list):
            for item in direct:
                if isinstance(item, dict) and (item.get("url") or item.get("trailUrl")):
                    _walk_json(item, rows, seen)
    _walk_json(data, rows, seen)
    if rows:
        return rows
    # Fallback: regex over stringified JSON
    text = json.dumps(data, ensure_ascii=True) if not isinstance(data, str) else data
    for m in _TRAIL_URL_IN_STR.finditer(text):
        url = m.group(0).split("?")[0]
        if url.lower() in seen:
            continue
        seen.add(url.lower())
        slug = url.rsplit("/", 1)[-1]
        rows.append({"url": url, "name": slug.replace("-", " ").title()})
    return rows


def _compact_catalog_rows(
    raw: list[dict[str, Any]],
    *,
    park_code: str,
    park_name: str,
    region: str,
    country: str,
) -> list[dict[str, Any]]:
    from scripts.crawl_alltrails_park_catalog import (  # noqa: PLC0415
        _compact_entry_from_listing_row,
        _dedupe_entries,
    )

    entries = []
    for item in raw:
        row = _compact_entry_from_listing_row(
            item,
            park_code=park_code,
            park_name=park_name,
            region=region,
            country=country,
        )
        if row:
            entries.append(row)
    return _dedupe_entries(entries)


def _load_json_paths(paths: Iterable[Path]) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            print(f"[skip] not a file: {path}", flush=True)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        parsed = parse_trails_from_json_blob(data)
        raw.extend(parsed)
        print(f"[json] {path.name} -> +{len(parsed)} rows (running total {len(raw)})", flush=True)
    return raw


def parse_trails_from_har(har_path: Path) -> list[dict[str, Any]]:
    """Merge trail rows from every JSON response embedded in a Chrome HAR export."""
    har = json.loads(har_path.read_text(encoding="utf-8", errors="replace"))
    entries = har.get("log", {}).get("entries", [])
    if not isinstance(entries, list):
        entries = []
    raw: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    json_hits = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        resp = entry.get("response") or {}
        content = resp.get("content") or {}
        text = content.get("text") or ""
        if not text:
            continue
        mime = str(content.get("mimeType") or "").lower()
        if "json" not in mime and "javascript" not in mime:
            continue
        if content.get("encoding") == "base64":
            try:
                text = base64.b64decode(text).decode("utf-8", errors="replace")
            except Exception:
                continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        json_hits += 1
        for row in parse_trails_from_json_blob(data):
            url = str(row.get("url") or "").lower()
            if url and url not in seen_urls:
                seen_urls.add(url)
                raw.append(row)
    print(f"[har] {har_path.name} -> {json_hits} JSON responses, {len(raw)} unique trails", flush=True)
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Build trail catalog from browser-exported JSON/HTML/URLs.")
    parser.add_argument(
        "--json",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="DevTools Copy response (repeat for each paginated page, or use --json-dir / --har)",
    )
    parser.add_argument(
        "--json-dir",
        type=Path,
        help="Folder of page-01.json, page-02.json, ... (all merged and deduped)",
    )
    parser.add_argument(
        "--har",
        type=Path,
        help="Chrome Network -> Save all as HAR with content (captures every paginated XHR)",
    )
    parser.add_argument("--html-file", type=Path, help="Saved park page HTML")
    parser.add_argument("--urls-file", type=Path, help="Text file: one trail URL per line")
    parser.add_argument("--park-code", default="yell")
    parser.add_argument("--park-name", default="Yellowstone National Park")
    parser.add_argument("--region", default="Wyoming")
    parser.add_argument("--country", default="United States")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "trail_catalog" / "parks" / "yell.json",
    )
    args = parser.parse_args()

    if not args.json and not args.json_dir and not args.har and not args.html_file and not args.urls_file:
        print("Provide one of: --json, --json-dir, --har, --html-file, --urls-file", file=sys.stderr)
        return 1

    raw: list[dict[str, Any]] = []

    json_paths: list[Path] = list(args.json or [])
    if args.json_dir and args.json_dir.is_dir():
        json_paths.extend(sorted(args.json_dir.glob("*.json")))
    if json_paths:
        raw.extend(_load_json_paths(json_paths))

    if args.har and args.har.is_file():
        raw.extend(parse_trails_from_har(args.har))

    if args.html_file and args.html_file.is_file():
        html = args.html_file.read_text(encoding="utf-8", errors="replace")
        parsed = parse_trails_from_html(html)
        raw.extend(parsed)
        print(f"[html] {args.html_file} -> {len(parsed)} trail rows", flush=True)

    if args.urls_file and args.urls_file.is_file():
        for line in args.urls_file.read_text(encoding="utf-8", errors="replace").splitlines():
            u = line.strip()
            if u and "/trail/" in u:
                raw.append({"url": u, "name": u.rsplit("/", 1)[-1].replace("-", " ").title()})
        print(f"[urls] {args.urls_file} -> added from list", flush=True)

    entries = _compact_catalog_rows(
        raw,
        park_code=args.park_code,
        park_name=args.park_name,
        region=args.region,
        country=args.country,
    )

    doc = {
        "catalog_version": 1,
        "written_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "method": "browser_export",
            "json_files": [str(p) for p in json_paths] if json_paths else None,
            "json_dir": str(args.json_dir) if args.json_dir else None,
            "har": str(args.har) if args.har else None,
            "html_file": str(args.html_file) if args.html_file else None,
            "urls_file": str(args.urls_file) if args.urls_file else None,
        },
        "park_code": args.park_code,
        "park_name": args.park_name,
        "region": args.region,
        "country": args.country,
        "trail_count": len(entries),
        "trails": entries,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    manifest_path = args.out.parent.parent / "manifest.json"
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
        "park_code": args.park_code,
        "park_name": args.park_name,
        "trail_count": len(entries),
        "file": str(args.out.relative_to(ROOT)).replace("\\", "/"),
        "source_url": "browser_export",
        "updated_at_utc": doc["written_at_utc"],
    }
    parks = [p for p in parks if isinstance(p, dict) and p.get("park_code") != args.park_code]
    parks.append(park_row)
    manifest["parks"] = sorted(parks, key=lambda p: str(p.get("park_code") or ""))
    manifest["updated_at_utc"] = doc["written_at_utc"]
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    print(f"\nWrote {len(entries)} trails -> {args.out}")
    print(f"Updated manifest -> {manifest_path}")
    if len(entries) < 20:
        print("WARNING: very few trails — export may be incomplete. See data/trail_catalog/BROWSER_EXPORT.md")
    elif len(entries) >= 200:
        print("OK: count looks healthy for Yellowstone.")
    heart = [e for e in entries if "heart-lake" in e.get("source_url", "")]
    if heart:
        print("heart-lake:", heart[0].get("source_url"))
    return 0 if entries else 1


if __name__ == "__main__":
    raise SystemExit(main())
