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
from src.wanderly.cursor_advisor import structured_followup_model_id
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
  (budget-conscious: avoid the most expensive unless clearly better value; prefer reasonable nonstop options for families;
  avoid obvious error fares like $0). If the profile is sparse, prefer lowest credible per-traveler price among nonstops.
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
- Among similar fits, prefer lower total **price_display** and fewer stops.
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
            raw = run_prompt_collect_assistant_text(
                client,
                prompt,
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
    listings_dump = [r.model_dump(exclude_none=True) for r in parsed.listings[:16]]

    if flight_advisor_rank_enabled():
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

    best = parsed.listings[idx]

    def _nz_price(s: str) -> bool:
        d = re.sub(r"[^\d]", "", s or "")
        return bool(d) and int(d) > 0

    price_disp = (best.price_display or "").strip() or "See Expedia"
    if not _nz_price(price_disp):
        for j, row in enumerate(parsed.listings):
            if _nz_price(row.price_display or ""):
                best = row
                idx = j
                price_disp = (row.price_display or "").strip()
                break

    fo: dict[str, Any] = {
        "airline": (best.airline or "—")[:120],
        "route": (best.route or route_hint or "—")[:180],
        "duration": (best.duration or "—")[:40],
        "price": price_disp[:32],
        "best": True,
        "stops": (best.stops or "")[:80],
        "departure_time": (best.departure_time or "")[:24],
        "arrival_time": (best.arrival_time or "")[:24],
        "llm_best_index": idx,
        "llm_best_rationale": (parsed.best_rationale or "")[:400],
        "llm_confidence": float(parsed.confidence or 0.0),
        "listings_parsed": len(parsed.listings),
        "listings": listings_dump[:12],
    }
    if best.airline_logo_url and str(best.airline_logo_url).startswith("http"):
        fo["airline_logo_url"] = str(best.airline_logo_url).strip()[:800]

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
    meta["listings"] = [row.model_dump() for row in parsed.listings[:12]]
    meta["best_index"] = idx
    return fo, profile, meta
