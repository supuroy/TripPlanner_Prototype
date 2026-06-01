"""Day-by-day waypoint sheet (start / end / camp) with lat/lng for custom maps."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

from src.wanderly.outdoor_adventure import is_outdoor_adventure_profile, normalize_camp_night
from src.wanderly.route_waypoints import (
    _labels_fuzzy_match,
    _nps_coord_lookups,
    _parse_day_label_endpoints,
    collect_hike_legs,
    resolve_waypoint,
)
from src.wanderly.geo_bounds import infer_park_bounds_key, summarize_waypoint_geo_issues
from src.wanderly.stock_photo_heroes import infer_hero_park_phrase


def _walk_legs(dl: dict[str, Any]) -> list[dict[str, Any]]:
    surf = dl.get("surface_legs")
    if not isinstance(surf, list):
        return []
    return [
        leg
        for leg in surf
        if isinstance(leg, dict) and str(leg.get("kind") or "").strip().lower() == "walk"
    ]


def _day_hike_endpoints(day_row: dict[str, Any]) -> tuple[str | None, str | None]:
    dl = day_row.get("day_logistics")
    if not isinstance(dl, dict):
        dl = {}
    walks = _walk_legs(dl)
    if walks:
        fr = str(walks[0].get("from_label") or "").strip() or None
        to = str(walks[-1].get("to_label") or "").strip() or None
        if fr or to:
            return fr, to
    parsed = _parse_day_label_endpoints(str(day_row.get("label") or ""))
    if parsed:
        return parsed[0], parsed[1]
    camp = normalize_camp_night(dl.get("camp_night"))
    base = str(day_row.get("base") or "").strip() or None
    if camp and base:
        return base, str(camp.get("label") or "")
    if camp:
        return None, str(camp.get("label") or "")
    return None, None


def _export_csv(days: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["day", "role", "label", "lat", "lng", "source"])
    for day in days:
        day_num = day.get("day")
        for role in ("hike_start", "hike_end", "camp"):
            pt = day.get(role)
            if not isinstance(pt, dict):
                continue
            label = str(pt.get("label") or "").strip()
            if not label:
                continue
            writer.writerow(
                [
                    day_num,
                    role,
                    label,
                    pt.get("lat") if pt.get("lat") is not None else "",
                    pt.get("lng") if pt.get("lng") is not None else "",
                    pt.get("source") or "",
                ]
            )
    return buf.getvalue()


def _export_geojson(days: list[dict[str, Any]], *, destination_name: str | None) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for day in days:
        day_num = int(day.get("day") or 0)
        for role in ("hike_start", "hike_end", "camp"):
            pt = day.get(role)
            if not isinstance(pt, dict):
                continue
            lat, lng = pt.get("lat"), pt.get("lng")
            if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
                continue
            label = str(pt.get("label") or "").strip()
            if not label:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [float(lng), float(lat)]},
                    "properties": {
                        "day": day_num,
                        "role": role,
                        "label": label,
                        "source": pt.get("source"),
                        "destination": destination_name,
                    },
                }
            )
    return {"type": "FeatureCollection", "features": features}


def build_itinerary_waypoint_sheet(
    *,
    destination_id: str | None = None,
    destination_name: str | None = None,
    destination_country: str | None = None,
    destination_hook: str | None = None,
    itinerary: list[dict[str, Any]] | None = None,
    nps_outdoor: dict[str, Any] | None = None,
    trail_lookups: dict[str, Any] | None = None,
    profile_pills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build per-day start / end / camp coordinates for user-exported maps."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    out: dict[str, Any] = {
        "ok": False,
        "fetched_at": fetched_at,
        "destination_id": destination_id,
        "destination_name": destination_name,
        "days": [],
        "resolved_count": 0,
        "unresolved_count": 0,
        "export": {"csv": "", "geojson": {"type": "FeatureCollection", "features": []}},
    }

    if profile_pills is not None and not is_outdoor_adventure_profile(profile_pills):
        out["message"] = "Waypoint sheets are for hiking/camping trips only."
        return out

    hike_legs = collect_hike_legs(itinerary)
    rows = sorted(
        [d for d in (itinerary or []) if isinstance(d, dict)],
        key=lambda x: int(x.get("day") or 0),
    )
    if not rows and not hike_legs:
        out["message"] = "No itinerary days yet — build the plan first."
        return out

    park = infer_hero_park_phrase(
        destination_id=destination_id,
        name=destination_name,
        hook=destination_hook,
    )
    bounds_key = infer_park_bounds_key(
        park_phrase=park,
        destination_id=destination_id,
        destination_name=destination_name,
        destination_hook=destination_hook,
    )
    nps_payload = nps_outdoor if isinstance(nps_outdoor, dict) else None
    camp_matches = (nps_payload or {}).get("camp_matches") if nps_payload else None
    nps_by_label, nps_by_site, nps_by_place = _nps_coord_lookups(
        camp_matches if isinstance(camp_matches, list) else None,
    )
    country = (destination_country or "").strip() or "United States"
    coord_cache: dict[str, Any] = {}
    out["park_phrase"] = park

    day_out: list[dict[str, Any]] = []
    resolved = 0
    unresolved = 0

    def _pt(place: str | None, role: str) -> dict[str, Any] | None:
        nonlocal resolved, unresolved
        if not place:
            return None
        wp = resolve_waypoint(
            place,
            park=park,
            country=country,
            nps_by_label=nps_by_label,
            nps_by_site=nps_by_site,
            nps_by_place=nps_by_place,
            trail_lookups=trail_lookups,
            cache=coord_cache,
            bounds_key=bounds_key,
        )
        wp["role"] = role
        if wp.get("lat") is not None:
            resolved += 1
        else:
            unresolved += 1
        return wp

    for day_row in rows:
        day_num = int(day_row.get("day") or 0)
        if day_num < 1:
            continue
        label = str(day_row.get("label") or "").strip()
        dl = day_row.get("day_logistics") if isinstance(day_row.get("day_logistics"), dict) else {}
        hike_start_lab, hike_end_lab = _day_hike_endpoints(day_row)
        camp = normalize_camp_night(dl.get("camp_night"))
        camp_lab = str(camp.get("label") or "").strip() if camp else None

        hike_start = _pt(hike_start_lab, "hike_start")
        hike_end = _pt(hike_end_lab, "hike_end")
        camp_pt = None
        if camp_lab:
            if hike_end and _labels_fuzzy_match(camp_lab, str(hike_end.get("label") or "")):
                camp_pt = {**hike_end, "role": "camp"}
            else:
                camp_pt = _pt(camp_lab, "camp")

        notes: list[str] = []
        if camp_pt and camp_pt.get("source") == "unresolved":
            notes.append("Camp coordinates not resolved — use NPS backcountry map or your permit.")
        if hike_start and hike_start.get("source") == "unresolved":
            notes.append("Start point unresolved — try NPS enrich or AllTrails lookup.")
        for pt in (hike_start, hike_end, camp_pt):
            if isinstance(pt, dict) and pt.get("source") == "rejected_out_of_bounds":
                notes.append(
                    "Coordinate rejected — outside expected park bounds "
                    "(stale or wrong trail lookup; rebuild without old per-leg AllTrails data)."
                )
                break

        day_out.append(
            {
                "day": day_num,
                "label": label,
                "hike_start": hike_start,
                "hike_end": hike_end,
                "camp": camp_pt,
                "notes": notes,
            }
        )

    out["days"] = day_out
    out["resolved_count"] = resolved
    out["unresolved_count"] = unresolved
    geo_summary = summarize_waypoint_geo_issues(day_out, bounds_key=bounds_key)
    out["geo_validation"] = geo_summary
    if geo_summary.get("rejected_count"):
        out["ok"] = False
    else:
        out["ok"] = bool(day_out) and resolved >= 1
    out["export"] = {
        "csv": _export_csv(day_out),
        "geojson": _export_geojson(day_out, destination_name=destination_name),
    }
    if not out["ok"]:
        out["message"] = (
            "No coordinates resolved yet. Run Check NPS and refresh AllTrails lookups, "
            "then rebuild this sheet."
        )
    elif unresolved:
        out["message"] = (
            f"{resolved} point(s) with coordinates; {unresolved} still unresolved "
            "(labels only — copy for your own map research)."
        )
    else:
        out["message"] = (
            f"All {resolved} waypoint(s) have coordinates. Export CSV or GeoJSON for your map app."
        )

    return out
