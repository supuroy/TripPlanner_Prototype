# Trail regions catalog (offline)

Indexes US state/region hubs from AllTrails **Top 50 regions** directory and links them to park codes we already crawled.

**Directory:** https://www.alltrails.com/directory/regions/A/1  
**Region hub example:** https://www.alltrails.com/us/utah

## Output

```text
data/trail_catalog/
  regions_manifest.json       # index: state_slug, listing_url, trail_count, file, park_codes[]
  regions/us-utah.json        # up to 50 trails per state hub (/us/utah)
  regions/us-california.json
```

## Level 1 + 2: discover regions and crawl top trails (default)

```powershell
cd "Travel Planner Prototype"
python scripts/crawl_alltrails_region_directory.py --connect-cdp http://127.0.0.1:9222
```

This will:

1. Open `https://www.alltrails.com/directory/regions/A/1` and collect region hub links (`/us/utah`, …).
2. Visit **each** hub, scroll the listing, and save up to **50 trails** per state (default `--trail-limit 50`).

Options:

```powershell
# Manifest only (your previous run — no second level)
python scripts/crawl_alltrails_region_directory.py --connect-cdp http://127.0.0.1:9222 --no-crawl-trails

# One state smoke test
python scripts/crawl_alltrails_region_catalog.py utah --connect-cdp http://127.0.0.1:9222

# Resume batch (skip states already crawled)
python scripts/crawl_alltrails_region_directory.py --connect-cdp http://127.0.0.1:9222 --skip-existing
```

`merge_regions_with_park_catalog` also adds states that appear only in `parks/*.json` (from each park's `region` field).

## Enrich feature rows (no new crawl)

After parks + features exist, backfill geo + `park_code`:

```powershell
python scripts/enrich_feature_catalog.py
python scripts/enrich_feature_catalog.py --feature waterfall
```

Adds `country_code`, `state_slug`, `region_path`, `state_name`, and re-resolves `park_code` from the park URL index.

## MCP

| Tool | Use |
|------|-----|
| `search_manifest_regions_tool` | Find Utah / California by name |
| `parks_for_state_tool` | `park_codes` for a state |
| `search_feature_trails_tool` | `state_slug=utah`, `max_distance_mi=7` |

Prefer **`state_slug` + `query`** over many empty `park_code` probes.
