from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.wanderly.app_paths import cache_subpath, resolve_under_cache

"""Best-effort import from ``.cache/wanderly_session_snapshots.json`` (older Wanderly persist).

The per-trip disk store (``wanderly_trips/``) does not read that file by default. When the
current RAM session has no destination rows but a legacy snapshot exists, we can copy one
snapshot row into the active session so a reload restores cards without manual JSON surgery.
"""


def snapshots_file_path() -> Path:
    raw = (os.environ.get("WANDERLY_SESSION_SNAPSHOTS_PATH") or "").strip()
    if raw:
        return resolve_under_cache(raw)
    return cache_subpath("wanderly_session_snapshots.json")


def _parse_updated_at(val: Any) -> float:
    if isinstance(val, (int, float)):
        v = float(val)
        return v / 1000.0 if v > 1e12 else v
    s = str(val or "").strip()
    if not s:
        return 0.0
    try:
        iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


def _dest_count(blob: dict[str, Any]) -> int:
    ui = blob.get("ui_destinations")
    ld = blob.get("last_destinations")
    u = len(ui) if isinstance(ui, list) else 0
    l = len(ld) if isinstance(ld, list) else 0
    return max(u, l)


def _read_by_session() -> dict[str, dict[str, Any]]:
    path = snapshots_file_path()
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


def _copy_snapshot_into_session(target: dict[str, Any], snap: dict[str, Any]) -> None:
    if isinstance(snap.get("chat"), list):
        target["chat"] = snap["chat"]
    if isinstance(snap.get("mode"), str) and snap["mode"]:
        target["mode"] = snap["mode"]
    if isinstance(snap.get("profile_pills"), list):
        target["profile_pills"] = snap["profile_pills"]
    ld = snap.get("last_destinations")
    ui = snap.get("ui_destinations")
    if isinstance(ld, list):
        target["last_destinations"] = list(ld)
    if isinstance(ui, list) and ui:
        target["ui_destinations"] = list(ui)
    elif isinstance(ld, list) and ld:
        target["ui_destinations"] = list(ld)
    elif isinstance(ui, list):
        target["ui_destinations"] = list(ui)


def maybe_hydrate_empty_session_from_snapshots(
    get_session: Callable[[str], dict[str, Any]],
    session_id: str,
) -> bool:
    """
    If ``get_session(session_id)`` has no destination cards, try to copy a legacy snapshot.

    Selection order:
    1. Exact ``session_id`` match in ``by_session`` with destination rows.
    2. If exactly one snapshot row has destination rows, use it.
    3. Otherwise use the snapshot row with destination rows and the latest ``updated_at``.

    Returns True if RAM was mutated.
    """
    sid = (session_id or "").strip()
    if not sid:
        return False
    s = get_session(sid)
    ui = s.get("ui_destinations")
    ld = s.get("last_destinations")
    u_n = len(ui) if isinstance(ui, list) else 0
    l_n = len(ld) if isinstance(ld, list) else 0
    if u_n > 0 or l_n > 0:
        return False

    by = _read_by_session()
    if not by:
        return False

    exact = by.get(sid)
    if isinstance(exact, dict) and _dest_count(exact) > 0:
        _copy_snapshot_into_session(s, exact)
        return True

    candidates: list[tuple[float, dict[str, Any]]] = []
    for blob in by.values():
        if not isinstance(blob, dict):
            continue
        n = _dest_count(blob)
        if n == 0:
            continue
        candidates.append((_parse_updated_at(blob.get("updated_at")), blob))

    if not candidates:
        return False

    if len(candidates) == 1:
        _copy_snapshot_into_session(s, candidates[0][1])
        return True

    forced = (os.environ.get("WANDERLY_LEGACY_SNAPSHOT_SESSION_ID") or "").strip()
    if forced and forced in by:
        hit = by[forced]
        if isinstance(hit, dict) and _dest_count(hit) > 0:
            _copy_snapshot_into_session(s, hit)
            return True

    candidates.sort(key=lambda x: x[0], reverse=True)
    _copy_snapshot_into_session(s, candidates[0][1])
    return True
