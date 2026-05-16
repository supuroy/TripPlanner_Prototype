"""
Deterministic hotel SRP row selection after LLM listing parse.

Uses a nightly USD band (search intent, ``budget_guidance`` lodging copy, or profile-derived)
to score parsed rows. Parsing stays LLM-driven; the **winner index** is rule-based by default
(``BOOKING_SEARCH_HOTEL_RULE_PICK``, on when unset).

Each rule-pick attempt is **appended** to ``Travel Planner Prototype/.cache/hotel_rule_pick.log`` by default
(``BOOKING_SEARCH_HOTEL_RULE_PICK_FILE`` unset or 1). Set ``BOOKING_SEARCH_HOTEL_RULE_PICK_LOG=1`` to also print
the same block to the server terminal. Override path with ``BOOKING_SEARCH_HOTEL_RULE_PICK_LOG_PATH``.

Every completed hotel ``booking_search_step`` also appends a ``[hotel_final]`` block: the returned card,
**why** it was chosen (``pick_method`` / ``pick_summary`` from the LLM path when applicable), **every
property-like anchor** from a raw HTML scan (``[considered]``), plus parsed LLM rows / rule scores / heuristic
re-order when applicable.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def hotel_rule_pick_log_enabled() -> bool:
    """Mirror rule-pick block to stdout when ``BOOKING_SEARCH_HOTEL_RULE_PICK_LOG=1``."""
    return os.environ.get("BOOKING_SEARCH_HOTEL_RULE_PICK_LOG", "").strip().lower() in ("1", "true", "yes", "on")


def hotel_rule_pick_file_enabled() -> bool:
    """Append rule-pick block to a log file (default on). Set ``BOOKING_SEARCH_HOTEL_RULE_PICK_FILE=0`` to disable."""
    raw = (os.environ.get("BOOKING_SEARCH_HOTEL_RULE_PICK_FILE") or "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def _travel_planner_root() -> Path:
    return Path(__file__).resolve().parents[2]


def hotel_rule_pick_log_path() -> Path | None:
    """
    Destination for rule-pick append logs. ``None`` when file logging is disabled.
    """
    if not hotel_rule_pick_file_enabled():
        return None
    raw = (os.environ.get("BOOKING_SEARCH_HOTEL_RULE_PICK_LOG_PATH") or "").strip()
    root = _travel_planner_root()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = root / raw
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    d = root / ".cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / "hotel_rule_pick.log"


def _format_hotel_rule_pick_block(
    dbg: dict[str, Any],
    *,
    band_source: str,
    area_hint: str,
    picked_index: int | None,
    session_id: str = "",
) -> str:
    src = (band_source or "unknown").strip() or "unknown"
    lo = dbg.get("nightly_lo")
    hi = dbg.get("nightly_hi")
    ah = (area_hint or "").strip()
    ah_disp = repr(ah[:72]) + ("…" if len(ah) > 72 else "") if ah else "(none)"
    sid = (session_id or "").strip()[:40]
    head = "[hotel_rule_pick] nightly band and candidates"
    if sid:
        head += f"  session_id={sid!s}"
    lines = [
        head,
        f"  band_source={src}  nightly_usd={lo}–{hi}  area_hint={ah_disp}",
        "  " + "-" * 72,
    ]
    rows = dbg.get("rows") if isinstance(dbg.get("rows"), list) else []
    if not rows:
        lines.append("  (no scored rows)")
    else:
        lines.append(
            f"  {'i':>3}  {'score':>7}  {'usd':>6}  {'rating':>6}  {'reviews':>7}  {'name':<28}  url"
        )
        lines.append("  " + "-" * 72)
        for r in rows:
            if not isinstance(r, dict):
                continue
            i_raw = r.get("i", "")
            try:
                i_disp = int(i_raw)
            except (TypeError, ValueError):
                i_disp = i_raw
            sc = r.get("score", "")
            usd = r.get("usd")
            usd_s = f"${usd}" if isinstance(usd, int) else "—"
            gr = r.get("guest_rating")
            rating_s = f"{float(gr):.1f}" if isinstance(gr, (int, float)) else "—"
            rc = r.get("review_count")
            rev_s = str(rc) if isinstance(rc, int) else "—"
            nm = str(r.get("name") or "")[:28]
            ur = str(r.get("url") or "—")[:44]
            mark = "  <- picked" if picked_index is not None and i_raw == picked_index else ""
            lines.append(
                f"  {str(i_disp):>3}  {sc!s:>7}  {usd_s:>6}  {rating_s:>6}  {rev_s:>7}  {nm:<28}  {ur}{mark}"
            )
    lines.append("  " + "-" * 72)
    if picked_index is not None:
        lines.append(f"  -> winner: index {picked_index} (highest score; ties -> lower USD then lower index)")
    else:
        lines.append(f"  -> no rule winner: {dbg.get('reason', 'unknown')} (fallback to LLM best_index / advisor)")
    return "\n".join(lines)


def _emit_hotel_rule_pick_log(
    dbg: dict[str, Any],
    *,
    band_source: str,
    area_hint: str,
    picked_index: int | None,
    session_id: str = "",
) -> None:
    block = _format_hotel_rule_pick_block(
        dbg, band_source=band_source, area_hint=area_hint, picked_index=picked_index, session_id=session_id
    )
    path = hotel_rule_pick_log_path()
    if path is not None:
        ts = datetime.now(timezone.utc).isoformat()
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write("\n" + "=" * 72 + f"\n{ts}Z\n")
                f.write(block)
                f.write("\n")
        except OSError as exc:
            if hotel_rule_pick_log_enabled():
                print(f"[hotel_rule_pick] log file write failed: {path} ({exc})", flush=True)
    if hotel_rule_pick_log_enabled():
        safe = block.encode("ascii", errors="replace").decode("ascii")
        print("\n" + safe, flush=True)


def append_hotel_rule_pick_file_note(body: str, *, session_id: str = "") -> None:
    """
    Append a free-form note to the same rule-pick log file (when file logging is on).

    Used when rule scoring cannot run (e.g. missing nightly band) so operators still see a record.
    """
    path = hotel_rule_pick_log_path()
    if path is None:
        return
    ts = datetime.now(timezone.utc).isoformat()
    sid = (session_id or "").strip()[:48]
    head = f"{ts}Z"
    if sid:
        head += f" session_id={sid!s}"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write("\n" + "=" * 72 + "\n" + head + "\n")
            f.write(body if str(body).endswith("\n") else str(body) + "\n")
    except OSError as exc:
        print(f"[hotel_rule_pick] log file write failed: {path} ({exc})", flush=True)


def _hotel_profile_sources_line(profile: dict[str, Any] | None, *, limit: int = 700) -> str:
    if not isinstance(profile, dict) or not profile:
        return "—"
    parts: list[str] = []
    for k, v in list(profile.items())[:14]:
        if isinstance(v, dict):
            parts.append(f"{k}={str(v.get('source') or '?')[:40]}")
        else:
            parts.append(f"{k}=?")
    s = "; ".join(parts)
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"


_FINAL_HOTEL_LOG_EXTRA_MAX = 32_000


def _format_llm_parse_failure_note(hm: dict[str, Any] | None) -> str:
    lines = ["  [why] LLM listing parse did not yield a card (fallback may be heuristic):"]
    if not hm:
        lines.append("    (no meta)")
        return "\n".join(lines)
    err = str(hm.get("llm_err") or "").strip()
    lines.append(f"    llm_err={err[:500]!s}" if err else "    llm_err=(empty)")
    return "\n".join(lines)


def _format_llm_pick_rationale_ok(hm: dict[str, Any]) -> str:
    lines = ["  [why] LLM listing path — how the winning row was chosen:"]
    pm = str(hm.get("pick_method") or "").strip()
    ps = str(hm.get("pick_summary") or "").strip()
    if pm:
        lines.append(f"    pick_method={pm!s}")
    if ps:
        chunk = ps if len(ps) <= 1200 else ps[:1199] + "…"
        lines.append(f"    pick_summary={chunk!s}")
    adj = hm.get("index_adjusted_for_nonempty_price")
    if isinstance(adj, dict):
        lines.append(f"    index_adjusted_for_nonempty_price={adj!s}")
    return "\n".join(lines)


def _format_llm_listings_table(listings: list[Any], *, winner_index: int | None) -> str:
    lines = ["  [candidates] LLM-parsed rows (index = JSON row order):"]
    if not isinstance(listings, list) or not listings:
        lines.append("    (none)")
        return "\n".join(lines)
    wix: int | None = None
    if winner_index is not None:
        try:
            wix = int(winner_index)
        except (TypeError, ValueError):
            wix = None
    for i, row in enumerate(listings[:20]):
        if not isinstance(row, dict):
            continue
        mark = "  <- card" if wix is not None and i == wix else ""
        nm = str(row.get("name") or "")[:54]
        pr = str(row.get("price_display") or "")[:26]
        ar = str(row.get("area_or_neighborhood") or "")[:34]
        ur = _short_url(row.get("listing_url"), 68)
        lines.append(f"    i={i:>2}  price={pr!s:>14}  area={ar!s}  name={nm!s}  url={ur!s}{mark}")
    if len(listings) > 20:
        lines.append(f"    … {len(listings) - 20} additional row(s) omitted from log")
    return "\n".join(lines)


def _format_rule_pick_rows_block(rows: list[Any], *, winner_index: int | None, rule_won: bool) -> str:
    lines = [
        "  [scores] rule-based scores (higher=better among rows with parseable nightly USD; "
        "see pick_hotel_listing_index_rule_based):"
    ]
    if not isinstance(rows, list) or not rows:
        lines.append("    (no score rows — band missing, rule off, or no parseable prices in parsed rows)")
        return "\n".join(lines)
    wix: int | None = None
    if rule_won and winner_index is not None:
        try:
            wix = int(winner_index)
        except (TypeError, ValueError):
            wix = None
    for r in rows[:20]:
        if not isinstance(r, dict):
            continue
        i_raw = r.get("i", "")
        try:
            i_int = int(i_raw)
        except (TypeError, ValueError):
            i_int = None
        mark = ""
        if wix is not None and i_int is not None and i_int == wix:
            mark = "  <- rule winner"
        sc = r.get("score", "")
        usd = r.get("usd")
        usd_s = f"${usd}" if isinstance(usd, int) else "—"
        nm = str(r.get("name") or "")[:42]
        ur = str(r.get("url") or "")[:58]
        lines.append(f"    i={i_raw!s:>3}  score={sc!s:>7}  usd={usd_s:>7}  name={nm!s}  url={ur!s}{mark}")
    if len(rows) > 20:
        lines.append(f"    … {len(rows) - 20} more score row(s) omitted")
    return "\n".join(lines)


def _format_heuristic_candidates_block(diag: dict[str, Any]) -> str:
    lines = ["  [candidates] HTML heuristic (Expedia SRP anchors + optional nightly intent reorder):"]
    ps = str(diag.get("pick_summary") or "").strip()
    if ps:
        lines.append(f"    {ps[:920]!s}")
    for c in (diag.get("ordered_candidates") or [])[:14]:
        if not isinstance(c, dict):
            continue
        lines.append(
            f"    final_rank={c.get('final_rank')!s}  title={str(c.get('title') or '')[:88]!s}  "
            f"href={str(c.get('href') or '')[:92]!s}"
        )
    bp = diag.get("budget_pick") if isinstance(diag.get("budget_pick"), dict) else {}
    brow = bp.get("rows") if isinstance(bp.get("rows"), list) else []
    if brow:
        lines.append("    budget evaluation (original_index before reorder; lower pick_score is better):")
        for r in brow[:12]:
            if not isinstance(r, dict):
                continue
            lines.append(
                f"      orig_i={r.get('original_index')!s}  median_usd_near_href={r.get('median_usd_near_href')!s}  "
                f"pick_score={r.get('pick_score')!s}  title={r.get('title')!s}  href={r.get('href')!s}"
            )
    pts = diag.get("price_tokens_sample") if isinstance(diag.get("price_tokens_sample"), list) else []
    if pts:
        preview = ", ".join(str(x) for x in pts[:14])
        lines.append(f"    price_tokens_sample={preview[:900]!s}")
    return "\n".join(lines)


def _href_likely_same_property(href: str, listing_url: str) -> bool:
    h = (href or "").strip()
    u = (listing_url or "").strip()
    if not h or not u:
        return False
    if h in u:
        return True
    if h.startswith("/") and h in u.replace("https://www.expedia.com", "").replace("https://expedia.com", ""):
        return True
    return False


def _format_page_anchor_consideration_block(
    rows: list[Any],
    *,
    final_listing_url: str,
    llm_listings_count: int | None,
) -> str:
    """
    Log every property-like anchor the scraper saw on the page (pre-heuristic reorder).

    Lets operators compare **HTML scan breadth** vs **LLM ``listings`` length** (first-anchor / under-parse bias).
    """
    if not isinstance(rows, list) or not rows:
        return ""
    lines = [
        "  [considered] All property-like anchors from raw HTML (extract_candidate_links; SRP discovery order):",
        f"    html_anchor_count={len(rows)}  llm_listing_row_count={llm_listings_count!s}  "
        f"(when LLM path: if counts differ, the model under-enumerated or excerpt missed cards)",
    ]
    fu = (final_listing_url or "").strip()
    for r in rows[:24]:
        if not isinstance(r, dict):
            continue
        rk = r.get("rank", "")
        title = str(r.get("title") or "")[:100]
        href = str(r.get("href") or "")[:110]
        mark = "  <- matches returned card URL" if _href_likely_same_property(href, fu) else ""
        lines.append(f"    rank={rk!s}  title={title!s}  href={href!s}{mark}")
    if len(rows) > 24:
        lines.append(f"    … {len(rows) - 24} more anchor(s) omitted from log")
    return "\n".join(lines)


def append_hotel_final_pick_log(
    *,
    selection_path: str,
    ho: dict[str, Any],
    hotel_profile: dict[str, Any] | None = None,
    session_id: str = "",
    dest_id: str = "",
    hotel_index: int | None = None,
    dest_label: str = "",
    h_llm_meta: dict[str, Any] | None = None,
    heuristic_diag: dict[str, Any] | None = None,
    page_anchor_candidates: list[dict[str, Any]] | None = None,
) -> None:
    """
    One summary block for the hotel card actually returned (LLM+rule, LLM+advisor, LLM best-index, or heuristic).

    Appends parsed listing rows / rule scores / heuristic anchors / **raw HTML anchor scan** so operators can
    see **all evaluated candidates**, not only the winner. Uses the same log file as rule-pick diagnostics.
    """
    path = hotel_rule_pick_log_path()
    if path is None:
        return
    ts = datetime.now(timezone.utc).isoformat()
    sid = (session_id or "").strip()[:48]
    head = f"{ts}Z"
    if sid:
        head += f" session_id={sid!s}"
    name = str(ho.get("name") or "")[:100]
    area = str(ho.get("area") or "")[:120]
    price = str(ho.get("price") or "")[:64]
    url = str(ho.get("listing_url") or "")[:220]
    idx_line = ""
    if ho.get("llm_best_index") is not None:
        idx_line = (
            f"  llm_best_index={ho.get('llm_best_index')}  "
            f"listings_parsed={ho.get('listings_parsed')}\n"
        )
    meta_bits = ""
    hm = h_llm_meta if isinstance(h_llm_meta, dict) else None
    llm_n: int | None = None
    if hm and isinstance(hm.get("listings"), list):
        llm_n = len(hm["listings"])
    extra_parts: list[str] = []
    ap = page_anchor_candidates if isinstance(page_anchor_candidates, list) else None
    if ap:
        extra_parts.append(
            _format_page_anchor_consideration_block(
                ap,
                final_listing_url=str(ho.get("listing_url") or ""),
                llm_listings_count=llm_n,
            )
        )
    if hm:
        flags: list[str] = []
        if hm.get("rule_pick"):
            flags.append("rule_pick")
        if hm.get("advisor_rank"):
            flags.append("advisor_rank")
        rps = hm.get("rule_pick_skip")
        if rps:
            flags.append(f"rule_pick_skip={rps}")
        if flags:
            meta_bits = f"  llm_meta_flags: {'; '.join(flags)}\n"
    if hm and not hm.get("llm_ok"):
        extra_parts.append(_format_llm_parse_failure_note(hm))
    elif hm and hm.get("llm_ok"):
        extra_parts.append(_format_llm_pick_rationale_ok(hm))
        lst = hm.get("listings") if isinstance(hm.get("listings"), list) else []
        if not lst and isinstance(ho.get("listings"), list):
            lst = ho.get("listings") or []
        extra_parts.append(_format_llm_listings_table(lst, winner_index=ho.get("llm_best_index")))
        rrows = hm.get("rule_pick_rows") if isinstance(hm.get("rule_pick_rows"), list) else []
        extra_parts.append(
            _format_rule_pick_rows_block(
                rrows,
                winner_index=ho.get("llm_best_index"),
                rule_won=bool(hm.get("rule_pick")),
            )
        )
    hd = heuristic_diag if isinstance(heuristic_diag, dict) else None
    if hd and hd.get("path") == "heuristic_html_snapshot":
        extra_parts.append(_format_heuristic_candidates_block(hd))
    extra = "\n".join(p for p in extra_parts if p).strip()
    if extra:
        if len(extra) > _FINAL_HOTEL_LOG_EXTRA_MAX:
            extra = extra[: _FINAL_HOTEL_LOG_EXTRA_MAX] + "\n  … (candidate detail truncated)\n"
        extra = "\n" + extra + "\n"
    else:
        extra = "\n"
    body = (
        "[hotel_final] card returned to client\n"
        f"  selection_path={selection_path}\n"
        f"  dest_id={dest_id}  hotel_index={hotel_index}  dest_query={str(dest_label)[:120]!s}\n"
        f"  name={name!s}\n"
        f"  area={area!s}\n"
        f"  price={price!s}\n"
        f"  listing_url={url!s}\n"
        f"{idx_line}"
        f"{meta_bits}"
        f"  field_sources={_hotel_profile_sources_line(hotel_profile)}\n"
        f"{extra}"
    )
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write("\n" + "=" * 72 + "\n" + head + "\n")
            f.write(body)
    except OSError as exc:
        print(f"[hotel_final] log file write failed: {path} ({exc})", flush=True)
    if hotel_rule_pick_log_enabled():
        print("\n" + body, flush=True)


def _short_url(u: Any, limit: int = 52) -> str:
    s = (u or "").strip() if isinstance(u, str) else ""
    if not s:
        return "—"
    return (s[:limit] + "…") if len(s) > limit else s


def parse_price_display_usd(price_display: str) -> int | None:
    """
    Best-effort nightly (or headline) USD integer from a listing ``price_display`` string.

    Assumes Expedia-style ``$123`` or ``$1,234`` near the start of the visible price token.
    """
    s = (price_display or "").strip()
    if not s:
        return None
    m = re.search(r"\$\s*([\d,]+)", s)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    if not raw.isdigit():
        return None
    v = int(raw)
    if v <= 0 or v > 500_000:
        return None
    return v


def _extract_usd_ranges(text: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for m in re.finditer(r"\$?\s*(\d[\d,]{1,5})\s*[–—\-]\s*\$?\s*(\d[\d,]{1,5})", text):
        lo = int(m.group(1).replace(",", ""))
        hi = int(m.group(2).replace(",", ""))
        if lo > hi:
            lo, hi = hi, lo
        out.append((lo, hi))
    return out


def _lodging_like_range(pairs: list[tuple[int, int]]) -> tuple[int, int] | None:
    """Keep ranges that look like per-night lodging (not whole-trip totals)."""
    cand = [(lo, hi) for lo, hi in pairs if 20 <= lo <= hi <= 6_000]
    if not cand:
        return None
    # Prefer a modest span; very wide bands still beat "no hint".
    cand.sort(key=lambda p: (p[1] - p[0], p[0]))
    return cand[0]


def nightly_band_from_budget_guidance(bg: dict[str, Any] | None) -> tuple[int, int] | None:
    """
    Parse a ``$low–$high`` style nightly band from itinerary ``budget_guidance`` strings.

    Uses ``lodging_band_short`` and ``lodging_style`` first (where the model is nudged to put
    ``.../n`` copy); ignores other keys in v1 to avoid swallowing whole-trip dollar ranges.
    """
    if not isinstance(bg, dict):
        return None
    for key in ("lodging_band_short", "lodging_style"):
        raw = bg.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        hit = _lodging_like_range(_extract_usd_ranges(raw))
        if hit:
            lo, hi = hit
            if hi - lo < 10:
                hi = lo + 25
            return int(lo), int(hi)
    return None


def _area_overlap_bonus(area_hint: str, row_area: str) -> float:
    ah = (area_hint or "").lower()
    ra = (row_area or "").lower()
    if not ah or not ra:
        return 0.0
    stop = frozenset(
        "the a an of in on at for to and or with from by into near downtown city centre center area".split()
    )
    aw = [w for w in re.split(r"[^\w]+", ah) if len(w) >= 3 and w not in stop]
    if not aw:
        return 0.0
    hits = sum(1 for w in aw if w in ra)
    return min(8.0, float(hits) * 2.5)


def _guest_rating_bonus(row: dict[str, Any]) -> float:
    g = row.get("guest_rating")
    if not isinstance(g, (int, float)) or g <= 0:
        return 0.0
    return min(14.0, max(0.0, (float(g) - 7.5) * 5.0))


def _review_count_bonus(row: dict[str, Any]) -> float:
    rc = row.get("review_count")
    if not isinstance(rc, int) or rc < 1:
        return 0.0
    if rc >= 50:
        return 8.0
    if rc >= 10:
        return 5.0
    if rc >= 3:
        return 2.0
    return 0.5


def _listing_url_bonus(listing_url: str | None) -> float:
    u = (listing_url or "").strip().lower()
    if "expedia.com" in u and u.startswith("http"):
        return 6.0
    if u.startswith("http"):
        return 2.0
    return 0.0


def score_hotel_listing_row(
    row: dict[str, Any],
    *,
    nightly_lo: int,
    nightly_hi: int,
    area_hint: str,
) -> tuple[float, int | None]:
    """
    Higher is better. Second tuple element is parsed USD (or None).
    """
    p = parse_price_display_usd(str(row.get("price_display") or ""))
    mid = 0.5 * (nightly_lo + nightly_hi)
    width = max(1.0, float(nightly_hi - nightly_lo))
    if p is None:
        base = 8.0
    elif p < nightly_lo:
        gap = float(nightly_lo - p)
        base = 55.0 - min(45.0, (gap / max(1.0, float(nightly_lo))) * 50.0)
    elif p > nightly_hi:
        gap = float(p - nightly_hi)
        base = max(5.0, 48.0 - (gap / max(1.0, float(nightly_hi))) * 55.0)
    else:
        dist = abs(float(p) - mid)
        base = 92.0 - min(40.0, (dist / (width * 0.5)) * 38.0)

    base += _area_overlap_bonus(area_hint, str(row.get("area_or_neighborhood") or ""))
    base += _listing_url_bonus(row.get("listing_url") if isinstance(row.get("listing_url"), str) else None)
    base += _guest_rating_bonus(row)
    base += _review_count_bonus(row)
    return float(base), p


def pick_hotel_listing_index_rule_based(
    listings: list[dict[str, Any]],
    *,
    nightly_lo: int,
    nightly_hi: int,
    area_hint: str = "",
    band_source: str = "",
    session_id: str = "",
) -> tuple[int | None, dict[str, Any]]:
    """
    Pick the best row index using **parseable nightly USD** rows only.

    Returns (None, dbg) when no listing has a usable ``price_display`` (caller should fall back
    to LLM ``best_index`` / advisor rank).
    """
    dbg: dict[str, Any] = {"nightly_lo": nightly_lo, "nightly_hi": nightly_hi, "rows": []}
    if nightly_hi < nightly_lo or nightly_lo < 1:
        out = None, {**dbg, "reason": "invalid_band"}
        _emit_hotel_rule_pick_log(
            out[1], band_source=band_source, area_hint=area_hint, picked_index=None, session_id=session_id
        )
        return out
    if not listings:
        out = None, {**dbg, "reason": "empty_listings"}
        _emit_hotel_rule_pick_log(
            out[1], band_source=band_source, area_hint=area_hint, picked_index=None, session_id=session_id
        )
        return out

    pool: list[tuple[int, float, int]] = []
    for i, row in enumerate(listings):
        if not isinstance(row, dict):
            continue
        score, p = score_hotel_listing_row(row, nightly_lo=nightly_lo, nightly_hi=nightly_hi, area_hint=area_hint)
        dbg["rows"].append(
            {
                "i": i,
                "score": round(score, 2),
                "usd": p,
                "guest_rating": row.get("guest_rating"),
                "review_count": row.get("review_count"),
                "name": (row.get("name") or "")[:48],
                "url": _short_url(row.get("listing_url") if isinstance(row.get("listing_url"), str) else None),
            }
        )
        if p is not None:
            pool.append((i, score, p))

    if not pool:
        out = None, {**dbg, "reason": "no_parseable_prices"}
        _emit_hotel_rule_pick_log(
            out[1], band_source=band_source, area_hint=area_hint, picked_index=None, session_id=session_id
        )
        return out

    # Max score; tie → lower nightly USD; tie → lower index (stable).
    best_i, _best_s, _best_p = max(pool, key=lambda t: (t[1], -t[2], -t[0]))
    dbg["picked_index"] = best_i
    _emit_hotel_rule_pick_log(
        dbg, band_source=band_source, area_hint=area_hint, picked_index=best_i, session_id=session_id
    )
    return best_i, dbg
