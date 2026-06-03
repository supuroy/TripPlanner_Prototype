"""Shared helpers for Cursor Cloud Agent + trail-catalog MCP PoC scripts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.wanderly.flight_listing_llm import parse_llm_json_object
from src.wanderly.trail_catalog.validate import validate_trail_ids

DEFAULT_POC_SESSION_PATH = Path(__file__).resolve().parents[3] / ".cache" / "poc_trail_agent_session.json"

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


def normalize_agent_id(raw: str) -> str:
    s = (raw or "").strip()
    if "cursor.com/agents" in s and "id=" in s:
        return s.rsplit("=", 1)[-1].strip()
    return s


def load_poc_session(session_path: Path | None = None) -> dict[str, Any] | None:
    path = session_path or DEFAULT_POC_SESSION_PATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def session_agent_id(session: dict[str, Any] | None) -> str | None:
    if not session:
        return None
    aid = str(session.get("agent_id") or "").strip()
    return aid or None


def session_uses_ui_mcp(session: dict[str, Any] | None) -> bool:
    """True when follow-ups should NOT pass inline API mcpServers (preserves UI Custom MCP)."""
    if not session:
        return False
    if session.get("attach_inline_mcp") is False:
        return True
    return str(session.get("source") or "") == "bind-agent-ui"


def resolve_poc_agent_id(
    *,
    session_path: Path | None = None,
    cli_agent_id: str | None = None,
    force_new: bool = False,
) -> tuple[str | None, dict[str, Any] | None, str]:
    """
    Pick agent for PoC runs.

    Returns ``(agent_id_or_none, session_dict, mode_label)``.
    """
    if force_new:
        return None, load_poc_session(session_path), "new-agent"
    if (cli_agent_id or "").strip():
        aid = normalize_agent_id(cli_agent_id)
        session = load_poc_session(session_path)
        return aid, session, "cli-agent-id"
    session = load_poc_session(session_path)
    aid = session_agent_id(session)
    if aid:
        source = str(session.get("source") or "session")
        return aid, session, f"session ({source})"
    return None, session, "new-agent"


def local_trail_preview(
    user_message: str,
    *,
    limit: int = 3,
    max_distance_mi: float | None = 7.0,
) -> list[dict[str, Any]]:
    """Offline catalog preview when Cloud Agent API returns no assistant text."""
    from src.wanderly.trail_catalog.catalog_query import CatalogQuery, search_catalog_trails

    result = search_catalog_trails(
        CatalogQuery(
            user_message=user_message,
            max_distance_mi=max_distance_mi,
            limit=limit,
        ),
    )
    rows = result.get("trails") if isinstance(result, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def format_local_preview_selections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        tid = str(row.get("canonical_trail_id") or row.get("trail_id") or "").strip()
        if not tid:
            continue
        out.append(
            {
                "trail_id": tid,
                "name": row.get("name"),
                "reason": "local catalog preview (not from Cloud Agent API response)",
                "park_code": row.get("park_code"),
                "feature_slug": row.get("feature_slug"),
            }
        )
    return out


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
Do not create git branches or edit files on this turn.
""".strip()

LIST_MCP_PROMPT = (
    "List every MCP server name you can use for tools (short bullet list, names only)."
)


def trail_catalog_visible_in_text(text: str) -> bool:
    low = (text or "").lower()
    return "trail-catalog" in low or "trail_catalog" in low


def diagnose_trail_catalog_mcp_visible(raw: str) -> tuple[bool, list[str]]:
    """True when the model's reply mentions trail-catalog among available MCP servers."""
    low = (raw or "").lower()
    if not low.strip():
        return False, []
    names: list[str] = []
    for line in raw.splitlines():
        s = line.strip().lstrip("-*• ").strip()
        if s and len(s) < 80:
            names.append(s)
    visible = trail_catalog_visible_in_text(raw)
    return visible, names


def classify_mcp_diagnose(raw: str) -> tuple[str, bool | None]:
    """
    Classify diagnose output.

    Returns ``(status, visible)`` where status is ``empty`` | ``visible`` | ``missing``,
    and ``visible`` is None when the API returned no assistant text (unknown).
    """
    text = (raw or "").strip()
    if not text:
        return "empty", None
    visible, _ = diagnose_trail_catalog_mcp_visible(text)
    return ("visible" if visible else "missing"), visible


def assess_trail_catalog_mcp(
    tool_calls: list[dict[str, Any]],
    mcp_catalog_calls: list[dict[str, Any]],
) -> tuple[bool, str]:
    """
    True when trail-catalog MCP is proven from SSE tool_call events.
    """
    if mcp_catalog_calls:
        tools = {str(c.get("tool") or "") for c in mcp_catalog_calls}
        if any(t for t in tools if t and t != "trail_catalog_stats"):
            return True, "strict_mcp"
        return True, "strict_mcp"
    blob = json.dumps(tool_calls, ensure_ascii=True).lower()
    if "trail-catalog" in blob and "not found" in blob and "available servers" in blob:
        return False, "mcp_not_found"
    for row in tool_calls:
        if str(row.get("name") or "").lower() != "mcp":
            continue
        if str(row.get("status") or "").lower() != "completed":
            continue
        detail = str(row.get("detail") or "")
        if "trail-catalog" not in detail and "trail_catalog" not in detail:
            continue
        if "search_trails_intent_tool" in detail or "trail_catalog_stats" in detail:
            return True, "strict_mcp"
        return True, "mcp_completed"
    for row in tool_calls:
        if str(row.get("name") or "").lower() != "get_mcp_tools":
            continue
        if str(row.get("status") or "").lower() != "completed":
            continue
        detail = str(row.get("detail") or "")
        if '"server": "trail-catalog"' in detail or '"server":"trail-catalog"' in detail:
            if "ready" in detail.lower():
                return True, "mcp_ready_get_mcp_tools"
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
        mcp_args_raw = args.get("args") if isinstance(args.get("args"), dict) else {}
        mcp_args = (
            {k: v for k, v in mcp_args_raw.items()}
            if mcp_args_raw
            else {k: v for k, v in args.items() if k not in ("toolCallId", "name", "providerIdentifier", "toolName")}
        )
        result_summary: Any = None
        result = payload.get("result")
        if isinstance(result, dict):
            success = result.get("success")
            if isinstance(success, dict):
                content = success.get("content")
                if isinstance(content, list) and content:
                    block0 = content[0] if isinstance(content[0], dict) else None
                    text_val: str | None = None
                    if isinstance(block0, dict):
                        if isinstance(block0.get("text"), dict) and "text" in block0["text"]:
                            text_val = str(block0["text"]["text"])
                        elif isinstance(block0.get("text"), str):
                            text_val = block0["text"]
                    if text_val:
                        try:
                            result_summary = json.loads(text_val)
                        except json.JSONDecodeError:
                            result_summary = text_val[:500]
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


def _normalize_selection_rows(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept ``selections`` or ``recommendations``; map canonical_trail_id → trail_id."""
    rows = parsed.get("selections")
    if not isinstance(rows, list):
        rows = parsed.get("recommendations")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        tid = str(row.get("trail_id") or row.get("canonical_trail_id") or "").strip()
        if not tid:
            continue
        out.append(
            {
                "trail_id": tid,
                "name": row.get("name"),
                "reason": row.get("reason") or row.get("why"),
                "park_code": row.get("park_code"),
                "feature_slug": row.get("feature_slug"),
            }
        )
    return out


def parse_selections(raw: str) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    parsed = parse_llm_json_object(raw)
    if not parsed:
        return None, [], []
    sels = _normalize_selection_rows(parsed)
    if sels and not parsed.get("selections"):
        parsed = {**parsed, "selections": sels}
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
