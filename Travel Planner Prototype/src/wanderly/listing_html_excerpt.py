"""Focus Expedia SRP HTML down to listing-relevant regions before LLM parse (smaller + faster)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

PageKind = Literal["flight", "hotel"]


def booking_search_trace_listing_focus_enabled() -> bool:
    """Mirror main booking trace file (JSON lines + ``ts``) for HTML focus timing."""
    return os.environ.get("BOOKING_SEARCH_TRACE_LISTING_FOCUS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _trace_listing_focus_booking(
    *,
    kind: PageKind,
    search_url: str,
    focus_method: str,
    raw_len: int,
    focus_len: int,
    focus_elapsed_ms: int,
) -> None:
    if not booking_search_trace_listing_focus_enabled():
        return
    try:
        from src.wanderly.ota_booking_search import booking_search_trace
    except ImportError:
        return
    booking_search_trace(
        "listing_html_focus",
        kind=kind,
        focus_method=focus_method,
        raw_len=raw_len,
        focus_len=focus_len,
        focus_elapsed_ms=focus_elapsed_ms,
        search_url_head=(search_url or "")[:200],
    )


def listing_html_debug_enabled() -> bool:
    return os.environ.get("BOOKING_SEARCH_LISTING_HTML_DEBUG", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _debug_log_path() -> Path:
    raw = (os.environ.get("BOOKING_SEARCH_LISTING_HTML_DEBUG_LOG") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = Path(__file__).resolve().parents[1] / raw
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    d = Path(__file__).resolve().parents[1] / ".cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / "booking_listing_html_excerpt_debug.log"


def log_listing_excerpt_debug(
    *,
    kind: PageKind,
    search_url: str,
    raw_len: int,
    stripped_len: int,
    focus_len: int,
    excerpt_len: int,
    focus_method: str,
) -> None:
    if not listing_html_debug_enabled():
        return
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "search_url_head": (search_url or "")[:240],
        "raw_len": raw_len,
        "stripped_len": stripped_len,
        "focus_len": focus_len,
        "excerpt_len": excerpt_len,
        "focus_method": focus_method,
        "url_fp16": hashlib.sha256((search_url or "").encode()).hexdigest()[:16],
    }
    try:
        with _debug_log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _strip_scripts_and_styles(html: str) -> str:
    """Remove script/style blocks to shrink DOM before region extraction."""
    s = html or ""
    s = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", s, flags=re.I)
    s = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", "", s, flags=re.I)
    s = re.sub(r"<noscript\b[^>]*>[\s\S]*?</noscript>", "", s, flags=re.I)
    return s


def _extract_main_region(html: str, *, max_chars: int) -> tuple[str, str]:
    """Return ``(slice, method)`` for the first ``<main>...</main>`` block."""
    m = re.search(r"<main\b[^>]*>[\s\S]*?</main>", html, flags=re.I)
    if not m:
        return "", "no_main"
    chunk = m.group(0)
    if len(chunk) > max_chars:
        return chunk[:max_chars], "main_tag_truncated"
    return chunk, "main_tag"


def _extract_shopping_anchor(html: str, kind: PageKind, *, max_chars: int) -> tuple[str, str]:
    needles: list[str] = []
    if kind == "flight":
        needles = [
            'id="app-flights-shopping-pwa"',
            "app-flights-shopping-pwa",
            "flights-shopping-pwa",
            "flight-results",
        ]
    else:
        needles = [
            "lodging-search",
            "shopping-pwa",
            "Hotel-Search",
            "property-listing",
            "resultsList",
        ]
    low = html.lower()
    for nd in needles:
        ix = low.find(nd.lower())
        if ix >= 0:
            chunk = html[max(0, ix - 2_000) : ix + max_chars]
            return chunk[:max_chars], f"needle:{nd[:40]}"
    return html[:max_chars], "head_fallback"


def listing_focus_html(
    html: str,
    *,
    kind: PageKind,
    search_url: str = "",
    max_chars: int = 140_000,
) -> tuple[str, str]:
    """
    Return ``(focused_html, method)`` — smaller input for downstream windowing / LLM excerpt.

    Order: strip scripts → ``<main>`` → kind-specific anchor → head fallback.
    """
    t0 = time.perf_counter()
    raw_len = len(html or "")
    stripped = _strip_scripts_and_styles(html or "")
    stripped_len = len(stripped)
    main_chunk, mth = _extract_main_region(stripped, max_chars=max_chars)
    if main_chunk and len(main_chunk) >= 8_000:
        elapsed = int((time.perf_counter() - t0) * 1000)
        log_listing_excerpt_debug(
            kind=kind,
            search_url=search_url,
            raw_len=raw_len,
            stripped_len=stripped_len,
            focus_len=len(main_chunk),
            excerpt_len=len(main_chunk),
            focus_method=mth,
        )
        _trace_listing_focus_booking(
            kind=kind,
            search_url=search_url,
            focus_method=mth,
            raw_len=raw_len,
            focus_len=len(main_chunk),
            focus_elapsed_ms=elapsed,
        )
        return main_chunk, mth
    anchor_chunk, ath = _extract_shopping_anchor(stripped, kind, max_chars=max_chars)
    method_out = ath if main_chunk == "" else f"{mth}+{ath}"
    elapsed = int((time.perf_counter() - t0) * 1000)
    log_listing_excerpt_debug(
        kind=kind,
        search_url=search_url,
        raw_len=raw_len,
        stripped_len=stripped_len,
        focus_len=len(anchor_chunk),
        excerpt_len=len(anchor_chunk),
        focus_method=method_out,
    )
    _trace_listing_focus_booking(
        kind=kind,
        search_url=search_url,
        focus_method=method_out,
        raw_len=raw_len,
        focus_len=len(anchor_chunk),
        focus_elapsed_ms=elapsed,
    )
    return anchor_chunk, method_out
