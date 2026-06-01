"""Print hike-leg rows from wanderly trip JSON files."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _ascii(s: str) -> str:
    s = s.replace("\u2192", "->").replace("\u2014", "-").replace("\u2013", "-")
    return re.sub(r"[^\x20-\x7e]", "?", s)

_CACHE = Path(__file__).resolve().parents[1] / ".cache" / "wanderly_trips"


def dests_from_trip(data: dict) -> list[dict]:
    for key in ("session", "wanderly"):
        block = data.get(key)
        if isinstance(block, dict):
            raw = block.get("last_destinations") or block.get("destinations")
            if isinstance(raw, list):
                return [d for d in raw if isinstance(d, dict)]
    raw = data.get("destinations")
    if isinstance(raw, list):
        return [d for d in raw if isinstance(d, dict)]
    return []


def main() -> None:
    paths = sorted(_CACHE.glob("trip-*.json"))
    if not paths:
        print(f"No trip files in {_CACHE}", file=sys.stderr)
        sys.exit(1)
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        print(f"\n## {p.name}\n")
        for d in dests_from_trip(data):
            name = d.get("name", "?")
            itin = d.get("itinerary") or []
            trail_lookups = d.get("trail_lookups") or {}
            oa = d.get("outdoor_adventure") or {}
            camps = oa.get("camp_nights") if isinstance(oa, dict) else []
            print(f"**Destination:** {name}")
            if camps:
                print(f"**outdoor_adventure.camp_nights:** {[c.get('label') for c in camps if isinstance(c, dict)]}")
            print()
            hdr = [
                "Day",
                "Day label",
                "Walk leg",
                "From -> To (surface_leg)",
                "trail_segment.name",
                "Plan mi",
                "elev_gain_ft",
                "camp_night",
                "AllTrails saved",
                "AT mi",
            ]
            print("| " + " | ".join(hdr) + " |")
            print("|" + "|".join(["---"] * len(hdr)) + "|")
            for day in itin:
                if not isinstance(day, dict):
                    continue
                dl = day.get("day_logistics") or {}
                if not isinstance(dl, dict):
                    continue
                segs = dl.get("surface_legs") or []
                ts = dl.get("trail_segment") if isinstance(dl.get("trail_segment"), dict) else {}
                camp = dl.get("camp_night") if isinstance(dl.get("camp_night"), dict) else {}
                walk_ix = [
                    i
                    for i, leg in enumerate(segs)
                    if isinstance(leg, dict) and (leg.get("kind") or "").lower() == "walk"
                ]
                if not walk_ix and not ts and not camp:
                    continue
                for wi in walk_ix or [-1]:
                    leg = segs[wi] if wi >= 0 else {}
                    key = f"{day.get('day')}:{wi}"
                    at = trail_lookups.get(key) if isinstance(trail_lookups, dict) else None
                    fr = (leg.get("from_label") or "-") if wi >= 0 else "-"
                    to = (leg.get("to_label") or "-") if wi >= 0 else "-"
                    route = f"{fr} -> {to}".replace("|", "/")[:70]
                    cells = [
                        str(day.get("day", "")),
                        _ascii(str((day.get("label") or "")[:40]).replace("|", "/")),
                        str(wi if wi >= 0 else "-"),
                        _ascii(route),
                        _ascii(str(ts.get("name", "")).replace("|", "/")[:35]),
                        str(ts.get("distance_mi", "")),
                        str(ts.get("elevation_gain_ft", "")),
                        _ascii(str(camp.get("label", ""))[:30].replace("|", "/")),
                        _ascii(str(at.get("name", "") if isinstance(at, dict) else "").replace("|", "/")[:35]),
                        str(at.get("length_mi", "") if isinstance(at, dict) else ""),
                    ]
                    print("| " + " | ".join(cells) + " |")
            print()


if __name__ == "__main__":
    main()
