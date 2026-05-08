#!/usr/bin/env python3
"""
Phase 1 — Querybook REST smoke test (Pinterest OSS ``/ds`` shape).

Runs a tiny read-only SQL via ``POST …/query_execution/``, polls ``GET …/query_execution/<id>/``,
then fetches a preview from ``GET …/statement_execution/<id>/result/``.

**Expedia EGAP:** internal docs describe ``Authorization: Bearer <token>`` for API/MCP access.
The raw data-science REST paths (e.g. ``/egap/ds/…``) are **not** documented as a supported
end-user API; FAQs say programmatic access should use **Querybook MCP**, **Trino JDBC/Python**,
or scheduling — not ad-hoc REST. This script remains useful where the fork still accepts OSS-style
calls; expect **401** if the deployment blocks that surface.

Run from the Travel Planner Prototype directory::

    cd "Travel Planner Prototype"
    python scripts/querybook_smoke.py

Requires ``QUERYBOOK_BASE_URL`` and token (``QUERYBOOK_API_ACCESS_TOKEN`` or ``QUERYBOOK_ACCESS_TOKEN``),
plus either ``QUERYBOOK_ENGINE_ID`` or ``QUERYBOOK_ENVIRONMENT_ID`` (engines listed for that env).

See .env.example. Exit 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

import httpx

from src.integrations.cursor_agent_client import load_env_from_roots

# Pinterest Querybook ``QueryExecutionStatus`` / ``StatementExecutionStatus`` numeric values
QE_DONE = 3
QE_ERROR = 4
QE_CANCEL = 5
QE_PENDING_REVIEW = 6
QE_REJECTED = 7
SE_DONE = 3
SE_ERROR = 4


def _env(name: str, default: str | None = None) -> str | None:
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    return default


def _require(name: str) -> str:
    v = _env(name)
    if not v:
        print(f"Missing required env: {name}", file=sys.stderr)
        sys.exit(1)
    return v


def _token() -> str:
    t = _env("QUERYBOOK_API_ACCESS_TOKEN") or _env("QUERYBOOK_ACCESS_TOKEN")
    if not t:
        print("Missing QUERYBOOK_API_ACCESS_TOKEN or QUERYBOOK_ACCESS_TOKEN", file=sys.stderr)
        sys.exit(1)
    return t


def _truthy(name: str, default: str = "1") -> bool:
    return (_env(name, default) or "").strip().lower() not in ("0", "false", "no", "off", "")


def _unwrap(resp_json: Any) -> Any:
    if isinstance(resp_json, dict) and "data" in resp_json:
        return resp_json["data"]
    return resp_json


def _qb_headers() -> dict[str, str]:
    # EG Querybook MCP/API docs: Authorization: Bearer <token>. OSS forks sometimes use api-access-token.
    style = (_env("QUERYBOOK_TOKEN_HEADER", "bearer") or "bearer").strip().lower()
    token = _token()
    if style in ("bearer", "authorization", "authorization-bearer"):
        return {"Authorization": f"Bearer {token}"}
    return {"api-access-token": token}


def _resolve_engine_id(client: httpx.Client, base: str, ds: str, headers: dict[str, str]) -> int:
    explicit = _env("QUERYBOOK_ENGINE_ID")
    if explicit:
        return int(explicit)
    env_id = _env("QUERYBOOK_ENVIRONMENT_ID")
    if not env_id:
        print("Set QUERYBOOK_ENGINE_ID or QUERYBOOK_ENVIRONMENT_ID", file=sys.stderr)
        sys.exit(1)
    params_arg = json.dumps({"environment_id": int(env_id)})
    url = f"{base.rstrip('/')}{ds}/query_engine/"
    r = client.get(url, headers=headers, params={"params": params_arg})
    print(f"GET {url} -> HTTP {r.status_code}")
    if r.status_code >= 400:
        print(r.text[:4000], file=sys.stderr)
        sys.exit(1)
    engines = _unwrap(r.json())
    if not isinstance(engines, list) or len(engines) == 0:
        print("No query engines returned for environment", file=sys.stderr)
        sys.exit(1)
    substr = (_env("QUERYBOOK_ENGINE_NAME_SUBSTR") or "").strip().lower()
    if substr:
        for e in engines:
            if not isinstance(e, dict):
                continue
            name = str(e.get("name") or "").lower()
            if substr in name:
                return int(e["id"])
    first = engines[0]
    if not isinstance(first, dict) or "id" not in first:
        print("Unexpected query_engine payload", file=sys.stderr)
        sys.exit(1)
    eid = int(first["id"])
    print(f"Using engine_id={eid} ({first.get('name', '')!r})")
    return eid


def _qe_terminal_fail(status: Any) -> str | None:
    if status == QE_ERROR or status == "ERROR":
        return "ERROR"
    if status == QE_CANCEL or status == "CANCEL":
        return "CANCEL"
    if status == QE_PENDING_REVIEW or status == "PENDING_REVIEW":
        return "PENDING_REVIEW (ad-hoc may require review in this environment)"
    if status == QE_REJECTED or status == "REJECTED":
        return "REJECTED"
    return None


def main() -> None:
    load_dotenv()
    load_env_from_roots()

    parser = argparse.ArgumentParser(description="Querybook /ds smoke")
    parser.add_argument("--sql", help="Override QUERYBOOK_SMOKE_SQL")
    parser.add_argument("--engine-id", type=int, help="Override QUERYBOOK_ENGINE_ID")
    args = parser.parse_args()

    base = _require("QUERYBOOK_BASE_URL").rstrip("/")
    ds = (_env("QUERYBOOK_DS_PATH", "/ds") or "/ds").rstrip("/")
    if not ds.startswith("/"):
        ds = "/" + ds
    timeout = float(_env("QUERYBOOK_REQUEST_TIMEOUT_S", "120") or "120")
    verify = _truthy("QUERYBOOK_TLS_VERIFY", "1")
    max_wait = float(_env("QUERYBOOK_POLL_MAX_WAIT_S", "300") or "300")
    interval = float(_env("QUERYBOOK_POLL_INTERVAL_S", "2") or "2")
    default_sql = "SELECT 1 AS ok, current_timestamp AS server_ts"
    sql = (args.sql or _env("QUERYBOOK_SMOKE_SQL") or default_sql).strip().rstrip(";")
    result_limit = int(_env("QUERYBOOK_RESULT_PREVIEW_LIMIT", "20") or "20")

    headers = _qb_headers()

    with httpx.Client(timeout=timeout, verify=verify) as client:
        engine_id = int(args.engine_id) if args.engine_id is not None else _resolve_engine_id(client, base, ds, headers)

        post_url = f"{base}{ds}/query_execution/"
        print(f"POST {post_url} engine_id={engine_id} ...")
        try:
            pr = client.post(post_url, headers=headers, json={"query": sql, "engine_id": engine_id})
        except httpx.RequestError as e:
            print(f"POST failed: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"HTTP {pr.status_code}")
        if 300 <= pr.status_code < 400:
            loc = (pr.headers.get("location") or "").lower()
            print(pr.text[:2000], file=sys.stderr)
            print(
                "\nRedirect (3xx) — server is sending you to web SSO, not accepting this request as API auth.\n"
                "  • Confirm VPN / ZPA (or AnyConnect) and Querybook access (e.g. egdp-analysts per internal docs).\n"
                "  • EG docs use ``Authorization: Bearer <token>`` for MCP/API; default here matches that.\n"
                "  • Raw ``/egap/ds/…`` REST may still require a browser session or be closed to clients — see Querybook FAQs.\n"
                "  • If you must experiment: QUERYBOOK_TOKEN_HEADER=api-access-token (OSS-style fallback only).\n",
                file=sys.stderr,
            )
            if "okta" in loc or "oauth" in loc:
                print("  (Location → Okta/OAuth.)\n", file=sys.stderr)
            sys.exit(1)
        if pr.status_code >= 400:
            print(pr.text[:4000], file=sys.stderr)
            if pr.status_code == 401:
                print(
                    "\n401 hints (EG Querybook):\n"
                    "  • Use ``Authorization: Bearer <token>`` (default QUERYBOOK_TOKEN_HEADER=bearer) with profile → API Access Token.\n"
                    "  • Internal FAQ: Querybook’s **REST** data-science API is **not** offered to users; 401 here may be policy, not a typo.\n"
                    "    Prefer **Querybook MCP** (``https://querybook.expedia.biz/mcp``) or **Trino** (JDBC / Python trino) for supported automation.\n"
                    "  • Regenerate token if needed; no extra quotes in .env; VPN on.\n"
                    "  • OSS-only fallback: QUERYBOOK_TOKEN_HEADER=api-access-token.\n"
                    f"  • Paths: QUERYBOOK_DS_PATH=/egap/ds, QUERYBOOK_BASE_URL=https://querybook.expedia.biz (example).",
                    file=sys.stderr,
                )
            sys.exit(1)
        try:
            created = _unwrap(pr.json())
        except Exception:
            print(pr.text[:4000], file=sys.stderr)
            sys.exit(1)
        if not isinstance(created, dict) or "id" not in created:
            print("Unexpected create response (no id)", file=sys.stderr)
            sys.exit(1)
        qid = int(created["id"])
        print(f"query_execution id={qid} initial status={created.get('status')!r}")

        deadline = time.monotonic() + max_wait
        ex: dict[str, Any] = created
        while time.monotonic() < deadline:
            st = ex.get("status")
            fail = _qe_terminal_fail(st)
            if fail:
                print(f"Query execution ended unsuccessfully: {fail}", file=sys.stderr)
                err_url = f"{base}{ds}/query_execution/{qid}/error/"
                try:
                    er = client.get(err_url, headers=headers)
                    if er.status_code == 200:
                        print(er.text[:4000], file=sys.stderr)
                except Exception:
                    pass
                sys.exit(1)
            if st == QE_DONE or st == "DONE":
                break
            time.sleep(interval)
            gr = client.get(f"{base}{ds}/query_execution/{qid}/", headers=headers)
            if gr.status_code >= 400:
                print(gr.text[:4000], file=sys.stderr)
                sys.exit(1)
            ex = _unwrap(gr.json())
            if not isinstance(ex, dict):
                print("Unexpected poll payload", file=sys.stderr)
                sys.exit(1)
            print(f"  poll status={ex.get('status')!r}")
        else:
            print(f"Timed out after {max_wait}s (last status={ex.get('status')!r})", file=sys.stderr)
            sys.exit(1)

        sdeadline = time.monotonic() + max_wait
        stmts: list[Any] = []
        while time.monotonic() < sdeadline:
            stmts = ex.get("statement_executions") or []
            if isinstance(stmts, list) and stmts and isinstance(stmts[0], dict) and stmts[0].get("id") is not None:
                break
            time.sleep(interval)
            gr = client.get(f"{base}{ds}/query_execution/{qid}/", headers=headers)
            if gr.status_code >= 400:
                print(gr.text[:4000], file=sys.stderr)
                sys.exit(1)
            ex = _unwrap(gr.json())
            if not isinstance(ex, dict):
                print("Unexpected poll payload", file=sys.stderr)
                sys.exit(1)
        else:
            print("No statement_executions appeared before timeout", file=sys.stderr)
            sys.exit(1)

        sid = int(stmts[0]["id"])
        sdeadline = time.monotonic() + max_wait
        while time.monotonic() < sdeadline:
            gr = client.get(f"{base}{ds}/query_execution/{qid}/", headers=headers)
            ex2 = _unwrap(gr.json())
            stmts2 = ex2.get("statement_executions") or []
            st0 = stmts2[0].get("status") if stmts2 and isinstance(stmts2[0], dict) else None
            if st0 == SE_DONE or st0 == "DONE":
                break
            if st0 == SE_ERROR or st0 == "ERROR":
                print("Statement execution ERROR", file=sys.stderr)
                sys.exit(1)
            time.sleep(interval)
        else:
            print("Statement did not reach DONE in time", file=sys.stderr)
            sys.exit(1)

        params_arg = json.dumps({"limit": result_limit})
        res_url = f"{base}{ds}/statement_execution/{sid}/result/"
        print(f"GET {res_url} (preview limit={result_limit}) ...")
        rr = client.get(res_url, headers=headers, params={"params": params_arg})
        print(f"HTTP {rr.status_code}")
        if rr.status_code >= 400:
            print(rr.text[:4000], file=sys.stderr)
            sys.exit(1)
        ct = rr.headers.get("content-type", "")
        if "json" in ct.lower():
            try:
                preview = rr.json()
                print("Result preview:", json.dumps(preview, indent=2, default=str)[:8000])
            except Exception:
                print(rr.text[:8000])
        else:
            print(rr.text[:8000])

    print("querybook_smoke: success")


if __name__ == "__main__":
    main()
