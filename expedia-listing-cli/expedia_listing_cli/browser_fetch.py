"""Fetch rendered HTML with Playwright (closer to what users see than raw HTTP)."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# Expedia flight SRP hydrates fares asynchronously; a fixed short settle often yields shell HTML.
_FLIGHT_FARE_RE = re.compile(r"\$\s*[1-9][\d,]*(?:\.\d{2})?")


def _is_flights_search_url(url: str) -> bool:
    try:
        path = (urlparse(url).path or "").lower()
    except Exception:
        return "flights-search" in (url or "").lower()
    return path.rstrip("/").endswith("flights-search") or "flights-search?" in (url or "").lower()


def _flight_fares_visible(html: str) -> bool:
    return _FLIGHT_FARE_RE.search(html or "") is not None


def fetch_page_html(url: str, *, timeout_ms: int | None = None, headless: bool = True) -> str:
    """
    Load ``url`` and return rendered HTML.

    We use ``domcontentloaded`` plus a short **settle** wait (non-flight pages). Expedia and similar sites
    rarely reach Playwright's ``networkidle`` (analytics, beacons, long-lived connections),
    so waiting for it caused multi‑second hangs or empty/incomplete parses.
    Opt-in: set ``BROWSER_WAIT_NETWORKIDLE=1`` to restore the old behavior.

    For ``/Flights-Search`` URLs we **poll** ``page.content()`` until a non-zero **$** fare token appears
    (or ``BROWSER_FLIGHT_POLL_MS`` elapses), because the flights PWA often renders prices after the first paint.
    Tune with ``BROWSER_FLIGHT_POLL_MS`` (default 60000) and ``BROWSER_FLIGHT_POLL_STEP_MS`` (default 2000).
    """
    timeout_ms = timeout_ms or int(os.environ.get("BROWSER_TIMEOUT_MS", "90000"))
    networkidle_ms = int(os.environ.get("BROWSER_NETWORKIDLE_MS", "8000"))
    settle_ms = int(os.environ.get("BROWSER_SETTLE_MS", "3000"))
    flight_poll_ms = int(os.environ.get("BROWSER_FLIGHT_POLL_MS", "60000"))
    flight_step_ms = int(os.environ.get("BROWSER_FLIGHT_POLL_STEP_MS", "2000"))
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise SystemExit(
            "Install playwright: pip install -r requirements.txt && playwright install chromium"
        ) from e

    _log("Launching Chromium …")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            _log(f"Navigating (timeout {timeout_ms} ms) …")
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            wait_net = os.environ.get("BROWSER_WAIT_NETWORKIDLE", "").strip().lower() in ("1", "true", "yes")
            if wait_net:
                _log(f"Waiting for network idle (up to {networkidle_ms} ms) …")
                try:
                    page.wait_for_load_state("networkidle", timeout=networkidle_ms)
                except Exception as exc:
                    _log(f"networkidle skipped: {exc!s}")
            elif settle_ms > 0 and not _is_flights_search_url(url):
                _log(f"Settling {settle_ms} ms for hydration (no networkidle) …")
                page.wait_for_timeout(settle_ms)

            if _is_flights_search_url(url):
                _log(
                    f"Flights-Search: polling HTML up to {flight_poll_ms} ms "
                    f"(step {flight_step_ms} ms) until fare tokens appear …"
                )
                t_poll = time.perf_counter()
                html = ""
                rounds = max(1, (flight_poll_ms + flight_step_ms - 1) // max(1, flight_step_ms))
                for rnd in range(rounds):
                    html = page.content()
                    if _flight_fares_visible(html):
                        _log(
                            f"Flight fares visible after ~{int((time.perf_counter() - t_poll) * 1000)} ms "
                            f"(poll round {rnd + 1})."
                        )
                        break
                    if rnd < rounds - 1:
                        page.wait_for_timeout(flight_step_ms)
                else:
                    _log(
                        f"Flight poll finished after {int((time.perf_counter() - t_poll) * 1000)} ms "
                        "without matching $ fare pattern (returning last snapshot)."
                    )
            else:
                _log("Reading page HTML …")
                html = page.content()
        finally:
            browser.close()
    _log(f"Got HTML ({len(html):,} chars).")
    return html


def write_snapshot(url: str, out_dir: Path, *, headless: bool = True) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = abs(hash(url)) % (10**9)
    path = out_dir / f"snapshot_{safe}.html"
    path.write_text(fetch_page_html(url, headless=headless), encoding="utf-8")
    return path
