"""
AllTrails search context — persisted on the destination at itinerary build (OTA-style).

The LLM emits structured fields so later Apify lookups use the correct park/region/trail,
without a hardcoded trail registry in code.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from src.wanderly.stock_photo_heroes import infer_hero_park_phrase

_ALLTRAILS_TRAIL_RE = re.compile(
    r"https?://(?:www\.)?alltrails\.com/(?:explore/)?trail/[^\s\"')]+",
    re.I,
)


def _clean(s: Any, *, limit: int = 240) -> str:
    return str(s or "").strip()[:limit]


def extract_alltrails_url_from_text(text: str | None) -> str | None:
    if not text:
        return None
    m = _ALLTRAILS_TRAIL_RE.search(text)
    return m.group(0).rstrip(".,)") if m else None


_VERIFICATION_STATUSES = frozenset({"verified", "unverified", "provider_unavailable", "catalog"})


def normalize_alltrails_search_context(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    trail_name = _clean(raw.get("trail_name"), limit=120)
    if not trail_name:
        return None
    park = _clean(raw.get("park_name"), limit=120) or None
    region = _clean(raw.get("region"), limit=80) or None
    country = _clean(raw.get("country"), limit=80) or None
    parent = extract_alltrails_url_from_text(_clean(raw.get("parent_alltrails_url"), limit=500))
    if not parent:
        parent = extract_alltrails_url_from_text(_clean(raw.get("alltrails_url"), limit=500))
    search_query = _clean(raw.get("search_query"), limit=240)
    if not search_query:
        parts = [p for p in (park, trail_name, region, country) if p]
        search_query = " ".join(parts)[:240]
    disambiguation = _clean(raw.get("disambiguation"), limit=320) or None
    out: dict[str, Any] = {
        "trail_name": trail_name,
        "search_query": search_query,
    }
    if park:
        out["park_name"] = park
    if region:
        out["region"] = region
    if country:
        out["country"] = country
    if disambiguation:
        out["disambiguation"] = disambiguation
    for fk in ("anchor_latitude", "anchor_longitude"):
        v = raw.get(fk)
        if isinstance(v, (int, float)):
            out[fk] = round(float(v), 6)

    # Verification provenance — a URL is only persisted as authoritative once a build-time
    # actor lookup confirmed it (see verify_parent_trail_on_itinerary). The LLM-emitted URL is
    # kept separately as an unverified hint so we never present a hallucinated slug as real.
    status = str(raw.get("verification_status") or "").strip().lower()
    verified_flag = raw.get("verified")
    if status not in _VERIFICATION_STATUSES:
        status = "verified" if verified_flag is True else ""
    if status in ("verified", "catalog"):
        out["verified"] = True
        out["verification_status"] = status
        if parent:
            out["parent_alltrails_url"] = parent
        tid = _clean(raw.get("alltrails_trail_id"), limit=64) or None
        if tid:
            out["alltrails_trail_id"] = tid
    else:
        if status in _VERIFICATION_STATUSES:
            out["verified"] = False
            out["verification_status"] = status
        elif verified_flag is False:
            out["verified"] = False
        # Unverified LLM URL is retained only as a hint, never as parent_alltrails_url.
        if parent:
            out["unverified_url_hint"] = parent
    return out


def alltrails_search_context_prompt_block() -> str:
    return """
--- ALLTRAILS SEARCH CONTEXT (required for backpacking / multi-day hike itineraries) ---

Include top-level ``alltrails_search_context`` so the trip can be **verified** against AllTrails
later. The server runs the AllTrails lookup itself — your job is to describe the trail precisely,
NOT to produce a URL. Duplicate trail names exist worldwide (e.g. Heart Lake in California vs
Wyoming), so park + region must be exact.

```json
"alltrails_search_context": {
  "trail_name": "Official trail name on AllTrails (e.g. Heart Lake Trail)",
  "park_name": "National park or reserve (e.g. Yellowstone National Park)",
  "region": "State or province for AllTrails (e.g. Wyoming, British Columbia)",
  "country": "United States or Canada",
  "search_query": "Trail + park + region — one line the server can search",
  "disambiguation": "What this route is NOT (wrong state, wrong park, similar name)"
}
```

Rules:
- **trail_name**, **park_name**, **region**, **country** are REQUIRED and must be accurate — the server
  verifies the trail by name **within that region**, so a wrong state means a wrong (or no) match.
- **search_query** MUST include park + region when the trail name is generic (Heart Lake, Eagle Peak, etc.).
- **Do NOT invent or guess an AllTrails URL.** There is no ``parent_alltrails_url`` field for you to fill —
  the server resolves and verifies the canonical URL. A guessed slug (e.g. ``/trail/.../heart-lake-trail``)
  is worse than none because it can resolve to the wrong trail.
- **The per-day plan must stand on its own.** Every hiking day's ``day_logistics.trail_segment`` MUST carry
  realistic ``name``, ``distance_mi``, ``elevation_gain_ft``, ``elevation_loss_ft`` and the matching ``walk``
  leg endpoints, so the trip is fully usable even if AllTrails has no page for it.
- Mirror ``trail_name`` on each hiking day's ``day_logistics.trail_segment.name``.
- **disambiguation**: one short sentence (e.g. "Yellowstone backcountry 8N1-8H2, not Castle Lake CA").
""".strip()


def derive_alltrails_search_context(
    destination: dict[str, Any] | None,
    *,
    outdoor_adventure: dict[str, Any] | None = None,
    itinerary_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Build context from card + itinerary when the model omitted the top-level object."""
    if not destination:
        return None
    existing = normalize_alltrails_search_context(destination.get("alltrails_search_context"))
    if existing:
        return existing

    name = _clean(destination.get("name"), limit=120)
    country = _clean(destination.get("country"), limit=80) or "United States"
    park = infer_hero_park_phrase(
        destination_id=str(destination.get("id") or ""),
        name=name,
        hook=str(destination.get("hook") or ""),
    )
    trail_name = name
    parent_url: str | None = None
    region: str | None = None

    rows = [r for r in (itinerary_rows or []) if isinstance(r, dict)]
    for row in rows:
        dl = row.get("day_logistics")
        if not isinstance(dl, dict):
            continue
        ts = dl.get("trail_segment")
        if isinstance(ts, dict):
            tn = _clean(ts.get("name"), limit=120)
            if tn:
                trail_name = tn
            notes = str(ts.get("notes") or "")
            u = extract_alltrails_url_from_text(notes) or extract_alltrails_url_from_text(
                str(ts.get("alltrails_url") or "")
            )
            if u:
                parent_url = u
        for leg in dl.get("surface_legs") or []:
            if not isinstance(leg, dict) or (leg.get("kind") or "").lower() != "walk":
                continue
            tn = _clean(leg.get("trail_name"), limit=120)
            if tn:
                trail_name = tn

    blob = f"{name} {destination.get('hook') or ''} {park} {country} {destination.get('id') or ''}".lower()
    if re.search(r"\bwyoming\b", blob) or "yellowstone" in blob or "heart-lake" in blob:
        region = "Wyoming"
    elif re.search(r"\b(british columbia|bc)\b", blob):
        region = "British Columbia"
    elif re.search(r"\b(california|ca)\b", blob) and "canada" not in blob:
        region = "California"
    elif re.search(r"\b(washington)\b", blob):
        region = "Washington"
    elif re.search(r"\b(utah)\b", blob):
        region = "Utah"
    elif re.search(r"\b(colorado)\b", blob):
        region = "Colorado"
    elif re.search(r"\b(ontario)\b", blob):
        region = "Ontario"
    elif re.search(r"\b(alberta)\b", blob):
        region = "Alberta"

    parts = [p for p in (park, trail_name, region, country) if p]
    search_query = " ".join(parts)[:240]
    if not trail_name or not search_query:
        return None
    return normalize_alltrails_search_context(
        {
            "trail_name": trail_name,
            "park_name": park or None,
            "region": region,
            "country": country,
            "parent_alltrails_url": parent_url,
            "search_query": search_query,
        }
    )


def verified_parent_alltrails_url(destination: dict[str, Any] | None) -> str | None:
    """Authoritative parent URL: Apify-verified, catalog-grounded, or None."""
    if not destination:
        return None
    ctx = normalize_alltrails_search_context(destination.get("alltrails_search_context"))
    if ctx and ctx.get("verified") is True:
        u = str(ctx.get("parent_alltrails_url") or "").strip()
        if u:
            return u
    g = destination.get("grounding")
    if isinstance(g, dict) and str(g.get("status") or "").strip().lower() == "catalog":
        return extract_alltrails_url_from_text(str(g.get("source_url") or ""))
    return None


def preferred_alltrails_urls_from_destination(
    destination: dict[str, Any] | None,
    *,
    trail_name: str | None = None,
    from_label: str | None = None,
    to_label: str | None = None,
) -> list[str]:
    """
    Authoritative URLs to hand the lookup — **verified evidence only**.

    No slug invention or LLM-hint promotion: a URL only appears here after a build-time
    actor lookup confirmed it (``verified: true``), or from offline trail catalog
    (``verification_status: catalog`` / ``grounding.source_url``).
    """
    u = verified_parent_alltrails_url(destination)
    if u:
        return [u]
    if not destination:
        return []
    g = destination.get("grounding")
    if isinstance(g, dict) and str(g.get("status") or "").strip().lower() == "catalog":
        su = extract_alltrails_url_from_text(str(g.get("source_url") or ""))
        if su:
            return [su]
    return []


def _slugify_trail_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:80]


def is_alltrails_trail_page_url(url: str | None) -> bool:
    u = (url or "").strip().lower()
    return "alltrails.com" in u and "/trail/" in u


def alltrails_region_path_from_url(url: str | None) -> str | None:
    """e.g. ``us/wyoming`` from ``/trail/…`` or ``/explore/trail/…`` pages."""
    u = (url or "").lower()
    if "/trail/" not in u:
        return None
    parts = [p for p in u.split("/trail/", 1)[1].split("/") if p]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return None


def alltrails_slug_from_url(url: str | None) -> str:
    return (url or "").lower().rstrip("/").rsplit("/", 1)[-1]


def _slug_core_for_match(slug: str) -> str:
    s = (slug or "").lower().strip("-")
    s = re.sub(r"--\d+$", "", s)
    return re.sub(r"-trail$", "", s)


def alltrails_slugs_equivalent(a: str, b: str) -> bool:
    """``heart-lake--2`` ≈ ``heart-lake`` ≈ ``heart-lake-trail`` — not ``heart-lake-trail-from-castle-lake``."""
    sa, sb = _slug_core_for_match(a), _slug_core_for_match(b)
    if not sa or not sb:
        return False
    if sa == sb:
        return True
    if sa + "-trail" == sb or sb + "-trail" == sa:
        return True
    return False


def candidate_parent_alltrails_urls(ctx: dict[str, Any] | None) -> list[str]:
    """
    Verified parent URL only — slug guessing is intentionally removed.

    Inventing ``/explore/trail/{region}/{name}--2`` style URLs is what broke Heart Lake (the
    actor returned garbage for unfetchable trail pages). The parent URL is now produced solely by
    ``verify_parent_trail_on_itinerary`` and carried as ``verified: true`` in the context.
    """
    norm = normalize_alltrails_search_context(ctx)
    if norm and norm.get("verified") is True:
        u = str(norm.get("parent_alltrails_url") or "").strip()
        if u and is_alltrails_trail_page_url(u):
            return [u]
    return []


def infer_parent_alltrails_url(ctx: dict[str, Any] | None) -> str | None:
    """Verified parent URL or None (no inference / guessing)."""
    urls = candidate_parent_alltrails_urls(ctx)
    return urls[0] if urls else None


def region_path_from_context(ctx: dict[str, Any] | None) -> str | None:
    """AllTrails path segment e.g. ``us/wyoming`` from persisted context."""
    if not ctx:
        return None
    parent = str(ctx.get("parent_alltrails_url") or "")
    parsed = alltrails_region_path_from_url(parent)
    if parsed:
        return parsed
    region = _clean(ctx.get("region")).lower().replace(" ", "-")
    country = _clean(ctx.get("country")).lower()
    sq = _clean(ctx.get("search_query")).lower()
    if not region and sq:
        if re.search(r"\bwyoming\b", sq) or "yellowstone" in sq:
            region = "wyoming"
        elif re.search(r"\b(british columbia|bc)\b", sq):
            region = "british-columbia"
        elif "california" in sq:
            region = "california"
    if "canada" in country or country == "ca":
        if region in ("british columbia", "bc"):
            return "canada/british-columbia"
        if region == "ontario":
            return "canada/ontario"
        if region == "alberta":
            return "canada/alberta"
    if "united states" in country or country in ("us", "usa"):
        if region == "wyoming":
            return "us/wyoming"
        if region == "california":
            return "us/california"
        if region == "washington":
            return "us/washington"
        if region == "utah":
            return "us/utah"
        if region == "colorado":
            return "us/colorado"
    return None


def catalog_trail_anchor_fallback(
    destination: dict[str, Any] | None,
    *,
    trail_name: str | None = None,
    from_label: str | None = None,
    to_label: str | None = None,
) -> dict[str, Any] | None:
    """
  Fallback coords when Apify returns the wrong trail page.

  Uses ``anchor_latitude`` / ``anchor_longitude`` on ``alltrails_search_context`` when set at
  itinerary build; otherwise no synthetic coords.
    """
    ctx = normalize_alltrails_search_context(
        (destination or {}).get("alltrails_search_context")
    )
    if not ctx:
        return None
    lat = ctx.get("anchor_latitude")
    lng = ctx.get("anchor_longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return None
    parent = str(ctx.get("parent_alltrails_url") or "")
    return {
        "name": str(ctx.get("trail_name") or trail_name or "Trail"),
        "url": parent or None,
        "latitude": float(lat),
        "longitude": float(lng),
        "locationLabel": ctx.get("park_name"),
        "state": ctx.get("region"),
        "country": ctx.get("country"),
        "description": "Catalog anchor coordinates from itinerary alltrails_search_context.",
    }


def alltrails_verify_on_build_enabled() -> bool:
    """
  Apify parent-trail verification at itinerary build — **off by default** (catalog-grounded path).

  Opt in with ``WANDERLY_ALLTRAILS_VERIFY=1``. Legacy ``WANDERLY_ALLTRAILS_ON_ITINERARY=0`` disables.
    """
    import os

    raw = (os.environ.get("WANDERLY_ALLTRAILS_VERIFY") or "0").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    legacy = (os.environ.get("WANDERLY_ALLTRAILS_ON_ITINERARY") or "").strip().lower()
    if legacy in ("0", "false", "no", "off"):
        return False
    return True


# Back-compat alias (older callers / env docs).
def alltrails_on_itinerary_build_enabled() -> bool:
    return alltrails_verify_on_build_enabled()


def _context_with_verification(
    ctx: dict[str, Any],
    *,
    status: str,
    parent_url: str | None = None,
    trail_id: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict[str, Any]:
    merged = dict(ctx)
    merged["verification_status"] = status
    merged["verified"] = status == "verified"
    if status == "verified":
        if parent_url:
            merged["parent_alltrails_url"] = parent_url
        if trail_id:
            merged["alltrails_trail_id"] = trail_id
        if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
            merged["anchor_latitude"] = round(float(latitude), 6)
            merged["anchor_longitude"] = round(float(longitude), 6)
    else:
        # Never persist an unverified URL as authoritative.
        merged.pop("parent_alltrails_url", None)
    return normalize_alltrails_search_context(merged) or merged


def verify_parent_trail_on_itinerary(
    destination: dict[str, Any],
    *,
    alltrails_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    One bounded AllTrails verification per destination at build time (on by default).

    Uses reliable actor inputs (park/region/coordinate search), matches the LLM ``trail_name``
    within the expected region, and stamps ``alltrails_search_context`` with:
      - ``verified: true`` + ``parent_alltrails_url`` + ``alltrails_trail_id`` + anchor coords on a
        confident match;
      - ``verified: false`` + ``verification_status: "unverified"`` on no confident match
        (no URL persisted);
      - ``verification_status: "provider_unavailable"`` when Apify token is missing / errors.

    The LLM estimates are always kept, so the trip stays fully usable regardless of outcome.
    """
    ctx = normalize_alltrails_search_context(alltrails_context) or normalize_alltrails_search_context(
        destination.get("alltrails_search_context")
    )
    if not ctx:
        return destination
    out_dest = dict(destination)
    if ctx.get("verified") is True and ctx.get("parent_alltrails_url"):
        out_dest["alltrails_search_context"] = ctx
        return out_dest
    if not alltrails_verify_on_build_enabled():
        out_dest["alltrails_search_context"] = ctx
        return out_dest

    from src.wanderly.alltrails_provider import apify_token, verify_parent_trail

    if not apify_token():
        out_dest["alltrails_search_context"] = _context_with_verification(
            ctx, status="provider_unavailable"
        )
        return out_dest
    try:
        res = verify_parent_trail(
            trail_name=str(ctx.get("trail_name") or ""),
            park_name=str(ctx.get("park_name") or "") or None,
            region=str(ctx.get("region") or "") or None,
            country=str(ctx.get("country") or destination.get("country") or "") or None,
            search_query=str(ctx.get("search_query") or "") or None,
            anchor_latitude=ctx.get("anchor_latitude"),
            anchor_longitude=ctx.get("anchor_longitude"),
            destination_id=str(destination.get("id") or "") or None,
        )
    except Exception:
        out_dest["alltrails_search_context"] = _context_with_verification(
            ctx, status="provider_unavailable"
        )
        return out_dest

    status = str(res.get("status") or "unverified")
    if status == "verified" and res.get("parent_alltrails_url"):
        out_dest["alltrails_search_context"] = _context_with_verification(
            ctx,
            status="verified",
            parent_url=str(res.get("parent_alltrails_url") or "") or None,
            trail_id=str(res.get("alltrails_trail_id") or "") or None,
            latitude=res.get("latitude"),
            longitude=res.get("longitude"),
        )
    elif status == "provider_unavailable":
        out_dest["alltrails_search_context"] = _context_with_verification(
            ctx, status="provider_unavailable"
        )
    else:
        out_dest["alltrails_search_context"] = _context_with_verification(ctx, status="unverified")
    return out_dest


# Back-compat alias for older imports.
maybe_resolve_parent_trail_on_itinerary = verify_parent_trail_on_itinerary
