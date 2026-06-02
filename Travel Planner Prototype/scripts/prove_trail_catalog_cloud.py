#!/usr/bin/env python3
"""
Prove trail catalog access from a Cursor Cloud Agent.

Two modes:
  diagnose-mcp  - Ask the agent which MCP servers it sees (API inline stdio check)
  prove-repo    - Agent runs in-repo Python catalog search (works when MCP is not surfaced)
  prove-mcp     - Same as poc_cursor_trail_agent_session.py prove-mcp (needs trail-catalog visible)

Examples:
  python scripts/prove_trail_catalog_cloud.py diagnose-mcp
  python scripts/prove_trail_catalog_cloud.py prove-repo
"""

from __future__ import annotations

import argparse
import json
import re
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
from src.wanderly.trail_catalog.poc_cursor_agent import (  # noqa: E402
    PROVE_MCP_PROMPT,
    assess_trail_catalog_mcp,
    extract_mcp_catalog_calls,
    print_mcp_catalog_calls,
)

LIST_MCP_PROMPT = (
    "List every MCP server name you can use for tools (short bullet list, names only)."
)

PROVE_REPO_PROMPT = """
You are on a Cloud Agent VM with this repo cloned (monorepo; app in Travel Planner Prototype/).

Prove the offline trail catalog works WITHOUT MCP — use the shell only:

cd "Travel Planner Prototype" && PYTHONPATH=. python3 -c "
from src.wanderly.trail_catalog.catalog_query import resolve_scope, search_catalog_trails
scope = resolve_scope('3 moderate waterfall hikes in Utah under 7 miles')
rows = search_catalog_trails(scope, limit=5)
print('scope', scope)
for r in rows[:3]:
    print(r.get('canonical_trail_id'), r.get('name'), r.get('length_mi'))
"

Return ONLY JSON:
{"ok": true, "canonical_trail_ids": ["trail:...", "..."], "note": "repo_shell"}
Copy canonical_trail_id values exactly from the command output.
""".strip()


def _collect_turn(
    client,
    *,
    agent_id: str | None,
    prompt: str,
    mcp: bool,
) -> dict:
    tool_calls: list[dict] = []

    def emit(ev: dict) -> None:
        if ev.get("type") != "tool_call":
            return
        tool_calls.append(
            {
                "name": ev.get("name"),
                "status": ev.get("status"),
                "callId": ev.get("callId"),
                "detail": (ev.get("detail") or "")[:80_000],
            }
        )

    mcp_servers = trail_catalog_mcp_servers() if mcp else None
    if agent_id:
        aid, rid = _begin_cloud_agent_run(
            client, prompt, existing_agent_id=agent_id, mcp_servers=mcp_servers
        )
    else:
        aid, rid = create_agent_run(client, prompt, mcp_servers=mcp_servers)
    time.sleep(2.0)
    raw = consume_sse_assistant_streaming(client, aid, rid, emit=emit)
    mcp_calls = extract_mcp_catalog_calls(tool_calls)
    return {
        "agent_id": aid,
        "run_id": rid,
        "agent_url": f"https://cursor.com/agents?id={aid}",
        "raw": raw or "",
        "tool_calls": tool_calls,
        "mcp_catalog_calls": mcp_calls,
    }


def cmd_diagnose_mcp(_: argparse.Namespace) -> int:
    load_env_from_roots()
    with basic_client(get_api_key(), timeout_s=360.0) as client:
        print("Creating agent (mcpServers=1) and asking which MCP servers are visible…", flush=True)
        turn = _collect_turn(client, agent_id=None, prompt=LIST_MCP_PROMPT, mcp=True)
    text = turn["raw"].lower()
    has_trail = "trail-catalog" in text or "trail_catalog" in text
    print(f"\nAgent: {turn['agent_url']}", flush=True)
    print(f"Assistant:\n{turn['raw'][:1200]}", flush=True)
    print(f"\ntrail-catalog visible in reply: {has_trail}", flush=True)
    print(f"SSE tool_call events: {len(turn['tool_calls'])}", flush=True)
    if not has_trail:
        print(
            "\nFAIL: Model only sees account plugins (e.g. Figma/Atlassian). "
            "Inline API mcpServers is not surfacing trail-catalog on this account/run.\n"
            "Try: enable trail-catalog in the agent UI from cloned .cursor/mcp.json, "
            "or run: python scripts/prove_trail_catalog_cloud.py prove-repo",
            flush=True,
        )
        return 1
    print("\nPASS: trail-catalog appears in the model's MCP list.", flush=True)
    return 0


def cmd_prove_repo(_: argparse.Namespace) -> int:
    load_env_from_roots()
    with basic_client(get_api_key(), timeout_s=360.0) as client:
        print("Creating agent (no inline MCP) — prove catalog via repo shell…", flush=True)
        turn = _collect_turn(client, agent_id=None, prompt=PROVE_REPO_PROMPT, mcp=False)
    raw = turn["raw"]
    ids = re.findall(r"trail:[a-z0-9\-:]+", raw)
    ok = bool(ids) and ("ok" in raw.lower())
    print(f"\nAgent: {turn['agent_url']}", flush=True)
    print(f"canonical_trail_ids found: {ids[:5]}", flush=True)
    print(f"Assistant snippet:\n{raw[:1500]}", flush=True)
    if ok:
        print("\nPASS: prove-repo (catalog via cloned repo + shell)", flush=True)
        return 0
    print("\nFAIL: prove-repo — no trail:… ids in response", flush=True)
    return 1


def cmd_prove_mcp(_: argparse.Namespace) -> int:
    load_env_from_roots()
    with basic_client(get_api_key(), timeout_s=360.0) as client:
        turn = _collect_turn(client, agent_id=None, prompt=PROVE_MCP_PROMPT, mcp=True)
    ok, note = assess_trail_catalog_mcp(turn["tool_calls"], turn["mcp_catalog_calls"])
    print(f"\n=== prove-mcp: {'PASS' if ok else 'FAIL'} ({note}) ===", flush=True)
    print_mcp_catalog_calls(turn["mcp_catalog_calls"])
    if turn["raw"]:
        print(turn["raw"][:1200], flush=True)
    print(f"Agent: {turn['agent_url']}", flush=True)
    if not ok:
        print("Run: python scripts/prove_trail_catalog_cloud.py diagnose-mcp", flush=True)
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("diagnose-mcp", help="Check if the model sees trail-catalog MCP")
    sub.add_parser("prove-repo", help="Prove catalog via in-VM Python (no MCP transport)")
    sub.add_parser("prove-mcp", help="Prove trail-catalog MCP tool calls via SSE")
    args = p.parse_args()
    if args.cmd == "diagnose-mcp":
        return cmd_diagnose_mcp(args)
    if args.cmd == "prove-repo":
        return cmd_prove_repo(args)
    return cmd_prove_mcp(args)


if __name__ == "__main__":
    raise SystemExit(main())
