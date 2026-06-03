"""Extract hero cover images from AllTrails park listing pages (offline enrichment)."""

from __future__ import annotations

import re
import time
from typing import Any

_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)'
    r'|content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\'])',
    re.I,
)
_TW_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)'
    r'|content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\'])',
    re.I,
)

_EXTRACT_PARK_HERO_JS = """
() => {
  const pick = (sel) => {
    const el = document.querySelector(sel);
    const v = el?.getAttribute('content') || el?.src || el?.getAttribute('srcset')?.split(/\\s+/)[0];
    return (v || '').trim();
  };
  const og = pick('meta[property="og:image:secure_url"]')
    || pick('meta[property="og:image"]');
  if (og) return og;
  const tw = pick('meta[name="twitter:image"]');
  if (tw) return tw;
  const hero = document.querySelector(
    'img[data-testid="hero-image"], [data-testid="hero"] img, header img, picture img'
  );
  if (hero?.src) return hero.src;
  const srcset = document.querySelector('picture source[srcset]');
  if (srcset) {
    const raw = srcset.getAttribute('srcset') || '';
    const first = raw.split(',')[0]?.trim().split(/\\s+/)[0];
    if (first) return first;
  }
  return null;
}
"""


def normalize_hero_image_url(raw: str | None) -> str | None:
    u = (raw or "").strip()
    if not u:
        return None
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("http://"):
        u = "https://" + u.removeprefix("http://")
    return u if u.startswith("https:") else None


def parse_park_hero_from_html(html: str) -> str | None:
    """Parse og:image / twitter:image from saved or fetched HTML."""
    blob = html or ""
    for pat in (_OG_IMAGE_RE, _TW_IMAGE_RE):
        m = pat.search(blob)
        if m:
            url = m.group(1) or m.group(2)
            nu = normalize_hero_image_url(url)
            if nu:
                return nu
    return None


def extract_park_hero_from_page(page: Any, *, wait_ms: int = 1200) -> str | None:
    """Read hero URL from a live Playwright page."""
    if wait_ms > 0:
        time.sleep(wait_ms / 1000.0)
    try:
        raw = page.evaluate(_EXTRACT_PARK_HERO_JS)
    except Exception:
        raw = None
    if isinstance(raw, str) and raw.strip():
        return normalize_hero_image_url(raw)
    try:
        html = page.content()
    except Exception:
        html = ""
    return parse_park_hero_from_html(html)


def _normalize_page_url(url: str) -> str:
    return (url or "").split("?")[0].rstrip("/")


def _fetch_park_hero_on_page(
    page: Any,
    park_url: str,
    *,
    timeout_ms: int,
    pause_for_captcha: bool,
    using_cdp: bool,
) -> dict[str, Any]:
    from src.wanderly.alltrails_park_crawl import _page_is_blocked

    url = (park_url or "").strip()
    out: dict[str, Any] = {"ok": False, "hero_image_url": None, "method": None, "error": None}
    if not url:
        out["error"] = "missing_url"
        return out

    if _normalize_page_url(url) != _normalize_page_url(page.url or ""):
        print(f"[park_hero] navigating CDP tab to {url!r}", flush=True)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    title = ""
    html = ""
    try:
        title = page.title() or ""
        html = page.content()
    except Exception:
        pass

    if _page_is_blocked(html, title):
        if pause_for_captcha:
            print("[park_hero] blocked — solve captcha in browser, then press Enter", flush=True)
            input()
            html = page.content()
            title = page.title() or ""
        else:
            out["error"] = "blocked_or_captcha"
            return out

    hero = extract_park_hero_from_page(page)
    if not hero and html:
        hero = parse_park_hero_from_html(html)

    if hero:
        out["ok"] = True
        out["hero_image_url"] = hero
        out["method"] = "alltrails_og_cdp" if using_cdp else "alltrails_og_playwright"
    else:
        out["error"] = "no_hero_image_found"

    return out


class CdpParkHeroSession:
    """Reuse one Chrome CDP tab across many park hero fetches."""

    def __init__(
        self,
        connect_cdp_url: str,
        *,
        timeout_ms: int = 90_000,
        pause_for_captcha: bool = False,
    ) -> None:
        self.connect_cdp_url = connect_cdp_url
        self.timeout_ms = timeout_ms
        self.pause_for_captcha = pause_for_captcha
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    def __enter__(self) -> CdpParkHeroSession:
        from playwright.sync_api import sync_playwright

        from src.wanderly.alltrails_park_crawl import connect_cdp_browser

        self._playwright = sync_playwright().start()
        self._browser, self._page = connect_cdp_browser(
            self._playwright,
            self.connect_cdp_url,
            target_url="https://www.alltrails.com/",
            use_current_tab=False,
            allow_blank_tab=True,
        )
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def fetch(self, park_url: str) -> dict[str, Any]:
        if self._page is None:
            return {"ok": False, "hero_image_url": None, "method": None, "error": "cdp_not_connected"}
        return _fetch_park_hero_on_page(
            self._page,
            park_url,
            timeout_ms=self.timeout_ms,
            pause_for_captcha=self.pause_for_captcha,
            using_cdp=True,
        )


def fetch_park_hero_playwright(
    park_url: str,
    *,
    connect_cdp_url: str | None = None,
    headless: bool = True,
    timeout_ms: int = 90_000,
    storage_state_path: str | None = None,
    browser_channel: str | None = "chrome",
    pause_for_captcha: bool = False,
) -> dict[str, Any]:
    """
    Load one AllTrails park page and return ``{ ok, hero_image_url, method, error }``.
    """
    from playwright.sync_api import sync_playwright

    from src.wanderly.alltrails_park_crawl import connect_cdp_browser

    url = (park_url or "").strip()
    out: dict[str, Any] = {"ok": False, "hero_image_url": None, "method": None, "error": None}
    if not url:
        out["error"] = "missing_url"
        return out

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

    with sync_playwright() as p:
        using_cdp = bool(connect_cdp_url)
        if using_cdp:
            browser, page = connect_cdp_browser(
                p,
                connect_cdp_url,
                target_url=url,
                use_current_tab=False,
                allow_blank_tab=True,
            )
            out = _fetch_park_hero_on_page(
                page,
                url,
                timeout_ms=timeout_ms,
                pause_for_captcha=pause_for_captcha,
                using_cdp=True,
            )
            browser.close()
            return out

        try:
            browser = p.chromium.launch(**launch_kwargs)
        except Exception:
            browser = p.chromium.launch(headless=headless)
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        out = _fetch_park_hero_on_page(
            page,
            url,
            timeout_ms=timeout_ms,
            pause_for_captcha=pause_for_captcha,
            using_cdp=False,
        )
        browser.close()

    return out
