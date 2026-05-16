"""Build Expedia Hotel-Search URLs aligned with browser query shapes (params drift over time)."""

from __future__ import annotations

import os
from datetime import date
from os import PathLike
from pathlib import Path
from urllib.parse import urlencode

from expedia_listing_cli.filter_catalog import HotelSearchCatalog, load_catalog_json, merge_catalog_query


def build_hotel_search_url(
    destination: str,
    check_in: date,
    check_out: date,
    *,
    adults: int = 2,
    rooms: int = 1,
    region_id: str | int | None = None,
    flexibility: str = "0_DAY",
    sort: str = "RECOMMENDED",
    typeahead_collation_id: str | None = None,
    category_search: str = "any_option",
    use_rewards: bool = False,
    base: str | None = None,
    catalog: HotelSearchCatalog | None = None,
    catalog_path: str | PathLike[str] | None = None,
) -> str:
    """
    Model a ``Hotel-Search`` URL like a real browser session (see Expedia network tab).

    ``destination`` should usually be the **full** label, e.g.
    ``Las Vegas, Nevada, United States of America`` — copy from the working URL
    or from the typeahead chip in the UI.

    ``regionId`` is important for consistent results; find it from a working
    browser URL for that destination (e.g. Las Vegas ``regionId=2008``).

    ``typeaheadCollationId`` is optional; omit unless you copied one from a
    recent session (it may expire).

    If ``catalog`` or ``catalog_path`` is set, query keys from the extracted JSON
    (see ``python -m expedia_listing_cli extract-filters``) are used as defaults;
    destination, dates, party size, ``regionId``, ``flexibility``, ``sort``,
    ``categorySearch``, ``useRewards``, and optional ``typeaheadCollationId``
    are overlaid from these arguments. Other keys from the golden URL are kept.
    """
    if catalog is not None and catalog_path is not None:
        raise ValueError("Pass at most one of catalog and catalog_path")
    if catalog_path is not None:
        catalog = load_catalog_json(Path(catalog_path))

    if catalog is not None:
        root = (
            base
            or os.environ.get("EXPEDIA_HOTEL_SEARCH_BASE")
            or catalog.base_url
            or "https://www.expedia.com"
        ).rstrip("/")
        path = catalog.path if catalog.path.startswith("/") else f"/{catalog.path}"
        tid = (typeahead_collation_id or os.environ.get("EXPEDIA_TYPEAHEAD_COLLATION_ID") or "").strip()
        rid = region_id
        if rid is None and (os.environ.get("EXPEDIA_REGION_ID") or "").strip():
            rid = os.environ.get("EXPEDIA_REGION_ID", "").strip()
        merged = merge_catalog_query(
            catalog,
            destination=destination,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            rooms=rooms,
            region_id=rid,
            flexibility=flexibility,
            sort=sort,
            typeahead_collation_id=tid or None,
            category_search=category_search,
            use_rewards=use_rewards,
        )
        return f"{root}{path}?{urlencode(merged)}"

    root = (base or os.environ.get("EXPEDIA_HOTEL_SEARCH_BASE") or "https://www.expedia.com").rstrip(
        "/"
    )
    ci = check_in.isoformat()
    co = check_out.isoformat()
    q: list[tuple[str, str]] = [
        ("destination", destination.strip()),
        ("flexibility", flexibility),
        ("d1", ci),
        ("startDate", ci),
        ("d2", co),
        ("endDate", co),
        ("adults", str(max(1, adults))),
        ("rooms", str(max(1, rooms))),
        ("sort", sort),
        ("theme", ""),
        ("userIntent", ""),
        ("semdtl", ""),
        ("categorySearch", category_search),
        ("useRewards", "true" if use_rewards else "false"),
        ("lodging", "HOTEL"),
    ]
    if region_id is not None and str(region_id).strip() != "":
        q.insert(1, ("regionId", str(region_id).strip()))
    if typeahead_collation_id and typeahead_collation_id.strip():
        # After rooms in many browser URLs; order rarely matters for servers.
        q.append(("typeaheadCollationId", typeahead_collation_id.strip()))

    return f"{root}/Hotel-Search?{urlencode(q)}"
