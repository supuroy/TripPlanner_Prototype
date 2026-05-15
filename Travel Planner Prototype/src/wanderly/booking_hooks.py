"""
Executable booking hooks produced at itinerary-build time.

``transport_plan`` remains the legacy name on destination cards; ``booking_hooks`` is the
canonical top-level object (same semantics, richer optional fields). Book-time code must
consume hooks only — no regex inference over itinerary prose when hooks are present.
"""

from __future__ import annotations

import re
from typing import Any


def _clean_label(s: Any, *, limit: int = 120) -> str:
    return " ".join(str(s or "").split())[:limit]


def parse_day_range_nights(day_range: str, *, default: int = 1) -> int:
    """``1-3`` → 3 nights; ``4`` → 1 night at that base."""
    dr = str(day_range or "").strip()
    if not dr:
        return max(1, default)
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", dr)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if b >= a:
            return max(1, b - a + 1)
    if re.match(r"^\d+$", dr):
        return 1
    return max(1, default)


def salvage_transport_plan_primary_mode(raw: dict[str, Any]) -> str | None:
    """
    Recover ``primary_mode`` when the model returns prose or schema-doc garbage
    (e.g. ``ground_only | commercial_air``) so ``normalize_transport_plan`` can run.
    """
    pm = raw.get("primary_mode")
    if not isinstance(pm, str):
        return None
    s = pm.strip().lower()
    if not s:
        return None
    # Doc-string style: take first alternative before |
    if "|" in s:
        s = s.split("|", 1)[0].strip()
    s = s.replace(" ", "_").replace("-", "_")
    if "no_flight" in s or "no_flights" in s or "no_commercial" in s:
        return "ground_only"
    if "ground_only" in s or s in ("ground", "surface", "surface_only", "road", "road_trip", "ferry", "drive", "driving"):
        return "ground_only"
    if "commercial_air" in s or s in ("air", "fly", "flight", "flights", "commercial"):
        return "commercial_air"
    if "mixed" in s:
        return "mixed"
    return None


def normalize_transport_plan(raw: Any) -> dict[str, Any] | None:
    """Normalize ``transport_plan`` / nested hooks transport block."""
    if not isinstance(raw, dict):
        return None
    mode_raw = str(raw.get("primary_mode") or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "ground": "ground_only",
        "surface_only": "ground_only",
        "no_flight": "ground_only",
        "road_trip": "ground_only",
        "air": "commercial_air",
        "fly": "commercial_air",
        "flight": "commercial_air",
        "flights": "commercial_air",
    }
    mode = aliases.get(mode_raw, mode_raw)
    if mode not in ("ground_only", "commercial_air", "mixed"):
        fixed = salvage_transport_plan_primary_mode(raw)
        if fixed in ("ground_only", "commercial_air", "mixed"):
            mode = fixed
        else:
            return None
    out: dict[str, Any] = {
        "primary_mode": mode,
        "summary": _clean_label(raw.get("summary"), limit=500),
        "commercial_flight_segments": [],
    }
    segs = raw.get("commercial_flight_segments")
    if isinstance(segs, list):
        clean: list[dict[str, Any]] = []
        for x in segs[:12]:
            if not isinstance(x, dict):
                continue
            fr = _clean_label(x.get("from_search_label"))
            to = _clean_label(x.get("to_search_label"))
            if not fr and not to:
                continue
            seg: dict[str, Any] = {"from_search_label": fr, "to_search_label": to}
            role = str(x.get("role") or "").strip().lower()[:24]
            if role:
                seg["role"] = role
            for key in ("day_hint", "day_offset"):
                dh = x.get(key)
                if isinstance(dh, (int, float)) and int(dh) == dh and int(dh) >= 0:
                    seg["day_offset"] = int(dh)
                    break
            clean.append(seg)
        out["commercial_flight_segments"] = clean
    moves = raw.get("lodging_moves")
    if isinstance(moves, list):
        lm: list[dict[str, Any]] = []
        for x in moves[:14]:
            if not isinstance(x, dict):
                continue
            dr = str(x.get("day_range") or "").strip()[:24]
            area = _clean_label(x.get("area"))
            note = _clean_label(x.get("note"), limit=220)
            nights_raw = x.get("nights")
            nights = int(nights_raw) if isinstance(nights_raw, (int, float)) and int(nights_raw) > 0 else None
            if dr or area or note:
                row: dict[str, Any] = {"day_range": dr, "area": area, "note": note}
                if nights is not None:
                    row["nights"] = nights
                lm.append(row)
        if lm:
            out["lodging_moves"] = lm
    return _coerce_mixed_surface_only_to_ground(out, movement_text=None)


def refine_transport_plan_with_movement_text(tp: dict[str, Any], movement_text: str) -> dict[str, Any]:
    """Re-run surface-only coercion using itinerary + budget text (summary alone is often empty)."""
    return _coerce_mixed_surface_only_to_ground(tp, movement_text=movement_text)


def _coerce_mixed_surface_only_to_ground(tp: dict[str, Any], *, movement_text: str | None) -> dict[str, Any]:
    """
    Models often emit ``mixed`` for drive+ferry trips with **no** airline segments.
    That should be ``ground_only`` so booking does not assume ambiguous commercial air.
    """
    if str(tp.get("primary_mode") or "").strip().lower() != "mixed":
        return tp
    segs = tp.get("commercial_flight_segments")
    n = len([x for x in segs if isinstance(x, dict)]) if isinstance(segs, list) else 0
    if n > 0:
        return tp
    summ = str(tp.get("summary") or "").lower()
    extra = str(movement_text or "").lower()
    blob = f"{summ} {extra}".strip()
    blob_ground = bool(
        re.search(
            r"\b(bc\s*ferries|ferry|ferries|peace\s*arch|highway\s*4|hwy\s*4|"
            r"tsawwassen|horseshoe\s*bay|departure\s*bay|nanaimo|drive|driving|road\s*trip|"
            r"border|i-5|customs|vehicle\s*reservation)\b",
            blob,
        )
    )
    if blob_ground:
        out = dict(tp)
        out["primary_mode"] = "ground_only"
        out["commercial_flight_segments"] = []
        return out
    return tp


def normalize_booking_hooks(raw: Any) -> dict[str, Any] | None:
    """
    Canonical ``booking_hooks`` object. Accepts either a full hooks dict or legacy
    ``transport_plan``-shaped payload under ``transport`` / top-level keys.
    """
    if not isinstance(raw, dict):
        return None
    tp_src = raw
    if isinstance(raw.get("transport"), dict):
        tp_src = raw["transport"]
    tp = normalize_transport_plan(tp_src)
    if not tp:
        return None
    out: dict[str, Any] = {"version": 1, "transport": tp}
    prefs = raw.get("search_preferences")
    if isinstance(prefs, dict) and prefs:
        out["search_preferences"] = prefs
    return out


def resolve_booking_hooks_from_dest(dest: dict[str, Any]) -> dict[str, Any] | None:
    """Prefer ``booking_hooks``; fall back to legacy ``transport_plan``."""
    bh = dest.get("booking_hooks")
    if isinstance(bh, dict):
        norm = normalize_booking_hooks(bh)
        if norm:
            return norm
    tp = dest.get("transport_plan")
    if isinstance(tp, dict):
        norm_tp = normalize_transport_plan(tp)
        if norm_tp:
            return {"version": 1, "transport": norm_tp}
    return None


def transport_block(hooks: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(hooks, dict):
        return None
    t = hooks.get("transport")
    if isinstance(t, dict) and t.get("primary_mode"):
        return t
    return None


def booking_include_commercial_flights(dest: dict[str, Any]) -> bool | None:
    """
    When structured hooks exist, return authoritative bool.
    ``None`` only when hooks are absent (caller may use legacy heuristics).
    """
    hooks = resolve_booking_hooks_from_dest(dest)
    tp = transport_block(hooks)
    if not tp:
        return None
    mode = str(tp.get("primary_mode") or "").strip().lower()
    segs = tp.get("commercial_flight_segments")
    n_segs = len([x for x in segs if isinstance(x, dict)]) if isinstance(segs, list) else 0
    if mode == "ground_only":
        return False
    if mode == "commercial_air":
        return n_segs > 0 or True  # commercial_air implies flights even if segments empty
    if mode == "mixed":
        return n_segs > 0
    return None


def commercial_flight_segments_for_booking(
    dest: dict[str, Any],
    profile_pills: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Ordered commercial flight segments with resolved from/to labels."""
    hooks = resolve_booking_hooks_from_dest(dest)
    tp = transport_block(hooks)
    if not tp:
        return []
    segs = tp.get("commercial_flight_segments")
    if not isinstance(segs, list):
        return []
    from_profile = ""
    for p in profile_pills or []:
        if isinstance(p, dict) and str(p.get("key") or "").strip().lower() == "from":
            from_profile = _clean_label(p.get("value"))
            break
    out: list[dict[str, Any]] = []
    for i, x in enumerate(segs):
        if not isinstance(x, dict):
            continue
        fr = _clean_label(x.get("from_search_label"))
        to = _clean_label(x.get("to_search_label"))
        if not fr and from_profile and i == 0:
            fr = from_profile
        if not fr and not to:
            continue
        out.append(
            {
                "from_search_label": fr,
                "to_search_label": to,
                "role": str(x.get("role") or "").strip().lower()[:24],
                "day_offset": (
                    x.get("day_offset")
                    if isinstance(x.get("day_offset"), int)
                    else (int(x["day_hint"]) if isinstance(x.get("day_hint"), (int, float)) else None)
                ),
                "segment_index": len(out),
            }
        )
    return out


def lodging_moves_for_booking(dest: dict[str, Any]) -> list[dict[str, Any]]:
    hooks = resolve_booking_hooks_from_dest(dest)
    tp = transport_block(hooks)
    if not tp:
        return []
    moves = tp.get("lodging_moves")
    if not isinstance(moves, list):
        return []
    return [x for x in moves if isinstance(x, dict) and (x.get("area") or x.get("day_range"))]


def build_booking_hooks_from_transport_plan(tp: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(tp, dict):
        return None
    norm = normalize_transport_plan(tp)
    if not norm:
        return None
    return {"version": 1, "transport": norm}
