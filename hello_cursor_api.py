"""
Hello-world for Cursor's HTTP API from Python.

Delegates HTTP/SSE logic to Travel Planner Prototype/src/integrations/cursor_agent_client.py

  --auth-only   Calls GET /v1/me and GET /v1/models
  --list-repos  GET /v1/repositories (slow rate limits)
  (default)     Cloud Agent run with TRIP_PROMPT

Docs: https://cursor.com/docs/api
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

# Repo layout: TripPlanner_Prototype/hello_cursor_api.py + Travel Planner Prototype/src/…
_TP_SRC = Path(__file__).resolve().parent / "Travel Planner Prototype" / "src"
if _TP_SRC.is_dir():
    _s = str(_TP_SRC)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from integrations.cursor_agent_client import (  # noqa: E402
    basic_client,
    get_api_key,
    load_env_from_roots,
    run_auth_only_impl,
    run_list_repos_impl,
    run_llm_hello_impl,
)

TRIP_PROMPT = (
    "Answer directly without using tools or editing any repository files.\n\n"
    "top 3 warm beach vacation destination suggestions in winter for someone in Seattle"
)


def load_env() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    load_env_from_roots(
        extra_roots=[
            os.path.join(root, ".env"),
            os.path.join(root, "config", ".env"),
        ]
    )


def run_auth_only(client: httpx.Client) -> None:
    run_auth_only_impl(client)


def run_list_repos(client: httpx.Client) -> None:
    run_list_repos_impl(client)


def run_llm_hello(client: httpx.Client) -> None:
    run_llm_hello_impl(client, TRIP_PROMPT)


def main() -> None:
    load_env()
    api_key = get_api_key()

    p = argparse.ArgumentParser(description="Cursor API hello-world (Python)")
    p.add_argument(
        "--auth-only",
        action="store_true",
        help="Only call /v1/me and GET /v1/models (no Cloud Agent, no repo required).",
    )
    p.add_argument(
        "--list-repos",
        action="store_true",
        help="GET /v1/repositories (strict rate limits) and check CURSOR_REPO_URL against it.",
    )
    args = p.parse_args()

    if args.auth_only and args.list_repos:
        print("Use only one of --auth-only or --list-repos.", file=sys.stderr)
        sys.exit(2)

    try:
        with basic_client(api_key) as client:
            if args.auth_only:
                run_auth_only(client)
            elif args.list_repos:
                run_list_repos(client)
            else:
                run_llm_hello(client)
    except httpx.HTTPStatusError as e:
        msg = f"HTTP {e.response.status_code} for {e.request.url!r}"
        try:
            detail = e.response.text
        except Exception:
            detail = ""
        print(msg, file=sys.stderr)
        if detail.strip():
            print(detail, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
