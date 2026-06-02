#!/usr/bin/env python3
"""
Start trail-catalog MCP (stdio). Used by Cursor IDE and Cloud Agent mcpServers.

Installs the ``mcp`` package if missing, then runs ``mcp/trail_catalog_server.py``.
Works on Windows and Linux (no bash required).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp" / "trail_catalog_server.py"


def main() -> None:
    os.chdir(ROOT)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", "mcp>=1.6.0"],
        env=env,
    )
    os.execve(
        sys.executable,
        [sys.executable, str(SERVER)],
        env,
    )


if __name__ == "__main__":
    main()
