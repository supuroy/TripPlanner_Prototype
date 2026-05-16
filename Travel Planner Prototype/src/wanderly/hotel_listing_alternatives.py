"""Top-N hotel picks for UI (ranked_picks + selected_rank on the returned hotel card)."""

from __future__ import annotations

import re
from typing import Any, Callable

MAX_HOTEL_RANKED_PICKS = 3

_PICK_FIELDS = (
    "name",
    "area",
    "price",
    "image",
    "listing_url",
    "guest_rating",
    "review_count",
    "rating_label",
    "star_rating",
    "pick_score",
)


def _format_nightly_price(price_display: str) -> str:
    p = (price_display or "").strip()
    if not p:
        return "See Expedia"
    if p.startswith("$"):
        return p[:48]
    return f"{p}/night est."[:48]


def _guest_star_rating(row: dict[str, Any]) -> str | None:
    gr = row.get("guest_rating")
    rl = str(row.get("rating_label") or "").strip()
    rc = row.get("review_count")
    if gr is None and not rl:
        return str(row.get("star_rating") or "").strip()[:80] or None
    bits: list[str] = []
    if gr is not None:
        bits.append(str(gr))
    if rl:
        bits.append(f"({rl})")
    if isinstance(rc, int):
        bits.append(f"{rc} reviews")
    return " ".join(bits)[:80] if bits else None


def llm_listing_row_to_pick(
    row: dict[str, Any],
    *,
    rank: int,
    nights: int,
    area_default: str,
    image_fallback: str = "",
) -> dict[str, Any]:
    """Map one LLM/heuristic listing row to a compact ranked pick."""
    name = str(row.get("name") or "Lodging")[:120]
    area = str(row.get("area_or_neighborhood") or row.get("area") or area_default)[:160]
    price = _format_nightly_price(str(row.get("price_display") or row.get("price") or ""))
    image = (str(row.get("image_url") or row.get("image") or "").strip() or image_fallback)[:800]
    listing_url = (str(row.get("listing_url") or "").strip())[:800]
    pick: dict[str, Any] = {
        "rank": int(rank),
        "name": name,
        "area": area,
        "price": price,
        "nights": int(nights),
        "image": image,
        "listing_url": listing_url,
    }
    if row.get("guest_rating") is not None:
        pick["guest_rating"] = float(row["guest_rating"])
    if row.get("review_count") is not None:
        try:
            pick["review_count"] = int(row["review_count"])
        except (TypeError, ValueError):
            pass
    rl = str(row.get("rating_label") or "").strip()
    if rl:
        pick["rating_label"] = rl[:40]
    sr = _guest_star_rating(row)
    if sr:
        pick["star_rating"] = sr
    if row.get("pick_score") is not None:
        try:
            pick["pick_score"] = round(float(row["pick_score"]), 2)
        except (TypeError, ValueError):
            pass
    elif row.get("score") is not None:
        try:
            pick["pick_score"] = round(float(row["score"]), 2)
        except (TypeError, ValueError):
            pass
    return pick


def ordered_listing_indices_from_rule_rows(
    rule_rows: list[dict[str, Any]],
    *,
    winner_index: int | None,
    max_picks: int = MAX_HOTEL_RANKED_PICKS,
) -> list[int]:
    """Distinct listing indices sorted by rule score (best first)."""
    rows = [r for r in rule_rows if isinstance(r, dict)]
    rows.sort(
        key=lambda r: (
            -float(r.get("score") or 0),
            int(r.get("usd") or 999_999),
            int(r.get("i") if r.get("i") is not None else 999),
        )
    )
    out: list[int] = []
    seen: set[int] = set()
    if winner_index is not None:
        try:
            wi = int(winner_index)
            if wi >= 0:
                out.append(wi)
                seen.add(wi)
        except (TypeError, ValueError):
            pass
    for r in rows:
        try:
            i = int(r.get("i"))
        except (TypeError, ValueError):
            continue
        if i < 0 or i in seen:
            continue
        seen.add(i)
        out.append(i)
        if len(out) >= max_picks:
            break
    return out[:max_picks]


def build_llm_ranked_picks(
    listings_dump: list[dict[str, Any]],
    pick_indices: list[int],
    *,
    nights: int,
    area_hint: str,
    image_fallback: str = "",
    rule_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build up to three picks from parsed LLM listing rows."""
    score_by_i: dict[int, float] = {}
    for r in rule_rows or []:
        if not isinstance(r, dict):
            continue
        try:
            score_by_i[int(r.get("i"))] = float(r.get("score") or 0)
        except (TypeError, ValueError):
            continue
    picks: list[dict[str, Any]] = []
    for rank, i in enumerate(pick_indices[:MAX_HOTEL_RANKED_PICKS], start=1):
        if i < 0 or i >= len(listings_dump):
            continue
        row = dict(listings_dump[i])
        if i in score_by_i:
            row["pick_score"] = score_by_i[i]
        picks.append(
            llm_listing_row_to_pick(
                row,
                rank=rank,
                nights=nights,
                area_default=area_hint,
                image_fallback=image_fallback,
            )
        )
    return picks


def apply_pick_to_hotel_option(ho: dict[str, Any], pick: dict[str, Any]) -> dict[str, Any]:
    """Copy display fields from a ranked pick onto the primary hotel card dict."""
    out = dict(ho)
    for k in _PICK_FIELDS:
        if k in pick and pick[k] is not None and pick[k] != "":
            out[k] = pick[k]
    if pick.get("nights") is not None:
        out["nights"] = int(pick["nights"])
    out["best"] = True
    return out


def attach_ranked_picks_to_hotel_option(
    ho: dict[str, Any],
    ranked_picks: list[dict[str, Any]],
    *,
    selected_rank: int = 1,
) -> dict[str, Any]:
    """
    Attach ``ranked_picks`` (max 3) and apply ``selected_rank`` to top-level card fields.

    Preserves ``ranked_picks`` on the object so the client can swap selection without re-fetch.
    """
    picks = []
    for i, p in enumerate(ranked_picks[:MAX_HOTEL_RANKED_PICKS]):
        if not isinstance(p, dict):
            continue
        row = dict(p)
        row["rank"] = int(row.get("rank") or (i + 1))
        picks.append(row)
    if not picks:
        return ho
    out = dict(ho)
    out["ranked_picks"] = picks
    try:
        sr = int(selected_rank)
    except (TypeError, ValueError):
        sr = 1
    if sr < 1 or sr > len(picks):
        sr = 1
    out["selected_rank"] = sr
    sel = next((p for p in picks if int(p.get("rank") or 0) == sr), picks[0])
    out = apply_pick_to_hotel_option(out, sel)
    out["best"] = True
    return out


def _clean_heuristic_title(title: str) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip())
    t = re.sub(r"^More information about\s+", "", t, flags=re.I)
    t = re.sub(r",\s*opens in a new tab\s*$", "", t, flags=re.I)
    return t[:120] or "Lodging"


def build_heuristic_ranked_picks(
    links: list[dict[str, str]],
    budget_rows: list[dict[str, Any]],
    html: str,
    *,
    nights: int,
    area: str,
    image_fallback: str,
    absolute_href: Callable[[str], str],
    image_near_href: Callable[[str, str], str] | None = None,
    normalize_price: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """Build ranked picks from heuristic budget scoring rows + link list."""
    from src.wanderly.hotel_listing_extract import extract_guest_rating_reviews_near_href

    rows = sorted(
        [r for r in budget_rows if isinstance(r, dict)],
        key=lambda r: (
            -float(r.get("score") or r.get("pick_score") or 0),
            int(r.get("median_usd_near_href") or 999_999),
            int(r.get("original_index") or 999),
        ),
    )
    picks: list[dict[str, Any]] = []
    seen_href: set[str] = set()
    norm = normalize_price or (lambda s: s)

    for r in rows:
        if len(picks) >= MAX_HOTEL_RANKED_PICKS:
            break
        try:
            oi = int(r.get("original_index"))
        except (TypeError, ValueError):
            continue
        if oi < 0 or oi >= len(links):
            continue
        href = (r.get("href_full") or links[oi].get("href") or "").strip()
        if not href:
            continue
        key = href.split("?")[0]
        if key in seen_href:
            continue
        seen_href.add(key)
        title = _clean_heuristic_title(str(links[oi].get("text") or r.get("title") or ""))
        med = r.get("median_usd_near_href")
        if isinstance(med, int) and med > 0:
            price = norm(f"${med}")
        else:
            price = "See Expedia"
        href_abs = absolute_href(href)
        gr_info = extract_guest_rating_reviews_near_href(html, href) if html else {}
        img = ""
        if image_near_href:
            img = (image_near_href(html, href) or "").strip()
        if not img:
            img = (image_fallback or "").strip()
        row: dict[str, Any] = {
            "name": title,
            "area": area,
            "price_display": price,
            "image_url": img[:800],
            "listing_url": href_abs[:800],
            "guest_rating": gr_info.get("guest_rating") if gr_info else r.get("guest_rating"),
            "review_count": gr_info.get("review_count") if gr_info else r.get("review_count"),
            "rating_label": gr_info.get("rating_label") if gr_info else None,
            "pick_score": r.get("score") or r.get("pick_score"),
        }
        picks.append(
            llm_listing_row_to_pick(
                row,
                rank=len(picks) + 1,
                nights=nights,
                area_default=area,
                image_fallback=image_fallback,
            )
        )
    return picks
