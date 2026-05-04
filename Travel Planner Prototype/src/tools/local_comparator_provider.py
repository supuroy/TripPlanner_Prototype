from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request
from urllib.error import URLError

from pydantic import BaseModel, Field

from src.tools.base_provider import BaseProvider


class ComparisonItemPayload(BaseModel):
    option_id: str
    name: str
    destination: Optional[str] = None
    pros: List[str] = Field(default_factory=list)
    cons: List[str] = Field(default_factory=list)
    fit_note: str


class ComparisonPayload(BaseModel):
    summary: str
    recommendation: str
    option_notes: List[ComparisonItemPayload] = Field(default_factory=list)


class LocalComparatorProvider(BaseProvider):
    name = "local_comparator_provider"

    def __init__(
        self,
        model_name: str = "llama3.2:3b",
        api_url: str = "http://127.0.0.1:11434/api/generate",
        timeout_seconds: int = 45,
    ) -> None:
        self.model_name = model_name
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds
        self.ollama_exe = self._detect_ollama_exe()
        self._shutdown = False
        self._proc_lock = threading.Lock()
        self._active_proc: Optional[subprocess.Popen] = None

    def fetch(self, query: Dict[str, Any]) -> Dict[str, Any]:
        if self._shutdown:
            raise RuntimeError("Local comparator is shutting down.")
        traveler_profile = query.get("traveler_profile", {})
        itinerary_options = query.get("itinerary_options", [])
        print(
            f"[local-comparator] fetch called model={self.model_name} options={len(itinerary_options)} "
            f"ollama_exe={'set' if self.ollama_exe else 'missing'}"
        )

        prompt = f"""
You are a local travel comparison assistant.
Return strict JSON matching this schema:
{json.dumps(ComparisonPayload.model_json_schema(), ensure_ascii=True)}

Traveler profile:
{traveler_profile}

Open itinerary options:
{itinerary_options}

Rules:
- Keep the summary short (3-5 sentences).
- Compare options based on fit, constraints, weather/reviews/activities enrichment, and tradeoffs.
- `recommendation` should name one option or suggest a tie-break criterion.
- Keep pros/cons concise and actionable.
"""

        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        text = self._generate_text(payload)
        print(f"[local-comparator] raw output length={len(text)}")
        parsed = self._parse_json_payload(text)
        structured = ComparisonPayload.model_validate(parsed)
        return {
            "status": "completed",
            "summary": structured.summary,
            "recommendation": structured.recommendation,
            "option_notes": [n.model_dump() for n in structured.option_notes],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model": self.model_name,
            "error": None,
        }

    def _generate_text(self, payload: Dict[str, Any]) -> str:
        if self._shutdown:
            raise RuntimeError("Local comparator is shutting down.")
        req = request.Request(
            self.api_url,
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
        )
        try:
            print(f"[local-comparator] trying Ollama HTTP API at {self.api_url}")
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                print("[local-comparator] Ollama HTTP API response received")
                return (body.get("response") or "").strip()
        except TimeoutError:
            print("[local-comparator] Ollama HTTP API timed out")
            raise
        except URLError:
            if self._shutdown:
                raise RuntimeError("Local comparator is shutting down.")
            print("[local-comparator] Ollama HTTP API unavailable; attempting CLI fallback")
            if not self.ollama_exe:
                raise
            with self._proc_lock:
                if self._shutdown:
                    raise RuntimeError("Local comparator is shutting down.")
                self._active_proc = subprocess.Popen(
                    [self.ollama_exe, "run", self.model_name, payload["prompt"]],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                proc = self._active_proc
            try:
                stdout, stderr = proc.communicate(timeout=self.timeout_seconds)
            finally:
                with self._proc_lock:
                    self._active_proc = None
            if proc.returncode != 0:
                raise RuntimeError(f"Ollama CLI failed rc={proc.returncode}: {(stderr or '')[:500]}")
            print("[local-comparator] Ollama CLI fallback response received")
            output = (stdout or "").strip()
            if output:
                return output
            raise ValueError(f"Ollama CLI returned empty output. stderr={(stderr or '').strip()[:500]}")

    @staticmethod
    def _detect_ollama_exe() -> Optional[str]:
        candidates = [
            os.environ.get("OLLAMA_EXE"),
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"),
            str(Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama.exe"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    @staticmethod
    def _parse_json_payload(text: str) -> Dict[str, Any]:
        raw = (text or "").strip()
        if not raw:
            raise ValueError("Comparator returned empty text.")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = raw[start : end + 1]
                return json.loads(candidate)
            preview = raw[:500].replace("\n", "\\n")
            raise ValueError(f"Comparator returned non-JSON output. Preview: {preview}")

    def shutdown(self) -> None:
        self._shutdown = True
        with self._proc_lock:
            proc = self._active_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
