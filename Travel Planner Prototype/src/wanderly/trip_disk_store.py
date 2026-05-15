from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_lock = threading.Lock()

_SESSION_KEYS = (
    "ui_destinations",
    "last_destinations",
    "profile_pills",
    "chat",
    "mode",
)


def trips_root() -> Path:
    raw = (os.environ.get("WANDERLY_TRIPS_DIR") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = Path(os.getcwd()) / p
    else:
        root = Path(__file__).resolve().parent.parent.parent
        p = root / ".cache" / "wanderly_trips"
    p.mkdir(parents=True, exist_ok=True)
    return p


def manifest_path() -> Path:
    return trips_root() / "manifest.json"


def trip_file_path(trip_id: str) -> Path:
    safe = "".join(c for c in trip_id if c.isalnum() or c in ("-", "_"))[:80] or "trip"
    return trips_root() / f"{safe}.json"


def _atomic_write_json(path: Path, doc: dict[str, Any]) -> None:
    """
    Write JSON atomically where the OS allows it.

    A sibling ``*.tmp`` in the same folder as ``path`` often fails on Windows under OneDrive
    (``PermissionError`` on ``replace``). We stage in the process temp dir, then ``os.replace``
    into the destination with retries; fall back to ``shutil.move`` and finally in-place write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")
    dest = str(path.resolve())
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            prefix="wanderly_",
            suffix=".json.tmp",
            dir=tempfile.gettempdir(),
        ) as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
            tmp_path = f.name

        last_err: OSError | None = None
        for attempt in range(8):
            if attempt:
                time.sleep(0.04 * attempt)
            try:
                os.replace(tmp_path, dest)
                tmp_path = None
                return
            except (PermissionError, OSError) as e:
                last_err = e
                winerr = getattr(e, "winerror", None)
                errno = getattr(e, "errno", None)
                if isinstance(e, PermissionError) or winerr in (5, 32, 183) or errno in (13, 11, 16, 1224):
                    continue
                raise

        if tmp_path and os.path.isfile(tmp_path):
            try:
                shutil.move(tmp_path, dest)
                tmp_path = None
                return
            except OSError as e:
                last_err = e

        if tmp_path and os.path.isfile(tmp_path):
            try:
                path.write_bytes(Path(tmp_path).read_bytes())
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            tmp_path = None
            return

        if last_err:
            raise last_err
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def lookup_trip_id_for_session(session_id: str) -> str | None:
    """Return ``trip_id`` from the manifest for this ``session_id``, if any."""
    sid = (session_id or "").strip()
    if not sid:
        return None
    manifest = read_manifest()
    for row in manifest.get("trips") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("session_id") or "").strip() == sid:
            tid = str(row.get("trip_id") or "").strip()
            return tid or None
    return None


def read_manifest() -> dict[str, Any]:
    p = manifest_path()
    if not p.is_file():
        return {"v": 1, "active_trip_id": "", "trips": []}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"v": 1, "active_trip_id": "", "trips": []}
    if not isinstance(raw, dict):
        return {"v": 1, "active_trip_id": "", "trips": []}
    trips = raw.get("trips")
    if not isinstance(trips, list):
        trips = []
    return {"v": 1, "active_trip_id": str(raw.get("active_trip_id") or ""), "trips": trips}


def write_manifest(manifest: dict[str, Any]) -> None:
    doc = {"v": 1, "active_trip_id": str(manifest.get("active_trip_id") or ""), "trips": manifest.get("trips") or []}
    with _lock:
        _atomic_write_json(manifest_path(), doc)


def read_trip_file(trip_id: str) -> dict[str, Any] | None:
    p = trip_file_path(trip_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _default_trip_name() -> str:
    return "Trip 1"


def _new_trip_id() -> str:
    return f"trip-{uuid.uuid4().hex[:12]}"


def _session_slice(session: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in _SESSION_KEYS:
        v = session.get(k)
        if k == "chat" and not isinstance(v, list):
            v = []
        if k == "ui_destinations" and not isinstance(v, list):
            v = []
        if k == "last_destinations" and not isinstance(v, list):
            v = []
        if k == "profile_pills" and not isinstance(v, list):
            v = []
        if k == "mode" and v is None:
            v = "idle"
        out[k] = v
    return out


def write_trip_file(
    trip_id: str,
    trip_name: str,
    session_id: str,
    session_slice: dict[str, Any],
) -> None:
    now = int(time.time() * 1000)
    doc = {
        "v": 1,
        "trip_id": trip_id,
        "trip_name": trip_name,
        "session_id": session_id,
        "updated_at_ms": now,
        "session": session_slice,
    }
    with _lock:
        _atomic_write_json(trip_file_path(trip_id), doc)


def persist_trip(trip_id: str, session_id: str, get_session: Any) -> bool:
    """Persist RAM session slice for ``trip_id`` after mutating ``get_session(session_id)``.

    Returns True when the per-trip JSON file was written. If the manifest row is missing but the
    trip file already exists for this ``trip_id`` and its stored ``session_id`` matches the caller
    (same browser tab), the manifest row is recreated so saves are not silently dropped.
    """
    tid = (trip_id or "").strip()
    sid = (session_id or "").strip()
    if not tid or not sid:
        return False
    s = get_session(sid)
    manifest = read_manifest()
    trips = [x for x in (manifest.get("trips") or []) if isinstance(x, dict)]
    name = _default_trip_name()
    found = False
    for row in trips:
        if str(row.get("trip_id") or "").strip() == tid:
            found = True
            name = str(row.get("trip_name") or name)[:120] or name
            row["session_id"] = sid
            row["updated_at_ms"] = int(time.time() * 1000)
            break
    if not found:
        blob = read_trip_file(tid)
        if not isinstance(blob, dict):
            return False
        blob_sid = str(blob.get("session_id") or "").strip()
        if blob_sid and blob_sid != sid:
            return False
        name = str(blob.get("trip_name") or name)[:120] or name
        new_row = {"trip_id": tid, "trip_name": name, "session_id": sid, "updated_at_ms": int(time.time() * 1000)}
        trips = [r for r in trips if str(r.get("trip_id") or "").strip() != tid]
        trips.append(new_row)
        manifest["trips"] = trips
        if not str(manifest.get("active_trip_id") or "").strip():
            manifest["active_trip_id"] = tid
        write_manifest(manifest)
        write_trip_file(tid, name, sid, _session_slice(s))
        return True
    write_manifest({**manifest, "trips": trips})
    write_trip_file(tid, name, sid, _session_slice(s))
    return True


def apply_trip_to_session(trip_blob: dict[str, Any], get_session: Any) -> str:
    """Load ``trip_blob`` snapshot into RAM (full replace for known keys); returns canonical ``session_id``."""
    sid = str(trip_blob.get("session_id") or "").strip()
    if not sid:
        sid = str(uuid.uuid4())
    inner = trip_blob.get("session")
    if not isinstance(inner, dict):
        inner = {}
    s = get_session(sid)
    defaults: dict[str, Any] = {
        "ui_destinations": [],
        "last_destinations": [],
        "profile_pills": [],
        "chat": [],
        "mode": "idle",
    }
    for k in _SESSION_KEYS:
        s[k] = inner[k] if k in inner else defaults[k]
    return sid


def _manifest_index_trips_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in manifest.get("trips") or []:
        if not isinstance(row, dict):
            continue
        tid = str(row.get("trip_id") or "").strip()
        if tid:
            out[tid] = row
    return out


def manifest_contains_trip_id(trip_id: str) -> bool:
    """True if ``trip_id`` appears on a row in ``manifest.json``."""
    tid = (trip_id or "").strip()
    if not tid:
        return False
    return tid in _manifest_index_trips_by_id(read_manifest())


def ensure_manifest_with_trip(
    hint_session_id: str | None,
    get_session: Any,
) -> tuple[str, str, dict[str, Any]]:
    """
    If manifest is empty, create one trip (prefer ``hint_session_id`` for stable browser tab ids).
    Returns ``(trip_id, session_id, manifest)``.
    """
    manifest = read_manifest()
    trips_raw = manifest.get("trips")
    trips: list[dict[str, Any]] = [x for x in trips_raw if isinstance(x, dict)] if isinstance(trips_raw, list) else []
    if trips:
        active = str(manifest.get("active_trip_id") or "").strip() or str(trips[0].get("trip_id") or "")
        if not active:
            active = str(trips[0].get("trip_id") or "")
        manifest["active_trip_id"] = active
        manifest["trips"] = trips
        write_manifest(manifest)
        by_id = _manifest_index_trips_by_id(manifest)
        row = by_id.get(active) or trips[0]
        tid = str(row.get("trip_id") or "").strip()
        sid = str(row.get("session_id") or "").strip()
        return tid, sid, manifest

    tid = _new_trip_id()
    sid = (hint_session_id or "").strip() or str(uuid.uuid4())
    now = int(time.time() * 1000)
    row = {"trip_id": tid, "trip_name": _default_trip_name(), "session_id": sid, "updated_at_ms": now}
    manifest = {"v": 1, "active_trip_id": tid, "trips": [row]}
    write_manifest(manifest)
    get_session(sid)
    write_trip_file(tid, row["trip_name"], sid, _session_slice(get_session(sid)))
    return tid, sid, manifest


def resolve_bootstrap_trip(
    manifest: dict[str, Any],
    hint_trip_id: str | None,
    hint_session_id: str | None,
) -> tuple[str, dict[str, Any] | None]:
    """Pick trip row + optional disk blob. Returns ``(trip_id, trip_file_or_none)``."""
    trips = [x for x in (manifest.get("trips") or []) if isinstance(x, dict)]
    by_id = {str(t.get("trip_id") or ""): t for t in trips if str(t.get("trip_id") or "")}

    if hint_trip_id and hint_trip_id in by_id:
        blob = read_trip_file(hint_trip_id)
        return hint_trip_id, blob

    if hint_session_id:
        for t in trips:
            if str(t.get("session_id") or "").strip() == hint_session_id.strip():
                tid = str(t.get("trip_id") or "").strip()
                if tid:
                    return tid, read_trip_file(tid)
                return str(manifest.get("active_trip_id") or ""), None

    active = str(manifest.get("active_trip_id") or "").strip()
    if active and active in by_id:
        return active, read_trip_file(active)

    if trips:
        tid0 = str(trips[0].get("trip_id") or "").strip()
        return tid0, read_trip_file(tid0) if tid0 else ("", None)

    return "", None


def bootstrap(
    hint_session_id: str | None,
    hint_trip_id: str | None,
    get_session: Any,
) -> dict[str, Any]:
    tid, sid_hint, manifest = ensure_manifest_with_trip(hint_session_id, get_session)
    chosen_id, blob = resolve_bootstrap_trip(manifest, hint_trip_id, hint_session_id)
    if not chosen_id:
        chosen_id = tid

    if blob is None:
        blob = read_trip_file(chosen_id)
    if blob is None:
        row = _manifest_index_trips_by_id(manifest).get(chosen_id) or {}
        sid_use = str(row.get("session_id") or "").strip() or sid_hint or str(uuid.uuid4())
        get_session(sid_use)
        write_trip_file(chosen_id, str(row.get("trip_name") or _default_trip_name()), sid_use, _session_slice(get_session(sid_use)))
        blob = read_trip_file(chosen_id) or {}

    canonical_sid = apply_trip_to_session(blob, get_session)
    manifest["active_trip_id"] = chosen_id
    trips = [x for x in (manifest.get("trips") or []) if isinstance(x, dict)]
    for row in trips:
        if str(row.get("trip_id") or "") == chosen_id:
            row["session_id"] = canonical_sid
            row["updated_at_ms"] = int(time.time() * 1000)
            break
    manifest["trips"] = trips
    write_manifest(manifest)

    trips_out = [
        {
            "trip_id": str(r.get("trip_id") or ""),
            "trip_name": str(r.get("trip_name") or ""),
            "session_id": str(r.get("session_id") or ""),
            "updated_at_ms": int(r["updated_at_ms"]) if isinstance(r.get("updated_at_ms"), (int, float)) else 0,
        }
        for r in trips
        if str(r.get("trip_id") or "")
    ]
    return {
        "trip_id": chosen_id,
        "session_id": canonical_sid,
        "active_trip_id": chosen_id,
        "trips": trips_out,
    }


def activate_trip(trip_id: str, get_session: Any) -> dict[str, Any] | None:
    manifest = read_manifest()
    by_id = _manifest_index_trips_by_id(manifest)
    if trip_id not in by_id:
        return None
    blob = read_trip_file(trip_id)
    if not blob:
        row = by_id[trip_id]
        sid = str(row.get("session_id") or "").strip() or str(uuid.uuid4())
        get_session(sid)
        write_trip_file(
            trip_id,
            str(row.get("trip_name") or _default_trip_name()),
            sid,
            _session_slice(get_session(sid)),
        )
        blob = read_trip_file(trip_id) or {}
    canonical_sid = apply_trip_to_session(blob, get_session)
    manifest["active_trip_id"] = trip_id
    trips = [x for x in (manifest.get("trips") or []) if isinstance(x, dict)]
    for row in trips:
        if str(row.get("trip_id") or "") == trip_id:
            row["session_id"] = canonical_sid
            row["updated_at_ms"] = int(time.time() * 1000)
            break
    manifest["trips"] = trips
    write_manifest(manifest)
    trips_out = [
        {
            "trip_id": str(r.get("trip_id") or ""),
            "trip_name": str(r.get("trip_name") or ""),
            "session_id": str(r.get("session_id") or ""),
            "updated_at_ms": int(r["updated_at_ms"]) if isinstance(r.get("updated_at_ms"), (int, float)) else 0,
        }
        for r in trips
        if str(r.get("trip_id") or "")
    ]
    return {"trip_id": trip_id, "session_id": canonical_sid, "active_trip_id": trip_id, "trips": trips_out}


def create_trip(name: str | None, get_session: Any) -> dict[str, Any]:
    manifest = read_manifest()
    trips = [x for x in (manifest.get("trips") or []) if isinstance(x, dict)]
    tid = _new_trip_id()
    sid = str(uuid.uuid4())
    label = (name or "").strip()[:120] or f"Trip {len(trips) + 1}"
    now = int(time.time() * 1000)
    row = {"trip_id": tid, "trip_name": label, "session_id": sid, "updated_at_ms": now}
    trips.append(row)
    manifest["trips"] = trips
    manifest["active_trip_id"] = tid
    write_manifest(manifest)
    get_session(sid)
    write_trip_file(tid, label, sid, _session_slice(get_session(sid)))
    trips_out = [
        {
            "trip_id": str(r.get("trip_id") or ""),
            "trip_name": str(r.get("trip_name") or ""),
            "session_id": str(r.get("session_id") or ""),
            "updated_at_ms": int(r["updated_at_ms"]) if isinstance(r.get("updated_at_ms"), (int, float)) else 0,
        }
        for r in trips
        if str(r.get("trip_id") or "")
    ]
    return {"trip_id": tid, "session_id": sid, "active_trip_id": tid, "trips": trips_out}


def _trips_summary_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    trips = [x for x in (manifest.get("trips") or []) if isinstance(x, dict)]
    return [
        {
            "trip_id": str(r.get("trip_id") or ""),
            "trip_name": str(r.get("trip_name") or ""),
            "session_id": str(r.get("session_id") or ""),
            "updated_at_ms": int(r["updated_at_ms"]) if isinstance(r.get("updated_at_ms"), (int, float)) else 0,
        }
        for r in trips
        if str(r.get("trip_id") or "")
    ]


def rename_trip(trip_id: str, new_name: str, get_session: Any) -> dict[str, Any] | None:
    """Update ``trip_name`` in manifest and on-disk trip JSON. Returns summary for API layer."""
    tid = (trip_id or "").strip()
    if not tid:
        return None
    label = (new_name or "").strip()[:120] or _default_trip_name()
    manifest = read_manifest()
    trips = [x for x in (manifest.get("trips") or []) if isinstance(x, dict)]
    found = False
    for row in trips:
        if str(row.get("trip_id") or "").strip() == tid:
            row["trip_name"] = label
            row["updated_at_ms"] = int(time.time() * 1000)
            found = True
            break
    if not found:
        return None
    write_manifest({**manifest, "trips": trips})
    blob = read_trip_file(tid)
    if isinstance(blob, dict):
        sid = str(blob.get("session_id") or "").strip() or str(uuid.uuid4())
        inner = blob.get("session")
        if not isinstance(inner, dict):
            inner = _session_slice(get_session(sid))
        write_trip_file(tid, label, sid, inner)
    else:
        row = _manifest_index_trips_by_id(manifest).get(tid) or {}
        sid = str(row.get("session_id") or "").strip()
        if sid:
            get_session(sid)
            write_trip_file(tid, label, sid, _session_slice(get_session(sid)))
    manifest2 = read_manifest()
    return {"trip_id": tid, "trip_name": label, "trips": _trips_summary_rows(manifest2)}


def delete_trip(trip_id: str, get_session: Any) -> dict[str, Any] | None:
    """Remove trip from manifest and delete its JSON file. Activates another trip (or creates one)."""
    tid_del = (trip_id or "").strip()
    if not tid_del:
        return None
    manifest = read_manifest()
    trips = [x for x in (manifest.get("trips") or []) if isinstance(x, dict)]
    new_trips = [t for t in trips if str(t.get("trip_id") or "").strip() != tid_del]
    if len(new_trips) == len(trips):
        return None

    p = trip_file_path(tid_del)
    with _lock:
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass

    active = str(manifest.get("active_trip_id") or "").strip()

    if not new_trips:
        tid = _new_trip_id()
        sid = str(uuid.uuid4())
        now = int(time.time() * 1000)
        row = {"trip_id": tid, "trip_name": _default_trip_name(), "session_id": sid, "updated_at_ms": now}
        fresh: dict[str, Any] = {"v": 1, "active_trip_id": tid, "trips": [row]}
        write_manifest(fresh)
        get_session(sid)
        write_trip_file(tid, row["trip_name"], sid, _session_slice(get_session(sid)))
        return {"trip_id": tid, "session_id": sid, "active_trip_id": tid, "trips": _trips_summary_rows(fresh)}

    manifest["trips"] = new_trips
    if active == tid_del:
        manifest["active_trip_id"] = str(new_trips[0].get("trip_id") or "").strip()
    write_manifest(manifest)
    next_active = str(manifest.get("active_trip_id") or "").strip()
    if not next_active:
        return None
    meta = activate_trip(next_active, get_session)
    return meta


def reset_for_tests() -> None:
    """Remove on-disk trips dir contents (tests only)."""
    root = trips_root()
    if not root.is_dir():
        return
    for p in root.iterdir():
        try:
            p.unlink()
        except OSError:
            pass
