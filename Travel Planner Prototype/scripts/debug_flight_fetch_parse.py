"""
Standalone Expedia flight fetch+parse debugger (no UI).

**Preferred:** use ``scripts/debug_flight_fetch_html.py`` for Playwright fetch + ``--parse-all``
(heuristic + LLM tables, top-3 rule picks, markdown report). This script remains a quick smoke probe.

Run from **Travel Planner Prototype** (the script lives under ``scripts/`` there):

  cd "…/TripPlanner_Prototype/Travel Planner Prototype"
  python scripts/debug_flight_fetch_html.py --url "https://www.expedia.com/Flights-Search?..."
  python scripts/debug_flight_fetch_html.py --parse-all --input-html .cache/debug_flight_fetch.html --url "…" --price-lo 350 --price-hi 500
  python scripts/debug_flight_fetch_parse.py --url "…" --llm
  python scripts/debug_flight_fetch_parse.py --trace --destination-id phuket-thailand

``--llm`` does not change the URL you pass: a pasted ``trip=roundtrip`` URL stays as-is. The Wanderly
``booking_search_step`` flight path builds **one-way** URLs from the catalog by default
(``BOOKING_SEARCH_FLIGHT_FORCE_ONEWAY``).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAVEL_ROOT = REPO_ROOT / "Travel Planner Prototype"
EXPEDIA_CLI_ROOT = REPO_ROOT / "expedia-listing-cli"

if str(EXPEDIA_CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(EXPEDIA_CLI_ROOT))
if str(TRAVEL_ROOT) not in sys.path:
    sys.path.insert(0, str(TRAVEL_ROOT))

from expedia_listing_cli.browser_fetch import fetch_page_html
from expedia_listing_cli.extract_heuristic import (
    extract_flight_airline_filter_hints,
    extract_flight_duration_guess,
    extract_price_like_strings,
)


def _last_flight_url_from_trace(trace_path: Path, destination_id: str) -> str | None:
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
        if obj.get("event") in ("flight_url_built", "step_ok"):
            did = str(obj.get("destination_id") or "")
            url = str(obj.get("url") or obj.get("search_url") or "")
            if did == destination_id and "/Flights-Search?" in url:
                out = url
    return out


def _scan_currency_tokens(html: str) -> dict[str, list[str]]:
    patterns = {
        "dollar": r"\$\s*[\d,]+(?:\.\d{2})?",
        "baht": r"฿\s*[\d,]+(?:\.\d{2})?",
        "eur": r"€\s*[\d,]+(?:\.\d{2})?",
        "gbp": r"£\s*[\d,]+(?:\.\d{2})?",
        "usd_word": r"\bUSD\s*[\d,]+(?:\.\d{2})?",
    }
    out: dict[str, list[str]] = {}
    for key, pat in patterns.items():
        vals: list[str] = []
        for m in re.findall(pat, html):
            if m not in vals:
                vals.append(m)
            if len(vals) >= 10:
                break
        out[key] = vals
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="Flight URL to fetch.")
    ap.add_argument("--trace", action="store_true", help="Read URL from booking_search_trace.log.")
    ap.add_argument("--destination-id", default="phuket-thailand")
    ap.add_argument("--trace-path", default=str(TRAVEL_ROOT / ".cache" / "booking_search_trace.log"))
    ap.add_argument("--headless", type=int, default=1)
    ap.add_argument("--save-html", default=str(TRAVEL_ROOT / ".cache" / "debug_flight_fetch_parse.html"))
    ap.add_argument(
        "--llm",
        action="store_true",
        help="After fetch, run Cursor LLM listing parse on Playwright HTML only (needs CURSOR_API_KEY).",
    )
    args = ap.parse_args()

    url = args.url.strip()
    if args.trace and not url:
        url = _last_flight_url_from_trace(Path(args.trace_path), args.destination_id) or ""
    if not url:
        raise SystemExit("No URL provided/found. Pass --url or use --trace with a valid trace file.")

    print("\n=== Debug Flight Fetch+Parse ===")
    print(f"url: {url}")
    q = parse_qs(urlparse(url).query)
    print(f"leg1: {q.get('leg1', [''])[0]}")
    print(f"trip: {q.get('trip', [''])[0]}")
    print(f"passengers: {q.get('passengers', [''])[0]}")
    trip_val = (q.get("trip") or [""])[0].lower()
    if trip_val == "roundtrip" or q.get("leg2"):
        print(
            "(note) URL is round-trip / has leg2 — fine for this debug script. "
            "Wanderly ``booking_search_step`` uses one-way catalog URLs by default unless "
            "BOOKING_SEARCH_FLIGHT_FORCE_ONEWAY=0."
        )

    html = fetch_page_html(url, headless=bool(args.headless))
    out_path = Path(args.save_html)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"html_len: {len(html)}")
    print(f"saved_html: {out_path}")

    m = re.search(r"<title>(.*?)</title>", html, flags=re.I | re.S)
    print(f"title: {(m.group(1).strip() if m else '<none>')}")

    heuristic_prices = extract_price_like_strings(html, limit=20)
    print(f"heuristic_price_like_strings({len(heuristic_prices)}): {heuristic_prices[:10]}")

    token_scan = _scan_currency_tokens(html)
    for k, vals in token_scan.items():
        print(f"{k}({len(vals)}): {vals[:6]}")

    # Sniff for client-side structured price-like fields to inspect parser drift.
    structured_hits = re.findall(r'"(?:displayPrice|formattedPrice|priceText|totalPrice)"\s*:\s*"([^"]{1,80})"', html)
    dedup_struct: list[str] = []
    for x in structured_hits:
        if x not in dedup_struct:
            dedup_struct.append(x)
        if len(dedup_struct) >= 12:
            break
    print(f"structured_price_fields({len(dedup_struct)}): {dedup_struct}")

    hints = extract_flight_airline_filter_hints(html, limit=20)
    print(f"airline_filter_hints({len(hints)}): {hints[:8]}")
    dur = extract_flight_duration_guess(html)
    print(f"duration_guess: {dur!r}")

    if args.llm:
        from src.integrations.cursor_agent_client import load_env_from_roots
        from src.wanderly.flight_listing_llm import run_flight_listing_llm_parse

        load_env_from_roots()
        leg1 = (q.get("leg1") or [""])[0]
        fo, _prof, meta = run_flight_listing_llm_parse(
            html,
            search_url=url,
            route_hint=leg1[:240] or "flight",
            to_label=None,
            profile_pills=[],
            criteria_lines=[],
            session_id=None,
        )
        print("\n=== LLM + Tavily listing parse ===")
        safe_meta = {k: v for k, v in (meta or {}).items() if k != "listings"}
        print(json.dumps(safe_meta, indent=2, default=str))
        if fo:
            preview = {k: fo[k] for k in sorted(fo) if k != "best"}
            print("picked:", json.dumps(preview, indent=2, default=str))
            if meta and isinstance(meta.get("listings"), list):
                print(f"listings_returned: {len(meta['listings'])}")
        else:
            print("(LLM parse failed — see llm_err / tavily_err in meta)")


if __name__ == "__main__":
    main()

