"""Heuristic extraction from saved HTML — fragile; for lab demos only."""

from __future__ import annotations

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup


def extract_candidate_links(html: str, *, limit: int = 15) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = " ".join(a.get_text().split())
        if not href or href in seen:
            continue
        # Common Expedia hotel property / listing paths (will drift over time)
        if re.search(r"/(Hotel-Information|h\d+\.|\.h\.|Hotel-Search\?)", href, re.I):
            if len(text) < 3 and "/Hotel-Information" not in href:
                continue
            seen.add(href)
            out.append({"href": href, "text": text[:500]})
        if len(out) >= limit:
            break

    return out


def extract_price_like_strings(html: str, *, limit: int = 30) -> list[str]:
    """Dollar-like tokens for debugging — not a structured price model."""
    found = re.findall(r"\$\s*[\d,]+(?:\.\d{2})?", html)
    dedup: list[str] = []
    for x in found:
        if x not in dedup:
            dedup.append(x)
        if len(dedup) >= limit:
            break
    return dedup


def report_from_file(path: Path, *, link_limit: int = 15) -> dict:
    html = path.read_text(encoding="utf-8", errors="replace")
    return {
        "source": str(path),
        "candidate_links": extract_candidate_links(html, limit=link_limit),
        "price_like_strings_sample": extract_price_like_strings(html),
    }


def print_report(path: Path) -> None:
    print(json.dumps(report_from_file(path), indent=2))
