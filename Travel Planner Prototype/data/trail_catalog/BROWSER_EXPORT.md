# AllTrails catalog ingest (when automation is blocked)

DataDome returns **403** to scripts. We do **not** bypass captchas with third-party solvers — that is brittle and against AllTrails' terms.

Use **your real Chrome session** once, then parse locally.

---

## Easiest: DOM scrape (no per-page JSON)

Paginated API responses (~20 trails each) are annoying. Skip them:

1. Open the park page in Chrome and click **Show more** until ~283 trails load.
2. **F12 → Console**, paste all of `data/trail_catalog/tools/scrape_park_dom.js`, press Enter.
3. JSON is copied to clipboard → save as `data/trail_catalog/imports/yellowstone_dom.json`.
4. Import:

```powershell
cd "Travel Planner Prototype"
python scripts/import_alltrails_park_export.py --json data/trail_catalog/imports/yellowstone_dom.json
```

---

## Paginated JSON (many small files)

Save each Network response as `page-01.json`, `page-02.json`, … in one folder:

```powershell
python scripts/import_alltrails_park_export.py --json-dir data\trail_catalog\imports\yellowstone_pages
```

Or pass files explicitly:

```powershell
python scripts/import_alltrails_park_export.py --json imports\p1.json --json imports\p2.json
```

All files are merged and deduped by trail URL.

---

## HAR (one export, all pages)

1. On the park page, open **Network** before clicking Show more.
2. Load the full list (Show more until done).
3. Right-click the request list → **Save all as HAR with content**.
4. Import:

```powershell
python scripts/import_alltrails_park_export.py --har data\trail_catalog\imports\yellowstone.har
```

---

## Captcha: what actually works

| Approach | Works? |
|----------|--------|
| httpx / curl_cffi / headless Playwright | Usually **no** (403 / captcha iframe) |
| "Break" captcha in code | **No** — not supported |
| You solve captcha in **headed** Playwright once | **Sometimes** |
| Scrape DOM in **your** Chrome after captcha | **Yes** (recommended) |
| Playwright attaches to **your** Chrome (CDP) | **Yes** if tab already shows the park list |

### Headed Playwright (save cookies for later)

```powershell
python scripts/crawl_alltrails_park_catalog.py --method playwright --headed --pause-for-captcha
```

Solve captcha in the window, press Enter, let it scroll Show more. Cookies saved to `.cache/alltrails_storage.json`.

Re-run with storage (may still fail if DataDome fingerprints automation):

```powershell
python scripts/crawl_alltrails_park_catalog.py --method playwright --storage-state .cache/alltrails_storage.json
```

### Attach to your real Chrome (best automation path)

1. Close other Chrome windows, then start Chrome with debugging:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:LOCALAPPDATA\AllTrailsCatalogChrome"
```

2. In that Chrome: open the park page, solve captcha, click **Show more** until the list looks complete (or let the script click it).

3. Attach and scrape:

```powershell
python scripts/crawl_alltrails_park_catalog.py --method playwright `
  --connect-cdp http://127.0.0.1:9222 `
  --headed
```

Playwright drives the tab you already passed DataDome in — it does not spawn a fresh bot profile.

---

## Dev seed (12 trails)

```powershell
copy data\trail_catalog\parks\yell_dev_seed.json data\trail_catalog\parks\yell.json
```

---

## Verify

```powershell
python -c "import json; d=json.load(open('data/trail_catalog/parks/yell.json')); print(d['trail_count']); print([t for t in d['trails'] if 'heart-lake' in t['source_url']])"
```
