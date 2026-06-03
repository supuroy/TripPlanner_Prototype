#!/usr/bin/env python3
"""
Prove trail catalog access from a Cursor Cloud Agent.

Modes:
  diagnose-mcp  - Ask which MCP servers the agent sees (uses bound session agent by default)
  prove-repo    - Agent runs in-repo Python catalog search (works when MCP is not surfaced)
  prove-mcp     - Prove trail-catalog MCP tool calls via SSE

Examples:
  python scripts/poc_cursor_trail_agent_session.py bind-agent bc-...
  python scripts/prove_trail_catalog_cloud.py diagnose-mcp
  python scripts/prove_trail_catalog_cloud.py diagnose-mcp --new-agent
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
    DEFAULT_POC_SESSION_PATH,
    LIST_MCP_PROMPT,
    PROVE_MCP_PROMPT,
    assess_trail_catalog_mcp,
    diagnose_trail_catalog_mcp_visible,
    extract_mcp_catalog_calls,
    print_mcp_catalog_calls,
    resolve_poc_agent_id,
    session_uses_ui_mcp,
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
    attach_inline_mcp: bool,
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

    inline_mcp = trail_catalog_mcp_servers() if attach_inline_mcp else None
    if agent_id:
        aid, rid = _begin_cloud_agent_run(
            client, prompt, existing_agent_id=agent_id, mcp_servers=inline_mcp
        )
    else:
        aid, rid = create_agent_run(client, prompt, mcp_servers=inline_mcp)
    time.sleep(0.25)
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


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--session-file",
        default=str(DEFAULT_POC_SESSION_PATH),
        help="Session JSON from bind-agent (default: .cache/poc_trail_agent_session.json)",
    )
    p.add_argument("--agent-id", default=None, help="Override session agent id")
    p.add_argument(
        "--new-agent",
        action="store_true",
        help="Ignore session and create a fresh API agent",
    )


def cmd_diagnose_mcp(args: argparse.Namespace) -> int:
    load_env_from_roots()
    session_path = Path(args.session_file)
    agent_id, session, mode = resolve_poc_agent_id(
        session_path=session_path,
        cli_agent_id=args.agent_id,
        force_new=args.new_agent,
    )
    attach_inline = True
    if agent_id and not args.new_agent:
        attach_inline = False if session_uses_ui_mcp(session) else True

    with basic_client(get_api_key(), timeout_s=360.0) as client:
        if agent_id:
            print(
                f"Using bound agent ...{agent_id[-12:]} ({mode}, inline_mcp={'yes' if attach_inline else 'no'})...",
                flush=True,
            )
        else:
            print("Creating new agent (inline mcpServers=1)...", flush=True)
        turn = _collect_turn(
            client,
            agent_id=agent_id,
            prompt=LIST_MCP_PROMPT,
            attach_inline_mcp=attach_inline,
        )
    has_trail = diagnose_trail_catalog_mcp_visible(turn["raw"])[0]
    print(f"\nAgent: {turn['agent_url']}", flush=True)
    print(f"Assistant:\n{turn['raw'][:1200]}", flush=True)
    print(f"\ntrail-catalog visible in reply: {has_trail}", flush=True)
    print(f"SSE tool_call events: {len(turn['tool_calls'])}", flush=True)
    if not has_trail:
        if agent_id and not attach_inline:
            print(
                "\nFAIL: Bound agent still does not list trail-catalog.\n"
                "Open the agent in the browser and confirm Custom MCP (trail-catalog) is enabled.",
                flush=True,
            )
        else:
            print(
                "\nFAIL: Model only sees account plugins (e.g. Figma/Atlassian). "
                "Inline API mcpServers is not surfacing trail-catalog on this account/run.\n"
                "Try: bind-agent on a browser agent with Custom MCP, then re-run diagnose-mcp.",
                flush=True,
            )
        return 1
    print(
        "\nPASS: trail-catalog appears in the model's MCP list.\n"
        "This does NOT return trails. Next: poc_cursor_trail_agent_session.py ask \"...\"",
        flush=True,
    )
    return 0


def cmd_prove_repo(args: argparse.Namespace) -> int:
    load_env_from_roots()
    with basic_client(get_api_key(), timeout_s=360.0) as client:
        print("Creating agent (no inline MCP) - prove catalog via repo shell...", flush=True)
        turn = _collect_turn(
            client, agent_id=None, prompt=PROVE_REPO_PROMPT, attach_inline_mcp=False
        )
    raw = turn["raw"]
    ids = re.findall(r"trail:[a-z0-9\-:]+", raw)
    ok = bool(ids) and ("ok" in raw.lower())
    print(f"\nAgent: {turn['agent_url']}", flush=True)
    print(f"canonical_trail_ids found: {ids[:5]}", flush=True)
    print(f"Assistant snippet:\n{raw[:1500]}", flush=True)
    if ok:
        print("\nPASS: prove-repo (catalog via cloned repo + shell)", flush=True)
        return 0
    print("\nFAIL: prove-repo - no trail:... ids in response", flush=True)
    return 1


def cmd_prove_mcp(args: argparse.Namespace) -> int:
    load_env_from_roots()
    session_path = Path(args.session_file)
    agent_id, session, mode = resolve_poc_agent_id(
        session_path=session_path,
        cli_agent_id=args.agent_id,
        force_new=args.new_agent,
    )
    attach_inline = False if (agent_id and session_uses_ui_mcp(session) and not args.new_agent) else True

    with basic_client(get_api_key(), timeout_s=360.0) as client:
        if agent_id:
            print(f"Prove MCP on bound agent ...{agent_id[-12:]} ({mode})...", flush=True)
        turn = _collect_turn(
            client,
            agent_id=agent_id,
            prompt=PROVE_MCP_PROMPT,
            attach_inline_mcp=attach_inline,
        )
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
    p_diag = sub.add_parser("diagnose-mcp", help="Check if the model sees trail-catalog MCP")
    _add_common_args(p_diag)
    p_repo = sub.add_parser("prove-repo", help="Prove catalog via in-VM Python (no MCP transport)")
    _add_common_args(p_repo)
    p_mcp = sub.add_parser("prove-mcp", help="Prove trail-catalog MCP tool calls via SSE")
    _add_common_args(p_mcp)
    args = p.parse_args()
    if args.cmd == "diagnose-mcp":
        return cmd_diagnose_mcp(args)
    if args.cmd == "prove-repo":
        return cmd_prove_repo(args)
    return cmd_prove_mcp(args)


if __name__ == "__main__":
    raise SystemExit(main())
