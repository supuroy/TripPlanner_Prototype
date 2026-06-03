"""Park hero catalog + AllTrails HTML parse + NPS park image helpers."""

from __future__ import annotations

from unittest.mock import patch

from src.wanderly.alltrails_park_hero import normalize_hero_image_url, parse_park_hero_from_html
from src.wanderly.nps_provider import fetch_nps_park_hero_urls, normalize_park_image
from src.wanderly.park_hero_catalog import (
    offline_park_hero_url,
    park_code_from_destination,
    park_hero_urls_for_destination,
    reset_park_hero_caches_for_tests,
    save_park_heroes_doc,
)


def test_parse_park_hero_from_html_og_image() -> None:
    html = """
    <html><head>
    <meta property="og:image" content="https://cdn.example.com/park.jpg" />
    </head></html>
    """
    assert parse_park_hero_from_html(html) == "https://cdn.example.com/park.jpg"


def test_normalize_hero_image_url_protocol_relative() -> None:
    assert normalize_hero_image_url("//cdn.alltrails.com/hero.jpg") == "https://cdn.alltrails.com/hero.jpg"


def test_park_code_from_destination_grounding() -> None:
    dest = {
        "id": "trail:us-utah-x",
        "name": "Some Trail",
        "grounding": {"park_code": "brca", "status": "catalog", "trail_id": "t1"},
    }
    assert park_code_from_destination(dest) == "brca"


def test_offline_park_hero_lookup(tmp_path, monkeypatch) -> None:
    reset_park_hero_caches_for_tests()
    heroes_path = tmp_path / "park_heroes.json"
    heroes_path.write_text(
        '{"catalog_version":1,"parks":{"brca":{"hero_image_url":"https://cdn.example.com/brca.jpg"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr("src.wanderly.park_hero_catalog._PARK_HEROES_PATH", heroes_path)
    reset_park_hero_caches_for_tests()
    assert offline_park_hero_url("brca") == "https://cdn.example.com/brca.jpg"


def test_park_hero_urls_offline_before_nps(tmp_path, monkeypatch) -> None:
    reset_park_hero_caches_for_tests()
    heroes_path = tmp_path / "park_heroes.json"
    heroes_path.write_text(
        '{"catalog_version":1,"parks":{"yell":{"hero_image_url":"https://cdn.example.com/yell.jpg"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr("src.wanderly.park_hero_catalog._PARK_HEROES_PATH", heroes_path)
    reset_park_hero_caches_for_tests()

    dest = {"id": "t1", "grounding": {"park_code": "yell", "status": "catalog"}}
    urls, source = park_hero_urls_for_destination(dest)
    assert source == "offline_alltrails"
    assert urls == ["https://cdn.example.com/yell.jpg"]

    with patch("src.wanderly.nps_provider.fetch_nps_park_hero_urls") as nps_mock:
        nps_mock.return_value = ["https://nps.gov/yell.jpg"]
        dest2 = {"id": "t2", "grounding": {"park_code": "yose", "status": "catalog"}}
        urls2, source2 = park_hero_urls_for_destination(dest2)
        assert source2 == "nps_live"
        assert urls2 == ["https://nps.gov/yell.jpg"]
        nps_mock.assert_called_once()


def test_normalize_park_image() -> None:
    row = normalize_park_image({"url": "https://nps.gov/photo.jpg", "title": "View"})
    assert row and row["url"].startswith("https://")


def test_fetch_nps_park_hero_urls() -> None:
    with patch("src.wanderly.nps_provider.nps_configured", return_value=True):
        with patch(
            "src.wanderly.nps_provider._get",
            return_value=[{"images": [{"url": "https://nps.gov/a.jpg"}, {"url": "https://nps.gov/b.jpg"}]}],
        ):
            urls = fetch_nps_park_hero_urls("yell", max_images=2)
            assert urls == ["https://nps.gov/a.jpg", "https://nps.gov/b.jpg"]
