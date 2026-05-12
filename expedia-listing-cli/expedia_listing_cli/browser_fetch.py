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

# Hotel SRP hydrates listing URLs / JSON after first paint (same class of issue as flights).
_HOTEL_SRP_READY_RE = re.compile(
    r"Hotel-Information|/Hotel-Information|/hotels/[^\s\"'<>]+|\"listingUrl\"\s*:\s*\"https:[^\"]*Hotel-Information",
    re.I,
)


def _is_flights_search_url(url: str) -> bool:
    try:
        path = (urlparse(url).path or "").lower()
    except Exception:
        return "flights-search" in (url or "").lower()
    return path.rstrip("/").endswith("flights-search") or "flights-search?" in (url or "").lower()


def _flight_fares_visible(html: str) -> bool:
    return _FLIGHT_FARE_RE.search(html or "") is not None


def _is_hotel_search_url(url: str) -> bool:
    try:
        path = (urlparse(url).path or "").lower()
    except Exception:
        return "hotel-search" in (url or "").lower()
    return path.rstrip("/").endswith("hotel-search") or "hotel-search?" in (url or "").lower()


def _hotel_srp_heuristic_ready(html: str) -> bool:
    """Cheap signal that hotel SRP has listing-shaped content (no BeautifulSoup per poll round)."""
    return bool(html and _HOTEL_SRP_READY_RE.search(html))


def _hotel_listing_links_ready(html: str) -> bool:
    """True when BeautifulSoup finds hotel-shaped anchors (aligns with OTA weak-fetch heuristic)."""
    if not html:
        return False
    try:
        from expedia_listing_cli.extract_heuristic import extract_candidate_links
    except ImportError:
        return False
    try:
        return len(extract_candidate_links(html, limit=4)) > 0
    except Exception:
        return False


def _hotel_poll_ready(html: str) -> bool:
    return _hotel_srp_heuristic_ready(html) or _hotel_listing_links_ready(html)


def fetch_page_html(url: str, *, timeout_ms: int | None = None, headless: bool = True) -> str:
    """
    Load ``url`` and return rendered HTML.

    We use ``domcontentloaded`` plus a short **settle** wait (non-flight / non-hotel-search pages). Expedia
    rarely reaches Playwright's ``networkidle`` (analytics, beacons, long-lived connections), so waiting for
    it caused multi‑second hangs or empty/incomplete parses. Opt-in: ``BROWSER_WAIT_NETWORKIDLE=1``.

    **Flights-Search:** poll ``page.content()`` until a ``$`` fare token matching ``_FLIGHT_FARE_RE`` appears
    (``BROWSER_FLIGHT_POLL_MS`` / ``BROWSER_FLIGHT_POLL_STEP_MS``). If the poll budget expires without a
    match, we **linger** in the same browser session (``BROWSER_FLIGHT_LINGER_ROUNDS`` ×
    ``BROWSER_FLIGHT_LINGER_MS``) before closing — avoids a second full Playwright launch in the caller.

    **Hotel-Search:** after the generic settle, poll until listing signals appear: regex
    ``_HOTEL_SRP_READY_RE`` **or** ``extract_candidate_links`` finds hotel-shaped anchors (same idea as the
    OTA weak-fetch check). Budget: ``BROWSER_HOTEL_POLL_MS`` / ``BROWSER_HOTEL_POLL_STEP_MS`` (defaults 35s / 2s).
    """
    timeout_ms = timeout_ms or int(os.environ.get("BROWSER_TIMEOUT_MS", "90000"))
    networkidle_ms = int(os.environ.get("BROWSER_NETWORKIDLE_MS", "8000"))
    settle_ms = int(os.environ.get("BROWSER_SETTLE_MS", "3000"))
    flight_poll_ms = int(os.environ.get("BROWSER_FLIGHT_POLL_MS", "60000"))
    flight_step_ms = int(os.environ.get("BROWSER_FLIGHT_POLL_STEP_MS", "2000"))
    # Hotel SRP: default cap lowered; exit early when regex OR extract_candidate_links sees listings.
    hotel_poll_ms = int(os.environ.get("BROWSER_HOTEL_POLL_MS", "35000"))
    hotel_step_ms = int(os.environ.get("BROWSER_HOTEL_POLL_STEP_MS", "2000"))
    flight_linger_rounds = max(0, int(os.environ.get("BROWSER_FLIGHT_LINGER_ROUNDS", "2")))
    flight_linger_ms = max(0, int(os.environ.get("BROWSER_FLIGHT_LINGER_MS", "2000")))
    skip_linger_html_min = int(os.environ.get("BROWSER_FLIGHT_SKIP_LINGER_HTML_MIN", "380000"))
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

            html = ""
            if _is_flights_search_url(url):
                _log(
                    f"Flights-Search: polling HTML up to {flight_poll_ms} ms "
                    f"(step {flight_step_ms} ms) until fare tokens appear …"
                )
                t_poll = time.perf_counter()
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
                if html and not _flight_fares_visible(html) and flight_linger_rounds and flight_linger_ms:
                    # Large DOM usually means the app shell + results region loaded; lingering rarely
                    # beats a second full fetch and burns wall time on slow machines.
                    if len(html) >= skip_linger_html_min:
                        _log(
                            f"Flight-Search: skipping linger (HTML already {len(html):,} chars ≥ "
                            f"{skip_linger_html_min:,})."
                        )
                    else:
                        _log(
                            f"Flight-Search: same-session linger {flight_linger_rounds}×{flight_linger_ms} ms "
                            "for late fare paint …"
                        )
                        for r in range(flight_linger_rounds):
                            page.wait_for_timeout(flight_linger_ms)
                            html = page.content()
                            if _flight_fares_visible(html):
                                _log(f"Flight fares visible after linger round {r + 1}.")
                                break
            elif _is_hotel_search_url(url):
                _log(
                    f"Hotel-Search: polling HTML up to {hotel_poll_ms} ms "
                    f"(step {hotel_step_ms} ms) until listing signals appear …"
                )
                t_poll = time.perf_counter()
                rounds = max(1, (hotel_poll_ms + hotel_step_ms - 1) // max(1, hotel_step_ms))
                for rnd in range(rounds):
                    html = page.content()
                    if _hotel_poll_ready(html):
                        _log(
                            f"Hotel listing signals after ~{int((time.perf_counter() - t_poll) * 1000)} ms "
                            f"(poll round {rnd + 1})."
                        )
                        break
                    if rnd < rounds - 1:
                        page.wait_for_timeout(hotel_step_ms)
                else:
                    _log(
                        f"Hotel poll finished after {int((time.perf_counter() - t_poll) * 1000)} ms "
                        "without listing regex match (returning last snapshot)."
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
