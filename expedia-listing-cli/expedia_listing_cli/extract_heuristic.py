"""Heuristic extraction from saved HTML — fragile; for lab demos only."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup


def _urls_from_raw_hotel_info(html: str, *, limit: int) -> list[dict[str, str]]:
    """
    Fallback when Expedia renders listing URLs in JSON/script blobs instead of anchor tags.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    patterns = (
        r"https?://www\.expedia\.com/Hotel-Information[^\"'\\<\s]+",
        r"/Hotel-Information[^\"'\\<\s]+",
    )
    for pat in patterns:
        for raw in re.findall(pat, html, flags=re.I):
            href = raw.replace("\\u0026", "&").replace("%26", "&")
            href = href.replace("\\/", "/")
            href = unquote(href)
            if not href or href in seen:
                continue
            seen.add(href)
            out.append({"href": href, "text": "Hotel listing"})
            if len(out) >= limit:
                return out
    return out


def extract_candidate_links(html: str, *, limit: int = 15) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = " ".join(a.get_text().split())
        if not href or href in seen:
            continue
        # Common Expedia hotel property / listing paths (will drift over time).
        # Keep this intentionally broad; strict slash-anchored patterns miss many valid hrefs.
        is_hotel_listing = (
            "Hotel-Information" in href
            or ".h." in href
            or re.search(r"\.h\d+\.", href, re.I) is not None
            or "Hotel-Search?" in href
        )
        if is_hotel_listing:
            if len(text) < 3 and "Hotel-Information" not in href:
                continue
            seen.add(href)
            out.append({"href": href, "text": text[:500]})
        if len(out) >= limit:
            break

    if out:
        return out

    # Fallback: hydrate from raw HTML/script payloads.
    out = _urls_from_raw_hotel_info(html, limit=limit)
    return out


def extract_document_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    t = soup.find("title")
    if not t:
        return None
    txt = t.get_text(" ", strip=True)
    return txt if txt else None


def extract_hotel_srp_image_url(html: str) -> str | None:
    """
    Best-effort hero/thumbnail from hotel SRP HTML when og:image is absent (Expedia embeds CDN URLs in markup/JSON).
    """
    host_pat = r"https://[a-z0-9.-]*(?:travel-assets|expedia)\.com[^\"'\\<\s)]+\.(?:jpg|jpeg|png|webp)"
    best: str | None = None
    for m in re.finditer(host_pat, html, flags=re.I):
        u = m.group(0).rstrip("\\).,;")
        low = u.lower()
        if "favicon" in low or "/icons/" in low:
            continue
        if len(u) < 40:
            continue
        # Prefer obvious property/listing imagery paths over tiny UI sprites.
        if "hotel" in low or "lodging" in low or re.search(r"\.h\d+\.", u, re.I) or "roomsand" in low:
            return u[:800]
        best = best or u[:800]
    if best:
        return best
    m = re.search(
        r'"(?:thumbnailUrl|imageUrl|heroImageUrl)"\s*:\s*"(https:[^"]+)"',
        html,
        flags=re.I,
    )
    if m:
        u = m.group(1).replace("\\/", "/")
        if u.startswith("http") and ("expedia" in u.lower() or "travel-assets" in u.lower()):
            return u[:800]
    return None


def extract_og_image_url(html: str) -> str | None:
    """First og:image / twitter:image with an http(s) URL (hotel SRPs often expose a lead image)."""
    soup = BeautifulSoup(html, "html.parser")
    for prop in ("og:image", "twitter:image"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            u = str(tag["content"]).strip()
            if u.startswith("http"):
                return u
    return None


def extract_flight_airline_filter_hints(html: str, *, limit: int = 16) -> list[dict[str, str]]:
    """
    Best-effort airline + price hints from an Expedia flight SRP HTML snapshot.

    1. **Filter checkboxes** (older markup): ``aria-label="6 Alaska Airlines flights from $412"`` with
       ``value="AS"``.
    2. **Listing copy** (newer markup): ``Alaska Airlines flight, departing at 4:15pm, arriving at 7:19pm,
       priced at $412`` embedded in ``aria-label`` / visible strings.

    Logos are usually not present as ``<img>`` tags in the initial HTML; callers should treat ``iata`` as
    optional and resolve artwork separately when needed.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    lab_re = re.compile(
        r"^(\d+)\s+(.+?)\s+flights\s+from\s+(\$\s*[\d,]+(?:\.\d{2})?)\s*$",
        re.I,
    )
    for inp in soup.find_all("input"):
        name = str(inp.get("name") or "")
        if "PREFERRED_AIRLINE" not in name.upper():
            continue
        raw = str(inp.get("aria-label") or "").strip()
        m = lab_re.match(raw)
        if not m:
            continue
        airline = m.group(2).strip()
        price = re.sub(r"\s+", "", m.group(3))
        iata = str(inp.get("value") or "").strip().upper()
        key = (airline, price)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "source": "preferred_airline_checkbox",
                "flight_count": m.group(1),
                "airline": airline,
                "min_price": price,
                "iata": iata,
            }
        )
        if len(out) >= limit:
            return out

    snippet_re = re.compile(
        r"(?P<airline>[A-Za-z0-9][A-Za-z0-9 &'\.\-]+?)\s+flight,?\s+"
        r"departing\s+at\s+(?P<dep>[^,]+),\s*"
        r"arriving\s+at\s+(?P<arr>[^,]+),\s*"
        r"priced\s+at\s+(?P<price>\$\s*[\d,]+(?:\.\d{2})?)",
        re.I,
    )
    for m in snippet_re.finditer(html):
        airline = " ".join(m.group("airline").split())
        if airline.lower().startswith("select "):
            airline = airline[7:].strip()
        price = re.sub(r"\s+", "", m.group("price"))
        key = (airline, price)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "source": "listing_snippet",
                "airline": airline[:120],
                "min_price": price,
                "departing": m.group("dep").strip()[:48],
                "arriving": m.group("arr").strip()[:48],
                "iata": "",
            }
        )
        if len(out) >= limit:
            break
    return out


def extract_flight_duration_guess(html: str) -> str | None:
    """
    Best-effort total-duration token from visible text (lab heuristic; Expedia markup changes often).
    """
    soup = BeautifulSoup(html, "html.parser")
    for junk in soup(["script", "style", "noscript"]):
        junk.decompose()
    text = soup.get_text(" ", strip=True)
    if len(text) > 500_000:
        text = text[:500_000]
    for pat in (
        r"\b(\d+)\s*h(?:r)?\s+(\d+)\s*m(?:in)?\b",
        r"\b(\d+)\s*hr\s+(\d+)\s*min\b",
        r"\b(\d+)\s*h\s+(\d+)\s*m\b",
    ):
        m = re.search(pat, text, re.I)
        if m:
            return f"{m.group(1)}h {m.group(2)}m"
    m = re.search(r"\b(\d+)\s*h(?:r)?\b(?!\s*\d+\s*m)", text, re.I)
    if m:
        return f"{m.group(1)}h+"
    return None


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
