"""Tests for Expedia SRP anchor ordering used by hotel snapshot parsing."""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root contains ``expedia-listing-cli`` as a sibling package in dev.
_ROOT = Path(__file__).resolve().parents[2]
_CLI = _ROOT / "expedia-listing-cli"
if str(_CLI) not in sys.path:
    sys.path.insert(0, str(_CLI))

from expedia_listing_cli.extract_heuristic import extract_candidate_links  # noqa: E402


def test_extract_candidate_links_prefers_property_urls_over_hotel_search_chrome() -> None:
    html = """
    <html><body>
      <nav><a href="https://www.expedia.com/Hotel-Search?foo=1">Search again</a></nav>
      <div class="listing">
        <a href="https://www.expedia.com/Vancouver-Hotels-The-Fairmont.h123.Hotel-Information">
          Fairmont Vancouver
        </a>
      </div>
    </body></html>
    """
    links = extract_candidate_links(html, limit=5)
    assert links, "expected at least one candidate"
    assert "Hotel-Information" in links[0]["href"]
    assert "Hotel-Search" not in links[0]["href"]
