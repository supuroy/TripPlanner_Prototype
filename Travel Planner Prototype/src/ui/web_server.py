from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from src.orchestrator import TravelPlannerOrchestrator
from src.ui.web import HTML_SCAFFOLD
from src.wanderly.mapper import map_workspace_to_wanderly
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
    set_last_destinations,
    set_last_profile_pills,
    snapshot_destinations_for_model,
    snapshot_profile_pills,
)
from src.wanderly.smoke_service import (
    run_refine_turn,
    run_refine_turn_streaming,
    run_smoke_turn,
    run_smoke_turn_streaming,
)


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


_itinerary_build_locks: dict[str, threading.Lock] = {}
_itinerary_build_locks_guard = threading.Lock()


def _itinerary_build_lock(key: str) -> threading.Lock:
    with _itinerary_build_locks_guard:
        if key not in _itinerary_build_locks:
            _itinerary_build_locks[key] = threading.Lock()
        return _itinerary_build_locks[key]


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
            if parsed.path == "/":
                self._send_html(HTML_SCAFFOLD)
                return
            if parsed.path == "/state":
                self._send_json(orchestrator.get_workspace_state(), status=200)
                return
            if parsed.path == "/health":
                self._send_json({"status": "ok"}, status=200)
                return
            if parsed.path == "/api/wanderly/workspace":
                qs = parse_qs(parsed.query or "")
                sid_list = qs.get("session_id") or []
                sid = sid_list[0] if sid_list else ""
                wanderly = map_workspace_to_wanderly(orchestrator.get_workspace_state())
                if sid:
                    s = get_session(sid)
                    if s.get("ui_destinations"):
                        wanderly["destinations"] = s["ui_destinations"]
                self._send_json({"wanderly": wanderly}, status=200)
                return
            self._send_json({"error": "Not found"}, status=404)

        def do_POST(self) -> None:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(content_length).decode("utf-8")
                payload = json.loads(raw) if raw else {}

                parsed = urlparse(self.path)
                path = parsed.path
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
                    if not msg or not sid:
                        self._send_json({"error": "Missing 'message' or 'session_id'."}, status=400)
                        return
                    try:
                        tid = _wanderly_trace_id(self)
                        t0 = time.perf_counter()
                        _timeline(
                            f"TIMELINE refine_turn_begin trace={tid!r} sid={sid[:12]!r} msg_len={len(msg)}"
                        )
                        out = run_refine_turn(msg, sid)
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
                            out_r = run_refine_turn_streaming(msg, sid, emit=emit_r)
                            emit_r(
                                {
                                    "type": "done",
                                    "session_id": sid,
                                    "wanderly": out_r["wanderly"],
                                    "parse_ok": out_r.get("parse_ok"),
                                }
                            )
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
                    s["ui_destinations"] = dests
                    self._send_json({"ok": True}, status=200)
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
                        )
                        self._send_json(
                            {
                                "session_id": sid,
                                "destination_id": did,
                                "itinerary": rows,
                                "budget_guidance": bg,
                            },
                            status=200,
                        )
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
                        )
                        emit_itin(
                            {
                                "type": "done",
                                "session_id": sid,
                                "destination_id": did,
                                "itinerary": rows,
                                "budget_guidance": bg,
                            }
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
                    s["last_destinations"] = wanderly.get("destinations") or []
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
                            digits = "".join(ch for ch in v if ch.isdigit())
                            if digits:
                                patch["budget_max_usd"] = float(digits)
                        elif k == "who":
                            num = "".join(ch for ch in v if ch.isdigit())
                            if num:
                                patch["group_size"] = int(num)
                            patch["editable_notes"] = v[:500]
                        elif k == "vibe":
                            patch["destination_preferences"] = [x.strip() for x in v.split(",") if x.strip()][:12]
                        elif k == "when":
                            patch["traveler_summary"] = v[:2000]
                        elif k == "duration":
                            patch["pace_preference"] = v[:120]
                    updated = orchestrator.update_traveler_profile(patch, source="wanderly_profile_patch")
                    wanderly = map_workspace_to_wanderly(orchestrator.get_workspace_state())
                    self._send_json({"traveler_profile": updated, "wanderly": wanderly}, status=200)
                    return

                if path == "/api/wanderly/advisor_feed":
                    msg = (payload.get("message") or "").strip()
                    sid = (payload.get("session_id") or "").strip()
                    focus_dest = (payload.get("focus_destination_id") or "").strip()
                    log_itinerary_build(
                        f"BEGIN path=http.advisor_feed sid={sid[:12]!r} focus={focus_dest[:24]!r} msg_chars={len(msg)}"
                    )
                    try:
                        pills: list = []
                        dests: list = []
                        if sid:
                            pills = snapshot_profile_pills(sid)
                            dests = snapshot_destinations_for_model(sid)
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
                        sug = {
                            "assistant_message": md,
                            "refine_prompt": refine_prompt,
                            "primary_recommendation": {"action": "none", "why": "Cursor advisor (cheap model) reply."},
                            "model": "cursor_advisor",
                        }
                        wanderly = map_workspace_to_wanderly(orchestrator.get_workspace_state())
                        if sid:
                            s = get_session(sid)
                            if isinstance(s.get("ui_destinations"), list) and s["ui_destinations"]:
                                wanderly["destinations"] = s["ui_destinations"]
                        self._send_json(
                            {
                                "advisor_suggestion": sug,
                                "feed_markdown": md or "Advisor updated.",
                                "refine_prompt": refine_prompt,
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

                self._send_json({"error": "Not found"}, status=404)
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
