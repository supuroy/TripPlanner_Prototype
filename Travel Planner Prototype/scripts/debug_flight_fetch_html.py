"""
Standalone Expedia **flight** SRP dev tool — Playwright fetch once, then parse saved HTML.

**Workflow (mirrors hotel ``debug_hotel_fetch_html.py``):**

1. Fetch SRP HTML once (Playwright):

   cd "…/TripPlanner_Prototype/Travel Planner Prototype"
   python scripts/debug_flight_fetch_html.py --url "https://www.expedia.com/Flights-Search?..."

2. Parse saved snapshot (heuristic + LLM + top-3 rule pick, markdown report):

   python scripts/debug_flight_fetch_html.py --parse-all ^
     --input-html .cache/debug_flight_fetch.html ^
     --url "https://www.expedia.com/Flights-Search?leg1=..." ^
     --route-hint "SEA to SGU" ^
     --price-lo 350 --price-hi 500

Loads repo ``.env`` for ``CURSOR_API_KEY`` when LLM runs. Writes ``.cache/<stem>_flight_parse_report.md``.
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
from urllib.parse import parse_qs, urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAVEL_ROOT = REPO_ROOT / "Travel Planner Prototype"
EXPEDIA_CLI_ROOT = REPO_ROOT / "expedia-listing-cli"

if str(EXPEDIA_CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(EXPEDIA_CLI_ROOT))
if str(TRAVEL_ROOT) not in sys.path:
    sys.path.insert(0, str(TRAVEL_ROOT))

from expedia_listing_cli.browser_fetch import fetch_page_html
from expedia_listing_cli.extract_heuristic import extract_price_like_strings


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
        did = str(obj.get("destination_id") or "")
        if destination_id and did != destination_id:
            continue
        for key in ("search_url", "url"):
            u = str(obj.get(key) or "")
            if "/Flights-Search?" in u:
                out = u
                break
    return out


def _term_safe(s: str) -> str:
    return (s or "").encode("ascii", errors="replace").decode("ascii")


def _print_table(title: str, columns: list[tuple[str, int]], rows: list[list[str]]) -> None:
    print()
    print(_term_safe(title))
    print("-" * len(title))
    if not rows:
        print("  (no rows)")
        return
    widths = [w for _, w in columns]
    hdr = "  ".join(h.ljust(widths[i]) for i, (h, _) in enumerate(columns))
    print(hdr)
    print("  ".join("-" * widths[i] for i in range(len(columns))))
    for row in rows:
        cells = []
        for i, (_, w) in enumerate(columns):
            cells.append(_term_safe(row[i] if i < len(row) else "")[:w].ljust(w))
        print("  ".join(cells))


def _md_cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    if not headers:
        return "_(empty)_\n"
    hdr = "| " + " | ".join(_md_cell(h) for h in headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = []
    for row in rows:
        cells = [_md_cell(row[i] if i < len(row) else "") for i in range(len(headers))]
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([hdr, sep, *body]) + "\n"


def _default_report_md_path(input_html: str) -> Path:
    stem = Path(input_html).stem or "flight_srp"
    return TRAVEL_ROOT / ".cache" / f"{stem}_flight_parse_report.md"


def _route_hint_from_url(url: str, explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()[:240]
    q = parse_qs(urlparse(url).query)
    leg1 = (q.get("leg1") or [""])[0]
    if leg1:
        return leg1[:240]
    return "flight search"


def _heuristic_parse_bundle(
    html: str,
    *,
    route_hint: str,
    to_label: str,
    band: tuple[int, int] | None,
) -> tuple[list[list[str]], list[str], dict[str, Any]]:
    from src.wanderly.flight_listing_alternatives import (
        build_llm_ranked_flight_picks,
        ordered_listing_indices_from_rule_rows,
    )
    from src.wanderly.flight_listing_extract import extract_flight_listing_rows_from_html
    from src.wanderly.flight_listing_score import pick_flight_listing_index
    from src.wanderly.ota_booking_search import _flight_option_from_snapshot

    rows = extract_flight_listing_rows_from_html(html, limit=20)
    fo, prof = _flight_option_from_snapshot(html, route_fallback=route_hint, to_label=to_label or None)

    winner_ix: int | None = None
    rule_dbg: dict[str, Any] = {}
    ranked_picks: list[dict[str, Any]] = []
    if rows:
        winner_ix, rule_dbg = pick_flight_listing_index(
            rows,
            price_lo=band[0] if band else None,
            price_hi=band[1] if band else None,
            route_hint=route_hint,
        )
        pick_ix = ordered_listing_indices_from_rule_rows(
            list(rule_dbg.get("rows") or []), winner_index=winner_ix
        )
        ranked_picks = build_llm_ranked_flight_picks(rows, pick_ix, rule_rows=list(rule_dbg.get("rows") or []))

    table_rows: list[list[str]] = []
    score_by_i = {int(r["i"]): r.get("score") for r in (rule_dbg.get("rows") or []) if isinstance(r, dict) and r.get("i") is not None}
    for i, row in enumerate(rows[:20]):
        mark = ""
        if winner_ix is not None and i == winner_ix:
            mark = "*"
        elif i == 0 and winner_ix is None and row.get("airline") == fo.get("airline"):
            mark = "~"
        table_rows.append(
            [
                str(i),
                str(row.get("airline") or "")[:36],
                str(row.get("price_display") or "")[:14],
                str(row.get("stops") or "")[:12],
                str(row.get("departure_time") or "")[:10],
                str(row.get("arrival_time") or "")[:10],
                str(score_by_i.get(i, "—")),
                str(row.get("duration") or "")[:10],
                mark,
                str(row.get("source") or "")[:20],
            ]
        )

    win_line = [
        f"Heuristic card: {fo.get('airline', '?')}  price={fo.get('price', '?')}  "
        f"route={fo.get('route', '?')}  duration={fo.get('duration', '?')}"
    ]
    if winner_ix is not None and winner_ix < len(rows):
        w = rows[winner_ix]
        win_line.append(
            f"Rule pick #{winner_ix}: {w.get('airline')} @ {w.get('price_display')}  "
            f"score={score_by_i.get(winner_ix, '?')}"
        )
    return table_rows, win_line, {
        "rows": rows,
        "option": fo,
        "profile": prof,
        "rule_dbg": rule_dbg,
        "ranked_picks": ranked_picks,
        "winner_index": winner_ix,
    }


def _llm_parse_bundle(
    html: str,
    *,
    url: str,
    route_hint: str,
    to_label: str,
    band: tuple[int, int] | None,
) -> tuple[list[list[str]], list[list[str]], list[str], dict[str, Any]]:
    from src.wanderly.flight_listing_alternatives import (
        build_llm_ranked_flight_picks,
        ordered_listing_indices_from_rule_rows,
    )
    from src.wanderly.flight_listing_llm import run_flight_listing_llm_parse
    from src.wanderly.flight_listing_score import pick_flight_listing_index

    fo, _prof, meta = run_flight_listing_llm_parse(
        html,
        search_url=url,
        route_hint=route_hint,
        to_label=to_label or None,
        profile_pills=[],
        criteria_lines=[],
        session_id="debug-flight-parse-all",
    )
    meta = meta or {}
    listings = meta.get("listings") if isinstance(meta.get("listings"), list) else []
    listings_dump = [dict(x) for x in listings if isinstance(x, dict)]

    winner_ix = meta.get("best_index")
    rule_rows: list[list[str]] = []
    ranked_picks: list[dict[str, Any]] = []
    rule_dbg: dict[str, Any] = {}

    if listings_dump:
        r_ix, rule_dbg = pick_flight_listing_index(
            listings_dump,
            price_lo=band[0] if band else None,
            price_hi=band[1] if band else None,
            route_hint=route_hint,
        )
        rule_rows_raw = list(rule_dbg.get("rows") or [])
        if r_ix is not None:
            winner_ix = r_ix
            meta["rule_pick"] = True
            meta["rule_pick_rows"] = rule_rows_raw
        pick_ix = ordered_listing_indices_from_rule_rows(rule_rows_raw, winner_index=winner_ix)
        ranked_picks = build_llm_ranked_flight_picks(
            listings_dump, pick_ix, rule_rows=rule_rows_raw
        )
        for r in rule_rows_raw[:20]:
            if not isinstance(r, dict):
                continue
            i_raw = r.get("i", "")
            mark = "*" if winner_ix is not None and str(i_raw) == str(winner_ix) else ""
            rule_rows.append(
                [
                    str(i_raw),
                    str(r.get("score", ""))[:8],
                    str(r.get("usd", "—")),
                    str(r.get("airline") or "")[:32],
                    str(r.get("stops") or "")[:12],
                    mark,
                ]
            )

    listing_rows: list[list[str]] = []
    for i, row in enumerate(listings_dump[:20]):
        mark = "*" if winner_ix is not None and i == int(winner_ix) else ""
        listing_rows.append(
            [
                str(i),
                str(row.get("airline") or "")[:36],
                str(row.get("price_display") or "")[:14],
                str(row.get("stops") or "")[:12],
                str(row.get("departure_time") or "")[:10],
                str(row.get("arrival_time") or "")[:10],
                str(row.get("duration") or "")[:10],
                mark,
            ]
        )

    err = str(meta.get("llm_err") or "").strip()
    if fo:
        win = [
            f"LLM card: {fo.get('airline', '?')}  price={fo.get('price', '?')}  "
            f"route={fo.get('route', '?')}  listings={len(listings_dump)}  "
            f"winner_index={winner_ix}  rule_pick={bool(meta.get('rule_pick'))}"
        ]
    elif err:
        win = [f"No LLM card: {err}"]
    else:
        win = ["No LLM card (unknown)"]

    return listing_rows, rule_rows, win, {
        "fo": fo,
        "meta": meta,
        "listings": listings_dump,
        "ranked_picks": ranked_picks,
        "rule_dbg": rule_dbg,
    }


def _ranked_picks_md(picks: list[dict[str, Any]]) -> str:
    if not picks:
        return "_(no ranked picks — set ``--price-lo`` / ``--price-hi`` for rule scoring)_\n"
    rows = []
    for p in picks:
        rows.append(
            [
                str(p.get("rank", "")),
                str(p.get("airline", ""))[:40],
                str(p.get("price", ""))[:14],
                str(p.get("stops", ""))[:12],
                str(p.get("route", ""))[:40],
                str(p.get("pick_score", "")),
            ]
        )
    return _markdown_table(["rank", "airline", "price", "stops", "route", "score"], rows)


def _build_report_md(
    *,
    html_path: Path,
    url: str,
    title: str,
    route_hint: str,
    band: tuple[int, int] | None,
    h_rows: list[list[str]],
    h_win: list[str],
    h_extra: dict[str, Any],
    l_rows: list[list[str]],
    rule_rows: list[list[str]],
    l_win: list[str],
    l_extra: dict[str, Any],
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    lines = [
        "# Flight SRP parse report",
        "",
        "Generated by `scripts/debug_flight_fetch_html.py --parse-all`.",
        "",
        "## Inputs",
        "",
        f"| Field | Value |",
        f"| --- | --- |",
        f"| generated_utc | {ts} |",
        f"| html_file | `{html_path}` |",
        f"| search_url | {url} |",
        f"| page_title | {title} |",
        f"| route_hint | {route_hint} |",
        f"| price_band_usd | {f'{band[0]}-{band[1]}' if band else '(none)'} |",
        "",
        "## Heuristic parsing",
        "",
        f"- Rows from airline filters / listing snippets: **{len(h_extra.get('rows') or [])}**",
        "",
    ]
    for w in h_win:
        lines.append(f"- {w}")
    lines.extend(
        [
            "",
            _markdown_table(
                ["i", "airline", "price", "stops", "depart", "arrive", "score", "win", "source"],
                h_rows,
            ),
            "",
            "### Heuristic top picks (rule-based, max 3)",
            "",
            _ranked_picks_md(h_extra.get("ranked_picks") or []),
            "",
            "### Heuristic card JSON",
            "",
            "```json",
            json.dumps(h_extra.get("option") or {}, indent=2, ensure_ascii=False, default=str),
            "```",
            "",
            "## LLM parsing",
            "",
        ]
    )
    for w in l_win:
        lines.append(f"- {w}")
    meta = l_extra.get("meta") if isinstance(l_extra.get("meta"), dict) else {}
    lines.extend(
        [
            "",
            _markdown_table(
                ["i", "airline", "price", "stops", "depart", "arrive", "duration", "win"],
                l_rows,
            ),
        ]
    )
    if rule_rows:
        lines.extend(
            [
                "",
                "### LLM rule-pick scores",
                "",
                _markdown_table(["i", "score", "usd", "airline", "stops", "win"], rule_rows),
            ]
        )
    lines.extend(
        [
            "",
            "### LLM top picks (rule-based, max 3)",
            "",
            _ranked_picks_md(l_extra.get("ranked_picks") or []),
            "",
            "### LLM card JSON",
            "",
            "```json",
            json.dumps(
                {k: v for k, v in (l_extra.get("fo") or {}).items() if k not in ("listings", "ranked_picks")},
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            "```",
            "",
            "### LLM meta",
            "",
            "```json",
            json.dumps({k: v for k, v in meta.items() if k != "listings"}, indent=2, default=str),
            "```",
            "",
            "## Next steps",
            "",
            "1. Compare prices in the tables to the live Expedia SRP.",
            "2. If LLM listings are empty, tune `compact_playwright_flight_html` excerpt windows.",
            "3. When satisfied, wire `flight_listing_alternatives` + rule pick into `ota_booking_search` flight step.",
            "",
        ]
    )
    return "\n".join(lines)


def _run_parse_all(args: argparse.Namespace, html: str, url: str) -> Path:
    from src.integrations.cursor_agent_client import load_env_from_roots

    load_env_from_roots()
    os.environ.setdefault("BOOKING_SEARCH_FLIGHT_LLM_PARSE", "1")

    band: tuple[int, int] | None = None
    if args.price_lo is not None and args.price_hi is not None:
        band = (int(args.price_lo), int(args.price_hi))

    route_hint = _route_hint_from_url(url, args.route_hint or "")
    to_label = (args.to_label or "").strip()
    p_in = Path(args.input_html).resolve()
    page_title = ""
    m = re.search(r"<title>(.*?)</title>", html, flags=re.I | re.S)
    if m:
        page_title = m.group(1).strip()[:200]

    quiet = bool(getattr(args, "quiet", False))
    if not quiet:
        print("\n=== Flight parse-all (saved HTML) ===")
        print(f"html: {p_in}  ({len(html):,} bytes)")
        print(f"route_hint: {route_hint}")
        if band:
            print(f"price_band_usd: {band[0]}-{band[1]}")

    h_rows, h_win, h_extra = _heuristic_parse_bundle(
        html, route_hint=route_hint, to_label=to_label, band=band
    )
    if not quiet:
        _print_table(
            f"Heuristic rows ({len(h_extra.get('rows') or [])})",
            [
                ("i", 3),
                ("airline", 36),
                ("price", 14),
                ("stops", 12),
                ("dep", 10),
                ("arr", 10),
                ("score", 8),
                ("dur", 10),
                ("win", 3),
                ("src", 20),
            ],
            h_rows,
        )
        for line in h_win:
            print(line)
        picks = h_extra.get("ranked_picks") or []
        if picks:
            print("\nHeuristic top 3:")
            for p in picks:
                print(f"  #{p.get('rank')} {p.get('airline')} {p.get('price')} score={p.get('pick_score', '')}")

    if not quiet:
        print("\n--- LLM parse ---")
    l_rows, rule_rows, l_win, l_extra = _llm_parse_bundle(
        html, url=url, route_hint=route_hint, to_label=to_label, band=band
    )
    if not quiet:
        meta = l_extra.get("meta") or {}
        if str(meta.get("llm_err") or "").strip() and not l_rows:
            print(f"  llm_err: {meta.get('llm_err')}")
        _print_table(
            f"LLM listings ({len(l_extra.get('listings') or [])})",
            [
                ("i", 3),
                ("airline", 36),
                ("price", 14),
                ("stops", 12),
                ("dep", 10),
                ("arr", 10),
                ("dur", 10),
                ("win", 3),
            ],
            l_rows,
        )
        if rule_rows:
            _print_table(
                "LLM rule scores",
                [("i", 3), ("score", 8), ("usd", 7), ("airline", 32), ("stops", 12), ("win", 3)],
                rule_rows,
            )
        for line in l_win:
            print(line)
        picks = l_extra.get("ranked_picks") or []
        if picks:
            print("\nLLM top 3:")
            for p in picks:
                print(f"  #{p.get('rank')} {p.get('airline')} {p.get('price')} score={p.get('pick_score', '')}")

    out_md = (
        Path(args.output_md).resolve()
        if getattr(args, "output_md", None)
        else _default_report_md_path(str(p_in))
    )
    out_md.parent.mkdir(parents=True, exist_ok=True)
    report = _build_report_md(
        html_path=p_in,
        url=url,
        title=page_title,
        route_hint=route_hint,
        band=band,
        h_rows=h_rows,
        h_win=h_win,
        h_extra=h_extra,
        l_rows=l_rows,
        rule_rows=rule_rows,
        l_win=l_win,
        l_extra=l_extra,
    )
    out_md.write_text(report, encoding="utf-8")
    print(f"\nWrote parse report: {out_md}")
    return out_md


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch and/or parse flight SRP HTML.")
    ap.add_argument("--parse-all", action="store_true", help="Parse --input-html; write markdown report.")
    ap.add_argument("--output-md", default="", help="Report path (default .cache/<stem>_flight_parse_report.md).")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--url", default="", help="Flights-Search URL.")
    ap.add_argument("--input-html", default="", help="Saved Playwright HTML.")
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--destination-id", default="")
    ap.add_argument("--trace-path", default=str(TRAVEL_ROOT / ".cache" / "booking_search_trace.log"))
    ap.add_argument("--headless", type=int, default=1)
    ap.add_argument("--save-html", default=str(TRAVEL_ROOT / ".cache" / "debug_flight_fetch.html"))
    ap.add_argument("--route-hint", default="")
    ap.add_argument("--to-label", default="")
    ap.add_argument("--price-lo", type=int, default=None, help="Per-traveler USD band low (rule pick).")
    ap.add_argument("--price-hi", type=int, default=None, help="Per-traveler USD band high (rule pick).")
    args = ap.parse_args()

    if args.parse_all:
        in_html = (args.input_html or "").strip()
        if not in_html:
            raise SystemExit("--parse-all requires --input-html PATH")
        url = args.url.strip()
        if args.trace and not url:
            url = _last_flight_url_from_trace(Path(args.trace_path), args.destination_id.strip()) or ""
        if not url:
            raise SystemExit("--parse-all needs --url (or --trace) for parser context.")
        p_in = Path(in_html)
        if not p_in.is_file():
            raise SystemExit(f"--input-html not found: {p_in}")
        html = p_in.read_text(encoding="utf-8", errors="replace")
        _run_parse_all(args, html, url)
        return

    url = args.url.strip()
    in_html = (args.input_html or "").strip()
    if args.trace and not url:
        url = _last_flight_url_from_trace(Path(args.trace_path), args.destination_id.strip()) or ""
    if not in_html and not url:
        raise SystemExit("Pass --url to fetch, or --input-html for saved HTML.")

    if in_html:
        html = Path(in_html).read_text(encoding="utf-8", errors="replace")
        if not url:
            url = "https://www.expedia.com/Flights-Search?from_saved_html=1"
        print(f"\n(saved HTML) {Path(in_html).resolve()}  bytes={len(html)}")
    else:
        print(f"\nFetching: {url[:120]}…")
        html = fetch_page_html(url, headless=bool(args.headless))
        out = Path(args.save_html)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(f"saved: {out}  bytes={len(html)}")
        prices = extract_price_like_strings(html, limit=12)
        print(f"price_tokens: {prices[:8]}")


if __name__ == "__main__":
    main()
