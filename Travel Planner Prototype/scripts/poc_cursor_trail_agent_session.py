#!/usr/bin/env python3
"""
Persistent Cursor Cloud Agent session for trail-catalog MCP PoC.

Creates one agent (with MCP), reuses it for follow-up questions, and logs every
trail-catalog MCP tool name + arguments + result summary.

Usage:
  cd "Travel Planner Prototype"

  # Create agent + confirm MCP (saves agent id to .cache)
  python scripts/poc_cursor_trail_agent_session.py start

  # Ask custom question (3 trails by default)
  python scripts/poc_cursor_trail_agent_session.py ask \\
    "3 moderate waterfall hikes in Utah or nearby, under 7 miles"

  # Predefined scenario on same agent
  python scripts/poc_cursor_trail_agent_session.py ask --scenario park
  python scripts/poc_cursor_trail_agent_session.py ask --scenario feature
  python scripts/poc_cursor_trail_agent_session.py ask --scenario combined

  # Run several scenarios on one agent
  python scripts/poc_cursor_trail_agent_session.py suite --scenarios park feature combined

  python scripts/poc_cursor_trail_agent_session.py status
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_SESSION_PATH = ROOT / ".cache" / "poc_trail_agent_session.json"

def _load_poc_single_shot():
    import importlib.util

    path = ROOT / "scripts" / "poc_cursor_cloud_trail_agent.py"
    spec = importlib.util.spec_from_file_location("poc_cursor_cloud_trail_agent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_poc = _load_poc_single_shot()
SCENARIOS = _poc.SCENARIOS
trail_catalog_mcp_servers = _poc.trail_catalog_mcp_servers
from src.integrations.cursor_agent_client import (  # noqa: E402
    _begin_cloud_agent_run,
    basic_client,
    consume_sse_assistant_streaming,
    create_agent_run,
    get_api_key,
    load_env_from_roots,
    poll_run_for_assistant_text,
)
from src.wanderly.trail_catalog.poc_cursor_agent import (  # noqa: E402
    BOOTSTRAP_PROMPT,
    build_recommendation_prompt,
    extract_mcp_catalog_calls,
    parse_selections,
    print_mcp_catalog_calls,
    validate_selection_trail_ids,
)
from src.wanderly.trail_catalog.search import catalog_stats
from src.wanderly.trail_catalog.store import load_feature_catalog, load_park_catalog

POC_SYSTEM_PREFIX = BOOTSTRAP_PROMPT.split("\n\n---\n\n")[0]


def _print_tool_call_summary(tool_calls: list[dict[str, Any]]) -> None:
    if not tool_calls:
        print("  (no tool_call SSE events captured)", flush=True)
        return
    counts: dict[str, int] = {}
    for row in tool_calls:
        key = f"{row.get('name')}:{row.get('status')}"
        counts[key] = counts.get(key, 0) + 1
    parts = [f"{k}={v}" for k, v in sorted(counts.items())]
    print(f"  tool_call events: {', '.join(parts[:12])}", flush=True)


def _print_mcp_zero_call_hints() -> None:
    launcher = ROOT / "scripts" / "run_trail_catalog_mcp_stdio.sh"
    print(
        "\n  HINT: 0 trail-catalog MCP calls. Check:\n"
        "  - scripts/run_trail_catalog_mcp_stdio.sh is committed and pushed (required in cloud VM)\n"
        "  - CURSOR_REPO_REF points at branch with data/trail_catalog + mcp/trail_catalog_server.py\n"
        f"  - Launcher exists locally: {launcher.is_file()}\n"
        "  - Open agent_url in Cursor UI and confirm trail-catalog server is ready (not only Atlassian/Figma)\n",
        flush=True,
    )


def _session_path(path: str | None) -> Path:
    return Path(path) if path else DEFAULT_SESSION_PATH


def _load_session(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(
            f"No session file at {path}. Run: python scripts/poc_cursor_trail_agent_session.py start"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _save_session(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _allowed_ids_for_spec(spec: dict[str, str]) -> set[str]:
    if spec.get("validate") == "park":
        doc = load_park_catalog(spec["park_code"])
    else:
        doc = load_feature_catalog(spec["feature_slug"])
    return {
        str(t.get("trail_id") or "").lower()
        for t in doc.get("trails") or []
        if isinstance(t, dict) and str(t.get("trail_id") or "").strip()
    }


def _run_turn(
    client: Any,
    *,
    agent_id: str | None,
    prompt: str,
    model: str | None,
    mcp_on_create: bool,
    detail_limit: int,
) -> dict[str, Any]:
    tool_calls: list[dict[str, Any]] = []

    def emit(ev: dict[str, Any]) -> None:
        if ev.get("type") != "tool_call":
            return
        detail = str(ev.get("detail") or "")
        tool_calls.append(
            {
                "name": ev.get("name"),
                "status": ev.get("status"),
                "callId": ev.get("callId"),
                "truncated": ev.get("truncated"),
                "detail": detail[:detail_limit],
            }
        )

    t0 = time.perf_counter()
    if agent_id:
        aid, run_id = _begin_cloud_agent_run(
            client,
            prompt,
            model_id_override=model,
            existing_agent_id=agent_id,
            persistent_instruction_prefix=POC_SYSTEM_PREFIX,
        )
        created = False
    else:
        aid, run_id = create_agent_run(
            client,
            prompt,
            model_id_override=model,
            mcp_servers=trail_catalog_mcp_servers() if mcp_on_create else None,
        )
        created = True

    try:
        raw = consume_sse_assistant_streaming(client, aid, run_id, emit=emit)
    except Exception as exc:
        raw = ""
        err = f"{type(exc).__name__}: {exc}"
    else:
        err = None

    # SSE often ends before the run finishes; poll when text/tools are missing.
    if not (raw or "").strip() or not tool_calls:
        polled = poll_run_for_assistant_text(client, aid, run_id, max_wait_s=180.0)
        if len(polled) > len(raw or ""):
            raw = polled

    mcp_calls = extract_mcp_catalog_calls(tool_calls)
    parsed, trail_ids, tools_claimed = parse_selections(raw or "")

    return {
        "agent_id": aid,
        "run_id": run_id,
        "agent_url": f"https://cursor.com/agents?id={aid}",
        "created_new_agent": created,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        "stream_error": err,
        "raw": raw or "",
        "tool_calls": tool_calls,
        "mcp_catalog_calls": mcp_calls,
        "mcp_call_count": len(mcp_calls),
        "tools_claimed_in_json": tools_claimed,
        "trail_ids": trail_ids,
        "selections": parsed.get("selections") if parsed else None,
        "parse_ok": parsed is not None and bool(trail_ids),
    }


def cmd_start(args: argparse.Namespace) -> int:
    load_env_from_roots()
    key = get_api_key()
    path = _session_path(args.session_file)

    if path.is_file() and not args.force_new:
        old = _load_session(path)
        print(f"Session already exists: {path}", flush=True)
        print(f"  agent_id: {old.get('agent_id')}", flush=True)
        print("  Use --force-new or `ask` on this agent. `status` shows details.", flush=True)
        return 0

    with basic_client(key, timeout_s=360.0) as client:
        print("Starting new Cloud Agent with trail-catalog MCP...", flush=True)
        turn = _run_turn(
            client,
            agent_id=None,
            prompt=BOOTSTRAP_PROMPT,
            model=args.model,
            mcp_on_create=True,
            detail_limit=args.detail_limit,
        )

    session = {
        "agent_id": turn["agent_id"],
        "agent_url": turn["agent_url"],
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "catalog_stats": catalog_stats(),
        "runs": [{"kind": "bootstrap", **{k: turn[k] for k in turn if k != "raw"}}],
    }
    _save_session(path, session)

    print(f"\nSession saved -> {path}", flush=True)
    print(f"Agent: {turn['agent_url']}", flush=True)
    print(f"Bootstrap MCP calls: {turn['mcp_call_count']}", flush=True)
    _print_tool_call_summary(turn.get("tool_calls") or [])
    print_mcp_catalog_calls(turn["mcp_catalog_calls"])
    if turn["mcp_call_count"] == 0:
        _print_mcp_zero_call_hints()
    if turn.get("raw"):
        print(f"\nAssistant (bootstrap):\n{turn['raw'][:800]}", flush=True)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    load_env_from_roots()
    key = get_api_key()
    path = _session_path(args.session_file)
    session = _load_session(path)
    agent_id = str(session["agent_id"])

    if args.scenario:
        spec = SCENARIOS[args.scenario]
        user_message = spec["user_message"]
        label = args.scenario
        allowed = _allowed_ids_for_spec(spec)
    else:
        user_message = args.message or ""
        if not user_message.strip():
            raise SystemExit("Provide a message or --scenario")
        label = "custom"
        allowed = None

    prompt = build_recommendation_prompt(
        user_message=user_message,
        scenario_label=label,
        trail_count=args.trail_count,
    )

    with basic_client(key, timeout_s=360.0) as client:
        print(f"\nFollow-up on agent {agent_id[-12:]}...", flush=True)
        print(f"Request: {user_message[:200]}", flush=True)
        turn = _run_turn(
            client,
            agent_id=agent_id,
            prompt=prompt,
            model=args.model,
            mcp_on_create=False,
            detail_limit=args.detail_limit,
        )

    invalid: list[str] = []
    if allowed is not None:
        invalid = validate_selection_trail_ids(turn["trail_ids"], allowed=allowed)
        turn["trail_ids_valid"] = not invalid and bool(turn["trail_ids"])
        turn["invalid_trail_ids"] = invalid
    else:
        turn["trail_ids_valid"] = turn["parse_ok"]

    turn["pass_note"] = "strict_mcp" if turn["mcp_call_count"] > 0 else "no_mcp_calls"
    turn["scenario"] = label

    runs = session.get("runs")
    if not isinstance(runs, list):
        runs = []
    runs.append({"kind": "ask", **{k: turn[k] for k in turn if k not in ("raw", "tool_calls")}})
    session["runs"] = runs
    session["agent_id"] = turn["agent_id"]
    _save_session(path, session)

    log_dir = ROOT / ".cache" / "poc_trail_agent_session" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "turn.json").write_text(
        json.dumps(turn, indent=2, ensure_ascii=True, default=str) + "\n",
        encoding="utf-8",
    )
    (log_dir / "mcp_calls.json").write_text(
        json.dumps(turn["mcp_catalog_calls"], indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    (log_dir / "tool_calls_raw.json").write_text(
        json.dumps(turn["tool_calls"], indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    if turn.get("raw"):
        (log_dir / "assistant.txt").write_text(turn["raw"], encoding="utf-8")

    print(f"\n--- MCP queries ({turn['mcp_call_count']}) ---", flush=True)
    _print_tool_call_summary(turn.get("tool_calls") or [])
    print_mcp_catalog_calls(turn["mcp_catalog_calls"])
    if turn["mcp_call_count"] == 0:
        _print_mcp_zero_call_hints()
    print(f"\n--- Selections ---", flush=True)
    print(json.dumps(turn.get("selections") or [], indent=2, ensure_ascii=True), flush=True)
    print(
        f"\nvalid={turn.get('trail_ids_valid')} pass_note={turn.get('pass_note')} "
        f"claimed_tools={turn.get('tools_claimed_in_json')}",
        flush=True,
    )
    print(f"Log dir: {log_dir}", flush=True)
    print(f"Agent: {turn['agent_url']}", flush=True)
    return 0


def cmd_suite(args: argparse.Namespace) -> int:
    scenarios = args.scenarios or list(SCENARIOS.keys())
    for sid in scenarios:
        if sid not in SCENARIOS:
            raise SystemExit(f"Unknown scenario: {sid}")
    for sid in scenarios:
        print(f"\n{'=' * 60}\nSUITE scenario={sid}\n{'=' * 60}", flush=True)
        ns = argparse.Namespace(**{**vars(args), "scenario": sid, "message": None})
        cmd_ask(ns)
        if args.pause_s > 0:
            time.sleep(args.pause_s)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    path = _session_path(args.session_file)
    if not path.is_file():
        print(f"No session at {path}", flush=True)
        return 1
    session = _load_session(path)
    print(json.dumps(session, indent=2, ensure_ascii=True)[:8000], flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent trail-catalog MCP agent PoC")
    parser.add_argument("--session-file", default=str(DEFAULT_SESSION_PATH))
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--detail-limit",
        type=int,
        default=50_000,
        help="Max chars per tool_call detail in logs (raise if MCP args truncated)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Create agent + MCP bootstrap")
    p_start.add_argument("--force-new", action="store_true")

    p_ask = sub.add_parser("ask", help="Follow-up question on existing agent")
    p_ask.add_argument("message", nargs="?", default=None)
    p_ask.add_argument("--scenario", choices=tuple(SCENARIOS.keys()), default=None)
    p_ask.add_argument("--trail-count", type=int, default=3)

    p_suite = sub.add_parser("suite", help="Run multiple scenarios on one agent")
    p_suite.add_argument("--scenarios", nargs="+", default=None)
    p_suite.add_argument("--trail-count", type=int, default=3)
    p_suite.add_argument("--pause-s", type=float, default=3.0)

    sub.add_parser("status", help="Show saved session JSON")

    args = parser.parse_args()
    if args.command == "start":
        return cmd_start(args)
    if args.command == "ask":
        return cmd_ask(args)
    if args.command == "suite":
        return cmd_suite(args)
    if args.command == "status":
        return cmd_status(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
