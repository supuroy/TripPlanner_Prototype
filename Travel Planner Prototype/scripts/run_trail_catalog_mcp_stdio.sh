#!/usr/bin/env bash
# Launch trail-catalog MCP for Cursor Cloud Agent stdio (Linux VM).
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
python3 -m pip install -q 'mcp>=1.6.0' 2>/dev/null || pip3 install -q 'mcp>=1.6.0'
exec python3 mcp/trail_catalog_server.py
