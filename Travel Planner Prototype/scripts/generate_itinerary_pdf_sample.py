#!/usr/bin/env python3
"""One-off: build a sample itinerary PDF from a saved wanderly trip file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.wanderly.itinerary_pdf_export import (  # noqa: E402
    build_itinerary_print_html,
    html_to_pdf,
    load_trip_destination,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate sample itinerary PDF from trip disk JSON.")
    ap.add_argument(
        "--trip",
        type=Path,
        default=ROOT / ".cache" / "wanderly_trips" / "trip-9693f3b3d414.json",
        help="Path to saved trip JSON",
    )
    ap.add_argument("--dest-id", default="port-renfrew-bc", help="Destination id in last_destinations")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "exports" / "samples",
        help="Output directory for HTML + PDF",
    )
    args = ap.parse_args()

    dest, pills = load_trip_destination(args.trip, args.dest_id)
    html = build_itinerary_print_html(dest, pills)
    slug = args.dest_id.replace("/", "-")
    out_dir = args.out_dir
    html_path = out_dir / f"{slug}-itinerary.html"
    pdf_path = out_dir / f"{slug}-itinerary.pdf"

    html_to_pdf(html, pdf_path, html_debug_path=html_path)
    print(f"Wrote HTML: {html_path}")
    print(f"Wrote PDF:  {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
