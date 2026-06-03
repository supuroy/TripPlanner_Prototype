"""Offline park hero images (AllTrails park pages) + runtime resolution for destination cards."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.wanderly.stock_photo_heroes import infer_hero_park_phrase

_CATALOG_ROOT = Path(__file__).resolve().parents[2] / "data" / "trail_catalog"
_PARK_HEROES_PATH = _CATALOG_ROOT / "park_heroes.json"
_MANIFEST_PATH = _CATALOG_ROOT / "manifest.json"

_manifest_cache: dict[str, Any] | None = None
_heroes_cache: dict[str, Any] | None = None


def park_heroes_path() -> Path:
    return _PARK_HEROES_PATH


def reset_park_hero_caches_for_tests() -> None:
    global _manifest_cache, _heroes_cache
    _manifest_cache = None
    _heroes_cache = None


def load_manifest_parks() -> list[dict[str, Any]]:
    global _manifest_cache
    if _manifest_cache is not None:
        parks = _manifest_cache.get("parks")
        return list(parks) if isinstance(parks, list) else []
    if not _MANIFEST_PATH.is_file():
        _manifest_cache = {"parks": []}
        return []
    try:
        doc = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        doc = {"parks": []}
    _manifest_cache = doc if isinstance(doc, dict) else {"parks": []}
    parks = _manifest_cache.get("parks")
    return [p for p in parks if isinstance(p, dict)] if isinstance(parks, list) else []


def manifest_row_for_park_code(park_code: str) -> dict[str, Any] | None:
    pc = (park_code or "").strip().lower()
    if not pc:
        return None
    for row in load_manifest_parks():
        if str(row.get("park_code") or "").strip().lower() == pc:
            return row
    return None


def load_park_heroes_doc(*, force_reload: bool = False) -> dict[str, Any]:
    global _heroes_cache
    if _heroes_cache is not None and not force_reload:
        return _heroes_cache
    if not _PARK_HEROES_PATH.is_file():
        _heroes_cache = {"catalog_version": 1, "parks": {}}
        return _heroes_cache
    try:
        doc = json.loads(_PARK_HEROES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        doc = {"catalog_version": 1, "parks": {}}
    if not isinstance(doc, dict):
        doc = {"catalog_version": 1, "parks": {}}
    parks = doc.get("parks")
    if not isinstance(parks, dict):
        doc["parks"] = {}
    _heroes_cache = doc
    return doc


def save_park_heroes_doc(doc: dict[str, Any]) -> None:
    global _heroes_cache
    out = dict(doc)
    out["updated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out.setdefault("catalog_version", 1)
    parks = out.get("parks")
    if not isinstance(parks, dict):
        out["parks"] = {}
    _PARK_HEROES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PARK_HEROES_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _heroes_cache = out


def offline_park_hero_entry(park_code: str) -> dict[str, Any] | None:
    pc = (park_code or "").strip().lower()
    if not pc:
        return None
    doc = load_park_heroes_doc()
    parks = doc.get("parks")
    if not isinstance(parks, dict):
        return None
    row = parks.get(pc)
    return row if isinstance(row, dict) else None


def offline_park_hero_url(park_code: str) -> str | None:
    row = offline_park_hero_entry(park_code)
    if not row:
        return None
    url = str(row.get("hero_image_url") or row.get("image_url") or "").strip()
    return url if url.startswith("https:") else None


def park_code_from_destination(dest: dict[str, Any]) -> str | None:
    """Resolve NPS/catalog park code for hero lookup."""
    g = dest.get("grounding")
    if isinstance(g, dict):
        pc = str(g.get("park_code") or "").strip().lower()
        if pc:
            return pc
    from src.wanderly.nps_provider import infer_park_code_from_name

    did = str(dest.get("id") or "")
    name = str(dest.get("name") or "")
    hook = str(dest.get("hook") or "")
    park_phrase = infer_hero_park_phrase(destination_id=did, name=name, hook=hook)
    if park_phrase:
        code = infer_park_code_from_name(park_phrase)
        if code:
            return code
    code = infer_park_code_from_name(name)
    if code:
        return code
    code = infer_park_code_from_name(hook)
    return code


def park_hero_urls_for_destination(dest: dict[str, Any], *, gallery_max: int = 3) -> tuple[list[str], str]:
    """
    Return ``(urls, source)`` for outdoor/catalog cards.

    ``source`` is ``offline_alltrails`` | ``nps_live`` | ``none``.
    """
    pc = park_code_from_destination(dest)
    if not pc:
        return [], "none"

    offline = offline_park_hero_url(pc)
    if offline:
        return [offline][: max(1, gallery_max)], "offline_alltrails"

    from src.wanderly.nps_provider import fetch_nps_park_hero_urls

    nps_urls = fetch_nps_park_hero_urls(pc, max_images=gallery_max)
    if nps_urls:
        return nps_urls[: max(1, gallery_max)], "nps_live"
    return [], "none"
