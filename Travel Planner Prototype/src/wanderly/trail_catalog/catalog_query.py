"""Unified catalog search (park + feature + region) with scope resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.wanderly.trail_catalog.canonical import (
    TrailIndex,
    attach_canonical_fields,
    canonical_trail_id_from_source_url,
)
from src.wanderly.trail_catalog.search import (
    parks_for_state,
    search_feature_trails,
    search_manifest_features,
    search_manifest_parks,
    search_manifest_regions,
    search_region_trails,
    search_trails,
)
from src.wanderly.trail_catalog.store import to_compact_row


@dataclass
class CatalogScope:
    """Resolved dimension scope from a user message or explicit filters."""

    park_codes: list[str] = field(default_factory=list)
    state_slugs: list[str] = field(default_factory=list)
    feature_slugs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "park_codes": self.park_codes,
            "state_slugs": self.state_slugs,
            "feature_slugs": self.feature_slugs,
            "notes": self.notes,
        }


@dataclass
class CatalogQuery:
    user_message: str = ""
    query: str = ""
    park_codes: list[str] = field(default_factory=list)
    state_slugs: list[str] = field(default_factory=list)
    feature_slugs: list[str] = field(default_factory=list)
    limit: int = 25
    min_distance_mi: float = 0.0
    max_distance_mi: float = 0.0
    difficulty: str = ""
    backpacking_only: bool = False
    merge_mode: str = "union"  # union | intersect (intersect only when multiple scopes yield rows)


def _norm_slug_list(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        s = (v or "").strip().lower().replace("us-", "")
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _norm_code_list(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        s = (v or "").strip().lower()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def resolve_scope(
    user_message: str = "",
    *,
    park_code: str = "",
    state_slug: str = "",
    feature_slug: str = "",
) -> CatalogScope:
    """
    Best-effort scope from explicit args + manifest substring matches on ``user_message``.
    """
    scope = CatalogScope()
    text = (user_message or "").strip()
    low = text.lower()
    words = set(re.findall(r"[a-z][a-z-]{2,}", low))

    if park_code:
        scope.park_codes.append(park_code.strip().lower())
    if state_slug:
        scope.state_slugs.append(state_slug.strip().lower().replace("us-", ""))
    if feature_slug:
        scope.feature_slugs.append(feature_slug.strip().lower())

    if text:
        for p in search_manifest_parks(text, limit=5):
            code = str(p.get("park_code") or "").lower()
            if code and code not in scope.park_codes:
                scope.park_codes.append(code)
                scope.notes.append(f"park:{code}")

        for r in search_manifest_regions(text, limit=5):
            st = str(r.get("state_slug") or "").lower()
            if st and st not in scope.state_slugs:
                scope.state_slugs.append(st)
                scope.notes.append(f"region:{st}")
            for pc in r.get("park_codes") or []:
                code = str(pc).lower()
                if code and code not in scope.park_codes:
                    scope.park_codes.append(code)

        for r in search_manifest_regions("", limit=60):
            st = str(r.get("state_slug") or "").lower()
            if not st:
                continue
            if st in words or st.replace("-", " ") in low:
                if st not in scope.state_slugs:
                    scope.state_slugs.append(st)
                    scope.notes.append(f"region_word:{st}")
                for pc in r.get("park_codes") or []:
                    code = str(pc).lower()
                    if code and code not in scope.park_codes:
                        scope.park_codes.append(code)

        for f in search_manifest_features(text, limit=5):
            fs = str(f.get("feature_slug") or "").lower()
            if fs and fs not in scope.feature_slugs:
                scope.feature_slugs.append(fs)
                scope.notes.append(f"feature:{fs}")

    # Common outdoor phrases
    if "waterfall" in low and "waterfall" not in scope.feature_slugs:
        scope.feature_slugs.append("waterfall")
    if "camping" in low and "camping" not in scope.feature_slugs:
        scope.feature_slugs.append("camping")
    if "hot spring" in low or "hot-spring" in low:
        if "hot-springs" not in scope.feature_slugs:
            scope.feature_slugs.append("hot-springs")

    scope.park_codes = _norm_code_list(scope.park_codes)
    scope.state_slugs = _norm_slug_list(scope.state_slugs)
    scope.feature_slugs = _norm_slug_list(scope.feature_slugs)

    # Expand state -> parks when we have a state but few parks
    if scope.state_slugs and len(scope.park_codes) < 2:
        for st in scope.state_slugs:
            for pc in parks_for_state(st):
                if pc not in scope.park_codes:
                    scope.park_codes.append(pc)

    return scope


def _difficulty_match(row: dict[str, Any], difficulty: str) -> bool:
    d = (difficulty or "").strip().lower()
    if not d:
        return True
    val = str(row.get("difficulty") or "").lower()
    return d in val or val in d


def _text_query(row: dict[str, Any], q: str) -> bool:
    query = (q or "").strip().lower()
    if not query:
        return True
    tokens = [t for t in re.split(r"\W+", query) if len(t) >= 3]
    blob = " ".join(
        str(row.get(k) or "")
        for k in (
            "name",
            "trail_id",
            "canonical_trail_id",
            "difficulty",
            "location_label",
            "state_slug",
            "park_code",
        )
    ).lower()
    if query in blob:
        return True
    return any(t in blob for t in tokens)


def _candidate_key(row: dict[str, Any]) -> str:
    cid = str(row.get("canonical_trail_id") or "").lower()
    if cid:
        return cid
    url = str(row.get("source_url") or "").strip().lower()
    if url:
        cid = canonical_trail_id_from_source_url(url)
        if cid:
            return cid
    return str(row.get("trail_id") or url or "").lower()


def search_catalog_trails(
    query: CatalogQuery | None = None,
    *,
    scope: CatalogScope | None = None,
    trail_index: TrailIndex | None = None,
) -> dict[str, Any]:
    """
    Search across park, feature, and region catalogs; dedupe by canonical trail id.

    Returns ``{ scope, count, trails[], deduped_from }``.
    Region files missing (crawl in progress) are skipped without error.
    """
    cq = query or CatalogQuery()
    sc = scope or resolve_scope(
        cq.user_message,
        park_code=",".join(cq.park_codes[:1]) if cq.park_codes else "",
        state_slug=",".join(cq.state_slugs[:1]) if cq.state_slugs else "",
        feature_slug=",".join(cq.feature_slugs[:1]) if cq.feature_slugs else "",
    )
    if cq.park_codes:
        for pc in _norm_code_list(cq.park_codes):
            if pc not in sc.park_codes:
                sc.park_codes.append(pc)
    if cq.state_slugs:
        for st in _norm_slug_list(cq.state_slugs):
            if st not in sc.state_slugs:
                sc.state_slugs.append(st)
    if cq.feature_slugs:
        for fs in _norm_slug_list(cq.feature_slugs):
            if fs not in sc.feature_slugs:
                sc.feature_slugs.append(fs)

    text_q = (cq.query or cq.user_message or "").strip()
    per_source_limit = max(cq.limit * 3, 50)
    buckets: dict[str, list[dict[str, Any]]] = {}

    def add_rows(source: str, rows: list[dict[str, Any]]) -> None:
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            row = attach_canonical_fields({**to_compact_row(raw), **raw})
            row["catalog_sources"] = [source]
            key = _candidate_key(row)
            if not key:
                continue
            if key not in buckets:
                buckets[key] = [row]
            else:
                existing = buckets[key][0]
                sources = list(existing.get("catalog_sources") or [])
                if source not in sources:
                    sources.append(source)
                existing["catalog_sources"] = sources
                for k, v in row.items():
                    if v is not None and not existing.get(k):
                        existing[k] = v

    # Explicit dimension scans
    park_codes = sc.park_codes or []
    state_slugs = sc.state_slugs or []
    feature_slugs = sc.feature_slugs or []

    if not park_codes and not state_slugs and not feature_slugs and text_q:
        sc = resolve_scope(text_q)
        park_codes = sc.park_codes
        state_slugs = sc.state_slugs
        feature_slugs = sc.feature_slugs

    for pc in park_codes:
        try:
            rows = search_trails(
                pc,
                query=text_q,
                limit=per_source_limit,
                backpacking_only=cq.backpacking_only,
                min_distance_mi=cq.min_distance_mi,
            )
            add_rows(f"park:{pc}", rows)
        except (FileNotFoundError, ValueError):
            continue

    for fs in feature_slugs:
        for st in state_slugs or [""]:
            try:
                rows = search_feature_trails(
                    fs,
                    query=text_q,
                    limit=per_source_limit,
                    park_code="",
                    state_slug=st,
                    min_distance_mi=cq.min_distance_mi,
                    max_distance_mi=cq.max_distance_mi,
                )
                src = f"feature:{fs}" + (f":{st}" if st else "")
                add_rows(src, rows)
            except (FileNotFoundError, ValueError):
                continue
        if not state_slugs:
            try:
                rows = search_feature_trails(
                    fs,
                    query=text_q,
                    limit=per_source_limit,
                    min_distance_mi=cq.min_distance_mi,
                    max_distance_mi=cq.max_distance_mi,
                )
                add_rows(f"feature:{fs}", rows)
            except (FileNotFoundError, ValueError):
                continue

    for st in state_slugs:
        try:
            rows = search_region_trails(
                st,
                query=text_q,
                limit=per_source_limit,
                max_distance_mi=cq.max_distance_mi,
                min_distance_mi=cq.min_distance_mi,
            )
            add_rows(f"region:{st}", rows)
        except (FileNotFoundError, ValueError):
            continue

    merged = list(buckets.values())
    flat: list[dict[str, Any]] = [b[0] for b in merged]
    flat = [r for r in flat if _difficulty_match(r, cq.difficulty) and _text_query(r, text_q)]
    if cq.max_distance_mi > 0:
        flat = [
            r
            for r in flat
            if isinstance(r.get("distance_mi"), (int, float))
            and float(r["distance_mi"]) <= cq.max_distance_mi
        ]
    if cq.min_distance_mi > 0:
        flat = [
            r
            for r in flat
            if isinstance(r.get("distance_mi"), (int, float))
            and float(r["distance_mi"]) >= cq.min_distance_mi
        ]

    idx = trail_index or TrailIndex.load(build_if_missing=False)
    for row in flat:
        cid = row.get("canonical_trail_id")
        if cid and idx.by_canonical:
            merged_row = idx.compact_row(str(cid))
            if merged_row:
                for k, v in merged_row.items():
                    row.setdefault(k, v)
        row["trail_id"] = str(row.get("canonical_trail_id") or row.get("trail_id") or "")

    flat.sort(key=lambda r: float(r.get("distance_mi") or 0))
    limit = max(1, min(int(cq.limit), 50))
    trails = flat[:limit]

    return {
        "scope": sc.to_dict(),
        "count": len(trails),
        "deduped_from": len(buckets),
        "trails": trails,
    }
