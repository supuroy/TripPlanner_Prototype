#!/usr/bin/env python3
"""
Phase 1 — Analytics Workbench HTTP smoke test (config-driven).

Per EGAP FAQ: AWB’s GraphQL API exposes **UI metadata** (clusters, schemas, groups,
LDAP, cost metadata, etc.) — **not** data-lake SQL results. For ``SELECT``/tables,
use **Trino** (``egdp_smoke.py``) or **Querybook MCP**, not AWB GraphQL.

Internal AWB REST shapes differ by deployment. This script performs one (optional)
follow-up poll so you can map real routes from browser Network or internal docs.

Run from the Travel Planner Prototype directory::

    cd "Travel Planner Prototype"
    python scripts/analytics_workbench_smoke.py

Required: ``AWB_BASE_URL``, ``AWB_ACCESS_TOKEN``, and either ``AWB_SMOKE_PATH`` (REST) or
``AWB_GRAPHQL_QUERY`` / ``AWB_GRAPHQL_QUERY_FILE`` (GraphQL — Expedia AWB uses ``POST /api/v1/graphql``).

Optional: ``AWB_SMOKE_METHOD``, ``AWB_SMOKE_BODY_JSON``, ``AWB_GRAPHQL_VARIABLES_JSON``,
``AWB_GRAPHQL_OPERATION_NAME``, ``AWB_GRAPHQL_ASSERT_DATA_PATH``, ``AWB_GRAPHQL_MIN_ROWS``,
polling env vars below.

Exit code 0 on HTTP 2xx and optional poll success, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

import httpx

from src.integrations.cursor_agent_client import load_env_from_roots


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


def _truthy(name: str, default: str = "1") -> bool:
    return (_env(name, default) or "").strip().lower() not in ("0", "false", "no", "off", "")


def _graphql_assert_data(j0: Any) -> None:
    """Optional: require non-null ``data`` subtree and/or minimum list length (tabular results)."""
    path = (_env("AWB_GRAPHQL_ASSERT_DATA_PATH") or "").strip()
    if not path:
        return
    node = _get_by_path(j0, path)
    if node is None:
        print(
            f"AWB_GRAPHQL_ASSERT_DATA_PATH={path!r} is null or missing in response.",
            file=sys.stderr,
        )
        sys.exit(1)
    min_raw = (_env("AWB_GRAPHQL_MIN_ROWS") or "").strip()
    if min_raw:
        mn = int(min_raw)
        if isinstance(node, list):
            if len(node) < mn:
                print(
                    f"Expected at least {mn} row(s) at {path!r}, got {len(node)}.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"Assert OK: {path} → {len(node)} row(s)")
            if node:
                print(f"  first row: {json.dumps(node[0], default=str)[:600]}")
        elif isinstance(node, dict) and mn <= 1:
            print(f"Assert OK: {path} → object (counts as 1 record for MIN_ROWS<=1)")
            print(f"  keys: {list(node.keys())[:20]}")
        else:
            print(
                f"AWB_GRAPHQL_MIN_ROWS is set but value at {path!r} is not a list (got {type(node).__name__}).",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        print(f"Assert OK: {path} is non-null")
        if isinstance(node, list):
            print(f"  list length {len(node)}")
        elif isinstance(node, dict):
            print(f"  object keys: {list(node.keys())[:15]}")


def _get_by_path(obj: Any, path: str) -> Any:
    """Dot path only, e.g. ``data.executionId`` (no ``[]`` indices)."""
    cur = obj
    for part in path.split("."):
        if not part:
            continue
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _auth_headers(token: str) -> dict[str, str]:
    mode = (_env("AWB_AUTH_MODE", "bearer") or "bearer").strip().lower()
    if mode == "bearer":
        return {"Authorization": f"Bearer {token}"}
    if mode in ("api-access-token", "api_access_token"):
        return {"api-access-token": token}
    if mode == "header":
        name = _require("AWB_AUTH_HEADER_NAME")
        return {name: token}
    print(f"Unknown AWB_AUTH_MODE={mode!r} (use bearer, api-access-token, header)", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    load_dotenv()
    load_env_from_roots()

    parser = argparse.ArgumentParser(description="Analytics Workbench HTTP smoke")
    parser.add_argument("--path", help="Override AWB_SMOKE_PATH for this run")
    parser.add_argument("--method", help="Override AWB_SMOKE_METHOD")
    args = parser.parse_args()

    base = _require("AWB_BASE_URL").rstrip("/")
    token = _require("AWB_ACCESS_TOKEN")
    timeout = float(_env("AWB_REQUEST_TIMEOUT_S", "120") or "120")
    verify = _truthy("AWB_TLS_VERIFY", "1")

    gq = (_env("AWB_GRAPHQL_QUERY") or "").strip()
    gq_file = (_env("AWB_GRAPHQL_QUERY_FILE") or "").strip()
    if gq_file and not gq:
        p = Path(gq_file).expanduser()
        if not p.is_file():
            p2 = _ROOT / gq_file.lstrip("./\\")
            if p2.is_file():
                p = p2
        try:
            gq = p.read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"AWB_GRAPHQL_QUERY_FILE read failed ({p}): {e}", file=sys.stderr)
            sys.exit(1)

    body: Any = None
    path: str
    method: str

    if gq:
        path = (args.path or _env("AWB_SMOKE_PATH") or "/api/v1/graphql").strip()
        method = ((args.method or _env("AWB_SMOKE_METHOD") or "POST")).strip().upper()
        vars_raw = _env("AWB_GRAPHQL_VARIABLES_JSON") or "{}"
        try:
            variables = json.loads(vars_raw)
        except json.JSONDecodeError as e:
            print(f"Invalid AWB_GRAPHQL_VARIABLES_JSON: {e}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(variables, dict):
            print("AWB_GRAPHQL_VARIABLES_JSON must be a JSON object", file=sys.stderr)
            sys.exit(1)
        body = {"query": gq, "variables": variables}
        op = (_env("AWB_GRAPHQL_OPERATION_NAME") or "").strip()
        if op:
            body["operationName"] = op
    else:
        path = (args.path or _env("AWB_SMOKE_PATH") or "").strip()
        if not path:
            print(
                "Set AWB_SMOKE_PATH (REST) or AWB_GRAPHQL_QUERY / AWB_GRAPHQL_QUERY_FILE (GraphQL).",
                file=sys.stderr,
            )
            sys.exit(1)
        method = ((args.method or _env("AWB_SMOKE_METHOD", "GET")) or "GET").strip().upper()
        raw_body = _env("AWB_SMOKE_BODY_JSON")
        if raw_body:
            try:
                body = json.loads(raw_body)
            except json.JSONDecodeError as e:
                print(f"Invalid AWB_SMOKE_BODY_JSON: {e}", file=sys.stderr)
                sys.exit(1)

    if not path.startswith("/"):
        path = "/" + path

    if "graphql" in path.lower():
        if body is None:
            print(
                "This URL is GraphQL: you must send a JSON body with a ``query`` field.\n"
                "  • Set AWB_GRAPHQL_QUERY or AWB_GRAPHQL_QUERY_FILE (and usually AWB_GRAPHQL_VARIABLES_JSON, "
                "AWB_GRAPHQL_OPERATION_NAME), **or**\n"
                "  • Set AWB_SMOKE_METHOD=POST and AWB_SMOKE_BODY_JSON to the full "
                "{operationName,query,variables} object.\n"
                "Do **not** use GET to /graphql with an empty body. "
                "If you set AWB_SMOKE_PATH=/api/v1/graphql, also set AWB_GRAPHQL_QUERY_FILE=./scripts/awb_smoke_graphql_ldap_user.graphql "
                "(or remove AWB_SMOKE_PATH so the GraphQL branch supplies the path).",
                file=sys.stderr,
            )
            sys.exit(1)
        if method == "GET":
            method = "POST"
            print("Note: switched method to POST for GraphQL endpoint (GET has no query body).", file=sys.stderr)

    headers = _auth_headers(token)
    extra = _env("AWB_EXTRA_HEADERS_JSON")
    if extra:
        try:
            headers.update(json.loads(extra))
        except json.JSONDecodeError as e:
            print(f"Invalid AWB_EXTRA_HEADERS_JSON: {e}", file=sys.stderr)
            sys.exit(1)

    url = urljoin(base + "/", path.lstrip("/"))

    poll_template = (_env("AWB_POLL_PATH_TEMPLATE") or "").strip()
    id_path = (_env("AWB_POLL_ID_JSON_PATH") or _env("AWB_START_ID_JSON_PATH") or "").strip()
    status_path = (_env("AWB_POLL_STATUS_JSON_PATH") or "").strip()
    done_raw = (_env("AWB_POLL_DONE_VALUES") or "DONE,SUCCEEDED,COMPLETED,success,3").strip()
    done_values = {x.strip() for x in done_raw.split(",") if x.strip()}
    fail_raw = (_env("AWB_POLL_FAIL_VALUES") or "FAILED,ERROR,error,4").strip()
    fail_values = {x.strip() for x in fail_raw.split(",") if x.strip()}
    max_wait = float(_env("AWB_POLL_MAX_WAIT_S", "120") or "120")
    poll_interval = float(_env("AWB_POLL_INTERVAL_S", "2") or "2")

    print(f"{method} {url} ...")

    with httpx.Client(timeout=timeout, verify=verify) as client:
        try:
            r0 = client.request(method, url, headers=headers, json=body if method != "GET" else None)
        except httpx.RequestError as e:
            print(f"Request failed: {e}", file=sys.stderr)
            sys.exit(1)

    print(f"HTTP {r0.status_code}")
    try:
        j0 = r0.json()
    except Exception:
        j0 = None
    if j0 is not None:
        print("Response JSON (truncated):", json.dumps(j0, indent=2, default=str)[:8000])
    else:
        print(r0.text[:4000])

    if r0.status_code >= 400:
        if r0.status_code == 404:
            print(
                "Hint: 404 usually means AWB_SMOKE_PATH (and/or AWB_BASE_URL) does not match this "
                "deployment. Copy path + method from browser DevTools (Network) on a working AWB page, "
                "or use internal API docs — /api/v1/me is only an example.",
                file=sys.stderr,
            )
        sys.exit(1)

    if isinstance(j0, dict) and j0.get("errors"):
        print("GraphQL returned errors in the response body:", file=sys.stderr)
        print(json.dumps(j0["errors"], indent=2, default=str)[:8000], file=sys.stderr)
        sys.exit(1)

    _graphql_assert_data(j0)

    if not poll_template or "{" not in poll_template:
        print("analytics_workbench_smoke: success (single request; set AWB_POLL_PATH_TEMPLATE + AWB_POLL_ID_JSON_PATH for polling)")
        return

    if not id_path:
        print("AWB_POLL_PATH_TEMPLATE set but missing AWB_POLL_ID_JSON_PATH (or AWB_START_ID_JSON_PATH)", file=sys.stderr)
        sys.exit(1)

    rid = _get_by_path(j0, id_path)
    if rid is None:
        # Async poll example env (data.id) does not match single-shot GraphQL (e.g. data.ldapUser.id).
        if isinstance(j0, dict) and "data" in j0 and not j0.get("errors"):
            print(
                f"No value at AWB_POLL_ID_JSON_PATH={id_path!r} — this response is already a complete GraphQL result.\n"
                f"  Unset AWB_POLL_PATH_TEMPLATE, AWB_POLL_ID_JSON_PATH, and AWB_POLL_STATUS_JSON_PATH "
                f"unless you are testing a multi-step (submit → poll) API.",
                file=sys.stderr,
            )
            print("analytics_workbench_smoke: success")
            return
        print(f"Could not read id from path {id_path!r} in start response", file=sys.stderr)
        sys.exit(1)
    rid_s = str(rid)
    poll_path = poll_template.format(id=rid_s, executionId=rid_s, runId=rid_s)
    if not poll_path.startswith("/"):
        poll_path = "/" + poll_path
    poll_url = urljoin(base + "/", poll_path.lstrip("/"))

    if not status_path:
        print("AWB_POLL_STATUS_JSON_PATH required when polling", file=sys.stderr)
        sys.exit(1)

    deadline = time.monotonic() + max_wait
    last_status: Any = None
    with httpx.Client(timeout=timeout, verify=verify) as client:
        while time.monotonic() < deadline:
            try:
                rp = client.get(poll_url, headers=headers)
            except httpx.RequestError as e:
                print(f"Poll request failed: {e}", file=sys.stderr)
                sys.exit(1)
            print(f"Poll GET {poll_url} -> HTTP {rp.status_code}")
            if rp.status_code >= 400:
                sys.exit(1)
            try:
                jp = rp.json()
            except Exception:
                print(rp.text[:2000])
                sys.exit(1)
            last_status = _get_by_path(jp, status_path)
            ls_norm = str(last_status).strip() if last_status is not None else ""
            print(f"  status ({status_path}) = {last_status!r}")
            candidates = {ls_norm}
            if isinstance(last_status, (int, float)):
                candidates.add(str(int(last_status)))
            if ls_norm in done_values or candidates & done_values:
                print("analytics_workbench_smoke: success (poll reached done state)")
                print(json.dumps(jp, indent=2, default=str)[:8000])
                return
            if ls_norm in fail_values:
                print(f"Poll reported failure status: {last_status!r}", file=sys.stderr)
                sys.exit(1)
            time.sleep(poll_interval)

    print(f"Poll timed out after {max_wait}s (last status={last_status!r})", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
