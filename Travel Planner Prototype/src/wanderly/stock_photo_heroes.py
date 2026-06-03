"""Licensed stock photo APIs for destination hero images (faster, higher quality than Wikimedia-only)."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
_LOG_FILE = _LOG_DIR / "hero_images.log"


def log_hero_image(msg: str) -> None:
    """Console + append ``Travel Planner Prototype/.cache/hero_images.log`` (UTC, millisecond timestamps)."""
    v = (os.environ.get("WANDERLY_HERO_IMAGE_LOG") or "1").strip().lower()
    if v in ("0", "false", "no", "off"):
        return
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond // 1000:03d}Z"
    line = f"{ts} [hero_images] {msg}"
    print(line, flush=True)
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{line}\n")
    except OSError:
        pass


def _normalize_photo_url(u: str) -> str | None:
    u = u.strip()
    if not u:
        return None
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("http://"):
        u = "https://" + u.removeprefix("http://")
    return u if u.startswith("https:") else None


def _clip_query(q: str, max_len: int = 100) -> str:
    t = q.strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def _name_for_stock_search(name: str) -> str:
    """Drop parenthetical / alternate names so stock search matches (e.g. 'Oahu (North Shore' → 'Oahu')."""
    n = (name or "").strip()
    if not n:
        return ""
    return re.split(r"\s*[(\[/|]", n, maxsplit=1)[0].strip()


def _first_n_words(s: str, n: int) -> str:
    parts = s.split()
    return " ".join(parts[:n]) if parts else ""


def _blob(*parts: str | None) -> str:
    return " ".join(p for p in parts if p).lower()


def _dedupe_query_list(queries: list[str]) -> list[str]:
    out: list[str] = []
    seen_set: set[str] = set()
    for q in queries:
        t = _clip_query(q.strip(), 80)
        if not t:
            continue
        k = t.lower()
        if k in seen_set:
            continue
        seen_set.add(k)
        out.append(t)
    return out


def _strip_trail_noise(name: str) -> str:
    """Drop hike-card words that confuse stock search (loop, wilderness, parentheticals)."""
    n = _name_for_stock_search(name) or (name or "").strip()
    n = re.sub(r"\s*[(\[/|].*$", "", n).strip()
    n = re.sub(
        r"\b(loop|loops|trail|hiking|backpacking|wilderness|corner|half)\b",
        "",
        n,
        flags=re.I,
    )
    return " ".join(n.split()).strip()


def infer_hero_park_phrase(
    *,
    destination_id: str | None = None,
    name: str | None = None,
    hook: str | None = None,
) -> str | None:
    """
    Parent park for in-park hike cards (``yellowstone-shoshone-lake`` → Yellowstone NP).
    """
    blob = _blob(destination_id, name, hook)
    did = (destination_id or "").strip().lower()
    if "yellowstone" in blob or did.startswith("yellowstone"):
        return "Yellowstone National Park"
    if "yosemite" in blob or did.startswith("yosemite"):
        return "Yosemite National Park"
    if "zion" in blob or did.startswith("zion"):
        return "Zion National Park"
    if "grand canyon" in blob:
        return "Grand Canyon National Park"
    if "glacier" in blob and "national" in blob:
        return "Glacier National Park"
    if "rocky mountain" in blob or did.startswith("rocky-mountain"):
        return "Rocky Mountain National Park"
    if "olympic" in blob and "national" in blob:
        return "Olympic National Park"
    if "west coast trail" in blob or "wct" in blob:
        return "West Coast Trail"
    return None


def build_stock_search_query(
    name: str,
    country: str,
    search_hint: str | None,
    *,
    park_phrase: str | None = None,
) -> str:
    """Primary stock search string (short; fallbacks added in fetch_*)."""
    if (search_hint or "").strip():
        base = _first_n_words(search_hint.strip(), 6)
    else:
        nm = _strip_trail_noise(name) or (name or "").strip()
        park = (park_phrase or "").strip()
        if park and nm:
            base = f"{park} {nm}"
        elif park:
            base = park
        else:
            base = f"{nm} {country.strip()}".strip() if nm else country.strip()
    return _clip_query(base, 80)


def build_stock_search_query_from_dest(
    dest: dict[str, Any],
    search_hint: str | None,
) -> str:
    """Build primary query using card id/hook park context (hike legs inside a national park)."""
    name = str(dest.get("name") or "")
    country = str(dest.get("country") or "")
    park = infer_hero_park_phrase(
        destination_id=str(dest.get("id") or ""),
        name=name,
        hook=str(dest.get("hook") or ""),
    )
    if not park:
        g = dest.get("grounding")
        if isinstance(g, dict):
            pc = str(g.get("park_code") or "").strip().lower()
            if pc:
                try:
                    from src.wanderly.park_hero_catalog import manifest_row_for_park_code

                    row = manifest_row_for_park_code(pc)
                    if row:
                        park = str(row.get("park_name") or "").strip() or park
                except Exception:
                    pass
    return build_stock_search_query(name, country, search_hint, park_phrase=park)


def _unsplash_query_candidates(
    primary: str,
    name: str,
    country: str,
    *,
    park_phrase: str | None = None,
) -> list[str]:
    """
    Unsplash Search often returns total=0 for long multi-keyword queries; try simpler strings.
    """
    nm = _strip_trail_noise(name) or (name or "").strip()
    cc = (country or "").strip()
    park = (park_phrase or "").strip()
    parts: list[str] = []
    if park:
        if nm:
            parts.extend(
                [
                    f"{park} {nm}",
                    f"{nm} {park}",
                    f"{park} hiking",
                    f"{park} lake",
                    f"{park} landscape",
                ]
            )
        parts.append(park)
    parts.extend(
        [
            primary,
            _first_n_words(primary, 5),
            _first_n_words(primary, 3),
            f"{nm} {cc}".strip() if nm and cc else "",
            f"{nm} travel".strip() if nm else "",
            nm,
            f"{nm} national park".strip() if nm else "",
            f"{nm} hiking".strip() if nm else "",
            f"{nm} island".strip() if nm else "",
            f"{nm} beach".strip() if nm else "",
        ]
    )
    return _dedupe_query_list([p for p in parts if p])


def _unsplash_fetch_once(query: str, *, max_images: int, access_key: str) -> tuple[list[str], int]:
    """One Unsplash search request. Returns (urls, total_reported_by_api)."""
    key = (access_key or "").strip()
    if not key or not query.strip():
        return [], -1
    per_page = min(30, max(int(max_images), 5))
    headers = {"Authorization": f"Client-ID {key}", "Accept": "application/json"}
    cf = (os.environ.get("UNSPLASH_CONTENT_FILTER") or "low").strip().lower()
    if cf not in ("low", "high"):
        cf = "low"
    params: dict[str, str | int] = {
        "query": query,
        "per_page": per_page,
        "content_filter": cf,
    }
    ori = (os.environ.get("UNSPLASH_ORIENTATION") or "").strip().lower()
    if ori in ("landscape", "portrait", "squarish"):
        params["orientation"] = ori
    data: dict | None = None
    last_status = 0
    try:
        with httpx.Client(timeout=22.0, headers=headers) as c:
            for attempt in range(3):
                r = c.get("https://api.unsplash.com/search/photos", params=params)
                last_status = r.status_code
                if r.status_code == 200:
                    data = r.json()
                    break
                if r.status_code == 429 and attempt < 2:
                    wait = 1.5 * (attempt + 1)
                    log_hero_image(f"Unsplash 429, retry in {wait:.1f}s query={query!r}")
                    time.sleep(wait)
                    continue
                err_snip = (r.text or "")[:240].replace("\n", " ")
                log_hero_image(f"Unsplash HTTP {r.status_code} query={query!r} body_snip={err_snip!r}")
                return [], -1
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        log_hero_image(f"Unsplash request error query={query!r}: {exc!r}")
        return [], -1
    if not isinstance(data, dict):
        log_hero_image(f"Unsplash bad JSON (status was {last_status}) query={query!r}")
        return [], -1
    total_raw = data.get("total")
    total = int(total_raw) if isinstance(total_raw, int) else 0
    results = data.get("results")
    if not isinstance(results, list):
        log_hero_image(f"Unsplash no results list total={total_raw!r} query={query!r} keys={list(data.keys())}")
        return [], total
    out: list[str] = []
    for item in results:
        if len(out) >= max_images:
            break
        if not isinstance(item, dict):
            continue
        urls = item.get("urls")
        if not isinstance(urls, dict):
            continue
        u = urls.get("full") or urls.get("regular") or urls.get("raw") or urls.get("small")
        nu = _normalize_photo_url(u) if isinstance(u, str) else None
        if nu:
            out.append(nu)
    if not out and total > 0:
        sample = json.dumps(results[0])[:220] if results and isinstance(results[0], dict) else "n/a"
        log_hero_image(f"Unsplash total={total} but zero usable URLs query={query!r} sample={sample}")
    return out, total


def fetch_unsplash_photo_urls(
    query: str,
    *,
    max_images: int,
    access_key: str,
    name: str = "",
    country: str = "",
    park_phrase: str | None = None,
) -> list[str]:
    """
    Unsplash Search — tries ``query`` then simpler fallbacks (name/country only).
    Long LLM phrases often return ``total=0`` from the API even when the site has photos.
    """
    key = (access_key or "").strip()
    if not key:
        return []
    for i, cq in enumerate(
        _unsplash_query_candidates(query, name, country, park_phrase=park_phrase),
    ):
        urls, total = _unsplash_fetch_once(cq, max_images=max_images, access_key=key)
        log_hero_image(f"Unsplash try[{i}] query={cq!r} total={total} urls={len(urls)}")
        if urls:
            if i > 0:
                log_hero_image(f"Unsplash recovered on simpler query after {i} miss(es)")
            return urls
    return []


def _pexels_fetch_once(query: str, *, max_images: int, api_key: str) -> tuple[list[str], int]:
    key = (api_key or "").strip()
    if not key or not query.strip():
        return [], -1
    per_page = min(80, max(1, int(max_images)))
    headers = {"Authorization": key, "Accept": "application/json", "User-Agent": "WanderlyTripPlanner/1.0"}
    params: dict[str, str] = {"query": query, "per_page": str(per_page)}
    pori = (os.environ.get("PEXELS_ORIENTATION") or "").strip().lower()
    if pori in ("landscape", "portrait", "square"):
        params["orientation"] = pori
    try:
        with httpx.Client(timeout=20.0, headers=headers) as c:
            r = c.get("https://api.pexels.com/v1/search", params=params)
            if r.status_code != 200:
                log_hero_image(f"Pexels HTTP {r.status_code} query={query!r} snip={(r.text or '')[:200]!r}")
                return [], -1
            data = r.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        log_hero_image(f"Pexels error query={query!r}: {exc!r}")
        return [], -1
    photos = data.get("photos") if isinstance(data, dict) else None
    total = int(data.get("total_results", 0) or 0) if isinstance(data, dict) else 0
    if not isinstance(photos, list):
        return [], total
    out: list[str] = []
    for photo in photos:
        if len(out) >= max_images:
            break
        if not isinstance(photo, dict):
            continue
        src = photo.get("src")
        if not isinstance(src, dict):
            continue
        u = src.get("large2x") or src.get("original") or src.get("large")
        nu = _normalize_photo_url(u) if isinstance(u, str) else None
        if nu:
            out.append(nu)
    return out, total


def fetch_pexels_photo_urls(
    query: str,
    *,
    max_images: int,
    api_key: str,
    name: str = "",
    country: str = "",
    park_phrase: str | None = None,
) -> list[str]:
    """Pexels search with the same query fallback ladder as Unsplash."""
    key = (api_key or "").strip()
    if not key:
        return []
    for i, cq in enumerate(
        _unsplash_query_candidates(query, name, country, park_phrase=park_phrase),
    ):
        urls, total = _pexels_fetch_once(cq, max_images=max_images, api_key=key)
        log_hero_image(f"Pexels try[{i}] query={cq!r} total={total} urls={len(urls)}")
        if urls:
            if i > 0:
                log_hero_image(f"Pexels recovered on simpler query after {i} miss(es)")
            return urls
    return []
