"""
Standalone Expedia **hotel** SRP dev tool — fetch HTML and/or parse saved snapshots.

**Parse saved HTML (no Playwright)** — heuristic + LLM, candidate tables:

  cd "…/TripPlanner_Prototype/Travel Planner Prototype"
  python scripts/debug_hotel_fetch_html.py --parse-all ^
    --input-html .cache/porto_moniz_srp.html ^
    --url "https://www.expedia.com/Hotel-Search?destination=Porto+Moniz%2C+Madeira+Region%2C+Portugal&..." ^
    --dest-label "Porto Moniz, Madeira, Portugal" ^
    --area-hint "Porto Moniz, Madeira, Portugal" ^
    --nightly-lo 180 --nightly-hi 320 --nights 1

Loads repo ``.env`` via ``load_env_from_roots()`` (needs ``CURSOR_API_KEY`` for LLM).

Writes a markdown report (default ``.cache/<stem>_parse_report.md``) with full heuristic + LLM tables
and JSON diagnostics for incremental parsing fixes. Use ``--quiet`` to skip terminal tables.
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
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAVEL_ROOT = REPO_ROOT / "Travel Planner Prototype"
EXPEDIA_CLI_ROOT = REPO_ROOT / "expedia-listing-cli"

if str(EXPEDIA_CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(EXPEDIA_CLI_ROOT))
if str(TRAVEL_ROOT) not in sys.path:
    sys.path.insert(0, str(TRAVEL_ROOT))

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
        did = str(obj.get("destination_id") or "")
        if destination_id and did != destination_id:
            continue
        for key in ("search_url", "url"):
            u = str(obj.get(key) or "")
            if "/Hotel-Search?" in u:
                out = u
                break
    return out


def _hotel_fetch_with_retry(url: str, *, headless: bool) -> str:
    html = fetch_page_html(url, headless=headless)
    retry_enabled = os.environ.get("BOOKING_SEARCH_RETRY_WEAK_FETCH", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    if not retry_enabled or "/Hotel-Search?" not in url:
        return html
    if extract_candidate_links(html, limit=8):
        return html
    html2 = fetch_page_html(url, headless=headless)
    if extract_candidate_links(html2, limit=8) or len(html2) > len(html):
        return html2
    return html


def _clean_title(raw: str) -> str:
    t = (raw or "").strip()
    t = re.sub(r"^\s*Opens\s+", "", t, flags=re.I)
    t = re.sub(r"\s+in\s+new\s+tab\s*$", "", t, flags=re.I)
    t = re.sub(r"^More information about\s+", "", t, flags=re.I)
    return " ".join(t.split())[:72]


def _fmt_guest_rating(val: Any) -> str:
    if isinstance(val, (int, float)) and float(val) > 0:
        return f"{float(val):.1f}"
    return "—"


def _fmt_review_count(val: Any) -> str:
    if isinstance(val, int) and val >= 0:
        return str(val)
    return "—"


def _short_href(href: str, n: int = 56) -> str:
    h = (href or "").strip()
    if len(h) <= n:
        return h
    return h[: max(0, n - 1)] + "…"


def _term_safe(s: str) -> str:
    """Avoid Windows cp1252 console crashes on arrows / accented hotel names."""
    return (s or "").encode("ascii", errors="replace").decode("ascii")


def _print_table(title: str, columns: list[tuple[str, int]], rows: list[list[str]]) -> None:
    """Simple fixed-width terminal table (no extra deps)."""
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
    stem = Path(input_html).stem or "hotel_srp"
    return TRAVEL_ROOT / ".cache" / f"{stem}_parse_report.md"


def _heuristic_candidate_table_rows(
    html: str,
    *,
    band: tuple[int, int] | None,
    area: str,
    nights: int,
    table_limit: int = 20,
) -> tuple[list[list[str]], list[str], dict[str, Any]]:
    from src.wanderly.hotel_listing_extract import extract_guest_rating_reviews_near_href
    from src.wanderly.ota_booking_search import (
        _heuristic_listing_nightly_usd_hint,
        _hotel_option_from_snapshot,
        _pick_hotel_link_for_budget,
    )

    links0 = extract_candidate_links(html, limit=24)
    tl, th = (band or (None, None))
    budget_diag: dict[str, Any] = {}
    links = list(links0)
    if links and tl is not None and th is not None and th >= tl:
        from src.wanderly.ota_booking_search import _destination_href_slug

        dest_slug = _destination_href_slug(area=area)
        links, budget_diag = _pick_hotel_link_for_budget(
            links,
            html,
            target_lo=tl,
            target_hi=th,
            destination_slug=dest_slug,
            area_hint=area,
        )

    budget_rows = [r for r in (budget_diag.get("rows") or []) if isinstance(r, dict)]

    def _hotel_href_key(href: str) -> str:
        m = re.search(r"\.h(\d+)\.Hotel-Information", href or "", re.I)
        return m.group(1) if m else ""

    def _budget_score_for_href(href_full: str) -> str:
        if not href_full:
            return "—"
        key = _hotel_href_key(href_full)
        for br in budget_rows:
            bh = (br.get("href_full") or br.get("href") or "").strip()
            if not bh or bh.endswith("…"):
                bh = (br.get("href_full") or "").strip()
            if not bh:
                continue
            if bh == href_full or href_full.startswith(bh.rstrip("…")) or bh in href_full:
                return str(br.get("score", br.get("pick_score", "—")))
            if key and key == _hotel_href_key(bh):
                return str(br.get("score", br.get("pick_score", "—")))
        return "—"

    href_to_orig: dict[str, int] = {}
    for oi, row in enumerate(links0):
        h = (row.get("href") or "").strip()
        if h and h not in href_to_orig:
            href_to_orig[h] = oi

    opt, prof, diag = _hotel_option_from_snapshot(
        html,
        area=area,
        nights=nights,
        image_fallback="",
        target_nightly_usd=band,
    )
    winner_url = str(opt.get("listing_url") or "")

    table_rows: list[list[str]] = []
    for final_rank, row in enumerate(links[: max(1, table_limit)]):
        href = (row.get("href") or "").strip()
        title = _clean_title(row.get("text") or "")
        nightly: str = "—"
        if href:
            hint = _heuristic_listing_nightly_usd_hint(
                html,
                href,
                tl=tl if tl is not None and th is not None else None,
                th=th if tl is not None and th is not None else None,
                exclude=set(),
            )
            if hint is not None:
                nightly = f"${hint}"

        orig_i = href_to_orig.get(href)
        pscore = _budget_score_for_href(href)

        is_card = ""
        if href and href in winner_url:
            is_card = "*"
        elif final_rank == 0 and not winner_url:
            is_card = "*"

        gr_info = extract_guest_rating_reviews_near_href(html, href) if href else {}

        table_rows.append(
            [
                str(final_rank),
                str(orig_i if orig_i is not None else "—"),
                title,
                nightly,
                _fmt_guest_rating(gr_info.get("guest_rating")),
                _fmt_review_count(gr_info.get("review_count")),
                pscore,
                is_card or "",
                href,
            ]
        )

    gr_w = opt.get("guest_rating")
    rc_w = opt.get("review_count")
    rating_bit = ""
    if gr_w is not None or rc_w is not None:
        rating_bit = f"  rating={_fmt_guest_rating(gr_w)}  reviews={_fmt_review_count(rc_w)}"
    winner_line = [
        f"Winner: {opt.get('name', '?')!s}  price={opt.get('price', '?')!s}{rating_bit}  "
        f"source={diag.get('chosen_price_source', '?')!s}"
    ]
    return table_rows, winner_line, {
        "option": opt,
        "diag": diag,
        "anchor_count": len(links0),
        "budget_diag": budget_diag,
        "profile": prof,
    }


def _llm_candidate_table_rows(
    html: str,
    *,
    url: str,
    dest_label: str,
    area_hint: str,
    nights: int,
    band: tuple[int, int] | None,
) -> tuple[list[list[str]], list[list[str]], list[str], dict[str, Any]]:
    from src.wanderly.hotel_listing_llm import run_hotel_listing_llm_parse

    sc = band
    ho, _prof, meta = run_hotel_listing_llm_parse(
        html,
        search_url=url,
        dest_label=dest_label,
        area_hint=area_hint,
        nights=nights,
        profile_pills=[],
        criteria_lines=[],
        session_id="debug-hotel-parse-all",
        image_fallback="",
        scoring_nightly_usd=sc,
        scoring_band_source="debug_script" if sc else "",
    )
    meta = meta or {}
    listings = meta.get("listings") if isinstance(meta.get("listings"), list) else []
    winner_ix = meta.get("best_index")
    if winner_ix is None and ho:
        winner_ix = ho.get("llm_best_index")

    listing_rows: list[list[str]] = []
    for i, row in enumerate(listings[:20]):
        if not isinstance(row, dict):
            continue
        mark = "*" if winner_ix is not None and i == int(winner_ix) else ""
        listing_rows.append(
            [
                str(i),
                str(row.get("name") or "")[:80],
                str(row.get("price_display") or "")[:32],
                _fmt_guest_rating(row.get("guest_rating")),
                _fmt_review_count(row.get("review_count")),
                str(row.get("area_or_neighborhood") or "")[:40],
                mark,
                str(row.get("listing_url") or ""),
            ]
        )

    rule_rows: list[list[str]] = []
    rrows = meta.get("rule_pick_rows") if isinstance(meta.get("rule_pick_rows"), list) else []
    rule_won = bool(meta.get("rule_pick"))
    for r in rrows[:20]:
        if not isinstance(r, dict):
            continue
        i_raw = r.get("i", "")
        mark = ""
        if rule_won and winner_ix is not None and str(i_raw) == str(winner_ix):
            mark = "*"
        rule_rows.append(
            [
                str(i_raw),
                str(r.get("score", ""))[:8],
                str(r.get("usd", "—")),
                _fmt_guest_rating(r.get("guest_rating")),
                _fmt_review_count(r.get("review_count")),
                str(r.get("name") or "")[:80],
                mark,
                str(r.get("url") or ""),
            ]
        )

    err = str(meta.get("llm_err") or "").strip()
    if ho:
        rating_bit = ""
        if ho.get("guest_rating") is not None or ho.get("review_count") is not None:
            rating_bit = (
                f"  rating={_fmt_guest_rating(ho.get('guest_rating'))}  "
                f"reviews={_fmt_review_count(ho.get('review_count'))}"
            )
        winner_line = [
            f"Winner: {ho.get('name', '?')!s}  price={ho.get('price', '?')!s}{rating_bit}  "
            f"path={meta.get('pick_method') or ('rule_pick' if meta.get('rule_pick') else 'llm')}  "
            f"listings_parsed={len(listings)}"
        ]
    elif err:
        winner_line = [f"No LLM card: {err}"]
    else:
        winner_line = ["No LLM card (unknown)"]

    return listing_rows, rule_rows, winner_line, {"ho": ho, "meta": meta, "listings": listings}


def _build_parse_report_markdown(
    *,
    html_path: Path,
    url: str,
    title: str,
    band: tuple[int, int] | None,
    dest_label: str,
    area_hint: str,
    nights: int,
    h_rows: list[list[str]],
    h_win: list[str],
    h_extra: dict[str, Any],
    l_rows: list[list[str]],
    rule_rows: list[list[str]],
    l_win: list[str],
    l_extra: dict[str, Any],
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    lines: list[str] = [
        "# Hotel SRP parse report",
        "",
        "Generated by `scripts/debug_hotel_fetch_html.py --parse-all`.",
        "",
        "## Inputs",
        "",
        f"| Field | Value |",
        f"| --- | --- |",
        f"| generated_utc | {ts} |",
        f"| html_file | `{html_path}` |",
        f"| html_bytes | {html_path.stat().st_size if html_path.is_file() else 0:,} |",
        f"| search_url | {url} |",
        f"| page_title | {title} |",
        f"| dest_label | {dest_label} |",
        f"| area_hint | {area_hint} |",
        f"| nights | {nights} |",
        f"| nightly_band_usd | {f'{band[0]}-{band[1]}' if band else '(none)'} |",
        "",
        "## Heuristic parsing",
        "",
        f"- HTML anchors found: **{h_extra.get('anchor_count', '?')}**",
        f"- **{' '.join(h_win)}**",
        "",
        "Candidates after optional budget reorder (`rank` 0 = first card). "
        "Column `win` is `*` for the row used as the returned hotel card. "
        "Higher `score` is better (nightly band + guest rating + review count; same formula as LLM rule_pick).",
        "",
        _markdown_table(
            [
                "rank",
                "orig",
                "name",
                "nightly_usd",
                "guest_rating",
                "reviews",
                "score",
                "win",
                "listing_url",
            ],
            h_rows,
        ),
    ]

    opt = h_extra.get("option") if isinstance(h_extra.get("option"), dict) else {}
    prof = h_extra.get("profile") if isinstance(h_extra.get("profile"), dict) else {}
    diag = h_extra.get("diag") if isinstance(h_extra.get("diag"), dict) else {}
    lines.extend(
        [
            "### Heuristic card (production `_hotel_option_from_snapshot`)",
            "",
            "```json",
            json.dumps(
                {"hotel": {k: v for k, v in opt.items() if k != "best"}, "field_sources": prof},
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            "```",
            "",
            "### Heuristic diagnostics",
            "",
            "```json",
            json.dumps(diag, indent=2, ensure_ascii=False, default=str),
            "```",
            "",
        ]
    )
    bd = h_extra.get("budget_diag")
    if isinstance(bd, dict) and bd.get("rows"):
        lines.extend(
            [
                "### Budget reorder scores (before final_rank sort)",
                "",
                _markdown_table(
                    [
                        "orig_i",
                        "median_usd",
                        "guest_rating",
                        "reviews",
                        "score",
                        "title",
                        "href",
                    ],
                    [
                        [
                            str(r.get("original_index", "")),
                            str(r.get("median_usd_near_href", "—")),
                            _fmt_guest_rating(r.get("guest_rating")),
                            _fmt_review_count(r.get("review_count")),
                            str(r.get("score", r.get("pick_score", ""))),
                            str(r.get("title", "")),
                            str(r.get("href", "")),
                        ]
                        for r in (bd.get("rows") or [])
                        if isinstance(r, dict)
                    ],
                ),
                "",
            ]
        )

    meta = l_extra.get("meta") if isinstance(l_extra.get("meta"), dict) else {}
    ho = l_extra.get("ho") if isinstance(l_extra.get("ho"), dict) else {}
    listings = l_extra.get("listings") if isinstance(l_extra.get("listings"), list) else []

    lines.extend(
        [
            "## LLM parsing",
            "",
            f"- **{' '.join(l_win)}**",
            f"- excerpt_chars: {meta.get('excerpt_chars', '—')}",
            f"- llm_response_chars: {meta.get('llm_chars', '—')}",
            f"- llm_err: `{meta.get('llm_err') or ''}`",
            f"- pick_method: `{meta.get('pick_method') or ''}`",
            f"- pick_summary: {meta.get('pick_summary') or '—'}",
            "",
            "### LLM parsed listings",
            "",
            _markdown_table(
                [
                    "i",
                    "name",
                    "price_display",
                    "guest_rating",
                    "reviews",
                    "area",
                    "win",
                    "listing_url",
                ],
                l_rows,
            ),
        ]
    )

    if rule_rows:
        lines.extend(
            [
                "### LLM rule-pick scores",
                "",
                _markdown_table(
                    ["i", "score", "usd", "guest_rating", "reviews", "name", "win", "url"],
                    rule_rows,
                ),
                "",
            ]
        )

    if ho:
        lines.extend(
            [
                "### LLM winner card",
                "",
                "```json",
                json.dumps(
                    {k: v for k, v in ho.items() if k not in ("best", "listings")},
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ),
                "```",
                "",
            ]
        )

    safe_meta = {k: v for k, v in meta.items() if k != "listings"}
    lines.extend(
        [
            "### LLM meta (no listings blob)",
            "",
            "```json",
            json.dumps(safe_meta, indent=2, ensure_ascii=False, default=str),
            "```",
            "",
        ]
    )
    if listings:
        lines.extend(
            [
                "### LLM listings JSON (raw)",
                "",
                "```json",
                json.dumps(listings, indent=2, ensure_ascii=False, default=str),
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## Notes for iteration",
            "",
            "- Compare **Heuristic** `listing_url` rows vs what you see on the Expedia SRP.",
            "- If **LLM parsed listings** is empty but heuristic has many anchors, the LLM excerpt likely "
            "missed result cards — tune `compact_playwright_hotel_html` / listing focus.",
            "- Re-run after code changes:",
            "",
            "```powershell",
            f"python scripts/debug_hotel_fetch_html.py --parse-all --input-html \"{html_path.name}\" "
            f"--url \"<Hotel-Search-URL>\" --dest-label \"...\" --nightly-lo 180 --nightly-hi 320",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _run_parse_all(args: argparse.Namespace, html: str, url: str) -> Path:
    from src.integrations.cursor_agent_client import load_env_from_roots

    load_env_from_roots()
    os.environ.setdefault("BOOKING_SEARCH_HOTEL_LLM_PARSE", "1")

    band: tuple[int, int] | None = None
    if args.nightly_lo is not None and args.nightly_hi is not None:
        band = (int(args.nightly_lo), int(args.nightly_hi))

    dest_label = (args.dest_label or "").strip() or "hotel search"
    area_hint = (args.area_hint or "").strip() or dest_label
    nights = max(1, int(args.nights))

    p_in = Path(args.input_html).resolve()
    page_title = ""
    m = re.search(r"<title>(.*?)</title>", html, flags=re.I | re.S)
    if m:
        page_title = m.group(1).strip()[:200]

    quiet = bool(getattr(args, "quiet", False))
    if not quiet:
        print("\n=== Hotel parse-all (saved HTML, no fetch) ===")
        print(f"html: {p_in}  ({len(html):,} bytes)")
        print(f"context_url: {url[:220]}{'…' if len(url) > 220 else ''}")
        if band:
            print(f"nightly_band_usd: {band[0]}-{band[1]}")
        if page_title:
            print(f"title: {page_title[:100]}")

    h_rows, h_win, h_extra = _heuristic_candidate_table_rows(
        html, band=band, area=area_hint, nights=nights
    )
    term_h = [
        [
            r[0],
            r[1],
            (r[2] or "")[:32],
            r[3],
            r[4],
            r[5],
            r[6] if len(r) > 6 else "—",
            r[7] if len(r) > 7 else "",
            _short_href(r[8] if len(r) > 8 else "", 36),
        ]
        for r in h_rows
    ]
    if not quiet:
        _print_table(
            f"Heuristic candidates ({h_extra.get('anchor_count', '?')} anchors; "
            f"after budget reorder, limit 20)",
            [
                ("rank", 4),
                ("orig", 4),
                ("name", 32),
                ("nightly", 8),
                ("rating", 6),
                ("reviews", 7),
                ("score", 8),
                ("win", 3),
                ("href", 36),
            ],
            term_h,
        )
        for line in h_win:
            print(line)

    if not quiet:
        print("\n--- LLM parse (CURSOR_API_KEY from .env) ---")
    l_rows, rule_rows, l_win, l_extra = _llm_candidate_table_rows(
        html,
        url=url,
        dest_label=dest_label,
        area_hint=area_hint,
        nights=nights,
        band=band,
    )
    meta = l_extra.get("meta") or {}
    if not quiet and str(meta.get("llm_err") or "").strip() and not l_rows:
        print(f"  llm_err: {meta.get('llm_err')}")
        print(f"  excerpt_chars: {meta.get('excerpt_chars')}  llm_chars: {meta.get('llm_chars')}")

    term_l = [
        [
            r[0],
            (r[1] or "")[:36],
            (r[2] or "")[:18],
            r[3],
            r[4],
            (r[5] or "")[:22],
            r[6],
            _short_href(r[7] if len(r) > 7 else "", 32),
        ]
        for r in l_rows
    ]
    term_rule = [
        [
            r[0],
            r[1],
            r[2],
            r[3],
            r[4],
            (r[5] or "")[:32],
            r[6],
            _short_href(r[7] if len(r) > 7 else "", 32),
        ]
        for r in rule_rows
    ]
    if not quiet:
        _print_table(
            f"LLM parsed listings ({len((meta.get('listings') or []))} rows)",
            [
                ("i", 3),
                ("name", 36),
                ("price", 18),
                ("rating", 6),
                ("reviews", 7),
                ("area", 22),
                ("win", 3),
                ("url", 32),
            ],
            term_l,
        )
        if rule_rows:
            _print_table(
                "LLM rule-pick scores (parseable nightly USD only)",
                [
                    ("i", 3),
                    ("score", 8),
                    ("usd", 7),
                    ("rating", 6),
                    ("reviews", 7),
                    ("name", 32),
                    ("win", 3),
                    ("url", 32),
                ],
                term_rule,
            )
        for line in l_win:
            print(line)

    out_md = Path(args.output_md).resolve() if getattr(args, "output_md", None) else _default_report_md_path(str(p_in))
    out_md.parent.mkdir(parents=True, exist_ok=True)
    report = _build_parse_report_markdown(
        html_path=p_in,
        url=url,
        title=page_title,
        band=band,
        dest_label=dest_label,
        area_hint=area_hint,
        nights=nights,
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

    if args.json:
        print("\n=== JSON dump (--json) ===")
        print(
            json.dumps(
                {"heuristic": h_extra, "llm_meta": {k: v for k, v in meta.items() if k != "listings"}},
                indent=2,
                default=str,
            )[:20000]
        )
    return out_md


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Fetch and/or parse hotel SRP HTML (Playwright fetch optional)."
    )
    ap.add_argument(
        "--parse-all",
        action="store_true",
        help="Use --input-html only: run heuristic + LLM, write .md report (loads .env).",
    )
    ap.add_argument(
        "--output-md",
        default="",
        help="Markdown report path (default: .cache/<input-stem>_parse_report.md).",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="With --parse-all: skip terminal tables; still writes --output-md.",
    )
    ap.add_argument("--url", default="", help="Hotel-Search URL (context for parsers).")
    ap.add_argument(
        "--input-html",
        default="",
        help="Saved Playwright HTML (required for --parse-all). No network fetch.",
    )
    ap.add_argument("--trace", action="store_true", help="Resolve --url from booking_search_trace.log.")
    ap.add_argument("--destination-id", default="")
    ap.add_argument("--trace-path", default=str(TRAVEL_ROOT / ".cache" / "booking_search_trace.log"))
    ap.add_argument("--headless", type=int, default=1)
    ap.add_argument("--save-html", default=str(TRAVEL_ROOT / ".cache" / "debug_hotel_fetch.html"))
    ap.add_argument("--excerpt", default="", help="Write LLM-focused excerpt file.")
    ap.add_argument("--llm", action="store_true", help="Legacy: LLM only with verbose JSON.")
    ap.add_argument("--heuristic", action="store_true", help="Legacy: heuristic only with verbose JSON.")
    ap.add_argument("--dest-label", default="")
    ap.add_argument("--area-hint", default="")
    ap.add_argument("--nights", type=int, default=1)
    ap.add_argument("--nightly-lo", type=int, default=None)
    ap.add_argument("--nightly-hi", type=int, default=None)
    ap.add_argument("--json", action="store_true", help="With --parse-all: also dump meta JSON.")
    args = ap.parse_args()

    if args.parse_all:
        in_html = (args.input_html or "").strip()
        if not in_html:
            raise SystemExit("--parse-all requires --input-html PATH (saved SRP snapshot).")
        url = args.url.strip()
        if args.trace and not url:
            url = _last_hotel_url_from_trace(Path(args.trace_path), args.destination_id.strip()) or ""
        if not url:
            raise SystemExit("--parse-all needs --url=… (or --trace) for parser context.")
        p_in = Path(in_html)
        if not p_in.is_file():
            raise SystemExit(f"--input-html not found: {p_in}")
        html = p_in.read_text(encoding="utf-8", errors="replace")
        _run_parse_all(args, html, url)
        return

    in_html = (args.input_html or "").strip()
    url = args.url.strip()
    if args.trace and not url and not in_html:
        url = _last_hotel_url_from_trace(Path(args.trace_path), args.destination_id.strip()) or ""
    if not in_html and not url:
        raise SystemExit("No input. Pass --url, --input-html, or --trace.")

    if in_html:
        p_in = Path(in_html)
        if not p_in.is_file():
            raise SystemExit(f"--input-html not found: {p_in}")
        html = p_in.read_text(encoding="utf-8", errors="replace")
        if not url:
            url = "https://www.expedia.com/Hotel-Search?from_saved_html=1"
        print(f"\n(saved HTML) {p_in.resolve()}  bytes={len(html)}")
    else:
        html = _hotel_fetch_with_retry(url, headless=bool(args.headless))
        Path(args.save_html).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save_html).write_text(html, encoding="utf-8", errors="replace")
        print(f"saved: {args.save_html}  bytes={len(html)}")

    if args.heuristic or args.llm:
        if args.heuristic:
            band = None
            if args.nightly_lo is not None and args.nightly_hi is not None:
                band = (int(args.nightly_lo), int(args.nightly_hi))
            rows, win, _ = _heuristic_candidate_table_rows(
                html,
                band=band,
                area=(args.area_hint or "hotel").strip(),
                nights=max(1, int(args.nights)),
            )
            _print_table(
                "Heuristic",
                [
                    ("rank", 4),
                    ("orig", 4),
                    ("name", 40),
                    ("nightly", 8),
                    ("pick_score", 10),
                    ("href", 44),
                    ("", 8),
                ],
                rows,
            )
            for w in win:
                print(w)
        if args.llm:
            from src.integrations.cursor_agent_client import load_env_from_roots

            load_env_from_roots()
            band = None
            if args.nightly_lo is not None and args.nightly_hi is not None:
                band = (int(args.nightly_lo), int(args.nightly_hi))
            lr, rr, win, _ = _llm_candidate_table_rows(
                html,
                url=url,
                dest_label=(args.dest_label or "hotel").strip(),
                area_hint=(args.area_hint or "").strip(),
                nights=max(1, int(args.nights)),
                band=band,
            )
            _print_table(
                "LLM listings",
                [("i", 3), ("name", 44), ("price", 22), ("area", 28), ("url", 40), ("", 8)],
                lr,
            )
            if rr:
                _print_table(
                    "Rule scores",
                    [("i", 3), ("score", 8), ("usd", 7), ("name", 44), ("url", 40), ("", 8)],
                    rr,
                )
            for w in win:
                print(w)


if __name__ == "__main__":
    main()
