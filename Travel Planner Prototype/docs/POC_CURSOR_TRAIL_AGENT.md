# PoC: Cursor Cloud Agent + trail metadata (MCP)

Standalone proof of concept **before** wiring Wanderly or the Figma app. A Cloud Agent created via the Cursor API queries offline catalog JSON through MCP tools (parks + AllTrails feature crawls).

## What this proves

| Piece | Role |
|--------|------|
| `data/trail_catalog/` | Offline metadata (parks, features, manifests) |
| `mcp/trail_catalog_server.py` | Stdio MCP tools (`search_trails_tool`, `search_feature_trails_tool`, …) |
| `scripts/poc_cursor_cloud_trail_agent.py` | Creates cloud agent + runs 1–3 scenarios |
| `scripts/poc_cursor_trail_agent_session.py` | **Same agent** across follow-ups; prints MCP query log |
| `src/integrations/cursor_agent_client.py` | `create_agent_run(..., mcp_servers=…)` |

The agent gets a **small prompt** (~2k chars) and must call MCP to read catalogs cloned from GitHub in Cursor’s VM.

## Local vs Cloud MCP config

| Workspace | Config file | Launcher |
|-----------|-------------|----------|
| Monorepo root (`TripPlanner_Prototype`) | `.cursor/mcp.json` | `python Travel Planner Prototype/scripts/run_trail_catalog_mcp.py` |
| App folder (`Travel Planner Prototype`) | `Travel Planner Prototype/.cursor/mcp.json` | `python mcp/trail_catalog_server.py` (after `pip install mcp`) |

`run_trail_catalog_mcp.py` installs `mcp` then starts the server (Windows + Linux). **Do not** point local Windows MCP at `run_trail_catalog_mcp_stdio.sh` (bash only — Cloud VM / API).

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

### Persistent session (one agent, many questions, MCP query log)

Use this when you want to confirm the agent **reuses** the same Cloud Agent and you can **see each MCP tool + arguments** it issued.

```powershell
cd "Travel Planner Prototype"

# 1) Create agent + bootstrap (trail_catalog_stats via MCP)
python scripts/poc_cursor_trail_agent_session.py start

# 2) Custom follow-up: exactly 3 trails
python scripts/poc_cursor_trail_agent_session.py ask ^
  "3 moderate waterfall hikes in Utah or nearby national parks, under 7 miles"

# 3) Or a built-in scenario on the same agent
python scripts/poc_cursor_trail_agent_session.py ask --scenario park
python scripts/poc_cursor_trail_agent_session.py ask --scenario feature

# 4) Run park + feature + combined in one session
python scripts/poc_cursor_trail_agent_session.py suite --scenarios park feature combined

python scripts/poc_cursor_trail_agent_session.py status
```

| Output | Meaning |
|--------|---------|
| Console `MCP call N: …` | Tool name, `arguments` JSON, truncated `result` |
| `.cache/poc_trail_agent_session.json` | Saved `agent_id` for follow-ups |
| `.cache/poc_trail_agent_session/<timestamp>/mcp_calls.json` | Full structured MCP log per turn |
| `tool_calls_raw.json` | Raw SSE `tool_call` payloads (raise `--detail-limit` if truncated) |

`pass_note`: `strict_mcp` when `mcp_call_count` ≥ 1; `no_mcp_calls` if the model answered without MCP (failure for this PoC).

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

### Cloud agent: fix MCP (after local works)

1. **Push** repo root `.cursor/mcp.json` + `Travel Planner Prototype/scripts/run_trail_catalog_mcp.py` on `CURSOR_REPO_REF`.
2. **New Cloud Agent** (old agents may not pick up MCP config).
3. In the agent UI: **Workspace MCP** → enable **trail-catalog** (from cloned repo).
4. Chat: *List every MCP server you can use* → must include **trail-catalog** (not only Figma/Atlassian).
5. Terminal proof:
   ```powershell
   python scripts/prove_trail_catalog_cloud.py diagnose-mcp
   python scripts/poc_cursor_trail_agent_session.py prove-mcp
   ```

API `mcpServers` uses the same launcher (`python3` + `run_trail_catalog_mcp.py`). If `diagnose-mcp` still lists only Figma/Atlassian, enable **trail-catalog** in the Cloud Agent UI from the repo config.

### If MCP stays missing (only Atlassian / Figma)

1. **`mcp` not installed in VM** — use `scripts/run_trail_catalog_mcp.py` (pip install + server), not raw `mcp/trail_catalog_server.py` alone.
2. **Windows local** — do not use `bash` / `.sh` in `.cursor/mcp.json`; use `run_trail_catalog_mcp.py`.
3. **Account / rollout** — inline stdio MCP on Cloud Agents may be limited; confirm in UI + Cursor support.

Check `pass_note`: `strict_mcp` = real MCP tool calls; `mcp_ready_get_mcp_tools` = server visible via discovery.

## After PoC

- Wire MCP only on **trail-picker / catalog-smoke** lane in Wanderly (not full planner prompt).
- Keep feature catalogs updated via `scripts/crawl_alltrails_feature_catalog.py`.
- Optional: HTTP MCP host if stdio in cloud VM is limiting.

Related: `data/trail_catalog/SPIKE_CURSOR_DYNAMIC.md`, `scripts/spike_cursor_dynamic_catalog.py`.
