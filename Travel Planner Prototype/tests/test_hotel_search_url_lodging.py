"""Hotel-Search URLs must request hotel inventory only (not VRBO)."""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from src.wanderly.ota_booking_search import _build_hotel_url, _ensure_hotel_only_lodging_pairs


def test_ensure_hotel_only_lodging_pairs_replaces_vrbo() -> None:
    pairs = [("destination", "X"), ("lodging", "VACATION_RENTAL"), ("adults", "2")]
    out = _ensure_hotel_only_lodging_pairs(pairs)
    lod = [v for k, v in out if k == "lodging"]
    assert lod == ["HOTEL"]


def test_build_hotel_url_includes_lodging_hotel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPEDIA_HOTEL_SEARCH_CATALOG_JSON", raising=False)

    def _no_catalog(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(
        "src.wanderly.ota_booking_search._catalog_path",
        _no_catalog,
    )
    url = _build_hotel_url(
        "Porto Moniz, Madeira, Portugal",
        date(2026, 5, 30),
        date(2026, 5, 31),
        2,
    )
    qs = parse_qs(urlparse(url).query)
    assert qs.get("lodging") == ["HOTEL"]
