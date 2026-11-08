"""Verify Cursor Apps identity headers (private/team publish)."""

from __future__ import annotations

import os
import threading
from typing import Any, Mapping

import jwt
from jwt import PyJWKClient

_DEFAULT_ISSUER = "https://api.cursor.com/apps/identity"
_CLOCK_LEEWAY_S = 60

_jwks_lock = threading.Lock()
_jwks_client: PyJWKClient | None = None
_jwks_issuer: str = ""


def _trim(value: object) -> str:
    return (value or "").strip() if isinstance(value, str) else ""


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return _trim(value)
    return ""


def _jwks_for(issuer: str) -> PyJWKClient:
    global _jwks_client, _jwks_issuer
    base = issuer.rstrip("/")
    with _jwks_lock:
        if _jwks_client is None or _jwks_issuer != base:
            _jwks_client = PyJWKClient(f"{base}/keys", cache_keys=True)
            _jwks_issuer = base
        return _jwks_client


def get_identity_from_headers(headers: Mapping[str, str]) -> dict[str, Any]:
    """Return {verified: bool, sub?, reason?, kind?, simulated?}. Never raises."""
    app_id = _trim(os.environ.get("CURSOR_APP_ID"))
    on_platform = bool(app_id) or _trim(os.environ.get("VERCEL"))

    if not app_id:
        if on_platform:
            return {"verified": False, "reason": "app_id_unset"}
        # Local dev (matches cursor/identity.dev.js default)
        return {
            "verified": True,
            "sub": "user:dev",
            "kind": "person",
            "simulated": True,
        }

    token = _header(headers, "x-cursor-identity-v2")
    v2 = bool(token)
    if not token:
        token = _header(headers, "x-cursor-identity")
    if not token:
        return {"verified": False, "reason": "missing_header"}

    issuer = _trim(os.environ.get("CURSOR_APP_IDENTITY_ISSUER")) or _DEFAULT_ISSUER
    try:
        client = _jwks_for(issuer)
        key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=app_id,
            issuer=issuer.rstrip("/"),
            leeway=_CLOCK_LEEWAY_S,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError:
        return {"verified": False, "reason": "bad_signature"}
    except Exception:
        return {"verified": False, "reason": "issuer_unreachable"}

    sub = _trim(claims.get("sub"))
    if not sub:
        return {"verified": False, "reason": "invalid_claims"}

    kind = claims.get("kind") or "person"
    if v2:
        if claims.get("ver") not in (2, "2", None):
            return {"verified": False, "reason": "invalid_claims"}
        if kind not in ("person", "key"):
            return {"verified": False, "reason": "invalid_claims"}

    return {
        "verified": True,
        "sub": sub,
        "kind": str(kind),
        "simulated": False,
    }
