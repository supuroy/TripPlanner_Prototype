# Windows launcher for trail-catalog MCP (local Cursor IDE).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m pip install -q "mcp>=1.6.0"
python mcp/trail_catalog_server.py
