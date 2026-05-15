"""
Per-day URL-oriented logistics (parallel to prose ``items`` / ``day_transport``).

Models emit ``day_logistics`` on itinerary rows; we normalize and persist so booking
code can build Expedia-style queries without regex over bullets.
"""

from __future__ import annotations

import re
from typing import Any


def _clean(s: Any, *, limit: int) -> str:
    return " ".join(str(s or "").split())[:limit]


def _coerce_iso_date(s: str) -> str | None:
    t = s.strip()[:32]
    return t if re.match(r"^\d{4}-\d{2}-\d{2}$", t) else None


def normalize_day_logistics(raw: Any) -> dict[str, Any] | None:
    """
    Return a compact dict or None when there is nothing concrete to store.

    Shape::
        {
          "flight": null | {"segments": [{"from_search_label", "to_search_label", "role?"}]},
          "lodging": null | {
              "destination_search_label", "area_hint?", "check_in?", "check_out?",
              "rooms?", "guests"?: {adults, children, infants_in_seat},
              "traveler_party"?: {total, adults, children, infants_in_seat} (mirrors guests; total = sum),
              "pet_friendly?" (bool true|false),
              "room_preferences"?: [str],
              "booking_factors"?: [str]  # short OTA-relevant tags (ADA, late arrival, EV parking, …)
          },
          "surface_legs"?: [{"kind", "from_label?", "to_label?", "operator_hint?"}]
        }
    """
    if not isinstance(raw, dict):
        return None

    out: dict[str, Any] = {"flight": None, "lodging": None}

    flight_raw = raw.get("flight")
    if isinstance(flight_raw, dict):
        segs_in = flight_raw.get("segments")
        segs: list[dict[str, str]] = []
        if isinstance(segs_in, list):
            for s in segs_in[:4]:
                if not isinstance(s, dict):
                    continue
                fr = _clean(s.get("from_search_label"), limit=120)
                to = _clean(s.get("to_search_label"), limit=120)
                if not fr and not to:
                    continue
                seg: dict[str, str] = {}
                if fr:
                    seg["from_search_label"] = fr
                if to:
                    seg["to_search_label"] = to
                role = str(s.get("role") or "").strip().lower()
                if role in ("inbound", "within_trip", "return"):
                    seg["role"] = role
                segs.append(seg)
        if segs:
            out["flight"] = {"segments": segs}

    lod_raw = raw.get("lodging")
    if isinstance(lod_raw, dict):
        lod: dict[str, Any] = {}
        dsl = _clean(lod_raw.get("destination_search_label"), limit=120)
        if dsl:
            lod["destination_search_label"] = dsl
        ah = _clean(lod_raw.get("area_hint"), limit=96)
        if ah:
            lod["area_hint"] = ah
        for key in ("check_in", "check_out"):
            v = lod_raw.get(key)
            if isinstance(v, str):
                d = _coerce_iso_date(v)
                if d:
                    lod[key] = d
        rooms = lod_raw.get("rooms")
        if isinstance(rooms, (int, float)) and 1 <= int(rooms) <= 9:
            lod["rooms"] = int(rooms)
        tp_in = lod_raw.get("traveler_party")
        g = lod_raw.get("guests")
        if isinstance(g, dict):
            gg: dict[str, int] = {}
            for key, cap in (("adults", 20), ("children", 20), ("infants_in_seat", 10)):
                n = g.get(key)
                if isinstance(n, (int, float)) and 0 <= int(n) <= cap:
                    gg[key] = int(n)
            if gg:
                lod["guests"] = gg
        elif isinstance(tp_in, dict):
            ta = tp_in.get("adults")
            tc = tp_in.get("children")
            ti = tp_in.get("infants_in_seat")
            gg3: dict[str, int] = {}
            if isinstance(ta, (int, float)) and 1 <= int(ta) <= 20:
                gg3["adults"] = int(ta)
            if isinstance(tc, (int, float)) and 0 <= int(tc) <= 20:
                gg3["children"] = int(tc)
            if isinstance(ti, (int, float)) and 0 <= int(ti) <= 10:
                gg3["infants_in_seat"] = int(ti)
            if gg3:
                gg3.setdefault("adults", 1)
                gg3.setdefault("children", 0)
                gg3.setdefault("infants_in_seat", 0)
                lod["guests"] = gg3
        g2 = lod.get("guests")
        if isinstance(g2, dict):
            a = int(g2.get("adults", 0) or 0)
            c = int(g2.get("children", 0) or 0)
            i = int(g2.get("infants_in_seat", 0) or 0)
            tot = a + c + i
            if tot >= 1:
                lod["traveler_party"] = {"total": tot, "adults": a, "children": c, "infants_in_seat": i}
        factors = lod_raw.get("booking_factors")
        if isinstance(factors, list):
            lod["booking_factors"] = [
                str(x).strip()[:72] for x in factors[:10] if isinstance(x, str) and str(x).strip()
            ]
        pf = lod_raw.get("pet_friendly")
        if pf is True:
            lod["pet_friendly"] = True
        elif pf is False:
            lod["pet_friendly"] = False
        prefs = lod_raw.get("room_preferences")
        if isinstance(prefs, list):
            lod["room_preferences"] = [
                str(x).strip()[:48] for x in prefs[:12] if isinstance(x, str) and str(x).strip()
            ]
        if lod:
            out["lodging"] = lod

    surf = raw.get("surface_legs")
    legs: list[dict[str, str]] = []
    if isinstance(surf, list):
        allowed = frozenset({"drive", "ferry", "train", "walk", "other"})
        for leg in surf[:8]:
            if not isinstance(leg, dict):
                continue
            kind = str(leg.get("kind") or "").strip().lower()
            if kind not in allowed:
                continue
            row_leg: dict[str, str] = {"kind": kind}
            for fk in ("from_label", "to_label", "operator_hint"):
                v = leg.get(fk)
                if isinstance(v, str) and v.strip():
                    row_leg[fk] = v.strip()[:120]
            if len(row_leg) > 1:
                legs.append(row_leg)
    if legs:
        out["surface_legs"] = legs

    if out["flight"] is None and out["lodging"] is None and "surface_legs" not in out:
        return None
    return out


def _pill_str(profile_pills: list[dict[str, Any]], key: str) -> str:
    k0 = (key or "").strip().lower()
    for p in profile_pills or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("key") or "").strip().lower() != k0:
            continue
        return str(p.get("value") or "").strip()
    return ""


def profile_implies_traveling_with_pet(profile_pills: list[dict[str, Any]]) -> bool:
    """True when profile copy mentions a dog/cat/pet (used to default ``pet_friendly`` on lodging)."""
    blob = f"{_pill_str(profile_pills, 'who')} {_pill_str(profile_pills, 'vibe')}".lower()
    if not blob.strip():
        return False
    return bool(re.search(r"\b(dog|dogs|pup|puppy|cat|cats|kitten|pet|pets|canine|feline)\b", blob))


def profile_default_guests(profile_pills: list[dict[str, Any]]) -> dict[str, int] | None:
    """Parse ``who`` pill for headcount; returns None when nothing usable."""
    who = _pill_str(profile_pills, "who").lower()
    if not who:
        return None
    adults = 2
    children = 0
    infants = 0
    m = re.search(r"(\d+)\s*(?:adult|adults)\b", who)
    if m:
        adults = max(1, min(20, int(m.group(1))))
    m2 = re.search(r"(\d+)\s*(?:child|children|kids?)\b", who)
    if m2:
        children = max(0, min(20, int(m2.group(1))))
    m3 = re.search(r"(\d+)\s*(?:infant|infants)\b", who)
    if m3:
        infants = max(0, min(10, int(m3.group(1))))
    # Wordy "three adults" style (common in UI copy)
    if m is None and re.search(r"\bthree\b.*\badults?\b", who):
        adults = 3
    if m is None and re.search(r"\bfour\b.*\badults?\b", who):
        adults = 4
    m4 = re.search(r"(\d+)\s*(?:traveler|travelers|people|guests|pax)\b", who)
    if m is None and m4:
        adults = max(1, min(20, int(m4.group(1))))
    return {"adults": adults, "children": children, "infants_in_seat": infants}


def enrich_itinerary_rows_lodging_from_profile(
    rows: list[dict[str, Any]],
    profile_pills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Fill missing lodging URL fields from profile so OTA builders do not infer from prose.

    When ``who`` / ``vibe`` mention a pet, sets ``pet_friendly`` true and adds a pet preference string
    if none present. Fills ``guests`` / ``rooms`` when absent.
    """
    pet_trip = profile_implies_traveling_with_pet(profile_pills)
    guests_def = profile_default_guests(profile_pills)
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        r = dict(row)
        dl = r.get("day_logistics")
        if not isinstance(dl, dict):
            out.append(r)
            continue
        lod = dl.get("lodging")
        if not isinstance(lod, dict) or not str(lod.get("destination_search_label") or "").strip():
            out.append(r)
            continue
        lod2: dict[str, Any] = dict(lod)
        if guests_def:
            merged: dict[str, int] = {k: int(v) for k, v in guests_def.items()}
            ex = lod2.get("guests")
            if isinstance(ex, dict):
                for key, cap in (("adults", 20), ("children", 20), ("infants_in_seat", 10)):
                    n = ex.get(key)
                    if isinstance(n, (int, float)) and 0 <= int(n) <= cap:
                        merged[key] = int(n)
            lod2["guests"] = merged
        rm = lod2.get("rooms")
        if not isinstance(rm, (int, float)) or not (1 <= int(rm) <= 9):
            lod2["rooms"] = 1
        else:
            lod2["rooms"] = int(rm)
        if pet_trip:
            lod2["pet_friendly"] = True
            prefs_in = lod2.get("room_preferences")
            prefs: list[str] = (
                [str(x).strip()[:48] for x in prefs_in if isinstance(x, str) and str(x).strip()][:12]
                if isinstance(prefs_in, list)
                else []
            )
            low = [p.lower() for p in prefs]
            if not any("pet" in p for p in low):
                prefs = (prefs + ["pet-friendly property"])[:12]
            lod2["room_preferences"] = prefs
        merged_raw: dict[str, Any] = {
            "flight": dl.get("flight"),
            "lodging": lod2,
            "surface_legs": dl.get("surface_legs"),
        }
        new_dl = normalize_day_logistics(merged_raw)
        if new_dl:
            r["day_logistics"] = new_dl
        out.append(r)
    return out
