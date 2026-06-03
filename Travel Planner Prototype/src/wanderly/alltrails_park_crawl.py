"""
Playwright-based harvest of trail rows from an AllTrails national-park listing page.

Used offline to build ``data/trail_catalog/`` without Apify quota.
"""

from __future__ import annotations

import gc
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

_TRAIL_HREF_RE = re.compile(r"/(?:explore/)?trail/[a-z]{2}/[^/]+/[^/]+", re.I)

# In-page script: collect trail cards from listing DOM.
_EXTRACT_TRAILS_JS = """
() => {
  const out = [];
  const seen = new Set();
  const anchors = document.querySelectorAll('a[href*="/trail/"]');
  for (const a of anchors) {
    let href = (a.href || '').split('?')[0].replace(/\\/$/, '');
    if (!href || seen.has(href)) continue;
    if (!/\\/trail\\/[a-z]{2}\\/[^/]+\\/[^/]+/i.test(href)) continue;
    seen.add(href);
    const card = a.closest('article')
      || a.closest('[data-testid]')
      || a.closest('li')
      || a.parentElement?.parentElement?.parentElement;
    const text = (card?.innerText || a.innerText || '').replace(/\\s+/g, ' ').trim();
    let name = (a.innerText || '').trim();
    if (!name || name.length < 3) {
      const h = card?.querySelector('h2,h3,h4');
      name = (h?.innerText || '').trim();
    }
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
    out.push({
      url: href,
      name: name.slice(0, 200),
      difficulty,
      distance_mi,
      rating,
      listing_blurb: text.slice(0, 280),
    });
  }
  return out;
}
"""

# Lighter payload for batch crawls (no listing_blurb text per trail).
_EXTRACT_TRAILS_COMPACT_JS = """
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('a[href*="/trail/"]').forEach((a) => {
    let href = (a.href || '').split('?')[0].replace(/\\/$/, '');
    if (!href || seen.has(href)) return;
    if (!/\\/trail\\/[a-z]{2}\\/[^/]+\\/[^/]+/i.test(href)) return;
    seen.add(href);
    let name = (a.innerText || '').trim();
    const card = a.closest('article') || a.closest('li');
    const h = card?.querySelector('h2,h3,h4');
    if ((!name || name.length < 3) && h) name = (h.innerText || '').trim();
    if (!name) {
      const slug = href.split('/').pop() || '';
      name = slug.replace(/-/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
    }
    let difficulty = null;
    const text = (card?.innerText || a.innerText || '').replace(/\\s+/g, ' ');
    const diffM = text.match(/\\b(Easy|Moderate|Hard|Difficult|Strenuous)\\b/i);
    if (diffM) difficulty = diffM[1];
    let distance_mi = null;
    const miM = text.match(/([\\d.]+)\\s*mi\\b/i);
    if (miM) distance_mi = parseFloat(miM[1]);
    const ratingM = text.match(/([\\d.]+)\\s*\\(\\s*[\\d,]+\\s*\\)/);
    const rating = ratingM ? parseFloat(ratingM[1]) : null;
    out.push({
      url: href,
      name: name.slice(0, 200),
      difficulty,
      distance_mi,
      rating,
    });
  });
  return out;
}
"""

_COUNT_TRAILS_JS = """
() => {
  const seen = new Set();
  document.querySelectorAll('a[href*="/trail/"]').forEach(a => {
    const href = (a.href || '').split('?')[0];
    if (/\\/trail\\/[a-z]{2}\\/[^/]+\\/[^/]+/i.test(href)) seen.add(href);
  });
  return seen.size;
}
"""

_EXTRACT_PARK_LINKS_JS = """
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('a[href*="/parks/"]').forEach(a => {
    let href = (a.href || '').split('?')[0].replace(/\\/$/, '');
    if (!/\\/parks\\/[a-z]{2}\\/[^/]+\\/[^/]+/i.test(href)) return;
    if (seen.has(href)) return;
    seen.add(href);
    let name = (a.innerText || '').trim();
    const card = a.closest('article') || a.closest('li') || a.closest('[data-testid]');
    const h = card?.querySelector('h2,h3,h4');
    if ((!name || name.length < 3) && h) name = (h.innerText || '').trim();
    out.push({ url: href, name: name.slice(0, 200) });
  });
  return out;
}
"""

_COUNT_PARK_LINKS_JS = """
() => {
  const seen = new Set();
  document.querySelectorAll('a[href*="/parks/"]').forEach(a => {
    const href = (a.href || '').split('?')[0];
    if (/\\/parks\\/[a-z]{2}\\/[^/]+\\/[^/]+/i.test(href)) seen.add(href);
  });
  return seen.size;
}
"""

_EXTRACT_TOP_50_PARKS_JS = """
() => {
  const out = [];
  const seen = new Set();
  const parkRe = /\\/parks\\/[a-z]{2}\\/[^/]+\\/[^/]+/i;
  const isTop50Heading = (el) =>
    /top\\s*50\\s*parks?/i.test((el.innerText || '').replace(/\\s+/g, ' ').trim());

  const heading = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6')).find(isTop50Heading);
  if (!heading) return out;

  const collectFrom = (root) => {
    if (!root) return;
    root.querySelectorAll('a[href*="/parks/"]').forEach((a) => {
      let href = (a.href || '').split('?')[0].replace(/\\/$/, '');
      if (!parkRe.test(href) || seen.has(href)) return;
      seen.add(href);
      let name = (a.innerText || '').trim();
      out.push({ url: href, name: name.slice(0, 200) });
    });
  };

  let el = heading.nextElementSibling;
  while (el && out.length < 55) {
    const tag = (el.tagName || '').toLowerCase();
    if (['h1', 'h2', 'h3'].includes(tag)) {
      const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
      if (t && !/top\\s*50/i.test(t)) break;
    }
    collectFrom(el);
    el = el.nextElementSibling;
  }
  if (out.length < 10) collectFrom(heading.parentElement);
  return out.slice(0, 50);
}
"""


def _normalize_trail_url(url: str) -> str:
    u = (url or "").strip().split("?")[0].rstrip("/")
    if u and not u.startswith("http"):
        u = urljoin("https://www.alltrails.com", u)
    return u


def _dismiss_consent(page: Any) -> None:
    """Best-effort cookie / consent banners."""
    selectors = [
        'button:has-text("Accept")',
        'button:has-text("Accept All")',
        'button:has-text("I Agree")',
        'button:has-text("Got it")',
        '[data-testid="accept-cookies"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500):
                loc.click(timeout=2000)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def _click_show_more_if_present(page: Any) -> bool:
    """Return True if a Show more control was clicked."""
    patterns = [
        re.compile(r"show more", re.I),
        re.compile(r"load more", re.I),
    ]
    for pat in patterns:
        try:
            btn = page.get_by_role("button", name=pat)
            if btn.count() > 0 and btn.first.is_visible(timeout=1200):
                btn.first.click(timeout=3000)
                return True
        except Exception:
            pass
        try:
            link = page.get_by_role("link", name=pat)
            if link.count() > 0 and link.first.is_visible(timeout=1200):
                link.first.click(timeout=3000)
                return True
        except Exception:
            pass
    return False


def _is_internal_debug_tab(url: str) -> bool:
    u = (url or "").strip().lower()
    if not u or u in ("about:blank", "about:newtab"):
        return True
    if "127.0.0.1:9222" in u or "localhost:9222" in u:
        return True
    if "/json/version" in u or "/json/list" in u:
        return True
    return u.startswith("chrome://") or u.startswith("devtools://")


def _cdp_browser_pages(browser: Any) -> list[Any]:
    pages: list[Any] = []
    for context in browser.contexts:
        pages.extend(context.pages)
    return pages


def _pick_cdp_page(
    browser: Any,
    *,
    target_url: str,
    use_current_tab: bool,
) -> Any:
    """
    Attach to a real AllTrails tab over CDP (not chrome:// or :9222/json/*).
    """
    pages = _cdp_browser_pages(browser)
    alltrails_pages = [p for p in pages if "alltrails.com" in (p.url or "").lower()]
    usable_pages = [p for p in pages if not _is_internal_debug_tab(p.url or "")]

    target_path = urlparse(target_url.split("?")[0].rstrip("/")).path

    if use_current_tab:
        if alltrails_pages:
            for page in alltrails_pages:
                page_path = urlparse((page.url or "").split("?")[0].rstrip("/")).path
                if target_path and target_path == page_path:
                    return page
            print(
                f"[playwright] AllTrails tab on {(alltrails_pages[0].url or '')!r} "
                f"(will navigate to {target_url!r} if needed)",
                flush=True,
            )
            return alltrails_pages[0]
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        if usable_pages:
            print(
                f"[playwright] No AllTrails tab yet; using {(usable_pages[0].url or '')!r}",
                flush=True,
            )
            return usable_pages[0]
        print("[playwright] opening a new tab in your Chrome (CDP)", flush=True)
        return context.new_page()

    context = browser.contexts[0] if browser.contexts else browser.new_context()
    if alltrails_pages:
        return alltrails_pages[0]
    if usable_pages:
        return usable_pages[0]
    return context.new_page()


def _is_navigable_blank_tab(url: str) -> bool:
    u = (url or "").strip().lower()
    return u in ("about:blank", "about:newtab")


def connect_cdp_browser(
    playwright: Any,
    connect_cdp_url: str,
    *,
    target_url: str,
    use_current_tab: bool,
    allow_blank_tab: bool = False,
) -> tuple[Any, Any]:
    """Return ``(browser, page)`` for an AllTrails-capable tab."""
    print(f"[playwright] connecting over CDP {connect_cdp_url!r}", flush=True)
    browser = playwright.chromium.connect_over_cdp(connect_cdp_url)
    page = _pick_cdp_page(browser, target_url=target_url, use_current_tab=use_current_tab)
    print(f"[playwright] CDP tab: {(page.url or '')!r}", flush=True)
    if _is_internal_debug_tab(page.url or ""):
        if allow_blank_tab and _is_navigable_blank_tab(page.url or ""):
            pass
        else:
            raise RuntimeError(
                f"CDP selected a debug tab ({page.url!r}), not AllTrails. "
                f"Open {target_url} in Chrome (port 9222), then re-run."
            )
    return browser, page


def _page_is_blocked(html: str, title: str) -> bool:
    """Heuristic captcha / bot wall — avoid matching incidental 'datadome' on real pages."""
    blob = f"{title} {html[:16_000]}".lower()
    if "captcha-delivery.com" in blob or "geo.captcha-delivery.com" in blob:
        return True
    if "please enable js" in blob and len(html) < 8000:
        return True
    if "datadome" in blob and ("captcha" in blob or "blocked" in blob):
        return True
    title_low = (title or "").strip().lower()
    if title_low in ("alltrails.com", "alltrails") and len(html) < 3500:
        return True
    return False


def _page_has_listing_signals(page: Any, *, discovery: str = "top-50") -> bool:
    """True when the open tab already shows park directory links (not a bot wall)."""
    try:
        mode = (discovery or "top-50").strip().lower()
        if mode in ("top-50", "top50", "top_50"):
            found = page.evaluate(
                """() => Array.from(document.querySelectorAll('h1,h2,h3,h4'))
                  .some(el => /top\\s*50\\s*parks?/i.test((el.innerText||'').replace(/\\s+/g,' ').trim()))"""
            )
            if found:
                return True
        count = int(page.evaluate(_COUNT_PARK_LINKS_JS))
        return count >= 3
    except Exception:
        return False


def _wait_for_listing_content(page: Any, *, timeout_ms: int) -> None:
    try:
        page.wait_for_selector('a[href*="/parks/"]', timeout=min(timeout_ms, 25_000))
    except Exception:
        page.wait_for_timeout(3000)


def _ensure_page_ready(
    page: Any,
    url: str,
    *,
    timeout_ms: int,
    using_cdp: bool,
    pause_for_captcha: bool,
    headless: bool,
    storage_state_path: str | None,
    use_current_tab: bool = False,
    discovery: str = "top-50",
) -> None:
    target = url.strip()
    current = (page.url or "").strip()

    current_ok = (
        using_cdp
        and use_current_tab
        and "alltrails.com" in current.lower()
        and not _is_internal_debug_tab(current)
    )
    target_path = urlparse(target.split("?")[0].rstrip("/")).path
    current_path = urlparse(current.split("?")[0].rstrip("/")).path
    same_listing = current_ok and target_path == current_path

    if same_listing and _page_has_listing_signals(page, discovery=discovery):
        print(f"[playwright] using current AllTrails tab {current!r}", flush=True)
        _wait_for_listing_content(page, timeout_ms=timeout_ms)
    elif current_ok and _page_has_listing_signals(page, discovery=discovery):
        print(f"[playwright] using current tab (listing already loaded)", flush=True)
        _wait_for_listing_content(page, timeout_ms=timeout_ms)
    elif target not in current or _is_internal_debug_tab(current):
        print(f"[playwright] goto {target!r}", flush=True)
        page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
        _wait_for_listing_content(page, timeout_ms=timeout_ms)
    page.wait_for_timeout(2000)

    if _page_has_listing_signals(page, discovery=discovery):
        return

    blocked = _page_is_blocked(page.content(), page.title())
    if blocked and (pause_for_captcha or using_cdp):
        print(
            "[playwright] Captcha or empty page detected. Fix it in the browser window, then press Enter...",
            flush=True,
        )
        if headless and not using_cdp:
            raise RuntimeError("Captcha in headless mode — use --headed --pause-for-captcha")
        input()
        if not use_current_tab:
            page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
        _wait_for_listing_content(page, timeout_ms=timeout_ms)
        page.wait_for_timeout(2000)
        if _page_has_listing_signals(page, discovery=discovery):
            return

    if _page_has_listing_signals(page, discovery=discovery):
        return

    if blocked and not using_cdp and not storage_state_path:
        raise RuntimeError(
            "AllTrails blocked automated access. Use --headed --pause-for-captcha once, "
            "then --storage-state .cache/alltrails_storage.json"
        )

    if blocked or not _page_has_listing_signals(page, discovery=discovery):
        hint = (
            "Open Chrome (debug port 9222), go to the Parks page with the Top 50 list visible, "
            "solve any captcha, then re-run with:\n"
            "  --use-current-tab --pause-for-captcha\n"
            "Or use your previous A/1 URL if that worked:\n"
            '  "https://www.alltrails.com/directory/parks/A/1" --discovery all'
        )
        raise RuntimeError(f"Still blocked or no park links on {target!r}.\n{hint}")


def _scroll_listing_page(
    page: Any,
    *,
    max_rounds: int,
    expected_min: int,
    scroll_pause_ms: int,
    count_js: str,
    label: str,
) -> None:
    prev_count = 0
    stable_rounds = 0
    for rnd in range(max_rounds):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(scroll_pause_ms)
        clicked = _click_show_more_if_present(page)
        if clicked:
            page.wait_for_timeout(scroll_pause_ms + 400)
        count = int(page.evaluate(count_js))
        if rnd % 5 == 0 or count != prev_count:
            print(
                f"[playwright] {label} round={rnd} links={count} clicked_show_more={clicked}",
                flush=True,
            )
        if count == prev_count and not clicked:
            stable_rounds += 1
        else:
            stable_rounds = 0
        prev_count = count
        if expected_min > 0 and stable_rounds >= 4 and count >= expected_min:
            break
        if stable_rounds >= 8 and count > 0:
            break


def _release_page_memory(page: Any) -> None:
    """Drop heavy DOM / navigation state before closing a tab."""
    try:
        page.goto("about:blank", wait_until="commit", timeout=15_000)
    except Exception:
        pass
    try:
        page.evaluate(
            """() => {
              try { window.stop(); } catch (e) {}
              if (window.gc) window.gc();
            }"""
        )
    except Exception:
        pass


def _extract_trail_rows_from_page(page: Any, *, compact: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw = page.evaluate(_EXTRACT_TRAILS_COMPACT_JS if compact else _EXTRACT_TRAILS_JS)
    if not isinstance(raw, list):
        return rows
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = _normalize_trail_url(str(item.get("url") or ""))
        if not _TRAIL_HREF_RE.search(url):
            continue
        row: dict[str, Any] = {
            "url": url,
            "name": str(item.get("name") or "").strip(),
            "difficulty": item.get("difficulty"),
            "length_mi": item.get("distance_mi"),
            "rating": item.get("rating"),
        }
        if not compact:
            row["listing_blurb"] = item.get("listing_blurb")
        rows.append(row)
    return rows


def discover_park_urls_on_page(
    page: Any,
    *,
    discovery: str = "top-50",
    limit: int | None = None,
) -> list[dict[str, str]]:
    """
    ``discovery``:
      - ``top-50`` — only the curated **Top 50 Parks** section (default)
      - ``all`` — every ``/parks/...`` link on the page
    """
    from src.wanderly.alltrails_directory import (
        merge_park_discovery_rows,
        parse_park_urls_from_html,
        parse_top_50_parks_from_html,
    )

    mode = (discovery or "top-50").strip().lower()
    cap = 50 if mode in ("top-50", "top50", "top_50") else limit

    js_rows: list[dict[str, str]] = []
    if mode in ("top-50", "top50", "top_50"):
        raw = page.evaluate(_EXTRACT_TOP_50_PARKS_JS)
    else:
        raw = page.evaluate(_EXTRACT_PARK_LINKS_JS)
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("url"):
                js_rows.append(
                    {
                        "url": str(item.get("url") or ""),
                        "name": str(item.get("name") or "").strip(),
                    }
                )

    html_rows: list[dict[str, str]] = []
    min_js = 10 if mode in ("top-50", "top50", "top_50") else 3
    if len(js_rows) < min_js:
        if mode in ("top-50", "top50", "top_50"):
            html_rows = parse_top_50_parks_from_html(page.content())
        else:
            html_rows = [
                {"url": r["url"], "name": r.get("name") or ""}
                for r in parse_park_urls_from_html(page.content())
            ]

    rows = merge_park_discovery_rows(js_rows, html_rows, limit=cap)
    if mode in ("top-50", "top50", "top_50") and limit is not None:
        return rows[:limit]
    return rows


def crawl_park_trails_on_page(
    page: Any,
    park_url: str,
    *,
    max_rounds: int = 80,
    expected_min: int = 50,
    scroll_pause_ms: int = 900,
    timeout_ms: int = 90_000,
    compact: bool = False,
    release_after: bool = True,
) -> list[dict[str, Any]]:
    """Paginate one park listing in an existing Playwright page."""
    park_url = park_url.strip()
    page.set_default_timeout(timeout_ms)
    page.goto(park_url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(2000)
    _dismiss_consent(page)
    _scroll_listing_page(
        page,
        max_rounds=max_rounds,
        expected_min=expected_min,
        scroll_pause_ms=scroll_pause_ms,
        count_js=_COUNT_TRAILS_JS,
        label="park",
    )
    rows = _extract_trail_rows_from_page(page, compact=compact)
    print(f"[playwright] park {park_url!r} -> {len(rows)} trails", flush=True)
    if release_after:
        _release_page_memory(page)
    return rows


def crawl_directory_parks_batch_playwright(
    directory_urls: list[str],
    *,
    limit: int = 50,
    discovery: str = "top-50",
    headless: bool = True,
    max_rounds: int = 80,
    expected_min: int = 50,
    directory_scroll_rounds: int = 25,
    scroll_pause_ms: int = 900,
    timeout_ms: int = 90_000,
    storage_state_path: str | None = None,
    save_storage_state_path: str | None = None,
    pause_for_captcha: bool = False,
    browser_channel: str | None = "chrome",
    connect_cdp_url: str | None = None,
    delay_between_parks_s: float = 1.5,
    use_current_tab: bool = False,
) -> list[dict[str, str]]:
    """
    Open directory page(s), collect up to ``limit`` park URLs (DOM order), return rows.

    Does not crawl trails — use with ``scripts/crawl_alltrails_park_directory.py``.
    ``use_current_tab``: with CDP, use the open Chrome tab (no forced navigation).
    """
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    from src.wanderly.alltrails_directory import merge_park_discovery_rows, parse_listing_page_url

    dirs = [parse_listing_page_url(u) for u in directory_urls if (u or "").strip()]
    if not dirs:
        raise ValueError("at least one directory URL is required")

    scroll_rounds = directory_scroll_rounds
    if (discovery or "").strip().lower() in ("top-50", "top50", "top_50"):
        scroll_rounds = min(directory_scroll_rounds, 3)

    launch_kwargs: dict[str, Any] = {"headless": headless}
    if browser_channel:
        launch_kwargs["channel"] = browser_channel
    ctx_kwargs: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}, "locale": "en-US"}
    if storage_state_path and Path(storage_state_path).is_file():
        ctx_kwargs["storage_state"] = storage_state_path

    discovered_groups: list[list[dict[str, str]]] = []
    with sync_playwright() as p:
        using_cdp = bool(connect_cdp_url)
        if using_cdp:
            browser, page = connect_cdp_browser(
                p,
                connect_cdp_url,
                target_url=dirs[0],
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

        _ensure_page_ready(
            page,
            dirs[0],
            timeout_ms=timeout_ms,
            using_cdp=using_cdp,
            pause_for_captcha=pause_for_captcha,
            headless=headless,
            storage_state_path=storage_state_path,
            use_current_tab=use_current_tab,
            discovery=discovery,
        )
        _dismiss_consent(page)
        if pause_for_captcha:
            print("[playwright] Press Enter after captcha/consent is cleared...", flush=True)
            input()

        for dir_url in dirs:
            print(f"[playwright] listing {dir_url!r} discovery={discovery!r}", flush=True)
            if not (use_current_tab and using_cdp and dir_url in (page.url or "")):
                page.goto(dir_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2000)
            if scroll_rounds > 0:
                _scroll_listing_page(
                    page,
                    max_rounds=scroll_rounds,
                    expected_min=0,
                    scroll_pause_ms=scroll_pause_ms,
                    count_js=_COUNT_PARK_LINKS_JS,
                    label="listing",
                )
            group = discover_park_urls_on_page(page, discovery=discovery, limit=limit)
            print(f"[playwright] listing {dir_url!r} -> {len(group)} park links", flush=True)
            discovered_groups.append(group)
            if delay_between_parks_s > 0:
                page.wait_for_timeout(int(delay_between_parks_s * 1000))

        if save_storage_state_path:
            Path(save_storage_state_path).parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=save_storage_state_path)

        if using_cdp:
            browser.close()
        else:
            browser.close()

    parks = merge_park_discovery_rows(*discovered_groups, limit=limit)
    print(f"[playwright] discovered {len(parks)} park URLs (limit={limit})", flush=True)
    return parks


def crawl_directory_and_parks_batch_playwright(
    directory_urls: list[str],
    *,
    limit: int = 50,
    discovery: str = "top-50",
    headless: bool = True,
    max_rounds: int = 80,
    expected_min: int = 50,
    directory_scroll_rounds: int = 25,
    scroll_pause_ms: int = 900,
    timeout_ms: int = 90_000,
    storage_state_path: str | None = None,
    save_storage_state_path: str | None = None,
    pause_for_captcha: bool = False,
    browser_channel: str | None = "chrome",
    connect_cdp_url: str | None = None,
    delay_between_parks_s: float = 2.0,
    on_park_crawled: Any | None = None,
    should_crawl_park: Any | None = None,
    fresh_page_per_park: bool = True,
    compact_trails: bool = True,
    gc_every_n_parks: int = 5,
    use_current_tab: bool = False,
) -> list[dict[str, Any]]:
    """
    Discover parks from directory URL(s), then crawl each park's trail listing.

    ``on_park_crawled``: ``(park_url, trail_rows, listing_name) -> None`` — rows are
    discarded after the callback returns; write catalogs inside the callback.

    ``fresh_page_per_park``: open/close a browser tab per park (recommended for CDP).
    ``use_current_tab``: with CDP, discover from the tab you already opened in Chrome.
    Returns per-park summary dicts (counts only, no trail payloads).
    """
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    from src.wanderly.alltrails_directory import merge_park_discovery_rows, parse_listing_page_url

    dirs = [parse_listing_page_url(u) for u in directory_urls if (u or "").strip()]
    if not dirs:
        raise ValueError("at least one directory URL is required")

    scroll_rounds = directory_scroll_rounds
    if (discovery or "").strip().lower() in ("top-50", "top50", "top_50"):
        scroll_rounds = min(directory_scroll_rounds, 3)

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
                target_url=dirs[0],
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

        _ensure_page_ready(
            page,
            dirs[0],
            timeout_ms=timeout_ms,
            using_cdp=using_cdp,
            pause_for_captcha=pause_for_captcha,
            headless=headless,
            storage_state_path=storage_state_path,
            use_current_tab=use_current_tab,
            discovery=discovery,
        )
        _dismiss_consent(page)
        if pause_for_captcha:
            print("[playwright] Press Enter after captcha/consent is cleared...", flush=True)
            input()

        discovered_groups: list[list[dict[str, str]]] = []
        for dir_url in dirs:
            print(f"[playwright] listing {dir_url!r} discovery={discovery!r}", flush=True)
            if not (use_current_tab and using_cdp and dir_url in (page.url or "")):
                page.goto(dir_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2000)
            if scroll_rounds > 0:
                _scroll_listing_page(
                    page,
                    max_rounds=scroll_rounds,
                    expected_min=0,
                    scroll_pause_ms=scroll_pause_ms,
                    count_js=_COUNT_PARK_LINKS_JS,
                    label="listing",
                )
            discovered_groups.append(discover_park_urls_on_page(page, discovery=discovery, limit=limit))

        parks = merge_park_discovery_rows(*discovered_groups, limit=limit)
        del discovered_groups
        print(f"[playwright] crawling trails for {len(parks)} parks", flush=True)

        if fresh_page_per_park:
            try:
                page.close()
            except Exception:
                pass

        for idx, park in enumerate(parks, start=1):
            park_url = park["url"]
            print(f"[playwright] park {idx}/{len(parks)} {park_url}", flush=True)
            if should_crawl_park is not None and not should_crawl_park(park_url):
                print(f"[playwright] skip {park_url!r}", flush=True)
                summaries.append(
                    {
                        "park_url": park_url,
                        "park_name": park.get("name") or "",
                        "trail_count": 0,
                        "ok": True,
                        "skipped": True,
                        "error": None,
                    }
                )
                continue
            park_page = context.new_page() if fresh_page_per_park else page
            trail_count = 0
            try:
                rows = crawl_park_trails_on_page(
                    park_page,
                    park_url,
                    max_rounds=max_rounds,
                    expected_min=expected_min,
                    scroll_pause_ms=scroll_pause_ms,
                    timeout_ms=timeout_ms,
                    compact=compact_trails,
                    release_after=True,
                )
                trail_count = len(rows)
                if on_park_crawled:
                    on_park_crawled(park_url, rows, park.get("name") or "")
                del rows
                summaries.append(
                    {
                        "park_url": park_url,
                        "park_name": park.get("name") or "",
                        "trail_count": trail_count,
                        "ok": True,
                        "error": None,
                    }
                )
            except Exception as exc:
                print(f"[playwright] park failed {park_url!r}: {exc!r}", flush=True)
                summaries.append(
                    {
                        "park_url": park_url,
                        "park_name": park.get("name") or "",
                        "trail_count": 0,
                        "ok": False,
                        "error": repr(exc),
                    }
                )
            finally:
                if fresh_page_per_park:
                    try:
                        _release_page_memory(park_page)
                        park_page.close()
                    except Exception:
                        pass
            if gc_every_n_parks > 0 and idx % gc_every_n_parks == 0:
                gc.collect()
            if delay_between_parks_s > 0 and idx < len(parks):
                time.sleep(delay_between_parks_s)

        if save_storage_state_path:
            Path(save_storage_state_path).parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=save_storage_state_path)

        if using_cdp:
            browser.close()
        else:
            browser.close()

    return summaries


def crawl_park_trails_playwright(
    park_url: str,
    *,
    headless: bool = True,
    max_rounds: int = 80,
    expected_min: int = 200,
    scroll_pause_ms: int = 900,
    timeout_ms: int = 90_000,
    storage_state_path: str | None = None,
    save_storage_state_path: str | None = None,
    pause_for_captcha: bool = False,
    browser_channel: str | None = "chrome",
    connect_cdp_url: str | None = None,
) -> list[dict[str, Any]]:
    """
  Load an AllTrails park listing and return trail dicts with url, name, difficulty, distance_mi.

  Paginates via scroll + "Show more" until link count stabilizes or ``max_rounds``.
    """
    from playwright.sync_api import sync_playwright

    park_url = park_url.strip()
    t0 = time.perf_counter()
    rows: list[dict[str, Any]] = []

    from pathlib import Path

    launch_kwargs: dict[str, Any] = {"headless": headless}
    if browser_channel:
        launch_kwargs["channel"] = browser_channel

    ctx_kwargs: dict[str, Any] = {
        "viewport": {"width": 1280, "height": 900},
        "locale": "en-US",
    }
    if storage_state_path and Path(storage_state_path).is_file():
        ctx_kwargs["storage_state"] = storage_state_path
        print(f"[playwright] using storage_state={storage_state_path!r}", flush=True)

    with sync_playwright() as p:
        using_cdp = bool(connect_cdp_url)
        if using_cdp:
            browser, page = connect_cdp_browser(
                p,
                connect_cdp_url,
                target_url=park_url,
                use_current_tab=True,
            )
            context = page.context
            if park_url not in (page.url or ""):
                print(f"[playwright] navigating CDP tab to {park_url!r}", flush=True)
                page.goto(park_url, wait_until="domcontentloaded", timeout=timeout_ms)
        else:
            try:
                browser = p.chromium.launch(**launch_kwargs)
            except Exception as exc:
                print(
                    f"[playwright] channel={browser_channel!r} failed ({exc!r}); "
                    "falling back to bundled Chromium",
                    flush=True,
                )
                browser = p.chromium.launch(headless=headless)
            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()
            print(f"[playwright] goto {park_url!r} headless={headless}", flush=True)
            page.goto(park_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.set_default_timeout(timeout_ms)
        page.wait_for_timeout(3000)

        if _page_is_blocked(page.content(), page.title()) and not using_cdp:
            if pause_for_captcha or not headless:
                print(
                    "[playwright] DataDome/captcha detected. Complete it in the browser window, "
                    "then press Enter here to continue...",
                    flush=True,
                )
                if headless:
                    raise RuntimeError(
                        "AllTrails returned a captcha page in headless mode. "
                        "Re-run with: --headed --pause-for-captcha"
                    )
                input()
                page.goto(park_url, wait_until="networkidle", timeout=timeout_ms)
                page.wait_for_timeout(3000)
            elif not storage_state_path:
                browser.close()
                raise RuntimeError(
                    "AllTrails blocked automated access (DataDome captcha). Options:\n"
                    "  1) python scripts/crawl_alltrails_park_catalog.py --method playwright "
                    "--headed --pause-for-captcha --save-storage .cache/alltrails_storage.json\n"
                    "  2) Re-run with --storage-state .cache/alltrails_storage.json after step 1\n"
                    "  3) Log in via Chrome once; use --channel chrome with saved storage state"
                )

        if _page_is_blocked(page.content(), page.title()) and not using_cdp:
            browser.close()
            raise RuntimeError("Still blocked after captcha wait — storage state may be invalid.")

        if _page_is_blocked(page.content(), page.title()) and using_cdp:
            raise RuntimeError(
                "Connected Chrome tab still shows DataDome. Solve captcha in that window, "
                "load the full park listing, then re-run."
            )

        _dismiss_consent(page)

        if pause_for_captcha and save_storage_state_path:
            print(
                "[playwright] Browse OK? Press Enter to start scrolling the trail list...",
                flush=True,
            )
            input()

        rows = crawl_park_trails_on_page(
            page,
            park_url,
            max_rounds=max_rounds,
            expected_min=expected_min,
            scroll_pause_ms=scroll_pause_ms,
            timeout_ms=timeout_ms,
        )

        if save_storage_state_path:
            Path(save_storage_state_path).parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=save_storage_state_path)
            print(f"[playwright] saved storage_state -> {save_storage_state_path!r}", flush=True)

        if using_cdp:
            browser.close()  # disconnect only; leaves user's Chrome running
        else:
            browser.close()

    elapsed = time.perf_counter() - t0
    print(f"[playwright] extracted {len(rows)} trails in {elapsed:.1f}s", flush=True)
    return rows
