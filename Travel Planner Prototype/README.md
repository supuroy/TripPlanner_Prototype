# Multi-Stage AI Travel Planner Prototype

Minimal Python scaffold for an extensible multi-stage travel planning system using **Cursor Cloud Agents** for all LLM calls (structured JSON via prompts + Pydantic validation). **No Google / Gemini API** is used in this prototype.

## Architecture

Core modules:
- `src/orchestrator.py`: stage controller and flow runner.
- `src/session_manager.py`: in-memory session/state history.
- `src/llm/cursor_llm_client.py`: Cursor-backed client for strict JSON and plain-text planner calls.
- `src/schemas/models.py`: Pydantic input/output contracts.

Stage modules:
- `src/stages/criteria_stage.py`
- `src/stages/inspiration_stage.py`
- `src/stages/itinerary_stage.py` (placeholder)
- `src/stages/booking_stage.py` (placeholder)
- `src/stages/activities_stage.py` (placeholder)

Tool/provider placeholders:
- `src/tools/weather_provider.py`
- `src/tools/flights_provider.py`
- `src/tools/hotels_provider.py`
- `src/tools/activities_provider.py`
- `src/tools/reviews_provider.py`

UI:
- CLI: `src/ui/cli.py`
- Browser UI template: `src/ui/web.py`
- Browser UI server: `src/ui/web_server.py`

Session state now includes:
- `traveler_profile`: editable traveler/group profile object.
- `itinerary_options`: parallel itinerary option objects.
- `active_itinerary_id`: selected option for ongoing planning.
- traveler profile memory persisted at `.memory/traveler_profile.json` across restarts.
- per-itinerary `destination_enrichment` (weather/reviews/top activities), populated asynchronously.
  includes both human-readable summaries and structured module details.
- `comparison_guidance`: "help me choose" comparison across open options (Cursor on-demand when not using the local Ollama path).

## Run

1. Create and activate your venv.
2. Install dependencies:
```bash
pip install -r requirements.txt
```
3. Create `.env` from `.env.example` and set **`CURSOR_API_KEY`** and **`CURSOR_REPO_URL`** (HTTPS GitHub URL visible to the Cursor GitHub app).
4. Run:
```bash
python main.py --input "I want to go somewhere warm in March under $3000"
```

PowerShell note:
- Use single quotes around input containing `$` so `$3000` is not treated as a variable:
```powershell
python main.py --input 'I want to go somewhere warm in March under $3000'
```

Run browser UI:
```bash
python main.py --ui --host 127.0.0.1 --port 8000
```
Then open `http://127.0.0.1:8000`.

The web UI (`--ui`) and CLI both use **Cursor** for criteria, inspiration, enrichment-style tool calls, comparisons, and Wanderly **`/api/wanderly/smoke_turn`** / **`refine`**.

Replay mode (no new LLM calls, uses cached responses from file):
```bash
python main.py --replay --input "any text"
```
Default cache file: `.cache/cursor_planner_cache.json` (override with **`CURSOR_PLANNER_CACHE_PATH`**). Older `.cache/gemini_replay_cache.json` files are not read automatically.

Local comparator setup (Ollama):
1. Ensure Ollama is installed.
2. Pull model:
```powershell
ollama pull llama3.2:3b
```
3. Keep Ollama running locally (`http://127.0.0.1:11434`).

In the web UI you can:
- Run plan flow from prompt text.
- Toggle `Replay` to reuse last cached LLM outputs.
- Edit/save `traveler_profile` JSON.
- View and switch active itinerary from a dropdown.
- Generate 2 more destination options from current criteria.
- Delete selected itinerary option.
- Generate "Help Me Choose" comparison on demand (Cursor, unless using the local comparator path).
- Inspect active itinerary details as structured JSON.
- Inspect current workspace state as structured JSON.
- View informational stage breadcrumb at top.
- See enrichment status (`pending/completed/error`) for each itinerary option.

## Current Flow (v1)

Implemented end-to-end:
1. Criteria gathering (LLM + strict Pydantic validation)
2. Inspiration/destination selection (LLM + top 3 destinations)

Output is JSON with:
- inputs considered
- assumptions
- missing info
- recommendations
- reasoning summary

Later stages return validated placeholder outputs for now.

## How To Extend

### Add a new stage
1. Add input/output schemas in `src/schemas/models.py`.
2. Create a stage class in `src/stages/` implementing `run(...)`.
3. Register stage in `src/orchestrator.py` and builder in `src/main.py`.

### Plug a new tool/provider
1. Implement a class in `src/tools/` that inherits `BaseProvider`.
2. Keep a stable `fetch(query: dict) -> dict` contract.
3. Inject provider into stages/orchestrator as needed.

### Evolve orchestration
- Use `run_stage(...)` for independent stage reruns.
- Use session history (`SessionManager`) for backtracking and iterative refinement.

## Notes

- This is a prototype backbone, not production infra.
- No auth, DB, or real booking APIs are included.
- State is in-memory per run.
