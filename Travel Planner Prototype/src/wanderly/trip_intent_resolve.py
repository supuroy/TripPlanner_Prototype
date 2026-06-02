"""
Resolve ``TripIntentClassification`` to catalog-backed park codes (server-side).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from src.wanderly.nps_provider import infer_park_code_from_name
from src.wanderly.trail_catalog.store import catalog_root, park_catalog_path, resolve_park_code_from_text
from src.wanderly.trip_intent_llm import TripIntentClassification, is_outdoor_trip_product
from src.wanderly.trip_intent_trace import dump_trip_intent_debug, log_trip_intent


class TripIntentResolution(BaseModel):
    trip_product: str = "leisure"
    destination_hints: list[str] = Field(default_factory=list)
    resolved_park_codes: list[str] = Field(default_factory=list)
    primary_park_code: str | None = None
    primary_park_name: str | None = None
    catalog_eligible: bool = False
    catalog_trail_count: int = 0
    rationale: str = ""


_manifest_cache: list[dict[str, Any]] | None = None


def _load_manifest_parks() -> list[dict[str, Any]]:
    global _manifest_cache
    if _manifest_cache is not None:
        return _manifest_cache
    path = catalog_root() / "manifest.json"
    if not path.is_file():
        _manifest_cache = []
        return _manifest_cache
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _manifest_cache = []
        return _manifest_cache
    parks = doc.get("parks")
    _manifest_cache = [p for p in parks if isinstance(p, dict)] if isinstance(parks, list) else []
    return _manifest_cache


def reset_manifest_cache_for_tests() -> None:
    global _manifest_cache
    _manifest_cache = None


def _park_has_catalog(park_code: str, *, min_trails: int = 5) -> tuple[bool, int]:
    code = (park_code or "").strip().lower()
    if not code:
        return False, 0
    path = park_catalog_path(code)
    if not path.is_file():
        return False, 0
    for row in _load_manifest_parks():
        if str(row.get("park_code") or "").lower() == code:
            try:
                n = int(row.get("trail_count") or 0)
            except (TypeError, ValueError):
                n = 0
            return n >= min_trails, n
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        trails = doc.get("trails")
        n = len(trails) if isinstance(trails, list) else 0
        return n >= min_trails, n
    except (OSError, json.JSONDecodeError):
        return False, 0


def _match_manifest_by_name(label: str) -> str | None:
    low = (label or "").strip().lower()
    if not low:
        return None
    best: tuple[int, str] | None = None
    for row in _load_manifest_parks():
        name = str(row.get("park_name") or "").strip()
        code = str(row.get("park_code") or "").strip().lower()
        if not name or not code:
            continue
        nl = name.lower()
        if low == nl or low in nl or nl in low:
            score = len(name)
            if best is None or score > best[0]:
                best = (score, code)
    return best[1] if best else None


def resolve_park_codes_from_hints(
    hints: list[str],
    *,
    user_message: str = "",
) -> list[str]:
    """Ordered unique park codes with on-disk catalogs."""
    blob = " ".join([user_message or "", *(hints or [])])
    codes: list[str] = []

    def add(code: str | None) -> None:
        c = (code or "").strip().lower()
        if not c:
            return
        ok, _ = _park_has_catalog(c)
        if ok and c not in codes:
            codes.append(c)

    for resolver in (
        lambda: resolve_park_code_from_text(blob),
        lambda: infer_park_code_from_name(blob),
    ):
        add(resolver())

    for hint in hints or []:
        for resolver in (
            lambda h=hint: resolve_park_code_from_text(h),
            lambda h=hint: infer_park_code_from_name(h),
            lambda h=hint: _match_manifest_by_name(h),
        ):
            add(resolver())

    return codes


def resolve_trip_intent(
    intent: TripIntentClassification,
    *,
    user_message: str = "",
) -> TripIntentResolution:
    product = intent.trip_product
    hints = list(intent.destination_hints)
    codes = resolve_park_codes_from_hints(hints, user_message=user_message) if is_outdoor_trip_product(product) else []

    primary: str | None = codes[0] if codes else None
    primary_name: str | None = None
    trail_count = 0
    if primary:
        for row in _load_manifest_parks():
            if str(row.get("park_code") or "").lower() == primary:
                primary_name = str(row.get("park_name") or "") or None
                try:
                    trail_count = int(row.get("trail_count") or 0)
                except (TypeError, ValueError):
                    trail_count = 0
                break
        ok, trail_count = _park_has_catalog(primary)
    else:
        ok = False

    eligible = bool(primary and ok and is_outdoor_trip_product(product))
    rationale = intent.rationale
    if eligible:
        rationale = f"catalog_eligible:{primary}"
    elif is_outdoor_trip_product(product) and not primary:
        rationale = (rationale + "; no_manifest_park").strip("; ")

    res = TripIntentResolution(
        trip_product=product,
        destination_hints=hints,
        resolved_park_codes=codes,
        primary_park_code=primary,
        primary_park_name=primary_name,
        catalog_eligible=eligible,
        catalog_trail_count=trail_count,
        rationale=rationale,
    )
    log_trip_intent(
        f"resolve product={product!r} primary={primary!r} eligible={eligible} "
        f"codes={codes!r} trails={trail_count}"
    )
    dump_trip_intent_debug(
        "resolve",
        {"intent": intent.model_dump(), "resolution": res.model_dump()},
    )
    return res
