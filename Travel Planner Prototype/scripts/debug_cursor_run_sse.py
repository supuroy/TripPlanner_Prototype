#!/usr/bin/env python3
"""Dump every SSE event type from a Cloud Agent run (debug MCP / empty runs)."""

from __future__ import annotations

import json
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
    fetch_run_blob,
    get_api_key,
    load_env_from_roots,
)
from src.wanderly.trail_catalog.poc_cursor_agent import PROVE_MCP_PROMPT  # noqa: E402


def main() -> int:
    load_env_from_roots()
    key = get_api_key()
    events: list[dict] = []

    def emit(ev: dict) -> None:
        events.append(ev)
        t = ev.get("type")
        if t == "tool_call":
            print(f"  tool_call name={ev.get('name')} status={ev.get('status')}", flush=True)
        elif t in ("run_status", "run_result", "stream_error"):
            print(f"  {t}: {ev}", flush=True)
        elif t == "assistant_delta":
            print(f"  assistant +{len(ev.get('delta') or '')} chars", flush=True)
        elif t == "cursor_event":
            print(f"  cursor_event: {ev.get('event')} {str(ev.get('snippet') or '')[:120]}", flush=True)

    with basic_client(key, timeout_s=360.0) as client:
        aid, rid = create_agent_run(
            client, PROVE_MCP_PROMPT, mcp_servers=trail_catalog_mcp_servers()
        )
        print(f"agent={aid} run={rid}", flush=True)
        time.sleep(2.0)
        text = consume_sse_assistant_streaming(client, aid, rid, emit=emit)
        blob = fetch_run_blob(client, aid, rid)

    out = ROOT / ".cache" / "debug_cursor_sse_last.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"assistant_text_len": len(text or ""), "run": blob, "events": events},
            indent=2,
            ensure_ascii=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nWrote {out} ({len(events)} events, {len(text or '')} chars text)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
