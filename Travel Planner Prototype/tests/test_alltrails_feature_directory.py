"""Unit tests for AllTrails trail-feature directory URL parsing."""

from __future__ import annotations

import pytest

from src.wanderly.alltrails_feature_directory import (
    feature_listing_url_for_slug,
    is_feature_city_listing_path,
    merge_feature_discovery_rows,
    normalize_feature_city_listing_url,
    normalize_feature_listing_url,
    parse_feature_city_links_from_html,
    parse_feature_links_from_html,
    parse_feature_listing_url,
    parse_feature_pagination_urls,
)


def test_normalize_feature_listing_url() -> None:
    url = normalize_feature_listing_url(
        "https://www.alltrails.com/directory/trail-features/waterfall"
    )
    assert url.endswith("/directory/trail-features/waterfall/A/1")


def test_parse_feature_listing_url() -> None:
    meta = parse_feature_listing_url(
        "https://www.alltrails.com/directory/trail-features/hot-springs/A/2"
    )
    assert meta.feature_slug == "hot-springs"
    assert meta.feature_name == "Hot Springs"
    assert meta.page == 2


def test_feature_listing_url_for_slug() -> None:
    assert "camping/A/1" in feature_listing_url_for_slug("camping")


def test_parse_feature_links_from_html() -> None:
    html = """
    <section>Trail features</section>
    <a href="/directory/trail-features/waterfall/A/1">Waterfalls</a>
    <a href="/directory/trail-features/camping">Camping</a>
  """
    rows = parse_feature_links_from_html(html)
    slugs = {r["slug"] for r in rows}
    assert slugs == {"waterfall", "camping"}


def test_merge_feature_discovery_rows_dedupes() -> None:
    a = [{"url": feature_listing_url_for_slug("waterfall"), "slug": "waterfall", "name": "Waterfall"}]
    b = [{"url": feature_listing_url_for_slug("waterfall"), "slug": "waterfall", "name": "Dup"}]
    merged = merge_feature_discovery_rows(a, b)
    assert len(merged) == 1


def test_parse_feature_pagination_urls() -> None:
    html = """
    <a href="/directory/trail-features/waterfall/A/1">1</a>
    <a href="/directory/trail-features/waterfall/A/3">3</a>
    """
    pages = parse_feature_pagination_urls(html, feature_slug="waterfall")
    assert len(pages) == 2
    assert all("waterfall" in u for u in pages)


def test_invalid_feature_url_raises() -> None:
    with pytest.raises(ValueError):
        parse_feature_listing_url("https://www.alltrails.com/parks/us/wyoming/yellowstone-national-park")


def test_feature_city_listing_path() -> None:
    assert is_feature_city_listing_path("/us/tennessee/chattanooga/waterfall", "waterfall")
    assert is_feature_city_listing_path("/us/washington-dc/waterfall", "waterfall")
    assert not is_feature_city_listing_path("/directory/trail-features/waterfall/A/1", "waterfall")


def test_parse_feature_city_links_from_html() -> None:
    html = """
    <a href="/us/tennessee/chattanooga/waterfall">Chattanooga</a>
    <a href="https://www.alltrails.com/us/washington-dc/waterfall">DC</a>
    """
    rows = parse_feature_city_links_from_html(html, feature_slug="waterfall")
    assert len(rows) == 2
    assert normalize_feature_city_listing_url(rows[0]["url"], feature_slug="waterfall").endswith(
        "/chattanooga/waterfall"
    )
