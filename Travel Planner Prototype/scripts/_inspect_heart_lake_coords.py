"""One-off: inspect Heart Lake trip JSON coords and geo rejection."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.wanderly.geo_bounds import apply_geo_bounds_to_waypoint, infer_park_bounds_key
from src.wanderly.itinerary_waypoint_sheet import build_itinerary_waypoint_sheet
from src.wanderly.route_waypoints import _extract_coords_from_trail_record

trip_path = ROOT / ".cache" / "wanderly_trips" / "trip-a789be33782e.json"
t = json.loads(trip_path.read_text(encoding="utf-8"))
dests = t.get("session", {}).get("last_destinations") or t.get("destinations") or []
h = next(d for d in dests if d.get("id") == "heart-lake-trail")

print("=== Destination ===")
print("id:", h["id"])
print("name:", h["name"])
oa = h.get("outdoor_adventure") or {}
print("outdoor alltrails:", oa.get("alltrails_url") or oa.get("preferred_alltrails_url"))
print("preferred_alltrails_url field:", h.get("preferred_alltrails_url"))

tl = h.get("trail_lookups") or {}
print("trail_lookups keys:", list(tl.keys())[:5], "... total", len(tl))

# first lookup entry trail coords
for k, entry in list(tl.items())[:1]:
    trail = entry.get("trail") or entry.get("source_raw") or entry
    if isinstance(trail, dict):
        coords = _extract_coords_from_trail_record(trail)
        print(f"\n=== AllTrails match: {entry.get('name') or trail.get('name')} ===")
        print("trailUrl:", trail.get("trailUrl") or trail.get("url"))
        print("areaName:", trail.get("areaName"), "cityName:", trail.get("cityName"))
        print("state:", trail.get("state"), "slug:", trail.get("slug"))
        print("extracted coords:", coords)
        if coords:
            lat, lng = coords
            bk = infer_park_bounds_key(destination_id=h["id"], destination_name=h["name"], destination_hook=h.get("hook"))
            print("bounds_key:", bk)
            wp = apply_geo_bounds_to_waypoint(
                {"label": "test", "lat": lat, "lng": lng, "source": "trail_lookup"},
                bounds_key=bk,
            )
            print("after geo_bounds:", wp)

print("\n=== NPS camp_matches ===")
for m in (h.get("nps_outdoor") or {}).get("camp_matches") or []:
    print(" ", m.get("plan_camp_label"), "->", m.get("status"), m.get("notes", "")[:60])

print("\n=== Build waypoint sheet (live) ===")
sheet = build_itinerary_waypoint_sheet(
    destination=h,
    profile_pills=t.get("session", {}).get("last_profile_pills") or [],
)
print("ok:", sheet.get("ok"), "message:", sheet.get("message"))
print("geo_validation:", sheet.get("geo_validation"))
for d in sheet.get("days") or []:
    print(f"Day {d['day']}:")
    for role in ("hike_start", "hike_end", "camp"):
        pt = d.get(role) or {}
        if pt:
            print(f"  {role}: {pt.get('label')!r} lat={pt.get('lat')} lng={pt.get('lng')} src={pt.get('source')}")
    if d.get("notes"):
        print("  notes:", d["notes"])
