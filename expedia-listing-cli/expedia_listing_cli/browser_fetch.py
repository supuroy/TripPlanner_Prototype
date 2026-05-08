"""Fetch rendered HTML with Playwright (closer to what users see than raw HTTP)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def fetch_page_html(url: str, *, timeout_ms: int | None = None, headless: bool = True) -> str:
    timeout_ms = timeout_ms or int(os.environ.get("BROWSER_TIMEOUT_MS", "90000"))
    networkidle_ms = int(os.environ.get("BROWSER_NETWORKIDLE_MS", "20000"))
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
            _log(f"Waiting for network idle (up to {networkidle_ms} ms) …")
            try:
                page.wait_for_load_state("networkidle", timeout=networkidle_ms)
            except Exception as exc:
                _log(f"networkidle skipped: {exc!s}")
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
