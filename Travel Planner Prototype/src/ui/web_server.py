from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from src.orchestrator import TravelPlannerOrchestrator
from src.stages.criteria_stage import CriteriaStage
from src.ui.web import HTML_SCAFFOLD
from src.wanderly.mapper import map_workspace_to_wanderly
from src.wanderly.schemas import sanitize_traveler_place_pill_value
from src.wanderly.hero_enrichment import enrich_destinations_hero_images
from src.wanderly.stock_photo_heroes import log_hero_image
from src.wanderly.itinerary_debug_log import log_itinerary_build
from src.wanderly.itinerary_lazy import (
    build_itinerary_for_destination,
    build_itinerary_for_destination_streaming,
    default_trip_days_from_pills,
    itinerary_build_lock_key,
)
from src.wanderly.cursor_advisor import (
    run_advisor_chat_cursor,
    run_plan_review_cursor,
    run_profile_change_advisor_cursor,
)
from src.wanderly.session_store import (
    get_session,
    hydrate_wanderly_session_from_disk,
    patch_session_destination_itinerary,
    persist_wanderly_session_snapshot,
    set_last_destinations,
    set_last_profile_pills,
    snapshot_destinations_for_model,
    snapshot_profile_pills,
    start_new_wanderly_trip_session,
    wanderly_destinations_for_session,
)
from src.wanderly import trip_disk_store
from src.wanderly.legacy_session_snapshots import maybe_hydrate_empty_session_from_snapshots
from src.wanderly.smoke_service import (
    run_refine_turn,
    run_refine_turn_streaming,
    run_smoke_turn,
    run_smoke_turn_streaming,
)
from src.wanderly.user_intent_llm import run_orchestrate_turn


def _itinerary_request_id(handler: BaseHTTPRequestHandler) -> str:
    h = (handler.headers.get("X-Wanderly-Request-Id") or "").strip()
    if h:
        return h[:128]
    return f"itin-{uuid.uuid4().hex[:16]}"


def _trace_itin_web(stage: str, **fields: Any) -> None:
    """Mirror itinerary traces into `.cache/itinerary_debug.log` for endpoint-level debugging."""
    bits: list[str] = [f"WEB {stage}"]
    trace = fields.pop("trace", None)
    if trace:
        bits.append(f"trace={trace!r}")
    for k, v in fields.items():
        try:
            bits.append(f"{k}={v!r}")
        except Exception:
            bits.append(f"{k}=<unrepr>")
    log_itinerary_build(" ".join(bits))


def _wanderly_trace_id(handler: BaseHTTPRequestHandler) -> str:
    for hdr in ("X-Wanderly-Trace-Id", "X-Wanderly-Request-Id"):
        v = (handler.headers.get(hdr) or "").strip()
        if v:
            return v[:128]
    return f"tr-{uuid.uuid4().hex[:20]}"


def _timeline(msg: str) -> None:
    log_itinerary_build(msg)


def normalize_request_path(raw_path: str) -> str:
    """Strip query/fragment-free path, decode %xx, trim, drop trailing slashes (except root)."""
    s = unquote(raw_path or "").strip().rstrip("/")
    return s if s else "/"


def _wanderly_workspace_payload(orchestrator: TravelPlannerOrchestrator, session_id: str) -> dict[str, Any]:
    wanderly = map_workspace_to_wanderly(orchestrator.get_workspace_state())
    sid = (session_id or "").strip()
    if sid:
        s = get_session(sid)
        # Session-scoped cards + traveler pills (same rules as GET /api/wanderly/workspace). Without this,
        # bootstrap/activate would keep ``profile_pills`` from the process-global orchestrator workspace
        # while destinations came from the switched trip — sidebar showed the wrong traveler profile.
        wanderly["profile_pills"] = snapshot_profile_pills(sid)
        wanderly["destinations"] = wanderly_destinations_for_session(s)
        tm = s.get("trip_meta")
        if isinstance(tm, dict):
            tid = str(tm.get("trip_id") or "").strip()
            tname = str(tm.get("trip_name") or "").strip()[:200]
            if tid or tname:
                wanderly["trip_meta"] = {"trip_id": tid, "trip_name": tname}
    return wanderly


def _truthy_flag(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    s = str(val or "").strip().lower()
    return s in ("1", "true", "yes", "on")


def _is_placeholder_profile_pills(pills: list[dict[str, Any]]) -> bool:
    """Client UI template — must not overwrite substantive trip-file pills via sync_ui."""
    if not pills:
        return True
    by_key = {str(p.get("key") or ""): str(p.get("value") or "").strip() for p in pills if isinstance(p, dict)}
    return (
        by_key.get("when", "") in ("", "Not specified")
        and by_key.get("who", "") in ("", "Travelers")
        and by_key.get("from", "") in ("", "Not specified")
        and by_key.get("budget", "") in ("", "Flexible")
        and by_key.get("duration", "") in ("", "Not specified")
    )


_itinerary_build_locks: dict[str, threading.Lock] = {}
_itinerary_build_locks_guard = threading.Lock()


def _itinerary_build_lock(key: str) -> threading.Lock:
    with _itinerary_build_locks_guard:
        if key not in _itinerary_build_locks:
            _itinerary_build_locks[key] = threading.Lock()
        return _itinerary_build_locks[key]


_booking_prepare_locks: dict[str, threading.Lock] = {}
_booking_prepare_locks_guard = threading.Lock()


def _booking_prepare_lock(session_id: str, destination_id: str) -> threading.Lock:
    key = f"prep:{(session_id or '').strip()}\n{(destination_id or '').strip()}"
    with _booking_prepare_locks_guard:
        if key not in _booking_prepare_locks:
            _booking_prepare_locks[key] = threading.Lock()
        return _booking_prepare_locks[key]


def _cors_headers(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    origin = (handler.headers.get("Origin") or "").strip()
    raw = os.environ.get(
        "WANDERLY_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    allowed = {x.strip() for x in raw.split(",") if x.strip()}
    h: dict[str, str] = {}
    if not origin:
        return h
    if origin in allowed or origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:"):
        h["Access-Control-Allow-Origin"] = origin
        h["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        h["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Wanderly-Request-Id, X-Wanderly-Trace-Id"
        h["Access-Control-Max-Age"] = "86400"
    return h


def run_web_server(
    orchestrator_factory: Callable[[], TravelPlannerOrchestrator],
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    orchestrator = orchestrator_factory()

    class Handler(BaseHTTPRequestHandler):
        def _apply_cors(self) -> None:
            for k, v in _cors_headers(self).items():
                self.send_header(k, v)

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            try:
                self.send_response(status)
                self._apply_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def _write_sse(self, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False)
            chunk = f"data: {body}\n\n".encode("utf-8")
            self.wfile.write(chunk)
            self.wfile.flush()

        def _send_html(self, html: str, status: int = 200) -> None:
            body = html.encode("utf-8")
            try:
                self.send_response(status)
                self._apply_cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._apply_cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = normalize_request_path(parsed.path)
            if path == "/":
                self._send_html(HTML_SCAFFOLD)
                return
            if path == "/state":
                self._send_json(orchestrator.get_workspace_state(), status=200)
                return
            if path == "/health":
                self._send_json({"status": "ok"}, status=200)
                return
            if path == "/api/wanderly/workspace":
                qs = parse_qs(parsed.query or "")
                sid_list = qs.get("session_id") or []
                sid = sid_list[0] if sid_list else ""
                snap = False
                wanderly = map_workspace_to_wanderly(orchestrator.get_workspace_state())
                if sid:
                    snap = bool(hydrate_wanderly_session_from_disk(sid))
                    s = get_session(sid)
                    # Always use session-scoped pills (including []) so a new / switched trip does not inherit
                    # orchestrator ``map_workspace_to_wanderly`` profile from another plan.
                    wanderly["profile_pills"] = snapshot_profile_pills(sid)
                    wanderly["destinations"] = wanderly_destinations_for_session(s)
                    tm = s.get("trip_meta")
                    if isinstance(tm, dict):
                        tid = str(tm.get("trip_id") or "").strip()
                        tname = str(tm.get("trip_name") or "").strip()[:200]
                        if tid or tname:
                            wanderly["trip_meta"] = {"trip_id": tid, "trip_name": tname}
                self._send_json({"wanderly": wanderly, "wanderly_snapshot_restored": snap}, status=200)
                return
            self._send_json({"error": f"Not found ({path})"}, status=404)

        def do_POST(self) -> None:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(content_length).decode("utf-8")
                payload = json.loads(raw) if raw else {}

                parsed = urlparse(self.path)
                path = normalize_request_path(parsed.path)
                print(f"[web] POST {path} payload_keys={list(payload.keys()) if isinstance(payload, dict) else 'n/a'}")

                if path == "/api/wanderly/smoke_turn":
                    msg = (payload.get("message") or "").strip()
                    sid = (payload.get("session_id") or "").strip() or str(uuid.uuid4())
                    if not msg:
                        self._send_json({"error": "Missing 'message'."}, status=400)
                        return
                    try:
                        tid = _wanderly_trace_id(self)
                        t0 = time.perf_counter()
                        _timeline(
                            f"TIMELINE smoke_turn_begin trace={tid!r} sid={sid[:12]!r} msg_len={len(msg)}"
                        )
                        out = run_smoke_turn(msg, sid)
                        wly0 = out.get("wanderly")
                        nd = (
                            len(wly0["destinations"])
                            if isinstance(wly0, dict) and isinstance(wly0.get("destinations"), list)
                            else -1
                        )
                        _timeline(
                            f"TIMELINE smoke_turn_end trace={tid!r} sid={sid[:12]!r} "
                            f"elapsed_ms={(time.perf_counter() - t0) * 1000:.1f} dest_n={nd}"
                        )
                        self._send_json({"session_id": sid, **out}, status=200)
                    except ValueError as ve:
                        self._send_json({"error": str(ve)}, status=400)
                    except Exception as exc:
                        print(f"[web] /api/wanderly/smoke_turn error: {exc!r}")
                        traceback.print_exc()
                        self._send_json({"error": str(exc)}, status=500)
                    return

                if path == "/api/wanderly/smoke_turn_stream":
                    msg = (payload.get("message") or "").strip()
                    sid = (payload.get("session_id") or "").strip() or str(uuid.uuid4())
                    if not msg:
                        self._send_json({"error": "Missing 'message'."}, status=400)
                        return
                    try:
                        self.send_response(200)
                        self._apply_cors()
                        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("X-Accel-Buffering", "no")
                        self.end_headers()

                        def emit(obj: dict) -> None:
                            self._write_sse(obj)

                        emit({"type": "phase", "message": "Connected — preparing request…"})
                        tid = _wanderly_trace_id(self)
                        t0 = time.perf_counter()
                        out = None
                        _timeline(
                            f"TIMELINE smoke_stream_begin trace={tid!r} sid={sid[:12]!r} msg_len={len(msg)}"
                        )
                        try:
                            out = run_smoke_turn_streaming(msg, sid, emit=emit)
                            emit(
                                {
                                    "type": "done",
                                    "session_id": sid,
                                    "wanderly": out["wanderly"],
                                    "parse_ok": out.get("parse_ok"),
                                }
                            )
                        finally:
                            nd = -1
                            if isinstance(out, dict):
                                wly = out.get("wanderly")
                                if isinstance(wly, dict) and isinstance(wly.get("destinations"), list):
                                    nd = len(wly["destinations"])
                            _timeline(
                                f"TIMELINE smoke_stream_end trace={tid!r} sid={sid[:12]!r} "
                                f"elapsed_ms={(time.perf_counter() - t0) * 1000:.1f} dest_n={nd}"
                            )
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        return
                    except ValueError as ve:
                        try:
                            self._write_sse({"type": "error", "message": str(ve)})
                        except OSError:
                            return
                    except Exception as exc:
                        print(f"[web] /api/wanderly/smoke_turn_stream error: {exc!r}")
                        traceback.print_exc()
                        try:
                            self._write_sse({"type": "error", "message": str(exc)})
                        except OSError:
                            return
                    return

                if path == "/api/wanderly/refine":
                    msg = (payload.get("message") or "").strip()
                    sid = (payload.get("session_id") or "").strip()
                    confirm_drop = _truthy_flag(payload.get("confirm_workspace_drop"))
                    if not msg or not sid:
                        self._send_json({"error": "Missing 'message' or 'session_id'."}, status=400)
                        return
                    try:
                        tid = _wanderly_trace_id(self)
                        t0 = time.perf_counter()
                        _timeline(
                            f"TIMELINE refine_turn_begin trace={tid!r} sid={sid[:12]!r} msg_len={len(msg)}"
                        )
                        out = run_refine_turn(msg, sid, confirm_workspace_drop=confirm_drop)
                        wly_r = out.get("wanderly")
                        nd_r = (
                            len(wly_r["destinations"])
                            if isinstance(wly_r, dict) and isinstance(wly_r.get("destinations"), list)
                            else -1
                        )
                        _timeline(
                            f"TIMELINE refine_turn_end trace={tid!r} sid={sid[:12]!r} "
                            f"elapsed_ms={(time.perf_counter() - t0) * 1000:.1f} dest_n={nd_r}"
                        )
                        self._send_json({"session_id": sid, **out}, status=200)
                    except ValueError as ve:
                        self._send_json({"error": str(ve)}, status=400)
                    except Exception as exc:
                        print(f"[web] /api/wanderly/refine error: {exc!r}")
                        traceback.print_exc()
                        self._send_json({"error": str(exc)}, status=500)
                    return

                if path == "/api/wanderly/refine_stream":
                    msg = (payload.get("message") or "").strip()
                    sid = (payload.get("session_id") or "").strip()
                    confirm_drop_rs = _truthy_flag(payload.get("confirm_workspace_drop"))
                    if not msg or not sid:
                        self._send_json({"error": "Missing 'message' or 'session_id'."}, status=400)
                        return
                    try:
                        self.send_response(200)
                        self._apply_cors()
                        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("X-Accel-Buffering", "no")
                        self.end_headers()

                        def emit_r(obj: dict) -> None:
                            self._write_sse(obj)

                        emit_r({"type": "phase", "message": "Connected — preparing refine…"})
                        tid_r = _wanderly_trace_id(self)
                        t0_r = time.perf_counter()
                        out_r: dict | None = None
                        _timeline(
                            f"TIMELINE refine_stream_begin trace={tid_r!r} sid={sid[:12]!r} msg_len={len(msg)}"
                        )
                        try:
                            out_r = run_refine_turn_streaming(
                                msg, sid, emit=emit_r, confirm_workspace_drop=confirm_drop_rs
                            )
                            done_payload: dict[str, Any] = {
                                "type": "done",
                                "session_id": sid,
                                "wanderly": out_r["wanderly"],
                                "parse_ok": out_r.get("parse_ok"),
                            }
                            if out_r.get("shortlist_drop_blocked"):
                                done_payload["shortlist_drop_blocked"] = True
                                done_payload["model_omitted_destination_ids"] = (
                                    out_r.get("model_omitted_destination_ids") or []
                                )
                            emit_r(done_payload)
                        finally:
                            nd_rs = -1
                            if isinstance(out_r, dict):
                                wly_rs = out_r.get("wanderly")
                                if isinstance(wly_rs, dict) and isinstance(wly_rs.get("destinations"), list):
                                    nd_rs = len(wly_rs["destinations"])
                            _timeline(
                                f"TIMELINE refine_stream_end trace={tid_r!r} sid={sid[:12]!r} "
                                f"elapsed_ms={(time.perf_counter() - t0_r) * 1000:.1f} dest_n={nd_rs}"
                            )
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        return
                    except ValueError as ve:
                        try:
                            self._write_sse({"type": "error", "message": str(ve)})
                        except OSError:
                            return
                    except Exception as exc:
                        print(f"[web] /api/wanderly/refine_stream error: {exc!r}")
                        traceback.print_exc()
                        try:
                            self._write_sse({"type": "error", "message": str(exc)})
                        except OSError:
                            return
                    return

                if path == "/api/wanderly/orchestrate":
                    msg = (payload.get("message") or "").strip()
                    sid = (payload.get("session_id") or "").strip()
                    focus_oid = (payload.get("focus_destination_id") or "").strip() or None
                    if not msg or not sid:
                        self._send_json({"error": "Missing 'message' or 'session_id'."}, status=400)
                        return
                    try:
                        out_o = run_orchestrate_turn(
                            user_message=msg, session_id=sid, focus_destination_id=focus_oid
                        )
                        self._send_json(out_o, status=200)
                    except Exception as exc:
                        print(f"[web] /api/wanderly/orchestrate error: {exc!r}")
                        traceback.print_exc()
                        self._send_json({"error": str(exc)}, status=500)
                    return

                if path == "/api/wanderly/trips/bootstrap":
                    hint_s = (payload.get("session_id") or "").strip() or None
                    hint_t = (payload.get("trip_id") or "").strip() or None
                    meta = trip_disk_store.bootstrap(hint_s, hint_t, get_session)
                    sid_b = meta["session_id"]
                    tid_b = (meta.get("trip_id") or "").strip()
                    if maybe_hydrate_empty_session_from_snapshots(get_session, sid_b) and tid_b:
                        trip_disk_store.persist_trip(tid_b, sid_b, get_session)
                    wly = _wanderly_workspace_payload(orchestrator, sid_b)
                    self._send_json({**meta, "wanderly": wly}, status=200)
                    return

                if path == "/api/wanderly/trips/activate":
                    tid_a = (payload.get("trip_id") or "").strip()
                    if not tid_a:
                        self._send_json({"error": "Missing 'trip_id'."}, status=400)
                        return
                    meta = trip_disk_store.activate_trip(tid_a, get_session)
                    if not meta:
                        self._send_json({"error": "Unknown trip_id."}, status=404)
                        return
                    sid_act = meta["session_id"]
                    tid_act = (meta.get("trip_id") or "").strip()
                    if maybe_hydrate_empty_session_from_snapshots(get_session, sid_act) and tid_act:
                        trip_disk_store.persist_trip(tid_act, sid_act, get_session)
                    wly = _wanderly_workspace_payload(orchestrator, sid_act)
                    self._send_json({**meta, "wanderly": wly}, status=200)
                    return

                if path == "/api/wanderly/trips/create":
                    raw_name = payload.get("name")
                    nm = str(raw_name).strip()[:120] if raw_name is not None else ""
                    meta = trip_disk_store.create_trip(nm or None, get_session)
                    wly = _wanderly_workspace_payload(orchestrator, meta["session_id"])
                    self._send_json({**meta, "wanderly": wly}, status=200)
                    return

                if path == "/api/wanderly/new_trip":
                    old_sid = (payload.get("session_id") or "").strip() or None
                    try:
                        new_sid, wly = start_new_wanderly_trip_session(old_sid)
                    except Exception as exc:
                        print(f"[web] /api/wanderly/new_trip error: {exc!r}")
                        traceback.print_exc()
                        self._send_json({"error": str(exc)}, status=500)
                        return
                    self._send_json({"session_id": new_sid, "wanderly": wly}, status=200)
                    return

                if path == "/api/wanderly/trips/rename":
                    tid_r = (payload.get("trip_id") or "").strip()
                    raw_rn = payload.get("name")
                    nm_r = str(raw_rn).strip()[:120] if raw_rn is not None else ""
                    if not tid_r:
                        self._send_json({"error": "Missing 'trip_id'."}, status=400)
                        return
                    meta_r = trip_disk_store.rename_trip(tid_r, nm_r, get_session)
                    if not meta_r:
                        self._send_json({"error": "Unknown trip_id."}, status=404)
                        return
                    m2 = trip_disk_store.read_manifest()
                    act = str(m2.get("active_trip_id") or "").strip()
                    sid_rw = ""
                    for row in m2.get("trips") or []:
                        if isinstance(row, dict) and str(row.get("trip_id") or "").strip() == act:
                            sid_rw = str(row.get("session_id") or "").strip()
                            break
                    wly_r = _wanderly_workspace_payload(orchestrator, sid_rw) if sid_rw else _wanderly_workspace_payload(
                        orchestrator, (payload.get("session_id") or "").strip()
                    )
                    self._send_json(
                        {**meta_r, "session_id": sid_rw, "active_trip_id": act, "wanderly": wly_r},
                        status=200,
                    )
                    return

                if path == "/api/wanderly/trips/delete":
                    tid_d = (payload.get("trip_id") or "").strip()
                    if not tid_d:
                        self._send_json({"error": "Missing 'trip_id'."}, status=400)
                        return
                    meta_d = trip_disk_store.delete_trip(tid_d, get_session)
                    if not meta_d:
                        self._send_json({"error": "Unknown trip_id."}, status=404)
                        return
                    sid_del = meta_d["session_id"]
                    tid_act = (meta_d.get("trip_id") or "").strip()
                    if maybe_hydrate_empty_session_from_snapshots(get_session, sid_del) and tid_act:
                        trip_disk_store.persist_trip(tid_act, sid_del, get_session)
                    wly_d = _wanderly_workspace_payload(orchestrator, sid_del)
                    self._send_json({**meta_d, "wanderly": wly_d}, status=200)
                    return

                if path == "/api/wanderly/sync_ui":
                    sid = (payload.get("session_id") or "").strip()
                    dests = payload.get("destinations")
                    if not sid:
                        self._send_json({"error": "Missing 'session_id'."}, status=400)
                        return
                    if not isinstance(dests, list):
                        self._send_json({"error": "Expected list 'destinations'."}, status=400)
                        return
                    s = get_session(sid)
                    tm = payload.get("trip_meta")
                    if isinstance(tm, dict):
                        tid = str(tm.get("trip_id") or "").strip()
                        tname = str(tm.get("trip_name") or "").strip()[:200]
                        if tid or tname:
                            s["trip_meta"] = {"trip_id": tid, "trip_name": tname}
                    pills_in = payload.get("profile_pills")
                    if isinstance(pills_in, list):
                        pills_clean = [x for x in pills_in if isinstance(x, dict)]
                        if pills_clean and not _is_placeholder_profile_pills(pills_clean):
                            set_last_profile_pills(sid, pills_clean)
                    s["ui_destinations"] = dests
                    trip_disk = (payload.get("trip_id") or "").strip()
                    looked = (trip_disk_store.lookup_trip_id_for_session(sid) or "").strip()
                    if trip_disk:
                        if not trip_disk_store.manifest_contains_trip_id(trip_disk) and looked:
                            trip_disk = looked
                    else:
                        trip_disk = looked
                    saved_disk = bool(trip_disk) and trip_disk_store.persist_trip(trip_disk, sid, get_session)
                    persist_wanderly_session_snapshot(sid)
                    self._send_json(
                        {"ok": True, "saved_to_disk": saved_disk, "trip_id": trip_disk or None},
                        status=200,
                    )
                    return

                if path == "/api/wanderly/enrich_heroes":
                    sid = (payload.get("session_id") or "").strip()
                    if not sid:
                        self._send_json({"error": "Missing 'session_id'."}, status=400)
                        return
                    raw_dests = payload.get("destinations")
                    s = get_session(sid)
                    if isinstance(raw_dests, list) and raw_dests:
                        base = [x for x in raw_dests if isinstance(x, dict)]
                    else:
                        base = [x for x in (s.get("last_destinations") or []) if isinstance(x, dict)]
                    if not base:
                        self._send_json({"error": "No destinations to enrich."}, status=400)
                        return
                    try:
                        t0 = time.perf_counter()
                        log_hero_image(
                            f"TIMELINE http enrich_heroes_begin session_id={sid!r} cards={len(base)} "
                            f"ids={[str(x.get('id')) for x in base if isinstance(x, dict)]}"
                        )
                        updated = enrich_destinations_hero_images(base, session_id=sid)
                        log_hero_image(
                            f"TIMELINE http enrich_heroes_end session_id={sid!r} cards={len(updated)} "
                            f"elapsed_ms={(time.perf_counter() - t0) * 1000:.1f}"
                        )
                        set_last_destinations(sid, updated)
                        ui = s.get("ui_destinations")
                        if isinstance(ui, list) and ui:
                            by_id = {str(x.get("id")): x for x in updated if isinstance(x, dict)}
                            merged_ui: list = []
                            for u in ui:
                                if not isinstance(u, dict):
                                    continue
                                mid = str(u.get("id") or "")
                                hit = by_id.get(mid)
                                if hit and hit.get("image"):
                                    nu = {**u, "image": hit["image"], "images": hit.get("images") or [hit["image"]]}
                                    merged_ui.append(nu)
                                else:
                                    merged_ui.append(u)
                            s["ui_destinations"] = merged_ui
                        persist_wanderly_session_snapshot(sid)
                        self._send_json(
                            {
                                "session_id": sid,
                                "wanderly": {
                                    "destinations": updated,
                                    "hero_images_enriched": True,
                                },
                            },
                            status=200,
                        )
                    except Exception as exc:
                        print(f"[web] /api/wanderly/enrich_heroes error: {exc!r}")
                        traceback.print_exc()
                        self._send_json({"error": str(exc)}, status=500)
                    return

                if path == "/api/wanderly/enrich_hero_one":
                    sid = (payload.get("session_id") or "").strip()
                    dest = payload.get("destination")
                    if not sid or not isinstance(dest, dict):
                        self._send_json({"error": "Missing session_id or destination."}, status=400)
                        return
                    try:
                        did = str(dest.get("id") or "")
                        t0 = time.perf_counter()
                        log_hero_image(
                            f"TIMELINE http enrich_hero_one_begin session_id={sid!r} destination_id={did!r}"
                        )
                        updated = enrich_destinations_hero_images([dest], session_id=sid)
                        one = updated[0] if updated else dest
                        log_hero_image(
                            f"TIMELINE http enrich_hero_one_end session_id={sid!r} destination_id={did!r} "
                            f"elapsed_ms={(time.perf_counter() - t0) * 1000:.1f}"
                        )
                        self._send_json({"session_id": sid, "wanderly": {"destinations": [one]}}, status=200)
                    except Exception as exc:
                        print(f"[web] /api/wanderly/enrich_hero_one error: {exc!r}")
                        traceback.print_exc()
                        self._send_json({"error": str(exc)}, status=500)
                    return

                if path == "/api/wanderly/build_itinerary":
                    sid = (payload.get("session_id") or "").strip()
                    if not sid:
                        self._send_json({"error": "Missing 'session_id'."}, status=400)
                        return
                    dest = payload.get("destination")
                    if not isinstance(dest, dict):
                        self._send_json({"error": "Expected 'destination' object."}, status=400)
                        return
                    did = str(dest.get("id") or "").strip()
                    if not did:
                        self._send_json({"error": "Destination must include 'id'."}, status=400)
                        return
                    pills_in = payload.get("profile_pills")
                    pills = [x for x in pills_in if isinstance(x, dict)] if isinstance(pills_in, list) else []
                    try:
                        days = int(payload.get("days") or 0)
                    except (TypeError, ValueError):
                        days = 0
                    if days <= 0:
                        days = default_trip_days_from_pills(pills)
                    rid = _itinerary_request_id(self)
                    tid_b = _wanderly_trace_id(self)
                    print(f"[web] /api/wanderly/build_itinerary rid={rid} sid={sid!r} dest_id={did!r} days={days}", flush=True)
                    _trace_itin_web(
                        "build_itinerary_begin",
                        trace=tid_b,
                        rid=rid,
                        sid=sid[:12],
                        dest_id=did,
                        days=days,
                        pills=len(pills),
                    )
                    try:
                        lk_key = itinerary_build_lock_key(sid, did)
                        with _itinerary_build_lock(lk_key):
                            itin_out = build_itinerary_for_destination(
                                destination=dest,
                                profile_pills=pills,
                                days=days,
                                session_id=sid,
                                request_id=rid,
                            )
                        rows = itin_out.get("itinerary") or []
                        bg = itin_out.get("budget_guidance")
                        bsh = itin_out.get("booking_search_hints")
                        ncf = itin_out.get("needs_commercial_flight")
                        itp = itin_out.get("transport_plan")
                        ibh = itin_out.get("booking_hooks")
                        print(
                            f"[web] /api/wanderly/build_itinerary ok rid={rid} dest_id={did!r} rows={len(rows)}",
                            flush=True,
                        )
                        _trace_itin_web(
                            "build_itinerary_ok",
                            trace=tid_b,
                            rid=rid,
                            sid=sid[:12],
                            dest_id=did,
                            rows=len(rows),
                            budget=bool(bg),
                            booking_hints=bool(bsh),
                            needs_commercial_flight=ncf if isinstance(ncf, bool) else None,
                        )
                        resp_body: dict[str, Any] = {
                            "session_id": sid,
                            "destination_id": did,
                            "itinerary": rows,
                            "budget_guidance": bg,
                        }
                        if isinstance(bsh, dict) and bsh:
                            resp_body["booking_search_hints"] = bsh
                        if isinstance(ncf, bool):
                            resp_body["needs_commercial_flight"] = ncf
                        if isinstance(itp, dict) and str(itp.get("primary_mode") or "").strip():
                            resp_body["transport_plan"] = itp
                        if isinstance(ibh, dict) and isinstance(ibh.get("transport"), dict):
                            resp_body["booking_hooks"] = ibh
                        if rows:
                            patch_session_destination_itinerary(
                                sid,
                                did,
                                itinerary=rows,
                                budget_guidance=bg if isinstance(bg, dict) else None,
                                booking_search_hints=bsh if isinstance(bsh, dict) else None,
                                needs_commercial_flight=ncf if isinstance(ncf, bool) else None,
                                transport_plan=itp if isinstance(itp, dict) and str(itp.get("primary_mode") or "").strip() else None,
                                booking_hooks=ibh if isinstance(ibh, dict) and isinstance(ibh.get("transport"), dict) else None,
                            )
                        self._send_json(resp_body, status=200)
                    except Exception as exc:
                        print(f"[web] /api/wanderly/build_itinerary error: {exc!r}")
                        traceback.print_exc()
                        _trace_itin_web(
                            "build_itinerary_error",
                            trace=tid_b,
                            rid=rid,
                            sid=sid[:12],
                            dest_id=did,
                            exc_type=type(exc).__name__,
                            message=str(exc)[:260],
                        )
                        self._send_json({"error": str(exc)}, status=500)
                    return

                if path == "/api/wanderly/build_itinerary_stream":
                    sid = (payload.get("session_id") or "").strip()
                    if not sid:
                        self._send_json({"error": "Missing 'session_id'."}, status=400)
                        return
                    dest = payload.get("destination")
                    if not isinstance(dest, dict):
                        self._send_json({"error": "Expected 'destination' object."}, status=400)
                        return
                    did = str(dest.get("id") or "").strip()
                    if not did:
                        self._send_json({"error": "Destination must include 'id'."}, status=400)
                        return
                    pills_in = payload.get("profile_pills")
                    pills = [x for x in pills_in if isinstance(x, dict)] if isinstance(pills_in, list) else []
                    try:
                        days = int(payload.get("days") or 0)
                    except (TypeError, ValueError):
                        days = 0
                    if days <= 0:
                        days = default_trip_days_from_pills(pills)
                    rid = _itinerary_request_id(self)
                    tid_bs = _wanderly_trace_id(self)
                    print(
                        f"[web] /api/wanderly/build_itinerary_stream rid={rid} sid={sid!r} dest_id={did!r} days={days}",
                        flush=True,
                    )
                    _trace_itin_web(
                        "build_itinerary_stream_begin",
                        trace=tid_bs,
                        rid=rid,
                        sid=sid[:12],
                        dest_id=did,
                        days=days,
                        pills=len(pills),
                    )
                    try:
                        self.send_response(200)
                        self._apply_cors()
                        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("X-Accel-Buffering", "no")
                        self.end_headers()

                        def emit_itin(obj: dict[str, Any]) -> None:
                            self._write_sse(obj)

                        emit_itin({"type": "phase", "message": "Building your day-by-day outline…"})
                        lk_key_s = itinerary_build_lock_key(sid, did)
                        with _itinerary_build_lock(lk_key_s):
                            itin_out = build_itinerary_for_destination_streaming(
                                destination=dest,
                                profile_pills=pills,
                                days=days,
                                emit=emit_itin,
                                session_id=sid,
                                request_id=rid,
                            )
                        rows = itin_out.get("itinerary") or []
                        bg = itin_out.get("budget_guidance")
                        bsh = itin_out.get("booking_search_hints")
                        ncf = itin_out.get("needs_commercial_flight")
                        itp = itin_out.get("transport_plan")
                        ibh = itin_out.get("booking_hooks")
                        print(
                            f"[web] /api/wanderly/build_itinerary_stream ok rid={rid} dest_id={did!r} rows={len(rows)}",
                            flush=True,
                        )
                        _trace_itin_web(
                            "build_itinerary_stream_ok",
                            trace=tid_bs,
                            rid=rid,
                            sid=sid[:12],
                            dest_id=did,
                            rows=len(rows),
                            budget=bool(bg),
                            booking_hints=bool(bsh),
                            needs_commercial_flight=ncf if isinstance(ncf, bool) else None,
                        )
                        done_payload: dict[str, Any] = {
                            "type": "done",
                            "session_id": sid,
                            "destination_id": did,
                            "itinerary": rows,
                            "budget_guidance": bg,
                        }
                        if isinstance(bsh, dict) and bsh:
                            done_payload["booking_search_hints"] = bsh
                        if isinstance(ncf, bool):
                            done_payload["needs_commercial_flight"] = ncf
                        if isinstance(itp, dict) and str(itp.get("primary_mode") or "").strip():
                            done_payload["transport_plan"] = itp
                        if isinstance(ibh, dict) and isinstance(ibh.get("transport"), dict):
                            done_payload["booking_hooks"] = ibh
                        emit_itin(done_payload)
                        if rows:
                            patch_session_destination_itinerary(
                                sid,
                                did,
                                itinerary=rows,
                                budget_guidance=bg if isinstance(bg, dict) else None,
                                booking_search_hints=bsh if isinstance(bsh, dict) else None,
                                needs_commercial_flight=ncf if isinstance(ncf, bool) else None,
                                transport_plan=itp if isinstance(itp, dict) and str(itp.get("primary_mode") or "").strip() else None,
                                booking_hooks=ibh if isinstance(ibh, dict) and isinstance(ibh.get("transport"), dict) else None,
                            )
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        _trace_itin_web(
                            "build_itinerary_stream_client_disconnect",
                            trace=tid_bs,
                            rid=rid,
                            sid=sid[:12],
                            dest_id=did,
                        )
                        return
                    except ValueError as ve:
                        _trace_itin_web(
                            "build_itinerary_stream_value_error",
                            trace=tid_bs,
                            rid=rid,
                            sid=sid[:12],
                            dest_id=did,
                            message=str(ve)[:260],
                        )
                        try:
                            self._write_sse({"type": "error", "message": str(ve)})
                        except OSError:
                            return
                    except Exception as exc:
                        print(f"[web] /api/wanderly/build_itinerary_stream error: {exc!r}")
                        traceback.print_exc()
                        _trace_itin_web(
                            "build_itinerary_stream_error",
                            trace=tid_bs,
                            rid=rid,
                            sid=sid[:12],
                            dest_id=did,
                            exc_type=type(exc).__name__,
                            message=str(exc)[:260],
                        )
                        try:
                            self._write_sse({"type": "error", "message": str(exc)})
                        except OSError:
                            return
                    return

                if path == "/api/wanderly/plan":
                    user_input = (payload.get("input") or "").strip()
                    sid = (payload.get("session_id") or "").strip() or str(uuid.uuid4())
                    if not user_input:
                        self._send_json({"error": "Missing 'input' text."}, status=400)
                        return
                    result = orchestrator.run_initial_flow(user_input)
                    wanderly = map_workspace_to_wanderly(orchestrator.get_workspace_state())
                    s = get_session(sid)
                    s["mode"] = "gemini_plan"
                    dests = wanderly.get("destinations") or []
                    s["last_destinations"] = dests
                    s["ui_destinations"] = dests
                    self._send_json({"session_id": sid, "run": result, "wanderly": wanderly}, status=200)
                    return

                if path == "/api/wanderly/profile_patch":
                    pills = payload.get("pills")
                    if not isinstance(pills, list):
                        self._send_json({"error": "Expected 'pills' array."}, status=400)
                        return
                    sid_pp = (payload.get("session_id") or "").strip()
                    if sid_pp:
                        set_last_profile_pills(sid_pp, [x for x in pills if isinstance(x, dict)])
                    patch: dict = {}
                    for p in pills:
                        if not isinstance(p, dict):
                            continue
                        k = str(p.get("key") or "")
                        v = str(p.get("value") or "")
                        if k == "budget":
                            parsed_b = CriteriaStage._extract_budget_max_usd(v)
                            if parsed_b is not None:
                                patch["budget_max_usd"] = parsed_b
                            elif v.strip().lower() in ("flexible", "not specified", "tbd", "unknown", "n/a"):
                                patch["budget_max_usd"] = None
                        elif k == "from":
                            vv = v.strip()
                            if vv and vv.lower() not in ("not specified", "tbd", "unknown", "n/a"):
                                patch["traveler_origin"] = sanitize_traveler_place_pill_value("from", vv)[:200]
                        elif k == "who":
                            m = re.match(r"^\s*(\d+)", v)
                            if m:
                                patch["group_size"] = int(m.group(1))
                            patch["editable_notes"] = v[:500]
                        elif k == "vibe":
                            patch["destination_preferences"] = [x.strip() for x in v.split(",") if x.strip()][:12]
                        elif k == "when":
                            patch["traveler_summary"] = v[:2000]
                        elif k == "duration":
                            vv = v.strip()
                            if re.search(r"\b\d{1,2}\s*(?:day|days|week|weeks|night|nights)\b", vv.lower()):
                                prev = (
                                    orchestrator.session.get_traveler_profile().editable_notes or ""
                                ).strip()
                                tid = f"Trip length: {vv[:100]}"
                                patch["editable_notes"] = (
                                    f"{prev}; {tid}".strip("; ") if prev else tid
                                )[:2000]
                            else:
                                patch["pace_preference"] = vv[:120]
                    updated = orchestrator.update_traveler_profile(patch, source="wanderly_profile_patch")
                    wanderly = map_workspace_to_wanderly(orchestrator.get_workspace_state())
                    if sid_pp:
                        tid_pp = (trip_disk_store.lookup_trip_id_for_session(sid_pp) or "").strip()
                        if tid_pp:
                            trip_disk_store.persist_trip(tid_pp, sid_pp, get_session)
                        persist_wanderly_session_snapshot(sid_pp)
                    self._send_json({"traveler_profile": updated, "wanderly": wanderly}, status=200)
                    return

                if path == "/api/wanderly/advisor_feed":
                    from src.wanderly.web_search_snippets import fetch_web_search_snippets

                    msg = (payload.get("message") or "").strip()
                    sid = (payload.get("session_id") or "").strip()
                    focus_dest = (payload.get("focus_destination_id") or "").strip()
                    run_web_search = bool(payload.get("run_web_search"))
                    sq_in = str(payload.get("search_query") or "").strip()[:500]
                    if run_web_search and not msg and sq_in:
                        msg = sq_in
                    log_itinerary_build(
                        f"BEGIN path=http.advisor_feed sid={sid[:12]!r} focus={focus_dest[:24]!r} "
                        f"msg_chars={len(msg)} run_web_search={int(run_web_search)}"
                    )
                    try:
                        pills: list = []
                        dests: list = []
                        web_used = False
                        web_sources: list[dict[str, str]] = []
                        if sid:
                            pills = snapshot_profile_pills(sid)
                            dests = snapshot_destinations_for_model(sid)
                        if run_web_search and sq_in:
                            snips = fetch_web_search_snippets(sq_in)
                            web_sources = [{"title": x.get("title", ""), "url": x.get("url", "")} for x in snips]
                            if snips:
                                web_used = True
                                chat_out = run_advisor_chat_cursor(
                                    user_message=msg,
                                    profile_pills=pills,
                                    destinations=dests,
                                    session_id=sid or None,
                                    focus_destination_id=focus_dest or None,
                                    web_snippets=snips,
                                )
                            else:
                                chat_out = {
                                    "assistant_markdown": (
                                        "Web search returned no snippets. Set **TAVILY_API_KEY** or **BRAVE_SEARCH_API_KEY** "
                                        "in the Travel Planner environment and restart the server — or use **Open in browser** below."
                                    ),
                                    "refine_prompt": None,
                                    "offer_web_search": False,
                                    "suggested_search_query": None,
                                }
                        else:
                            chat_out = run_advisor_chat_cursor(
                                user_message=msg,
                                profile_pills=pills,
                                destinations=dests,
                                session_id=sid or None,
                                focus_destination_id=focus_dest or None,
                            )
                        md = str(chat_out.get("assistant_markdown") or "").strip()
                        ref_raw = chat_out.get("refine_prompt")
                        refine_prompt = (
                            str(ref_raw).strip()
                            if isinstance(ref_raw, str) and str(ref_raw).strip().lower() not in ("null", "none")
                            else None
                        )
                        offer_search = bool(chat_out.get("offer_web_search"))
                        sq_raw = chat_out.get("suggested_search_query")
                        suggested_search_query = (
                            str(sq_raw).strip()[:500]
                            if isinstance(sq_raw, str) and str(sq_raw).strip().lower() not in ("null", "none")
                            else None
                        )
                        if offer_search and not suggested_search_query:
                            offer_search = False
                        if run_web_search:
                            offer_search = False
                            suggested_search_query = None
                        sug = {
                            "assistant_message": md,
                            "refine_prompt": refine_prompt,
                            "offer_web_search": offer_search,
                            "suggested_search_query": suggested_search_query,
                            "primary_recommendation": {"action": "none", "why": "Cursor advisor (cheap model) reply."},
                            "model": "cursor_advisor",
                        }
                        wanderly = map_workspace_to_wanderly(orchestrator.get_workspace_state())
                        if sid:
                            s = get_session(sid)
                            wanderly["destinations"] = wanderly_destinations_for_session(s)
                        self._send_json(
                            {
                                "advisor_suggestion": sug,
                                "feed_markdown": md or "Advisor updated.",
                                "refine_prompt": refine_prompt,
                                "offer_web_search": offer_search,
                                "suggested_search_query": suggested_search_query,
                                "web_search_used": web_used if run_web_search else False,
                                "web_sources": web_sources if run_web_search else [],
                                "wanderly": wanderly,
                            },
                            status=200,
                        )
                    except Exception as exc:
                        print(f"[web] /api/wanderly/advisor_feed error: {exc!r}")
                        traceback.print_exc()
                        self._send_json({"error": str(exc)}, status=500)
                    return

                if path == "/api/wanderly/plan_review":
                    sid = (payload.get("session_id") or "").strip()
                    if not sid:
                        self._send_json({"error": "Missing 'session_id'."}, status=400)
                        return
                    profile_in = payload.get("profile_pills")
                    dests_in = payload.get("destinations")
                    pills = (
                        [x for x in profile_in if isinstance(x, dict)]
                        if isinstance(profile_in, list)
                        else snapshot_profile_pills(sid)
                    )
                    dests = (
                        [x for x in dests_in if isinstance(x, dict)]
                        if isinstance(dests_in, list)
                        else snapshot_destinations_for_model(sid)
                    )
                    try:
                        tid_pr = _wanderly_trace_id(self)
                        t0_pr = time.perf_counter()
                        _timeline(
                            f"TIMELINE plan_review_begin trace={tid_pr!r} sid={sid[:12]!r} "
                            f"pills={len(pills)} dests={len(dests)}"
                        )
                        review = run_plan_review_cursor(
                            profile_pills=pills,
                            destinations=dests,
                            session_id=sid,
                        )
                        _timeline(
                            f"TIMELINE plan_review_end trace={tid_pr!r} sid={sid[:12]!r} "
                            f"elapsed_ms={(time.perf_counter() - t0_pr) * 1000:.1f} "
                            f"skipped={bool(isinstance(review, dict) and review.get('skipped'))}"
                        )
                        self._send_json({"session_id": sid, "review": review}, status=200)
                    except Exception as exc:
                        print(f"[web] /api/wanderly/plan_review error: {exc!r}")
                        traceback.print_exc()
                        self._send_json({"error": str(exc)}, status=500)
                    return

                if path == "/api/wanderly/profile_change_advisor":
                    sid = (payload.get("session_id") or "").strip()
                    pill_key = str(payload.get("pill_key") or "").strip()
                    if not sid or not pill_key:
                        self._send_json({"error": "Missing 'session_id' or 'pill_key'."}, status=400)
                        return
                    old_value = str(payload.get("old_value") or "")
                    new_value = str(payload.get("new_value") or "")
                    profile_in = payload.get("profile_pills")
                    dests_in = payload.get("destinations")
                    pills = (
                        [x for x in profile_in if isinstance(x, dict)]
                        if isinstance(profile_in, list)
                        else snapshot_profile_pills(sid)
                    )
                    dests = (
                        [x for x in dests_in if isinstance(x, dict)]
                        if isinstance(dests_in, list)
                        else snapshot_destinations_for_model(sid)
                    )
                    try:
                        result = run_profile_change_advisor_cursor(
                            pill_key=pill_key,
                            old_value=old_value,
                            new_value=new_value,
                            profile_pills=pills,
                            destinations=dests,
                            session_id=sid,
                        )
                        self._send_json({"session_id": sid, "result": result}, status=200)
                    except Exception as exc:
                        print(f"[web] /api/wanderly/profile_change_advisor error: {exc!r}")
                        traceback.print_exc()
                        self._send_json({"error": str(exc)}, status=500)
                    return

                if path == "/api/wanderly/booking_search_prepare":
                    import copy as _copy_mod
                    import time as _time_mod

                    from src.wanderly.ota_booking_search import (
                        booking_prepare_request_fingerprint,
                        booking_search_trace,
                        build_booking_criteria,
                    )
                    from src.wanderly.session_store import (
                        clear_booking_prepare_caches,
                        get_booking_prepare_response_cache,
                        set_booking_prepare_response_cache,
                    )

                    sid_bs = (payload.get("session_id") or "").strip()
                    did_bs = (payload.get("destination_id") or "").strip()
                    if not sid_bs or not did_bs:
                        self._send_json({"error": "Missing session_id or destination_id."}, status=400)
                        return
                    dh_bs = payload.get("date_hints") if isinstance(payload.get("date_hints"), dict) else None
                    dests_bs = snapshot_destinations_for_model(sid_bs)
                    dest_bs = next(
                        (x for x in dests_bs if isinstance(x, dict) and str(x.get("id") or "") == did_bs),
                        None,
                    )
                    if not dest_bs:
                        self._send_json({"error": "Destination not found in session."}, status=404)
                        return
                    pills_raw_bs = payload.get("profile_pills")
                    if isinstance(pills_raw_bs, list) and any(isinstance(x, dict) for x in pills_raw_bs):
                        pills_bs = [x for x in pills_raw_bs if isinstance(x, dict)]
                    else:
                        pills_bs = snapshot_profile_pills(sid_bs)
                    skip_raw = payload.get("skip_parallel_prefetch")
                    skip_parallel_prefetch = skip_raw is True or (
                        isinstance(skip_raw, str) and skip_raw.strip().lower() in ("1", "true", "yes")
                    )
                    try:
                        fp_bs = booking_prepare_request_fingerprint(
                            dest_bs, pills_bs, dh_bs, skip_parallel_prefetch=skip_parallel_prefetch
                        )
                        with _booking_prepare_lock(sid_bs, did_bs):
                            cached_bs = get_booking_prepare_response_cache(sid_bs, did_bs, fp_bs)
                            if cached_bs is not None:
                                booking_search_trace(
                                    "booking_prepare_served_from_cache",
                                    destination_id=did_bs,
                                    session_id_prefix=sid_bs[:12] if len(sid_bs) >= 12 else sid_bs,
                                    fp_sha8=fp_bs[:8],
                                )
                                self._send_json(cached_bs, status=200)
                                return
                            booking_search_trace(
                                "booking_prepare_begin",
                                destination_id=did_bs,
                                session_id_prefix=sid_bs[:12] if len(sid_bs) >= 12 else sid_bs,
                            )
                            t_prep = _time_mod.perf_counter()
                            clear_booking_prepare_caches(sid_bs, did_bs)
                            out_bs = build_booking_criteria(
                                dest_bs,
                                pills_bs,
                                date_hints=dh_bs,
                                session_id=sid_bs,
                                skip_parallel_prefetch=skip_parallel_prefetch,
                            )
                            set_booking_prepare_response_cache(
                                sid_bs, did_bs, fp_bs, _copy_mod.deepcopy(out_bs)
                            )
                            booking_search_trace(
                                "booking_prepare_done",
                                destination_id=did_bs,
                                session_id_prefix=sid_bs[:12] if len(sid_bs) >= 12 else sid_bs,
                                elapsed_ms=int((_time_mod.perf_counter() - t_prep) * 1000),
                            )
                            self._send_json(out_bs, status=200)
                    except Exception as exc:
                        print(f"[web] /api/wanderly/booking_search_prepare error: {exc!r}")
                        traceback.print_exc()
                        self._send_json({"error": str(exc)}, status=500)
                    return

                if path == "/api/wanderly/booking_search_step":
                    from src.wanderly.ota_booking_search import run_booking_search_step

                    sid_st = (payload.get("session_id") or "").strip()
                    did_st = (payload.get("destination_id") or "").strip()
                    step_st = str(payload.get("step") or "").strip().lower()
                    if not sid_st or not did_st or step_st not in ("flight", "hotel"):
                        self._send_json({"error": "Missing session_id, destination_id, or invalid step."}, status=400)
                        return
                    hi_raw = payload.get("hotel_index")
                    hotel_ix: int | None = None
                    if step_st == "hotel":
                        if hi_raw is None:
                            self._send_json({"error": "hotel_index required for hotel step."}, status=400)
                            return
                        try:
                            hotel_ix = int(hi_raw)
                        except (TypeError, ValueError):
                            self._send_json({"error": "hotel_index must be an integer."}, status=400)
                            return
                    dh_st = payload.get("date_hints") if isinstance(payload.get("date_hints"), dict) else None
                    dests_st = snapshot_destinations_for_model(sid_st)
                    dest_st = next(
                        (x for x in dests_st if isinstance(x, dict) and str(x.get("id") or "") == did_st),
                        None,
                    )
                    if not dest_st:
                        self._send_json({"error": "Destination not found in session."}, status=404)
                        return
                    pills_raw_st = payload.get("profile_pills")
                    if isinstance(pills_raw_st, list) and any(isinstance(x, dict) for x in pills_raw_st):
                        pills_st = [x for x in pills_raw_st if isinstance(x, dict)]
                    else:
                        pills_st = snapshot_profile_pills(sid_st)
                    fl_leg = str(payload.get("flight_leg") or "").strip().lower()
                    flight_leg_kw = fl_leg if fl_leg in ("outbound", "return") else None
                    seg_ix_raw = payload.get("flight_segment_index")
                    flight_seg_ix: int | None = None
                    if seg_ix_raw is not None:
                        try:
                            flight_seg_ix = int(seg_ix_raw)
                        except (TypeError, ValueError):
                            self._send_json({"error": "flight_segment_index must be an integer."}, status=400)
                            return
                    try:
                        result_st = run_booking_search_step(
                            dest_st,
                            step=step_st,
                            hotel_index=hotel_ix,
                            profile_pills=pills_st,
                            date_hints=dh_st,
                            session_id=sid_st,
                            flight_leg=flight_leg_kw,
                            flight_segment_index=flight_seg_ix,
                        )
                        if isinstance(result_st, dict) and result_st.get("search_url"):
                            print(
                                f"[web] booking_search_step session={sid_st[:12]}… dest={did_st} "
                                f"step={step_st} url={result_st.get('search_url')}",
                                flush=True,
                            )
                        dbg = result_st.get("debug_parse") if isinstance(result_st, dict) else None
                        if dbg:
                            print(f"[web] booking_search_step debug_parse step={dbg.get('step')!r}", flush=True)
                        self._send_json(result_st, status=200)
                    except Exception as exc:
                        print(f"[web] /api/wanderly/booking_search_step error: {exc!r}")
                        traceback.print_exc()
                        self._send_json({"ok": False, "error": str(exc)}, status=500)
                    return

                if path == "/plan":
                    user_input = (payload.get("input") or "").strip()
                    if not user_input:
                        self._send_json({"error": "Missing 'input' text."}, status=400)
                        return
                    result = orchestrator.run_initial_flow(user_input)
                    self._send_json(result, status=200)
                    return

                if path == "/traveler":
                    patch = payload.get("traveler_profile")
                    if not isinstance(patch, dict):
                        self._send_json({"error": "Expected object in 'traveler_profile'."}, status=400)
                        return
                    result = orchestrator.update_traveler_profile(patch, source="profile_autosave")
                    self._send_json({"traveler_profile": result}, status=200)
                    return

                if path == "/profiles/create":
                    name = (payload.get("name") or "").strip()
                    result = orchestrator.create_traveler_profile(name or "New Profile")
                    self._send_json({"traveler_profile_entry": result}, status=200)
                    return

                if path == "/profiles/select":
                    profile_id = (payload.get("profile_id") or "").strip()
                    if not profile_id:
                        self._send_json({"error": "Missing 'profile_id'."}, status=400)
                        return
                    result = orchestrator.select_traveler_profile(profile_id)
                    self._send_json({"traveler_profile_entry": result}, status=200)
                    return

                if path == "/itineraries":
                    name = (payload.get("name") or "Option").strip()
                    result = orchestrator.create_itinerary_option(name=name)
                    self._send_json({"itinerary_option": result}, status=200)
                    return

                if path == "/itineraries/generate_more":
                    count = payload.get("count", 2)
                    try:
                        count = int(count)
                    except (TypeError, ValueError):
                        self._send_json({"error": "Count must be an integer."}, status=400)
                        return
                    result = orchestrator.generate_more_options(count=count)
                    self._send_json({"generated": result}, status=200)
                    return

                if path == "/itineraries/select":
                    option_id = (payload.get("option_id") or "").strip()
                    if not option_id:
                        self._send_json({"error": "Missing 'option_id'."}, status=400)
                        return
                    result = orchestrator.select_itinerary_option(option_id=option_id)
                    self._send_json({"active_itinerary": result}, status=200)
                    return

                if path == "/itineraries/delete":
                    option_id = (payload.get("option_id") or "").strip()
                    if not option_id:
                        self._send_json({"error": "Missing 'option_id'."}, status=400)
                        return
                    removed = orchestrator.delete_itinerary_option(option_id=option_id)
                    if not removed:
                        self._send_json({"error": "Unknown itinerary option id."}, status=404)
                        return
                    self._send_json({"deleted": option_id}, status=200)
                    return

                if path == "/itineraries/shortlist":
                    option_ids = payload.get("option_ids")
                    if not isinstance(option_ids, list) or not all(isinstance(x, str) for x in option_ids):
                        self._send_json({"error": "Expected string array in 'option_ids'."}, status=400)
                        return
                    result = orchestrator.shortlist_itinerary_options(option_ids=option_ids)
                    self._send_json({"shortlisted": result}, status=200)
                    return

                if path == "/itineraries/update":
                    option_id = (payload.get("option_id") or "").strip()
                    patch = payload.get("patch")
                    if not option_id or not isinstance(patch, dict):
                        self._send_json(
                            {"error": "Expected 'option_id' and object 'patch'."},
                            status=400,
                        )
                        return
                    result = orchestrator.update_itinerary_option(option_id=option_id, patch=patch)
                    self._send_json({"itinerary_option": result}, status=200)
                    return

                if path == "/replay":
                    enabled = payload.get("enabled")
                    if not isinstance(enabled, bool):
                        self._send_json({"error": "Expected boolean 'enabled'."}, status=400)
                        return
                    result = orchestrator.set_replay_mode(enabled)
                    self._send_json(result, status=200)
                    return

                if path == "/compare":
                    result = orchestrator.generate_comparison_guidance_on_demand()
                    self._send_json({"comparison_guidance": result}, status=200)
                    return

                if path == "/advisor/suggest":
                    message = (payload.get("message") or "").strip()
                    print(f"[web] /advisor/suggest called message_len={len(message)}")
                    result = orchestrator.advisor_suggest(user_message=message, use_local_model=True)
                    print("[web] /advisor/suggest completed")
                    self._send_json({"advisor_suggestion": result}, status=200)
                    return

                if path == "/advisor/approve":
                    print("[web] /advisor/approve called")
                    result = orchestrator.advisor_approve_and_execute()
                    print("[web] /advisor/approve completed")
                    self._send_json(result, status=200)
                    return

                if path == "/advisor/approve_alternative":
                    idx = payload.get("index", 0)
                    try:
                        idx = int(idx)
                    except (TypeError, ValueError):
                        self._send_json({"error": "Alternative index must be an integer."}, status=400)
                        return
                    print(f"[web] /advisor/approve_alternative called index={idx}")
                    result = orchestrator.advisor_approve_alternative_and_execute(alternative_index=idx)
                    print("[web] /advisor/approve_alternative completed")
                    self._send_json(result, status=200)
                    return

                if path == "/itineraries/build_active":
                    print("[web] /itineraries/build_active called")
                    result = orchestrator.build_active_itinerary()
                    print("[web] /itineraries/build_active completed")
                    self._send_json({"active_itinerary": result}, status=200)
                    return

                if path == "/itineraries/check_fit":
                    result = orchestrator.check_itinerary_fit()
                    self._send_json({"itinerary_fit_report": result}, status=200)
                    return

                self._send_json({"error": f"Not found ({path})"}, status=404)
            except Exception as exc:
                print(f"[web] POST error path={getattr(self, 'path', 'unknown')}: {exc!r}")
                traceback.print_exc()
                self._send_json({"error": str(exc)}, status=500)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Web UI running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down web server...")
    finally:
        server.shutdown()
        server.server_close()
        orchestrator.shutdown()
