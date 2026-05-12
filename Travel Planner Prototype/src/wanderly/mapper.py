from __future__ import annotations

import re
from typing import Any

from src.wanderly.commerce_stub import attach_commerce_placeholders
from src.wanderly.schemas import SmokeProfilePillDTO, WanderlyWorkspaceDTO


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "dest").lower()).strip("-")
    return (s or "dest")[:48]


def _who_pill_value(gs: Any, gt: str) -> str:
    """Avoid redundant text like ``2 couple with pet`` when headcount matches the group label."""
    gt_s = (gt or "travelers").strip() or "travelers"
    gt_l = gt_s.lower()
    try:
        n = int(gs) if gs is not None else None
    except (TypeError, ValueError):
        n = None
    if n is None:
        return gt_s[:120]
    if "couple" in gt_l and n == 2:
        return gt_s[:120]
    if "solo" in gt_l and n == 1:
        return gt_s[:120]
    if "family" in gt_l and " with pet" in gt_l and n is not None and 2 <= n <= 12:
        return gt_s[:120]
    if " with pet" in gt_l and n is not None and 2 <= n <= 12 and ("adult" in gt_l or "traveler" in gt_l):
        return gt_s[:120]
    return f"{n} {gt_s}"[:120]


def _profile_to_pills(tp: dict[str, Any]) -> list[SmokeProfilePillDTO]:
    """Six pills in the same key order as ``merge_profile_pills_advisor_precedence`` (when, who, from, …)."""
    pills: list[SmokeProfilePillDTO] = []
    gs = tp.get("group_size")
    gt = tp.get("group_type") or "travelers"
    who = _who_pill_value(gs, str(gt))
    assumptions = tp.get("assumptions_used")
    first_asm = assumptions[0] if isinstance(assumptions, list) and assumptions else "Dates TBD"
    when_val = tp.get("traveler_summary") or first_asm
    pills.append(
        SmokeProfilePillDTO(
            key="when",
            label="When",
            value=str(when_val)[:120],
            icon="📅",
        )
    )
    pills.append(SmokeProfilePillDTO(key="who", label="Who", value=who, icon="👥"))
    origin = (tp.get("traveler_origin") or "").strip()
    pills.append(
        SmokeProfilePillDTO(
            key="from",
            label="Flying from",
            value=origin[:120] if origin else "Not specified",
            icon="✈️",
        )
    )
    prefs = tp.get("destination_preferences") or []
    if isinstance(prefs, list) and prefs:
        vibe = ", ".join(str(p) for p in prefs[:5])
        pills.append(SmokeProfilePillDTO(key="vibe", label="Vibe", value=vibe[:120], icon="✨"))
    else:
        pills.append(SmokeProfilePillDTO(key="vibe", label="Vibe", value=str(tp.get("trip_type") or "leisure"), icon="✨"))
    budget = tp.get("budget_max_usd")
    if isinstance(budget, (int, float)):
        pills.append(
            SmokeProfilePillDTO(
                key="budget",
                label="Budget",
                value=f"Up to ~${budget:,.0f}",
                icon="💰",
            )
        )
    else:
        pills.append(SmokeProfilePillDTO(key="budget", label="Budget", value="Flexible", icon="💰"))
    pace = tp.get("pace_preference") or "balanced"
    pills.append(SmokeProfilePillDTO(key="duration", label="Duration", value=str(pace), icon="⏱"))
    return pills


def _option_to_dest_card(opt: dict[str, Any]) -> dict[str, Any]:
    dest_name = opt.get("chosen_destination") or "Destination TBD"
    oid = str(opt.get("option_id") or _slug(dest_name))
    hook = opt.get("trip_highlight") or opt.get("name") or "Option from planner session."
    est = opt.get("estimated_trip_cost_usd")
    cost = f"~${float(est):,.0f} pp est." if isinstance(est, (int, float)) else ""
    hours = opt.get("estimated_travel_time_hours")
    flight = f"{float(hours):.0f}h est." if isinstance(hours, (int, float)) else "—"
    enrich = opt.get("destination_enrichment") or {}
    weather = enrich.get("weather_summary")
    temp = "—"
    if isinstance(weather, str) and weather:
        temp = weather[:80]
    weather_summary = str(weather).strip() if weather else None
    placeholder_img = f"https://placehold.co/800x500/1a1a24/8899aa?text={dest_name.replace(' ', '+')}"
    return {
        "id": oid,
        "name": dest_name.split(",")[0].strip() if dest_name else oid,
        "country": dest_name.split(",")[-1].strip() if "," in dest_name else "",
        "hook": str(hook)[:220],
        "image": placeholder_img,
        "images": [placeholder_img],
        "temp": temp if temp != "—" else "n/a",
        "flight": flight,
        "cost": cost,
        "vibe": (opt.get("destination_candidates") or [])[:6]
        if isinstance(opt.get("destination_candidates"), list)
        else ["Planner option"],
        "score": 88,
        "dismissed": False,
        "pinned": False,
        "warnings": [],
        "itinerary": [],
        "flights": [
            {
                "airline": "Placeholder",
                "route": "Origin → Destination",
                "duration": flight,
                "price": "",
                "best": True,
            }
        ],
        "hotels": [
            {
                "name": "Hotel placeholder",
                "area": "City center",
                "price": "",
                "nights": int(opt.get("recommended_length_days") or 5),
                "image": placeholder_img,
                "best": True,
            }
        ],
        "activities": [],
        "weather_summary": weather_summary,
    }


def map_workspace_to_wanderly(state: dict[str, Any]) -> dict[str, Any]:
    """Map orchestrator GET /state payload into WanderlyWorkspaceDTO dict."""
    tp = state.get("traveler_profile") or {}
    pills = _profile_to_pills(tp if isinstance(tp, dict) else {})
    raw_opts = state.get("itinerary_options") or []
    destinations: list[dict[str, Any]] = []
    enrichment_pending = False
    weather_by_destination_id: dict[str, str] = {}
    for o in raw_opts:
        if not isinstance(o, dict):
            continue
        card = _option_to_dest_card(o)
        destinations.append(card)
        en = o.get("destination_enrichment") or {}
        st = str(en.get("status") or "").lower().strip()
        if st and st not in ("completed", "error", "not_started"):
            enrichment_pending = True
        ws = en.get("weather_summary")
        if ws:
            weather_by_destination_id[str(card["id"])] = str(ws)

    destinations = attach_commerce_placeholders(destinations)

    advisor = state.get("advisor_suggestion")
    if advisor is not None and hasattr(advisor, "model_dump"):
        advisor = advisor.model_dump()
    dto = WanderlyWorkspaceDTO(
        assistant_markdown="Your planner workspace is synced. Open a card for placeholders — live inventory comes in a later version.",
        profile_pills=pills,
        destinations=destinations,
        agent_status_hint=None,
        enrichment_pending=enrichment_pending,
        weather_by_destination_id=weather_by_destination_id,
        advisor_suggestion=advisor if isinstance(advisor, dict) else None,
        breadcrumb=state.get("breadcrumb") or [],
    )
    return dto.model_dump()
