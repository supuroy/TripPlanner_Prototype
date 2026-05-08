"""
Standalone Expedia hotel fetch+parse debugger (no UI).

Usage:
  python scripts/debug_hotel_fetch_parse.py --trace --destination-id phuket-thailand
  python scripts/debug_hotel_fetch_parse.py --url "https://www.expedia.com/Hotel-Search?..."
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAVEL_ROOT = REPO_ROOT / "Travel Planner Prototype"
EXPEDIA_CLI_ROOT = REPO_ROOT / "expedia-listing-cli"
if str(EXPEDIA_CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(EXPEDIA_CLI_ROOT))

from expedia_listing_cli.browser_fetch import fetch_page_html
from expedia_listing_cli.extract_heuristic import extract_candidate_links, extract_price_like_strings


def _last_hotel_url_from_trace(trace_path: Path, destination_id: str) -> str | None:
    if not trace_path.is_file():
        return None
    out: str | None = None
    for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if obj.get("event") in ("hotel_url_built", "step_ok"):
            did = str(obj.get("destination_id") or "")
            url = str(obj.get("url") or obj.get("search_url") or "")
            if did == destination_id and "/Hotel-Search?" in url:
                out = url
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="Hotel URL to fetch")
    ap.add_argument("--trace", action="store_true", help="Read URL from booking trace")
    ap.add_argument("--destination-id", default="phuket-thailand")
    ap.add_argument("--trace-path", default=str(TRAVEL_ROOT / ".cache" / "booking_search_trace.log"))
    ap.add_argument("--headless", type=int, default=1)
    ap.add_argument("--save-html", default=str(TRAVEL_ROOT / ".cache" / "debug_hotel_fetch_parse.html"))
    args = ap.parse_args()

    url = args.url.strip()
    if args.trace and not url:
        url = _last_hotel_url_from_trace(Path(args.trace_path), args.destination_id) or ""
    if not url:
        raise SystemExit("No URL provided/found. Pass --url or use --trace with a valid trace file.")

    print("\n=== Debug Hotel Fetch+Parse ===")
    print(f"url: {url}")

    html = fetch_page_html(url, headless=bool(args.headless))
    out_path = Path(args.save_html)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"html_len: {len(html)}")
    print(f"saved_html: {out_path}")

    m = re.search(r"<title>(.*?)</title>", html, flags=re.I | re.S)
    print(f"title: {(m.group(1).strip() if m else '<none>')}")

    links = extract_candidate_links(html, limit=10)
    print(f"candidate_links({len(links)}):")
    for i, lk in enumerate(links[:5], 1):
        print(f"  {i}. text={lk.get('text','')[:80]!r} href={lk.get('href','')[:180]}")

    prices = extract_price_like_strings(html, limit=20)
    print(f"price_like_strings({len(prices)}): {prices[:10]}")

    print("pattern counts:")
    for pat in (r"Hotel-Information", r"\\/Hotel-Information", r"application/ld\\+json", r"itemListElement"):
        cnt = len(re.findall(pat, html, flags=re.I))
        print(f"  {pat}: {cnt}")


if __name__ == "__main__":
    main()

