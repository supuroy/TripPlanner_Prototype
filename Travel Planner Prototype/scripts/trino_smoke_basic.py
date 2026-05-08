#!/usr/bin/env python3
"""
Minimal Trino smoke test using BasicAuthentication (Ava-style).

Run from the Travel Planner Prototype directory:

    python scripts/trino_smoke_basic.py

Required env:
    TRINO_HOST
    TRINO_CATALOG
    TRINO_USER and TRINO_PASSWORD **or** Vault (see below)

Optional — Vault KV v2 instead of TRINO_PASSWORD in .env:
    VAULT_ADDR
    TRINO_VAULT_SECRET_PATH   (e.g. egdp/trino — path under the KV mount)
    TRINO_VAULT_KV_MOUNT=secret
    TRINO_VAULT_USER_FIELD=username
    TRINO_VAULT_PASSWORD_FIELD=password
    Token: run ``vault login`` (token file) or ``VAULT_TOKEN`` in the shell only — not in .env

Optional env:
    TRINO_PORT=8443
    TRINO_HTTP_SCHEME=https
    TRINO_SCHEMA=default
    TRINO_SMOKE_SQL=SELECT 1 AS test_col
    TRINO_TLS_VERIFY=1
    TRINO_CA_BUNDLE=C:\\path\\to\\corp-ca-bundle.pem
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.integrations.cursor_agent_client import load_env_from_roots
from src.integrations.trino_credentials import resolve_trino_user_and_password


def _env(name: str, default: str | None = None) -> str | None:
    value = (os.environ.get(name) or "").strip()
    return value if value else default


def _require(name: str) -> str:
    value = _env(name)
    if not value:
        print(f"Missing required env: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def main() -> None:
    load_dotenv()
    load_env_from_roots()

    host = _require("TRINO_HOST")
    user, password = resolve_trino_user_and_password()
    catalog = _require("TRINO_CATALOG")
    port = int(_env("TRINO_PORT", "8443") or "8443")
    http_scheme = (_env("TRINO_HTTP_SCHEME", "https") or "https").lower()
    schema = _env("TRINO_SCHEMA", "default")
    sql = (_env("TRINO_SMOKE_SQL", "SELECT 1 AS test_col") or "SELECT 1 AS test_col").strip().rstrip(";")
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
        # Allow TRINO_TLS_VERIFY to be a path as a convenience.
        verify = verify_raw

    try:
        import trino
    except ImportError:
        print("Missing dependency: trino. Install with `pip install -r requirements.txt`.", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting {http_scheme}://{host}:{port} catalog={catalog!r} schema={schema!r} user={user!r}")
    try:
        conn = trino.dbapi.connect(
            host=host,
            port=port,
            catalog=catalog,
            http_scheme=http_scheme,
            schema=schema,
            auth=trino.auth.BasicAuthentication(user, password),
            verify=verify,
        )
        cursor = conn.cursor()
        cursor.execute(sql)
        results = cursor.fetchall()
    except Exception as exc:
        print(f"Trino smoke failed: {exc}", file=sys.stderr)
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            print(
                "Hint: set TRINO_CA_BUNDLE to your corporate CA bundle path (preferred), "
                "or set TRINO_TLS_VERIFY=0 for a temporary diagnostic run.",
                file=sys.stderr,
            )
        sys.exit(1)
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass

    print("Query succeeded. Rows:")
    for row in results:
        print(f"  {row}")
    print("trino_smoke_basic: success")


if __name__ == "__main__":
    main()
