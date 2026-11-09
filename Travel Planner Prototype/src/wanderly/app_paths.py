"""Writable cache and data paths (local repo vs serverless / Cursor Apps)."""

from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def hosted_runtime() -> bool:
    if (os.environ.get("CURSOR_APP_ID") or "").strip():
        return True
    if (os.environ.get("VERCEL") or "").strip():
        return True
    if (os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or "").strip():
        return True
    if (os.environ.get("LAMBDA_TASK_ROOT") or "").strip():
        return True
    root = package_root()
    return str(root).startswith("/var/task")


@lru_cache(maxsize=1)
def cache_root() -> Path:
    """Directory for logs, trip JSON, session snapshots, and planner caches."""
    raw = (os.environ.get("WANDERLY_CACHE_DIR") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
    elif hosted_runtime():
        p = Path(tempfile.gettempdir()) / "wanderly-cache"
    else:
        p = package_root() / ".cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_subpath(*parts: str) -> Path:
    return cache_root().joinpath(*parts)


def resolve_under_cache(path: str | Path) -> Path:
    """Map ``.cache/...`` (or relative) paths onto ``cache_root()`` on serverless."""
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    if p.parts and p.parts[0] == ".cache":
        return cache_subpath(*p.parts[1:])
    return (Path.cwd() / p).resolve()
