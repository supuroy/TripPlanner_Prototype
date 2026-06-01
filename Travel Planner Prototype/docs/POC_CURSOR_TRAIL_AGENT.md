# PoC: Cursor Cloud Agent + trail metadata (MCP)

Standalone proof of concept **before** wiring Wanderly or the Figma app. A Cloud Agent created via the Cursor API queries offline catalog JSON through MCP tools (parks + AllTrails feature crawls).

## What this proves

| Piece | Role |
|--------|------|
| `data/trail_catalog/` | Offline metadata (parks, features, manifests) |
| `mcp/trail_catalog_server.py` | Stdio MCP tools (`search_trails_tool`, `search_feature_trails_tool`, …) |
| `scripts/poc_cursor_cloud_trail_agent.py` | Creates cloud agent + runs 1–3 scenarios |
| `src/integrations/cursor_agent_client.py` | `create_agent_run(..., mcp_servers=…)` |

The agent gets a **small prompt** (~2k chars) and must call MCP to read catalogs cloned from GitHub in Cursor’s VM.

## Prerequisites

1. **Catalog on GitHub** at `CURSOR_REPO_URL` / `CURSOR_REPO_REF` (cloud VM clones this repo).
2. **`Travel Planner Prototype/.env`**
   ```env
   CURSOR_API_KEY=...
   CURSOR_REPO_URL=https://github.com/<org>/TripPlanner_Prototype
   CURSOR_REPO_REF=poc/cursor-trail-catalog-agent
   ```
3. **Monorepo layout:** if the Git root is `TripPlanner_Prototype` (parent of `Travel Planner Prototype/`), set:
   ```env
   CURSOR_MCP_CWD=Travel Planner Prototype
   ```
   If the Git root *is* `Travel Planner Prototype`, leave `CURSOR_MCP_CWD` unset.

4. **Deps**
   ```powershell
   cd "Travel Planner Prototype"
   pip install -r requirements-spike.txt
   ```

## Git: branch, commit catalog, push (run locally)

**Path rule:** If your prompt shows `...\TripPlanner_Prototype\Travel Planner Prototype>`, you are **inside** the Python project. Use `data/...`, `mcp/`, not `"Travel Planner Prototype/data/..."`. The double-folder warnings mean you used the wrong prefix.

You are already on `poc/cursor-trail-catalog-agent` if `git branch --show-current` says so. Skip `checkout -b` and just add + commit + push.

### Option A — repo root `TripPlanner_Prototype`

```powershell
cd "C:\Users\sroychaudhury\OneDrive - Expedia Group\EGA Initiatives\TripPlanner_Prototype"
git checkout poc/cursor-trail-catalog-agent

git add "Travel Planner Prototype/data/trail_catalog/features/"
git add "Travel Planner Prototype/data/trail_catalog/features_manifest.json"
git add "Travel Planner Prototype/data/trail_catalog/manifest.json"
git add "Travel Planner Prototype/data/trail_catalog/parks/"
git add "Travel Planner Prototype/mcp/"
git add "Travel Planner Prototype/scripts/poc_cursor_cloud_trail_agent.py"
git add "Travel Planner Prototype/scripts/spike_cursor_dynamic_catalog.py"
git add "Travel Planner Prototype/docs/POC_CURSOR_TRAIL_AGENT.md"
git add "Travel Planner Prototype/src/wanderly/trail_catalog/"
git add "Travel Planner Prototype/src/wanderly/alltrails_feature_directory.py"
git add "Travel Planner Prototype/src/wanderly/alltrails_feature_crawl.py"
git add "Travel Planner Prototype/src/integrations/cursor_agent_client.py"
git add "Travel Planner Prototype/requirements-spike.txt"
git add "Travel Planner Prototype/.env.example"
git add "Travel Planner Prototype/tests/test_trail_catalog_search.py"
git add "Travel Planner Prototype/tests/test_alltrails_feature_directory.py"
git status
git commit -m "PoC: Cursor cloud agent with trail-catalog MCP (parks + features)"
git push origin poc/cursor-trail-catalog-agent
```

### Option B — already in `Travel Planner Prototype` (your terminal case)

```powershell
cd "C:\Users\sroychaudhury\OneDrive - Expedia Group\EGA Initiatives\TripPlanner_Prototype\Travel Planner Prototype"

git add data/trail_catalog/features/ data/trail_catalog/features_manifest.json
git add data/trail_catalog/manifest.json data/trail_catalog/parks/
git add mcp/ scripts/poc_cursor_cloud_trail_agent.py docs/POC_CURSOR_TRAIL_AGENT.md
git add src/wanderly/trail_catalog/ src/wanderly/alltrails_feature_directory.py
git add src/wanderly/alltrails_feature_crawl.py src/integrations/cursor_agent_client.py
git add requirements-spike.txt .env.example
git add tests/test_trail_catalog_search.py tests/test_alltrails_feature_directory.py
git status
git commit -m "PoC: Cursor cloud agent with trail-catalog MCP (parks + features)"
git push origin poc/cursor-trail-catalog-agent
```

After commit, confirm on GitHub that `Travel Planner Prototype/data/trail_catalog/features/waterfall.json` exists on the branch. The earlier push created the branch name but **no changes were staged** when add paths were wrong.

Set in `.env`:

```env
CURSOR_REPO_REF=poc/cursor-trail-catalog-agent
```

Merge to `main` when ready:

```powershell
git checkout main
git pull origin main
git merge poc/cursor-trail-catalog-agent
git push origin main
```

Then set `CURSOR_REPO_REF=main` (or leave unset).

**Do not commit:** `.env`, `.cache/`, large debug logs, Apify tokens.

## Local sanity checks (no API cost)

```powershell
cd "Travel Planner Prototype"

python -c "from src.wanderly.trail_catalog.search import catalog_stats; import json; print(json.dumps(catalog_stats(), indent=2))"

python -c "from src.wanderly.trail_catalog.search import search_feature_trails; print(len(search_feature_trails('waterfall', query='zion', limit=5)))"

python scripts/poc_cursor_cloud_trail_agent.py --dry-run --scenario all
```

Optional: run MCP in IDE (see `mcp/README.md`) and ask Cursor chat to call `search_manifest_features_tool`.

## Run the cloud PoC

```powershell
cd "Travel Planner Prototype"

python scripts/poc_cursor_cloud_trail_agent.py --dry-run --scenario feature

python scripts/poc_cursor_cloud_trail_agent.py --scenario feature

python scripts/poc_cursor_cloud_trail_agent.py --scenario park

python scripts/poc_cursor_cloud_trail_agent.py --scenario all
```

Results: `.cache/poc_cursor_trail_agent/<timestamp>/summary.json`

### Pass criteria

| Field | Meaning |
|--------|---------|
| `mcp_tool_events` | ≥ 1 trail-catalog tool call in SSE |
| `trail_ids_valid` | Every `trail_id` exists in local catalog for that scenario |
| `pass` | Both true |
| `agent_url` | Open run in Cursor UI for debugging |

### Scenarios

| `--scenario` | MCP path |
|--------------|----------|
| `park` | `search_manifest_parks_tool` → `search_trails_tool` (`yell`) |
| `feature` | `search_manifest_features_tool` → `search_feature_trails_tool` (`waterfall`) |
| `combined` | `search_feature_trails_tool(camping, park_code=brca)` |

## MCP tools (reference)

| Tool | Use |
|------|-----|
| `trail_catalog_stats` | Park/feature counts |
| `search_manifest_parks_tool` | Resolve `park_code` |
| `search_trails_tool` | Trails in one park |
| `get_trail_tool` | One park trail |
| `search_manifest_features_tool` | List features (waterfall, camping, …) |
| `search_feature_trails_tool` | Trails in one feature file |
| `get_feature_trail_tool` | One feature trail |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `trail_ids_valid: false`, empty tools | Catalog not on branch Cursor clones; push `features/` + manifests |
| **`trail-catalog` not found. Available: Atlassian, Figma** | Custom stdio MCP did not start in the VM (see below). Catalog still works via **repo fallback** (`python3` + `search_feature_trails`). |
| MCP never called | Run `python scripts/poc_cursor_cloud_trail_agent.py --dry-run` and confirm `mcpServers` payload; open `agent_url` logs |
| `FileNotFoundError` for catalog in cloud | Wrong repo root: set `CURSOR_MCP_CWD=Travel Planner Prototype` |
| 409 on agent runs | Wait / use fresh agent; Wanderly serializes lanes in prod |

### If MCP stays missing (only Atlassian / Figma)

The API accepts `mcpServers` (log shows `mcp_servers=1`), but the agent still cannot see `trail-catalog`. Common causes:

1. **`mcp` Python package not installed** in the cloud VM — launcher script `scripts/run_trail_catalog_mcp_stdio.sh` runs `pip install mcp` first (commit + push this script).
2. **Account / rollout** — inline stdio MCP on Cloud Agents may be limited; confirm with Cursor docs or support.
3. **PoC still valid without MCP** — agent clones repo and runs `PYTHONPATH=. python3 -c "from src.wanderly.trail_catalog.search import …"` (same metadata, not MCP transport).

Check `pass_note` in `summary.json`: `strict_mcp` = real MCP; `repo_fallback` = catalog via repo shell (your recent runs).

Local IDE test: open `Travel Planner Prototype` in Cursor; `.cursor/mcp.json` registers the server for chat (not the same as Cloud API).

## After PoC

- Wire MCP only on **trail-picker / catalog-smoke** lane in Wanderly (not full planner prompt).
- Keep feature catalogs updated via `scripts/crawl_alltrails_feature_catalog.py`.
- Optional: HTTP MCP host if stdio in cloud VM is limiting.

Related: `data/trail_catalog/SPIKE_CURSOR_DYNAMIC.md`, `scripts/spike_cursor_dynamic_catalog.py`.
