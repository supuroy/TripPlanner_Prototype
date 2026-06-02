#!/usr/bin/env python3
"""Compare cloud agent behavior for short vs long MCP prompts."""

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
from src.wanderly.trail_catalog.poc_cursor_agent import (  # noqa: E402
    BOOTSTRAP_PROMPT,
    POC_SYSTEM_JSON,
    PROVE_MCP_PROMPT,
)

PROMPTS = {
    "short_mcp": (
        "Call trail_catalog_stats on the trail-catalog MCP server. "
        'Return JSON only: {"ok": true}'
    ),
    "prove": PROVE_MCP_PROMPT,
    "bootstrap": BOOTSTRAP_PROMPT,
    "system_json_only": POC_SYSTEM_JSON + '\n\nSay {"ok": true} only.',
}


def run_one(client, name: str, prompt: str) -> int:
    events: list[str] = []
    tool_names: list[str] = []

    def emit(ev: dict) -> None:
        events.append(str(ev.get("type")))
        if ev.get("type") == "tool_call":
            tool_names.append(f"{ev.get('name')}:{ev.get('status')}")

    aid, rid = create_agent_run(client, prompt, mcp_servers=trail_catalog_mcp_servers())
    time.sleep(2.0)
    text = consume_sse_assistant_streaming(client, aid, rid, emit=emit)
    print(
        f"  {name}: text_len={len(text or '')} tools={tool_names or 'none'} "
        f"event_types={sorted(set(events))}",
        flush=True,
    )
    if text:
        print(f"    snippet: {(text or '')[:180]!r}", flush=True)
    return 1 if tool_names or (text or "").strip() else 0


def main() -> int:
    load_env_from_roots()
    ok = 0
    with basic_client(get_api_key(), timeout_s=360.0) as client:
        for name, prompt in PROMPTS.items():
            print(f"\n=== {name} (prompt_chars={len(prompt)}) ===", flush=True)
            ok += run_one(client, name, prompt)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
