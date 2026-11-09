"""
Reusable Cursor Cloud Agents HTTP client (shared with repo-root hello_cursor_api.py).

Cursor exposes model output via repo-backed agents and SSE streams — not a generic
chat-completions API. See https://cursor.com/docs/cloud-agent/api/endpoints.md
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

try:
    from src.wanderly.itinerary_debug_log import log_itinerary_build as _itinerary_log
except Exception:
    _itinerary_log = None

CURSOR_API_BASE = "https://api.cursor.com"

_cursor_lane_locks: dict[str, threading.Lock] = {}
_cursor_lane_locks_guard = threading.Lock()


def _cursor_lane_run_lock(lock_key: str | None):
    """
    Serialize Cloud Agent usage for one Wanderly session + persistence lane.

    ThreadingHTTPServer can run two ``advisor_feed`` (or smoke + advisor) handlers concurrently; without
    this, two ``POST …/agents/{id}/runs`` calls race and Cursor returns 409.
    """
    k = (lock_key or "").strip()
    if not k:
        return nullcontext()
    with _cursor_lane_locks_guard:
        if k not in _cursor_lane_locks:
            _cursor_lane_locks[k] = threading.Lock()
        lk = _cursor_lane_locks[k]
    return lk


def _trace_cursor(msg: str, *, path: str | None = None) -> None:
    """Writes to ``.cache/itinerary_debug.log`` when ``WANDERLY_ITIN_LOG`` is on (same as itinerary traces)."""
    if _itinerary_log is None:
        return
    try:
        pfx = f"path={path} " if (path or "").strip() else ""
        _itinerary_log(f"CURSOR {pfx}{msg}")
    except Exception:
        pass


def _httpx_err_snip(e: httpx.HTTPStatusError) -> str:
    try:
        if e.response is None:
            return ""
        return (e.response.text or "").replace("\n", " ").strip()[:280]
    except Exception:
        return ""


# ``GET …/runs/{id}/stream`` sometimes returns 409 (conflict) or 503 while the run is still
# materializing, or if a second stream open races Cursor's lifecycle. Retry with backoff.
_RETRYABLE_SSE_STATUS = frozenset({404, 425, 409, 503})


def _is_recoverable_cursor_stream_sse_message(message: str) -> bool:
    """
    Cursor may emit ``event: error`` on the run SSE stream when the live stream handle expires
    while the run is still finishing. The client UI treats ``stream_error`` as fatal; suppress
    these so the server can poll ``GET …/runs/{id}`` or retry with a fresh agent.
    """
    m = (message or "").strip().lower()
    if not m:
        return False
    needles = (
        "no longer available",
        "stream is no longer",
        "run stream",
        "already consumed",
        "stream ended",
    )
    return any(n in m for n in needles)


def _sse_retry_sleep_seconds(attempt: int) -> float:
    """Bounded exponential backoff (same spirit as ``POST …/runs`` 409 retries)."""
    return min(12.0, 1.25 * (2 ** max(0, attempt)))


def assistant_text_from_run_json(blob: dict[str, Any]) -> str:
    """
    Best-effort extract assistant-visible text from ``GET /v1/agents/{id}/runs/{run_id}`` JSON
    when SSE cannot be opened again (e.g. second stream returns 409 after run finished).
    """
    for key in ("assistant_message", "result", "output", "summary", "responseText", "text"):
        v = blob.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    msgs = blob.get("messages")
    if isinstance(msgs, list):
        parts: list[str] = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "").lower()
            if role in ("assistant", "agent", "model"):
                c = m.get("content")
                if isinstance(c, str) and c.strip():
                    parts.append(c.strip())
                elif isinstance(c, list):
                    for bit in c:
                        if isinstance(bit, dict) and isinstance(bit.get("text"), str) and bit["text"].strip():
                            parts.append(bit["text"].strip())
        if parts:
            return "\n".join(parts)
    out = blob.get("output")
    if isinstance(out, dict):
        inner = assistant_text_from_run_json(out)
        if inner:
            return inner
    resp = blob.get("response")
    if isinstance(resp, dict):
        inner = assistant_text_from_run_json(resp)
        if inner:
            return inner
    if isinstance(resp, str) and resp.strip():
        return resp.strip()
    return ""


def _assistant_text_from_run_resource(client: httpx.Client, agent_id: str, run_id: str) -> str:
    try:
        r = client.get(f"/v1/agents/{agent_id}/runs/{run_id}")
        r.raise_for_status()
        body = r.json()
    except Exception:
        return ""
    blob: Any = body
    if isinstance(body, dict):
        run_o = body.get("run")
        if isinstance(run_o, dict):
            blob = run_o
    if not isinstance(blob, dict):
        return ""
    return assistant_text_from_run_json(blob)


def fetch_run_blob(client: httpx.Client, agent_id: str, run_id: str) -> dict[str, Any] | None:
    """``GET /v1/agents/{id}/runs/{run_id}`` run object, or None on error."""
    try:
        r = client.get(f"/v1/agents/{agent_id}/runs/{run_id}")
        r.raise_for_status()
        body = r.json()
    except Exception:
        return None
    if isinstance(body, dict):
        run_o = body.get("run")
        if isinstance(run_o, dict):
            return run_o
        return body
    return None


def poll_run_for_assistant_text(
    client: httpx.Client,
    agent_id: str,
    run_id: str,
    *,
    max_wait_s: float = 90.0,
    interval_s: float = 0.45,
    on_poll: Callable[[str, float], None] | None = None,
) -> str:
    """
    Poll ``GET …/runs/{id}`` until assistant text appears or the run fails / timeout.
    Used when SSE ``/stream`` consistently returns 409 (Cursor lifecycle / single-consumer semantics).
    """
    deadline = time.monotonic() + max_wait_s
    terminal_fail = frozenset({"FAILED", "CANCELLED", "ERROR"})
    terminal_ok = frozenset({"COMPLETED", "COMPLETE", "SUCCEEDED", "DONE", "FINISHED"})
    t0 = time.monotonic()
    last_status = ""
    while time.monotonic() < deadline:
        blob = fetch_run_blob(client, agent_id, run_id)
        if isinstance(blob, dict):
            txt = assistant_text_from_run_json(blob)
            if txt.strip():
                return txt.strip()
            st = str(blob.get("status") or blob.get("state") or "").upper()
            if st != last_status:
                last_status = st
                if on_poll:
                    on_poll(st, time.monotonic() - t0)
            if st in terminal_fail:
                break
            if st in terminal_ok:
                break
        time.sleep(interval_s)
    return ""


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


def _read_api_key_file() -> str:
    path = (os.environ.get("CURSOR_API_KEY_FILE") or "").strip()
    if not path:
        return ""
    try:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _apply_hosted_cursor_defaults() -> None:
    try:
        from src.wanderly.app_paths import hosted_runtime
    except Exception:
        return
    if not hosted_runtime():
        return
    wanderly_repo = (os.environ.get("WANDERLY_CURSOR_REPO_URL") or "").strip()
    gh_repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if not (os.environ.get("CURSOR_REPO_URL") or "").strip():
        if wanderly_repo:
            os.environ.setdefault("CURSOR_REPO_URL", wanderly_repo)
        elif gh_repo:
            os.environ.setdefault("CURSOR_REPO_URL", f"https://github.com/{gh_repo}")
    if not (os.environ.get("CURSOR_REPO_REF") or "").strip():
        ref = (
            (os.environ.get("WANDERLY_CURSOR_REPO_REF") or "").strip()
            or (os.environ.get("VERCEL_GIT_COMMIT_REF") or "").strip()
            or (os.environ.get("GITHUB_REF_NAME") or "").strip()
        )
        if ref:
            os.environ.setdefault("CURSOR_REPO_REF", ref)


def get_api_key() -> str:
    _apply_hosted_cursor_defaults()
    key = (
        (os.environ.get("CURSOR_API_KEY") or "").strip()
        or (os.environ.get("WANDERLY_CURSOR_API_KEY") or "").strip()
        or _read_api_key_file()
    )
    if not key:
        try:
            from src.wanderly.app_paths import hosted_runtime
            hosted = hosted_runtime()
        except Exception:
            hosted = bool((os.environ.get("CURSOR_APP_ID") or "").strip())
        if hosted:
            raise ValueError(
                "Missing Cloud Agents API key. In Cursor, open this app's Publish settings → "
                "Secrets and set WANDERLY_CURSOR_API_KEY (create a key at cursor.com/dashboard → "
                "Integrations → Cloud Agents), then republish."
            )
        raise ValueError(
            "Missing CURSOR_API_KEY. Set it in .env or config/.env (see config/.env.example), "
            "or WANDERLY_CURSOR_API_KEY for hosted apps."
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
        elif event_type == "result":
            try:
                payload = json.loads(data_raw)
                chunk = str(payload.get("text") or "")
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
    tool_detail_limit: int = 120_000,
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
            try:
                payload = json.loads(data_raw)
                cap = max(int(tool_detail_limit), 800)
                emit(
                    {
                        "type": "tool_call",
                        "callId": payload.get("callId"),
                        "name": str(payload.get("name") or ""),
                        "status": str(payload.get("status") or ""),
                        "truncated": payload.get("truncated"),
                        "detail": (data_raw or "")[:cap],
                    }
                )
            except json.JSONDecodeError:
                snippet = (data_raw or "").replace("\n", " ").strip()
                if snippet:
                    emit({"type": "tool_call", "detail": snippet[:500]})
        elif event_type == "result":
            try:
                payload = json.loads(data_raw)
                chunk = str(payload.get("text") or "")
                if chunk:
                    buffer.append(chunk)
                    if emit:
                        emit({"type": "assistant_delta", "delta": chunk, "from": "result"})
                if emit:
                    emit(
                        {
                            "type": "run_result",
                            "status": str(payload.get("status") or ""),
                            "durationMs": payload.get("durationMs"),
                        }
                    )
            except json.JSONDecodeError:
                if emit:
                    emit({"type": "run_result", "status": ""})
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
    mcp_servers: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    _apply_hosted_cursor_defaults()
    repo_url = normalize_github_repo_url((os.environ.get("CURSOR_REPO_URL") or "").strip())
    if not repo_url:
        raise ValueError(
            "CURSOR_REPO_URL is required for Cloud Agents. Set it in .env (https://… GitHub repo URL) "
            "or add CURSOR_REPO_URL in the app's Publish → Secrets."
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
        "mode": "agent",
    }
    if mcp_servers:
        body["mcpServers"] = mcp_servers
        _trace_cursor(f"create_agent_run mcp_servers={len(mcp_servers)}", path=None)

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


_REUSE_FOLLOWUP_HINT_DEFAULT = (
    "Follow-up on this same Cloud Agent thread: keep the same output format and every rule you were given "
    "earlier in this thread (they still apply). Do not restate the long instructions. "
    "Respond using only the new information below."
)


def cursor_reuse_strip_static_instructions_enabled() -> bool:
    """
    When true (default), follow-up runs on an existing agent may omit a leading static instruction block
    that was already sent on the first run (see ``persistent_instruction_prefix`` on run helpers).
    New-agent paths always send the full prompt text.
    """
    raw = (os.environ.get("CURSOR_REUSE_STRIP_STATIC_INSTRUCTIONS") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def effective_followup_prompt_for_reuse(
    *,
    full_prompt: str,
    persistent_instruction_prefix: str | None,
    existing_agent_id: str | None,
) -> str:
    """
    For ``POST …/agents/{id}/runs``, optionally replace ``full_prompt`` with a shorter follow-up body.

    Callers pass the same ``full_prompt`` as for a fresh agent; when reusing an agent id and
    ``persistent_instruction_prefix`` matches the start of ``full_prompt``, the prefix is stripped and a
    short reminder is prepended so the model still knows prior rules apply.
    """
    if not (existing_agent_id or "").strip():
        return full_prompt
    if not cursor_reuse_strip_static_instructions_enabled():
        return full_prompt
    pfx = (persistent_instruction_prefix or "").strip()
    if not pfx:
        return full_prompt
    base = full_prompt or ""
    if not base.startswith(pfx):
        return full_prompt
    tail = base[len(pfx) :].lstrip("\n")
    if len(tail.strip()) < 12:
        return full_prompt
    hint = (os.environ.get("CURSOR_REUSE_FOLLOWUP_HINT") or "").strip() or _REUSE_FOLLOWUP_HINT_DEFAULT
    return f"{hint}\n\n{tail}"


def create_followup_run(
    client: httpx.Client,
    agent_id: str,
    prompt_text: str,
    *,
    mcp_servers: list[dict[str, Any]] | None = None,
) -> str:
    """Start a new run on an existing Cloud Agent (same repo/workspace context as prior runs)."""
    body: dict[str, Any] = {"prompt": {"text": prompt_text}, "mode": "agent"}
    if mcp_servers:
        body["mcpServers"] = mcp_servers
        _trace_cursor(f"create_followup_run mcp_servers={len(mcp_servers)}", path=None)
    r = client.post(f"/v1/agents/{agent_id}/runs", json=body)
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
    persistent_instruction_prefix: str | None = None,
    mcp_servers: list[dict[str, Any]] | None = None,
    on_new_agent: Callable[[str], None] | None = None,
    on_agent_cleared: Callable[[], None] | None = None,
    cursor_trace_path: str | None = None,
) -> tuple[str, str]:
    tp = (cursor_trace_path or "").strip() or None
    aid_in = (existing_agent_id or "").strip()
    if cursor_reuse_agents_enabled() and aid_in:
        followup_prompt = effective_followup_prompt_for_reuse(
            full_prompt=prompt_text,
            persistent_instruction_prefix=persistent_instruction_prefix,
            existing_agent_id=aid_in,
        )
        if followup_prompt != prompt_text:
            _trace_cursor(
                f"reuse_prompt_strip reuse=1 chars_in={len(prompt_text)} chars_out={len(followup_prompt)}",
                path=tp,
            )
        _trace_cursor(f"reuse_attempt agent_tail={aid_in[-8:]!r} reuse=1", path=tp)
        last_err: httpx.HTTPStatusError | None = None
        max_busy_tries = 6
        for busy_try in range(max_busy_tries):
            try:
                run_id = create_followup_run(
                    client, aid_in, followup_prompt, mcp_servers=mcp_servers
                )
                _trace_cursor(f"reuse_ok agent_tail={aid_in[-8:]!r} run_tail={str(run_id)[-8:]!r}", path=tp)
                return aid_in, run_id
            except httpx.HTTPStatusError as e:
                last_err = e
                sc = e.response.status_code if e.response is not None else 0
                snip = _httpx_err_snip(e)
                _trace_cursor(
                    f"reuse_http_error agent_tail={aid_in[-8:]!r} status={sc} try={busy_try + 1} body_snip={snip!r}",
                    path=tp,
                )
                if sc == 409:
                    if busy_try + 1 < max_busy_tries:
                        time.sleep(min(12.0, 1.25 * (2**busy_try)))
                        continue
                    break
                if sc in (404, 410):
                    if on_agent_cleared:
                        on_agent_cleared()
                    _trace_cursor(f"reuse_agent_gone agent_tail={aid_in[-8:]!r} status={sc}", path=tp)
                    break
                raise
        if last_err is not None and last_err.response is not None and last_err.response.status_code == 409:
            _trace_cursor(
                f"reuse_409_fallback_new_agent agent_tail={aid_in[-8:]!r} body_snip={_httpx_err_snip(last_err)!r}",
                path=tp,
            )
            if on_agent_cleared:
                on_agent_cleared()
            agent_id, run_id = create_agent_run(client, prompt_text, model_id_override=model_id_override)
            if on_new_agent:
                on_new_agent(agent_id)
            return agent_id, run_id
        if last_err is not None and last_err.response is None:
            raise last_err

    agent_id, run_id = create_agent_run(client, prompt_text, model_id_override=model_id_override)
    _trace_cursor(f"new_agent_run agent_tail={agent_id[-8:]!r} run_tail={run_id[-8:]!r} reuse=0", path=tp)
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


def wait_for_run_started(
    client: httpx.Client,
    agent_id: str,
    run_id: str,
    *,
    max_wait_s: float = 45.0,
) -> str:
    """Poll until run leaves ``CREATING`` (or terminal). Returns last status."""
    deadline = time.monotonic() + max_wait_s
    last = ""
    while time.monotonic() < deadline:
        blob = fetch_run_blob(client, agent_id, run_id)
        if isinstance(blob, dict):
            last = str(blob.get("status") or blob.get("state") or "").upper()
            if last and last not in ("CREATING", ""):
                return last
        time.sleep(0.35)
    return last


def consume_run_sse_robust(
    client: httpx.Client,
    agent_id: str,
    run_id: str,
    *,
    emit: Callable[[dict[str, Any]], None] | None = None,
    tool_detail_limit: int = 120_000,
    max_wait_s: float = 120.0,
    max_attempts: int = 4,
) -> str:
    """
    Read assistant text from run SSE with reconnect retries.

    Cursor sometimes closes the first stream before assistant events arrive, or returns an
    empty turn while ``durationMs`` shows work happened. Re-open ``/stream`` after short waits.
    """
    terminal = frozenset({"FINISHED", "FAILED", "CANCELLED", "COMPLETE", "COMPLETED", "DONE"})
    texts: list[str] = []
    deadline = time.monotonic() + max_wait_s
    attempt = 0
    while time.monotonic() < deadline and attempt < max_attempts:
        attempt += 1
        wait_for_run_started(client, agent_id, run_id, max_wait_s=min(20.0, max_wait_s))
        try:
            chunk = consume_sse_assistant_streaming(
                client,
                agent_id,
                run_id,
                emit=emit,
                tool_detail_limit=tool_detail_limit,
            )
        except httpx.HTTPStatusError as exc:
            sc = exc.response.status_code if exc.response is not None else 0
            if sc == 409 and attempt < max_attempts:
                time.sleep(min(2.0 * attempt, 8.0))
                continue
            raise
        if chunk:
            texts.append(chunk)
        combined = "".join(texts)
        if combined.strip():
            return combined
        blob = fetch_run_blob(client, agent_id, run_id)
        st = ""
        if isinstance(blob, dict):
            st = str(blob.get("status") or blob.get("state") or "").upper()
        if st in terminal:
            if attempt < max_attempts and time.monotonic() < deadline:
                time.sleep(min(2.5 * attempt, 8.0))
                continue
            break
        time.sleep(1.0)
    return "".join(texts)


def _assistant_text_or_retry_sse(
    client: httpx.Client,
    agent_id: str,
    run_id: str,
    text: str,
) -> str:
    """
    First SSE read sometimes finishes before ``assistant`` events (race on new runs).
    Wait for a terminal run status, then open the stream again and collect (non-streaming consumer).
    If Cursor returns 409 on the second stream (common once a run is terminal), fall back to
    ``GET …/runs/{id}`` JSON when the API exposes assistant text there.
    """
    if (text or "").strip():
        return text
    wait_for_stream_ready(client, agent_id, run_id)
    try:
        retry = consume_sse_assistant(client, agent_id, run_id)
    except httpx.HTTPStatusError as e:
        sc = e.response.status_code if e.response is not None else 0
        if sc == 409:
            _trace_cursor(
                "sse_second_stream_409_try_run_json "
                f"run_tail={str(run_id)[-8:]!r} body_snip={_httpx_err_snip(e)!r}",
                path=None,
            )
            fb = poll_run_for_assistant_text(client, agent_id, run_id, max_wait_s=75.0)
            return fb if (fb or "").strip() else text
        raise
    return retry if (retry or "").strip() else text


def _llm_telemetry_maybe_start(
    *,
    activity: str | None,
    sub_activity: str | None,
    prompt_text: str,
    persistent_instruction_prefix: str | None,
    stream: bool,
    model_id_override: str | None,
    meta: dict[str, Any] | None,
) -> tuple[str | None, float]:
    act = (activity or "").strip()
    if not act:
        return None, 0.0
    try:
        from src.wanderly import llm_telemetry as lt

        if not lt.llm_telemetry_enabled():
            return None, 0.0
        span_id = lt.record_llm_start(
            activity=act,
            sub_activity=(sub_activity or "").strip() or "unknown",
            prompt_text=prompt_text,
            persistent_instruction_prefix=persistent_instruction_prefix,
            stream=stream,
            model_id=(model_id_override or "").strip() or None,
            meta=meta,
        )
        return span_id, time.perf_counter()
    except Exception:
        return None, 0.0


def _llm_telemetry_maybe_end(
    span_id: str | None,
    started_at_perf: float,
    *,
    activity: str | None,
    sub_activity: str | None,
    prompt_text: str,
    persistent_instruction_prefix: str | None,
    stream: bool,
    model_id_override: str | None,
    meta: dict[str, Any] | None,
    response_text: str = "",
    error: BaseException | None = None,
) -> None:
    if not span_id:
        return
    try:
        from src.wanderly import llm_telemetry as lt

        lt.record_llm_end(
            span_id,
            activity=(activity or "").strip() or "unknown",
            sub_activity=(sub_activity or "").strip() or "unknown",
            started_at_perf=started_at_perf,
            prompt_text=prompt_text,
            persistent_instruction_prefix=persistent_instruction_prefix,
            stream=stream,
            model_id=(model_id_override or "").strip() or None,
            meta=meta,
            status="error" if error else "ok",
            response_text=response_text,
            error=error,
        )
    except Exception:
        pass


def run_prompt_collect_assistant_text(
    client: httpx.Client,
    prompt_text: str,
    *,
    model_id_override: str | None = None,
    existing_agent_id: str | None = None,
    persistent_instruction_prefix: str | None = None,
    on_new_agent: Callable[[str], None] | None = None,
    on_agent_cleared: Callable[[], None] | None = None,
    cursor_trace_path: str | None = None,
    cursor_run_lock_key: str | None = None,
    llm_telemetry_activity: str | None = None,
    llm_telemetry_sub_activity: str | None = None,
    llm_telemetry_meta: dict[str, Any] | None = None,
) -> str:
    """Create or continue a Cloud Agent run, consume the assistant SSE stream, return full text."""
    span_id, t0 = _llm_telemetry_maybe_start(
        activity=llm_telemetry_activity,
        sub_activity=llm_telemetry_sub_activity,
        prompt_text=prompt_text,
        persistent_instruction_prefix=persistent_instruction_prefix,
        stream=False,
        model_id_override=model_id_override,
        meta=llm_telemetry_meta,
    )
    tp = (cursor_trace_path or "").strip() or None
    result = ""
    telemetry_err: BaseException | None = None
    try:
        with _cursor_lane_run_lock(cursor_run_lock_key):
            agent_id, run_id = _begin_cloud_agent_run(
                client,
                prompt_text,
                model_id_override=model_id_override,
                existing_agent_id=existing_agent_id,
                persistent_instruction_prefix=persistent_instruction_prefix,
                on_new_agent=on_new_agent,
                on_agent_cleared=on_agent_cleared,
                cursor_trace_path=tp,
            )
            time.sleep(1.0)
            last_err: Exception | None = None
            for attempt in range(8):
                try:
                    text = consume_sse_assistant(client, agent_id, run_id)
                    result = _assistant_text_or_retry_sse(client, agent_id, run_id, text)
                    break
                except httpx.HTTPStatusError as e:
                    last_err = e
                    sc = e.response.status_code if e.response is not None else 0
                    _trace_cursor(
                        f"sse_http_error mode=buffered run_tail={run_id[-8:]!r} status={sc} attempt={attempt + 1} "
                        f"body_snip={_httpx_err_snip(e)!r}",
                        path=tp,
                    )
                    if e.response is not None and e.response.status_code in _RETRYABLE_SSE_STATUS:
                        time.sleep(_sse_retry_sleep_seconds(attempt))
                        continue
                    raise
                except Exception as e:
                    _trace_cursor(
                        f"sse_transport_error mode=buffered run_tail={run_id[-8:]!r} "
                        f"type={type(e).__name__} message={str(e)[:180]!r}",
                        path=tp,
                    )
                    raise
            else:
                if last_err:
                    if isinstance(last_err, httpx.HTTPStatusError) and last_err.response is not None:
                        sc = last_err.response.status_code
                        if sc in _RETRYABLE_SSE_STATUS:
                            polled = poll_run_for_assistant_text(client, agent_id, run_id)
                            if polled.strip():
                                _trace_cursor(
                                    f"sse_retries_exhausted_poll_ok mode=buffered sc={sc} run_tail={str(run_id)[-8:]!r}",
                                    path=tp,
                                )
                                result = polled
                    if not result:
                        raise last_err
    except BaseException as e:
        telemetry_err = e
        raise
    finally:
        _llm_telemetry_maybe_end(
            span_id,
            t0,
            activity=llm_telemetry_activity,
            sub_activity=llm_telemetry_sub_activity,
            prompt_text=prompt_text,
            persistent_instruction_prefix=persistent_instruction_prefix,
            stream=False,
            model_id_override=model_id_override,
            meta=llm_telemetry_meta,
            response_text=result,
            error=telemetry_err,
        )
    return result


def run_prompt_collect_assistant_text_streaming(
    client: httpx.Client,
    prompt_text: str,
    *,
    emit: Callable[[dict[str, Any]], None] | None = None,
    model_id_override: str | None = None,
    existing_agent_id: str | None = None,
    persistent_instruction_prefix: str | None = None,
    on_new_agent: Callable[[str], None] | None = None,
    on_agent_cleared: Callable[[], None] | None = None,
    cursor_trace_path: str | None = None,
    cursor_run_lock_key: str | None = None,
    llm_telemetry_activity: str | None = None,
    llm_telemetry_sub_activity: str | None = None,
    llm_telemetry_meta: dict[str, Any] | None = None,
) -> str:
    """Create or continue a Cloud Agent run, stream assistant SSE, invoke emit() for progress, return full text."""
    span_id, t0 = _llm_telemetry_maybe_start(
        activity=llm_telemetry_activity,
        sub_activity=llm_telemetry_sub_activity,
        prompt_text=prompt_text,
        persistent_instruction_prefix=persistent_instruction_prefix,
        stream=True,
        model_id_override=model_id_override,
        meta=llm_telemetry_meta,
    )
    tp = (cursor_trace_path or "").strip() or None
    result = ""
    telemetry_err: BaseException | None = None

    def _emit(obj: dict[str, Any]) -> None:
        if emit:
            emit(obj)

    eff_existing = (existing_agent_id or "").strip() or None

    def _stream_collect() -> str:
        nonlocal eff_existing
        for outer_pass in range(2):
            sse_state: dict[str, bool] = {"suppressed_recoverable": False}

            def _cursor_stream_emit(ev: dict[str, Any]) -> None:
                if ev.get("type") == "stream_error":
                    raw = str(ev.get("message") or "")
                    if _is_recoverable_cursor_stream_sse_message(raw):
                        sse_state["suppressed_recoverable"] = True
                        _trace_cursor(
                            "suppressed_recoverable_stream_error "
                            f"msg={raw[:160]!r} outer_pass={outer_pass}",
                            path=tp,
                        )
                        return
                _emit(ev)

            with _cursor_lane_run_lock(cursor_run_lock_key):
                _emit({"type": "phase", "message": "Understanding what you want from this trip…"})
                agent_id, run_id = _begin_cloud_agent_run(
                    client,
                    prompt_text,
                    model_id_override=model_id_override,
                    existing_agent_id=eff_existing,
                    persistent_instruction_prefix=persistent_instruction_prefix,
                    on_new_agent=on_new_agent,
                    on_agent_cleared=on_agent_cleared,
                    cursor_trace_path=tp,
                )
                _emit({"type": "phase", "message": "Exploring destinations and tradeoffs for you…"})
                time.sleep(1.0)
                _emit({"type": "phase", "message": "Generating structured recommendations…"})
                last_err: Exception | None = None
                final_text = ""
                for attempt in range(8):
                    try:
                        _emit({"type": "phase", "message": "Listening to the model as it works…"})
                        text = consume_sse_assistant_streaming(
                            client, agent_id, run_id, emit=_cursor_stream_emit
                        )
                        if not (text or "").strip():
                            _emit({"type": "phase", "message": "Almost there — wrapping up…"})
                        final_text = _assistant_text_or_retry_sse(client, agent_id, run_id, text)
                        break
                    except httpx.HTTPStatusError as e:
                        last_err = e
                        sc = e.response.status_code if e.response is not None else 0
                        _trace_cursor(
                            f"sse_http_error mode=streaming run_tail={run_id[-8:]!r} status={sc} attempt={attempt + 1} "
                            f"body_snip={_httpx_err_snip(e)!r}",
                            path=tp,
                        )
                        if e.response is not None and e.response.status_code in _RETRYABLE_SSE_STATUS:
                            _emit(
                                {
                                    "type": "phase",
                                    "message": "Still connecting — trying again in a moment…",
                                }
                            )
                            time.sleep(_sse_retry_sleep_seconds(attempt))
                            continue
                        raise
                    except Exception as e:
                        _trace_cursor(
                            f"sse_transport_error mode=streaming run_tail={run_id[-8:]!r} "
                            f"type={type(e).__name__} message={str(e)[:180]!r}",
                            path=tp,
                        )
                        raise
                if last_err:
                    if isinstance(last_err, httpx.HTTPStatusError) and last_err.response is not None:
                        sc = last_err.response.status_code
                        if sc in _RETRYABLE_SSE_STATUS:
                            polled = poll_run_for_assistant_text(client, agent_id, run_id)
                            if polled.strip():
                                _trace_cursor(
                                    f"sse_retries_exhausted_poll_ok mode=streaming sc={sc} run_tail={str(run_id)[-8:]!r}",
                                    path=tp,
                                )
                                return polled
                    raise last_err
                if (final_text or "").strip():
                    return final_text

            want_retry = sse_state["suppressed_recoverable"] or bool(eff_existing)
            if outer_pass == 0 and on_agent_cleared is not None and want_retry:
                on_agent_cleared()
                eff_existing = None
                _emit(
                    {
                        "type": "phase",
                        "message": "The AI session refreshed — retrying your request…",
                    }
                )
                continue
            return final_text
        return ""

    try:
        result = _stream_collect()
    except BaseException as e:
        telemetry_err = e
        raise
    finally:
        _llm_telemetry_maybe_end(
            span_id,
            t0,
            activity=llm_telemetry_activity,
            sub_activity=llm_telemetry_sub_activity,
            prompt_text=prompt_text,
            persistent_instruction_prefix=persistent_instruction_prefix,
            stream=True,
            model_id_override=model_id_override,
            meta=llm_telemetry_meta,
            response_text=result,
            error=telemetry_err,
        )
    return result


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
            if e.response is not None and e.response.status_code in _RETRYABLE_SSE_STATUS:
                time.sleep(_sse_retry_sleep_seconds(attempt))
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
