"""Extract Hotel-Search query params from a golden browser URL; save/load JSON for URL building."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlparse


SCHEMA_VERSION = 1

# Keys we replace from :func:`build_hotel_search_url` arguments (single value each in the URL).
_OVERLAY_KEYS = frozenset(
    {
        "destination",
        "d1",
        "startDate",
        "d2",
        "endDate",
        "adults",
        "rooms",
        "flexibility",
        "sort",
        "categorySearch",
        "useRewards",
    }
)

# If the catalog omits core keys, prepend them in this order (browser-shaped).
_MISSING_CORE_PREFIX_ORDER = (
    "destination",
    "regionId",
    "flexibility",
    "d1",
    "startDate",
    "d2",
    "endDate",
    "adults",
    "rooms",
    "typeaheadCollationId",
    "sort",
    "categorySearch",
    "useRewards",
)


def _pairs_from_url(url: str) -> tuple[tuple[str, str], ...]:
    raw = (url or "").strip()
    if not raw:
        return ()
    pairs = parse_qsl(urlparse(raw).query, keep_blank_values=True, strict_parsing=False)
    return tuple((str(k), v) for k, v in pairs)


@dataclass(frozen=True)
class HotelSearchCatalog:
    """
    Serializable defaults from a real Hotel-Search URL.

    ``query_param_pairs`` preserves order and **duplicate keys** (e.g. ``price=min`` and
    ``price=max`` appear twice on Expedia).
    """

    base_url: str
    path: str
    query_param_pairs: tuple[tuple[str, str], ...]
    extracted_from_url: str
    parsed_at: str
    schema_version: int = SCHEMA_VERSION

    @property
    def query_params(self) -> dict[str, str]:
        """Last occurrence per key (convenience only); use ``query_param_pairs`` to rebuild URLs."""
        out: dict[str, str] = {}
        for k, v in self.query_param_pairs:
            out[k] = v
        return out

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "extracted_from_url": self.extracted_from_url,
            "parsed_at": self.parsed_at,
            "base_url": self.base_url,
            "path": self.path,
            "query_param_pairs": [[a, b] for a, b in self.query_param_pairs],
            "query_params": self.query_params,
        }

    @staticmethod
    def from_json_dict(data: Mapping[str, Any]) -> HotelSearchCatalog:
        ver = int(data.get("schema_version", 0))
        if ver != SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema_version {ver!r}; expected {SCHEMA_VERSION}")
        pairs_raw = data.get("query_param_pairs")
        if (
            pairs_raw is not None
            and isinstance(pairs_raw, Sequence)
            and not isinstance(pairs_raw, (str, bytes))
            and len(pairs_raw) > 0
        ):
            qpairs: list[tuple[str, str]] = []
            for item in pairs_raw:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    qpairs.append((str(item[0]), "" if item[1] is None else str(item[1])))
                else:
                    raise ValueError("query_param_pairs must be a list of [key, value] pairs")
            query_param_pairs = tuple(qpairs)
        else:
            # Legacy: flat object (loses duplicate keys such as two ``price`` entries)
            flat = data.get("query_params") or {}
            if not isinstance(flat, Mapping):
                raise ValueError("query_param_pairs or query_params is required")
            query_param_pairs = tuple((str(k), "" if v is None else str(v)) for k, v in dict(flat).items())
        return HotelSearchCatalog(
            base_url=str(data.get("base_url") or "").strip(),
            path=str(data.get("path") or "/Hotel-Search").strip() or "/Hotel-Search",
            query_param_pairs=query_param_pairs,
            extracted_from_url=str(data.get("extracted_from_url") or "").strip(),
            parsed_at=str(data.get("parsed_at") or "").strip(),
            schema_version=SCHEMA_VERSION,
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_query_params_flat(url: str) -> dict[str, str]:
    """
    Parse query string into a flat dict; duplicate keys keep the **last** occurrence.
    Prefer :func:`_pairs_from_url` when duplicates matter.
    """
    out: dict[str, str] = {}
    for k, v in _pairs_from_url(url):
        out[k] = v
    return out


def extract_catalog_from_url(url: str) -> HotelSearchCatalog:
    """
    Build a catalog from a full browser URL (ideally Hotel-Search with filters applied).

    ``base_url`` is ``scheme://netloc``; ``path`` is the URL path (default ``/Hotel-Search``).
    Query keys and values are preserved in order, including repeated keys (e.g. ``price``).
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL is empty")
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("URL must include scheme and host, e.g. https://www.expedia.com/...")
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path or "/Hotel-Search"
    if not path.startswith("/"):
        path = "/" + path
    q_pairs = _pairs_from_url(raw)
    return HotelSearchCatalog(
        base_url=base,
        path=path,
        query_param_pairs=q_pairs,
        extracted_from_url=raw,
        parsed_at=_utc_now_iso(),
    )


def save_catalog_json(catalog: HotelSearchCatalog, path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(catalog.to_json_dict(), indent=2, ensure_ascii=False) + "\n"
    p.write_text(text, encoding="utf-8")
    return p


def load_catalog_json(path: Path | str) -> HotelSearchCatalog:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Catalog JSON must be an object")
    return HotelSearchCatalog.from_json_dict(data)


def _merge_pairs_with_overlay(
    pairs: tuple[tuple[str, str], ...],
    replacements: dict[str, str],
    *,
    region_id: str | int | None,
    typeahead_collation_id: str | None,
) -> list[tuple[str, str]]:
    """
    Walk original pair order; replace first occurrence of each overlay key with the new
    value and drop later occurrences of that key (browser uses single value for those).
    Keys not in ``replacements`` (e.g. ``price`` twice) pass through unchanged.
    """
    rid = region_id
    has_rid = rid is not None and str(rid).strip() != ""
    tid = (typeahead_collation_id or "").strip()

    repl = dict(replacements)
    if has_rid:
        repl["regionId"] = str(rid).strip()
    if tid:
        repl["typeaheadCollationId"] = tid

    # Keys we replace from the builder: overlay set plus region/typeahead when supplied
    replace_keys = set(_OVERLAY_KEYS)
    if has_rid:
        replace_keys.add("regionId")
    if tid:
        replace_keys.add("typeaheadCollationId")

    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for k, v in pairs:
        if k in replace_keys:
            if k in seen:
                continue
            out.append((k, repl[k]))
            seen.add(k)
        else:
            out.append((k, v))

    prefix: list[tuple[str, str]] = []
    for k in _MISSING_CORE_PREFIX_ORDER:
        if k in replace_keys and k in repl and k not in seen:
            prefix.append((k, repl[k]))
            seen.add(k)
    return prefix + out


def merge_catalog_query(
    catalog: HotelSearchCatalog,
    *,
    destination: str,
    check_in: date,
    check_out: date,
    adults: int,
    rooms: int,
    region_id: str | int | None,
    flexibility: str,
    sort: str,
    typeahead_collation_id: str | None,
    category_search: str,
    use_rewards: bool,
) -> list[tuple[str, str]]:
    """
    Overlay trip-specific fields on the catalog pair list. Duplicate keys such as
    ``price`` are left as in the catalog unless added to overlay later.
    """
    ci = check_in.isoformat()
    co = check_out.isoformat()
    replacements: dict[str, str] = {
        "destination": destination.strip(),
        "d1": ci,
        "startDate": ci,
        "d2": co,
        "endDate": co,
        "adults": str(max(1, adults)),
        "rooms": str(max(1, rooms)),
        "flexibility": flexibility,
        "sort": sort,
        "categorySearch": category_search,
        "useRewards": "true" if use_rewards else "false",
    }
    return _merge_pairs_with_overlay(
        catalog.query_param_pairs,
        replacements,
        region_id=region_id,
        typeahead_collation_id=typeahead_collation_id,
    )
