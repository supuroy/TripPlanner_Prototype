"""
Wanderly — Cursor Apps / Vercel Python entrypoint.

The hosted platform loads ``app`` (WSGI). Local ``python main.py`` runs the UI
server directly on ``PORT``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SKIP = frozenset(
    {
        "Figma Prototype",
        "config",
        "expedia-listing-cli",
        "node_modules",
        ".git",
        ".cursor",
    }
)


def _backend_dir() -> Path:
    for child in sorted(_ROOT.iterdir()):
        if not child.is_dir() or child.name in _SKIP or child.name.startswith("."):
            continue
        if (child / "main.py").is_file() and (child / "requirements.txt").is_file():
            return child
    raise RuntimeError(f"Wanderly backend package not found under {_ROOT}")


def _bootstrap_backend() -> Path:
    backend = _backend_dir()
    os.chdir(backend)
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    return backend


_backend_dir = _bootstrap_backend()

from src.ui.wsgi_proxy import application as app  # noqa: E402  # isort: skip

__all__ = ["app"]


def run_direct() -> None:
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    sys.argv = ["main.py", "--ui", "--host", host, "--port", str(port)]
    from src.main import main as planner_main

    planner_main()


if __name__ == "__main__":
    run_direct()
