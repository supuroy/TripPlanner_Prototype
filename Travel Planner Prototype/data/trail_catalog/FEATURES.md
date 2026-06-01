# Trail features catalog (offline)

Indexes trails by AllTrails **trail feature** (waterfall, camping, hot springs, etc.), separate from per-park catalogs under `parks/`.

**Hub:** https://www.alltrails.com/directory/trail-features  
**Feature directory (cities, not trails):** https://www.alltrails.com/directory/trail-features/waterfall/A/1  
**City trail listings (actual trails):** https://www.alltrails.com/us/tennessee/chattanooga/waterfall

## How AllTrails structures features

1. **`/directory/trail-features/{feature}/A/N`** — Top 50 cities + A–Z directory. **No `/trail/` links** on this page.
2. **`/us/{state}/{city}/{feature}`** (or `/us/{region}/{feature}`) — Lists trails for that place + feature. The crawler discovers (1), then scrolls each (2).

## Output layout

```text
data/trail_catalog/
  features_manifest.json
  features/{feature_slug}.json
```

## Crawl (Playwright + Chrome CDP recommended)

```powershell
cd "Travel Planner Prototype"
python scripts/crawl_alltrails_feature_catalog.py waterfall --connect-cdp http://127.0.0.1:9222
```

Defaults: **50 cities** per feature (`--city-limit 50`), **1** directory page (`--max-directory-pages 1`).

**Smoke (3 cities only):**

```powershell
python scripts/crawl_alltrails_feature_catalog.py waterfall --connect-cdp http://127.0.0.1:9222 --city-limit 3
```

**More directory pages (A–Z):**

```powershell
python scripts/crawl_alltrails_feature_catalog.py waterfall --connect-cdp http://127.0.0.1:9222 --max-directory-pages 5 --city-limit 0
```

(`--city-limit 0` = no cap.)

**Batch:**

```powershell
python scripts/crawl_alltrails_feature_directory.py --features waterfall camping --connect-cdp http://127.0.0.1:9222 --city-limit 10
```

Re-crawl after the fix (old `features/*.json` with `trail_count: 0` used the wrong page type).

## Code map

| Module | Role |
|--------|------|
| `src/wanderly/alltrails_feature_directory.py` | Directory + city URL parsing |
| `src/wanderly/alltrails_feature_crawl.py` | Discover cities, crawl each city page |
| `src/wanderly/trail_catalog/write_feature.py` | Write `features/*.json` |
| `scripts/crawl_alltrails_feature_catalog.py` | One feature |
| `scripts/crawl_alltrails_feature_directory.py` | Batch |

Not wired into Wanderly or MCP yet.
