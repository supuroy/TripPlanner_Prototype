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


def main() -> int:
    load_env_from_roots()
    events: list[str] = []

    def emit(ev: dict) -> None:
        events.append(str(ev.get("type")))
        if ev.get("type") == "tool_call":
            print(f"  tool {ev.get('name')} {ev.get('status')}", flush=True)

    with basic_client(get_api_key(), timeout_s=360.0) as c:
        aid, rid = create_agent_run(
            c,
            "Use run_terminal_cmd once to run: echo cloud_shell_ok — then reply with the output.",
        )
        time.sleep(2)
        text = consume_sse_assistant_streaming(c, aid, rid, emit=emit)
    print(f"text_len={len(text or '')} events={sorted(set(events))}", flush=True)
    print(text[:500] if text else "(empty)", flush=True)
    return 0 if "cloud_shell_ok" in (text or "") else 1


if __name__ == "__main__":
    raise SystemExit(main())
