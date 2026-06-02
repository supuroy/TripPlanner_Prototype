#!/usr/bin/env python3
"""Minimal cloud agent ping (no MCP) to see if the model responds via SSE."""

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


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--with-mcp", action="store_true")
    args = p.parse_args()
    load_env_from_roots()
    events: list[str] = []

    def emit(ev: dict) -> None:
        events.append(str(ev.get("type")))
        if ev.get("type") == "assistant_delta":
            print(f"  delta +{len(ev.get('delta') or '')}", flush=True)
        if ev.get("type") == "run_result":
            print(f"  run_result {ev}", flush=True)

    with basic_client(get_api_key(), timeout_s=360.0) as client:
        mcp = trail_catalog_mcp_servers() if args.with_mcp else None
        aid, rid = create_agent_run(
            client,
            'Reply with only this JSON, no markdown: {"hello": true}',
            mcp_servers=mcp,
        )
        print(f"mcp_servers={len(mcp) if mcp else 0}", flush=True)
        print(f"agent={aid}", flush=True)
        time.sleep(2.0)
        text = consume_sse_assistant_streaming(client, aid, rid, emit=emit)

    print(f"text={text!r}", flush=True)
    print(f"event_types={events}", flush=True)
    return 0 if (text or "").strip() else 1


if __name__ == "__main__":
    raise SystemExit(main())
