"""Playwright discovery for AllTrails ``/directory/regions`` and optional region hub crawls."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from src.wanderly.alltrails_feature_crawl import _extract_feature_trail_rows_from_page
from src.wanderly.alltrails_park_crawl import (
    _COUNT_TRAILS_JS,
    _dismiss_consent,
    _page_is_blocked,
    _release_page_memory,
    _scroll_listing_page,
    connect_cdp_browser,
)
from src.wanderly.alltrails_region_directory import (
    REGIONS_HUB_URL,
    merge_region_discovery_rows,
    normalize_region_directory_url,
    normalize_us_region_listing_url,
    parse_region_pagination_urls,
)

_EXTRACT_US_REGION_LINKS_JS = """
() => {
  const out = [];
  const seen = new Set();
  const add = (href, nameHint) => {
    let path = (href || '').split('?')[0].replace(/\\/$/, '');
    if (!path) return;
    const m = path.match(/\\/us\\/([a-z0-9-]+)$/i);
    if (!m) return;
    const slug = m[1].toLowerCase();
    if (['directory', 'parks', 'trail', 'explore', 'us'].includes(slug)) return;
    const url = `https://www.alltrails.com/us/${slug}`;
    if (seen.has(url)) return;
    seen.add(url);
    let name = (nameHint || '').trim();
    if (!name || name.length < 2) {
      name = slug.replace(/-/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
    }
    out.push({ url, slug, name: name.slice(0, 120) });
  };
  document.querySelectorAll('a[href*="/us/"]').forEach((a) => {
    try {
      const href = new URL(a.href, location.origin).pathname;
      add(href, (a.innerText || '').trim());
    } catch (e) {}
  });
  return out;
}
"""

_COUNT_US_REGION_LINKS_JS = """
() => {
  const seen = new Set();
  document.querySelectorAll('a[href*="/us/"]').forEach((a) => {
    const m = (a.pathname || '').match(/\\/us\\/([a-z0-9-]+)$/i);
    if (m && m[1] && !['directory','parks','trail','explore','us'].includes(m[1].toLowerCase())) {
      seen.add(m[1].toLowerCase());
    }
  });
  return seen.size;
}
"""


def discover_region_links_on_page(page: Any, *, limit: int | None = None) -> list[dict[str, str]]:
    rows = page.evaluate(_EXTRACT_US_REGION_LINKS_JS)
    if not isinstance(rows, list):
        return []
    if limit is not None:
        return rows[:limit]
    return rows


def crawl_regions_directory_playwright(
    directory_url: str,
    *,
    limit: int | None = 50,
    max_pages: int = 1,
    headless: bool = True,
    scroll_rounds: int = 15,
    scroll_pause_ms: int = 900,
    timeout_ms: int = 90_000,
    storage_state_path: str | None = None,
    save_storage_state_path: str | None = None,
    pause_for_captcha: bool = False,
    browser_channel: str | None = "chrome",
    connect_cdp_url: str | None = None,
    use_current_tab: bool = False,
) -> list[dict[str, str]]:
    """Discover US region hub URLs from ``/directory/regions/A/N``."""
    from playwright.sync_api import sync_playwright

    directory_url = normalize_region_directory_url(directory_url)

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
                target_url=directory_url,
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
        if directory_url not in (page.url or ""):
            page.goto(directory_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2000)
        _dismiss_consent(page)

        directory_urls = [directory_url]
        if max_pages > 1:
            extra = parse_region_pagination_urls(page.content())
            seen = {directory_url}
            directory_urls = [directory_url]
            for u in extra:
                if u not in seen:
                    seen.add(u)
                    directory_urls.append(u)
            directory_urls = directory_urls[:max_pages]

        groups: list[list[dict[str, str]]] = []
        for idx, list_url in enumerate(directory_urls):
            if idx > 0:
                page.goto(list_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(2000)
                _dismiss_consent(page)
            _scroll_listing_page(
                page,
                max_rounds=scroll_rounds,
                expected_min=0,
                scroll_pause_ms=scroll_pause_ms,
                count_js=_COUNT_US_REGION_LINKS_JS,
                label=f"regions-dir-p{idx + 1}",
            )
            groups.append(discover_region_links_on_page(page))

        regions = merge_region_discovery_rows(*groups, limit=limit)
        print(
            f"[playwright] regions directory -> {len(regions)} US region hub(s) "
            f"from {len(directory_urls)} page(s)",
            flush=True,
        )

        if save_storage_state_path:
            Path(save_storage_state_path).parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=save_storage_state_path)

        if using_cdp:
            browser.close()
        else:
            browser.close()

    return regions


def _crawl_trails_on_region_hub_page(
    page: Any,
    region_url: str,
    *,
    trail_limit: int,
    max_rounds: int,
    expected_min: int,
    scroll_pause_ms: int,
    timeout_ms: int,
) -> list[dict[str, Any]]:
    """Scroll ``/us/{state}`` and extract trail cards (up to ``trail_limit``)."""
    region_url = normalize_us_region_listing_url(region_url)
    page.set_default_timeout(timeout_ms)
    if region_url not in (page.url or "").split("?")[0].rstrip("/"):
        page.goto(region_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2000)
        _dismiss_consent(page)
    want = max(1, min(int(trail_limit), 100))
    _scroll_listing_page(
        page,
        max_rounds=max_rounds,
        expected_min=min(expected_min, want),
        scroll_pause_ms=scroll_pause_ms,
        count_js=_COUNT_TRAILS_JS,
        label="region-hub",
    )
    rows = _extract_feature_trail_rows_from_page(page)
    return rows[:want]


def crawl_region_hub_trails_playwright(
    region_url: str,
    *,
    trail_limit: int = 50,
    max_rounds: int = 60,
    expected_min: int = 45,
    headless: bool = True,
    scroll_pause_ms: int = 900,
    timeout_ms: int = 90_000,
    storage_state_path: str | None = None,
    pause_for_captcha: bool = False,
    browser_channel: str | None = "chrome",
    connect_cdp_url: str | None = None,
    use_current_tab: bool = False,
) -> list[dict[str, Any]]:
    """Crawl top trails from one region hub (``/us/utah``)."""
    from playwright.sync_api import sync_playwright

    region_url = normalize_us_region_listing_url(region_url)
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
                target_url=region_url,
                use_current_tab=use_current_tab,
            )
        else:
            try:
                browser = p.chromium.launch(**launch_kwargs)
            except Exception:
                browser = p.chromium.launch(headless=headless)
            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()

        if _page_is_blocked(page):
            raise RuntimeError("AllTrails blocked automated access on region hub page.")

        rows = _crawl_trails_on_region_hub_page(
            page,
            region_url,
            trail_limit=trail_limit,
            max_rounds=max_rounds,
            expected_min=expected_min,
            scroll_pause_ms=scroll_pause_ms,
            timeout_ms=timeout_ms,
        )
        _release_page_memory(page)
        if using_cdp:
            browser.close()
        else:
            browser.close()
    return rows


def crawl_regions_directory_and_trails_playwright(
    directory_url: str,
    *,
    region_limit: int | None = 50,
    trail_limit: int = 50,
    max_pages: int = 1,
    max_rounds: int = 60,
    headless: bool = True,
    scroll_rounds: int = 15,
    scroll_pause_ms: int = 900,
    timeout_ms: int = 90_000,
    storage_state_path: str | None = None,
    save_storage_state_path: str | None = None,
    pause_for_captcha: bool = False,
    browser_channel: str | None = "chrome",
    connect_cdp_url: str | None = None,
    use_current_tab: bool = False,
    delay_between_regions_s: float = 1.0,
    should_crawl_region: Any | None = None,
    on_region_crawled: Any | None = None,
) -> list[dict[str, Any]]:
    """
  Discover region hubs from the directory, then visit each ``/us/{state}`` page
  and harvest up to ``trail_limit`` trails (default Top ~50).
  """
    from playwright.sync_api import sync_playwright

    directory_url = normalize_region_directory_url(directory_url)
    launch_kwargs: dict[str, Any] = {"headless": headless}
    if browser_channel:
        launch_kwargs["channel"] = browser_channel
    ctx_kwargs: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}, "locale": "en-US"}
    if storage_state_path and Path(storage_state_path).is_file():
        ctx_kwargs["storage_state"] = storage_state_path

    summaries: list[dict[str, Any]] = []

    with sync_playwright() as p:
        using_cdp = bool(connect_cdp_url)
        if using_cdp:
            browser, page = connect_cdp_browser(
                p,
                connect_cdp_url,
                target_url=directory_url,
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
        if directory_url not in (page.url or ""):
            page.goto(directory_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2000)
        _dismiss_consent(page)

        directory_urls = [directory_url]
        if max_pages > 1:
            extra = parse_region_pagination_urls(page.content())
            seen = {directory_url}
            directory_urls = [directory_url]
            for u in extra:
                if u not in seen:
                    seen.add(u)
                    directory_urls.append(u)
            directory_urls = directory_urls[:max_pages]

        groups: list[list[dict[str, str]]] = []
        for idx, list_url in enumerate(directory_urls):
            if idx > 0:
                page.goto(list_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(2000)
                _dismiss_consent(page)
            _scroll_listing_page(
                page,
                max_rounds=scroll_rounds,
                expected_min=0,
                scroll_pause_ms=scroll_pause_ms,
                count_js=_COUNT_US_REGION_LINKS_JS,
                label=f"regions-dir-p{idx + 1}",
            )
            groups.append(discover_region_links_on_page(page))

        regions = merge_region_discovery_rows(*groups, limit=region_limit)
        print(
            f"[playwright] regions directory -> {len(regions)} hub(s); "
            f"crawling up to {trail_limit} trails each",
            flush=True,
        )

        for ridx, region in enumerate(regions, start=1):
            slug = str(region.get("slug") or "").strip().lower()
            hub_url = str(region.get("url") or "").strip()
            name = str(region.get("name") or slug).strip()
            if not slug or not hub_url:
                continue
            if should_crawl_region and not should_crawl_region(slug, hub_url):
                print(f"[playwright] skip region {slug!r}", flush=True)
                continue
            print(
                f"[playwright] region ({ridx}/{len(regions)}) {name!r} -> {hub_url}",
                flush=True,
            )
            try:
                trails = _crawl_trails_on_region_hub_page(
                    page,
                    hub_url,
                    trail_limit=trail_limit,
                    max_rounds=max_rounds,
                    expected_min=min(45, trail_limit),
                    scroll_pause_ms=scroll_pause_ms,
                    timeout_ms=timeout_ms,
                )
            except Exception as exc:
                print(f"[playwright]   ERROR {slug!r}: {exc}", flush=True)
                trails = []
            print(f"[playwright]   -> {len(trails)} trails", flush=True)
            summary = {
                "slug": slug,
                "name": name,
                "url": hub_url,
                "trail_count": len(trails),
                "trails": trails,
            }
            summaries.append(summary)
            if on_region_crawled:
                on_region_crawled(summary)
            if delay_between_regions_s > 0 and ridx < len(regions):
                time.sleep(delay_between_regions_s)

        if save_storage_state_path:
            Path(save_storage_state_path).parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=save_storage_state_path)

        if using_cdp:
            browser.close()
        else:
            browser.close()

    return summaries


def crawl_regions_hub_playwright(
    *,
    limit: int | None = 50,
    headless: bool = True,
    scroll_rounds: int = 15,
    scroll_pause_ms: int = 900,
    timeout_ms: int = 90_000,
    storage_state_path: str | None = None,
    save_storage_state_path: str | None = None,
    pause_for_captcha: bool = False,
    browser_channel: str | None = "chrome",
    connect_cdp_url: str | None = None,
    use_current_tab: bool = False,
) -> list[dict[str, str]]:
    """Discover from main regions hub (redirects or links to directory pages)."""
    return crawl_regions_directory_playwright(
        f"{REGIONS_HUB_URL}/A/1",
        limit=limit,
        max_pages=1,
        headless=headless,
        scroll_rounds=scroll_rounds,
        scroll_pause_ms=scroll_pause_ms,
        timeout_ms=timeout_ms,
        storage_state_path=storage_state_path,
        save_storage_state_path=save_storage_state_path,
        pause_for_captcha=pause_for_captcha,
        browser_channel=browser_channel,
        connect_cdp_url=connect_cdp_url,
        use_current_tab=use_current_tab,
    )
