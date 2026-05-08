"""
CLI: build Expedia hotel search URLs, fetch rendered HTML (Playwright), heuristic parse.

Lab / demo only — not integrated with Wanderly. Do not present output as live quotes or inventory.

Usage::

    cd expedia-listing-cli
    pip install -r requirements.txt
    playwright install chromium

    # Browser-shaped URL (use full destination label + regionId from a working Expedia URL)
    python -m expedia_listing_cli url \\
        --destination "Las Vegas, Nevada, United States of America" \\
        --region-id 2008 \\
        --checkin 2026-05-07 --checkout 2026-05-09

    python -m expedia_listing_cli fetch --url "https://www.expedia.com/Hotel-Search?..."

    # Extract query params from a golden browser URL (all filters on) → JSON catalog
    python -m expedia_listing_cli extract-filters --url "https://www.expedia.com/Hotel-Search?..." -o catalog.json

    # Build / fetch using catalog defaults (Travel Planner can set EXPEDIA_HOTEL_SEARCH_CATALOG_JSON)
    python -m expedia_listing_cli url --catalog catalog.json --destination "..." --region-id 2008 ...
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from expedia_listing_cli.url_builder import build_hotel_search_url


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _url_from_args(args: argparse.Namespace) -> str:
    tid = (args.typeahead_collation_id or os.environ.get("EXPEDIA_TYPEAHEAD_COLLATION_ID") or "").strip()
    rid = args.region_id
    if rid is None and (os.environ.get("EXPEDIA_REGION_ID") or "").strip():
        rid = os.environ.get("EXPEDIA_REGION_ID", "").strip()
    cat_path = (getattr(args, "catalog", None) or os.environ.get("EXPEDIA_HOTEL_SEARCH_CATALOG_JSON") or "").strip()
    return build_hotel_search_url(
        args.destination,
        _parse_date(args.checkin),
        _parse_date(args.checkout),
        adults=args.adults,
        rooms=args.rooms,
        region_id=rid,
        flexibility=args.flexibility,
        sort=args.sort,
        typeahead_collation_id=tid or None,
        catalog_path=cat_path or None,
    )


def cmd_url(args: argparse.Namespace) -> None:
    print(_url_from_args(args))


def cmd_fetch(args: argparse.Namespace) -> None:
    from expedia_listing_cli.browser_fetch import write_snapshot
    from expedia_listing_cli.extract_heuristic import print_report

    load_dotenv()
    out_dir = Path(os.environ.get("OUTPUT_DIR", ".cache"))
    headless = os.environ.get("HEADLESS", "1").strip().lower() not in ("0", "false", "no")

    if args.url:
        url = args.url.strip()
    else:
        url = _url_from_args(args)
    env_url = (os.environ.get("EXPEDIA_HOTEL_SEARCH_URL") or "").strip()
    if env_url:
        print("Using EXPEDIA_HOTEL_SEARCH_URL from environment.", file=sys.stderr, flush=True)
        url = env_url

    print(f"Fetching: {url}", file=sys.stderr, flush=True)
    path = write_snapshot(url, out_dir, headless=headless)
    print(f"Wrote: {path.resolve()}", flush=True)
    print("Heuristic parse (stdout, JSON):", file=sys.stderr, flush=True)
    print_report(path)


def cmd_parse(args: argparse.Namespace) -> None:
    from expedia_listing_cli.extract_heuristic import print_report

    print_report(Path(args.html_file))


def cmd_extract_filters(args: argparse.Namespace) -> None:
    import json

    from expedia_listing_cli.filter_catalog import extract_catalog_from_url, save_catalog_json

    url = (args.url or "").strip()
    if not url and args.url_file:
        url = Path(args.url_file).read_text(encoding="utf-8").strip()
    if not url:
        raise SystemExit("extract-filters: pass --url or --url-file with a non-empty Hotel-Search URL")

    catalog = extract_catalog_from_url(url)
    if args.stdout:
        print(json.dumps(catalog.to_json_dict(), indent=2, ensure_ascii=False))
        return
    out = Path(args.output)
    save_catalog_json(catalog, out)
    print(f"Wrote {out.resolve()} ({len(catalog.query_params)} query keys)", file=sys.stderr, flush=True)


def _add_build_args(p: argparse.ArgumentParser, *, required: bool = True) -> None:
    p.add_argument("--destination", required=required, help='Full label, e.g. "Las Vegas, Nevada, United States of America"')
    p.add_argument("--checkin", required=required, help="YYYY-MM-DD")
    p.add_argument("--checkout", required=required, help="YYYY-MM-DD")
    p.add_argument("--adults", type=int, default=2)
    p.add_argument("--rooms", type=int, default=1)
    p.add_argument(
        "--region-id",
        default=None,
        metavar="ID",
        help="From a working browser URL (e.g. 2008 for Las Vegas). Or set EXPEDIA_REGION_ID in .env.",
    )
    p.add_argument("--flexibility", default="0_DAY", help="e.g. 0_DAY")
    p.add_argument("--sort", default="RECOMMENDED", help="e.g. RECOMMENDED")
    p.add_argument(
        "--typeahead-collation-id",
        default=None,
        help="Optional; copy from browser if needed. Or set EXPEDIA_TYPEAHEAD_COLLATION_ID.",
    )
    p.add_argument(
        "--catalog",
        default=None,
        metavar="PATH",
        help="JSON from extract-filters; merges golden query params. Or set EXPEDIA_HOTEL_SEARCH_CATALOG_JSON.",
    )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Expedia listing lab CLI (demo only)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_url = sub.add_parser("url", help="Print a browser-style Hotel-Search URL")
    _add_build_args(p_url, required=True)
    p_url.set_defaults(func=cmd_url)

    p_fetch = sub.add_parser("fetch", help="Playwright fetch + save HTML + heuristic parse")
    p_fetch.add_argument("--url", default=None, help="Full URL (optional if build args given)")
    _add_build_args(p_fetch, required=False)
    p_fetch.set_defaults(func=cmd_fetch)

    p_parse = sub.add_parser("parse", help="Heuristic parse from saved HTML file")
    p_parse.add_argument("html_file")
    p_parse.set_defaults(func=cmd_parse)

    p_ef = sub.add_parser(
        "extract-filters",
        help="Parse a golden Hotel-Search URL and write query params JSON for --catalog / Travel Planner",
    )
    src = p_ef.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", default=None, help="Full browser URL (quote in shell)")
    src.add_argument(
        "--url-file",
        default=None,
        metavar="PATH",
        help="Read URL from a one-line text file (avoids shell escaping)",
    )
    p_ef.add_argument(
        "-o",
        "--output",
        default="expedia_hotel_search_catalog.json",
        help="Output JSON path (default: ./expedia_hotel_search_catalog.json)",
    )
    p_ef.add_argument(
        "--stdout",
        action="store_true",
        help="Print JSON to stdout instead of writing a file",
    )
    p_ef.set_defaults(func=cmd_extract_filters)

    args = parser.parse_args()
    if args.cmd == "fetch" and not args.url:
        if not (args.destination and args.checkin and args.checkout):
            parser.error("fetch requires either --url or (--destination --checkin --checkout)")
    args.func(args)


if __name__ == "__main__":
    main()
