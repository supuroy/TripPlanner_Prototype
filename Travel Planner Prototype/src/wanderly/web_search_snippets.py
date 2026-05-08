"""Optional web search snippets for advisor grounding (Tavily or Brave Search API)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from src.integrations.cursor_agent_client import load_env_from_roots


def _clip(s: str, n: int) -> str:
    t = (s or "").strip().replace("\x00", "")
    return t if len(t) <= n else t[: n - 1] + "…"


def _tavily_search(query: str, *, max_results: int) -> list[dict[str, str]]:
    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not key:
        return []
    body: dict[str, Any] = {
        "api_key": key,
        "query": query[:400],
        "search_depth": "basic",
        "max_results": min(max_results, 10),
        "include_answer": False,
    }
    with httpx.Client(timeout=35.0) as client:
        r = client.post("https://api.tavily.com/search", json=body)
        r.raise_for_status()
        data = r.json()
    out: list[dict[str, str]] = []
    for it in (data.get("results") or [])[:max_results]:
        if not isinstance(it, dict):
            continue
        title = _clip(str(it.get("title") or ""), 220)
        url = _clip(str(it.get("url") or ""), 500)
        body_txt = _clip(str(it.get("content") or it.get("raw_content") or ""), 1200)
        if title or body_txt:
            out.append({"title": title or url or "result", "url": url, "body": body_txt})
    return out


def _brave_search(query: str, *, max_results: int) -> list[dict[str, str]]:
    key = (os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get("BRAVE_API_KEY") or "").strip()
    if not key:
        return []
    with httpx.Client(timeout=35.0) as client:
        r = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query[:400], "count": min(max_results, 10)},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    raw = data.get("web") or {}
    results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(results, list):
        return []
    out: list[dict[str, str]] = []
    for it in results[:max_results]:
        if not isinstance(it, dict):
            continue
        title = _clip(str(it.get("title") or ""), 220)
        url = _clip(str(it.get("url") or ""), 500)
        desc = _clip(str(it.get("description") or it.get("snippet") or ""), 1200)
        if title or desc:
            out.append({"title": title or url or "result", "url": url, "body": desc})
    return out


def fetch_web_search_snippets(query: str, *, max_results: int = 6) -> list[dict[str, str]]:
    """
    Return short title/url/body rows for LLM grounding.

    Configure **one** of:
    - ``TAVILY_API_KEY`` (recommended — rich text snippets), or
    - ``BRAVE_SEARCH_API_KEY`` (or legacy ``BRAVE_API_KEY``).
    """
    load_env_from_roots()
    q = (query or "").strip()
    if len(q) < 2:
        return []
    try:
        sn = _tavily_search(q, max_results=max_results)
        if sn:
            return sn
        return _brave_search(q, max_results=max_results)
    except Exception:
        return []
