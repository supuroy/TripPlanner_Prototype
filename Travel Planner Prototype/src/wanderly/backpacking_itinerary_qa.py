"""Deterministic QA for multi-day backpacking / hiking itineraries."""

from __future__ import annotations

import re
from typing import Any

from src.wanderly.outdoor_adventure import is_backpacking_or_multiday_hike, normalize_camp_night
from src.wanderly.route_waypoints import _labels_fuzzy_match, _parse_day_label_endpoints
from src.wanderly.stock_photo_heroes import infer_hero_park_phrase

_SEVERITY_ERROR = "error"
_SEVERITY_WARNING = "warning"
_SEVERITY_INFO = "info"

# Other U.S. parks that often appear when the LLM mixes destinations (generic guard).
_OTHER_PARK_MARKERS: tuple[tuple[str, str], ...] = (
    ("grand teton", "Grand Teton"),
    ("glacier national", "Glacier"),
    ("yosemite", "Yosemite"),
    ("zion national", "Zion"),
    ("rocky mountain national", "Rocky Mountain"),
    ("olympic national", "Olympic"),
    ("banff", "Banff"),
    ("jasper", "Jasper"),
)


def _issue(
    *,
    code: str,
    severity: str,
    message: str,
    day: int | None = None,
    suggestion: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if day is not None:
        row["day"] = day
    if suggestion:
        row["suggestion"] = suggestion
    return row


def _label_chain_segments(label: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s*→\s*|\s*->\s*", label or "") if p.strip()]


def _walk_legs(dl: dict[str, Any]) -> list[dict[str, Any]]:
    surf = dl.get("surface_legs")
    if not isinstance(surf, list):
        return []
    return [
        leg
        for leg in surf
        if isinstance(leg, dict) and str(leg.get("kind") or "").strip().lower() == "walk"
    ]


def _is_gateway_day(row: dict[str, Any]) -> bool:
    dl = row.get("day_logistics")
    if not isinstance(dl, dict):
        return False
    if dl.get("flight"):
        return True
    lodging = dl.get("lodging")
    camp = normalize_camp_night(dl.get("camp_night"))
    if lodging and not camp:
        return True
    return False


def _is_likely_exit_day(row: dict[str, Any], *, day_num: int, max_day: int) -> bool:
    if day_num >= max_day:
        return True
    dl = row.get("day_logistics")
    if not isinstance(dl, dict):
        return False
    if dl.get("flight"):
        fr = dl.get("flight")
        if isinstance(fr, dict) and str(fr.get("role") or "").lower() in ("return", "outbound"):
            return True
    label = str(row.get("label") or "").lower()
    if re.search(r"\b(exit|fly home|depart|return flight|travel day)\b", label):
        return True
    return False


def _park_mismatch_in_text(text: str, home_park: str | None) -> str | None:
    if not home_park:
        return None
    blob = (text or "").lower()
    home = home_park.lower()
    if home not in blob:
        for marker, display in _OTHER_PARK_MARKERS:
            if marker in blob and marker not in home:
                return display
    return None


def _first_trailhead_token(rows: list[dict[str, Any]]) -> str | None:
    for row in sorted(rows, key=lambda r: int(r.get("day") or 0)):
        for leg in _walk_legs(row.get("day_logistics") or {}):
            fr = str(leg.get("from_label") or "").strip()
            if re.search(r"\bTH\b|trailhead", fr, re.I):
                return fr
        parsed = _parse_day_label_endpoints(str(row.get("label") or ""))
        if parsed:
            fr = parsed[0]
            if re.search(r"\bTH\b|trailhead", fr, re.I):
                return fr
    return None


def normalize_chained_day_label(label: str) -> str:
    """Keep only the first arrow-delimited segment (primary hike for the day)."""
    parts = _label_chain_segments(label)
    if not parts:
        return (label or "").strip()[:200]
    return parts[0][:200]


def validate_backpacking_itinerary(
    destination: dict[str, Any],
    *,
    profile_pills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Return ``{ ok, issues[], error_count, warning_count }`` for a destination card.

  Runs for backpacking / multi-day hike profiles or when ``outdoor_adventure`` is present.
    """
    pills = profile_pills or []
    oa = destination.get("outdoor_adventure")
    if not is_backpacking_or_multiday_hike(pills) and not oa:
        return {"ok": True, "issues": [], "error_count": 0, "warning_count": 0, "skipped": True}

    itin = destination.get("itinerary")
    if not isinstance(itin, list) or not itin:
        return {"ok": True, "issues": [], "error_count": 0, "warning_count": 0}

    rows = [dict(x) for x in itin if isinstance(x, dict)]
    rows.sort(key=lambda r: int(r.get("day") or 0))
    issues: list[dict[str, Any]] = []

    home_park = infer_hero_park_phrase(
        destination_id=str(destination.get("id") or "") or None,
        name=str(destination.get("name") or "") or None,
        hook=str(destination.get("hook") or "") or None,
    )
    max_day = max(int(r.get("day") or 0) for r in rows) if rows else 0
    trailhead = _first_trailhead_token(rows)

    # --- Per-day checks ---
    prev_night: str | None = None
    for row in rows:
        day_num = int(row.get("day") or 0)
        if day_num < 1:
            continue
        label = str(row.get("label") or "").strip()
        dl = row.get("day_logistics") if isinstance(row.get("day_logistics"), dict) else {}
        gateway = _is_gateway_day(row)
        exit_day = _is_likely_exit_day(row, day_num=day_num, max_day=max_day)

        chain = _label_chain_segments(label)
        if len(chain) > 1:
            issues.append(
                _issue(
                    code="LABEL_CHAINED",
                    severity=_SEVERITY_WARNING,
                    day=day_num,
                    message=f"Day {day_num} label chains {len(chain)} segments — use a single «A to B» title.",
                    suggestion="Refine: rewrite day labels to one hike segment each (no pasted prior days).",
                )
            )

        walks = _walk_legs(dl)
        camp = normalize_camp_night(dl.get("camp_night"))
        trail_seg = dl.get("trail_segment") if isinstance(dl.get("trail_segment"), dict) else None

        if (camp or trail_seg) and not walks and not gateway:
            issues.append(
                _issue(
                    code="MISSING_WALK_LEG",
                    severity=_SEVERITY_ERROR,
                    day=day_num,
                    message=f"Day {day_num} has camp or trail stats but no walk surface leg.",
                    suggestion="Rebuild itinerary or refine: add a walk leg with from_label and to_label.",
                )
            )

        if exit_day and camp and str(camp.get("kind") or "").lower() == "backcountry":
            issues.append(
                _issue(
                    code="EXIT_WITH_CAMP",
                    severity=_SEVERITY_ERROR,
                    day=day_num,
                    message=f"Day {day_num} looks like an exit/travel day but still assigns a backcountry camp.",
                    suggestion="Refine: remove camp_night on fly-home or gateway exit days.",
                )
            )

        for leg in walks:
            fr = str(leg.get("from_label") or "").strip()
            to = str(leg.get("to_label") or "").strip()
            blob = f"{fr} {to} {label}"
            foreign = _park_mismatch_in_text(blob, home_park)
            if foreign and home_park:
                issues.append(
                    _issue(
                        code="CROSS_PARK_HIKE",
                        severity=_SEVERITY_ERROR,
                        day=day_num,
                        message=f"Day {day_num} references {foreign} but this card is for {home_park}.",
                        suggestion="Refine: keep all hike endpoints inside the same park as this destination.",
                    )
                )

            if trailhead and day_num > 1 and to and _labels_fuzzy_match(to, trailhead):
                issues.append(
                    _issue(
                        code="RETURN_TO_TRAILHEAD",
                        severity=_SEVERITY_ERROR,
                        day=day_num,
                        message=f"Day {day_num} returns to the start trailhead after the first hiking day.",
                        suggestion="Refine: end at the published exit or next camp — not the day-1 trailhead.",
                    )
                )

        if walks and camp:
            primary = walks[-1]
            to_lab = str(primary.get("to_label") or "").strip()
            camp_lab = str(camp.get("label") or "").strip()
            if to_lab and camp_lab and not _labels_fuzzy_match(to_lab, camp_lab):
                issues.append(
                    _issue(
                        code="CAMP_WALK_MISMATCH",
                        severity=_SEVERITY_WARNING,
                        day=day_num,
                        message=f"Day {day_num} walk ends at «{to_lab[:60]}» but camp is «{camp_lab[:60]}».",
                        suggestion="Refine: align walk to_label with camp_night.label.",
                    )
                )

        if not gateway and walks and prev_night and day_num > 1:
            fr0 = str(walks[0].get("from_label") or "").strip()
            if fr0 and not _labels_fuzzy_match(fr0, prev_night):
                issues.append(
                    _issue(
                        code="BREAK_IN_CHAIN",
                        severity=_SEVERITY_ERROR,
                        day=day_num,
                        message=(
                            f"Day {day_num} starts at «{fr0[:50]}» but previous night was "
                            f"«{prev_night[:50]}»."
                        ),
                        suggestion="Refine: day N+1 walk from_label must match day N camp (or trailhead).",
                    )
                )

        if camp and not exit_day:
            prev_night = str(camp.get("label") or "")
        elif walks and not exit_day:
            to_end = str(walks[-1].get("to_label") or "").strip()
            if to_end:
                prev_night = to_end

    error_count = sum(1 for x in issues if x.get("severity") == _SEVERITY_ERROR)
    warning_count = sum(1 for x in issues if x.get("severity") == _SEVERITY_WARNING)
    ok = error_count == 0
    return {
        "ok": ok,
        "issues": issues,
        "error_count": error_count,
        "warning_count": warning_count,
    }


def apply_backpacking_itinerary_qa(
    destination: dict[str, Any],
    *,
    profile_pills: list[dict[str, Any]] | None = None,
    normalize_labels: bool = True,
) -> dict[str, Any]:
    """Attach ``itinerary_qa``; optionally normalize chained day labels."""
    dest = dict(destination)
    itin = dest.get("itinerary")
    if normalize_labels and isinstance(itin, list):
        rows = []
        for raw in itin:
            if not isinstance(raw, dict):
                rows.append(raw)
                continue
            row = dict(raw)
            label = str(row.get("label") or "")
            if len(_label_chain_segments(label)) > 1:
                row["label"] = normalize_chained_day_label(label)
            rows.append(row)
        dest["itinerary"] = rows

    qa = validate_backpacking_itinerary(dest, profile_pills=profile_pills)
    dest["itinerary_qa"] = qa

    warn_lines = [
        f"{x.get('code')}: {x.get('message')}"
        for x in (qa.get("issues") or [])
        if isinstance(x, dict) and x.get("severity") in (_SEVERITY_ERROR, _SEVERITY_WARNING)
    ]
    if warn_lines:
        existing = [str(w) for w in (dest.get("warnings") or []) if isinstance(w, str) and w.strip()]
        merged = list(dict.fromkeys(existing + warn_lines[:6]))
        dest["warnings"] = merged[:12]
    return dest
