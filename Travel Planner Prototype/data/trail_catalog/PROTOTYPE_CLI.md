# Grounded trail catalog — CLI prototype

Tests the [hybrid catalog plan](~/.cursor/plans/grounded_trail_catalog_73503561.plan.md) without the web UI.

## Flow

1. **Park filter** — resolve `yell` from "Yellowstone" (NPS aliases).
2. **Pre-filter** — `long` / `backpacking_candidate` / ≥8 mi (~145 trails for Yellowstone).
3. **LLM picker** — choose 2 `trail_id`s from compact rows only (validated server-side).
4. **Destination cards** — built from catalog (`grounding.trail_id`, `source_url`).
5. **Itinerary LLM** — one call per trail; must echo catalog grounding.
6. **Validator** — checks `grounding` and trail name / URL presence.

## Commands

```powershell
cd "Travel Planner Prototype"

# Dry-run: catalog + heuristic picks (no Cursor API)
python scripts/grounded_trail_prototype.py --dry-run

# Full run: 2 itineraries, 4 days / 3 nights (needs Cursor API key in .env)
python scripts/grounded_trail_prototype.py `
  --message "4 days 3 nights backpacking in Yellowstone" `
  --days 4 --nights 3 --itineraries 2

# Output (default)
# .cache/grounded_trail_prototype_last.json
```

Requires `data/trail_catalog/parks/yell.json` (283-trail catalog).
