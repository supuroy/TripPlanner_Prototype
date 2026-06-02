"""Shared helpers for Cursor Cloud Agent + trail-catalog MCP PoC scripts."""

from __future__ import annotations

import json
from typing import Any

from src.wanderly.flight_listing_llm import parse_llm_json_object
from src.wanderly.trail_catalog.validate import validate_trail_ids

POC_SYSTEM_JSON = """
You are a trail recommender for a proof of concept. Return ONLY one JSON object (no markdown):
{
  "scenario": "<short label for this request>",
  "data_source": "mcp_trail_catalog",
  "mcp_tools_used": ["<tool names you called>"],
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
- Prefer ONE call to search_trails_intent_tool with the full user request (and max_distance_mi when given).
  Optional: resolve_scope_tool first if scope is unclear. Do NOT probe many park_code values that return zero rows.
- Use canonical trail_id values from tool output (trail:country-region-slug). Never invent ids or URLs.
- Return exactly the number of trails requested in the user message (default 3).
""".strip()

BOOTSTRAP_PROMPT = (
    POC_SYSTEM_JSON
    + "\n\n---\n\nBootstrap: Confirm you can see the trail-catalog MCP server "
    "(call trail_catalog_stats once). Reply with a one-line JSON: "
    '{"ok": true, "stats": <brief>}.'
)


def build_recommendation_prompt(
    *,
    user_message: str,
    scenario_label: str = "custom",
    trail_count: int = 3,
) -> str:
    return (
        POC_SYSTEM_JSON
        + f"\n\n---\n\nScenario: {scenario_label}\n"
        + f"User request: {user_message}\n\n"
        +         f"Recommend exactly {trail_count} trails.\n"
        "Call search_trails_intent_tool(user_message=..., max_distance_mi=...) when the user gives distance or region constraints. "
        "List tool names you used in mcp_tools_used."
    )


def parse_mcp_tool_payload(detail: str) -> dict[str, Any] | None:
    if not (detail or "").strip():
        return None
    try:
        outer = json.loads(detail)
    except json.JSONDecodeError:
        return None
    if not isinstance(outer, dict):
        return None
    return outer


PROVE_MCP_PROMPT = """
You are verifying trail-catalog MCP on a Cursor Cloud Agent.

Do this in order:
1. Use MCP server ``trail-catalog`` (stdio). Call ``trail_catalog_stats`` once.
2. Return ONLY one JSON object (no markdown):
   {"ok": true, "mcp_server": "trail-catalog", "stats": <object from tool>}
If the server is missing, return:
   {"ok": false, "error": "<exact error text>"}

Do not use repo grep or python shell for catalog data on this turn — MCP only.
""".strip()


def assess_trail_catalog_mcp(
    tool_calls: list[dict[str, Any]],
    mcp_catalog_calls: list[dict[str, Any]],
) -> tuple[bool, str]:
    """
    True when trail-catalog MCP is proven from SSE tool_call events.
  """
    if mcp_catalog_calls:
        return True, "strict_mcp"
    blob = json.dumps(tool_calls, ensure_ascii=True).lower()
    if "trail-catalog" in blob and "not found" in blob and "available servers" in blob:
        return False, "mcp_not_found"
    for row in tool_calls:
        if str(row.get("name") or "").lower() != "get_mcp_tools":
            continue
        if str(row.get("status") or "").lower() != "completed":
            continue
        detail = str(row.get("detail") or "")
        if '"server": "trail-catalog"' in detail or '"server":"trail-catalog"' in detail:
            if "ready" in detail.lower():
                return True, "mcp_ready_get_mcp_tools"
    for row in tool_calls:
        if str(row.get("name") or "").lower() != "mcp":
            continue
        if str(row.get("status") or "").lower() != "completed":
            continue
        detail = str(row.get("detail") or "")
        if "trail-catalog" in detail or "trail_catalog_stats" in detail:
            return True, "mcp_completed"
    return False, "no_mcp_calls"


def extract_mcp_catalog_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Parse SSE tool_call events into structured trail-catalog MCP invocations.
    """
    out: list[dict[str, Any]] = []
    for row in tool_calls:
        if str(row.get("name") or "").lower() != "mcp":
            continue
        if str(row.get("status") or "").lower() != "completed":
            continue
        payload = parse_mcp_tool_payload(str(row.get("detail") or ""))
        if not payload:
            continue
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        provider = str(args.get("providerIdentifier") or "")
        tool_name = str(args.get("toolName") or args.get("name") or "")
        if provider != "trail-catalog" and not tool_name.startswith("trail-catalog-"):
            if "trail-catalog-" not in str(args.get("name") or ""):
                continue
        if tool_name.startswith("trail-catalog-"):
            tool_name = tool_name.replace("trail-catalog-", "", 1)
        mcp_args = {k: v for k, v in args.items() if k not in ("toolCallId", "name", "providerIdentifier", "toolName")}
        result_summary: Any = None
        result = payload.get("result")
        if isinstance(result, dict):
            success = result.get("success")
            if isinstance(success, dict):
                content = success.get("content")
                if isinstance(content, list) and content:
                    text_block = content[0].get("text") if isinstance(content[0], dict) else None
                    if isinstance(text_block, dict) and "text" in text_block:
                        try:
                            result_summary = json.loads(text_block["text"])
                        except json.JSONDecodeError:
                            result_summary = str(text_block["text"])[:500]
                elif isinstance(content, str):
                    try:
                        result_summary = json.loads(content)
                    except json.JSONDecodeError:
                        result_summary = content[:500]
        out.append(
            {
                "call_id": payload.get("callId") or row.get("callId"),
                "tool": tool_name,
                "arguments": mcp_args,
                "result_summary": result_summary,
            }
        )
    return out


def print_mcp_catalog_calls(calls: list[dict[str, Any]]) -> None:
    if not calls:
        print("  (no trail-catalog MCP tool calls recorded in this run)", flush=True)
        return
    for i, call in enumerate(calls, start=1):
        print(f"\n  MCP call {i}: {call.get('tool')}", flush=True)
        print(f"    arguments: {json.dumps(call.get('arguments') or {}, ensure_ascii=True)}", flush=True)
        summary = call.get("result_summary")
        if isinstance(summary, dict):
            preview = summary
            if "trails" in summary and isinstance(summary["trails"], list):
                preview = {
                    **{k: v for k, v in summary.items() if k != "trails"},
                    "trail_count": len(summary["trails"]),
                    "trail_ids_sample": [
                        t.get("trail_id") for t in summary["trails"][:5] if isinstance(t, dict)
                    ],
                }
            elif "features" in summary:
                preview = summary
            elif "parks" in summary:
                preview = summary
            print(f"    result: {json.dumps(preview, ensure_ascii=True)[:1200]}", flush=True)
        elif summary is not None:
            print(f"    result: {str(summary)[:400]}", flush=True)


def parse_selections(raw: str) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    parsed = parse_llm_json_object(raw)
    if not parsed:
        return None, [], []
    sels = parsed.get("selections")
    if not isinstance(sels, list):
        return parsed, [], list(parsed.get("mcp_tools_used") or [])
    ids = [str(x.get("trail_id") or "") for x in sels if isinstance(x, dict)]
    tools_used = parsed.get("mcp_tools_used")
    if isinstance(tools_used, list):
        tools = [str(t) for t in tools_used]
    else:
        tools = []
    return parsed, ids, tools


def validate_selection_trail_ids(
    trail_ids: list[str],
    *,
    allowed: set[str] | None = None,
) -> list[str]:
    if not allowed:
        return []
    return validate_trail_ids(trail_ids, allowed)
