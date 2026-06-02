#!/usr/bin/env python3
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.poc_cursor_cloud_trail_agent import trail_catalog_mcp_servers  # noqa: E402
from src.integrations.cursor_agent_client import (  # noqa: E402
    basic_client,
    consume_sse_assistant_streaming,
    create_agent_run,
    get_api_key,
    load_env_from_roots,
)

CASES = [
    (
        "ack_mcp_no_tools",
        'You have trail-catalog MCP attached. Do not call tools. Reply JSON: {"ok": true}',
    ),
    ("list_mcp", "List which MCP servers you can use. Reply in one short paragraph."),
    (
        "get_mcp_tools",
        "Use get_mcp_tools for server trail-catalog tool trail_catalog_stats. Return JSON.",
    ),
    (
        "call_stats",
        "Call trail_catalog_stats on the trail-catalog MCP server. Return JSON: {\"ok\": true}",
    ),
]


def main() -> int:
    load_env_from_roots()
    with basic_client(get_api_key(), timeout_s=360.0) as client:
        for name, prompt in CASES:
            ev: list[str] = []
            tools: list[str] = []

            def emit(e: dict, tools=tools, ev=ev) -> None:
                ev.append(str(e.get("type")))
                if e.get("type") == "tool_call":
                    tools.append(f"{e.get('name')}:{e.get('status')}")
                    print(f"    tool {e.get('name')} {e.get('status')}", flush=True)
                if e.get("type") == "assistant_delta":
                    print(f"    +{len(e.get('delta') or '')}", flush=True)

            print(f"\n=== {name} ===", flush=True)
            aid, rid = create_agent_run(client, prompt, mcp_servers=trail_catalog_mcp_servers())
            time.sleep(2.0)
            text = consume_sse_assistant_streaming(client, aid, rid, emit=emit)
            print(
                f"  text_len={len(text or '')} tools={tools or 'none'}",
                flush=True,
            )
            if text:
                print(f"  snippet={text[:200]!r}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
