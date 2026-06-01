"""
Direct HTTP fetch for AllTrails park listing pages (no Playwright).

AllTrails often returns **403 + DataDome** to scripts from datacenter IPs. This module still
implements the fetch + parse path for:
  - environments where it works (home IP, VPN, future allowlist)
  - HTML you save from a normal browser session (--html-file)

Optional: ``pip install curl_cffi`` for Chrome TLS impersonation (may still be blocked).
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

import httpx

_TRAIL_PATH_RE = re.compile(
    r"https?://(?:www\.)?alltrails\.com/(?:explore/)?trail/([a-z]{2})/([^/\s\"'?]+)/([^/\s\"'?]+)",
    re.I,
)
_TRAIL_REL_RE = re.compile(
    r"/(?:explore/)?trail/([a-z]{2})/([^/\s\"'?]+)/([^/\s\"'?]+)",
    re.I,
)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.alltrails.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _normalize_url(url: str) -> str:
    u = (url or "").strip().split("?")[0].rstrip("/")
    if u and not u.startswith("http"):
        u = urljoin("https://www.alltrails.com", u)
    return u


def is_blocked_response(html: str, status_code: int) -> bool:
    if status_code == 403:
        return True
    low = (html or "")[:12_000].lower()
    if len(html or "") < 4000 and "alltrails.com" in low:
        if "captcha" in low or "datadome" in low or "captcha-delivery" in low:
            return True
        if "please enable js" in low:
            return True
    return "captcha-delivery.com" in low or "geo.captcha-delivery.com" in low


def fetch_url_httpx(url: str, *, timeout_s: float = 60.0) -> tuple[int, str, str]:
    """Returns (status_code, html, fetch_method)."""
    with httpx.Client(
        timeout=timeout_s,
        follow_redirects=True,
        headers=dict(_DEFAULT_HEADERS),
    ) as client:
        res = client.get(url)
        return res.status_code, res.text, "httpx"


def fetch_url_curl_cffi(url: str, *, timeout_s: float = 60.0, impersonate: str = "chrome120") -> tuple[int, str, str]:
    """Returns (status_code, html, fetch_method). Raises ImportError if curl_cffi missing."""
    from curl_cffi import requests as curl_requests

    res = curl_requests.get(
        url,
        impersonate=impersonate,
        timeout=timeout_s,
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    return int(res.status_code), str(res.text), f"curl_cffi:{impersonate}"


def fetch_park_page(
    park_url: str,
    *,
    timeout_s: float = 60.0,
    try_curl_cffi: bool = True,
) -> dict[str, Any]:
    """
    Try direct HTTP fetch. On success returns ``{ok, status_code, html, method}``.
    On block returns ``{ok: False, blocked: True, ...}`` with guidance.
    """
    park_url = park_url.strip()
    attempts: list[dict[str, Any]] = []

    status, html, method = fetch_url_httpx(park_url, timeout_s=timeout_s)
    attempts.append({"method": method, "status_code": status, "html_len": len(html)})
    if not is_blocked_response(html, status) and status < 400:
        return {
            "ok": True,
            "status_code": status,
            "html": html,
            "method": method,
            "attempts": attempts,
        }

    if try_curl_cffi:
        try:
            status2, html2, method2 = fetch_url_curl_cffi(park_url, timeout_s=timeout_s)
            attempts.append({"method": method2, "status_code": status2, "html_len": len(html2)})
            if not is_blocked_response(html2, status2) and status2 < 400:
                return {
                    "ok": True,
                    "status_code": status2,
                    "html": html2,
                    "method": method2,
                    "attempts": attempts,
                }
            html, status, method = html2, status2, method2
        except ImportError:
            attempts.append({"method": "curl_cffi", "error": "not installed"})
        except Exception as exc:
            attempts.append({"method": "curl_cffi", "error": repr(exc)})

    return {
        "ok": False,
        "blocked": is_blocked_response(html, status),
        "status_code": status,
        "html": html[:2000],
        "method": method,
        "attempts": attempts,
        "hint": (
            "AllTrails blocked automated HTTP from this network (DataDome 403). "
            "Use a normal browser: open the park page, scroll to load all trails, "
            "then save HTML (Ctrl+S) or use DevTools Network tab to copy the listing response. "
            "Run: python scripts/crawl_alltrails_park_catalog.py --html-file saved_page.html"
        ),
    }


def parse_trails_from_html(html: str) -> list[dict[str, Any]]:
    """
    Extract trail rows from park listing HTML (saved page or successful fetch).

    Returns dicts: url, name, difficulty?, distance_mi?, rating?, listing_blurb?
    """
    if not html:
        return []
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []

    def add(url: str, name: str, blurb: str = "") -> None:
        url = _normalize_url(url)
        if not _TRAIL_REL_RE.search(url) or url.lower() in seen:
            return
        seen.add(url.lower())
        text = blurb or ""
        difficulty = None
        m = re.search(r"\b(Easy|Moderate|Hard|Difficult|Strenuous)\b", text, re.I)
        if m:
            difficulty = m.group(1)
        distance_mi = None
        mi = re.search(r"([\d.]+)\s*mi\b", text, re.I)
        if mi:
            try:
                distance_mi = float(mi.group(1))
            except ValueError:
                pass
        rating = None
        rm = re.search(r"([\d.]+)\s*\(\s*[\d,]+\s*\)", text)
        if rm:
            try:
                rating = float(rm.group(1))
            except ValueError:
                pass
        if not name or len(name) < 2:
            slug = url.rsplit("/", 1)[-1]
            name = slug.replace("-", " ").title()
        rows.append(
            {
                "url": url,
                "name": name[:200],
                "difficulty": difficulty,
                "length_mi": distance_mi,
                "rating": rating,
                "listing_blurb": text[:280] if text else None,
            }
        )

    for m in _TRAIL_PATH_RE.finditer(html):
        add(m.group(0), m.group(3).replace("-", " ").title())

    # Markdown-style headings from readable exports: "### #1 - Trail Name"
    for m in re.finditer(
        r"#{1,4}\s*#?\d*\s*-\s*([^\n]+)\n+([\d.]+)\s*\([^)]+\)[·•]\s*(\w+)[·•]\s*([\d.]+)\s*mi",
        html,
        re.I,
    ):
        name = m.group(1).strip()
        # find nearest trail url for this name in html window
        start = max(0, m.start() - 800)
        window = html[start : m.end() + 1200]
        um = _TRAIL_PATH_RE.search(window)
        if um:
            blurb = f"{m.group(2)} ({m.group(3)}) {m.group(4)} mi"
            add(um.group(0), name, blurb)

    for m in _TRAIL_REL_RE.finditer(html):
        url = _normalize_url(m.group(0))
        if url.lower() in seen:
            continue
        add(url, m.group(3).replace("-", " ").title())

    return rows


def crawl_park_trails_http(
    park_url: str,
    *,
    timeout_s: float = 60.0,
    try_curl_cffi: bool = True,
) -> list[dict[str, Any]]:
    """Fetch park page via HTTP and parse trails; raises RuntimeError if blocked."""
    result = fetch_park_page(park_url, timeout_s=timeout_s, try_curl_cffi=try_curl_cffi)
    if not result.get("ok"):
        msg = str(result.get("hint") or "HTTP fetch failed")
        raise RuntimeError(f"{msg}\nAttempts: {json.dumps(result.get('attempts'), ensure_ascii=True)}")
    html = str(result.get("html") or "")
    rows = parse_trails_from_html(html)
    print(
        f"[http] method={result.get('method')} status={result.get('status_code')} "
        f"html_len={len(html)} trails_parsed={len(rows)}",
        flush=True,
    )
    return rows
