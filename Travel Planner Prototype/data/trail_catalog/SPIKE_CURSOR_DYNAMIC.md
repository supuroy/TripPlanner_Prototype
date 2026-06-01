# Spike: dynamic catalog context (MCP vs prompt injection)

Goal: prove whether **Cursor Cloud Agents** can recommend trails by **querying** the catalog (MCP or repo tools) instead of embedding the whole index in the prompt.

## Prerequisites

1. `CURSOR_API_KEY` and `CURSOR_REPO_URL` in `Travel Planner Prototype/.env` (catalog JSON **committed and pushed** to that repo/ref).
2. `pip install -r requirements-spike.txt`
3. Smoke-test MCP locally (optional but fast):

```powershell
cd "Travel Planner Prototype"
python -c "from src.wanderly.trail_catalog.search import search_trails; print(len(search_trails('yell', limit=5)))"
python mcp/trail_catalog_server.py
# Ctrl+C after it starts (stdio server)
```

## Run the spike

```powershell
cd "Travel Planner Prototype"
python scripts/spike_cursor_dynamic_catalog.py --park yell --query "backpacking heart lake"
```

### Modes

| Mode | What it tests |
|------|----------------|
| `inline` | Same as today: dump N trails into the prompt (measure size) |
| `repo` | Minimal prompt; agent must read `data/trail_catalog/` from cloned repo |
| `mcp_stdio` | Minimal prompt + `mcpServers` stdio → `mcp/trail_catalog_server.py` in cloud VM |

```powershell
python scripts/spike_cursor_dynamic_catalog.py --mode mcp_stdio --park yell
python scripts/spike_cursor_dynamic_catalog.py --mode inline --park yell --inline-limit 80
python scripts/spike_cursor_dynamic_catalog.py --mode repo --park yell
python scripts/spike_cursor_dynamic_catalog.py --mode all
```

## Pass / fail signals

Write results to `.cache/spike_cursor_dynamic/<timestamp>/summary.json`.

| Signal | Pass for dynamic approach |
|--------|---------------------------|
| `prompt_chars` (inline) | Grows with catalog; expect 50k+ for large parks |
| `tool_calls` (mcp_stdio) | ≥1 MCP tool invocation in SSE (`name` contains `mcp` or trail tool) |
| `parse_ok` | Response JSON with `selections[].trail_id` |
| `trail_ids_valid` | Every id exists in catalog for that park |
| `elapsed_ms` | Record; MCP may be slower but prompt stays small |

**Spike succeeds for product direction** if `mcp_stdio` or `repo` achieves `trail_ids_valid` with `prompt_chars` under ~4k while `inline` at full park size would exceed a practical budget.

## Why Wanderly does not use MCP yet

`cursor_agent_client.create_agent_run` did not pass `mcpServers` until this spike. Production Wanderly still loads JSON in Python and sends it in the prompt. After a successful spike, wire MCP on the **trail picker** lane only.

## Remote MCP (optional later)

Cloud Agents also support `type: http` / `sse` MCP URLs. You could host the same tools behind HTTP (with auth) if stdio in the VM is awkward. Not required for this spike.
