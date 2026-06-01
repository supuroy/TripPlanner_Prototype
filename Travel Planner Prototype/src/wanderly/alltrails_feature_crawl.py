"""
Playwright harvest of trail rows from AllTrails ``/directory/trail-features/{slug}`` pages.

Offline only — builds ``data/trail_catalog/features/`` without Wanderly or MCP.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin

from src.wanderly.alltrails_feature_directory import (
    parse_feature_listing_url,
    parse_feature_pagination_urls,
)
from src.wanderly.alltrails_park_crawl import (
    _COUNT_TRAILS_JS,
    _TRAIL_HREF_RE,
    _dismiss_consent,
    _normalize_trail_url,
    _page_is_blocked,
    _release_page_memory,
    _scroll_listing_page,
    connect_cdp_browser,
)

# Feature listings often show area / park under the trail title.
_EXTRACT_TRAILS_FEATURE_JS = """
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('a[href*="/trail/"]').forEach((a) => {
    let href = (a.href || '').split('?')[0].replace(/\\/$/, '');
    if (!href || seen.has(href)) return;
    if (!/\\/trail\\/[a-z]{2}\\/[^/]+\\/[^/]+/i.test(href)) return;
    seen.add(href);
    const card = a.closest('article')
      || a.closest('[data-testid]')
      || a.closest('li')
      || a.parentElement?.parentElement?.parentElement;
    const text = (card?.innerText || a.innerText || '').replace(/\\s+/g, ' ').trim();
    let name = (a.innerText || '').trim();
    const h = card?.querySelector('h2,h3,h4');
    if ((!name || name.length < 3) && h) name = (h.innerText || '').trim();
    if (!name) {
      const slug = href.split('/').pop() || '';
      name = slug.replace(/-/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
    }
    let difficulty = null;
    const diffM = text.match(/\\b(Easy|Moderate|Hard|Difficult|Strenuous)\\b/i);
    if (diffM) difficulty = diffM[1];
    let distance_mi = null;
    const miM = text.match(/([\\d.]+)\\s*mi\\b/i);
    if (miM) distance_mi = parseFloat(miM[1]);
    const ratingM = text.match(/([\\d.]+)\\s*\\(\\s*[\\d,]+\\s*\\)/);
    const rating = ratingM ? parseFloat(ratingM[1]) : null;
    const parkA = card?.querySelector('a[href*="/parks/"]');
    let park_url = '';
    let location_label = '';
    if (parkA) {
      park_url = (parkA.href || '').split('?')[0].replace(/\\/$/, '');
      location_label = (parkA.innerText || '').trim();
    }
    out.push({
      url: href,
      name: name.slice(0, 200),
      difficulty,
      distance_mi,
      rating,
      location_label: location_label.slice(0, 200),
      park_url: park_url.slice(0, 500),
    });
  });
  return out;
}
"""

_EXTRACT_FEATURE_LINKS_JS = """
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('a[href*="/directory/trail-features/"]').forEach((a) => {
    let href = (a.href || '').split('?')[0].replace(/\\/$/, '');
    const m = href.match(/\\/directory\\/trail-features\\/([a-z0-9-]+)(?:\\/A\\/\\d+)?$/i);
    if (!m) return;
    const slug = m[1].toLowerCase();
    if (!slug || slug === 'trail-features') return;
    const canonical = `https://www.alltrails.com/directory/trail-features/${slug}/A/1`;
    if (seen.has(canonical)) return;
    seen.add(canonical);
    let name = (a.innerText || '').trim();
    if (!name || name.length < 2) {
      name = slug.replace(/-/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
    }
    out.push({ url: canonical, slug, name: name.slice(0, 120) });
  });
  return out;
}
"""

_COUNT_FEATURE_LINKS_JS = """
() => {
  const seen = new Set();
  document.querySelectorAll('a[href*="/directory/trail-features/"]').forEach((a) => {
    const href = (a.href || '').split('?')[0].replace(/\\/$/, '');
    const m = href.match(/\\/directory\\/trail-features\\/([a-z0-9-]+)(?:\\/A\\/\\d+)?$/i);
    if (m && m[1] && m[1] !== 'trail-features') seen.add(m[1].toLowerCase());
  });
  return seen.size;
}
"""


def _extract_feature_city_links_js(feature_slug: str) -> str:
    slug = (feature_slug or "").strip().lower().replace("'", "")
    return f"""
() => {{
  const featureSlug = {slug!r};
  const out = [];
  const seen = new Set();
  const isCityListing = (pathname) => {{
    const parts = pathname.replace(/\\/+$/, '').split('/').filter(Boolean);
    if (parts.length < 3 || parts[0].toLowerCase() !== 'us') return false;
    if (parts[parts.length - 1].toLowerCase() !== featureSlug) return false;
    if (pathname.toLowerCase().includes('/directory/')) return false;
    if (parts.length === 3) return true;
    if (parts.length === 4) {{
      return parts[1].toLowerCase() !== featureSlug && parts[2].toLowerCase() !== featureSlug;
    }}
    return false;
  }};
  document.querySelectorAll('a[href*="/us/"]').forEach((a) => {{
    let href = (a.href || '').split('?')[0].replace(/\\/$/, '');
    if (!href || seen.has(href)) return;
    let path = '';
    try {{ path = new URL(href).pathname; }} catch (e) {{ return; }}
    if (!isCityListing(path)) return;
    seen.add(href);
    const parts = path.split('/').filter(Boolean);
    const label = (a.innerText || '').trim()
      || (parts.length === 4 ? parts[2] : parts[1]).replace(/-/g, ' ');
    out.push({{ url: href, name: label.slice(0, 120) }});
  }});
  return out;
}}
"""


def _count_feature_city_links_js(feature_slug: str) -> str:
    slug = (feature_slug or "").strip().lower().replace("'", "")
    return f"""
() => {{
  const slug = {slug!r};
  const seen = new Set();
  document.querySelectorAll('a[href*="/us/"]').forEach((a) => {{
    let path = '';
    try {{ path = new URL((a.href || '').split('?')[0]).pathname; }} catch (e) {{ return; }}
    const parts = path.replace(/\\/+$/, '').split('/').filter(Boolean);
    if (parts.length < 3 || parts[0].toLowerCase() !== 'us') return;
    if (parts[parts.length - 1].toLowerCase() !== slug) return;
    if (path.toLowerCase().includes('/directory/')) return;
    if (parts.length === 3 || parts.length === 4) seen.add(path);
  }});
  return seen.size;
}}
"""


def _extract_feature_trail_rows_from_page(page: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw = page.evaluate(_EXTRACT_TRAILS_FEATURE_JS)
    if not isinstance(raw, list):
        return rows
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = _normalize_trail_url(str(item.get("url") or ""))
        if not _TRAIL_HREF_RE.search(url):
            continue
        park_url = str(item.get("park_url") or "").strip()
        if park_url and not park_url.startswith("http"):
            park_url = urljoin("https://www.alltrails.com", park_url)
        rows.append(
            {
                "url": url,
                "name": str(item.get("name") or "").strip(),
                "difficulty": item.get("difficulty"),
                "length_mi": item.get("distance_mi"),
                "rating": item.get("rating"),
                "location_label": str(item.get("location_label") or "").strip(),
                "park_url": park_url,
            }
        )
    return rows


def discover_feature_city_links_on_page(
    page: Any,
    *,
    feature_slug: str,
    limit: int | None = None,
) -> list[dict[str, str]]:
    from src.wanderly.alltrails_feature_directory import (
        merge_feature_city_rows,
        parse_feature_city_links_from_html,
    )

    slug = (feature_slug or "").strip().lower()
    js_rows: list[dict[str, str]] = []
    raw = page.evaluate(_extract_feature_city_links_js(slug))
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("url"):
                js_rows.append(
                    {
                        "url": str(item.get("url") or "").split("?")[0].rstrip("/"),
                        "name": str(item.get("name") or "").strip(),
                    }
                )
    html_rows = parse_feature_city_links_from_html(page.content(), feature_slug=slug)
    return merge_feature_city_rows(js_rows, html_rows, limit=limit)


def discover_feature_links_on_page(page: Any, *, limit: int | None = None) -> list[dict[str, str]]:
    from src.wanderly.alltrails_feature_directory import (
        merge_feature_discovery_rows,
        parse_feature_links_from_html,
    )

    js_rows: list[dict[str, str]] = []
    raw = page.evaluate(_EXTRACT_FEATURE_LINKS_JS)
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("url"):
                js_rows.append(
                    {
                        "url": str(item.get("url") or ""),
                        "slug": str(item.get("slug") or "").strip().lower(),
                        "name": str(item.get("name") or "").strip(),
                    }
                )

    html_rows = parse_feature_links_from_html(page.content())
    return merge_feature_discovery_rows(js_rows, html_rows, limit=limit)


def crawl_feature_trails_on_page(
    page: Any,
    feature_url: str,
    *,
    max_rounds: int = 80,
    expected_min: int = 30,
    scroll_pause_ms: int = 900,
    timeout_ms: int = 90_000,
    release_after: bool = True,
) -> list[dict[str, Any]]:
    """Scroll one feature listing page and extract trail rows."""
    feature_url = feature_url.strip()
    page.set_default_timeout(timeout_ms)
    page.goto(feature_url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(2000)
    _dismiss_consent(page)
    _scroll_listing_page(
        page,
        max_rounds=max_rounds,
        expected_min=expected_min,
        scroll_pause_ms=scroll_pause_ms,
        count_js=_COUNT_TRAILS_JS,
        label="feature",
    )
    rows = _extract_feature_trail_rows_from_page(page)
    meta = parse_feature_listing_url(feature_url)
    print(
        f"[playwright] feature {meta.feature_slug!r} page {meta.page} "
        f"-> {len(rows)} trails",
        flush=True,
    )
    if release_after:
        _release_page_memory(page)
    return rows


def _merge_trail_rows_by_url(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for group in groups:
        for row in group:
            url = _normalize_trail_url(str(row.get("url") or ""))
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({**row, "url": url})
    return out


def _crawl_trails_on_city_page(
    page: Any,
    city_url: str,
    *,
    max_rounds: int,
    expected_min: int,
    scroll_pause_ms: int,
    timeout_ms: int,
) -> list[dict[str, Any]]:
    page.set_default_timeout(timeout_ms)
    if city_url not in (page.url or "").split("?")[0].rstrip("/"):
        page.goto(city_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2000)
        _dismiss_consent(page)
    _scroll_listing_page(
        page,
        max_rounds=max_rounds,
        expected_min=expected_min,
        scroll_pause_ms=scroll_pause_ms,
        count_js=_COUNT_TRAILS_JS,
        label="feature-city",
    )
    return _extract_feature_trail_rows_from_page(page)


def crawl_feature_trails_playwright(
    feature_url: str,
    *,
    headless: bool = True,
    max_rounds: int = 80,
    expected_min: int = 10,
    max_pages: int = 1,
    city_limit: int | None = 50,
    discovery: str = "cities",
    scroll_pause_ms: int = 900,
    timeout_ms: int = 90_000,
    storage_state_path: str | None = None,
    save_storage_state_path: str | None = None,
    pause_for_captcha: bool = False,
    browser_channel: str | None = "chrome",
    connect_cdp_url: str | None = None,
    use_current_tab: bool = False,
    delay_between_cities_s: float = 1.0,
) -> list[dict[str, Any]]:
    """
    Crawl trails for one AllTrails trail feature.

    ``discovery=cities`` (default): ``/directory/trail-features/{slug}/A/N`` lists
    **city** pages (``/us/{state}/{city}/{slug}``); this discovers those URLs, then
    scrolls each city page for ``/trail/`` links (same as park listings).

    ``discovery=direct``: scroll the given URL only (usually yields 0 on directory pages).
    """
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    meta = parse_feature_listing_url(feature_url)
    mode = (discovery or "cities").strip().lower()

    launch_kwargs: dict[str, Any] = {"headless": headless}
    if browser_channel:
        launch_kwargs["channel"] = browser_channel
    ctx_kwargs: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}, "locale": "en-US"}
    if storage_state_path and Path(storage_state_path).is_file():
        ctx_kwargs["storage_state"] = storage_state_path

    all_rows: list[dict[str, Any]] = []

    with sync_playwright() as p:
        using_cdp = bool(connect_cdp_url)
        if using_cdp:
            browser, page = connect_cdp_browser(
                p,
                connect_cdp_url,
                target_url=meta.listing_url,
                use_current_tab=use_current_tab,
            )
            context = page.context
        else:
            try:
                browser = p.chromium.launch(**launch_kwargs)
            except Exception:
                browser = p.chromium.launch(headless=headless)
            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()

        page.set_default_timeout(timeout_ms)
        page.goto(meta.listing_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2500)

        if _page_is_blocked(page.content(), page.title()) and not using_cdp:
            if pause_for_captcha or not headless:
                print(
                    "[playwright] Captcha detected — complete it in the browser, then press Enter...",
                    flush=True,
                )
                if headless:
                    raise RuntimeError("Captcha in headless mode — use --headed --pause-for-captcha")
                input()
                page.goto(meta.listing_url, wait_until="domcontentloaded", timeout=timeout_ms)
            else:
                browser.close()
                raise RuntimeError(
                    "AllTrails blocked automated access. Use CDP (--connect-cdp) or "
                    "--headed --pause-for-captcha with --save-storage."
                )

        _dismiss_consent(page)
        group_rows: list[list[dict[str, Any]]] = []

        if mode in ("direct", "directory-only"):
            directory_urls = [meta.listing_url]
            if max_pages > 1:
                extra = parse_feature_pagination_urls(page.content(), feature_slug=meta.feature_slug)
                seen_u: set[str] = {meta.listing_url}
                directory_urls = [meta.listing_url]
                for u in extra:
                    if u not in seen_u:
                        seen_u.add(u)
                        directory_urls.append(u)
                directory_urls = directory_urls[:max_pages]
            for idx, list_url in enumerate(directory_urls):
                if idx > 0:
                    page.goto(list_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(2000)
                    _dismiss_consent(page)
                _scroll_listing_page(
                    page,
                    max_rounds=max_rounds,
                    expected_min=expected_min if idx == 0 else 0,
                    scroll_pause_ms=scroll_pause_ms,
                    count_js=_COUNT_TRAILS_JS,
                    label=f"feature-dir-p{idx + 1}",
                )
                group_rows.append(_extract_feature_trail_rows_from_page(page))
        else:
            directory_urls = [meta.listing_url]
            if max_pages > 1:
                extra = parse_feature_pagination_urls(page.content(), feature_slug=meta.feature_slug)
                seen_u: set[str] = {meta.listing_url}
                directory_urls = [meta.listing_url]
                for u in extra:
                    if u not in seen_u:
                        seen_u.add(u)
                        directory_urls.append(u)
                directory_urls = directory_urls[:max_pages]

            city_groups: list[list[dict[str, str]]] = []
            count_city_js = _count_feature_city_links_js(meta.feature_slug)
            for idx, dir_url in enumerate(directory_urls):
                if idx > 0:
                    page.goto(dir_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(2000)
                    _dismiss_consent(page)
                _scroll_listing_page(
                    page,
                    max_rounds=min(max_rounds, 25),
                    expected_min=0,
                    scroll_pause_ms=scroll_pause_ms,
                    count_js=count_city_js,
                    label=f"feature-dir-p{idx + 1}",
                )
                city_groups.append(
                    discover_feature_city_links_on_page(page, feature_slug=meta.feature_slug)
                )

            from src.wanderly.alltrails_feature_directory import merge_feature_city_rows

            cities = merge_feature_city_rows(*city_groups, limit=city_limit)
            print(
                f"[playwright] feature {meta.feature_slug!r}: "
                f"{len(cities)} city listing(s) from {len(directory_urls)} directory page(s)",
                flush=True,
            )
            if not cities:
                print(
                    "[playwright] WARN: no city URLs found on directory page. "
                    "AllTrails lists cities at /us/.../feature_slug, not trails on "
                    "/directory/trail-features/...",
                    flush=True,
                )

            for cidx, city in enumerate(cities, start=1):
                city_url = str(city.get("url") or "")
                if not city_url:
                    continue
                print(
                    f"[playwright]   city ({cidx}/{len(cities)}) {city.get('name') or city_url}",
                    flush=True,
                )
                rows = _crawl_trails_on_city_page(
                    page,
                    city_url,
                    max_rounds=max_rounds,
                    expected_min=expected_min,
                    scroll_pause_ms=scroll_pause_ms,
                    timeout_ms=timeout_ms,
                )
                print(f"[playwright]     -> {len(rows)} trails", flush=True)
                group_rows.append(rows)
                if delay_between_cities_s > 0 and cidx < len(cities):
                    time.sleep(delay_between_cities_s)

        all_rows = _merge_trail_rows_by_url(*group_rows)
        print(
            f"[playwright] feature {meta.feature_slug!r} total {len(all_rows)} unique trails",
            flush=True,
        )

        if save_storage_state_path:
            Path(save_storage_state_path).parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=save_storage_state_path)

        if using_cdp:
            browser.close()
        else:
            browser.close()

    return all_rows


def crawl_feature_directory_playwright(
    hub_url: str,
    *,
    limit: int | None = None,
    headless: bool = True,
    directory_scroll_rounds: int = 15,
    scroll_pause_ms: int = 900,
    timeout_ms: int = 90_000,
    storage_state_path: str | None = None,
    save_storage_state_path: str | None = None,
    pause_for_captcha: bool = False,
    browser_channel: str | None = "chrome",
    connect_cdp_url: str | None = None,
    use_current_tab: bool = False,
) -> list[dict[str, str]]:
    """Discover feature slugs from the trail-features hub page."""
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    hub_url = (hub_url or "").strip() or "https://www.alltrails.com/directory/trail-features"

    launch_kwargs: dict[str, Any] = {"headless": headless}
    if browser_channel:
        launch_kwargs["channel"] = browser_channel
    ctx_kwargs: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}, "locale": "en-US"}
    if storage_state_path and Path(storage_state_path).is_file():
        ctx_kwargs["storage_state"] = storage_state_path

    with sync_playwright() as p:
        using_cdp = bool(connect_cdp_url)
        if using_cdp:
            browser, page = connect_cdp_browser(
                p,
                connect_cdp_url,
                target_url=hub_url,
                use_current_tab=use_current_tab,
            )
            context = page.context
        else:
            try:
                browser = p.chromium.launch(**launch_kwargs)
            except Exception:
                browser = p.chromium.launch(headless=headless)
            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()

        page.set_default_timeout(timeout_ms)
        if hub_url not in (page.url or ""):
            page.goto(hub_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2000)
        _dismiss_consent(page)
        if directory_scroll_rounds > 0:
            _scroll_listing_page(
                page,
                max_rounds=directory_scroll_rounds,
                expected_min=0,
                scroll_pause_ms=scroll_pause_ms,
                count_js=_COUNT_FEATURE_LINKS_JS,
                label="features-hub",
            )
        features = discover_feature_links_on_page(page, limit=limit)
        print(f"[playwright] trail-features hub -> {len(features)} features", flush=True)

        if save_storage_state_path:
            Path(save_storage_state_path).parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=save_storage_state_path)

        if using_cdp:
            browser.close()
        else:
            browser.close()

    return features


def crawl_features_batch_playwright(
    feature_urls: list[str],
    *,
    headless: bool = True,
    max_rounds: int = 80,
    expected_min: int = 10,
    max_pages: int = 1,
    city_limit: int | None = 50,
    discovery: str = "cities",
    scroll_pause_ms: int = 900,
    timeout_ms: int = 90_000,
    storage_state_path: str | None = None,
    save_storage_state_path: str | None = None,
    pause_for_captcha: bool = False,
    browser_channel: str | None = "chrome",
    connect_cdp_url: str | None = None,
    delay_between_features_s: float = 1.5,
    delay_between_cities_s: float = 1.0,
    on_feature_crawled: Any | None = None,
    should_crawl_feature: Any | None = None,
    use_current_tab: bool = False,
) -> list[dict[str, Any]]:
    """Crawl multiple feature listing URLs; optional callback per feature."""
    summaries: list[dict[str, Any]] = []
    urls = [u.strip() for u in feature_urls if (u or "").strip()]
    for idx, url in enumerate(urls, start=1):
        meta = parse_feature_listing_url(url)
        if should_crawl_feature and not should_crawl_feature(meta.feature_slug, url):
            print(f"[playwright] skip feature {meta.feature_slug!r}", flush=True)
            continue
        print(f"[playwright] ({idx}/{len(urls)}) {meta.feature_slug!r}", flush=True)
        rows = crawl_feature_trails_playwright(
            url,
            headless=headless,
            max_rounds=max_rounds,
            expected_min=expected_min,
            max_pages=max_pages,
            city_limit=city_limit,
            discovery=discovery,
            scroll_pause_ms=scroll_pause_ms,
            timeout_ms=timeout_ms,
            storage_state_path=storage_state_path,
            save_storage_state_path=save_storage_state_path if idx == len(urls) else None,
            pause_for_captcha=pause_for_captcha,
            browser_channel=browser_channel,
            connect_cdp_url=connect_cdp_url,
            use_current_tab=use_current_tab and idx == 1,
            delay_between_cities_s=delay_between_cities_s,
        )
        if on_feature_crawled:
            on_feature_crawled(meta, rows)
        summaries.append(
            {
                "feature_slug": meta.feature_slug,
                "feature_url": meta.listing_url,
                "trail_count": len(rows),
            }
        )
        if delay_between_features_s > 0 and idx < len(urls):
            time.sleep(delay_between_features_s)
    return summaries
