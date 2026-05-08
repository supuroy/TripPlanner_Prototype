#!/usr/bin/env python3
"""
Phase 1 — EGDP / Trino connectivity smoke test (standalone from Wanderly).

Run from the Travel Planner Prototype directory::

    cd "Travel Planner Prototype"
    python scripts/egdp_smoke.py

Requires ``TRINO_HOST``, ``TRINO_CATALOG``, and credentials (``TRINO_USER`` / ``TRINO_PASSWORD`` or Vault — see .env.example).
Optional: ``TRINO_SCHEMA``, ``EGDP_SMOKE_SQL``, ``TRINO_PORT``, ``TRINO_HTTP_SCHEME``, ``TRINO_CA_BUNDLE``.

Exit code 0 on success (rows printed), 1 on configuration or connection errors.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Project root = parent of ``scripts/`` (Travel Planner Prototype)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

from src.integrations.cursor_agent_client import load_env_from_roots
from src.integrations.trino_credentials import resolve_trino_user_and_password


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


def main() -> None:
    load_dotenv()
    load_env_from_roots()

    parser = argparse.ArgumentParser(description="EGDP / Trino hello-world query")
    parser.add_argument(
        "--sql",
        help="Override EGDP_SMOKE_SQL for this run (read-only queries only)",
        default=None,
    )
    args = parser.parse_args()

    host = _require("TRINO_HOST")
    user, password = resolve_trino_user_and_password()
    catalog = _require("TRINO_CATALOG")
    schema = _env("TRINO_SCHEMA") or None
    port = int(_env("TRINO_PORT", "443") or "443")
    http_scheme = (_env("TRINO_HTTP_SCHEME", "https") or "https").lower()
    request_timeout = float(_env("TRINO_REQUEST_TIMEOUT_S", "120") or "120")
    default_sql = "SELECT 1 AS ok, current_timestamp AS server_ts, current_user AS session_user"
    sql = (args.sql or _env("EGDP_SMOKE_SQL") or default_sql).strip().rstrip(";")

    ca_bundle = _env("TRINO_CA_BUNDLE")
    verify_raw = (_env("TRINO_TLS_VERIFY", "1") or "1").strip()
    verify_lower = verify_raw.lower()
    if ca_bundle:
        verify: bool | str = ca_bundle
    elif verify_lower in ("0", "false", "no", "off"):
        verify = False
    elif verify_lower in ("1", "true", "yes", "on", ""):
        verify = True
    else:
        verify = verify_raw

    try:
        from trino.auth import BasicAuthentication
        from trino.dbapi import connect
    except ImportError:
        print("Install the Trino client: pip install trino", file=sys.stderr)
        sys.exit(1)

    auth = BasicAuthentication(user, password)

    print(f"Connecting {http_scheme}://{host}:{port} catalog={catalog!r} schema={schema!r} user={user!r} ...")
    try:
        conn = connect(
            host=host,
            port=port,
            user=user,
            catalog=catalog,
            schema=schema,
            http_scheme=http_scheme,
            auth=auth,
            request_timeout=request_timeout,
            verify=verify,
        )
    except Exception as e:
        print(f"connect() failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchmany(int(_env("EGDP_SMOKE_ROW_LIMIT", "20") or "20"))
        cols = [d[0] for d in (cur.description or [])]
        print(f"Query OK — {len(rows)} row(s) (showing up to limit), columns: {cols}")
        for i, row in enumerate(rows, 1):
            print(f"  [{i}] {row}")
        if not rows:
            print("(no rows; still counts as success if the query completed)")
    except Exception as e:
        print(f"Query failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print("egdp_smoke: success")


if __name__ == "__main__":
    main()
