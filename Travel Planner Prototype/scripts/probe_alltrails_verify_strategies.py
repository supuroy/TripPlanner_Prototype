#!/usr/bin/env python3
"""One-off: run the three verify strategies against live Apify (not the 2-call cap)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

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
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


def main() -> int:
    _load_dotenv()
    from src.wanderly.alltrails_provider import (
        _alltrails_search_url,
        _best_verified_match,
        _run_apify_search,
        _verify_direct_page_match,
        apify_token,
    )
    from src.wanderly.alltrails_search_context import region_path_from_context

    if not apify_token():
        print("No APIFY_API_TOKEN — set in repo .env or Travel Planner Prototype/.env")
        return 1

    q = "Yellowstone National Park Heart Lake Trail Wyoming United States"
    ctx = {
        "trail_name": "Heart Lake Trail",
        "park_name": "Yellowstone National Park",
        "region": "Wyoming",
        "country": "United States",
        "search_query": q,
    }
    exp = region_path_from_context(ctx)
    direct_url = "https://www.alltrails.com/trail/us/wyoming/heart-lake-trail"
    strategies = [
        ("direct_trail_page", {"startUrl": direct_url, "maxItems": 3}),
        ("park_region", {"searchType": "city", "city": "Yellowstone National Park", "maxItems": 12}),
        ("search_url", {"startUrl": _alltrails_search_url(q), "maxItems": 12}),
    ]
    report: dict = {"search_query": q, "expected_region": exp, "strategies": []}
    for label, inp in strategies:
        row: dict = {"label": label, "input": inp}
        try:
            batch = _run_apify_search(inp)
        except Exception as exc:
            row["error"] = repr(exc)
            report["strategies"].append(row)
            continue
        row["count"] = len(batch)
        row["samples"] = [
            {
                "name": str(it.get("name") or "")[:120],
                "url": str(it.get("url") or it.get("trailUrl") or it.get("link") or "")[:160],
            }
            for it in batch[:10]
        ]
        best, score = _best_verified_match(
            batch, trail_name="Heart Lake Trail", expected_region_path=exp
        )
        row["best_verified_name"] = (best or {}).get("name")
        row["best_verified_url"] = (best or {}).get("url") or (best or {}).get("trailUrl")
        row["best_verified_score"] = round(score, 3) if best else score
        if label == "direct_trail_page" and batch:
            d, ds = _verify_direct_page_match(
                batch,
                start_url=direct_url,
                trail_name="Heart Lake Trail",
                expected_region_path=exp,
            )
            row["direct_page_match"] = bool(d)
            row["direct_page_score"] = round(ds, 3)
        report["strategies"].append(row)

    out = ROOT / ".cache" / "_probe_alltrails_verify.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Wrote {out}\n")
    for row in report["strategies"]:
        print(f"=== {row['label']} ===")
        if row.get("error"):
            print("ERROR", row["error"])
            continue
        print("count", row["count"])
        for s in row.get("samples") or []:
            print(f"  - {s.get('name')} | {s.get('url')}")
        print(
            "best_verified_match:",
            row.get("best_verified_name"),
            "score",
            row.get("best_verified_score"),
        )
        if "direct_page_match" in row:
            print("direct_page_match:", row["direct_page_match"], "score", row.get("direct_page_score"))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
