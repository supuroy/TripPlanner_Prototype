"""Post-process destination cards with hero images: stock APIs (default) or Wikimedia fallback."""

from __future__ import annotations

import json
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.integrations.cursor_agent_client import (
    basic_client,
    get_api_key,
    load_env_from_roots,
    run_prompt_collect_assistant_text,
)
from src.wanderly.session_store import cursor_run_persist_kwargs
from src.wanderly.stock_photo_heroes import (
    _normalize_photo_url,
    build_stock_search_query_from_dest,
    fetch_pexels_photo_urls,
    fetch_unsplash_photo_urls,
    infer_hero_park_phrase,
    log_hero_image,
)
from src.wanderly.wikimedia_heroes import resolve_hero_image_urls, wikimedia_client

_HINT_PROMPT = """
You help pick VERY SHORT English stock-photo search phrases (Unsplash works best with 2–5 plain words).
For hikes inside a national park, include the park name (e.g. "Yellowstone lake" not just "Shoshone Lake loop").
Return ONLY a JSON array (no markdown). Each element must be:
{"id":"<exact id from input>","image_search":"2-5 words, landmark or place type, no commas or long lists"}

Input JSON array:
""".strip()


def _extract_json_array(text: str) -> list[dict[str, Any]] | None:
    t = text.strip()
    try:
        out = json.loads(t)
        return out if isinstance(out, list) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\[[\s\S]*\]", t)
    if m:
        try:
            out = json.loads(m.group(0))
            return out if isinstance(out, list) else None
        except json.JSONDecodeError:
            return None
    return None


def _llm_search_hints(destinations: list[dict[str, Any]], session_id: str | None = None) -> dict[str, str]:
    load_env_from_roots()
    key = get_api_key()
    slim = [
        {
            "id": d.get("id"),
            "name": d.get("name"),
            "country": d.get("country"),
            "hook": d.get("hook"),
        }
        for d in destinations
    ]
    log_hero_image(f"TIMELINE llm_hints_begin cards={len(destinations)} ids={[str(x.get('id')) for x in slim]}")
    t0 = time.perf_counter()
    prompt = _HINT_PROMPT + "\n" + json.dumps(slim, ensure_ascii=True)
    light = (os.environ.get("CURSOR_LIGHT_MODEL_FOR_IMAGES") or "").strip() or None
    with basic_client(key) as client:
        raw = run_prompt_collect_assistant_text(
            client,
            prompt,
            model_id_override=light,
            persistent_instruction_prefix=_HINT_PROMPT + "\n",
            **cursor_run_persist_kwargs(session_id or "", light, trace_path="hero_enrichment.llm_hints"),
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    log_hero_image(f"TIMELINE llm_hints_end cards={len(destinations)} elapsed_ms={elapsed_ms:.1f}")
    rows = _extract_json_array(raw) or []
    hints: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        did = str(row.get("id") or "").strip()
        q = str(row.get("image_search") or "").strip()
        if did and q:
            words = q.split()[:6]
            hints[did] = " ".join(words)[:100]
    if hints:
        log_hero_image(f"LLM image_search hints for ids: {list(hints.keys())}")
    return hints


def _unsplash_access_key() -> str:
    return (os.environ.get("UNSPLASH_ACCESS_KEY") or os.environ.get("UNSPLASH_CLIENT_ID") or "").strip()


def _pexels_api_key() -> str:
    return (os.environ.get("PEXELS_API_KEY") or "").strip()


def _hero_image_provider() -> str:
    """unsplash | pexels | wikimedia — with sane fallbacks if keys missing."""
    raw = (os.environ.get("WANDERLY_HERO_IMAGE_SOURCE") or "auto").strip().lower()
    u_key, p_key = _unsplash_access_key(), _pexels_api_key()
    if raw == "wikimedia":
        return "wikimedia"
    if raw == "unsplash":
        return "unsplash" if u_key else ("pexels" if p_key else "wikimedia")
    if raw == "pexels":
        return "pexels" if p_key else ("unsplash" if u_key else "wikimedia")
    # auto: prefer Unsplash, then Pexels, else Wikimedia
    if u_key:
        return "unsplash"
    if p_key:
        return "pexels"
    return "wikimedia"


def _hero_fallback_urls(d: dict[str, Any]) -> list[str]:
    """No Apify / per-leg AllTrails hero fallback (removed)."""
    return []


def _stock_worker_idx(
    idx: int,
    d: dict[str, Any],
    hint: str | None,
    gallery_max: int,
    provider: str,
) -> tuple[int, list[str]]:
    """Returns (card_index, urls) so merge does not rely on destination id uniqueness."""
    did = str(d.get("id") or "").strip()
    name = str(d.get("name") or "")
    country = str(d.get("country") or "")
    park = infer_hero_park_phrase(
        destination_id=did,
        name=name,
        hook=str(d.get("hook") or ""),
    )
    q = build_stock_search_query_from_dest(d, hint)
    log_hero_image(
        f"stock start idx={idx} id={did!r} name={name!r} country={country!r} "
        f"park={park!r} query={q!r}",
    )
    try:
        if provider == "unsplash":
            urls = fetch_unsplash_photo_urls(
                q,
                max_images=gallery_max,
                access_key=_unsplash_access_key(),
                name=name,
                country=country,
                park_phrase=park,
            )
        else:
            urls = fetch_pexels_photo_urls(
                q,
                max_images=gallery_max,
                api_key=_pexels_api_key(),
                name=name,
                country=country,
                park_phrase=park,
            )
    except Exception:
        log_hero_image(f"stock exception idx={idx} id={did!r}\n{traceback.format_exc()}")
        return idx, []
    log_hero_image(f"stock done idx={idx} id={did!r} url_count={len(urls)}")
    return idx, urls


def _enrich_with_stock_parallel(
    destinations: list[dict[str, Any]],
    hints: dict[str, str],
    gallery_max: int,
    provider: str,
) -> list[dict[str, Any]]:
    workers = int(os.environ.get("HERO_IMAGE_FETCH_CONCURRENCY", "5") or "5")
    workers = max(1, min(workers, 8, len(destinations)))
    results_by_idx: dict[int, list[str]] = {}
    log_hero_image(
        f"enrich stock_parallel provider={provider} cards={len(destinations)} workers={workers} gallery_max={gallery_max}",
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = []
        for i, d in enumerate(destinations):
            did = str(d.get("id") or "").strip()
            h = hints.get(did) if did else None
            futs.append(pool.submit(_stock_worker_idx, i, d, h, gallery_max, provider))
        for fut in as_completed(futs):
            try:
                idx, urls = fut.result()
                results_by_idx[idx] = urls
            except Exception:
                log_hero_image(f"stock future error\n{traceback.format_exc()}")
                continue
    fallback_wiki = os.environ.get("HERO_IMAGE_STOCK_FALLBACK_WIKIMEDIA", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    gap = float(os.environ.get("HERO_IMAGE_REQUEST_GAP_S", "0.55"))

    if not fallback_wiki:
        out: list[dict[str, Any]] = []
        for i, d in enumerate(destinations):
            row = dict(d)
            did = str(row.get("id") or "").strip()
            urls = list(results_by_idx.get(i, []))
            if not urls:
                urls = _hero_fallback_urls(row)
                if urls:
                    log_hero_image(
                        f"merge idx={i} id={did!r} name={row.get('name')!r} — hero from trail/AllTrails fallback",
                    )
            if not urls:
                log_hero_image(
                    f"merge idx={i} id={did!r} name={row.get('name')!r} — no hero URLs (placeholder unchanged)",
                )
            if urls:
                row["image"] = urls[0]
                row["images"] = urls
            out.append(row)
        return out

    out = []
    with wikimedia_client() as http:
        for i, d in enumerate(destinations):
            row = dict(d)
            did = str(row.get("id") or "").strip()
            name = str(row.get("name") or "")
            country = str(row.get("country") or "")
            urls = list(results_by_idx.get(i, []))
            if not urls:
                urls = _hero_fallback_urls(row)
                if urls:
                    log_hero_image(f"merge idx={i} id={did!r} — hero from trail/AllTrails before Wikimedia")
            if not urls:
                hint = hints.get(did) if did else None
                urls = resolve_hero_image_urls(
                    name,
                    country,
                    search_hint=hint,
                    max_images=gallery_max,
                    client=http,
                )
                time.sleep(gap)
            if urls:
                row["image"] = urls[0]
                row["images"] = urls
            out.append(row)
    return out


def _enrich_with_wikimedia_sequential(
    destinations: list[dict[str, Any]],
    hints: dict[str, str],
    gallery_max: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    gap = float(os.environ.get("HERO_IMAGE_REQUEST_GAP_S", "0.9"))
    with wikimedia_client() as http:
        for i, d in enumerate(destinations):
            row = dict(d)
            did = str(row.get("id") or "").strip()
            name = str(row.get("name") or "")
            country = str(row.get("country") or "")
            hint = hints.get(did) if did else None
            log_hero_image(f"wikimedia idx={i} id={did!r} name={name!r}")
            urls = resolve_hero_image_urls(
                name,
                country,
                search_hint=hint,
                max_images=gallery_max,
                client=http,
            )
            if urls:
                row["image"] = urls[0]
                row["images"] = urls
            else:
                log_hero_image(f"wikimedia idx={i} id={did!r} — zero URLs")
            out.append(row)
            time.sleep(gap)
    return out


def enrich_destinations_hero_images(
    destinations: list[dict[str, Any]],
    *,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fill ``image`` + ``images[]`` (up to ``HERO_GALLERY_MAX``, default 3).

    **Default path** (``WANDERLY_HERO_IMAGE_SOURCE=auto``): Unsplash if ``UNSPLASH_ACCESS_KEY`` is set,
    else Pexels if ``PEXELS_API_KEY`` is set, else Wikimedia only.

    Stock APIs run **in parallel** (``HERO_IMAGE_FETCH_CONCURRENCY``). TripAdvisor and similar sites are
    not used: they have no suitable public API and scraping is brittle and typically disallowed by ToS.
    """
    load_env_from_roots()
    if not destinations:
        return []
    t_run = time.perf_counter()
    log_hero_image(
        f"TIMELINE enrich_pipeline_begin count={len(destinations)} "
        f"ids={[str(d.get('id')) for d in destinations]}"
    )
    log_hero_image(f"enrich_destinations_hero_images start count={len(destinations)}")
    try:
        gallery_max = int(os.environ.get("HERO_GALLERY_MAX", "3") or "3")
    except ValueError:
        gallery_max = 3
    gallery_max = max(1, min(gallery_max, 6))
    hints: dict[str, str] = {}
    use_llm = os.environ.get("CURSOR_HERO_IMAGE_LLM", "1").strip().lower() not in ("0", "false", "no")
    if use_llm:
        try:
            hints = _llm_search_hints(destinations, session_id=session_id)
        except Exception:
            hints = {}

    provider = _hero_image_provider()
    if provider in ("unsplash", "pexels"):
        log_hero_image(f"TIMELINE stock_parallel_begin provider={provider}")
        t0 = time.perf_counter()
        out = _enrich_with_stock_parallel(destinations, hints, gallery_max, provider)
        log_hero_image(
            f"TIMELINE stock_parallel_end provider={provider} elapsed_ms={(time.perf_counter() - t0) * 1000:.1f}"
        )
    else:
        log_hero_image("TIMELINE wikimedia_sequential_begin")
        t0 = time.perf_counter()
        out = _enrich_with_wikimedia_sequential(destinations, hints, gallery_max)
        log_hero_image(
            f"TIMELINE wikimedia_sequential_end elapsed_ms={(time.perf_counter() - t0) * 1000:.1f}"
        )
    log_hero_image(
        f"TIMELINE enrich_pipeline_end count={len(out)} total_elapsed_ms={(time.perf_counter() - t_run) * 1000:.1f}"
    )
    return out
