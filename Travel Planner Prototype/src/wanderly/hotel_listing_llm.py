"""
Optional hotel SRP parsing: Playwright HTML → Cursor LLM → structured listings + best pick (lab / demo).

Requires ``CURSOR_API_KEY``. Enable with ``BOOKING_SEARCH_HOTEL_LLM_PARSE=1``.
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


def compact_playwright_hotel_html(html: str, *, max_chars: int | None = None) -> str:
    max_chars = max_chars or _max_input_chars()
    parts: list[str] = []
    mt = re.search(r"<title>[^<]{1,240}</title>", html, flags=re.I)
    if mt:
        parts.append(mt.group(0))
    anchor = html
    for needle in ("Hotel-Search", "Hotel-Information", "lodging", "property"):
        ix = anchor.lower().find(needle.lower())
        if ix >= 0:
            anchor = anchor[max(0, ix - 5_000) :]
            break
    windows: list[tuple[int, int]] = []
    for m in re.finditer(r"\$\s*[1-9][\d,]{1,5}\b", anchor):
        lo, hi = m.span()
        windows.append((max(0, lo - 8000), min(len(anchor), hi + 8000)))
    for m in re.finditer(r"Hotel-Information[^\"'\\s]{10,220}", anchor, re.I):
        lo, hi = m.span()
        windows.append((max(0, lo - 4000), min(len(anchor), hi + 4000)))
    merged = _merge_intervals(windows)
    if merged:
        blob = "\n\n<!-- window -->\n\n".join(anchor[a:b] for a, b in merged[:50])
    else:
        blob = anchor[: min(len(anchor), max_chars - 500)]
    body = "\n\n".join(parts) + "\n\n" + blob
    if len(body) > max_chars:
        body = body[:max_chars] + "\n<!-- truncated -->"
    return body


class HotelListingRow(BaseModel):
    name: str = ""
    price_display: str = ""
    price_notes: str | None = None
    area_or_neighborhood: str = ""
    listing_url: str | None = None
    image_url: str | None = None
    star_rating: str | None = None


class HotelListingLLMResponse(BaseModel):
    listings: list[HotelListingRow] = Field(default_factory=list)
    best_index: int = Field(0, ge=0)
    best_rationale: str = ""
    confidence: float = 0.0


class HotelRankResponse(BaseModel):
    best_index: int = Field(0, ge=0)
    rationale: str = ""


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
- Build `listings` from visible hotel result rows (name, price/night if shown, neighborhood, listing_url as https link if present, hero/thumbnail image URL if present, star/review hints if present).
- `best_index`: provisional best **value** (not yet fully personalized — a second pass may adjust).
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
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    meta: dict[str, Any] = {"path": "hotel_listing_llm", "excerpt_chars": 0, "llm_err": ""}
    load_env_from_roots()
    try:
        get_api_key()
    except ValueError as exc:
        meta["llm_err"] = str(exc)
        return None, None, meta

    excerpt = compact_playwright_hotel_html(html)
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
        return None, None, meta
    try:
        parsed = HotelListingLLMResponse.model_validate(obj)
    except Exception as exc:
        meta["llm_err"] = f"validate:{type(exc).__name__}:{exc!s}"[:400]
        return None, None, meta
    if not parsed.listings:
        meta["llm_err"] = "empty_listings"
        return None, None, meta

    idx = parsed.best_index if parsed.best_index < len(parsed.listings) else 0
    listings_dump = [r.model_dump(exclude_none=True) for r in parsed.listings[:16]]

    if hotel_advisor_rank_enabled():
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
    if best.star_rating:
        ho["star_rating"] = str(best.star_rating).strip()[:40]

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
    return ho, hprof, meta
