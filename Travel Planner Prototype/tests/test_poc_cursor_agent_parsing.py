"""PoC parsing: MCP SSE detail + agent JSON shape (recommendations vs selections)."""

from __future__ import annotations

import json

from src.wanderly.trail_catalog.poc_cursor_agent import (
    assess_trail_catalog_mcp,
    extract_mcp_catalog_calls,
    parse_selections,
)


def _mcp_completed_detail(*, include_result: bool = True) -> str:
    trails = [
        {
            "trail_id": "trail:us-utah-zion-lower-emerald-pool-trail",
            "name": "Lower Emerald Pool Trail",
            "description": "x" * 200,
        },
        {
            "trail_id": "trail:us-utah-zion-lower-pine-creek-waterfall-trail",
            "name": "Lower Pine Creek Waterfall Trail",
            "description": "y" * 200,
        },
        {
            "trail_id": "trail:us-utah-salt-lake-rocky-mouth-falls-trail",
            "name": "Rocky Mouth Falls Trail",
            "description": "z" * 200,
        },
    ]
    result_block = {
        "success": {
            "content": [
                {
                    "text": {
                        "text": json.dumps({"ok": True, "trails": trails, "trail_count": 3})
                    }
                }
            ]
        }
    }
    payload = {
        "callId": "call-1",
        "name": "mcp",
        "status": "completed",
        "args": {
            "providerIdentifier": "trail-catalog",
            "toolName": "search_trails_intent_tool",
            "args": {
                "user_message": "3 moderate waterfall hikes in Utah under 7 miles",
                "max_distance_mi": 7,
            },
        },
    }
    if include_result:
        payload["result"] = result_block
    return json.dumps(payload)


def test_extract_mcp_catalog_calls_needs_full_detail_not_800_char_truncation():
    full = _mcp_completed_detail()
    truncated = full[:800]
    assert len(full) > 900
    assert extract_mcp_catalog_calls(
        [{"name": "mcp", "status": "completed", "detail": truncated}]
    ) == []
    calls = extract_mcp_catalog_calls(
        [{"name": "mcp", "status": "completed", "detail": full}]
    )
    assert len(calls) == 1
    assert calls[0]["tool"] == "search_trails_intent_tool"
    assert calls[0]["arguments"]["max_distance_mi"] == 7
    summary = calls[0]["result_summary"]
    assert isinstance(summary, dict)
    assert summary.get("trail_count") == 3


def test_parse_selections_accepts_recommendations_and_canonical_trail_id():
    raw = """
Here are three Utah waterfall hikes under 7 miles.

{"ok": true, "recommendations": [
  {"canonical_trail_id": "trail:us-utah-zion-lower-emerald-pool-trail", "name": "Lower Emerald Pool", "why": "Short waterfall payoff"},
  {"canonical_trail_id": "trail:us-utah-zion-lower-pine-creek-waterfall-trail", "name": "Lower Pine Creek", "why": "Moderate canyon"},
  {"canonical_trail_id": "trail:us-utah-salt-lake-rocky-mouth-falls-trail", "name": "Rocky Mouth Falls", "why": "Family-friendly"}
], "mcp_tools_used": ["search_trails_intent_tool"]}
"""
    parsed, ids, tools = parse_selections(raw)
    assert parsed is not None
    assert len(ids) == 3
    assert ids[0].startswith("trail:us-utah-")
    assert tools == ["search_trails_intent_tool"]
    assert parsed["selections"][0]["trail_id"] == ids[0]
    assert parsed["selections"][0]["reason"] == "Short waterfall payoff"


def test_assess_prefers_completed_mcp_search_over_get_mcp_tools_only():
    tool_calls = [
        {
            "name": "get_mcp_tools",
            "status": "completed",
            "detail": '{"result":{"success":{"content":[{"text":{"text":"{\\"server\\": \\"trail-catalog\\", \\"status\\": \\"ready\\", \\"claimed_tools\\": [\\"search_trails_intent_tool\\"]}"}}]}}}',
        },
        {"name": "mcp", "status": "completed", "detail": _mcp_completed_detail()},
    ]
    mcp_calls = extract_mcp_catalog_calls(tool_calls)
    ok, note = assess_trail_catalog_mcp(tool_calls, mcp_calls)
    assert ok is True
    assert note == "strict_mcp"
