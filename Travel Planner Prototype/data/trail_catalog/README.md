# Trail catalog (offline)

Minimal per-trail records for grounded shortlist + itinerary builds.

## If fetch_url.py shows `blocked: True` (403)

**Stop trying automated fetch** — use a one-time browser export:

**[BROWSER_EXPORT.md](BROWSER_EXPORT.md)** (step-by-step DevTools JSON import)

```powershell
python scripts/import_alltrails_park_export.py --json path\to\yellowstone_export.json
```

## 1. Test direct HTTP (usually fails)

```bash
python scripts/fetch_url.py "https://www.alltrails.com/parks/us/wyoming/yellowstone-national-park" --parse-trails
```

If `ok: True`, then: `python scripts/crawl_alltrails_park_catalog.py --method http`

## 2. Browser save (works when HTTP/Playwright are blocked)

1. Open the [Yellowstone park page](https://www.alltrails.com/parks/us/wyoming/yellowstone-national-park) in Chrome.
2. Scroll / click **Show more** until you see all ~283 trails.
3. **Save page** (Ctrl+S, “Webpage, Complete”) to e.g. `yellowstone-park.html`.
4. Build catalog:

```bash
python scripts/crawl_alltrails_park_catalog.py \
  --html-file path/to/yellowstone-park.html \
  --method html_file
```

## 3. Playwright (if captcha solvable once)

See headed + `--pause-for-captcha` flow in earlier docs; save `.cache/alltrails_storage.json`.

## 4. Apify (when quota resets)

```bash
python scripts/crawl_alltrails_park_catalog.py --method apify --max-items 320
```

## Output

`data/trail_catalog/parks/yell.json` + `manifest.json`
