#!/usr/bin/env python3
"""
Fetch a URL with plain HTTP and print diagnostics (or save body to file).

Useful to test whether AllTrails allows direct fetch from your network.

  cd "Travel Planner Prototype"
  python scripts/fetch_url.py "https://www.alltrails.com/parks/us/wyoming/yellowstone-national-park"
  python scripts/fetch_url.py "https://example.com" --out .cache/fetch_test.html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.wanderly.alltrails_park_http import (  # noqa: E402
    fetch_park_page,
    is_blocked_response,
    parse_trails_from_html,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Direct HTTP fetch with AllTrails-aware diagnostics.")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument("--out", type=Path, default=None, help="Save HTML body to file")
    parser.add_argument("--no-curl-cffi", action="store_true", help="Skip curl_cffi impersonation attempt")
    parser.add_argument("--parse-trails", action="store_true", help="Count trails if HTML looks like AllTrails park page")
    args = parser.parse_args()

    result = fetch_park_page(args.url, try_curl_cffi=not args.no_curl_cffi)
    print("ok:", result.get("ok"))
    print("blocked:", result.get("blocked"))
    print("status_code:", result.get("status_code"))
    print("method:", result.get("method"))
    print("attempts:", result.get("attempts"))
    if not result.get("ok"):
        print("\nhint:", result.get("hint"))
        return 1

    html = str(result.get("html") or "")
    print("html_len:", len(html))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(html, encoding="utf-8", errors="replace")
        print("wrote:", args.out)

    if args.parse_trails or "alltrails.com" in args.url:
        trails = parse_trails_from_html(html)
        print("trails_parsed:", len(trails))
        for t in trails[:5]:
            print(" -", t.get("name"), "|", t.get("url"))
        if is_blocked_response(html, int(result.get("status_code") or 0)):
            print("(page still looks blocked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
