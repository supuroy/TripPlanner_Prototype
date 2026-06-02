#!/usr/bin/env python3
"""
Proof of concept: Cursor Cloud Agent + trail-catalog MCP (parks + features).

Creates a cloud agent via POST /v1/agents, attaches stdio MCP ``trail_catalog_server.py``,
and asks it to recommend trails using tools only (no catalog stuffed in the prompt).

Prerequisites:
  - CURSOR_API_KEY, CURSOR_REPO_URL in .env (catalog JSON pushed to that ref)
  - pip install -r requirements-spike.txt
  - For monorepos: set CURSOR_MCP_CWD=Travel Planner Prototype

Usage:
  cd "Travel Planner Prototype"
  python scripts/poc_cursor_cloud_trail_agent.py --dry-run
  python scripts/poc_cursor_cloud_trail_agent.py --scenario park
  python scripts/poc_cursor_cloud_trail_agent.py --scenario feature
  python scripts/poc_cursor_cloud_trail_agent.py --scenario all

See docs/POC_CURSOR_TRAIL_AGENT.md
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

from src.integrations.cursor_agent_client import (  # noqa: E402
    basic_client,
    consume_sse_assistant_streaming,
    create_agent_run,
    get_api_key,
    load_env_from_roots,
    poll_run_for_assistant_text,
)
from src.wanderly.flight_listing_llm import parse_llm_json_object
from src.wanderly.trail_catalog.search import catalog_stats
from src.wanderly.trail_catalog.store import load_feature_catalog, load_park_catalog
from src.wanderly.trail_catalog.validate import validate_trail_ids

_SYSTEM = """
You are a trail recommender for a proof of concept. Return ONLY one JSON object (no markdown):
{
  "scenario": "<scenario id>",
  "data_source": "mcp_trail_catalog",
  "selections": [
    {
      "trail_id": "<exact trail_id from MCP tool output>",
      "name": "<trail name>",
      "reason": "<one sentence>",
      "park_code": "<optional>",
      "feature_slug": "<optional>"
    }
  ]
}
Rules:
- You MUST call trail-catalog MCP tools before answering.
- trail_id values must be copied exactly from tool results; never invent ids or URLs.
- Pick 2-3 trails that best match the user request.
""".strip()

SCENARIOS: dict[str, dict[str, str]] = {
    "park": {
        "description": "Park catalog only (Yellowstone backpacking)",
        "user_message": (
            "I want 3–4 day backpacking in Yellowstone National Park with lake routes, moderate difficulty. "
            "Use park tools: resolve park_code, then search trails in that park."
        ),
        "validate": "park",
        "park_code": "yell",
        "feature_slug": "",
    },
    "feature": {
        "description": "Feature catalog only (waterfall trails)",
        "user_message": (
            "Recommend 2–3 memorable waterfall hikes in the western US, moderate difficulty, "
            "under 8 miles if possible. Use feature tools (waterfall catalog)."
        ),
        "validate": "feature",
        "park_code": "",
        "feature_slug": "waterfall",
    },
    "combined": {
        "description": "Feature catalog filtered by park (camping in Bryce)",
        "user_message": (
            "Find 2 camping-friendly trails in Bryce Canyon area using the camping feature catalog. "
            "Prefer trails with park_code brca if the tool supports filtering."
        ),
        "validate": "feature",
        "park_code": "",
        "feature_slug": "camping",
    },
}


def trail_catalog_mcp_servers() -> list[dict[str, Any]]:
    """
    Stdio MCP in Cursor cloud VM.

    Monorepo clones to ``/workspace``; app lives in ``Travel Planner Prototype/``.
    Uses bash -lc so pip+server run from the app dir (VM cwd is usually repo root).
    """
    subdir = (os.environ.get("CURSOR_MCP_CWD") or "Travel Planner Prototype").strip().strip("/")
    if subdir:
        inner = (
            f"cd '{subdir}' && python3 -m pip install -q 'mcp>=1.6.0' "
            "&& exec python3 mcp/trail_catalog_server.py"
        )
    else:
        inner = "python3 -m pip install -q 'mcp>=1.6.0' && exec python3 mcp/trail_catalog_server.py"
    return [
        {
            "name": "trail-catalog",
            "type": "stdio",
            "command": "bash",
            "args": ["-lc", inner],
            "env": {
                "PYTHONPATH": subdir or ".",
            },
        }
    ]


def _analyze_mcp_from_tool_calls(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Honest MCP outcome — avoid false pass when agent only greps Python source."""
    blob = json.dumps(tool_calls, ensure_ascii=True)
    blob_low = blob.lower()
    missing = "trail-catalog" in blob_low and "not found" in blob_low and "available servers" in blob_low

    catalog_tool_names = (
        "search_manifest_features_tool",
        "search_feature_trails_tool",
        "search_trails_tool",
        "trail_catalog_stats",
        "search_manifest_parks_tool",
        "get_feature_trail_tool",
        "get_trail_tool",
    )
    mcp_invoked = False
    for row in tool_calls:
        detail = str(row.get("detail") or "")
        if row.get("status") != "completed":
            continue
        if not any(name in detail for name in catalog_tool_names):
            continue
        # Ignore matches only inside read_file/grep of source (not an MCP result).
        if '"name":"mcp"' in detail or "mcp/trail_catalog" in detail:
            mcp_invoked = True
            break
        if '"result":{"success"' in detail and any(name in detail for name in catalog_tool_names):
            mcp_invoked = True
            break

    return {
        "mcp_server_missing": missing,
        "mcp_trail_catalog_tool_used": mcp_invoked and not missing,
        "mcp_tool_events": sum(1 for t in tool_calls if "mcp" in str(t.get("name") or "").lower()),
    }


def _build_prompt(scenario_id: str) -> tuple[str, dict[str, Any]]:
    spec = SCENARIOS[scenario_id]
    prompt = (
        _SYSTEM
        + f"\n\n---\n\nScenario: {scenario_id}\n"
        + f"Task: {spec['user_message']}\n\n"
        "MCP tools available on server ``trail-catalog``:\n"
        "- trail_catalog_stats\n"
        "- search_manifest_parks_tool, search_trails_tool, get_trail_tool\n"
        "- search_manifest_features_tool, search_feature_trails_tool, get_feature_trail_tool\n"
        "Call the tools you need, then return the JSON object."
    )
    meta = {
        "scenario": scenario_id,
        "description": spec["description"],
        "prompt_chars": len(prompt),
        "local_catalog_stats": catalog_stats(),
    }
    return prompt, meta


def _allowed_ids(spec: dict[str, str]) -> set[str]:
    if spec["validate"] == "park":
        doc = load_park_catalog(spec["park_code"])
    else:
        doc = load_feature_catalog(spec["feature_slug"])
    return {
        str(t.get("trail_id") or "").lower()
        for t in doc.get("trails") or []
        if isinstance(t, dict) and str(t.get("trail_id") or "").strip()
    }


def _parse_result(raw: str) -> tuple[dict[str, Any] | None, list[str]]:
    parsed = parse_llm_json_object(raw)
    if not parsed:
        return None, []
    sels = parsed.get("selections")
    if not isinstance(sels, list):
        return parsed, []
    ids = [str(x.get("trail_id") or "") for x in sels if isinstance(x, dict)]
    return parsed, ids


def run_scenario(
    scenario_id: str,
    *,
    model: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    spec = SCENARIOS[scenario_id]
    prompt, meta = _build_prompt(scenario_id)
    result: dict[str, Any] = {**meta}

    if dry_run:
        result["dry_run"] = True
        result["mcp_servers"] = trail_catalog_mcp_servers()
        result["allowed_id_sample"] = sorted(_allowed_ids(spec))[:5]
        print("mcpServers payload:", json.dumps(result["mcp_servers"], indent=2), flush=True)
        return result

    load_env_from_roots()
    key = get_api_key()
    tool_calls: list[dict[str, Any]] = []
    t0 = time.perf_counter()

    def emit(ev: dict[str, Any]) -> None:
        if ev.get("type") == "tool_call":
            tool_calls.append(
                {
                    "name": ev.get("name"),
                    "status": ev.get("status"),
                    "callId": ev.get("callId"),
                    "truncated": ev.get("truncated"),
                    "detail": (ev.get("detail") or "")[:1000],
                }
            )

    with basic_client(key, timeout_s=360.0) as client:
        agent_id, run_id = create_agent_run(
            client,
            prompt,
            model_id_override=model,
            mcp_servers=trail_catalog_mcp_servers(),
        )
        result["agent_id"] = agent_id
        result["run_id"] = run_id
        result["agent_url"] = f"https://cursor.com/agents?id={agent_id}"
        try:
            raw = consume_sse_assistant_streaming(client, agent_id, run_id, emit=emit)
        except Exception as exc:
            result["stream_error"] = f"{type(exc).__name__}: {exc}"
            raw = poll_run_for_assistant_text(client, agent_id, run_id, max_wait_s=180.0)

    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    result["raw_chars"] = len(raw or "")
    result["tool_calls"] = tool_calls
    result.update(_analyze_mcp_from_tool_calls(tool_calls))

    parsed, trail_ids = _parse_result(raw or "")
    allowed = _allowed_ids(spec)
    bad = validate_trail_ids(trail_ids, allowed)
    result["parse_ok"] = parsed is not None and bool(trail_ids)
    result["trail_ids"] = trail_ids
    result["trail_ids_valid"] = not bad and bool(trail_ids)
    result["invalid_trail_ids"] = bad
    if parsed:
        result["selections"] = parsed.get("selections")
    result["raw_snippet"] = (raw or "").replace("\n", " ")[:600]
    if result.get("mcp_trail_catalog_tool_used"):
        result["pass_note"] = "strict_mcp"
        result["pass"] = bool(result.get("trail_ids_valid"))
    elif result.get("trail_ids_valid"):
        result["pass_note"] = "repo_fallback"
        result["pass"] = True
    else:
        result["pass_note"] = "failed"
        result["pass"] = False
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="PoC: Cursor Cloud Agent + trail catalog MCP")
    parser.add_argument(
        "--scenario",
        choices=tuple(SCENARIOS.keys()) + ("all",),
        default="feature",
        help="Which user intent to test (default: feature)",
    )
    parser.add_argument("--model", default=None, help="Cursor model id override")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt + local stats only")
    args = parser.parse_args()

    scenarios = list(SCENARIOS.keys()) if args.scenario == "all" else [args.scenario]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / ".cache" / "poc_cursor_trail_agent" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "run_id": run_id,
        "scenarios": {},
        "repo_url": (os.environ.get("CURSOR_REPO_URL") or "").strip() if not args.dry_run else None,
        "repo_ref": (os.environ.get("CURSOR_REPO_REF") or "main").strip(),
    }

    for sid in scenarios:
        print(f"\n=== scenario={sid} ===", flush=True)
        try:
            row = run_scenario(sid, model=args.model, dry_run=args.dry_run)
        except Exception as exc:
            row = {"scenario": sid, "error": f"{type(exc).__name__}: {exc}"}
        summary["scenarios"][sid] = row
        print(json.dumps(row, indent=2, ensure_ascii=True)[:5000], flush=True)

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"\nWrote {summary_path}", flush=True)

    if not args.dry_run:
        passed = [s for s, r in summary["scenarios"].items() if r.get("pass")]
        for sid, row in summary["scenarios"].items():
            note = row.get("pass_note", "?")
            mcp_missing = row.get("mcp_server_missing")
            print(
                f"  {sid}: pass={row.get('pass')} note={note} "
                f"mcp_missing={mcp_missing} trail_ids_valid={row.get('trail_ids_valid')}",
                flush=True,
            )
        if summary["scenarios"].get(scenarios[0], {}).get("agent_url"):
            print(f"Agent: {summary['scenarios'][scenarios[0]]['agent_url']}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
