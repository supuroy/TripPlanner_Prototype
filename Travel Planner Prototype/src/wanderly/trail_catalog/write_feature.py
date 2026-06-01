"""Write per-feature trail catalog JSON and update ``features_manifest.json``."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_feature_catalog_files(
    root: Path,
    *,
    feature_slug: str,
    feature_name: str,
    listing_url: str,
    entries: list[dict[str, Any]],
    method: str,
    out_path: Path | None = None,
) -> Path:
    """Write ``data/trail_catalog/features/{slug}.json`` and refresh manifest."""
    catalog_root = root / "data" / "trail_catalog"
    slug = feature_slug.lower().strip()
    out = out_path or (catalog_root / "features" / f"{slug}.json")
    written_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc: dict[str, Any] = {
        "catalog_version": 1,
        "catalog_kind": "trail_feature",
        "written_at_utc": written_at,
        "source": {"listing_url": listing_url, "method": method},
        "feature_slug": slug,
        "feature_name": feature_name,
        "trail_count": len(entries),
        "trails": entries,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    manifest_path = catalog_root / "features_manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    features = manifest.get("features")
    if not isinstance(features, list):
        features = []
    row = {
        "feature_slug": slug,
        "feature_name": feature_name,
        "trail_count": len(entries),
        "file": str(out.relative_to(root)).replace("\\", "/"),
        "source_url": listing_url,
        "updated_at_utc": written_at,
    }
    features = [f for f in features if isinstance(f, dict) and f.get("feature_slug") != slug]
    features.append(row)
    manifest["catalog_kind"] = "trail_features"
    manifest["features"] = sorted(features, key=lambda f: str(f.get("feature_slug") or ""))
    manifest["updated_at_utc"] = written_at
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return out
