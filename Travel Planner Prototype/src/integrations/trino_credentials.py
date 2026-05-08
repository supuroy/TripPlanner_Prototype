"""
Resolve Trino Basic-auth user/password from the environment or HashiCorp Vault KV v2.

Keep secrets in Vault, not in ``.env``. KV v2 HTTP shape:
``GET {VAULT_ADDR}/v1/{mount}/data/{path}`` with ``X-Vault-Token``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx


def _env(name: str, default: str | None = None) -> str | None:
    v = (os.environ.get(name) or "").strip()
    return v if v else default


def _vault_token() -> str | None:
    t = _env("VAULT_TOKEN")
    if t:
        return t
    token_file = _env("VAULT_TOKEN_PATH")
    if token_file:
        p = Path(token_file).expanduser()
    else:
        p = Path.home() / ".vault-token"
    if not p.is_file():
        return None
    raw = p.read_text(encoding="utf-8").strip()
    return raw or None


def vault_tls_verify() -> bool | str:
    """TLS verify for Vault HTTPS: PEM path or bool. Reuses Trino CA settings if Vault-specific unset."""
    ca = _env("VAULT_CA_BUNDLE") or _env("TRINO_CA_BUNDLE")
    verify_raw = (_env("VAULT_TLS_VERIFY") or _env("TRINO_TLS_VERIFY", "1") or "1").strip()
    verify_lower = verify_raw.lower()
    if ca:
        return ca
    if verify_lower in ("0", "false", "no", "off"):
        return False
    if verify_lower in ("1", "true", "yes", "on", ""):
        return True
    return verify_raw


def read_vault_kv_v2_secrets(addr: str, token: str, mount: str, secret_path: str) -> dict[str, Any]:
    url = f"{addr.rstrip('/')}/v1/{mount.strip('/')}/data/{secret_path.strip('/')}"
    headers: dict[str, str] = {"X-Vault-Token": token}
    ns = _env("VAULT_NAMESPACE")
    if ns:
        headers["X-Vault-Namespace"] = ns
    verify = vault_tls_verify()
    try:
        r = httpx.get(url, headers=headers, timeout=30.0, verify=verify)
    except httpx.RequestError as e:
        print(f"Vault request failed: {e}", file=sys.stderr)
        sys.exit(1)
    if r.status_code == 404:
        print(f"Vault KV path not found (404): {url}", file=sys.stderr)
        sys.exit(1)
    if r.status_code in (401, 403):
        print(
            f"Vault denied ({r.status_code}). Renew login: `vault login` or set VAULT_TOKEN for this shell only.",
            file=sys.stderr,
        )
        sys.exit(1)
    if r.status_code >= 400:
        print(f"Vault HTTP {r.status_code}: {(r.text or '')[:2000]}", file=sys.stderr)
        sys.exit(1)
    try:
        body = r.json()
    except Exception:
        print(f"Vault returned non-JSON: {(r.text or '')[:2000]}", file=sys.stderr)
        sys.exit(1)
    data = body.get("data", {}).get("data")
    if not isinstance(data, dict):
        print("Vault KV v2 response missing data.data object.", file=sys.stderr)
        sys.exit(1)
    return data


def _require_env(name: str) -> str:
    v = _env(name)
    if not v:
        print(f"Missing required env: {name}", file=sys.stderr)
        sys.exit(1)
    return v


def resolve_trino_user_and_password() -> tuple[str, str]:
    """
    Prefer env vars. If either is missing, load from Vault KV v2 using ``TRINO_VAULT_SECRET_PATH``.

    - ``TRINO_USER`` / ``TRINO_PASSWORD`` in ``.env`` (password empty → Vault)
    - Or both fields in Vault: ``username`` and ``password`` by default (field names configurable)
    """
    user_env = _env("TRINO_USER")
    pass_env = _env("TRINO_PASSWORD")

    if user_env and pass_env:
        return user_env.strip(), pass_env.strip()

    secret_path = _env("TRINO_VAULT_SECRET_PATH")
    if not secret_path:
        if not user_env:
            print("Missing TRINO_USER.", file=sys.stderr)
        if not pass_env:
            print(
                "Missing TRINO_PASSWORD. Set it, or set TRINO_VAULT_SECRET_PATH and use Vault.",
                file=sys.stderr,
            )
        sys.exit(1)

    addr = _require_env("VAULT_ADDR")
    token = _vault_token()
    if not token:
        print(
            "Vault token missing. Run `vault login` (writes ~/.vault-token on Unix or the equivalent under your profile on Windows) "
            "or set VAULT_TOKEN "
            "for this shell only — never commit tokens to .env.",
            file=sys.stderr,
        )
        sys.exit(1)

    mount = _env("TRINO_VAULT_KV_MOUNT", "secret") or "secret"
    user_field = _env("TRINO_VAULT_USER_FIELD", "username") or "username"
    pass_field = _env("TRINO_VAULT_PASSWORD_FIELD", "password") or "password"

    secrets = read_vault_kv_v2_secrets(addr, token, mount, secret_path)

    if user_env:
        user = user_env.strip()
    else:
        u = secrets.get(user_field)
        if not isinstance(u, str) or not u.strip():
            print(f"Vault field {user_field!r} missing or empty (or set TRINO_USER).", file=sys.stderr)
            sys.exit(1)
        user = u.strip()

    if pass_env:
        password = pass_env.strip()
    else:
        p = secrets.get(pass_field)
        if not isinstance(p, str) or not p.strip():
            print(f"Vault field {pass_field!r} missing or empty (or set TRINO_PASSWORD).", file=sys.stderr)
            sys.exit(1)
        password = p.strip()

    return user, password


def resolve_trino_password() -> str:
    """Backward-compatible: same as ``resolve_trino_user_and_password()[1]``."""
    _, pw = resolve_trino_user_and_password()
    return pw
