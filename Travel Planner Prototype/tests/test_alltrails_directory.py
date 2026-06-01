"""Tests for AllTrails directory park URL parsing."""

from src.wanderly.alltrails_directory import (
    merge_park_discovery_rows,
    parse_listing_page_url,
    parse_park_urls_from_html,
    parse_top_50_parks_from_html,
)


def test_parse_listing_page_urls() -> None:
    assert (
        parse_listing_page_url("https://www.alltrails.com/directory/parks/A/1")
        == "https://www.alltrails.com/directory/parks/A/1"
    )
    assert parse_listing_page_url("https://www.alltrails.com/us") == "https://www.alltrails.com/us"
    assert (
        parse_listing_page_url("https://www.alltrails.com/directory/parks")
        == "https://www.alltrails.com/directory/parks"
    )


def test_parse_top_50_section_from_html() -> None:
    html = """
    <h2>Top 50 Parks</h2>
    <div>
      <a href="https://www.alltrails.com/parks/us/utah/zion-national-park">Zion</a>
      <a href="https://www.alltrails.com/parks/us/wyoming/yellowstone-national-park">Yellowstone</a>
    </div>
    <h2>Trail Features</h2>
    <a href="https://www.alltrails.com/parks/us/nevada/great-basin-national-park">Should not appear</a>
    """
    rows = parse_top_50_parks_from_html(html)
    assert len(rows) == 2
    assert any("zion" in r["url"] for r in rows)
    assert not any("great-basin" in r["url"] for r in rows)


def test_parse_park_urls_from_html_order() -> None:
    html = """
    <a href="https://www.alltrails.com/parks/us/california/yosemite-national-park">Yosemite</a>
    <a href="/parks/us/utah/zion-national-park">Zion</a>
    <a href="https://www.alltrails.com/parks/us/california/yosemite-national-park">dup</a>
    """
    rows = parse_park_urls_from_html(html)
    assert len(rows) == 2
    assert rows[0]["url"].endswith("/yosemite-national-park")
    assert rows[1]["url"].endswith("/zion-national-park")


def test_merge_park_discovery_limit() -> None:
    a = [{"url": "https://www.alltrails.com/parks/us/wyoming/yellowstone-national-park", "name": ""}]
    b = [{"url": "https://www.alltrails.com/parks/us/california/yosemite-national-park", "name": ""}]
    merged = merge_park_discovery_rows(a, b, limit=1)
    assert len(merged) == 1
    assert "yellowstone" in merged[0]["url"]
