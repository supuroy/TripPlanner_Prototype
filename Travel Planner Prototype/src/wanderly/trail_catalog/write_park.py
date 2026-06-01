"""Write per-park trail catalog JSON and update manifest."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_park_catalog_files(
    root: Path,
    *,
    park_url: str,
    park_code: str,
    park_name: str,
    region: str,
    country: str,
    entries: list[dict[str, Any]],
    method: str,
    out_path: Path | None = None,
) -> Path:
    """Write ``data/trail_catalog/parks/{park_code}.json`` and refresh ``manifest.json``."""
    catalog_root = root / "data" / "trail_catalog"
    out = out_path or (catalog_root / "parks" / f"{park_code.lower()}.json")
    written_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc: dict[str, Any] = {
        "catalog_version": 1,
        "written_at_utc": written_at,
        "source": {"park_url": park_url, "method": method},
        "park_code": park_code,
        "park_name": park_name,
        "region": region,
        "country": country,
        "trail_count": len(entries),
        "trails": entries,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    manifest_path = catalog_root / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    parks = manifest.get("parks")
    if not isinstance(parks, list):
        parks = []
    park_row = {
        "park_code": park_code,
        "park_name": park_name,
        "trail_count": len(entries),
        "file": str(out.relative_to(root)).replace("\\", "/"),
        "source_url": park_url,
        "updated_at_utc": written_at,
    }
    parks = [p for p in parks if isinstance(p, dict) and p.get("park_code") != park_code]
    parks.append(park_row)
    manifest["parks"] = sorted(parks, key=lambda p: str(p.get("park_code") or ""))
    manifest["updated_at_utc"] = written_at
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return out
