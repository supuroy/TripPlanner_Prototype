"""Resolve hero image URLs from Wikipedia / Wikimedia Commons (no API keys)."""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from typing import Any

import httpx

# https://meta.wikimedia.org/wiki/User-Agent_policy — include library + contact-style note.
# Override with WANDERLY_WIKIMEDIA_USER_AGENT if Wikipedia returns 403 (corporate policy).
def _wikimedia_headers() -> dict[str, str]:
    custom = (os.environ.get("WANDERLY_WIKIMEDIA_USER_AGENT") or "").strip()
    if custom:
        ua = custom
    else:
        hv = getattr(httpx, "__version__", "unknown")
        ua = (
            "WanderlyTripPlanner/1.0 "
            "(https://www.mediawiki.org/wiki/API:Etiquette; corporate travel prototype) "
            f"httpx/{hv}"
        )
    return {
        "User-Agent": ua,
        "Api-User-Agent": ua,
        "Accept": "application/json",
    }


def _truncate_search(s: str, max_len: int = 240) -> str:
    t = s.strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def _get_json(client: httpx.Client, url: str, params: dict[str, Any]) -> Any | None:
    """GET JSON with light backoff on rate limits (burst hero enrich hits many cards)."""
    for attempt in range(3):
        try:
            r = client.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 503) and attempt < 2:
                ra = r.headers.get("Retry-After")
                try:
                    wait_s = min(12.0, float(ra)) if ra else 2.0 * (attempt + 1)
                except ValueError:
                    wait_s = 2.0 * (attempt + 1)
                time.sleep(wait_s)
                continue
            if r.status_code == 403 and attempt == 0:
                time.sleep(2.0)
                continue
        except httpx.HTTPError:
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None
    return None


@contextlib.contextmanager
def wikimedia_client(timeout: float = 30.0) -> Iterator[httpx.Client]:
    """One client per enrich batch — reuse connections and space requests politely."""
    with httpx.Client(timeout=timeout, headers=_wikimedia_headers()) as c:
        yield c


def wikipedia_best_thumb(name: str, country: str, *, client: httpx.Client | None = None) -> str | None:
    """Use English Wikipedia opensearch + pageimages for a city-style thumbnail."""
    search = _truncate_search(f"{name} {country}".strip() or name.strip())
    if not search:
        return None
    if client is None:
        with httpx.Client(timeout=20.0, headers=_wikimedia_headers()) as h:
            return wikipedia_best_thumb(name, country, client=h)
    try:
        data = _get_json(
            client,
            "https://en.wikipedia.org/w/api.php",
            {
                "action": "opensearch",
                "search": search,
                "limit": "1",
                "namespace": "0",
                "format": "json",
            },
        )
        if not isinstance(data, list) or len(data) < 2:
            return None
        titles = data[1]
        if not isinstance(titles, list) or not titles:
            return None
        title = str(titles[0])
        data2 = _get_json(
            client,
            "https://en.wikipedia.org/w/api.php",
            {
                "action": "query",
                "titles": title,
                "prop": "pageimages",
                "format": "json",
                "pithumbsize": "1600",
            },
        )
        if not isinstance(data2, dict):
            return None
        pages = (data2.get("query") or {}).get("pages") or {}
        for page in pages.values():
            if not isinstance(page, dict):
                continue
            thumb = page.get("thumbnail")
            if isinstance(thumb, dict):
                src = thumb.get("source")
                if isinstance(src, str) and src.startswith("https:"):
                    return src
            orig = page.get("original")
            if isinstance(orig, dict):
                src = orig.get("source")
                if isinstance(src, str) and src.startswith("https:"):
                    return src
    except (ValueError, TypeError):
        return None
    return None


def commons_file_thumbs(query: str, *, limit: int = 12, client: httpx.Client | None = None) -> list[str]:
    """Search Wikimedia Commons (file namespace); return distinct photo URLs in result order."""
    q = _truncate_search(query.strip(), max_len=280)
    if not q or limit <= 0:
        return []
    lim = min(50, max(limit, 6))
    if client is None:
        with httpx.Client(timeout=25.0, headers=_wikimedia_headers()) as h:
            return commons_file_thumbs(query, limit=limit, client=h)
    out: list[str] = []
    seen: set[str] = set()
    try:
        data = _get_json(
            client,
            "https://commons.wikimedia.org/w/api.php",
            {
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": q,
                "gsrnamespace": "6",
                "gsrlimit": str(lim),
                "prop": "imageinfo",
                "iiprop": "url|thumburl",
                "iiurlwidth": "2048",
            },
        )
        if not isinstance(data, dict):
            return []
        pages = (data.get("query") or {}).get("pages") or {}
        for page in pages.values():
            if not isinstance(page, dict):
                continue
            ii = page.get("imageinfo")
            if not isinstance(ii, list) or not ii:
                continue
            info = ii[0]
            if not isinstance(info, dict):
                continue
            # Prefer full file URL over generator thumb for sharper heroes when available.
            cand = info.get("url") or info.get("thumburl")
            if isinstance(cand, str) and cand.startswith("https:") and cand not in seen:
                seen.add(cand)
                out.append(cand)
                if len(out) >= limit:
                    break
    except (ValueError, TypeError):
        return []
    return out


def commons_file_thumb(query: str) -> str | None:
    """First Commons hit (backward compatible)."""
    thumbs = commons_file_thumbs(query, limit=1)
    return thumbs[0] if thumbs else None


def resolve_hero_image_urls(
    name: str,
    country: str,
    *,
    search_hint: str | None = None,
    max_images: int = 3,
    client: httpx.Client | None = None,
) -> list[str]:
    """
    Up to max_images distinct HTTPS URLs: Wikipedia lead image first, then Commons file search.
    Pass a shared ``client`` from ``wikimedia_client()`` when resolving many destinations in one batch.
    """
    cap = max(1, min(int(max_images), 6))
    urls: list[str] = []
    seen: set[str] = set()

    def add(u: str | None) -> None:
        if not u or u in seen:
            return
        seen.add(u)
        urls.append(u)

    add(wikipedia_best_thumb(name, country, client=client))
    hint = (search_hint or "").strip() or f"{name} {country}".strip() or name
    for u in commons_file_thumbs(hint, limit=max(12, cap * 4), client=client):
        add(u)
        if len(urls) >= cap:
            break
    return urls[:cap]


def resolve_hero_image_url(name: str, country: str, *, search_hint: str | None = None) -> str | None:
    """Single primary hero (first of resolve_hero_image_urls)."""
    xs = resolve_hero_image_urls(name, country, search_hint=search_hint, max_images=1, client=None)
    return xs[0] if xs else None
