#!/usr/bin/env python3
"""Local stand-in for MCP search_trails_intent_tool (no Cloud Agent)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from src.wanderly.trail_catalog.catalog_query import CatalogQuery, resolve_scope, search_catalog_trails

    msg = " ".join(sys.argv[1:]) or "3 moderate waterfall hikes in Utah under 7 miles"
    scope = resolve_scope(msg)
    print("=== resolve_scope ===", flush=True)
    print(json.dumps(scope.to_dict(), indent=2, ensure_ascii=True), flush=True)

    result = search_catalog_trails(
        CatalogQuery(
            user_message=msg,
            max_distance_mi=7,
            limit=10,
        ),
    )
    print("\n=== search_catalog_trails (1 logical query) ===", flush=True)
    print(f"deduped_from={result['deduped_from']} count={result['count']}", flush=True)
    for t in result["trails"][:5]:
        print(
            f"  - {t.get('canonical_trail_id')} | {t.get('name')} | "
            f"{t.get('distance_mi')} mi | sources={t.get('catalog_sources')}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
