"""Tests for offline feature-catalog enrichment."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.wanderly.trail_catalog.enrich import (
    enrich_feature_catalog_doc,
    enrich_feature_trail_entry,
    geo_from_trail_id,
    geo_from_source_url,
)
from src.wanderly.trail_catalog.search import search_feature_trails


def test_geo_from_trail_id_utah() -> None:
    g = geo_from_trail_id("waterfall:us-utah-emerald-pools-trail")
    assert g["country_code"] == "us"
    assert g["state_slug"] == "utah"
    assert g["region_path"] == "us/utah"
    assert g["state_name"] == "Utah"


def test_geo_from_source_url() -> None:
    g = geo_from_source_url("https://www.alltrails.com/trail/us/utah/emerald-pools-trail")
    assert g["state_slug"] == "utah"
    assert g["region_path"] == "us/utah"


def test_enrich_feature_trail_entry_adds_state(tmp_path: Path) -> None:
    catalog_root = tmp_path / "catalog"
    (catalog_root / "parks").mkdir(parents=True)
    (catalog_root / "parks" / "zion.json").write_text(
        json.dumps(
            {
                "park_code": "zion",
                "region": "Utah",
                "trails": [
                    {
                        "trail_id": "zion:test",
                        "source_url": "https://www.alltrails.com/trail/us/utah/emerald-pools-trail",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    from src.wanderly.trail_catalog.resolve_park import build_trail_url_to_park_index

    index = build_trail_url_to_park_index(catalog_root)
    row = enrich_feature_trail_entry(
        {
            "trail_id": "waterfall:us-utah-emerald-pools-trail",
            "source_url": "https://www.alltrails.com/trail/us/utah/emerald-pools-trail",
            "name": "Emerald Pools",
        },
        feature_slug="waterfall",
        url_index=index,
    )
    assert row["state_slug"] == "utah"
    assert row.get("park_code") == "zion"


def test_search_feature_trails_state_slug_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog_root = tmp_path / "catalog"
    features = catalog_root / "features"
    features.mkdir(parents=True)
    doc = {
        "feature_slug": "waterfall",
        "trails": [
            {
                "trail_id": "waterfall:us-utah-a-trail",
                "name": "A",
                "state_slug": "utah",
                "distance_mi": 3.0,
            },
            {
                "trail_id": "waterfall:us-california-b-trail",
                "name": "B",
                "state_slug": "california",
                "distance_mi": 5.0,
            },
        ],
    }
    (features / "waterfall.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(
        "src.wanderly.trail_catalog.store._CATALOG_ROOT",
        catalog_root,
    )
    rows = search_feature_trails("waterfall", state_slug="utah", limit=10)
    assert len(rows) == 1
    assert rows[0]["name"] == "A"


def test_enrich_feature_catalog_doc_stats(tmp_path: Path) -> None:
    catalog_root = tmp_path / "catalog"
    features = catalog_root / "features"
    features.mkdir(parents=True)
    (features / "waterfall.json").write_text(
        json.dumps(
            {
                "feature_slug": "waterfall",
                "trails": [
                    {
                        "trail_id": "waterfall:us-utah-x",
                        "source_url": "https://www.alltrails.com/trail/us/utah/x",
                        "name": "X",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    doc = json.loads((features / "waterfall.json").read_text(encoding="utf-8"))
    enriched, stats = enrich_feature_catalog_doc(doc, catalog_root=catalog_root)
    assert stats["state_slug_after"] == 1
    assert enriched["trails"][0]["state_slug"] == "utah"
