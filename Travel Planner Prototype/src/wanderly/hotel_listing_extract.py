"""Extract guest rating / review count from Expedia hotel SRP HTML near listing anchors."""

from __future__ import annotations

import re
from typing import Any

_GUEST_RATING_BADGE_RE = re.compile(
    r'uitk-badge-base-text["\']?\s+aria-hidden=["\']true["\']>(\d(?:\.\d)?)</span>',
    re.I,
)
_RATING_OUT_OF_10_RE = re.compile(r"(\d(?:\.\d)?)\s*out of\s*10", re.I)
_REVIEWS_RE = re.compile(
    r'aria-hidden=["\']true["\']>(\d[\d,]*)\s*reviews</span>',
    re.I,
)
_RATING_LABEL_RE = re.compile(
    r'aria-hidden=["\']true["\']>(Wonderful|Excellent|Very good|Good|Fair|Poor)</span>',
    re.I,
)


def _find_listing_href_ix(html: str, href: str) -> int:
    h = (href or "").strip()
    if not h or not html:
        return -1
    needles: list[str] = [h]
    if "&amp;" in h:
        needles.append(h.replace("&amp;", "&"))
    if "&" in h:
        needles.append(h.replace("&", "&amp;"))
    if h.startswith("http"):
        m = re.search(r"https?://[^/]+(/[^?#]+)", h, re.I)
        if m:
            needles.append(m.group(1))
    else:
        needles.append(h.lstrip("/"))
    q = h.find("?")
    if q > 0:
        needles.append(h[:q])
    pid_m = re.search(r"(\.h\d+\.Hotel-Information)", h, re.I)
    if pid_m:
        needles.append(pid_m.group(1))
    seen: set[str] = set()
    for nd in needles:
        if not nd or nd in seen:
            continue
        seen.add(nd)
        ix = html.find(nd)
        if ix >= 0:
            if nd.startswith(".h") and pid_m:
                path_start = html.rfind('href="', max(0, ix - 400), ix)
                if path_start >= 0:
                    return path_start + 6
            return ix
    return -1


def _closest_match(
    chunk: str,
    anchor_pos: int,
    pattern: re.Pattern[str],
    *,
    validator,
) -> re.Match[str] | None:
    best: re.Match[str] | None = None
    best_dist = 10**9
    for m in pattern.finditer(chunk):
        if not validator(m):
            continue
        dist = abs(m.start() - anchor_pos)
        if dist < best_dist:
            best_dist = dist
            best = m
    return best


def extract_guest_rating_reviews_near_href(
    html: str,
    href: str,
    *,
    before: int = 4_000,
    after: int = 22_000,
) -> dict[str, Any]:
    """
    Best-effort guest score (0–10), review count, and label (e.g. Wonderful) for one SRP card.

    Scans HTML near the property ``Hotel-Information`` href (rating block is usually after the link).
    """
    ix = _find_listing_href_ix(html, href)
    if ix < 0:
        return {}
    lo = max(0, ix - before)
    hi = min(len(html), ix + after)
    chunk = html[lo:hi]
    anchor_pos = ix - lo

    # Limit to this card: from anchor through the next property link (or window end).
    next_prop = re.search(
        r'\.h\d+\.Hotel-Information',
        chunk[anchor_pos + 80 :],
        re.I,
    )
    card_end = anchor_pos + 80 + next_prop.start() if next_prop else len(chunk)
    card_lo = max(0, anchor_pos - 2_500)
    card_chunk = chunk[card_lo:card_end]
    card_anchor = anchor_pos - card_lo

    out: dict[str, Any] = {}

    rm = _closest_match(
        card_chunk,
        card_anchor,
        _GUEST_RATING_BADGE_RE,
        validator=lambda m: 0 < float(m.group(1)) <= 10,
    )
    if rm is None:
        rm = _closest_match(
            card_chunk,
            card_anchor,
            _RATING_OUT_OF_10_RE,
            validator=lambda m: 0 < float(m.group(1)) <= 10,
        )
    if rm is not None:
        out["guest_rating"] = round(float(rm.group(1)), 1)

    vm = _closest_match(
        card_chunk,
        card_anchor,
        _REVIEWS_RE,
        validator=lambda m: int(m.group(1).replace(",", "")) >= 0,
    )
    if vm is not None:
        out["review_count"] = int(vm.group(1).replace(",", ""))

    lm = _closest_match(
        card_chunk,
        card_anchor,
        _RATING_LABEL_RE,
        validator=lambda _m: True,
    )
    if lm is not None:
        out["rating_label"] = lm.group(1).strip()

    return out


def enrich_hotel_listing_row_from_html(row: dict[str, Any], html: str) -> dict[str, Any]:
    """Fill ``guest_rating`` / ``review_count`` / ``rating_label`` on a listing dict when missing."""
    if not isinstance(row, dict) or not html:
        return row
    href = str(row.get("listing_url") or row.get("href") or "").strip()
    if not href:
        return row
    ext = extract_guest_rating_reviews_near_href(html, href)
    if not ext:
        return row
    merged = dict(row)
    for key in ("guest_rating", "review_count", "rating_label"):
        if key in ext and ext[key] is not None:
            merged[key] = ext[key]
    if ext.get("guest_rating") is not None or ext.get("review_count") is not None:
        label = ext.get("rating_label") or merged.get("rating_label") or ""
        gr = merged.get("guest_rating")
        rc = merged.get("review_count")
        parts: list[str] = []
        if gr is not None:
            parts.append(str(gr))
        if label:
            parts.append(f"({label})")
        if rc is not None:
            parts.append(f"{rc} reviews")
        if parts and not merged.get("star_rating"):
            merged["star_rating"] = " ".join(parts)[:80]
    return merged
