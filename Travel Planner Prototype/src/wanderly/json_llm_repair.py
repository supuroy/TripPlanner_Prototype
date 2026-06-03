"""Shared fixes for malformed JSON fragments in LLM assistant output."""

from __future__ import annotations

import json
import re
from typing import Any


def repair_llm_json_blob(s: str) -> str:
    """
    Fix token sequences that break ``json.loads`` / ``JSONDecoder.raw_decode``.

    Example seen in production: ``"editable":able":true`` (merged / truncated ``true``).
    """
    if not s:
        return s
    s = re.sub(
        r'"editable"\s*:\s*able"\s*:\s*true\b',
        '"editable": true',
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r'"editable"\s*:\s*able"\s*:\s*false\b',
        '"editable": false',
        s,
        flags=re.IGNORECASE,
    )
    return s


def extract_first_json_object(text: str) -> dict[str, Any] | None:
    """
    Parse the first complete JSON object in ``text``.

    Models sometimes emit two identical objects back-to-back (``}{`` with no separator);
    greedy ``{``..``}`` slicing would include both and fail ``json.loads``.
    """
    t = repair_llm_json_blob((text or "").strip())
    if not t:
        return None
    dec = json.JSONDecoder()
    try:
        out, _ = dec.raw_decode(t)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.IGNORECASE)
    if m:
        inner = repair_llm_json_blob(m.group(1).strip())
        try:
            out, _ = dec.raw_decode(inner)
            return out if isinstance(out, dict) else None
        except json.JSONDecodeError:
            pass
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            out, _end = dec.raw_decode(t, i)
            if isinstance(out, dict):
                return out
        except json.JSONDecodeError:
            continue
    return None
