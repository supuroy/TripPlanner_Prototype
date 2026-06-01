"""Camp night backfill and gateway trail matching."""

from __future__ import annotations

import json
from pathlib import Path

from src.wanderly.alltrails_search_context import derive_alltrails_search_context
from src.wanderly.day_logistics import repair_destination_camp_logistics, sync_camp_nights_to_itinerary_rows


def test_derive_alltrails_context_for_wct_card() -> None:
    dest = {
        "name": "West Coast Trail (Pachena half)",
        "hook": "Gateway Pachena Bay British Columbia Canada",
        "country": "Canada",
    }
    ctx = derive_alltrails_search_context(dest)
    assert ctx is not None
    assert "west coast" in ctx["search_query"].lower() or "pachena" in ctx["search_query"].lower()


def test_sync_camp_nights_from_outdoor_adventure() -> None:
    rows = [
        {
            "day": 1,
            "day_logistics": {
                "surface_legs": [{"kind": "walk", "from_label": "A", "to_label": "B"}],
            },
        },
    ]
    oa = {"camp_nights": [{"kind": "backcountry", "label": "Michigan Creek Campsite"}]}
    out = sync_camp_nights_to_itinerary_rows(rows, oa)
    camp = out[0]["day_logistics"]["camp_night"]
    assert camp["label"] == "Michigan Creek Campsite"


def test_repair_westcoast_trip_port_renfrew_card() -> None:
    trip_path = (
        Path(__file__).resolve().parents[1] / ".cache" / "wanderly_trips" / "trip-4b2e3fd5ddcd.json"
    )
    if not trip_path.is_file():
        return
    data = json.loads(trip_path.read_text(encoding="utf-8"))
    dests = (data.get("session") or {}).get("ui_destinations") or []
    pr = next((d for d in dests if str(d.get("name") or "") == "Port Renfrew"), None)
    assert pr is not None
    fixed = repair_destination_camp_logistics(dict(pr))
    camps = [
        (int(d.get("day") or 0), (d.get("day_logistics") or {}).get("camp_night"))
        for d in fixed.get("itinerary") or []
        if isinstance(d, dict)
    ]
    hike_camps = [c for day, c in camps if c and day in (2, 3)]
    assert len(hike_camps) >= 1
