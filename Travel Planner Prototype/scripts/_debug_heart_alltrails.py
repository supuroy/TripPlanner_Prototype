"""Debug Heart Lake alltrails context after rebuild."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load repo .env
for env_path in (ROOT.parent / ".env", ROOT / ".env"):
    if not env_path.is_file():
        continue
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from src.wanderly.alltrails_provider import apify_token
from src.wanderly.alltrails_search_context import (
    alltrails_verify_on_build_enabled,
    derive_alltrails_search_context,
    normalize_alltrails_search_context,
    verify_parent_trail_on_itinerary,
)

dest = {
    "id": "heart-lake-trail",
    "name": "Heart Lake Trail",
    "country": "United States",
    "hook": "Yellowstone National Park",
}
print("APIFY token set:", bool(apify_token()))
print("verify enabled:", alltrails_verify_on_build_enabled())
ctx = derive_alltrails_search_context(dest, itinerary_rows=[])
print("derived:", json.dumps(ctx, indent=2))
if ctx:
    out = verify_parent_trail_on_itinerary(
        {**dest, "alltrails_search_context": ctx},
        alltrails_context=ctx,
    )
    final = out.get("alltrails_search_context")
    print("after verify:", json.dumps(final, indent=2))

trip = ROOT / ".cache" / "wanderly_trips" / "trip-0303b791485e.json"
if trip.is_file():
    t = json.loads(trip.read_text(encoding="utf-8"))
    for d in (t.get("session") or {}).get("ui_destinations") or []:
        if "heart" in (d.get("name") or "").lower():
            print("\nON DISK:", json.dumps(d.get("alltrails_search_context"), indent=2))
