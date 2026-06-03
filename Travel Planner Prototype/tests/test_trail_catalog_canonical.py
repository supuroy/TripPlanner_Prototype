"""Canonical trail index and unified catalog search."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.wanderly.trail_catalog.canonical import (
    build_trail_index_doc,
    canonical_trail_id_from_source_url,
)
from src.wanderly.trail_catalog.catalog_query import CatalogQuery, resolve_scope, search_catalog_trails


def test_canonical_trail_id_from_url() -> None:
    url = "https://www.alltrails.com/trail/us/utah/emerald-pools-trail"
    assert canonical_trail_id_from_source_url(url) == "trail:us-utah-emerald-pools-trail"


def test_build_trail_index_dedupes_dimensions(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    (root / "parks").mkdir(parents=True)
    (root / "features").mkdir(parents=True)
    url = "https://www.alltrails.com/trail/us/utah/shared-trail"
    (root / "parks" / "zion.json").write_text(
        json.dumps(
            {
                "park_code": "zion",
                "trails": [
                    {
                        "trail_id": "zion:shared-trail",
                        "source_url": url,
                        "name": "Shared",
                        "distance_mi": 3.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "features" / "waterfall.json").write_text(
        json.dumps(
            {
                "feature_slug": "waterfall",
                "trails": [
                    {
                        "trail_id": "waterfall:us-utah-shared-trail",
                        "source_url": url,
                        "name": "Shared",
                        "state_slug": "utah",
                        "distance_mi": 3.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    doc = build_trail_index_doc(root)
    assert doc["unique_trail_count"] == 1
    trail = doc["trails"][0]
    assert trail["canonical_trail_id"] == "trail:us-utah-shared-trail"
    assert len(trail["aliases"]) == 2


def test_search_catalog_trails_feature_and_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "catalog"
    features = root / "features"
    features.mkdir(parents=True)
    (features / "waterfall.json").write_text(
        json.dumps(
            {
                "feature_slug": "waterfall",
                "trails": [
                    {
                        "trail_id": "waterfall:us-utah-a",
                        "source_url": "https://www.alltrails.com/trail/us/utah/a",
                        "name": "A Falls",
                        "state_slug": "utah",
                        "distance_mi": 2.0,
                    },
                    {
                        "trail_id": "waterfall:us-california-b",
                        "source_url": "https://www.alltrails.com/trail/us/california/b",
                        "name": "B Falls",
                        "state_slug": "california",
                        "distance_mi": 4.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("src.wanderly.trail_catalog.store._CATALOG_ROOT", root)
    monkeypatch.setattr("src.wanderly.trail_catalog.canonical.catalog_root", lambda: root)
    result = search_catalog_trails(
        CatalogQuery(
            feature_slugs=["waterfall"],
            state_slugs=["utah"],
            limit=10,
        ),
    )
    assert result["count"] == 1
    assert result["trails"][0]["canonical_trail_id"] == "trail:us-utah-a"


def test_resolve_scope_finds_utah() -> None:
    scope = resolve_scope("moderate waterfall hikes in Utah under 7 miles")
    assert "utah" in scope.state_slugs
    assert "waterfall" in scope.feature_slugs
