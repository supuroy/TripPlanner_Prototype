#!/usr/bin/env python3
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.integrations.cursor_agent_client import (  # noqa: E402
    basic_client,
    consume_sse_assistant_streaming,
    create_agent_run,
    get_api_key,
    load_env_from_roots,
)

PROMPT = """
Run exactly one shell command in the repo (monorepo root):

cd "Travel Planner Prototype" && PYTHONPATH=. python3 -c "from src.wanderly.trail_catalog.search import catalog_stats; print(catalog_stats())"

Paste the full command output in your reply.
""".strip()


def main() -> int:
    load_env_from_roots()

    def emit(ev: dict) -> None:
        if ev.get("type") == "tool_call":
            print(f"  tool {ev.get('name')} {ev.get('status')}", flush=True)

    with basic_client(get_api_key(), timeout_s=360.0) as c:
        aid, rid = create_agent_run(c, PROMPT)
        time.sleep(2)
        text = consume_sse_assistant_streaming(c, aid, rid, emit=emit)
    print(f"text_len={len(text or '')}", flush=True)
    print(text[:2000] if text else "(empty)", flush=True)
    return 0 if (text or "").strip() else 1


if __name__ == "__main__":
    raise SystemExit(main())
