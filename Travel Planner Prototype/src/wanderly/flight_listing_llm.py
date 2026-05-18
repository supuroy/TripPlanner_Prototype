"""
Optional flight SRP parsing: Playwright HTML → Cursor LLM → structured listings + best pick (lab / demo).

Uses **Playwright HTML only** (no Tavily — avoids metered extract API). Requires ``CURSOR_API_KEY``.
Enable with ``BOOKING_SEARCH_FLIGHT_LLM_PARSE=1``.

Optional second pass: ``BOOKING_SEARCH_FLIGHT_ADVISOR_RANK=1`` reranks listings with
``CURSOR_REFINEMENT_MODEL_ID`` / ``CURSOR_ADVISOR_MODEL_ID`` via ``structured_followup_model_id()``.
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
from src.wanderly.booking_search_emit import BookingEmitFn, booking_llm_collect
from src.wanderly.cursor_advisor import structured_followup_model_id
from src.wanderly.flight_listing_score import flight_rule_pick_enabled
from src.wanderly.session_store import cursor_run_persist_kwargs_for_lane


def flight_llm_parse_enabled() -> bool:
    return os.environ.get("BOOKING_SEARCH_FLIGHT_LLM_PARSE", "").strip().lower() in ("1", "true", "yes", "on")


def flight_advisor_rank_enabled() -> bool:
    return os.environ.get("BOOKING_SEARCH_FLIGHT_ADVISOR_RANK", "").strip().lower() in ("1", "true", "yes", "on")


def _llm_max_input_chars() -> int:
    raw = (os.environ.get("BOOKING_SEARCH_FLIGHT_LLM_MAX_INPUT_CHARS") or "").strip()
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


def compact_playwright_flight_html(html: str, *, max_chars: int | None = None, search_url: str = "") -> str:
    """
    Shrink a huge one-line PWA document: keep ``<title>`` plus windows around dollar fares / listing prose.
    """
    max_chars = max_chars or _llm_max_input_chars()
    from src.wanderly.listing_html_excerpt import listing_focus_html

    focus_cap = min(220_000, max(max_chars * 2, max_chars + 40_000))
    focused, _meth = listing_focus_html(html or "", kind="flight", search_url=search_url, max_chars=focus_cap)
    base_html = focused if len(focused) >= 4_000 else (html or "")
    parts: list[str] = []
    mt = re.search(r"<title>[^<]{1,240}</title>", html, flags=re.I)
    if mt:
        parts.append(mt.group(0))
    idx = base_html.find('id="app-flights-shopping-pwa"')
    if idx < 0:
        idx = base_html.find("app-flights-shopping-pwa")
    anchor = base_html[idx:] if idx >= 0 else base_html
    windows: list[tuple[int, int]] = []
    for m in re.finditer(r"\$\s*[1-9][\d,]{2,4}\b", anchor):
        lo, hi = m.span()
        windows.append((max(0, lo - 7000), min(len(anchor), hi + 7000)))
    merged = _merge_intervals(windows)
    if merged:
        blob = "\n\n<!-- window -->\n\n".join(anchor[a:b] for a, b in merged[:60])
    else:
        blob = anchor[: min(len(anchor), max_chars - 500)]
    body = "\n\n".join(parts) + "\n\n" + blob
    if len(body) > max_chars:
        body = body[:max_chars] + "\n<!-- truncated -->"
    return body


class FlightListingRow(BaseModel):
    airline: str = ""
    airline_logo_url: str | None = None
    departure_time: str = ""
    arrival_time: str = ""
    route: str = ""
    duration: str = ""
    stops: str = ""
    price_display: str = ""
    price_notes: str | None = None


class FlightListingLLMResponse(BaseModel):
    listings: list[FlightListingRow] = Field(default_factory=list)
    best_index: int = Field(0, ge=0)
    best_rationale: str = ""
    confidence: float = 0.0


class FlightRankResponse(BaseModel):
    best_index: int = Field(0, ge=0)
    rationale: str = ""


def parse_llm_json_object(text: str) -> dict[str, Any] | None:
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I).strip()
        if "```" in t:
            t = t[: t.rfind("```")].strip()
    i = t.find("{")
    if i < 0:
        return None
    dec = json.JSONDecoder()
    try:
        obj, _ = dec.raw_decode(t[i:])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _fallback_listing_from_context(route_hint: str, excerpt: str) -> FlightListingRow:
    """
    Minimal non-empty listing when SRP excerpt lacks fare cards.
    Keeps UI stable without pretending we parsed real prices.
    """
    title = ""
    mt = re.search(r"<title>([^<]{1,240})</title>", excerpt, flags=re.I)
    if mt:
        title = mt.group(1).strip()
    route = (route_hint or "").strip()
    m3 = re.search(r"\b([A-Z]{3})\s+to\s+([A-Z]{3})\s+flights\b", title)
    if m3:
        route = f"{m3.group(1)} → {m3.group(2)}"
    elif not route and title:
        route = title[:160]
    return FlightListingRow(
        airline="Sample fare (Expedia)",
        departure_time="",
        arrival_time="",
        route=route or "Route unavailable",
        duration="—",
        stops="",
        price_display="See Expedia",
        price_notes="No complete fare rows in this snapshot.",
    )


def _flight_parse_prompt(
    *,
    search_url: str,
    route_hint: str,
    to_label: str | None,
    profile_json: str,
    criteria_lines: str,
    playwright_excerpt: str,
) -> str:
    schema = FlightListingLLMResponse.model_json_schema()
    dest = (to_label or "").strip() or "the searched destination"
    return f"""You are parsing flight **search results** for a lab demo (not booking). The search is **outbound one-way**
for catalog alignment (return leg is not in this HTML).

Search URL (reference, do not invent fares not present in excerpt):
{search_url}

Route hint from our app: {route_hint!s}
Destination focus: {dest!s}

Traveler profile (JSON — sidebar pills: budget, who, when, vibe, …):
{profile_json}

Trip / search criteria (human-readable lines from our planner):
{criteria_lines}

--- Playwright HTML excerpt (primary and only source) ---
{playwright_excerpt}
--- End excerpt ---

Task:
- Build `listings` ONLY from text/URLs in the excerpt (no invented airlines, times, or prices).
- Each listing: airline, optional airline_logo_url if an http(s) URL appears in the excerpt, departure_time,
  arrival_time, route (city/airport line), duration, stops (e.g. Nonstop / 1 stop), price_display, optional price_notes.
- Set `best_index` to the 0-based index of the listing that best **matches the traveler profile and criteria**
  (budget-conscious: avoid the most expensive unless clearly better value; prefer **fewer stops** and **shorter duration**
  when prices are similar; prefer reasonable nonstop options for families; avoid obvious error fares like $0).
  If the profile is sparse, prefer lowest credible per-traveler price among nonstops with the shortest duration you can read.
- `best_rationale`: one short sentence tying the choice to profile/criteria when possible.
- `confidence`: 0.0–1.0
- IMPORTANT fallback when no complete fare rows are visible in this snapshot:
  - still return exactly ONE listing
  - airline: "Sample fare (Expedia)"
  - route: infer from page title like "SEA to SUG flights" or route_hint
  - duration: "—"
  - price_display: "See Expedia"
  - price_notes: "No complete fare rows in this snapshot."
  - set best_index=0 and low confidence (e.g. 0.15)

Return **only** one JSON object matching this JSON Schema (no markdown fences, no prose):
{json.dumps(schema, ensure_ascii=True)}
"""


def _flight_rank_prompt(
    *,
    listings_json: str,
    profile_json: str,
    criteria_lines: str,
) -> str:
    return f"""You choose the **single best flight listing** for this traveler from a JSON array (0-based indices).

Traveler profile (JSON):
{profile_json}

Trip criteria:
{criteria_lines}

Listings (JSON array):
{listings_json}

Rules:
- Optimize for fit to **budget**, **who**, **vibe**, and **schedule hints** in the profile/criteria (not price alone).
- Among similar fits, prefer lower **price_display**, **fewer stops**, and **shorter duration**.
- Return ONE JSON object: {{"best_index": <int>, "rationale": "<one short sentence>"}}
No markdown fences, no other keys.
"""


def advisor_rank_flight_best_index(
    listings: list[dict[str, Any]],
    *,
    profile_pills: list[dict[str, Any]] | None,
    criteria_lines: list[str] | None,
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
    prompt = _flight_rank_prompt(
        listings_json=json.dumps(listings[:16], ensure_ascii=True)[:48_000],
        profile_json=json.dumps(profile_pills or [], ensure_ascii=True)[:12_000],
        criteria_lines="\n".join(criteria_lines or [])[:6000],
    )
    sid = (session_id or "").strip() or "wanderly-anon"
    try:
        with basic_client(get_api_key(), timeout_s=120.0) as client:
            raw = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=mid,
                **cursor_run_persist_kwargs_for_lane(
                    sid, "flight_listing_rank", trace_path="flight_listing_rank.json"
                ),
            )
    except Exception:
        return None, ""
    obj = parse_llm_json_object(raw)
    if not obj:
        return None, ""
    try:
        pr = FlightRankResponse.model_validate(obj)
    except Exception:
        return None, ""
    if pr.best_index < 0 or pr.best_index >= len(listings):
        return None, ""
    return pr.best_index, (pr.rationale or "").strip()[:500]


def run_flight_listing_llm_parse(
    html: str,
    *,
    search_url: str,
    route_hint: str,
    to_label: str | None = None,
    profile_pills: list[dict[str, Any]] | None = None,
    criteria_lines: list[str] | None = None,
    session_id: str | None = None,
    scoring_price_usd: tuple[int, int] | None = None,
    scoring_band_source: str = "",
    emit: BookingEmitFn | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    """
    Returns ``(flight_option, profile, meta)`` where ``flight_option`` is None on failure (caller falls back).
    """
    meta: dict[str, Any] = {
        "path": "flight_listing_llm",
        "excerpt_chars": 0,
        "llm_err": "",
    }
    load_env_from_roots()
    try:
        get_api_key()
    except ValueError as exc:
        meta["llm_err"] = str(exc)
        return None, None, meta

    pw_excerpt = compact_playwright_flight_html(html, search_url=search_url)
    meta["excerpt_chars"] = len(pw_excerpt)
    profile_json = json.dumps(profile_pills or [], ensure_ascii=True)[:14_000]
    crit = "\n".join(criteria_lines or [])[:8000]

    prompt = _flight_parse_prompt(
        search_url=search_url,
        route_hint=route_hint,
        to_label=to_label,
        profile_json=profile_json,
        criteria_lines=crit,
        playwright_excerpt=pw_excerpt,
    )
    model = (os.environ.get("CURSOR_LIGHT_MODEL_FOR_FLIGHT_LIST") or "").strip() or None
    sid = (session_id or "").strip() or "wanderly-anon"
    raw = ""
    try:
        with basic_client(get_api_key(), timeout_s=180.0) as client:
            raw = booking_llm_collect(
                client,
                prompt,
                emit=emit,
                phase_start="Parsing flight listings from Expedia…",
                model_id_override=model,
                **cursor_run_persist_kwargs_for_lane(
                    sid, "flight_listing_llm", trace_path="flight_listing_llm.json"
                ),
            )
    except Exception as exc:
        meta["llm_err"] = f"{type(exc).__name__}:{exc!s}"[:400]
        return None, None, meta

    meta["llm_chars"] = len(raw or "")
    obj = parse_llm_json_object(raw)
    if not obj:
        meta["llm_err"] = "json_parse_failed"
        return None, None, meta
    try:
        parsed = FlightListingLLMResponse.model_validate(obj)
    except Exception as exc:
        meta["llm_err"] = f"validate:{type(exc).__name__}:{exc!s}"[:400]
        return None, None, meta
    if not parsed.listings:
        parsed.listings = [_fallback_listing_from_context(route_hint, pw_excerpt)]
        parsed.best_index = 0
        if not parsed.best_rationale:
            parsed.best_rationale = "Snapshot lacked complete fare rows; showing a placeholder route."
        if not parsed.confidence:
            parsed.confidence = 0.15
        meta["llm_soft_fallback"] = "empty_listings"

    idx = parsed.best_index if parsed.best_index < len(parsed.listings) else 0
    from src.wanderly.flight_listing_extract import enrich_flight_listings_from_html

    listings_dump = enrich_flight_listings_from_html(
        [r.model_dump(exclude_none=True) for r in parsed.listings[:16]],
        html,
    )
    meta["rule_pick_rows"] = []
    meta["scoring_band_source"] = (scoring_band_source or "").strip()[:80]
    sp = scoring_price_usd
    if sp is not None and len(sp) == 2 and isinstance(sp[0], int) and isinstance(sp[1], int):
        meta["scoring_price_lo_hi"] = [int(sp[0]), int(sp[1])]

    rule_used = False
    band_ok = sp is not None and len(sp) == 2 and sp[1] >= sp[0] > 0
    if flight_rule_pick_enabled() and listings_dump:
        from src.wanderly.flight_listing_score import pick_flight_listing_index

        r_idx, r_dbg = pick_flight_listing_index(
            listings_dump,
            price_lo=int(sp[0]) if band_ok else None,
            price_hi=int(sp[1]) if band_ok else None,
            route_hint=route_hint,
        )
        meta["rule_pick_rows"] = list((r_dbg or {}).get("rows") or [])
        if r_idx is not None:
            idx = int(r_idx)
            rule_used = True
            meta["rule_pick"] = True
            meta["rule_pick_dbg"] = {
                "picked_index": r_idx,
                "pick_mode": r_dbg.get("pick_mode"),
                "price_lo": r_dbg.get("price_lo"),
                "price_hi": r_dbg.get("price_hi"),
            }
        else:
            meta["rule_pick"] = False
            meta["rule_pick_skip"] = str((r_dbg or {}).get("reason") or "unknown")

    if (not rule_used) and flight_advisor_rank_enabled():
        r_idx, r_rat = advisor_rank_flight_best_index(
            listings_dump,
            profile_pills=profile_pills,
            criteria_lines=criteria_lines,
            session_id=session_id,
        )
        if r_idx is not None:
            idx = r_idx
            meta["advisor_rank"] = True
            meta["advisor_rationale"] = r_rat
            parsed.best_rationale = r_rat or parsed.best_rationale

    best_row = listings_dump[idx] if idx < len(listings_dump) else parsed.listings[idx].model_dump(exclude_none=True)
    best = parsed.listings[idx] if idx < len(parsed.listings) else parsed.listings[0]

    def _nz_price(s: str) -> bool:
        d = re.sub(r"[^\d]", "", s or "")
        return bool(d) and int(d) > 0

    price_disp = str(best_row.get("price_display") or best.price_display or "").strip() or "See Expedia"
    if (not rule_used) and (not _nz_price(price_disp)):
        for j, row in enumerate(listings_dump):
            if _nz_price(str(row.get("price_display") or "")):
                best_row = row
                idx = j
                price_disp = str(row.get("price_display") or "").strip()
                break

    if rule_used:
        meta["pick_method"] = "rule_pick"
        meta["pick_summary"] = (
            f"rule_pick: index={idx} among {len(listings_dump)} rows; "
            f"band={meta.get('scoring_price_lo_hi')!r}; mode={meta.get('rule_pick_dbg', {}).get('pick_mode')!r}."
        )

    fo: dict[str, Any] = {
        "airline": str(best_row.get("airline") or best.airline or "—")[:120],
        "route": str(best_row.get("route") or best.route or route_hint or "—")[:180],
        "duration": str(best_row.get("duration") or best.duration or "—")[:40],
        "price": price_disp[:32],
        "best": True,
        "stops": str(best_row.get("stops") or best.stops or "")[:80],
        "departure_time": str(best_row.get("departure_time") or best.departure_time or "")[:24],
        "arrival_time": str(best_row.get("arrival_time") or best.arrival_time or "")[:24],
        "llm_best_index": idx,
        "llm_best_rationale": (parsed.best_rationale or "")[:400],
        "llm_confidence": float(parsed.confidence or 0.0),
        "listings_parsed": len(parsed.listings),
        "listings": listings_dump[:12],
    }
    logo = str(best_row.get("airline_logo_url") or getattr(best, "airline_logo_url", "") or "")
    if logo.startswith("http"):
        fo["airline_logo_url"] = logo.strip()[:800]

    profile: dict[str, Any] = {
        "airline": {"source": "cursor_llm_flight_listings"},
        "route": {"source": "cursor_llm_flight_listings"},
        "duration": {"source": "cursor_llm_flight_listings"},
        "price": {"source": "cursor_llm_flight_listings"},
        "stops": {"source": "cursor_llm_flight_listings"},
        "times": {"source": "cursor_llm_flight_listings"},
    }
    if "airline_logo_url" in fo:
        profile["airline_logo_url"] = {"source": "cursor_llm_flight_listings"}

    meta["llm_ok"] = True
    meta["listings"] = listings_dump[:12]
    meta["best_index"] = idx

    try:
        from src.wanderly.flight_listing_alternatives import (
            attach_ranked_picks_to_flight_option,
            build_llm_ranked_flight_picks,
            ordered_listing_indices_from_rule_rows,
        )

        rule_rows = meta.get("rule_pick_rows") if isinstance(meta.get("rule_pick_rows"), list) else []
        if rule_rows:
            pick_ix = ordered_listing_indices_from_rule_rows(rule_rows, winner_index=idx)
        else:
            pick_ix = [idx] + [j for j in range(len(listings_dump)) if j != idx]
        ranked = build_llm_ranked_flight_picks(listings_dump, pick_ix, rule_rows=rule_rows)
        if ranked:
            fo = attach_ranked_picks_to_flight_option(fo, ranked, selected_rank=1)
            meta["ranked_picks_count"] = len(ranked)
    except ImportError:
        pass

    return fo, profile, meta
