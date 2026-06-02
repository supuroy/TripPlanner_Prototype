#!/usr/bin/env python3
"""Create agent with MCP, then follow-up: does trail-catalog appear after first turn?"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.poc_cursor_cloud_trail_agent import trail_catalog_mcp_servers  # noqa: E402
from src.integrations.cursor_agent_client import (  # noqa: E402
    _begin_cloud_agent_run,
    basic_client,
    consume_sse_assistant_streaming,
    create_agent_run,
    get_api_key,
    load_env_from_roots,
)


def run_turn(client, aid: str | None, prompt: str) -> tuple[str, str, str]:
    tools: list[str] = []

    def emit(ev: dict) -> None:
        if ev.get("type") == "tool_call":
            tools.append(f"{ev.get('name')}:{ev.get('status')}")

    if aid:
        aid, rid = _begin_cloud_agent_run(
            client, prompt, existing_agent_id=aid, mcp_servers=trail_catalog_mcp_servers()
        )
    else:
        aid, rid = create_agent_run(client, prompt, mcp_servers=trail_catalog_mcp_servers())
    time.sleep(2.0)
    text = consume_sse_assistant_streaming(client, aid, rid, emit=emit)
    return aid, text, ",".join(tools) or "none"


def main() -> int:
    load_env_from_roots()
    with basic_client(get_api_key(), timeout_s=360.0) as client:
        print("Turn 1: hello", flush=True)
        aid, t1, tools1 = run_turn(
            client, None, 'Reply JSON only: {"hello": true}'
        )
        print(f"  text_len={len(t1)} tools={tools1}", flush=True)

        print("Turn 2: list MCP", flush=True)
        aid, t2, tools2 = run_turn(
            client,
            aid,
            "List every MCP server you can use (names only).",
        )
        print(f"  text={t2[:400]!r}", flush=True)
        print(f"  tools={tools2}", flush=True)

        print("Turn 3: call stats", flush=True)
        aid, t3, tools3 = run_turn(
            client,
            aid,
            "Call trail_catalog_stats on trail-catalog MCP. Return JSON with stats.",
        )
        print(f"  text_len={len(t3)} tools={tools3}", flush=True)
        if t3:
            print(f"  snippet={t3[:300]!r}", flush=True)
        print(f"agent={aid}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
