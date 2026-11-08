from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from src.wanderly.itinerary_debug_log import itinerary_file_log_enabled, log_itinerary_build

_lock = threading.RLock()
_sessions: dict[str, dict[str, Any]] = {}

# Lazy JSON mirror of Cloud Agent ids (session_id -> lane -> agent_id); None = not read from disk yet.
_cursor_agent_file_store: dict[str, dict[str, str]] | None = None


def _cursor_agent_ids_persist_enabled() -> bool:
    """When true (default), ``get/set/clear_cursor_agent_id`` sync to ``WANDERLY_CURSOR_AGENT_IDS_PATH``."""
    raw = (os.environ.get("WANDERLY_CURSOR_AGENT_IDS_PERSIST") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _cursor_agent_ids_file() -> Path:
    raw = (os.environ.get("WANDERLY_CURSOR_AGENT_IDS_PATH") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = Path(os.getcwd()) / p
    else:
        root = Path(__file__).resolve().parent.parent.parent
        p = root / ".cache" / "cursor_agent_ids.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _replace_path_with_retries(src: Path, dst: Path) -> None:
    """``os.replace``-style move of ``src`` onto ``dst`` with retries for transient Windows locks.

    OneDrive, antivirus, and editors often hold a brief exclusive lock on the destination JSON;
    ``[WinError 5] Access is denied`` on ``.tmp`` → ``.json`` replace is common under those conditions.
    """
    last_err: OSError | None = None
    max_attempts = int((os.environ.get("WANDERLY_SNAPSHOT_REPLACE_RETRIES") or "18").strip() or "18")
    max_attempts = max(3, min(max_attempts, 60))
    base = float((os.environ.get("WANDERLY_SNAPSHOT_REPLACE_DELAY_S") or "0.04").strip() or "0.04")
    base = max(0.01, min(base, 0.5))
    for attempt in range(max_attempts):
        try:
            src.replace(dst)
            return
        except PermissionError as e:
            last_err = e
        except OSError as e:
            if getattr(e, "winerror", None) == 5 or getattr(e, "errno", None) in (13, 1):
                last_err = e
            else:
                raise
        if attempt < max_attempts - 1:
            time.sleep(base * (attempt + 1))
    # Last resort: non-atomic overwrite (may succeed when rename is blocked but the file is writable).
    try:
        payload = src.read_text(encoding="utf-8")
        dst.write_text(payload, encoding="utf-8")
    except OSError as e2:
        if last_err is not None:
            raise last_err from e2
        raise
    finally:
        try:
            if src.is_file():
                src.unlink()
        except OSError:
            pass


def _write_text_file_atomically(path: Path, text: str) -> None:
    """Write UTF-8 text to ``path`` via a unique temp file + replace (Windows-lock tolerant)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:10]}.tmp"
    tmp.write_text(text, encoding="utf-8")
    try:
        _replace_path_with_retries(tmp, path)
    finally:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass


def _parse_cursor_agent_file_payload(raw: object) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("by_session")
    if not isinstance(inner, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for sid, lanes in inner.items():
        sk = str(sid).strip()
        if not sk or not isinstance(lanes, dict):
            continue
        slot: dict[str, str] = {}
        for lane, aid in lanes.items():
            lk = str(lane).strip()
            av = str(aid).strip()
            if lk and av:
                slot[lk] = av
        if slot:
            out[sk] = slot
    return out


def _read_cursor_agent_file_unlocked() -> dict[str, dict[str, str]]:
    path = _cursor_agent_ids_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _parse_cursor_agent_file_payload(raw)


def _cursor_agent_file_store_ensure_unlocked() -> dict[str, dict[str, str]]:
    global _cursor_agent_file_store
    if not _cursor_agent_ids_persist_enabled():
        return {}
    if _cursor_agent_file_store is None:
        _cursor_agent_file_store = _read_cursor_agent_file_unlocked()
    return _cursor_agent_file_store


def _write_cursor_agent_file_unlocked(store: dict[str, dict[str, str]]) -> None:
    if not _cursor_agent_ids_persist_enabled():
        return
    path = _cursor_agent_ids_file()
    doc = {"v": 1, "by_session": store}
    text = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    _write_text_file_atomically(path, text)


def reset_cursor_agent_file_cache_for_tests() -> None:
    """Drop in-memory file mirror (tests only)."""
    global _cursor_agent_file_store
    _cursor_agent_file_store = None


# --- Wanderly session snapshot (destinations + profile + chat) for server restarts / browser return ---


def _session_snapshot_persist_enabled() -> bool:
    raw = (os.environ.get("WANDERLY_SESSION_SNAPSHOT_PERSIST") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _wanderly_session_snapshots_file() -> Path:
    raw = (os.environ.get("WANDERLY_SESSION_SNAPSHOT_PATH") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = Path(os.getcwd()) / p
    else:
        root = Path(__file__).resolve().parent.parent.parent
        p = root / ".cache" / "wanderly_session_snapshots.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_all_session_snapshots_from_disk() -> dict[str, dict[str, Any]]:
    path = _wanderly_session_snapshots_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    inner = raw.get("by_session")
    if not isinstance(inner, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for sid, blob in inner.items():
        sk = str(sid).strip()
        if sk and isinstance(blob, dict):
            out[sk] = blob
    return out


def _write_all_session_snapshots_to_disk(store: dict[str, dict[str, Any]]) -> None:
    if not _session_snapshot_persist_enabled():
        return
    path = _wanderly_session_snapshots_file()
    doc = {"v": 1, "by_session": store}
    text = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True, default=str) + "\n"
    _write_text_file_atomically(path, text)


def _session_snapshot_entry_from_session_unlocked(s: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    ui = s.get("ui_destinations")
    if isinstance(ui, list):
        entry["ui_destinations"] = ui
    pp = s.get("profile_pills")
    if isinstance(pp, list):
        entry["profile_pills"] = pp
    ch = s.get("chat")
    if isinstance(ch, list):
        entry["chat"] = ch
    ld = s.get("last_destinations")
    if isinstance(ld, list):
        entry["last_destinations"] = ld
    tm = s.get("trip_meta")
    if isinstance(tm, dict) and (str(tm.get("trip_id") or "").strip() or str(tm.get("trip_name") or "").strip()):
        entry["trip_meta"] = {
            "trip_id": str(tm.get("trip_id") or "").strip(),
            "trip_name": str(tm.get("trip_name") or "").strip()[:200],
        }
    return entry


def persist_wanderly_session_snapshot(session_id: str) -> None:
    """Write ui destinations, profile pills, chat, and last destinations for one session to disk."""
    if not _session_snapshot_persist_enabled():
        return
    sid = (session_id or "").strip()
    if not sid:
        return
    with _lock:
        s = _sessions.get(sid)
        if not s:
            return
        disk = _read_all_session_snapshots_from_disk()
        disk[sid] = _session_snapshot_entry_from_session_unlocked(s)
        _write_all_session_snapshots_to_disk(disk)


def delete_wanderly_session_snapshot(session_id: str) -> None:
    sid = (session_id or "").strip()
    if not sid or not _session_snapshot_persist_enabled():
        return
    with _lock:
        disk = _read_all_session_snapshots_from_disk()
        if sid in disk:
            del disk[sid]
            _write_all_session_snapshots_to_disk(disk)


def _itinerary_row_count(row: dict[str, Any]) -> int:
    it = row.get("itinerary")
    return len(it) if isinstance(it, list) else 0


def wanderly_destinations_for_session(s: dict[str, Any]) -> list[dict[str, Any]]:
    """Session-scoped cards for ``GET /api/wanderly/workspace``.

    Prefer ``ui_destinations`` (client mirror). When a UI row has no day-by-day ``itinerary`` but
    ``last_destinations`` has one for the same id (planner kept slim UI rows), backfill so restores
    do not look like an empty plan and the client does not immediately queue lazy rebuilds.
    """
    from src.wanderly.day_logistics import repair_destination_camp_logistics

    ui = s.get("ui_destinations")
    last_raw = s.get("last_destinations")
    ui_list = [dict(x) for x in ui if isinstance(x, dict)] if isinstance(ui, list) else []
    last_list = [x for x in last_raw if isinstance(x, dict)] if isinstance(last_raw, list) else []
    if not ui_list:
        return [repair_destination_camp_logistics(dict(x)) for x in last_list]
    if not last_list:
        return [repair_destination_camp_logistics(d) for d in ui_list]
    last_by_id = {str(x.get("id") or ""): x for x in last_list}
    out: list[dict[str, Any]] = []
    for u in ui_list:
        uid = str(u.get("id") or "")
        alt = last_by_id.get(uid)
        if not alt or not isinstance(alt, dict):
            out.append(u)
            continue
        if _itinerary_row_count(u) > 0:
            out.append(u)
            continue
        if _itinerary_row_count(alt) == 0:
            out.append(u)
            continue
        merged = dict(u)
        merged["itinerary"] = [dict(d) if isinstance(d, dict) else d for d in (alt.get("itinerary") or [])]
        dirty_a = alt.get("itinerary_dirty")
        merged["itinerary_dirty"] = bool(dirty_a) if isinstance(dirty_a, bool) else False
        bg = alt.get("budget_guidance")
        if isinstance(bg, dict) and bg and not merged.get("budget_guidance"):
            merged["budget_guidance"] = dict(bg)
        bsh = alt.get("booking_search_hints")
        if isinstance(bsh, dict) and bsh and not merged.get("booking_search_hints"):
            merged["booking_search_hints"] = dict(bsh)
        out.append(merged)
    return [repair_destination_camp_logistics(d) for d in out]


def patch_session_destination_itinerary(
    session_id: str,
    destination_id: str,
    *,
    itinerary: list[Any],
    budget_guidance: dict[str, Any] | None = None,
    booking_search_hints: dict[str, Any] | None = None,
    needs_commercial_flight: bool | None = None,
    transport_plan: dict[str, Any] | None = None,
    booking_hooks: dict[str, Any] | None = None,
    itinerary_qa: dict[str, Any] | None = None,
    alltrails_search_context: dict[str, Any] | None = None,
) -> None:
    """Merge a completed lazy itinerary into stored session rows and persist (survives tab close)."""
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return
    if not isinstance(itinerary, list) or not itinerary:
        return

    def merge_list(target: Any) -> list[Any] | None:
        if not isinstance(target, list) or not target:
            return None
        out_list: list[Any] = []
        hit = False
        for raw in target:
            if not isinstance(raw, dict):
                out_list.append(raw)
                continue
            row = dict(raw)
            if str(row.get("id") or "").strip() != did:
                out_list.append(row)
                continue
            hit = True
            row["itinerary"] = [dict(x) if isinstance(x, dict) else x for x in itinerary]
            row["itinerary_dirty"] = False
            if isinstance(budget_guidance, dict) and budget_guidance:
                row["budget_guidance"] = budget_guidance
            if isinstance(booking_search_hints, dict) and booking_search_hints:
                row["booking_search_hints"] = booking_search_hints
            if isinstance(needs_commercial_flight, bool):
                row["needs_commercial_flight"] = needs_commercial_flight
            if isinstance(transport_plan, dict) and transport_plan.get("primary_mode"):
                row["transport_plan"] = transport_plan
            if isinstance(booking_hooks, dict) and isinstance(booking_hooks.get("transport"), dict):
                row["booking_hooks"] = booking_hooks
            if isinstance(itinerary_qa, dict):
                row["itinerary_qa"] = itinerary_qa
            if isinstance(alltrails_search_context, dict) and alltrails_search_context.get("trail_name"):
                row["alltrails_search_context"] = alltrails_search_context
            out_list.append(row)
        return out_list if hit else None

    changed = False
    with _lock:
        s = _sessions.setdefault(
            sid,
            {
                "chat": [],
                "last_destinations": [],
                "mode": "idle",
            },
        )
        for key in ("ui_destinations", "last_destinations"):
            cur = s.get(key)
            merged = merge_list(cur)
            if merged is not None:
                s[key] = merged
                changed = True

    if changed:
        dl_in = sum(1 for x in itinerary if isinstance(x, dict) and x.get("day_logistics"))
        if itinerary_file_log_enabled():
            tp0 = transport_plan if isinstance(transport_plan, dict) else None
            log_itinerary_build(
                f"path=wanderly.patch_itinerary sid={sid[:12]!r} dest_id={did!r} rows={len(itinerary)} "
                f"incoming_days_with_day_logistics={dl_in} "
                f"incoming_has_transport_plan={bool(tp0 and str(tp0.get('primary_mode') or '').strip())}"
            )
            from src.wanderly.alltrails_build_trace import log_alltrails_patch_session

            log_alltrails_patch_session(
                session_id=sid,
                dest_id=did,
                alltrails_search_context=alltrails_search_context,
            )
        persist_wanderly_session_snapshot(sid)
        clear_booking_prepare_caches(sid, did)


def _apply_session_snapshot_blob_to_session_unlocked(s: dict[str, Any], blob: dict[str, Any]) -> None:
    ui = blob.get("ui_destinations")
    if isinstance(ui, list):
        s["ui_destinations"] = ui
    pp = blob.get("profile_pills")
    if isinstance(pp, list):
        s["profile_pills"] = [x for x in pp if isinstance(x, dict)]
    ch = blob.get("chat")
    if isinstance(ch, list):
        s["chat"] = [x for x in ch if isinstance(x, dict)]
    ld = blob.get("last_destinations")
    if isinstance(ld, list):
        s["last_destinations"] = [x for x in ld if isinstance(x, dict)]
    tm = blob.get("trip_meta")
    if isinstance(tm, dict):
        tid = str(tm.get("trip_id") or "").strip()
        tname = str(tm.get("trip_name") or "").strip()[:200]
        if tid or tname:
            s["trip_meta"] = {"trip_id": tid, "trip_name": tname}


def hydrate_wanderly_session_from_disk(session_id: str) -> bool:
    """
    Load persisted cards/profile/chat into memory when the in-memory session has no UI cards yet.

    Skips when the session already has ``ui_destinations`` (warm RAM path).
    Returns True when snapshot data was merged from disk into this session.
    """
    if not _session_snapshot_persist_enabled():
        return False
    sid = (session_id or "").strip()
    if not sid:
        return False
    disk = _read_all_session_snapshots_from_disk()
    blob = disk.get(sid)
    if not isinstance(blob, dict) or not blob:
        return False
    with _lock:
        warm = _sessions.get(sid)
        if warm and isinstance(warm.get("ui_destinations"), list) and len(warm["ui_destinations"]) > 0:
            return False
        s = _sessions.setdefault(
            sid,
            {
                "chat": [],
                "last_destinations": [],
                "mode": "idle",
            },
        )
        _apply_session_snapshot_blob_to_session_unlocked(s, blob)
    return True


def clear_all_cursor_agent_ids_for_session(session_id: str) -> None:
    """Remove every stored Cursor Cloud Agent id for ``session_id`` (memory + optional JSON file)."""
    sid = (session_id or "").strip()
    if not sid:
        return
    with _lock:
        s = _sessions.get(sid)
        if s and isinstance(s.get("cursor_agents"), dict):
            s["cursor_agents"].clear()
        if _cursor_agent_ids_persist_enabled():
            store = _cursor_agent_file_store_ensure_unlocked()
            store.pop(sid, None)
            _write_cursor_agent_file_unlocked(store)


def start_new_wanderly_trip_session(old_session_id: str | None) -> tuple[str, dict[str, Any]]:
    """
    Start a fresh Wanderly trip: new ``session_id``, empty workspace payload, drop old snapshot + Cursor ids.

    Returns ``(new_session_id, wanderly_workspace_dict)``.
    """
    old = (old_session_id or "").strip()
    new_sid = str(uuid.uuid4())
    if old:
        with _lock:
            _sessions.pop(old, None)
        delete_wanderly_session_snapshot(old)
        clear_all_cursor_agent_ids_for_session(old)
    s = get_session(new_sid)
    s["profile_pills"] = []
    persist_wanderly_session_snapshot(new_sid)
    empty: dict[str, Any] = {"assistant_markdown": "", "profile_pills": [], "destinations": []}
    return new_sid, empty


def reset_wanderly_session_snapshot_cache_for_tests() -> None:
    """No-op placeholder for tests that monkeypatch disk paths (keeps API surface stable)."""
    return


def ensure_session_owner(session_id: str, owner_sub: str) -> bool:
    """Bind wanderly session to verified viewer ``sub``; refuse cross-viewer access."""
    sid = (session_id or "").strip()
    sub = (owner_sub or "").strip()
    if not sid:
        return True
    if not sub:
        return False

    trip_owner: str | None = None
    try:
        from src.wanderly import trip_disk_store

        trip_owner = trip_disk_store.lookup_owner_for_session(sid)
    except Exception:
        trip_owner = None
    if trip_owner and trip_owner != sub:
        return False

    with _lock:
        bucket = _sessions.get(sid)
        if bucket is None:
            _sessions[sid] = {
                "chat": [],
                "last_destinations": [],
                "mode": "idle",
                "owner_sub": sub,
            }
            return True
        existing = (bucket.get("owner_sub") or "").strip()
        if existing and existing != sub:
            return False
        if not existing:
            bucket["owner_sub"] = sub
        return True


def get_session(session_id: str) -> dict[str, Any]:
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = {
                "chat": [],
                "last_destinations": [],
                "mode": "idle",
            }
        return _sessions[session_id]


def append_chat(session_id: str, role: str, text: str) -> None:
    s = get_session(session_id)
    s["chat"].append({"role": role, "text": text})
    persist_wanderly_session_snapshot(session_id)


def set_last_destinations(session_id: str, destinations: list[dict[str, Any]]) -> None:
    s = get_session(session_id)
    s["last_destinations"] = destinations
    persist_wanderly_session_snapshot(session_id)


def set_last_profile_pills(session_id: str, pills: list[dict[str, Any]]) -> None:
    s = get_session(session_id)
    s["profile_pills"] = [x for x in pills if isinstance(x, dict)]
    persist_wanderly_session_snapshot(session_id)


def set_trip_intent(session_id: str, intent: dict[str, Any] | None) -> None:
    s = get_session(session_id)
    if intent is None:
        s.pop("trip_intent", None)
    else:
        s["trip_intent"] = dict(intent)
    persist_wanderly_session_snapshot(session_id)


def get_trip_intent(session_id: str) -> dict[str, Any] | None:
    s = get_session(session_id)
    raw = s.get("trip_intent")
    return dict(raw) if isinstance(raw, dict) else None


def snapshot_profile_pills(session_id: str) -> list[dict[str, Any]]:
    s = get_session(session_id)
    raw = s.get("profile_pills")
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def chat_context_snippet(session_id: str, max_messages: int = 6, *, max_chars: int = 4500) -> str:
    """
    Recent chat for advisor / refine context. Drops consecutive duplicate lines (model retries),
    then trims to ``max_chars`` from the start when oversized (keeps most recent by trimming oldest).
    """
    s = get_session(session_id)
    lines: list[str] = []
    for m in s["chat"][-max_messages:]:
        role = str(m.get("role") or "?")
        text = str(m.get("text") or "").strip()
        if not text:
            continue
        seg = f"{role}: {text}"
        if lines and lines[-1] == seg:
            continue
        lines.append(seg)
    out = "\n".join(lines)
    if not out:
        return "(no prior turns)"
    cap = max(512, int(max_chars))
    if len(out) <= cap:
        return out
    # Prioritize recent turns: keep suffix within budget (prior_chat most often ends with user ask).
    return out[-cap:]


def set_booking_hotels_plan(session_id: str, destination_id: str, hotels: list[dict[str, Any]]) -> None:
    """Cache multi-stay rows from ``booking_search_prepare`` for subsequent ``booking_search_step`` calls."""
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return
    s = get_session(sid)
    plans = s.setdefault("booking_hotels_plans", {})
    if isinstance(plans, dict):
        plans[did] = [x for x in hotels if isinstance(x, dict)]


def get_booking_hotels_plan(session_id: str, destination_id: str) -> list[dict[str, Any]] | None:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return None
    s = get_session(sid)
    plans = s.get("booking_hotels_plans")
    if not isinstance(plans, dict):
        return None
    raw = plans.get(did)
    return raw if isinstance(raw, list) else None


def set_booking_search_intent(session_id: str, destination_id: str, intent: dict[str, Any]) -> None:
    """Cache LLM-derived booking URL intent params from prepare for step reuse."""
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did or not isinstance(intent, dict):
        return
    s = get_session(sid)
    intents = s.setdefault("booking_search_intents", {})
    if isinstance(intents, dict):
        intents[did] = dict(intent)


def get_booking_search_intent(session_id: str, destination_id: str) -> dict[str, Any] | None:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return None
    s = get_session(sid)
    intents = s.get("booking_search_intents")
    if not isinstance(intents, dict):
        return None
    raw = intents.get(did)
    return raw if isinstance(raw, dict) else None


def bump_booking_prefetch_generation(session_id: str, destination_id: str) -> int:
    """
    Increment per-destination prefetch generation so in-flight background Playwright fetches
    can detect superseded runs and skip writes.
    """
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return 0
    with _lock:
        if sid not in _sessions:
            _sessions[sid] = {
                "chat": [],
                "last_destinations": [],
                "mode": "idle",
            }
        s = _sessions[sid]
        gens = s.setdefault("booking_prefetch_generations", {})
        if not isinstance(gens, dict):
            gens = {}
            s["booking_prefetch_generations"] = gens
        cur = int(gens.get(did, 0)) + 1
        gens[did] = cur
        return cur


def get_booking_prefetch_generation(session_id: str, destination_id: str) -> int:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return 0
    with _lock:
        s = _sessions.get(sid)
        if not s:
            return 0
        gens = s.get("booking_prefetch_generations")
        if not isinstance(gens, dict):
            return 0
        return int(gens.get(did, 0))


def clear_booking_prepare_caches(session_id: str, destination_id: str) -> None:
    """
    Invalidate everything ``booking_search_prepare`` owns for one destination:
    cached hotel rows, search-intent JSON, criteria snapshot for steps, prefetched HTML,
    and the short-lived duplicate-prepare response cache.
    """
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return
    bump_booking_prefetch_generation(sid, did)
    s = get_session(sid)
    bp = s.setdefault("booking_hotels_plans", {})
    if isinstance(bp, dict):
        bp.pop(did, None)
    bi = s.setdefault("booking_search_intents", {})
    if isinstance(bi, dict):
        bi.pop(did, None)
    cs = s.setdefault("booking_criteria_snapshots", {})
    if isinstance(cs, dict):
        cs.pop(did, None)
    hp = s.setdefault("booking_html_prefetch", {})
    if isinstance(hp, dict):
        hp.pop(did, None)
    pc = s.setdefault("booking_prepare_response_cache", {})
    if isinstance(pc, dict):
        pc.pop(did, None)


def get_booking_prepare_response_cache(
    session_id: str, destination_id: str, fingerprint: str
) -> dict[str, Any] | None:
    """Return last ``booking_search_prepare`` JSON when ``fingerprint`` still matches (Strict Mode dedupe)."""
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    fp = (fingerprint or "").strip()
    if not sid or not did or not fp:
        return None
    s = get_session(sid)
    box = s.get("booking_prepare_response_cache")
    if not isinstance(box, dict):
        return None
    cell = box.get(did)
    if not isinstance(cell, dict):
        return None
    if str(cell.get("fingerprint") or "") != fp:
        return None
    raw = cell.get("payload")
    return dict(raw) if isinstance(raw, dict) else None


def set_booking_prepare_response_cache(
    session_id: str, destination_id: str, fingerprint: str, payload: dict[str, Any]
) -> None:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    fp = (fingerprint or "").strip()
    if not sid or not did or not fp or not isinstance(payload, dict):
        return
    s = get_session(sid)
    box = s.setdefault("booking_prepare_response_cache", {})
    if isinstance(box, dict):
        box[did] = {"fingerprint": fp, "payload": dict(payload)}


def set_booking_criteria_snapshot(session_id: str, destination_id: str, payload: dict[str, Any]) -> None:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did or not isinstance(payload, dict):
        return
    s = get_session(sid)
    box = s.setdefault("booking_criteria_snapshots", {})
    if isinstance(box, dict):
        box[did] = dict(payload)


def get_booking_criteria_snapshot(session_id: str, destination_id: str) -> dict[str, Any] | None:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return None
    s = get_session(sid)
    box = s.get("booking_criteria_snapshots")
    if not isinstance(box, dict):
        return None
    raw = box.get(did)
    return raw if isinstance(raw, dict) else None


def clear_booking_html_prefetch(session_id: str, destination_id: str) -> None:
    """Drop only Playwright HTML prefetches (e.g. before a fresh ``build_booking_criteria``)."""
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    if not sid or not did:
        return
    bump_booking_prefetch_generation(sid, did)
    s = get_session(sid)
    hp = s.setdefault("booking_html_prefetch", {})
    if isinstance(hp, dict):
        hp.pop(did, None)


def set_booking_prefetch_html(
    session_id: str,
    destination_id: str,
    cache_key: str,
    html: str,
    *,
    expected_generation: int | None = None,
) -> None:
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    key = (cache_key or "").strip()
    if not sid or not did or not key:
        return
    if expected_generation is not None and get_booking_prefetch_generation(sid, did) != int(expected_generation):
        return
    s = get_session(sid)
    outer = s.setdefault("booking_html_prefetch", {})
    if not isinstance(outer, dict):
        outer = {}
        s["booking_html_prefetch"] = outer
    inner = outer.setdefault(did, {})
    if isinstance(inner, dict):
        inner[key] = html


def pop_booking_prefetch_html(session_id: str, destination_id: str, cache_key: str) -> str | None:
    """Remove and return one prefetched HTML blob, if present."""
    sid = (session_id or "").strip()
    did = (destination_id or "").strip()
    key = (cache_key or "").strip()
    if not sid or not did or not key:
        return None
    s = get_session(sid)
    outer = s.get("booking_html_prefetch")
    if not isinstance(outer, dict):
        return None
    inner = outer.get(did)
    if not isinstance(inner, dict):
        return None
    raw = inner.pop(key, None)
    if not inner:
        outer.pop(did, None)
    if isinstance(raw, str) and raw:
        return raw
    return None


def date_hints_fingerprint(date_hints: dict[str, Any] | None) -> str:
    """Stable string for matching prepare vs step ``date_hints`` payloads."""
    if not date_hints:
        return "{}"
    try:
        return json.dumps(date_hints, sort_keys=True, default=str)
    except TypeError:
        return repr(date_hints)


def snapshot_destinations_for_model(session_id: str) -> list[dict[str, Any]]:
    """Prefer client-synced cards (pin/dismiss) for refine context; else last model list."""
    s = get_session(session_id)
    ui = s.get("ui_destinations")
    if isinstance(ui, list) and len(ui) > 0:
        return [x for x in ui if isinstance(x, dict)]
    last = s.get("last_destinations") or []
    return [x for x in last if isinstance(x, dict)]


def cursor_lane_key(model_id_override: str | None) -> str:
    """Stable key for Cursor Cloud Agent reuse (one agent per lane per Wanderly session)."""
    o = (model_id_override or "").strip()
    return "planner" if not o else f"m:{o}"


def get_cursor_agent_id(session_id: str, lane: str) -> str | None:
    sid = (session_id or "").strip()
    lane_k = (lane or "").strip()
    if not sid or not lane_k:
        return None
    with _lock:
        s = _sessions.get(sid)
        if s:
            agents = s.get("cursor_agents")
            if isinstance(agents, dict):
                raw = agents.get(lane_k)
                if raw is not None:
                    out = str(raw).strip()
                    if out:
                        return out
        if not _cursor_agent_ids_persist_enabled():
            return None
        store = _cursor_agent_file_store_ensure_unlocked()
        inner = store.get(sid)
        if not isinstance(inner, dict):
            return None
        aid = inner.get(lane_k)
        if aid is None:
            return None
        out2 = str(aid).strip()
        if not out2:
            return None
        if s is not None:
            box = s.setdefault("cursor_agents", {})
            if isinstance(box, dict):
                box[lane_k] = out2
        return out2


def set_cursor_agent_id(session_id: str, lane: str, agent_id: str) -> None:
    sid = (session_id or "").strip()
    lane_k = (lane or "").strip()
    aid = (agent_id or "").strip()
    if not sid or not lane_k or not aid:
        return
    with _lock:
        if sid not in _sessions:
            _sessions[sid] = {
                "chat": [],
                "last_destinations": [],
                "mode": "idle",
            }
        s = _sessions[sid]
        if "cursor_agents" not in s or not isinstance(s.get("cursor_agents"), dict):
            s["cursor_agents"] = {}
        s["cursor_agents"][lane_k] = aid
        if _cursor_agent_ids_persist_enabled():
            store = _cursor_agent_file_store_ensure_unlocked()
            slot = store.setdefault(sid, {})
            slot[lane_k] = aid
            _write_cursor_agent_file_unlocked(store)


def clear_cursor_agent_id(session_id: str, lane: str) -> None:
    sid = (session_id or "").strip()
    lane_k = (lane or "").strip()
    if not sid or not lane_k:
        return
    with _lock:
        s = _sessions.get(sid)
        if s:
            agents = s.get("cursor_agents")
            if isinstance(agents, dict) and lane_k in agents:
                del agents[lane_k]
        if _cursor_agent_ids_persist_enabled():
            store = _cursor_agent_file_store_ensure_unlocked()
            inner = store.get(sid)
            if isinstance(inner, dict) and lane_k in inner:
                del inner[lane_k]
                if not inner:
                    del store[sid]
                _write_cursor_agent_file_unlocked(store)


def cursor_run_persist_kwargs(
    session_id: str, model_id_override: str | None, *, trace_path: str | None = None
) -> dict[str, Any]:
    """
    Optional kwargs for ``run_prompt_collect_assistant_text*`` when
    ``cursor_reuse_agents_enabled()`` and session_id are set.

    When ``WANDERLY_CURSOR_AGENT_IDS_PERSIST`` is on (default), agent ids are also stored in
    ``WANDERLY_CURSOR_AGENT_IDS_PATH`` (default ``.cache/cursor_agent_ids.json`` under Travel Planner Prototype)
    so a restarted server can resume the same Cloud Agent for the same ``session_id`` and lane.

    Follow-up runs may send a shorter prompt when ``CURSOR_REUSE_STRIP_STATIC_INSTRUCTIONS`` is on and callers
    pass ``persistent_instruction_prefix`` to ``run_prompt_collect_assistant_text*`` (see ``cursor_agent_client``).

    ``trace_path`` is copied into ``cursor_trace_path`` for ``.cache/itinerary_debug.log`` lines
    (which Wanderly code path hit Cursor / 409).

    ``cursor_run_lock_key`` serializes Cursor HTTP for ``session_id`` + persistence lane (avoids 409 when
    two HTTP handlers start runs concurrently).
    """
    from src.integrations.cursor_agent_client import cursor_reuse_agents_enabled

    sid = (session_id or "").strip()
    lane = cursor_lane_key(model_id_override)
    if not sid or not cursor_reuse_agents_enabled():
        tp0 = (trace_path or "").strip()
        out: dict[str, Any] = {}
        if tp0:
            out["cursor_trace_path"] = tp0
        if sid:
            out["cursor_run_lock_key"] = f"{sid}\t{lane}"
        return out
    existing = get_cursor_agent_id(sid, lane)
    tp = (trace_path or "").strip() or f"wanderly.persist:{lane}"

    def on_new(aid: str, _sid: str = sid, _lane: str = lane) -> None:
        set_cursor_agent_id(_sid, _lane, aid)

    def on_clear(_sid: str = sid, _lane: str = lane) -> None:
        clear_cursor_agent_id(_sid, _lane)

    return {
        "existing_agent_id": existing,
        "on_new_agent": on_new,
        "on_agent_cleared": on_clear,
        "cursor_trace_path": tp,
        "cursor_run_lock_key": f"{sid}\t{lane}",
    }


def cursor_run_persist_kwargs_for_lane(
    session_id: str, lane: str, *, trace_path: str | None = None
) -> dict[str, Any]:
    """
    Like ``cursor_run_persist_kwargs`` but uses an explicit persistence lane key
    (e.g. ``itinerary_lazy``) so callers do not share a Cloud Agent with unrelated flows.

    Shares the same on-disk agent id map as ``cursor_run_persist_kwargs`` when file persist is enabled.

    ``trace_path`` → ``cursor_trace_path`` for log correlation (see ``cursor_run_persist_kwargs``).

    ``cursor_run_lock_key`` — same serialization contract as ``cursor_run_persist_kwargs``.
    """
    from src.integrations.cursor_agent_client import cursor_reuse_agents_enabled

    sid = (session_id or "").strip()
    lane_k = (lane or "").strip() or "default"
    if not sid or not cursor_reuse_agents_enabled():
        tp0 = (trace_path or "").strip()
        out: dict[str, Any] = {}
        if tp0:
            out["cursor_trace_path"] = tp0
        if sid:
            out["cursor_run_lock_key"] = f"{sid}\t{lane_k}"
        return out
    existing = get_cursor_agent_id(sid, lane_k)
    tp = (trace_path or "").strip() or f"wanderly.persist_lane:{lane_k}"

    def on_new(aid: str, _sid: str = sid, _lk: str = lane_k) -> None:
        set_cursor_agent_id(_sid, _lk, aid)

    def on_clear(_sid: str = sid, _lk: str = lane_k) -> None:
        clear_cursor_agent_id(_sid, _lk)

    return {
        "existing_agent_id": existing,
        "on_new_agent": on_new,
        "on_agent_cleared": on_clear,
        "cursor_trace_path": tp,
        "cursor_run_lock_key": f"{sid}\t{lane_k}",
    }
