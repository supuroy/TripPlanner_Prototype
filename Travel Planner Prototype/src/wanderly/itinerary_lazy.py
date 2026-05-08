"""Lazy day-by-day itinerary for a single destination (Cursor JSON → UI ItinDay shape)."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from typing import Any

from src.integrations.cursor_agent_client import (
    basic_client,
    get_api_key,
    load_env_from_roots,
    run_prompt_collect_assistant_text,
    run_prompt_collect_assistant_text_streaming,
)
from src.wanderly.itinerary_debug_log import itinerary_debug_verbose, log_itinerary_build
from src.wanderly.session_store import cursor_run_persist_kwargs_for_lane, get_cursor_agent_id

_ITINERARY_PROMPT = """
You are Wanderly. Build a day-by-day trip outline for ONE destination plus rough planning-level budget guidance (ranges / bands only — not quotes or live fares).

Return ONLY one JSON object (no markdown fences, no prose before or after). Shape:
{
  "itinerary": [
    {"day": <int 1..N>, "label": "Short title → optional area", "items": ["3-5 concrete bullets"], "accent": "emerald|blue|purple|amber"}
  ],
  "budget_guidance": {
    "trip_ballpark_usd_pp": "e.g. ≈ $2.2–3.8k pp all-in (ballpark; say if intl flights excluded)",
    "lodging_style": "e.g. mid-range boutique + one splurge night",
    "flight_ballpark": "e.g. economy return from a major US hub often ~$600–1.1k pp (very rough)",
    "notes": "1–2 sentences on what widens or tightens the band (season, pace, dining); never claim availability.",
    "trip_usd_pp_short": "≤40 chars for small UI cards, e.g. ≈ $3.5–5k pp",
    "flight_band_short": "≤40 chars for small UI cards, e.g. ~$700–1.1k rt pp",
    "lodging_band_short": "≤40 chars for small UI cards, e.g. mid guesthouse ~$90–180/n"
  }
}

Rules:
- itinerary: exactly N days; day 1..N inclusive; last day travel-out / fly home style.
- items: short strings — no exact prices inside day bullets.
- budget_guidance: include all seven keys with non-empty strings. The four long fields are for detail panels; the three *_short fields MUST be concise (hard cap ~40 characters each, no newlines) so they fit destination mini-cards — compress ranges and drop filler words.
- Optional accent on 1-2 highlight days only; omit accent on most days.
- Default pace should be comfortable, not exhausting. Avoid "run around every day" plans unless user explicitly asks for fast-paced / whirlwind / many-city hopping.
- Keep base changes reasonable for trip length:
  - 3-5 days: at most 2 bases
  - 6-8 days: at most 3 bases
  - 9-11 days: at most 4 bases
  - 12-14 days: at most 5 bases
- Prefer 2-3 nights per base before moving, except when explicit user intent says otherwise.
- For mixed-region destinations (e.g., "X + Y"), choose a few anchor bases and do day trips instead of nightly relocations.

Legacy: if you cannot produce the object, a bare JSON array of days is accepted but budget_guidance will be missing.

N (number of days):
""".strip()


def _extract_json_array(text: str) -> list[dict[str, Any]] | None:
    """
    Parse the first JSON array in ``text``.

    Avoid greedy ``[...]`` regex: model output often includes ``]`` inside strings or
    prose after the array, which breaks ``re.search(r'\\[[\\s\\S]*\\]')`` and yields
    ``extracted_len=0`` despite a valid array (see itinerary_debug EMPTY logs).
    """
    t = text.strip()
    if not t:
        return None
    # Strip a single leading ```json ... ``` fence if present
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE).strip()
        if "```" in t:
            t = t[: t.rfind("```")].strip()

    dec = json.JSONDecoder()
    try:
        out, _ = dec.raw_decode(t)
        return out if isinstance(out, list) else None
    except json.JSONDecodeError:
        pass

    for i, ch in enumerate(t):
        if ch != "[":
            continue
        try:
            out, _end = dec.raw_decode(t, i)
            if isinstance(out, list):
                return out
        except json.JSONDecodeError:
            continue
    return None


def _strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE).strip()
        if "```" in t:
            t = t[: t.rfind("```")].strip()
    return t


_BUDGET_GUIDANCE_KEYS = (
    "trip_ballpark_usd_pp",
    "lodging_style",
    "flight_ballpark",
    "notes",
    "trip_usd_pp_short",
    "flight_band_short",
    "lodging_band_short",
)
_BUDGET_GUIDANCE_CAP: dict[str, int] = {
    "trip_usd_pp_short": 44,
    "flight_band_short": 44,
    "lodging_band_short": 44,
}


def _normalize_budget_guidance(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, str] = {}
    for k in _BUDGET_GUIDANCE_KEYS:
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            cap = _BUDGET_GUIDANCE_CAP.get(k, 500)
            out[str(k)] = " ".join(v.strip().split())[:cap]
    return out or None


def _extract_itinerary_payload(raw_text: str) -> tuple[list[Any], dict[str, str] | None]:
    """Prefer wrapped ``{itinerary, budget_guidance}``; fall back to bare JSON array."""
    t = _strip_code_fences(raw_text).lstrip("\ufeff")
    if not t:
        return [], None
    # Skip leading noise (models sometimes print a preamble before the JSON object)
    lead = t.lstrip()
    if lead and not lead.startswith(("{", "[")):
        cut = lead.find("{")
        if cut > 0:
            t = lead[cut:]
    dec = json.JSONDecoder()
    # Parse the first complete JSON value from the buffer. This reliably picks up a
    # top-level ``{"itinerary":[...],"budget_guidance":{...}}`` object (with optional
    # trailing prose) instead of mis-parsing the first ``{`` inside a legacy array.
    try:
        top, _end = dec.raw_decode(t, 0)
        if isinstance(top, dict) and isinstance(top.get("itinerary"), list):
            bg = _normalize_budget_guidance(top.get("budget_guidance"))
            return list(top["itinerary"]), bg
        if isinstance(top, list):
            return top, None
    except json.JSONDecodeError:
        pass
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            obj, _end = dec.raw_decode(t, i)
            if isinstance(obj, dict) and isinstance(obj.get("itinerary"), list):
                bg = _normalize_budget_guidance(obj.get("budget_guidance"))
                return list(obj["itinerary"]), bg
        except json.JSONDecodeError:
            continue
    legacy = _extract_json_array(raw_text) or []
    return legacy, None


def _parse_itinerary_day_number(raw: Any) -> int | None:
    """Accept ``1``, ``1.0``, ``"1"``, ``"Day 3"`` from model JSON."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        try:
            d = int(raw)
        except (TypeError, ValueError):
            return None
        return d if abs(raw - d) < 1e-9 else None
    s = str(raw).strip()
    if not s:
        return None
    m = re.match(r"^day\s*(\d+)\s*$", s, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    try:
        return int(float(s))
    except ValueError:
        m2 = re.search(r"(\d+)", s)
        return int(m2.group(1)) if m2 else None


def _string_to_item_bullets(s: str) -> list[str]:
    """Turn a single prose / semicolon / bullet string into discrete items."""
    t = s.strip()
    if not t:
        return []
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if len(lines) > 1:
        return [ln[:200] for ln in lines[:8]]
    one = lines[0] if lines else t
    if "•" in one:
        parts = [p.strip() for p in one.split("•") if p.strip()]
        return [p[:200] for p in parts[:8]]
    if ";" in one:
        parts = [p.strip() for p in one.split(";") if p.strip()]
        return [p[:200] for p in parts[:8]]
    if " | " in one:
        parts = [p.strip() for p in one.split(" | ") if p.strip()]
        return [p[:200] for p in parts[:8]]
    return [one[:200]]


def _item_cell_to_str(it: Any) -> str | None:
    if isinstance(it, str) and it.strip():
        return it.strip()[:200]
    if isinstance(it, dict):
        s = str(
            it.get("text")
            or it.get("item")
            or it.get("title")
            or it.get("label")
            or it.get("description")
            or it.get("bullet")
            or it.get("summary")
            or it.get("headline")
            or it.get("name")
            or it.get("activity")
            or "",
        ).strip()
        return s[:200] if s else None
    return None


def _normalize_itinerary_rows(raw: list[Any], *, max_days: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    allowed_accents = {"emerald", "blue", "purple", "amber"}
    for row in raw:
        if not isinstance(row, dict):
            continue
        raw_day = row.get("day")
        if raw_day is None:
            raw_day = row.get("day_num") or row.get("dayNumber") or row.get("index")
        day = _parse_itinerary_day_number(raw_day)
        if day is None or day < 1 or day > max_days:
            continue
        label = str(row.get("label") or f"Day {day}")[:120]
        items_in = row.get("items")
        if items_in is None:
            items_in = (
                row.get("bullets")
                or row.get("highlights")
                or row.get("activities")
                or row.get("Activities")
            )
        items: list[str] = []
        if isinstance(items_in, list):
            for it in items_in[:12]:
                cell = _item_cell_to_str(it)
                if cell:
                    items.append(cell)
        elif isinstance(items_in, str) and items_in.strip():
            items.extend(_string_to_item_bullets(items_in)[:8])
        if len(items) < 1 and label.strip():
            items = [label.strip()[:200]]
        if len(items) < 1:
            continue
        if len(items) == 1:
            items.append("Flexible time — explore or rest")
        entry: dict[str, Any] = {"day": day, "label": label, "items": items}
        ac = str(row.get("accent") or "").strip().lower()
        if ac in allowed_accents:
            entry["accent"] = ac
        out.append(entry)
    out.sort(key=lambda x: int(x["day"]))
    # de-dupe by day (keep first)
    seen: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for e in out:
        d = int(e["day"])
        if d in seen:
            continue
        seen.add(d)
        deduped.append(e)
    return deduped[:max_days]


def _max_comfortable_bases(days: int) -> int:
    d = max(1, int(days))
    if d <= 5:
        return 2
    if d <= 8:
        return 3
    if d <= 11:
        return 4
    return 5


def _is_explicit_fast_pace_request(profile_pills: list[dict[str, Any]], destination: dict[str, Any]) -> bool:
    text_bits: list[str] = []
    for p in profile_pills:
        if not isinstance(p, dict):
            continue
        text_bits.append(str(p.get("value") or ""))
    text_bits.append(str(destination.get("hook") or ""))
    blob = " ".join(text_bits).lower()
    return any(
        k in blob
        for k in (
            "fast-paced",
            "fast paced",
            "whirlwind",
            "packed itinerary",
            "pack in",
            "many cities",
            "as many cities",
            "city hopping",
            "multi-city sprint",
            "aggressive pace",
            "every day a new city",
            "road trip every day",
        )
    )


def _label_base_token(label: str) -> str:
    s = " ".join(str(label or "").split())
    if not s:
        return ""
    tail = s.split("→")[-1] if "→" in s else s
    tail = tail.split("->")[-1] if "->" in tail else tail
    # Keep the location side of "title · location" when present.
    if "·" in tail:
        parts = [x.strip() for x in tail.split("·") if x.strip()]
        if parts:
            tail = parts[-1]
    return tail.strip()


def _canon_token(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _replace_label_base(label: str, new_base: str) -> str:
    s = " ".join(str(label or "").split())
    if not s:
        return label
    if "→" in s:
        left = s.split("→", 1)[0].strip()
        return f"{left} → {new_base}".strip()
    if "->" in s:
        left = s.split("->", 1)[0].strip()
        return f"{left} → {new_base}".strip()
    if "·" in s:
        parts = [x.strip() for x in s.split("·")]
        if parts:
            parts[-1] = new_base
            return " · ".join([x for x in parts if x])
    return f"{s} → {new_base}"


def _enforce_comfortable_hops(
    rows: list[dict[str, Any]],
    *,
    max_days: int,
    profile_pills: list[dict[str, Any]],
    destination: dict[str, Any],
) -> list[dict[str, Any]]:
    if not rows:
        return rows
    if _is_explicit_fast_pace_request(profile_pills, destination):
        return rows

    max_bases = _max_comfortable_bases(max_days)
    kept_bases: list[str] = []
    remap: dict[str, str] = {}
    changed = False
    out: list[dict[str, Any]] = []

    for r in rows:
        rr = dict(r)
        label = str(rr.get("label") or "")
        base = _label_base_token(label)
        if not base:
            out.append(rr)
            continue
        ck = _canon_token(base)
        if not ck:
            out.append(rr)
            continue
        if ck in remap:
            mapped = remap[ck]
        else:
            if len(kept_bases) < max_bases:
                kept_bases.append(base)
                remap[ck] = base
                mapped = base
            else:
                # Clamp additional new bases to the latest kept base to avoid daily relocations.
                mapped = kept_bases[-1]
                remap[ck] = mapped
        if _canon_token(mapped) != ck:
            rr["label"] = _replace_label_base(label, mapped)[:120]
            changed = True
        out.append(rr)

    if changed:
        log_itinerary_build(
            f"HOP_GUARD applied days={len(rows)} max_bases={max_bases} kept={kept_bases!r}"
        )
    return out


def _log_itinerary_normalize_all_dropped(rid: str, mode: str, extracted: list[Any]) -> None:
    """When JSON parsed but every day row failed ``_normalize_itinerary_rows`` (helps debug UI EMPTY)."""
    first = extracted[0] if extracted else None
    if not isinstance(first, dict):
        type_counts: dict[str, int] = {}
        for row in extracted[:8]:
            k = type(row).__name__
            type_counts[k] = type_counts.get(k, 0) + 1
        first_preview = repr(first)[:180] if first is not None else ""
        log_itinerary_build(
            f"DROPPED_ALL rid={rid} mode={mode} n_in={len(extracted)} first_type={type(first).__name__!r} "
            f"type_counts={type_counts!r} first_preview={first_preview!r}"
        )
        return
    items_v = first.get("items")
    log_itinerary_build(
        f"DROPPED_ALL rid={rid} mode={mode} n_in={len(extracted)} first_keys={list(first.keys())!r} "
        f"day={first.get('day')!r} items_type={type(items_v).__name__} items_preview={repr(items_v)[:220]}"
    )


def _itinerary_cursor_lane(light: str | None) -> str:
    """
    Persistence lane for lazy itinerary only — must NOT reuse ``planner`` (smoke/refine)
    or follow-up runs see huge JSON/chat context and rarely return a bare JSON array.
    """
    o = (light or "").strip()
    return "itinerary_lazy" if not o else f"itinerary_lazy:m:{o}"


def itinerary_parallel_dest_enabled() -> bool:
    """When true, each destination uses its own Cursor persistence lane (parallel builds; see env README)."""
    return (os.environ.get("WANDERLY_ITINERARY_PARALLEL_DEST") or "").strip().lower() in ("1", "true", "yes", "on")


def _cursor_lane_for_itinerary_build(session_id: str, destination_id: str, light: str | None) -> str:
    """Cursor agent lane for one itinerary build (optionally unique per destination)."""
    base = _itinerary_cursor_lane(light)
    if not itinerary_parallel_dest_enabled():
        return base
    did = re.sub(r"[^a-zA-Z0-9_.-]+", "-", (destination_id or "").strip())[:56]
    return f"{base}:d:{did}" if did else base


def itinerary_build_lock_key(session_id: str, destination_id: str) -> str:
    """Serialize server-side builds for the same Cursor lane (avoids 409 on concurrent POSTs)."""
    light = (os.environ.get("CURSOR_LIGHT_MODEL_FOR_ITINERARY") or os.environ.get("CURSOR_LIGHT_MODEL_FOR_IMAGES") or "").strip() or None
    lane = _cursor_lane_for_itinerary_build(session_id, destination_id, light)
    return f"{(session_id or '').strip()}\t{lane}"


def _itin_reuse_hint(session_id: str | None, light: str | None, destination_id: str = "") -> str:
    sid = (session_id or "").strip()
    if not sid:
        return "reuse=no_session"
    lane = _cursor_lane_for_itinerary_build(sid, destination_id, light)
    aid = get_cursor_agent_id(sid, lane)
    if aid:
        tail = aid[-8:] if len(aid) > 8 else aid
        return f"reuse_lane={lane} existing_agent=…{tail}"
    return f"reuse_lane={lane} existing_agent=(none)"


def _vibe_list_for_prompt(raw: object) -> list[str]:
    """Cap vibe tokens — cards may carry long arrays from enrichment."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw[:8]:
        if isinstance(x, str) and x.strip():
            out.append(x.strip()[:48])
    return out


def _itinerary_prompt_and_days(
    *,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    days: int,
) -> tuple[str, int]:
    n = max(3, min(int(days), 14))
    hook = str(destination.get("hook") or "").strip()
    ctx = json.dumps(
        {
            "destination": {
                "id": destination.get("id"),
                "name": destination.get("name"),
                "country": destination.get("country"),
                "hook": hook[:320],
                "vibe": _vibe_list_for_prompt(destination.get("vibe")),
            },
            "profile_pills": profile_pills[:24],
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    prompt = _ITINERARY_PROMPT + f" {n}\n\nContext JSON:\n{ctx}"
    return prompt, n


def build_itinerary_for_destination(
    *,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    days: int,
    session_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """
    Call Cursor once; return ``{"itinerary": [...], "budget_guidance": {...}|None}`` for the client.
    """
    rid = (request_id or "?").strip() or "?"
    did = str(destination.get("id") or "")
    load_env_from_roots()
    key = get_api_key()
    prompt, n = _itinerary_prompt_and_days(destination=destination, profile_pills=profile_pills, days=days)
    light = (os.environ.get("CURSOR_LIGHT_MODEL_FOR_ITINERARY") or os.environ.get("CURSOR_LIGHT_MODEL_FOR_IMAGES") or "").strip() or None
    log_itinerary_build(
        f"BEGIN rid={rid} mode=json sid={(session_id or '')[:12]!r} dest_id={did!r} days={days} n={n} "
        f"light_model={light!r} {_itin_reuse_hint(session_id, light, did)}"
    )
    t0 = time.perf_counter()
    raw = ""
    _lane = _cursor_lane_for_itinerary_build(session_id or "", did, light)
    _tp = f"itinerary_lazy.json rid={rid} dest={did} lane={_lane}"
    try:
        with basic_client(key) as client:
            raw = run_prompt_collect_assistant_text(
                client,
                prompt,
                model_id_override=light,
                **cursor_run_persist_kwargs_for_lane(session_id or "", _lane, trace_path=_tp),
            )
    except Exception as e:
        log_itinerary_build(
            f"FAIL rid={rid} mode=json dest_id={did!r} exc={type(e).__name__}: {e!s} "
            f"elapsed_ms={(time.perf_counter() - t0) * 1000.0:.1f}"
        )
        raise
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    extracted, budget = _extract_itinerary_payload(raw)
    out = _normalize_itinerary_rows(extracted if isinstance(extracted, list) else [], max_days=n)
    out = _enforce_comfortable_hops(
        out,
        max_days=n,
        profile_pills=profile_pills,
        destination=destination,
    )
    log_itinerary_build(
        f"END rid={rid} mode=json dest_id={did!r} raw_chars={len(raw or '')} extracted_len={len(extracted)} "
        f"normalized_len={len(out)} budget_keys={list(budget or [])} elapsed_ms={elapsed_ms:.1f}"
    )
    if not out:
        tail = (raw or "").replace("\n", " ").strip()[:500]
        log_itinerary_build(f"EMPTY rid={rid} mode=json extracted_len={len(extracted)} raw_snippet={tail!r}")
        if extracted:
            _log_itinerary_normalize_all_dropped(rid, "json", extracted)
    if not out and itinerary_debug_verbose():
        snippet = (raw or "").strip().replace("\r", "")[:2000]
        log_itinerary_build(f"DEBUG_EMPTY rid={rid} raw_head={snippet!r}")
    return {"itinerary": out, "budget_guidance": budget}


def build_itinerary_for_destination_streaming(
    *,
    destination: dict[str, Any],
    profile_pills: list[dict[str, Any]],
    days: int,
    emit: Callable[[dict[str, Any]], None] | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """
    Same as ``build_itinerary_for_destination`` but streams Cursor SSE.
    Forwards thinking/status/tool events to ``emit``; never forwards ``assistant_delta``
    so clients can show chain-of-thought without streaming raw JSON.
    """
    rid = (request_id or "?").strip() or "?"
    did = str(destination.get("id") or "")
    load_env_from_roots()
    key = get_api_key()
    prompt, n = _itinerary_prompt_and_days(destination=destination, profile_pills=profile_pills, days=days)
    light = (os.environ.get("CURSOR_LIGHT_MODEL_FOR_ITINERARY") or os.environ.get("CURSOR_LIGHT_MODEL_FOR_IMAGES") or "").strip() or None

    def _safe_emit(ev: dict[str, Any]) -> None:
        if emit is None:
            return
        if ev.get("type") == "assistant_delta":
            return
        emit(ev)

    log_itinerary_build(
        f"BEGIN rid={rid} mode=sse sid={(session_id or '')[:12]!r} dest_id={did!r} days={days} n={n} "
        f"light_model={light!r} {_itin_reuse_hint(session_id, light, did)}"
    )
    t0 = time.perf_counter()
    raw = ""
    _lane = _cursor_lane_for_itinerary_build(session_id or "", did, light)
    _tp = f"itinerary_lazy.sse rid={rid} dest={did} lane={_lane}"
    try:
        with basic_client(key) as client:
            raw = run_prompt_collect_assistant_text_streaming(
                client,
                prompt,
                emit=_safe_emit if emit else None,
                model_id_override=light,
                **cursor_run_persist_kwargs_for_lane(session_id or "", _lane, trace_path=_tp),
            )
    except Exception as e:
        log_itinerary_build(
            f"FAIL rid={rid} mode=sse dest_id={did!r} exc={type(e).__name__}: {e!s} "
            f"elapsed_ms={(time.perf_counter() - t0) * 1000.0:.1f}"
        )
        raise
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    extracted, budget = _extract_itinerary_payload(raw)
    out = _normalize_itinerary_rows(extracted if isinstance(extracted, list) else [], max_days=n)
    out = _enforce_comfortable_hops(
        out,
        max_days=n,
        profile_pills=profile_pills,
        destination=destination,
    )
    log_itinerary_build(
        f"END rid={rid} mode=sse dest_id={did!r} raw_chars={len(raw or '')} extracted_len={len(extracted)} "
        f"normalized_len={len(out)} budget_keys={list(budget or [])} elapsed_ms={elapsed_ms:.1f}"
    )
    if not out:
        tail = (raw or "").replace("\n", " ").strip()[:500]
        log_itinerary_build(f"EMPTY rid={rid} mode=sse extracted_len={len(extracted)} raw_snippet={tail!r}")
        if extracted:
            _log_itinerary_normalize_all_dropped(rid, "sse", extracted)
    if not out and itinerary_debug_verbose():
        snippet = (raw or "").strip().replace("\r", "")[:2000]
        log_itinerary_build(f"DEBUG_EMPTY rid={rid} raw_head={snippet!r}")
    return {"itinerary": out, "budget_guidance": budget}


def default_trip_days_from_pills(profile_pills: list[dict[str, Any]]) -> int:
    """Rough day count from duration / when pills."""
    for p in profile_pills:
        if not isinstance(p, dict):
            continue
        if str(p.get("key") or "") != "duration":
            continue
        v = str(p.get("value") or "").lower()
        if "2+ week" in v or "14" in v or "10-14" in v:
            return 10
        if "week" in v and "2" in v:
            return 10
        if "7" in v or "week" in v:
            return 7
        if "3-4" in v or "4" in v:
            return 4
        if "5" in v:
            return 5
    return int(os.environ.get("WANDERLY_DEFAULT_ITINERARY_DAYS", "6") or "6")
