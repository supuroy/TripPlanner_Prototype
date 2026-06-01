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
    _assistant_text_or_retry_sse,
    _begin_cloud_agent_run,
    assistant_text_from_run_json,
    basic_client,
    consume_sse_assistant_streaming,
    create_agent_run,
    fetch_run_blob,
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
        "  - Cloud UI MCP menu shows User plugins (Atlassian/Figma) only — API stdio servers may not appear there.\n"
        "    Read the agent transcript for get_mcp_tools / 'trail-catalog not found' / mcp tool calls.\n"
        "  - Monorepo: .cursor/mcp.json at repo root should point at Travel Planner Prototype/ (commit + push).\n",
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


def _poll_progress(label: str) -> Any:
    last_print = 0.0

    def on_poll(status: str, elapsed_s: float) -> None:
        nonlocal last_print
        if elapsed_s - last_print < 12.0 and elapsed_s > 0.5:
            return
        last_print = elapsed_s
        print(f"  [{label}] waiting {elapsed_s:.0f}s run_status={status or '?'}", flush=True)

    return on_poll


def _run_turn(
    client: Any,
    *,
    agent_id: str | None,
    prompt: str,
    model: str | None,
    mcp_on_create: bool,
    detail_limit: int,
    max_wait_s: float = 180.0,
    stream_progress: bool = True,
) -> dict[str, Any]:
    tool_calls: list[dict[str, Any]] = []
    last_stream_status = ""

    def emit(ev: dict[str, Any]) -> None:
        nonlocal last_stream_status
        if stream_progress and ev.get("type") == "run_status":
            st = str(ev.get("status") or "")
            if st and st != last_stream_status:
                last_stream_status = st
                print(f"  [stream] run_status={st}", flush=True)
        if ev.get("type") != "tool_call":
            return
        if stream_progress:
            print(
                f"  [stream] tool_call name={ev.get('name')} status={ev.get('status')}",
                flush=True,
            )
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
            mcp_servers=trail_catalog_mcp_servers(),
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

    print(f"  run_id=…{str(run_id)[-8:]} (SSE + retry; poll up to {int(max_wait_s)}s if needed)", flush=True)
    sse_text = ""
    err: str | None = None
    try:
        sse_text = consume_sse_assistant_streaming(client, aid, run_id, emit=emit)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        print(f"  SSE error: {err}", flush=True)

    print(
        f"  SSE done: {len(sse_text or '')} chars, {len(tool_calls)} tool_call events",
        flush=True,
    )

    raw = _assistant_text_or_retry_sse(client, aid, run_id, sse_text or "")

    if not (raw or "").strip() and not tool_calls:
        print("  Polling run (terminal, no text in GET run)…", flush=True)
        polled = poll_run_for_assistant_text(
            client,
            aid,
            run_id,
            max_wait_s=min(max_wait_s, 30.0),
            on_poll=_poll_progress("poll"),
        )
        if len(polled) > len(raw or ""):
            raw = polled
        blob = fetch_run_blob(client, aid, run_id)
        run_st = ""
        dur_ms = ""
        if isinstance(blob, dict):
            run_st = str(blob.get("status") or blob.get("state") or "")
            dur_ms = str(blob.get("durationMs") or "")
        print(
            f"  Poll finished: {len(raw or '')} chars, run_status={run_st or '?'}, "
            f"durationMs={dur_ms or '?'}",
            flush=True,
        )

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
            max_wait_s=args.max_wait_s,
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
            max_wait_s=args.max_wait_s,
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


def cmd_debug_run(args: argparse.Namespace) -> int:
    """Fetch GET /runs/{id} JSON (use when PoC finishes with empty raw)."""
    load_env_from_roots()
    key = get_api_key()
    path = _session_path(args.session_file)
    session = _load_session(path)
    agent_id = str(session["agent_id"])
    run_id = (args.run_id or "").strip()
    if not run_id:
        runs = session.get("runs") or []
        if not runs:
            raise SystemExit("No runs in session; pass --run-id")
        run_id = str(runs[-1].get("run_id") or "")
    if not run_id:
        raise SystemExit("Missing run_id")

    env_ref = (os.environ.get("CURSOR_REPO_REF") or "(unset, API defaults to main)").strip()
    with basic_client(key, timeout_s=60.0) as client:
        blob = fetch_run_blob(client, agent_id, run_id)
        agent_meta: dict[str, Any] | None = None
        try:
            ar = client.get(f"/v1/agents/{agent_id}")
            ar.raise_for_status()
            body = ar.json()
            agent_meta = body.get("agent") if isinstance(body, dict) else None
            if not isinstance(agent_meta, dict) and isinstance(body, dict):
                agent_meta = body
        except Exception as exc:
            agent_meta = {"_fetch_error": str(exc)}

    extracted = assistant_text_from_run_json(blob) if isinstance(blob, dict) else ""
    print("=== debug-run summary ===", flush=True)
    if isinstance(blob, dict):
        print(f"  run_id: {blob.get('id') or run_id}", flush=True)
        print(f"  status: {blob.get('status')}", flush=True)
        print(f"  durationMs: {blob.get('durationMs')} (server-side run time)", flush=True)
        git = blob.get("git")
        if isinstance(git, dict):
            branches = git.get("branches") or []
            if branches:
                print(f"  git branch on run: {branches[0]}", flush=True)
    print(f"  CURSOR_REPO_REF in .env: {env_ref}", flush=True)
    if isinstance(agent_meta, dict):
        repos = agent_meta.get("repos") or []
        if repos and isinstance(repos[0], dict):
            print(f"  agent repos[0].startingRef: {repos[0].get('startingRef')}", flush=True)
            print(f"  agent repos[0].url: {repos[0].get('url')}", flush=True)
    print(
        f"  assistant text via GET run: {len(extracted)} chars"
        + (f" (preview: {extracted[:120]!r}…)" if extracted else " (none — use UI transcript or SSE logs)"),
        flush=True,
    )
    if isinstance(blob, dict) and str(blob.get("status") or "").upper() == "FINISHED":
        dur = blob.get("durationMs")
        if isinstance(dur, (int, float)) and dur < 30_000:
            print(
                "  NOTE: FINISHED in <30s with no API text usually means the model ended immediately "
                "(MCP missing, busy 409, or empty turn). Your script may still wait on a stale SSE socket.",
                flush=True,
            )
    print("\n=== GET /runs/{id} (full) ===", flush=True)
    print(json.dumps(blob, indent=2, ensure_ascii=True, default=str)[:30_000], flush=True)
    if agent_meta is not None:
        print("\n=== GET /agents/{id} (repos) ===", flush=True)
        print(json.dumps(agent_meta, indent=2, ensure_ascii=True, default=str)[:15_000], flush=True)
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
    parser.add_argument(
        "--max-wait-s",
        type=float,
        default=300.0,
        help="Max seconds to poll GET run after SSE ends (default 300; cloud MCP can be slow)",
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

    p_dbg = sub.add_parser("debug-run", help="Print GET run JSON for last or given run")
    p_dbg.add_argument("--run-id", default=None)

    args = parser.parse_args()
    if args.command == "start":
        return cmd_start(args)
    if args.command == "ask":
        return cmd_ask(args)
    if args.command == "suite":
        return cmd_suite(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "debug-run":
        return cmd_debug_run(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
