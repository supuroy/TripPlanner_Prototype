"""
Optional hotel SRP parsing: Playwright HTML → Cursor LLM → structured listings + best pick (lab / demo).

Requires ``CURSOR_API_KEY``. Enable with ``BOOKING_SEARCH_HOTEL_LLM_PARSE=1``.

Listing rows are still produced by the LLM; the **default winner** is chosen with deterministic
rule scoring when ``BOOKING_SEARCH_HOTEL_RULE_PICK`` is on (default). Set to ``0`` to use the
parse pass ``best_index`` plus optional ``BOOKING_SEARCH_HOTEL_ADVISOR_RANK`` rerank instead.
"""


from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import BaseModel, Field

from src.integrations.cursor_agent_client import (
    basic_client,
    get_api_key,
    load_env_from_roots,
    run_prompt_collect_assistant_text,
)
from src.wanderly.cursor_advisor import structured_followup_model_id
from src.wanderly.flight_listing_llm import parse_llm_json_object
from src.wanderly.session_store import cursor_run_persist_kwargs_for_lane


def hotel_llm_parse_enabled() -> bool:
    return os.environ.get("BOOKING_SEARCH_HOTEL_LLM_PARSE", "").strip().lower() in ("1", "true", "yes", "on")


def hotel_advisor_rank_enabled() -> bool:
    return os.environ.get("BOOKING_SEARCH_HOTEL_ADVISOR_RANK", "").strip().lower() in ("1", "true", "yes", "on")


def hotel_rule_pick_enabled() -> bool:
    """When on (default), override ``best_index`` / advisor rank with nightly-band rule scoring."""
    raw = (os.environ.get("BOOKING_SEARCH_HOTEL_RULE_PICK") or "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def _max_input_chars() -> int:
    raw = (os.environ.get("BOOKING_SEARCH_HOTEL_LLM_MAX_INPUT_CHARS") or "").strip()
    if raw.isdigit():
        return max(8_000, min(200_000, int(raw)))
    return 90_000


def _merge_intervals(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not windows:
        return []
    windows.sort()
    out: list[tuple[int, int]] = [windows[0]]
    for lo, hi in windows[1:]:
        plo, phi = out[-1]
        if lo <= phi + 200:
            out[-1] = (plo, max(phi, hi))
        else:
            out.append((lo, hi))
    return out


_HOTEL_LISTING_HREF_RE = re.compile(
    r'href="(/[^"]*?\.h\d+\.Hotel-Information[^"]*)"',
    re.I,
)
_HOTEL_PROPERTY_ID_RE = re.compile(r"\.(h\d+)\.Hotel-Information", re.I)


def _hotel_listing_card_windows(html: str, *, max_cards: int = 22) -> list[tuple[int, int]]:
    """
    Build excerpt windows around SRP property cards (``…Hotel-Information`` links).

    Filter sidebars also contain ``$`` prices; card-centric windows avoid ``empty_listings``.
    """
    seen_pid: set[str] = set()
    windows: list[tuple[int, int]] = []
    for m in _HOTEL_LISTING_HREF_RE.finditer(html):
        href = m.group(1)
        pid_m = _HOTEL_PROPERTY_ID_RE.search(href)
        pid = (pid_m.group(1) if pid_m else href).lower()
        if pid in seen_pid:
            continue
        seen_pid.add(pid)
        lo, hi = m.span()
        windows.append((max(0, lo - 14_000), min(len(html), hi + 6_000)))
        if len(windows) >= max_cards:
            break
    return windows


def compact_playwright_hotel_html(html: str, *, max_chars: int | None = None, search_url: str = "") -> str:
    max_chars = max_chars or _max_input_chars()
    from src.wanderly.listing_html_excerpt import _strip_scripts_and_styles, listing_focus_html

    raw = html or ""
    stripped = _strip_scripts_and_styles(raw)
    parts: list[str] = []
    mt = re.search(r"<title>[^<]{1,240}</title>", raw, flags=re.I)
    if mt:
        parts.append(mt.group(0))

    windows = _hotel_listing_card_windows(stripped)
    blob = ""
    if windows:
        merged = _merge_intervals(windows)
        blob = "\n\n<!-- listing-card -->\n\n".join(stripped[a:b] for a, b in merged[:24])
    else:
        focus_cap = min(220_000, max(max_chars * 2, max_chars + 40_000))
        focused, _meth = listing_focus_html(raw, kind="hotel", search_url=search_url, max_chars=focus_cap)
        anchor = focused if len(focused) >= 4_000 else stripped
        for needle in ("Hotel-Information", "lodging-card", "property-listing", "Hotel-Search"):
            ix = anchor.lower().find(needle.lower())
            if ix >= 0:
                anchor = anchor[max(0, ix - 5_000) :]
                break
        fallback_windows: list[tuple[int, int]] = []
        for m in re.finditer(r"Hotel-Information[^\"'\\s]{10,220}", anchor, re.I):
            lo, hi = m.span()
            fallback_windows.append((max(0, lo - 6_000), min(len(anchor), hi + 6_000)))
        merged = _merge_intervals(fallback_windows)
        blob = (
            "\n\n<!-- window -->\n\n".join(anchor[a:b] for a, b in merged[:40])
            if merged
            else anchor[: min(len(anchor), max_chars - 500)]
        )

    body = "\n\n".join(parts) + "\n\n" + blob
    if len(body) > max_chars:
        body = body[:max_chars] + "\n<!-- truncated -->"
    return body


class HotelListingRow(BaseModel):
    name: str = ""
    price_display: str = Field(
        default="",
        description=(
            "The **per-night** rate shown for this property on the SRP card (e.g. \"$447 nightly\", \"$447/night\"). "
            "Must NOT be the trip total (e.g. \"$2,215 total\", \"total with taxes and fees\"), a crossed-out/rack rate, "
            "or a generic page-wide price. If the card only shows a stay total and no nightly line, leave this empty "
            "and put the total text in price_notes."
        ),
    )
    price_notes: str | None = Field(
        default=None,
        description=(
            "Optional: stay **total** with taxes, crossed-out comparison prices, \"$X off\", scarcity lines "
            "(\"We have 1 left\"), resort fees, etc. Do not fold totals into price_display."
        ),
    )
    area_or_neighborhood: str = ""
    listing_url: str | None = None
    image_url: str | None = None
    guest_rating: float | None = Field(
        default=None,
        description="Guest review score shown on the card, 0–10 (e.g. 9.2). Not star class.",
    )
    review_count: int | None = Field(
        default=None,
        description="Number of guest reviews shown on the card (e.g. 62).",
    )
    rating_label: str | None = Field(
        default=None,
        description="Qualitative label if shown (e.g. Wonderful, Very good).",
    )
    star_rating: str | None = Field(
        default=None,
        description="Optional combined display string if the card only exposes a single text blob.",
    )


class HotelListingLLMResponse(BaseModel):
    listings: list[HotelListingRow] = Field(default_factory=list)
    best_index: int = Field(0, ge=0)
    best_rationale: str = ""
    confidence: float = 0.0


class HotelRankResponse(BaseModel):
    best_index: int = Field(0, ge=0)
    rationale: str = ""


def _coerce_hotel_row_price_display_nightly(row: dict[str, Any]) -> dict[str, Any]:
    """
    If the model put a **stay total** (or taxes-total) string into ``price_display``, move it to ``price_notes``
    so nightly rule scoring does not treat a trip total as per-night.
    """
    pd = str(row.get("price_display") or "").strip()
    if not pd:
        return row
    low = pd.lower()
    mislabeled = bool(
        re.search(r"\btotal\b", low)
        or "taxes and fees" in low
        or "taxes & fees" in low
        or re.search(r"\bstay\s+total\b", low)
        or re.search(r"\bfor\s+the\s+stay\b", low)
    )
    if not mislabeled:
        return row
    pn0 = str(row.get("price_notes") or "").strip()
    merged = "; ".join(x for x in (pd, pn0) if x)
    return {**row, "price_display": "", "price_notes": merged or None}


def _hotel_parse_prompt(
    *,
    search_url: str,
    dest_label: str,
    area_hint: str,
    nights: int,
    profile_json: str,
    criteria_lines: str,
    excerpt: str,
) -> str:
    schema = HotelListingLLMResponse.model_json_schema()
    return f"""You parse **hotel search results** HTML for a lab demo (not booking). Use only facts visible in the excerpt.

Search URL (reference):
{search_url}

Destination query: {dest_label!s}
Stay area hint: {area_hint!s}
Nights: {nights}

Traveler profile (JSON):
{profile_json}

Trip criteria (human-readable lines):
{criteria_lines}

--- Playwright HTML excerpt ---
{excerpt}
--- End excerpt ---

Task:
- Build `listings` from visible hotel result rows (name, **per-night** price, neighborhood, listing_url as https link if present, hero/thumbnail image URL if present).
- For each row include **guest_rating** (numeric 0–10, e.g. 9.2 from "9.2 out of 10" or badge text), **review_count** (integer, e.g. 62 from "62 reviews"), and **rating_label** (e.g. Wonderful) when visible on that card. Do not confuse star **class** (3-star hotel) with guest score.

**Price rules (critical — Expedia-style cards often show several dollar amounts):**
- `price_display` must be the **headline per-night** rate for the stay (look for labels like **\"nightly\"**, **\"/ night\"**, **\"per night\"**). Example: if you see both \"$447 nightly\" and \"$2,215 total\", use **$447** (or \"$447 nightly\") in `price_display` and put the total line in `price_notes`.
- Do **not** put trip **totals** (\"$X total\", \"total with taxes and fees\"), **discount deltas** (\"$207 off\"), or **struck-through / was** rack prices into `price_display` unless that is the only visible nightly figure and you are certain it is per-night.
- If multiple nightly-looking numbers appear, prefer the one **paired with \"nightly\" / \"per night\"** near the property name for that card, not a larger number from a bundle or package elsewhere on the row.
- If you truly cannot find a per-night figure but a **stay total** is explicit, leave `price_display` empty (or \"See Expedia\") and copy the total wording into `price_notes` so downstream logic does not treat a total as nightly.
- `best_index`: provisional row index for ordering only — the server may override with **rule-based scoring** when nightly budget hints exist (still set a sensible default). Prefer rows whose `price_display` reflects a credible **nightly** rate when choosing `best_index`.
- `best_rationale` and `confidence` as usual.
- Do not invent URLs or prices not hinted in the excerpt.

Return **only** one JSON object matching this JSON Schema (no markdown fences, no prose):
{json.dumps(schema, ensure_ascii=True)}
"""


def _hotel_rank_prompt(
    *,
    listings_json: str,
    profile_json: str,
    criteria_lines: str,
    dest_label: str,
    nights: int,
) -> str:
    return f"""You choose the **single best hotel listing** for this traveler from a pre-parsed JSON array (0-based indices).

Destination / search: {dest_label!s}
Nights: {nights}

Traveler profile (JSON):
{profile_json}

Trip criteria:
{criteria_lines}

Listings (JSON array):
{listings_json}

Rules:
- Respect budget / vibe / who from the profile when inferring tradeoffs (e.g. avoid obvious ultra-lux if budget is tight).
- If trip criteria mention a **per-night dollar band** for hotels, favor listings whose **price_display** reflects a **per-night** rate that fits that band — do not treat trip totals as nightly when comparing.
- Prefer listings with a real ``listing_url`` on expedia.com when tied on fit.
- Return ONE JSON object: {{"best_index": <int>, "rationale": "<one short sentence>"}}
No markdown fences, no other keys.
"""


def advisor_rank_hotel_best_index(
    listings: list[dict[str, Any]],
    *,
    profile_pills: list[dict[str, Any]] | None,
    criteria_lines: list[str] | None,
    dest_label: str,
    nights: int,
    session_id: str | None,
) -> tuple[int | None, str]:
    mid = structured_followup_model_id()
    if not mid or not listings:
        return None, ""
    load_env_from_roots()
    try:
        get_api_key()
    except ValueError:
        return None, ""
    prompt = _hotel_rank_prompt(
        listings_json=json.dumps(listings[:16], ensure_ascii=True)[:48_000],
        profile_json=json.dumps(profile_pills or [], ensure_ascii=True)[:12_000],
        criteria_lines="\n".join(criteria_lines or [])[:6000],
        dest_label=dest_label,
        nights=nights,
    )
    sid = (session_id or "").strip() or "wanderly-anon"
    try:
        with basic_client(get_api_key(), timeout_s=120.0) as client:
            raw = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=mid,
                **cursor_run_persist_kwargs_for_lane(
                    sid, "hotel_listing_rank", trace_path="hotel_listing_rank.json"
                ),
            )
    except Exception:
        return None, ""
    obj = parse_llm_json_object(raw)
    if not obj:
        return None, ""
    try:
        pr = HotelRankResponse.model_validate(obj)
    except Exception:
        return None, ""
    if pr.best_index < 0 or pr.best_index >= len(listings):
        return None, ""
    return pr.best_index, (pr.rationale or "").strip()[:500]


def run_hotel_listing_llm_parse(
    html: str,
    *,
    search_url: str,
    dest_label: str,
    area_hint: str,
    nights: int,
    profile_pills: list[dict[str, Any]] | None = None,
    criteria_lines: list[str] | None = None,
    session_id: str | None = None,
    image_fallback: str = "",
    scoring_nightly_usd: tuple[int, int] | None = None,
    scoring_band_source: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    meta: dict[str, Any] = {"path": "hotel_listing_llm", "excerpt_chars": 0, "llm_err": ""}
    load_env_from_roots()
    try:
        get_api_key()
    except ValueError as exc:
        meta["llm_err"] = str(exc)
        return None, None, meta

    excerpt = compact_playwright_hotel_html(html, search_url=search_url)
    meta["excerpt_chars"] = len(excerpt)
    profile_json = json.dumps(profile_pills or [], ensure_ascii=True)[:14_000]
    crit = "\n".join(criteria_lines or [])[:8000]

    prompt = _hotel_parse_prompt(
        search_url=search_url,
        dest_label=dest_label,
        area_hint=area_hint,
        nights=nights,
        profile_json=profile_json,
        criteria_lines=crit,
        excerpt=excerpt,
    )
    model = (os.environ.get("CURSOR_LIGHT_MODEL_FOR_HOTEL_LIST") or "").strip() or None
    sid = (session_id or "").strip() or "wanderly-anon"
    raw = ""
    try:
        with basic_client(get_api_key(), timeout_s=180.0) as client:
            raw = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=model,
                **cursor_run_persist_kwargs_for_lane(
                    sid, "hotel_listing_llm", trace_path="hotel_listing_llm.json"
                ),
            )
    except Exception as exc:
        meta["llm_err"] = f"{type(exc).__name__}:{exc!s}"[:400]
        return None, None, meta

    meta["llm_chars"] = len(raw or "")
    obj = parse_llm_json_object(raw)
    if not obj:
        meta["llm_err"] = "json_parse_failed"
        meta["llm_diag"] = {
            "dest_label": (dest_label or "")[:140],
            "search_url": (search_url or "")[:320],
            "area_hint": (area_hint or "")[:100],
            "nights": int(nights),
            "excerpt_chars": int(meta.get("excerpt_chars") or 0),
            "llm_response_chars": int(meta.get("llm_chars") or 0),
            "note": "Model output was not valid JSON object with listings.",
        }
        return None, None, meta
    try:
        parsed = HotelListingLLMResponse.model_validate(obj)
    except Exception as exc:
        meta["llm_err"] = f"validate:{type(exc).__name__}:{exc!s}"[:400]
        meta["llm_diag"] = {
            "dest_label": (dest_label or "")[:140],
            "search_url": (search_url or "")[:320],
            "area_hint": (area_hint or "")[:100],
            "nights": int(nights),
            "excerpt_chars": int(meta.get("excerpt_chars") or 0),
            "llm_response_chars": int(meta.get("llm_chars") or 0),
            "note": "JSON parsed but failed HotelListingLLMResponse validation.",
        }
        return None, None, meta
    if not parsed.listings:
        meta["llm_err"] = "empty_listings"
        meta["llm_diag"] = {
            "dest_label": (dest_label or "")[:140],
            "search_url": (search_url or "")[:320],
            "area_hint": (area_hint or "")[:100],
            "nights": int(nights),
            "excerpt_chars": int(meta.get("excerpt_chars") or 0),
            "llm_response_chars": int(meta.get("llm_chars") or 0),
            "best_index_in_payload": int(getattr(parsed, "best_index", 0) or 0),
            "confidence_in_payload": float(getattr(parsed, "confidence", 0.0) or 0.0),
            "note": "Model returned listings=[] — HTML excerpt may lack visible hotel cards, or model skipped rows.",
        }
        return None, None, meta

    idx = parsed.best_index if parsed.best_index < len(parsed.listings) else 0
    from src.wanderly.hotel_listing_extract import enrich_hotel_listing_row_from_html

    listings_dump = []
    for r in parsed.listings[:16]:
        row = _coerce_hotel_row_price_display_nightly(r.model_dump(exclude_none=True))
        listings_dump.append(enrich_hotel_listing_row_from_html(row, html))
    meta["rule_pick_rows"] = []
    meta["scoring_band_source"] = (scoring_band_source or "").strip()[:80]
    sn0 = scoring_nightly_usd
    if sn0 is not None and len(sn0) == 2 and isinstance(sn0[0], int) and isinstance(sn0[1], int):
        meta["scoring_nightly_lo_hi"] = [int(sn0[0]), int(sn0[1])]

    rule_used = False
    sn = scoring_nightly_usd
    band_ok = (
        sn is not None
        and len(sn) == 2
        and isinstance(sn[0], int)
        and isinstance(sn[1], int)
        and sn[1] >= sn[0] > 0
    )
    if hotel_rule_pick_enabled() and band_ok:
        from src.wanderly.hotel_listing_score import pick_hotel_listing_index_rule_based

        r_idx, r_dbg = pick_hotel_listing_index_rule_based(
            listings_dump,
            nightly_lo=int(sn[0]),
            nightly_hi=int(sn[1]),
            area_hint=area_hint or "",
            band_source=(scoring_band_source or "").strip()[:80],
            session_id=(session_id or "").strip()[:80],
        )
        if r_idx is not None:
            idx = int(r_idx)
            rule_used = True
            meta["rule_pick"] = True
            meta["rule_pick_rows"] = list((r_dbg or {}).get("rows") or [])
            meta["rule_pick_dbg"] = {
                "picked_index": r_dbg.get("picked_index"),
                "nightly_lo": r_dbg.get("nightly_lo"),
                "nightly_hi": r_dbg.get("nightly_hi"),
                "top_scores": (r_dbg.get("rows") or [])[:8],
            }
        else:
            meta["rule_pick"] = False
            meta["rule_pick_skip"] = str((r_dbg or {}).get("reason") or "unknown")
            meta["rule_pick_rows"] = list((r_dbg or {}).get("rows") or [])

    elif hotel_rule_pick_enabled() and not band_ok:
        from src.wanderly.hotel_listing_score import append_hotel_rule_pick_file_note

        append_hotel_rule_pick_file_note(
            "[hotel_rule_pick] skipped — no valid nightly USD band (scoring_nightly_usd).\n"
            f"  scoring_nightly_usd={scoring_nightly_usd!r}  band_source={meta.get('scoring_band_source')!r}\n"
            f"  listings_parsed={len(listings_dump)}  dest_label={dest_label!s}\n"
            "  Fix: set traveler Budget pill (trip total), add destination budget_guidance.lodging_band_short "
            "with a $low–$high/n style range, or ensure hotel search intent includes price_min_usd/price_max_usd.\n"
            "  Also confirm BOOKING_SEARCH_HOTEL_LLM_PARSE=1 or the listing parser never runs (no rule-pick log).",
            session_id=sid,
        )
        meta["rule_pick"] = False
        meta["rule_pick_skip"] = "no_nightly_band"

    if (not rule_used) and hotel_advisor_rank_enabled():
        r_idx, r_rat = advisor_rank_hotel_best_index(
            listings_dump,
            profile_pills=profile_pills,
            criteria_lines=criteria_lines,
            dest_label=dest_label,
            nights=nights,
            session_id=session_id,
        )
        if r_idx is not None:
            idx = r_idx
            meta["advisor_rank"] = True
            meta["advisor_rationale"] = r_rat
            parsed.best_rationale = r_rat or parsed.best_rationale

    idx_main = idx
    best = parsed.listings[idx]

    def _nz_price(s: str) -> bool:
        d = re.sub(r"[^\d]", "", s or "")
        return bool(d) and int(d) > 0

    price_disp = (best.price_display or "").strip() or "See Expedia"
    if (not rule_used) and (not _nz_price(price_disp)):
        for j, row in enumerate(parsed.listings):
            if _nz_price(row.price_display or ""):
                best = row
                idx = j
                price_disp = (row.price_display or "").strip()
                break
    if idx != idx_main:
        meta["index_adjusted_for_nonempty_price"] = {"from": idx_main, "to": idx}

    if rule_used:
        meta["pick_method"] = "rule_pick"
        meta["pick_summary"] = (
            f"rule_pick: final_listing_index={idx} among {len(listings_dump)} parsed rows; "
            f"nightly_band={meta.get('scoring_nightly_lo_hi')!r}; band_source={meta.get('scoring_band_source')!r}."
        )
    elif meta.get("advisor_rank"):
        ar = (meta.get("advisor_rationale") or "").strip()
        meta["pick_method"] = "advisor_rank"
        meta["pick_summary"] = (
            f"advisor_rank: final_listing_index={idx} among {len(listings_dump)} rows. "
            f"Rationale: {ar[:520]!s}"
        )
    else:
        br = (parsed.best_rationale or "").strip()
        meta["pick_method"] = "llm_best_index"
        meta["pick_summary"] = (
            f"llm_best_index path: final_listing_index={idx} among {len(listings_dump)} rows "
            f"(rule_pick_skip={meta.get('rule_pick_skip')!r}). Model rationale: {br[:520]!s}"
        )
    adj_d = meta.get("index_adjusted_for_nonempty_price")
    if isinstance(adj_d, dict) and adj_d.get("from") is not None and adj_d.get("to") is not None:
        if int(adj_d["from"]) != int(adj_d["to"]):
            meta["pick_summary"] = (
                (meta.get("pick_summary") or "")
                + f" Post-pick: index {adj_d['from']}→{adj_d['to']} to use the first row with a numeric dollar price."
            )

    ho: dict[str, Any] = {
        "name": (best.name or "Lodging")[:120],
        "area": (best.area_or_neighborhood or area_hint)[:160],
        "price": price_disp[:40] if price_disp.startswith("$") else f"{price_disp}/night est."[:48],
        "nights": nights,
        "best": True,
        "image": (best.image_url or "").strip()[:800],
        "listing_url": (best.listing_url or "").strip()[:800],
        "listings": listings_dump[:12],
        "listings_parsed": len(parsed.listings),
        "llm_best_index": idx,
        "llm_best_rationale": (parsed.best_rationale or "")[:400],
        "llm_confidence": float(parsed.confidence or 0.0),
    }
    if best.guest_rating is not None:
        ho["guest_rating"] = float(best.guest_rating)
    if best.review_count is not None:
        ho["review_count"] = int(best.review_count)
    if best.rating_label:
        ho["rating_label"] = str(best.rating_label).strip()[:40]
    if best.star_rating:
        ho["star_rating"] = str(best.star_rating).strip()[:40]
    elif best.guest_rating is not None:
        bits = [str(best.guest_rating)]
        if best.rating_label:
            bits.append(f"({best.rating_label})")
        if best.review_count is not None:
            bits.append(f"{best.review_count} reviews")
        ho["star_rating"] = " ".join(bits)[:80]

    img_src = "cursor_llm_hotel_listings"
    if not (best.image_url or "").strip() and (image_fallback or "").strip():
        ho["image"] = image_fallback.strip()[:800]
        img_src = "itinerary_card_fallback"
    elif not (ho.get("image") or "").strip():
        img_src = "none"

    hprof: dict[str, Any] = {
        "name": {"source": "cursor_llm_hotel_listings"},
        "area": {"source": "cursor_llm_hotel_listings"},
        "price": {"source": "cursor_llm_hotel_listings"},
        "image": {"source": img_src},
        "listing_url": {"source": "cursor_llm_hotel_listings"},
    }

    meta["llm_ok"] = True
    meta["listings"] = listings_dump
    meta["best_index"] = idx

    try:
        from src.wanderly.hotel_listing_alternatives import (
            attach_ranked_picks_to_hotel_option,
            build_llm_ranked_picks,
            ordered_listing_indices_from_rule_rows,
        )

        rule_rows = meta.get("rule_pick_rows") if isinstance(meta.get("rule_pick_rows"), list) else []
        if rule_rows:
            pick_ix = ordered_listing_indices_from_rule_rows(rule_rows, winner_index=idx)
        else:
            pick_ix = [idx] + [j for j in range(len(listings_dump)) if j != idx]
        ranked = build_llm_ranked_picks(
            listings_dump,
            pick_ix,
            nights=nights,
            area_hint=area_hint or "",
            image_fallback=image_fallback,
            rule_rows=rule_rows,
        )
        if ranked:
            ho = attach_ranked_picks_to_hotel_option(ho, ranked, selected_rank=1)
            meta["ranked_picks_count"] = len(ranked)
    except ImportError:
        pass

    return ho, hprof, meta
