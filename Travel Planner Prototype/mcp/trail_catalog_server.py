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

from src.wanderly.trail_catalog.search import (  # noqa: E402
    catalog_stats,
    get_feature_trail_compact,
    get_trail_compact,
    search_feature_trails,
    search_manifest_features,
    search_manifest_parks,
    search_trails,
)

mcp = FastMCP(
    "trail-catalog",
    instructions=(
        "Offline AllTrails trail metadata (parks + trail features). "
        "Parks: search_manifest_parks_tool then search_trails_tool. "
        "Features (waterfall, camping, …): search_manifest_features_tool then "
        "search_feature_trails_tool. Never invent trail_id values."
    ),
)


@mcp.tool()
def trail_catalog_stats() -> str:
    """Summary of indexed parks and trail row counts."""
    return json.dumps(catalog_stats(), ensure_ascii=True, indent=2)


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
def search_feature_trails_tool(
    feature_slug: str,
    query: str = "",
    limit: int = 20,
    park_code: str = "",
) -> str:
    """
    Search trails in one feature catalog file (features/{slug}.json).
    Optional park_code filters rows that were joined to a park catalog.
    """
    rows = search_feature_trails(
        feature_slug,
        query=query,
        limit=max(1, min(limit, 50)),
        park_code=park_code,
    )
    return json.dumps(
        {
            "feature_slug": feature_slug.strip().lower(),
            "park_code_filter": park_code.strip().lower() or None,
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
