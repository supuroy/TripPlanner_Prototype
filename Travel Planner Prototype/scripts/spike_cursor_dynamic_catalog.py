#!/usr/bin/env python3
"""
Compare catalog access strategies for Cursor Cloud Agents:

  inline     - trails embedded in prompt (today's pattern; measures size)
  repo       - minimal prompt; agent reads files from CURSOR_REPO_URL clone
  mcp_stdio  - minimal prompt; agent uses MCP trail-catalog tools (stdio in cloud VM)

Results: .cache/spike_cursor_dynamic/<run_id>/summary.json

See data/trail_catalog/SPIKE_CURSOR_DYNAMIC.md
"""

from __future__ import annotations

import argparse
import json
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
from src.wanderly.trail_catalog.search import search_trails
from src.wanderly.trail_catalog.store import load_park_catalog, to_compact_row
from src.wanderly.trail_catalog.validate import validate_trail_ids

_SELECT_SYSTEM = """
You are a trail recommender. Return ONLY one JSON object (no markdown):
{
  "park_code": "<park code>",
  "selections": [
    {"trail_id": "<exact trail_id>", "reason": "<one sentence>"}
  ]
}
Pick 2-3 trails that best match the user request. trail_id must exist in the data source you used.
""".strip()


def _mcp_stdio_servers() -> list[dict[str, Any]]:
    """Stdio MCP runs inside Cursor's cloud VM at repo root after clone."""
    import os

    from scripts.poc_cursor_cloud_trail_agent import trail_catalog_mcp_servers

    return trail_catalog_mcp_servers()


def _prompt_inline(park_code: str, *, inline_limit: int, user_query: str) -> tuple[str, dict[str, Any]]:
    doc = load_park_catalog(park_code)
    compact = [to_compact_row(t) for t in doc.get("trails") or [] if isinstance(t, dict)]
    subset = compact[: max(1, inline_limit)]
    payload = {
        "park_code": park_code,
        "user_message": user_query,
        "park_trails": subset,
        "note": f"INLINE MODE: {len(subset)} of {len(compact)} trails included in prompt",
    }
    prompt = _SELECT_SYSTEM + "\n\n---\n\n" + json.dumps(payload, ensure_ascii=True, indent=2)
    meta = {
        "mode": "inline",
        "prompt_chars": len(prompt),
        "trails_in_prompt": len(subset),
        "trails_in_catalog": len(compact),
    }
    return prompt, meta


def _prompt_repo_only(park_code: str, user_query: str) -> tuple[str, dict[str, Any]]:
    prompt = (
        _SELECT_SYSTEM
        + f"\n\n---\n\nUser request: {user_query}\n"
        f"Park code hint: {park_code}\n\n"
        "REPO MODE: Do NOT guess trails. Read the catalog from this repository:\n"
        f"- Manifest: data/trail_catalog/manifest.json\n"
        f"- Park trails: data/trail_catalog/parks/{park_code}.json\n"
        "Use file read/search tools to load trails, filter for backpacking, then return JSON selections "
        "with trail_id values that appear in that park file."
    )
    meta = {"mode": "repo", "prompt_chars": len(prompt)}
    return prompt, meta


def _prompt_mcp(park_code: str, user_query: str) -> tuple[str, dict[str, Any]]:
    prompt = (
        _SELECT_SYSTEM
        + f"\n\n---\n\nUser request: {user_query}\n"
        f"Suggested park_code: {park_code}\n\n"
        "MCP MODE: You MUST use the trail-catalog MCP server tools:\n"
        "1. search_manifest_parks_tool (if needed) to confirm park_code\n"
        "2. search_trails_tool(park_code, query, limit) to list candidates\n"
        "3. Return JSON selections using trail_id values ONLY from tool results\n"
        "Do not invent trail ids or URLs."
    )
    meta = {"mode": "mcp_stdio", "prompt_chars": len(prompt)}
    return prompt, meta


def _parse_selections(raw: str) -> tuple[dict[str, Any] | None, list[str]]:
    parsed = parse_llm_json_object(raw)
    if not parsed:
        return None, []
    sels = parsed.get("selections")
    if not isinstance(sels, list):
        return parsed, []
    ids = [str(x.get("trail_id") or "") for x in sels if isinstance(x, dict)]
    return parsed, ids


def _run_mode(
    mode: str,
    *,
    park_code: str,
    user_query: str,
    inline_limit: int,
    model: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    if mode == "inline":
        prompt, meta = _prompt_inline(park_code, inline_limit=inline_limit, user_query=user_query)
        mcp_servers = None
    elif mode == "repo":
        prompt, meta = _prompt_repo_only(park_code, user_query)
        mcp_servers = None
    elif mode == "mcp_stdio":
        prompt, meta = _prompt_mcp(park_code, user_query)
        mcp_servers = _mcp_stdio_servers()
    else:
        raise ValueError(f"unknown mode {mode!r}")

    result: dict[str, Any] = {**meta, "park_code": park_code, "user_query": user_query}

    if dry_run:
        result["dry_run"] = True
        if mode == "inline":
            allowed = {str(t.get("trail_id") or "").lower() for t in search_trails(park_code, limit=inline_limit)}
            result["sample_allowed_ids"] = sorted(allowed)[:5]
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
                    "detail": (ev.get("detail") or "")[:800],
                }
            )

    with basic_client(key, timeout_s=300.0) as client:
        agent_id, run_id = create_agent_run(
            client,
            prompt,
            model_id_override=model,
            mcp_servers=mcp_servers,
        )
        result["agent_id_tail"] = agent_id[-12:]
        result["run_id_tail"] = run_id[-12:]
        try:
            raw = consume_sse_assistant_streaming(client, agent_id, run_id, emit=emit)
        except Exception as exc:
            result["stream_error"] = f"{type(exc).__name__}: {exc}"
            raw = poll_run_for_assistant_text(client, agent_id, run_id, max_wait_s=120.0)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    result["elapsed_ms"] = round(elapsed_ms, 1)
    result["raw_chars"] = len(raw or "")
    result["tool_call_events"] = len(tool_calls)
    result["tool_calls"] = tool_calls[:20]

    parsed, trail_ids = _parse_selections(raw or "")
    result["parse_ok"] = parsed is not None and bool(trail_ids)
    result["trail_ids"] = trail_ids

    # Validate against full park catalog (inline prompt is not backpacking-prefiltered).
    doc = load_park_catalog(park_code)
    allowed = {
        str(t.get("trail_id") or "").lower()
        for t in doc.get("trails") or []
        if isinstance(t, dict) and str(t.get("trail_id") or "").strip()
    }
    bad = validate_trail_ids(trail_ids, allowed)
    result["trail_ids_valid"] = not bad and bool(trail_ids)
    result["invalid_trail_ids"] = bad
    if parsed:
        result["selections"] = parsed.get("selections")
    result["raw_snippet"] = (raw or "").replace("\n", " ")[:500]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike: Cursor dynamic catalog access")
    parser.add_argument("--park", default="yell", help="Park code (e.g. yell, yose)")
    parser.add_argument(
        "--query",
        default="4 days backpacking in Yellowstone, moderate trails, prefer lake routes",
    )
    parser.add_argument(
        "--mode",
        choices=("inline", "repo", "mcp_stdio", "all"),
        default="mcp_stdio",
    )
    parser.add_argument("--inline-limit", type=int, default=40, help="Trails embedded for inline mode")
    parser.add_argument("--model", default=None, help="Cursor model id override")
    parser.add_argument("--dry-run", action="store_true", help="Build prompts only, no API")
    args = parser.parse_args()

    modes = ["inline", "repo", "mcp_stdio"] if args.mode == "all" else [args.mode]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / ".cache" / "spike_cursor_dynamic" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "run_id": run_id,
        "park_code": args.park,
        "query": args.query,
        "modes": {},
    }

    for mode in modes:
        print(f"\n=== mode={mode} ===", flush=True)
        try:
            row = _run_mode(
                mode,
                park_code=args.park.strip().lower(),
                user_query=args.query,
                inline_limit=args.inline_limit,
                model=args.model,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            row = {"mode": mode, "error": f"{type(exc).__name__}: {exc}"}
        summary["modes"][mode] = row
        print(json.dumps(row, indent=2, ensure_ascii=True)[:4000], flush=True)

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"\nWrote {summary_path}", flush=True)

    # Quick recommendation line
    inline_chars = summary["modes"].get("inline", {}).get("prompt_chars")
    mcp = summary["modes"].get("mcp_stdio", {})
    if inline_chars and mcp.get("prompt_chars"):
        ratio = inline_chars / max(mcp["prompt_chars"], 1)
        print(
            f"Prompt size ratio inline/mcp_stdio ≈ {ratio:.1f}x "
            f"({inline_chars} vs {mcp.get('prompt_chars')} chars)",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
