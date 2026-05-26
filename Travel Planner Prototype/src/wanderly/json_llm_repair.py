"""Shared fixes for malformed JSON fragments in LLM assistant output."""

from __future__ import annotations

import re


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
