"""Tests for AllTrails regions directory URL parsing."""

from __future__ import annotations

import pytest

from src.wanderly.alltrails_region_directory import (
    merge_region_discovery_rows,
    normalize_region_directory_url,
    normalize_us_region_listing_url,
    parse_region_links_from_html,
)


def test_normalize_region_directory_url() -> None:
    u = normalize_region_directory_url("https://www.alltrails.com/directory/regions/A/1")
    assert u.endswith("/directory/regions/A/1")


def test_normalize_us_region_listing_url() -> None:
    u = normalize_us_region_listing_url("/us/utah")
    assert u == "https://www.alltrails.com/us/utah"


def test_parse_region_links_from_html() -> None:
    html = '<a href="/us/utah">Utah</a><a href="/us/california">California</a>'
    rows = parse_region_links_from_html(html, limit=10)
    slugs = {r["slug"] for r in rows}
    assert "utah" in slugs
    assert "california" in slugs


def test_trail_id_for_region_trail() -> None:
    from src.wanderly.alltrails_region_directory import trail_id_for_region_trail

    tid = trail_id_for_region_trail(
        "https://www.alltrails.com/trail/us/utah/angels-landing-trail",
        state_slug="utah",
    )
    assert tid == "region:us-utah-angels-landing-trail"


def test_merge_region_discovery_rows_dedupes() -> None:
    a = [{"url": "https://www.alltrails.com/us/utah", "slug": "utah", "name": "Utah"}]
    b = [{"url": "https://www.alltrails.com/us/utah/", "slug": "utah", "name": "UT"}]
    merged = merge_region_discovery_rows(a, b)
    assert len(merged) == 1
