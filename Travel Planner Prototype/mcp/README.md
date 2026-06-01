# Trail catalog MCP (dynamic context spike)

Query `data/trail_catalog/` via tools instead of pasting thousands of trails into the Cursor prompt.

## Install

```powershell
cd "Travel Planner Prototype"
pip install mcp
```

## Local test (Cursor IDE)

1. **Cursor Settings → MCP** → add server (or merge into your MCP config):

```json
{
  "mcpServers": {
    "trail-catalog": {
      "command": "python",
      "args": ["mcp/trail_catalog_server.py"],
      "cwd": "C:/path/to/TripPlanner_Prototype/Travel Planner Prototype"
    }
  }
}
```

2. In Cursor chat, ask: *"Use trail-catalog search_manifest_parks for yellowstone, then search_trails with park_code yell, limit 5."*

3. Confirm the agent calls MCP tools and returns real `trail_id` values from the JSON files.

## Cloud Agent API test

Use `scripts/spike_cursor_dynamic_catalog.py` (see `data/trail_catalog/SPIKE_CURSOR_DYNAMIC.md`).

**Important:** Cloud stdio MCP runs inside Cursor's VM on the **cloned GitHub repo**. Your catalog must be committed and pushed to `CURSOR_REPO_URL` / `CURSOR_REPO_REF`, or the MCP server will see an empty `data/trail_catalog/`.

## Tools

| Tool | Purpose |
|------|---------|
| `trail_catalog_stats` | Park + feature counts |
| `search_manifest_parks_tool` | Resolve `park_code` from name |
| `search_trails_tool` | Filtered trail list for one park |
| `get_trail_tool` | One trail by `trail_id` |
| `search_manifest_features_tool` | List features (waterfall, camping, …) |
| `search_feature_trails_tool` | Trails in `features/{slug}.json` |
| `get_feature_trail_tool` | One feature trail by `trail_id` |

## Cloud Agent PoC

See **`docs/POC_CURSOR_TRAIL_AGENT.md`** and:

```powershell
python scripts/poc_cursor_cloud_trail_agent.py --scenario feature
```
