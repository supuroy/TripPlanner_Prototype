#!/usr/bin/env python3
"""
Stdio MCP server: query offline trail catalog without stuffing prompts.

Local IDE: add to Cursor MCP config (see mcp/README.md).
Cloud Agent spike: pass in POST /v1/agents ``mcpServers`` (stdio runs in Cursor VM with repo clone).

  python mcp/trail_catalog_server.py

Requires: pip install mcp
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "Install MCP SDK: pip install mcp",
        file=sys.stderr,
    )
    raise SystemExit(1) from None

from src.wanderly.trail_catalog.catalog_query import (  # noqa: E402
    CatalogQuery,
    resolve_scope,
    search_catalog_trails,
)
from src.wanderly.trail_catalog.canonical import TrailIndex  # noqa: E402
from src.wanderly.trail_catalog.search import (  # noqa: E402
    catalog_stats,
    get_feature_trail_compact,
    get_trail_compact,
    parks_for_state,
    search_feature_trails,
    search_manifest_features,
    search_manifest_parks,
    search_manifest_regions,
    search_region_trails,
    search_trails,
)

mcp = FastMCP(
    "trail-catalog",
    instructions=(
        "Offline AllTrails trail metadata (parks + trail features). "
        "Parks: search_manifest_parks_tool then search_trails_tool. "
        "Preferred: resolve_scope_tool then search_trails_intent_tool (one search across park+feature+region). "
        "Use state_slug + feature_slug instead of many park_code probes. "
        "trail_id values in results are canonical (trail:country-region-slug). Never invent ids."
    ),
)


@mcp.tool()
def trail_catalog_stats() -> str:
    """Summary of indexed parks and trail row counts."""
    return json.dumps(catalog_stats(), ensure_ascii=True, indent=2)


@mcp.tool()
def resolve_scope_tool(user_message: str) -> str:
    """Resolve user text to park_codes, state_slugs, feature_slugs (manifest-backed)."""
    scope = resolve_scope(user_message)
    return json.dumps(scope.to_dict(), ensure_ascii=True, indent=2)


@mcp.tool()
def search_trails_intent_tool(
    user_message: str = "",
    query: str = "",
    park_code: str = "",
    state_slug: str = "",
    feature_slug: str = "",
    limit: int = 25,
    max_distance_mi: float = 0,
    min_distance_mi: float = 0,
    difficulty: str = "",
    backpacking_only: bool = False,
) -> str:
    """
    Unified trail search across park, feature, and region catalogs (deduped by canonical id).
    Example: user_message='moderate waterfall hikes in Utah under 7 miles', max_distance_mi=7.
    """
    cq = CatalogQuery(
        user_message=user_message,
        query=query or user_message,
        park_codes=[park_code] if park_code.strip() else [],
        state_slugs=[state_slug] if state_slug.strip() else [],
        feature_slugs=[feature_slug] if feature_slug.strip() else [],
        limit=max(1, min(limit, 50)),
        max_distance_mi=max_distance_mi,
        min_distance_mi=min_distance_mi,
        difficulty=difficulty,
        backpacking_only=backpacking_only,
    )
    idx = TrailIndex.load(build_if_missing=False)
    result = search_catalog_trails(cq, trail_index=idx)
    return json.dumps(result, ensure_ascii=True, indent=2)


@mcp.tool()
def get_trail_by_canonical_id_tool(canonical_trail_id: str) -> str:
    """Lookup one trail by canonical id (trail:us-utah-…) or any known dimension alias."""
    idx = TrailIndex.load(build_if_missing=True)
    row = idx.compact_row(canonical_trail_id)
    if not row:
        return json.dumps({"error": "trail_not_found", "canonical_trail_id": canonical_trail_id})
    return json.dumps(row, ensure_ascii=True, indent=2)


@mcp.tool()
def search_manifest_parks_tool(query: str, limit: int = 10) -> str:
    """Find parks by name (e.g. 'yellowstone', 'yosemite'). Returns park_code + trail_count."""
    rows = search_manifest_parks(query, limit=max(1, min(limit, 30)))
    return json.dumps({"parks": rows}, ensure_ascii=True, indent=2)


@mcp.tool()
def search_trails_tool(
    park_code: str,
    query: str = "",
    limit: int = 20,
    backpacking_only: bool = True,
) -> str:
    """
    Search trails in one park catalog file. Returns compact rows (trail_id, name, distance_mi, …).
    Call search_manifest_parks_tool first if park_code is unknown.
    """
    rows = search_trails(
        park_code,
        query=query,
        limit=max(1, min(limit, 50)),
        backpacking_only=backpacking_only,
    )
    return json.dumps(
        {"park_code": park_code.strip().lower(), "count": len(rows), "trails": rows},
        ensure_ascii=True,
        indent=2,
    )


@mcp.tool()
def get_trail_tool(park_code: str, trail_id: str) -> str:
    """Fetch one trail row by exact trail_id from a park catalog."""
    row = get_trail_compact(park_code, trail_id)
    if not row:
        return json.dumps({"error": "trail_not_found", "park_code": park_code, "trail_id": trail_id})
    return json.dumps(row, ensure_ascii=True, indent=2)


@mcp.tool()
def search_manifest_features_tool(query: str = "", limit: int = 10) -> str:
    """List trail features (waterfall, camping, hot-springs, …) from features_manifest.json."""
    rows = search_manifest_features(query, limit=max(1, min(limit, 30)))
    return json.dumps({"features": rows}, ensure_ascii=True, indent=2)


@mcp.tool()
def search_manifest_regions_tool(query: str = "", limit: int = 10) -> str:
    """Find US states/regions (e.g. utah, california). Returns park_codes in each region when known."""
    rows = search_manifest_regions(query, limit=max(1, min(limit, 30)))
    return json.dumps({"regions": rows}, ensure_ascii=True, indent=2)


@mcp.tool()
def search_region_trails_tool(
    state_slug: str,
    query: str = "",
    limit: int = 20,
    park_code: str = "",
    max_distance_mi: float = 0,
) -> str:
    """Search trails crawled from a US region hub (regions/us-{state}.json). Requires region crawl."""
    rows = search_region_trails(
        state_slug,
        query=query,
        limit=max(1, min(limit, 50)),
        park_code=park_code,
        max_distance_mi=max_distance_mi,
    )
    return json.dumps(
        {
            "state_slug": state_slug.strip().lower().replace("us-", ""),
            "count": len(rows),
            "trails": rows,
        },
        ensure_ascii=True,
        indent=2,
    )


@mcp.tool()
def parks_for_state_tool(state_slug: str) -> str:
    """List park_code values for a US state slug (e.g. utah). Uses regions_manifest + park catalogs."""
    codes = parks_for_state(state_slug)
    return json.dumps({"state_slug": state_slug.strip().lower(), "park_codes": codes}, ensure_ascii=True, indent=2)


@mcp.tool()
def search_feature_trails_tool(
    feature_slug: str,
    query: str = "",
    limit: int = 20,
    park_code: str = "",
    state_slug: str = "",
    max_distance_mi: float = 0,
) -> str:
    """
    Search trails in one feature catalog file (features/{slug}.json).
    Prefer state_slug=utah or query=zion over many park_code calls.
    Optional park_code, state_slug, max_distance_mi filters.
    """
    rows = search_feature_trails(
        feature_slug,
        query=query,
        limit=max(1, min(limit, 50)),
        park_code=park_code,
        state_slug=state_slug,
        max_distance_mi=max_distance_mi,
    )
    return json.dumps(
        {
            "feature_slug": feature_slug.strip().lower(),
            "park_code_filter": park_code.strip().lower() or None,
            "state_slug_filter": state_slug.strip().lower() or None,
            "max_distance_mi": max_distance_mi if max_distance_mi > 0 else None,
            "count": len(rows),
            "trails": rows,
        },
        ensure_ascii=True,
        indent=2,
    )


@mcp.tool()
def get_feature_trail_tool(feature_slug: str, trail_id: str) -> str:
    """Fetch one trail row by exact trail_id from a feature catalog."""
    row = get_feature_trail_compact(feature_slug, trail_id)
    if not row:
        return json.dumps(
            {
                "error": "trail_not_found",
                "feature_slug": feature_slug,
                "trail_id": trail_id,
            }
        )
    return json.dumps(row, ensure_ascii=True, indent=2)


if __name__ == "__main__":
    mcp.run()
