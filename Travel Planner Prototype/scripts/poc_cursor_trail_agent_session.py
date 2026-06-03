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
    consume_run_sse_robust,
    fetch_run_blob,
    get_api_key,
    load_env_from_roots,
    poll_run_for_assistant_text,
)
from src.wanderly.trail_catalog.poc_cursor_agent import (  # noqa: E402
    BOOTSTRAP_PROMPT,
    LIST_MCP_PROMPT,
    PROVE_MCP_PROMPT,
    assess_trail_catalog_mcp,
    build_recommendation_prompt,
    classify_mcp_diagnose,
    diagnose_trail_catalog_mcp_visible,
    extract_mcp_catalog_calls,
    load_poc_session,
    normalize_agent_id,
    parse_selections,
    print_mcp_catalog_calls,
    resolve_poc_agent_id,
    session_uses_ui_mcp,
    validate_selection_trail_ids,
    format_local_preview_selections,
    local_trail_preview,
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


def _print_api_empty_run_help(*, diagnose_passed: bool = False) -> None:
    print(
        "\n  NOTE: Cloud Agent API returned no assistant text and no MCP tool events in SSE.",
        flush=True,
    )
    if diagnose_passed:
        print(
            "  diagnose-mcp PASS only means the agent lists trail-catalog - it does not run tools.\n"
            "  On many accounts, API follow-up runs can list UI Custom MCP servers but cannot\n"
            "  execute MCP tools (browser chat on the same agent usually can).\n"
            "  Open the agent URL in the browser to confirm MCP tool calls in the transcript.",
            flush=True,
        )
    else:
        print(
            "  Run diagnose-mcp first. If trail-catalog is missing, bind a browser agent with Custom MCP.",
            flush=True,
        )


def _print_local_preview(user_message: str, *, trail_count: int) -> None:
    rows = local_trail_preview(user_message, limit=trail_count)
    if not rows:
        print("\n--- Local catalog preview (offline) ---\n  (no rows)", flush=True)
        return
    sels = format_local_preview_selections(rows)
    print(
        "\n--- Local catalog preview (offline; NOT from Cloud Agent API) ---",
        flush=True,
    )
    print(json.dumps(sels, indent=2, ensure_ascii=True), flush=True)
    print(
        "  Compare with the agent transcript in the browser. "
        "For offline-only: python scripts/test_trail_search_intent_local.py \"...\"",
        flush=True,
    )


def _print_mcp_zero_call_hints(*, api_inline_failed: bool = False) -> None:
    launcher = ROOT / "scripts" / "run_trail_catalog_mcp.py"
    print(
        "\n  HINT: 0 trail-catalog MCP calls recorded in this run.",
        flush=True,
    )
    if api_inline_failed:
        print(
            "  DIAGNOSE: API inline mcpServers did NOT attach trail-catalog (model only sees account plugins).\n"
            "  Terminal scripts cannot prove MCP until trail-catalog is visible to the cloud agent.\n"
            "  Fix (pick one):\n"
            "  1. Open the agent in browser -> composer + -> Custom MCP (Command):\n"
            "       command: python3\n"
            "       args: Travel Planner Prototype/scripts/run_trail_catalog_mcp.py\n"
            "       env PYTHONPATH=Travel Planner Prototype\n"
            "  2. Or Dashboard -> Team MCP Servers -> add trail-catalog from repo .cursor/mcp.json\n"
            "  3. Bind that UI agent for follow-ups:\n"
            "       python scripts/poc_cursor_trail_agent_session.py bind-agent <agent-id>\n"
            "  4. Re-check: python scripts/prove_trail_catalog_cloud.py diagnose-mcp\n",
            flush=True,
        )
    else:
        print(
            "  Check:\n"
            f"  - Launcher exists: {launcher.is_file()}\n"
            "  - CURSOR_REPO_REF branch has data/trail_catalog + scripts/run_trail_catalog_mcp.py\n"
            "  - Run: python scripts/prove_trail_catalog_cloud.py diagnose-mcp\n"
            "  - Open agent_url transcript if SSE captured 0 tool_call events\n",
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
    attach_inline_mcp: bool = True,
    strip_reuse_prefix: bool = True,
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
                "detail": detail[: max(detail_limit, 120_000)],
            }
        )

    inline_mcp = trail_catalog_mcp_servers() if attach_inline_mcp else None
    reuse_prefix = POC_SYSTEM_PREFIX if strip_reuse_prefix else None
    t0 = time.perf_counter()
    if agent_id:
        aid, run_id = _begin_cloud_agent_run(
            client,
            prompt,
            model_id_override=model,
            existing_agent_id=agent_id,
            persistent_instruction_prefix=reuse_prefix,
            mcp_servers=inline_mcp,
        )
        created = False
    else:
        aid, run_id = create_agent_run(
            client,
            prompt,
            model_id_override=model,
            mcp_servers=inline_mcp if mcp_on_create else None,
        )
        created = True

    time.sleep(0.25)
    print(f"  run_id=...{str(run_id)[-8:]} (SSE + retry; poll up to {int(max_wait_s)}s if needed)", flush=True)
    sse_text = ""
    err: str | None = None
    try:
        sse_text = consume_run_sse_robust(
            client,
            aid,
            run_id,
            emit=emit,
            tool_detail_limit=detail_limit,
            max_wait_s=max_wait_s,
        )
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        print(f"  SSE error: {err}", flush=True)

    print(
        f"  SSE done: {len(sse_text or '')} chars, {len(tool_calls)} tool_call events",
        flush=True,
    )

    raw = sse_text or ""
    if not (raw or "").strip():
        raw = _assistant_text_or_retry_sse(client, aid, run_id, raw or "")

    if not (raw or "").strip() and not tool_calls:
        print("  Polling run (terminal, no text in GET run)...", flush=True)
        polled = poll_run_for_assistant_text(
            client,
            aid,
            run_id,
            max_wait_s=max_wait_s,
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
    mcp_ok, _ = assess_trail_catalog_mcp(turn.get("tool_calls") or [], turn.get("mcp_catalog_calls") or [])
    if not mcp_ok:
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
    attach_inline = not session_uses_ui_mcp(session)
    strip_prefix = not session_uses_ui_mcp(session)

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
        print(
            f"\nFollow-up on agent ...{agent_id[-12:]} "
            f"(inline_mcp={'yes' if attach_inline else 'no'}, full_prompt={'yes' if strip_prefix is False else 'no'})...",
            flush=True,
        )
        print(f"Request: {user_message[:200]}", flush=True)
        turn = _run_turn(
            client,
            agent_id=agent_id,
            prompt=prompt,
            model=args.model,
            mcp_on_create=False,
            attach_inline_mcp=attach_inline,
            strip_reuse_prefix=strip_prefix,
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

    mcp_ok, pass_note = assess_trail_catalog_mcp(
        turn.get("tool_calls") or [], turn.get("mcp_catalog_calls") or []
    )
    turn["mcp_proven"] = mcp_ok
    turn["pass_note"] = pass_note
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
    mcp_ok, _ = assess_trail_catalog_mcp(turn.get("tool_calls") or [], turn.get("mcp_catalog_calls") or [])
    if not mcp_ok:
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
    if not turn.get("parse_ok"):
        _print_api_empty_run_help(
            diagnose_passed=session_uses_ui_mcp(session),
        )
        if args.local_preview:
            _print_local_preview(user_message, trail_count=args.trail_count)
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


def _diagnose_mcp_on_agent(
    client: Any,
    *,
    agent_id: str | None,
    model: str | None,
    detail_limit: int,
    attach_inline_mcp: bool,
    mode_label: str,
    max_wait_s: float = 90.0,
    retries: int = 3,
) -> tuple[str, bool | None, str]:
    """Ask an agent which MCP servers it sees. Returns (status, visible, agent_id)."""
    if agent_id:
        print(
            f"  [diagnose] Follow-up on agent ...{agent_id[-12:]} ({mode_label}, "
            f"inline_mcp={'yes' if attach_inline_mcp else 'no'})...",
            flush=True,
        )
    else:
        print(
            f"  [diagnose] Creating agent to list visible MCP servers "
            f"(inline_mcp={'yes' if attach_inline_mcp else 'no'})...",
            flush=True,
        )

    best_status = "empty"
    best_visible: bool | None = None
    best_raw = ""
    used_agent = str(agent_id or "")

    for attempt in range(1, max(1, retries) + 1):
        if attempt > 1:
            print(f"  [diagnose] Retry {attempt}/{retries} (API returned empty)...", flush=True)
            time.sleep(min(3.0 * attempt, 10.0))
        turn = _run_turn(
            client,
            agent_id=agent_id,
            prompt=LIST_MCP_PROMPT,
            model=model,
            mcp_on_create=attach_inline_mcp and agent_id is None,
            attach_inline_mcp=attach_inline_mcp,
            strip_reuse_prefix=False,
            detail_limit=detail_limit,
            max_wait_s=max_wait_s,
            stream_progress=attempt == 1,
        )
        used_agent = str(turn.get("agent_id") or used_agent)
        raw = str(turn.get("raw") or "")
        status, visible = classify_mcp_diagnose(raw)
        if raw.strip():
            best_status, best_visible, best_raw = status, visible, raw
            break
        best_status, best_visible, best_raw = status, visible, raw

    snippet = best_raw.strip()[:400]
    if snippet:
        print(f"  [diagnose] Assistant: {snippet}", flush=True)
    else:
        print("  [diagnose] Assistant: (empty - API did not stream text)", flush=True)
    if best_visible is None:
        print("  [diagnose] trail-catalog visible: unknown (empty API response)", flush=True)
    else:
        print(f"  [diagnose] trail-catalog visible: {best_visible}", flush=True)
    return best_status, best_visible, used_agent


def cmd_diagnose_mcp(args: argparse.Namespace) -> int:
    load_env_from_roots()
    key = get_api_key()
    path = _session_path(args.session_file)
    agent_id, session, mode = resolve_poc_agent_id(
        session_path=path,
        cli_agent_id=args.agent_id,
        force_new=args.new_agent,
    )
    attach_inline = True
    if agent_id and not args.new_agent:
        attach_inline = False if session_uses_ui_mcp(session) else not args.no_inline_mcp
    elif args.no_inline_mcp:
        attach_inline = False

    with basic_client(key, timeout_s=360.0) as client:
        status, visible, used_agent = _diagnose_mcp_on_agent(
            client,
            agent_id=agent_id,
            model=args.model,
            detail_limit=args.detail_limit,
            attach_inline_mcp=attach_inline,
            mode_label=mode,
            max_wait_s=args.max_wait_s,
            retries=args.retries,
        )

    print(f"\nAgent: https://cursor.com/agents?id={used_agent}", flush=True)
    if status == "visible":
        print(
            "\nPASS: trail-catalog appears in the model's MCP list.\n"
            "This does NOT return trails - it only checks MCP visibility.\n"
            "Next: python scripts/poc_cursor_trail_agent_session.py ask \"...\"\n"
            "If ask is empty, run MCP in the browser agent UI (API may not execute Custom MCP tools).",
            flush=True,
        )
        return 0
    if status == "empty":
        print(
            "\nINCONCLUSIVE: Cloud Agent API returned no assistant text after retries.\n"
            "This is NOT proof that trail-catalog is missing - the API stream often drops text.\n"
            "Open the agent URL in the browser and ask: \"List every MCP server you can use for tools.\"\n"
            "If the browser lists trail-catalog, your bind is fine; use browser chat for MCP tool calls.",
            flush=True,
        )
        return 2
    if agent_id and not attach_inline:
        print(
            "\nFAIL: Bound agent replied but did not list trail-catalog.\n"
            "Open the agent URL in the browser and confirm Custom MCP (trail-catalog) is enabled.",
            flush=True,
        )
    else:
        print(
            "\nFAIL: Inline API mcpServers is not attaching trail-catalog (Figma/Atlassian only).\n"
            "Use bind-agent on a browser agent with Custom MCP, then re-run diagnose-mcp.",
            flush=True,
        )
    return 1


def cmd_bind_agent(args: argparse.Namespace) -> int:
    """Save a browser-configured Cloud Agent id (Custom MCP enabled in UI) for session follow-ups."""
    load_env_from_roots()
    path = _session_path(args.session_file)
    agent_id = normalize_agent_id(args.agent_id)
    session = {
        "agent_id": agent_id,
        "agent_url": f"https://cursor.com/agents?id={agent_id}",
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "bind-agent-ui",
        "attach_inline_mcp": False,
        "catalog_stats": catalog_stats(),
        "runs": [],
    }
    _save_session(path, session)
    print(f"Bound session -> {path}", flush=True)
    print(f"Agent: {session['agent_url']}", flush=True)
    print(
        "Ensure trail-catalog Custom MCP is enabled on this agent in the browser, then:\n"
        "  python scripts/poc_cursor_trail_agent_session.py diagnose-mcp   # lists MCP servers (not trails)\n"
        "  python scripts/poc_cursor_trail_agent_session.py ask \"3 moderate waterfall hikes in Utah under 7 miles\"",
        flush=True,
    )
    return 0


def cmd_prove_mcp(args: argparse.Namespace) -> int:
    """Single-turn cloud agent created only to prove trail-catalog MCP attaches."""
    load_env_from_roots()
    key = get_api_key()
    path = _session_path(args.session_file)

    agent_id, session, mode = resolve_poc_agent_id(
        session_path=path,
        cli_agent_id=args.agent_id,
        force_new=args.new_agent and not args.agent_id,
    )
    attach_inline = True
    if agent_id and session_uses_ui_mcp(session) and not args.new_agent:
        attach_inline = False

    with basic_client(key, timeout_s=360.0) as client:
        if not args.skip_diagnose:
            status, visible, _ = _diagnose_mcp_on_agent(
                client,
                agent_id=agent_id,
                model=args.model,
                detail_limit=args.detail_limit,
                attach_inline_mcp=attach_inline if agent_id else True,
                mode_label=mode,
                max_wait_s=min(args.max_wait_s, 90.0),
                retries=2,
            )
            if status == "missing" or (status == "empty" and agent_id is None):
                print("\n=== PROVE MCP: BLOCKED (trail_catalog_not_visible) ===", flush=True)
                if status == "empty" and agent_id:
                    print(
                        "Diagnose inconclusive (empty API). Try browser or re-run diagnose-mcp.",
                        flush=True,
                    )
                elif agent_id and not attach_inline:
                    print(
                        "Bound agent does not list trail-catalog. Enable Custom MCP in the browser first.",
                        flush=True,
                    )
                else:
                    print(
                        "Inline POST mcpServers is not attaching trail-catalog on this account.\n"
                        "Configure Custom MCP on a Cloud Agent in the browser, then bind-agent.",
                        flush=True,
                    )
                _print_mcp_zero_call_hints(api_inline_failed=attach_inline or agent_id is None)
                return 1

        if agent_id:
            print(f"Prove MCP on agent ...{agent_id[-12:]} ({mode})...", flush=True)
        else:
            print("Creating Cloud Agent (prove trail-catalog MCP only)...", flush=True)
        turn = _run_turn(
            client,
            agent_id=agent_id,
            prompt=PROVE_MCP_PROMPT,
            model=args.model,
            mcp_on_create=agent_id is None,
            attach_inline_mcp=attach_inline if agent_id else True,
            detail_limit=args.detail_limit,
            max_wait_s=args.max_wait_s,
        )

    mcp_ok, pass_note = assess_trail_catalog_mcp(
        turn.get("tool_calls") or [], turn.get("mcp_catalog_calls") or []
    )
    session = {
        "agent_id": turn["agent_id"],
        "agent_url": turn["agent_url"],
        "prove_mcp": mcp_ok,
        "pass_note": pass_note,
        "runs": [{"kind": "prove_mcp", **{k: turn[k] for k in turn if k not in ("raw", "tool_calls")}}],
    }
    _save_session(path, session)

    print(f"\n=== PROVE MCP: {'PASS' if mcp_ok else 'FAIL'} ({pass_note}) ===", flush=True)
    print(f"tool_call events: {len(turn.get('tool_calls') or [])}", flush=True)
    _print_tool_call_summary(turn.get("tool_calls") or [])
    print_mcp_catalog_calls(turn["mcp_catalog_calls"])
    if not mcp_ok:
        _print_mcp_zero_call_hints(api_inline_failed=pass_note in ("no_mcp_calls", "mcp_not_found"))
    if turn.get("raw"):
        print(f"\nAssistant:\n{turn['raw'][:1200]}", flush=True)
    print(f"\nAgent: {turn['agent_url']}", flush=True)
    return 0 if mcp_ok else 1


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

    p_prove = sub.add_parser("prove-mcp", help="One-shot: prove trail-catalog MCP on a new cloud agent")
    p_prove.add_argument("--max-wait-s", type=float, default=300.0)
    p_prove.add_argument(
        "--skip-diagnose",
        action="store_true",
        help="Skip pre-check that API inline mcpServers lists trail-catalog",
    )
    p_prove.add_argument(
        "--agent-id",
        default=None,
        help="Reuse an existing agent (e.g. UI agent with Custom MCP enabled)",
    )

    p_prove.add_argument(
        "--new-agent",
        action="store_true",
        help="Ignore session and create a fresh API agent (tests inline mcpServers)",
    )

    p_diag = sub.add_parser("diagnose-mcp", help="List MCP servers visible to session or bound agent")
    p_diag.add_argument("--agent-id", default=None, help="Override session agent id")
    p_diag.add_argument("--new-agent", action="store_true", help="Force new API agent (inline mcpServers test)")
    p_diag.add_argument(
        "--no-inline-mcp",
        action="store_true",
        help="Do not pass inline mcpServers even on a new agent",
    )
    p_diag.add_argument("--max-wait-s", type=float, default=90.0)
    p_diag.add_argument("--retries", type=int, default=3, help="Retries when API returns empty text")

    p_bind = sub.add_parser("bind-agent", help="Save a browser Cloud Agent id for session ask/follow-ups")
    p_bind.add_argument("agent_id", help="bc-… id or full https://cursor.com/agents?id=… URL")

    p_ask = sub.add_parser("ask", help="Follow-up question on existing agent")
    p_ask.add_argument("message", nargs="?", default=None)
    p_ask.add_argument("--scenario", choices=tuple(SCENARIOS.keys()), default=None)
    p_ask.add_argument("--trail-count", type=int, default=3)
    p_ask.add_argument(
        "--local-preview",
        action="store_true",
        help="If API returns no trails, print offline catalog matches for comparison",
    )

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
    if args.command == "prove-mcp":
        return cmd_prove_mcp(args)
    if args.command == "diagnose-mcp":
        return cmd_diagnose_mcp(args)
    if args.command == "bind-agent":
        return cmd_bind_agent(args)
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
