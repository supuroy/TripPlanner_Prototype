"""
Reusable Cursor Cloud Agents HTTP client (shared with repo-root hello_cursor_api.py).

Cursor exposes model output via repo-backed agents and SSE streams — not a generic
chat-completions API. See https://cursor.com/docs/cloud-agent/api/endpoints.md
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

CURSOR_API_BASE = "https://api.cursor.com"


def load_env_from_roots(extra_roots: list[str] | None = None) -> None:
    """Load .env from cwd, repo root candidates, and Travel Planner Prototype."""
    load_dotenv()
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "..", "..", ".env"),
        os.path.join(here, "..", "..", ".env"),
        os.path.join(here, "..", ".env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.getcwd(), "config", ".env"),
    ]
    if extra_roots:
        candidates.extend(extra_roots)
    for p in candidates:
        ap = os.path.abspath(p)
        if os.path.isfile(ap):
            load_dotenv(ap, override=False)


def get_api_key() -> str:
    key = (os.environ.get("CURSOR_API_KEY") or "").strip()
    if not key:
        raise ValueError(
            "Missing CURSOR_API_KEY. Set it in .env or config/.env (see config/.env.example)."
        )
    return key


def basic_client(api_key: str, timeout_s: float = 300.0) -> httpx.Client:
    return httpx.Client(base_url=CURSOR_API_BASE, auth=(api_key, ""), timeout=httpx.Timeout(timeout_s))


def normalize_github_repo_url(url: str) -> str:
    u = url.strip().rstrip("/")
    if u.startswith("git@"):
        at = u.find("@")
        colon = u.find(":", at + 1)
        if at >= 0 and colon > at:
            host = u[at + 1 : colon]
            path = u[colon + 1 :].removesuffix(".git")
            return f"https://{host}/{path}"
    if u.endswith(".git"):
        u = u[:-4]
    return u


def _pick_default_variant(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not variants:
        return None
    for v in variants:
        if v.get("isDefault"):
            return v
    return variants[0]


def resolve_agent_model(client: httpx.Client) -> dict[str, Any] | None:
    requested = (os.environ.get("CURSOR_MODEL_ID") or "").strip()
    r = client.get("/v1/models")
    r.raise_for_status()
    items = r.json().get("items") or []

    def ref_for_model(entry: dict[str, Any]) -> dict[str, Any] | None:
        mid = str(entry.get("id") or "").strip()
        if not mid:
            return None
        variants = entry.get("variants")
        if not isinstance(variants, list) or not variants:
            return {"id": mid}
        sel = _pick_default_variant(variants)
        if not isinstance(sel, dict):
            return {"id": mid}
        params = sel.get("params") or []
        if isinstance(params, list) and len(params) > 0:
            return {"id": mid, "params": params}
        return {"id": mid}

    if requested:
        for it in items:
            if isinstance(it, str) and it == requested:
                return {"id": it}
            if isinstance(it, dict) and str(it.get("id") or "").strip() == requested:
                return ref_for_model(it)
        return None

    for it in items:
        if isinstance(it, dict):
            variants = it.get("variants")
            if not isinstance(variants, list):
                continue
            for v in variants:
                if isinstance(v, dict) and v.get("isDefault"):
                    mid = str(it.get("id") or "").strip()
                    if not mid:
                        continue
                    params = v.get("params") or []
                    if isinstance(params, list) and len(params) > 0:
                        return {"id": mid, "params": params}
                    return {"id": mid}

    for it in items:
        if isinstance(it, str):
            return {"id": it}
        if isinstance(it, dict):
            got = ref_for_model(it)
            if got:
                return got
    return None


def iter_sse_lines(response: httpx.Response) -> Iterator[str]:
    for raw in response.iter_lines():
        if raw is None:
            continue
        if isinstance(raw, bytes):
            line = raw.decode("utf-8", errors="replace")
        else:
            line = raw
        yield line.rstrip("\r")


def consume_sse_assistant(client: httpx.Client, agent_id: str, run_id: str) -> str:
    url = f"/v1/agents/{agent_id}/runs/{run_id}/stream"
    headers = {"Accept": "text/event-stream"}
    buffer: list[str] = []
    event_type: str | None = None
    data_lines: list[str] = []

    def flush_block() -> None:
        nonlocal event_type, data_lines
        if event_type is None:
            data_lines = []
            return
        if not data_lines:
            event_type = None
            data_lines = []
            return
        data_raw = "\n".join(data_lines)
        if event_type == "assistant":
            try:
                payload = json.loads(data_raw)
                chunk = payload.get("text") or ""
                if chunk:
                    buffer.append(chunk)
            except json.JSONDecodeError:
                pass
        event_type = None
        data_lines = []

    with client.stream("GET", url, headers=headers) as resp:
        resp.raise_for_status()
        for line in iter_sse_lines(resp):
            if line.startswith("event:"):
                flush_block()
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
            elif line == "":
                flush_block()
        flush_block()

    return "".join(buffer)


def consume_sse_assistant_streaming(
    client: httpx.Client,
    agent_id: str,
    run_id: str,
    *,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """Like consume_sse_assistant but forwards SSE events for live UI (assistant deltas + other events)."""
    url = f"/v1/agents/{agent_id}/runs/{run_id}/stream"
    headers = {"Accept": "text/event-stream"}
    buffer: list[str] = []
    event_type: str | None = None
    data_lines: list[str] = []

    def flush_block() -> None:
        nonlocal event_type, data_lines
        if event_type is None:
            data_lines = []
            return
        if not data_lines:
            event_type = None
            data_lines = []
            return
        data_raw = "\n".join(data_lines)
        if event_type == "assistant":
            try:
                payload = json.loads(data_raw)
                chunk = payload.get("text") or ""
                if chunk:
                    buffer.append(chunk)
                    if emit:
                        emit(
                            {
                                "type": "assistant_delta",
                                "delta": chunk,
                                "chars": sum(len(x) for x in buffer),
                            }
                        )
            except json.JSONDecodeError:
                pass
        elif event_type == "thinking":
            # Cursor stream: model "thinking" / reasoning deltas (separate from final assistant text).
            try:
                payload = json.loads(data_raw)
                chunk = str(payload.get("text") or "")
                if chunk and emit:
                    emit({"type": "thinking_delta", "delta": chunk})
            except json.JSONDecodeError:
                pass
        elif event_type == "status" and emit:
            try:
                payload = json.loads(data_raw)
                emit(
                    {
                        "type": "run_status",
                        "status": str(payload.get("status") or ""),
                        "run_id": payload.get("runId"),
                    }
                )
            except json.JSONDecodeError:
                pass
        elif event_type == "tool_call" and emit:
            snippet = (data_raw or "").replace("\n", " ").strip()
            if snippet:
                emit({"type": "tool_call", "detail": snippet[:500]})
        elif event_type == "result" and emit:
            try:
                payload = json.loads(data_raw)
                emit({"type": "run_result", "status": str(payload.get("status") or "")})
            except json.JSONDecodeError:
                pass
        elif event_type == "error" and emit:
            try:
                payload = json.loads(data_raw)
                emit(
                    {
                        "type": "stream_error",
                        "code": payload.get("code"),
                        "message": str(payload.get("message") or "stream error"),
                    }
                )
            except json.JSONDecodeError:
                emit({"type": "stream_error", "message": data_raw[:300]})
        elif event_type == "heartbeat":
            pass
        elif emit and event_type:
            snippet = (data_raw or "")[:280].replace("\n", " ").strip()
            if snippet:
                emit({"type": "cursor_event", "event": event_type, "snippet": snippet})
        event_type = None
        data_lines = []

    with client.stream("GET", url, headers=headers) as resp:
        resp.raise_for_status()
        for line in iter_sse_lines(resp):
            if line.startswith("event:"):
                flush_block()
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
            elif line == "":
                flush_block()
        flush_block()

    return "".join(buffer)


def create_agent_run(
    client: httpx.Client,
    prompt_text: str,
    *,
    model_id_override: str | None = None,
) -> tuple[str, str]:
    repo_url = normalize_github_repo_url((os.environ.get("CURSOR_REPO_URL") or "").strip())
    if not repo_url:
        raise ValueError(
            "CURSOR_REPO_URL is required for Cloud Agents. Set it in .env (https://… GitHub repo URL)."
        )
    parsed = urlparse(repo_url)
    if parsed.scheme != "https":
        raise ValueError("CURSOR_REPO_URL must use https://")

    repo_ref = (os.environ.get("CURSOR_REPO_REF") or "").strip()
    starting_ref = repo_ref if repo_ref else "main"
    repo_entry: dict[str, Any] = {"url": repo_url, "startingRef": starting_ref}

    body: dict[str, Any] = {
        "prompt": {"text": prompt_text},
        "repos": [repo_entry],
    }

    override = (model_id_override or "").strip()
    if override:
        body["model"] = {"id": override}
    else:
        model_ref = resolve_agent_model(client)
        if model_ref:
            body["model"] = model_ref

    def variant_payloads(b: dict[str, Any]) -> list[dict[str, Any]]:
        uniq_keys: set[str] = set()
        out: list[dict[str, Any]] = []

        def add(x: dict[str, Any]) -> None:
            key = json.dumps(x, sort_keys=True)
            if key not in uniq_keys:
                uniq_keys.add(key)
                out.append(x)

        add(json.loads(json.dumps(b)))
        m = b.get("model")
        if isinstance(m, dict) and "params" in m:
            stripped = json.loads(json.dumps(b))
            del stripped["model"]["params"]
            add(stripped)
        if "model" in b:
            bare = json.loads(json.dumps(b))
            del bare["model"]
            add(bare)
        return out

    payloads = variant_payloads(body)
    last_text = ""
    r: httpx.Response | None = None
    for i, payload in enumerate(payloads):
        r = client.post("/v1/agents", json=payload)
        last_text = r.text
        if r.status_code < 400:
            break
        if r.status_code == 400 and i + 1 < len(payloads):
            continue
        if r.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"POST /v1/agents failed: {last_text}",
                request=r.request,
                response=r,
            )

    assert r is not None
    if r.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"POST /v1/agents failed: {last_text}",
            request=r.request,
            response=r,
        )
    data = r.json()
    agent_id = data["agent"]["id"]
    run_id = data["run"]["id"]
    return agent_id, run_id


def cursor_reuse_agents_enabled() -> bool:
    """When true (default), Wanderly may POST follow-up runs on an existing agent id per session."""
    raw = (os.environ.get("CURSOR_REUSE_AGENT") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def create_followup_run(client: httpx.Client, agent_id: str, prompt_text: str) -> str:
    """Start a new run on an existing Cloud Agent (same repo/workspace context as prior runs)."""
    r = client.post(f"/v1/agents/{agent_id}/runs", json={"prompt": {"text": prompt_text}})
    r.raise_for_status()
    data = r.json()
    run = data.get("run") if isinstance(data, dict) else None
    rid = run.get("id") if isinstance(run, dict) else None
    if not rid:
        raise ValueError(f"Missing run id in follow-up response: {data!r}")
    return str(rid)


def _begin_cloud_agent_run(
    client: httpx.Client,
    prompt_text: str,
    *,
    model_id_override: str | None = None,
    existing_agent_id: str | None = None,
    on_new_agent: Callable[[str], None] | None = None,
    on_agent_cleared: Callable[[], None] | None = None,
) -> tuple[str, str]:
    aid_in = (existing_agent_id or "").strip()
    if cursor_reuse_agents_enabled() and aid_in:
        last_err: httpx.HTTPStatusError | None = None
        for busy_try in range(2):
            try:
                run_id = create_followup_run(client, aid_in, prompt_text)
                return aid_in, run_id
            except httpx.HTTPStatusError as e:
                last_err = e
                sc = e.response.status_code if e.response is not None else 0
                if sc == 409 and busy_try == 0:
                    time.sleep(3.0)
                    continue
                if sc in (404, 410):
                    if on_agent_cleared:
                        on_agent_cleared()
                    break
                raise
        if last_err is not None and (last_err.response is None or last_err.response.status_code == 409):
            raise last_err

    agent_id, run_id = create_agent_run(client, prompt_text, model_id_override=model_id_override)
    if on_new_agent:
        on_new_agent(agent_id)
    return agent_id, run_id


def wait_for_stream_ready(client: httpx.Client, agent_id: str, run_id: str, max_wait_s: float = 60.0) -> None:
    deadline = time.monotonic() + max_wait_s
    url = f"/v1/agents/{agent_id}/runs/{run_id}"
    while time.monotonic() < deadline:
        rr = client.get(url)
        rr.raise_for_status()
        status = (rr.json().get("status") or "").upper()
        if status in ("FINISHED", "FAILED", "CANCELLED"):
            break
        time.sleep(0.5)


def _assistant_text_or_retry_sse(
    client: httpx.Client,
    agent_id: str,
    run_id: str,
    text: str,
) -> str:
    """
    First SSE read sometimes finishes before ``assistant`` events (race on new runs).
    Wait for a terminal run status, then open the stream again and collect (non-streaming consumer).
    """
    if (text or "").strip():
        return text
    wait_for_stream_ready(client, agent_id, run_id)
    retry = consume_sse_assistant(client, agent_id, run_id)
    return retry if (retry or "").strip() else text


def run_prompt_collect_assistant_text(
    client: httpx.Client,
    prompt_text: str,
    *,
    model_id_override: str | None = None,
    existing_agent_id: str | None = None,
    on_new_agent: Callable[[str], None] | None = None,
    on_agent_cleared: Callable[[], None] | None = None,
) -> str:
    """Create or continue a Cloud Agent run, consume the assistant SSE stream, return full text."""
    agent_id, run_id = _begin_cloud_agent_run(
        client,
        prompt_text,
        model_id_override=model_id_override,
        existing_agent_id=existing_agent_id,
        on_new_agent=on_new_agent,
        on_agent_cleared=on_agent_cleared,
    )
    time.sleep(1.0)
    last_err: Exception | None = None
    for attempt in range(8):
        try:
            text = consume_sse_assistant(client, agent_id, run_id)
            return _assistant_text_or_retry_sse(client, agent_id, run_id, text)
        except httpx.HTTPStatusError as e:
            last_err = e
            if e.response is not None and e.response.status_code in (404, 425):
                time.sleep(0.75 * (attempt + 1))
                continue
            raise
    if last_err:
        raise last_err
    return ""


def run_prompt_collect_assistant_text_streaming(
    client: httpx.Client,
    prompt_text: str,
    *,
    emit: Callable[[dict[str, Any]], None] | None = None,
    model_id_override: str | None = None,
    existing_agent_id: str | None = None,
    on_new_agent: Callable[[str], None] | None = None,
    on_agent_cleared: Callable[[], None] | None = None,
) -> str:
    """Create or continue a Cloud Agent run, stream assistant SSE, invoke emit() for progress, return full text."""

    def _emit(obj: dict[str, Any]) -> None:
        if emit:
            emit(obj)

    _emit({"type": "phase", "message": "Understanding what you want from this trip…"})
    agent_id, run_id = _begin_cloud_agent_run(
        client,
        prompt_text,
        model_id_override=model_id_override,
        existing_agent_id=existing_agent_id,
        on_new_agent=on_new_agent,
        on_agent_cleared=on_agent_cleared,
    )
    _emit({"type": "phase", "message": "Exploring destinations and tradeoffs for you…"})
    time.sleep(1.0)
    last_err: Exception | None = None
    for attempt in range(8):
        try:
            _emit({"type": "phase", "message": "Listening to the model as it works…"})
            text = consume_sse_assistant_streaming(client, agent_id, run_id, emit=emit)
            if not (text or "").strip():
                _emit({"type": "phase", "message": "Almost there — wrapping up…"})
            return _assistant_text_or_retry_sse(client, agent_id, run_id, text)
        except httpx.HTTPStatusError as e:
            last_err = e
            if e.response is not None and e.response.status_code in (404, 425):
                _emit(
                    {
                        "type": "phase",
                        "message": "Still connecting — trying again in a moment…",
                    }
                )
                time.sleep(0.75 * (attempt + 1))
                continue
            raise
    if last_err:
        raise last_err
    return ""


def run_auth_only_impl(client: httpx.Client) -> None:
    r = client.get("/v1/me")
    r.raise_for_status()
    print("GET /v1/me -> OK")
    print(json.dumps(r.json(), indent=2))

    r2 = client.get("/v1/models")
    r2.raise_for_status()
    print("\nGET /v1/models -> OK")
    print(json.dumps(r2.json(), indent=2))


def _canonical_repo_url(url: str) -> str:
    return normalize_github_repo_url(url).rstrip("/").lower()


def run_list_repos_impl(client: httpx.Client) -> None:
    print(
        "Calling GET /v1/repositories (slow; ~1 request/min limit). "
        "Checking whether CURSOR_REPO_URL is visible to the Cursor GitHub app…\n",
        flush=True,
    )
    r = client.get("/v1/repositories")
    r.raise_for_status()
    data = r.json()
    items = data.get("items") or []
    urls: list[str] = []
    for it in items:
        if isinstance(it, dict) and it.get("url"):
            urls.append(str(it["url"]))
    want = (os.environ.get("CURSOR_REPO_URL") or "").strip()
    print(f"Repositories returned: {len(urls)}\n", flush=True)
    if want:
        c_want = _canonical_repo_url(want)
        match = [u for u in urls if _canonical_repo_url(u) == c_want]
        if match:
            print(f"CURSOR_REPO_URL matches app-visible repo: {match[0]!r}\n", flush=True)
        else:
            print(
                f"CURSOR_REPO_URL is NOT in this list (canonical: {c_want!r}).\n"
                "Cloud Agents POST /v1/agents will reject unknown repos with HTTP 400.\n",
                flush=True,
            )
    else:
        print("Set CURSOR_REPO_URL to check for a specific repo.\n", flush=True)
    preview = min(30, len(urls))
    if preview:
        print(f"First {preview} URLs:", flush=True)
        for u in urls[:preview]:
            print(f"  {u}", flush=True)
    if len(urls) > preview:
        print(f"  … and {len(urls) - preview} more", flush=True)


def run_llm_hello_impl(client: httpx.Client, prompt_text: str) -> None:
    print("Prompt:\n" + prompt_text + "\n", flush=True)
    agent_id, run_id = create_agent_run(client, prompt_text)
    print(f"\nCreated agent={agent_id} run={run_id}\n--- assistant stream ---\n", flush=True)
    time.sleep(1.0)
    last_err: Exception | None = None
    for attempt in range(8):
        try:
            text = consume_sse_assistant_streaming_to_stdout(client, agent_id, run_id)
            print("--- end stream ---\n", flush=True)
            if not text.strip():
                wait_for_stream_ready(client, agent_id, run_id)
                rr = client.get(f"/v1/agents/{agent_id}/runs/{run_id}")
                rr.raise_for_status()
                print("Run status:", json.dumps(rr.json(), indent=2))
            return
        except httpx.HTTPStatusError as e:
            last_err = e
            if e.response is not None and e.response.status_code in (404, 425):
                time.sleep(0.75 * (attempt + 1))
                continue
            raise
    if last_err:
        raise last_err


def consume_sse_assistant_streaming_to_stdout(client: httpx.Client, agent_id: str, run_id: str) -> str:
    """Like consume_sse_assistant but also writes chunks to stdout (CLI UX)."""
    url = f"/v1/agents/{agent_id}/runs/{run_id}/stream"
    headers = {"Accept": "text/event-stream"}
    buffer: list[str] = []
    event_type: str | None = None
    data_lines: list[str] = []

    def flush_block() -> None:
        nonlocal event_type, data_lines
        if event_type is None:
            data_lines = []
            return
        if not data_lines:
            event_type = None
            data_lines = []
            return
        data_raw = "\n".join(data_lines)
        if event_type == "assistant":
            try:
                payload = json.loads(data_raw)
                chunk = payload.get("text") or ""
                if chunk:
                    buffer.append(chunk)
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
            except json.JSONDecodeError:
                pass
        event_type = None
        data_lines = []

    with client.stream("GET", url, headers=headers) as resp:
        resp.raise_for_status()
        for line in iter_sse_lines(resp):
            if line.startswith("event:"):
                flush_block()
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
            elif line == "":
                flush_block()
        flush_block()

    sys.stdout.write("\n")
    return "".join(buffer)
