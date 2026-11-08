"""WSGI entry surface for Cursor Apps / Vercel Python hosting.

Starts the existing ThreadingHTTPServer on a loopback port once, then forwards
each WSGI request with httpx so we keep the full BaseHTTPRequestHandler stack.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any, Callable

import httpx

from src.main import build_orchestrator
from src.ui.web_server import run_web_server

_EMBED_HOST = "127.0.0.1"
_EMBED_PORT = int(os.environ.get("WANDERLY_EMBED_PORT", "18080"))
_started = threading.Lock()
_ready = threading.Event()


def _wait_for_port(host: str, port: int, timeout_s: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _ensure_embedded_server() -> None:
    with _started:
        if _ready.is_set():
            return

        def _run() -> None:
            run_web_server(
                orchestrator_factory=lambda: build_orchestrator(for_web_ui=True),
                host=_EMBED_HOST,
                port=_EMBED_PORT,
            )

        threading.Thread(target=_run, name="wanderly-embed", daemon=True).start()
        if _wait_for_port(_EMBED_HOST, _EMBED_PORT):
            _ready.set()
        else:
            raise RuntimeError(f"Embedded Wanderly server did not start on {_EMBED_HOST}:{_EMBED_PORT}")


def _header_value(environ: dict[str, Any], name: str) -> str | None:
    key = "HTTP_" + name.upper().replace("-", "_")
    val = environ.get(key)
    if val is None and name.lower() == "content-length":
        val = environ.get("CONTENT_LENGTH")
    return str(val) if val is not None else None


def application(environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
    _ensure_embedded_server()
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    query = environ.get("QUERY_STRING") or ""
    url = f"http://{_EMBED_HOST}:{_EMBED_PORT}{path}"
    if query:
        url = f"{url}?{query}"

    headers: dict[str, str] = {}
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            hdr = key[5:].replace("_", "-").title()
            headers[hdr] = str(value)
    for hop in ("host", "connection", "transfer-encoding"):
        headers.pop(hop.title(), None)
        headers.pop(hop.upper(), None)

    body = b""
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
        except ValueError:
            length = 0
        if length > 0:
            body = environ["wsgi.input"].read(length)
        else:
            body = environ["wsgi.input"].read()

    try:
        with httpx.Client(timeout=httpx.Timeout(300.0)) as client:
            response = client.request(method, url, headers=headers, content=body)
    except httpx.HTTPError as exc:
        start_response("502 Bad Gateway", [("Content-Type", "application/json")])
        return [f'{{"error":"upstream_unreachable","detail":{exc!r}}}'.encode()]

    resp_headers: list[tuple[str, str]] = []
    for name, value in response.headers.multi_items():
        lname = name.lower()
        if lname in ("connection", "transfer-encoding", "content-encoding"):
            continue
        resp_headers.append((name, value))
    start_response(f"{response.status_code} {response.reason_phrase}", resp_headers)
    return [response.content]
