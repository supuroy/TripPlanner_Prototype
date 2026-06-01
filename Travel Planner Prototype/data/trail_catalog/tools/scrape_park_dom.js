/**
 * Run in Chrome DevTools Console on an AllTrails park listing page.
 *
 * 1. Open the park page and click "Show more" until the list stops growing.
 * 2. F12 -> Console -> paste this whole file -> Enter.
 * 3. Output is copied to clipboard (if copy() works) and logged.
 * 4. Save clipboard to data/trail_catalog/imports/yellowstone_dom.json
 * 5. python scripts/import_alltrails_park_export.py --json data/trail_catalog/imports/yellowstone_dom.json
 */
(() => {
  const seen = new Set();
  const trails = [];
  document.querySelectorAll('a[href*="/trail/"]').forEach((a) => {
    let url = (a.href || "").split("?")[0].replace(/\/$/, "");
    if (!/\/trail\/[a-z]{2}\/[^/]+\/[^/]+/i.test(url) || seen.has(url)) return;
    seen.add(url);
    const card =
      a.closest("article") ||
      a.closest("[data-testid]") ||
      a.closest("li") ||
      a.parentElement?.parentElement?.parentElement;
    const text = (card?.innerText || a.innerText || "").replace(/\s+/g, " ").trim();
    let name = (a.innerText || "").trim();
    if (!name || name.length < 3) {
      const h = card?.querySelector("h2,h3,h4");
      name = (h?.innerText || "").trim();
    }
    if (!name) {
      const slug = url.split("/").pop() || "";
      name = slug.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    }
    const diffM = text.match(/\b(Easy|Moderate|Hard|Difficult|Strenuous)\b/i);
    const miM = text.match(/([\d.]+)\s*mi\b/i);
    const ratingM = text.match(/([\d.]+)\s*\(\s*[\d,]+\s*\)/);
    trails.push({
      url,
      name: name.slice(0, 200),
      difficulty: diffM ? diffM[1] : null,
      length_mi: miM ? parseFloat(miM[1]) : null,
      rating: ratingM ? parseFloat(ratingM[1]) : null,
    });
  });
  const payload = {
    source: "dom_console_scrape",
    scraped_at: new Date().toISOString(),
    page_url: location.href,
    trail_count: trails.length,
    trails,
  };
  const json = JSON.stringify(payload, null, 2);
  console.log(`Extracted ${trails.length} trails from DOM`);
  try {
    copy(json);
    console.log("Copied JSON to clipboard — paste into yellowstone_dom.json");
  } catch (e) {
    console.log("copy() unavailable — right-click the object below -> Copy object, or save from console");
  }
  return payload;
})();
