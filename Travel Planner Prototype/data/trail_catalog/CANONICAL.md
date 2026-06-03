# Canonical trail index

Trails in **park**, **feature**, and **region** files often share the same AllTrails URL but use different dimension `trail_id` prefixes. The canonical index merges them.

## Join key

`source_url` (normalized) → `canonical_trail_id` e.g. `trail:us-utah-emerald-pools-trail`

## Build / refresh

```powershell
cd "Travel Planner Prototype"
python scripts/build_trail_canonical_index.py
```

**Re-run after region crawl** adds or updates `regions/us-*.json`. No full catalog redo — only new URLs are merged in.

```powershell
python scripts/build_trail_canonical_index.py --stats-only
```

## Wanderly + MCP

- Unified search: `search_catalog_trails` / MCP `search_trails_intent_tool`
- Scope: `resolve_scope` / MCP `resolve_scope_tool`
- Lookup: `get_trail_by_canonical_id_tool`

Dimension-specific `trail_id` values are kept as aliases in the index and optional `catalog_trail_id` on rows.
