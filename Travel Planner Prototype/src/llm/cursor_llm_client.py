"""Planner LLM client backed by Cursor Cloud Agents (no Google / Gemini API)."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from json import JSONDecodeError
from pathlib import Path
from typing import Type, TypeVar

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

from src.integrations.cursor_agent_client import (
    basic_client,
    get_api_key,
    load_env_from_roots,
    run_prompt_collect_assistant_text,
)

T = TypeVar("T", bound=BaseModel)


class CursorLlmClient:
    """
    Drop-in replacement for the former Gemini-backed client: same public methods used by
    stages, tools, and orchestrator (generate_structured / generate_text, replay cache, etc.).
    All live calls go through Cursor Cloud Agents HTTP + SSE (see cursor_agent_client).
    """

    def __init__(
        self,
        cache_path: str | None = None,
        api_key: str | None = None,
    ) -> None:
        load_dotenv()
        load_env_from_roots()
        self._api_key_override = (api_key or "").strip() or None
        # Note: API key is validated on first call to _key(), allowing web server preview/hosting
        # to bind ports and serve health checks even before secrets are accessed.
        default_cache = os.environ.get("CURSOR_PLANNER_CACHE_PATH", ".cache/cursor_planner_cache.json")
        self.cache_path = Path(cache_path or default_cache)
        self.model_name = "cursor-cloud-agent"
        self.replay_mode = False
        self.last_call_info: dict = {}
        self.request_interval_seconds = float(os.getenv("CURSOR_MIN_INTERVAL_SECONDS", "2.0"))
        self.max_retry_attempts = int(os.getenv("CURSOR_MAX_RETRY_ATTEMPTS", "4"))
        self._rate_lock = threading.Lock()
        self._last_request_ts = 0.0

    def _key(self) -> str:
        return self._api_key_override or get_api_key()

    def set_replay_mode(self, enabled: bool) -> None:
        self.replay_mode = bool(enabled)

    def is_replay_mode(self) -> bool:
        return self.replay_mode

    def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        cache_key: str | None = None,
        metadata: dict | None = None,
        use_cache_if_available: bool = False,
    ) -> T:
        key = cache_key or "__last_call__"
        cache = self._read_cache()
        if use_cache_if_available:
            cached = cache.get(key)
            if cached and isinstance(cached.get("response_json"), dict):
                self.last_call_info = {
                    "cache_key": key,
                    "used_cache": True,
                    "replay_fallback_live": False,
                    "message": f"Cache hit for '{key}' (cache-first mode).",
                }
                return schema.model_validate(cached["response_json"])
        if self.replay_mode:
            cached = cache.get(key)
            if cached and isinstance(cached.get("response_json"), dict):
                self.last_call_info = {
                    "cache_key": key,
                    "used_cache": True,
                    "replay_fallback_live": False,
                    "message": f"Replay cache hit for '{key}'.",
                }
                return schema.model_validate(cached["response_json"])
            self.last_call_info = {
                "cache_key": key,
                "used_cache": False,
                "replay_fallback_live": True,
                "message": f"Replay cache miss for '{key}'. Using live Cursor agent.",
            }
        else:
            self.last_call_info = {
                "cache_key": key,
                "used_cache": False,
                "replay_fallback_live": False,
                "message": f"Live Cursor agent call for '{key}'.",
            }

        payload = self._generate_json_with_retry(prompt=prompt, cache_key=key)
        cache[key] = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "model": self.model_name,
            "prompt": prompt,
            "response_json": payload,
            "metadata": metadata or {},
        }
        cache["__last_call__"] = cache[key]
        self._write_cache(cache)
        return schema.model_validate(payload)

    def generate_text(
        self,
        prompt: str,
        cache_key: str | None = None,
        metadata: dict | None = None,
        use_cache_if_available: bool = False,
    ) -> str:
        key = cache_key or "__last_text_call__"
        cache = self._read_cache()
        if use_cache_if_available:
            cached = cache.get(key)
            if cached and isinstance(cached.get("response_text"), str):
                self.last_call_info = {
                    "cache_key": key,
                    "used_cache": True,
                    "replay_fallback_live": False,
                    "message": f"Cache hit for '{key}' (cache-first mode).",
                }
                return cached["response_text"]
        if self.replay_mode:
            cached = cache.get(key)
            if cached and isinstance(cached.get("response_text"), str):
                self.last_call_info = {
                    "cache_key": key,
                    "used_cache": True,
                    "replay_fallback_live": False,
                    "message": f"Replay cache hit for '{key}'.",
                }
                return cached["response_text"]
            self.last_call_info = {
                "cache_key": key,
                "used_cache": False,
                "replay_fallback_live": True,
                "message": f"Replay cache miss for '{key}'. Using live Cursor agent.",
            }
        else:
            self.last_call_info = {
                "cache_key": key,
                "used_cache": False,
                "replay_fallback_live": False,
                "message": f"Live Cursor agent call for '{key}'.",
            }

        raw_text = self._call_cursor_with_retries(prompt, key).strip()
        raw_text = self._strip_code_fence(raw_text)
        cache[key] = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "model": self.model_name,
            "prompt": prompt,
            "response_text": raw_text,
            "metadata": metadata or {},
        }
        cache["__last_call__"] = cache[key]
        self._write_cache(cache)
        return raw_text

    def get_cached_call(self, cache_key: str) -> dict | None:
        return self._read_cache().get(cache_key)

    def has_cached_call(self, cache_key: str) -> bool:
        return cache_key in self._read_cache()

    def get_last_call_info(self) -> dict:
        return dict(self.last_call_info)

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            return "\n".join(lines).strip()
        return text

    def _read_cache(self) -> dict:
        if not self.cache_path.exists():
            return {}
        raw = self.cache_path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        return json.loads(raw)

    def _write_cache(self, cache: dict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    def _throttle_if_needed(self, cache_key: str) -> None:
        if self.request_interval_seconds <= 0:
            return
        with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_ts
            wait_for = self.request_interval_seconds - elapsed
            if wait_for > 0:
                self.last_call_info["message"] = (
                    f"Throttling '{cache_key}' for {wait_for:.1f}s between Cursor agent runs."
                )
                time.sleep(wait_for)
            self._last_request_ts = time.monotonic()

    def _call_cursor_once(self, prompt: str, cache_key: str) -> str:
        self._throttle_if_needed(cache_key)
        key = self._key()
        with basic_client(key) as client:
            return run_prompt_collect_assistant_text(
                client, prompt, cursor_trace_path="cursor_llm_client.invoke"
            )

    def _call_cursor_with_retries(self, prompt: str, cache_key: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retry_attempts + 1):
            try:
                return self._call_cursor_once(prompt, cache_key)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                code = exc.response.status_code if exc.response is not None else 0
                if code == 429 and attempt < self.max_retry_attempts:
                    delay = min(5.0 * attempt, 60.0)
                    self.last_call_info["message"] = (
                        f"HTTP 429 from Cursor; retrying in {delay:.1f}s ({attempt}/{self.max_retry_attempts})."
                    )
                    time.sleep(delay)
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                if attempt >= self.max_retry_attempts:
                    raise
                delay = min(2.0 * attempt, 30.0)
                self.last_call_info["message"] = f"Cursor call failed; retry in {delay:.1f}s: {exc!r}"
                time.sleep(delay)
        raise last_exc if last_exc else RuntimeError("Unknown Cursor LLM failure")

    def _generate_json_with_retry(self, prompt: str, cache_key: str) -> dict:
        attempts = [
            prompt,
            (
                prompt
                + "\n\nIMPORTANT:\n"
                + "- The previous output was invalid/truncated JSON.\n"
                + "- Return ONLY valid, complete JSON.\n"
                + "- No markdown, no comments, no trailing text.\n"
            ),
        ]
        last_error: Exception | None = None
        for idx, attempt_prompt in enumerate(attempts, start=1):
            raw_text = self._call_cursor_with_retries(attempt_prompt, cache_key).strip()
            raw_text = self._strip_code_fence(raw_text)
            try:
                return self._parse_json_payload(raw_text=raw_text, cache_key=cache_key)
            except Exception as exc:
                last_error = exc
                self.last_call_info["message"] = (
                    f"Attempt {idx} produced invalid JSON for '{cache_key}'; retrying with stricter instruction."
                )
        raise ValueError(f"Cursor agent failed to return valid JSON after retries for '{cache_key}': {last_error}")

    @staticmethod
    def _parse_json_payload(raw_text: str, cache_key: str) -> dict:
        try:
            return json.loads(raw_text)
        except JSONDecodeError:
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = raw_text[start : end + 1]
                try:
                    return json.loads(candidate)
                except JSONDecodeError:
                    pass
            preview = raw_text[:500].replace("\n", "\\n")
            raise ValueError(
                f"LLM returned invalid JSON for cache_key='{cache_key}'. Preview: {preview}"
            ) from None
