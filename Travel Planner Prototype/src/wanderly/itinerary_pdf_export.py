"""Print-friendly itinerary HTML + Playwright PDF (sample / future export endpoint)."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SURFACE_ICON = {
    "drive": "🚗",
    "ferry": "⛴",
    "walk": "🚶",
    "train": "🚆",
    "flight": "✈",
}


def _esc(text: Any) -> str:
    if text is None:
        return ""
    return html.escape(str(text).strip())


def _pill_label(pill: dict[str, Any]) -> str:
    return _esc(pill.get("label") or pill.get("key") or "")


def _pill_value(pill: dict[str, Any]) -> str:
    return _esc(pill.get("value") or pill.get("text") or "")


def _pill_emoji(pill: dict[str, Any]) -> str:
    raw = pill.get("emoji") or pill.get("icon") or ""
    return _esc(raw) if isinstance(raw, str) else ""


def _pick_hero_image(dest: dict[str, Any]) -> str:
    """First usable card image URL for PDF cover."""
    for candidate in (dest.get("image"), *(dest.get("images") or [])):
        if isinstance(candidate, str) and candidate.strip().startswith(("http://", "https://")):
            return candidate.strip()
    return ""


def _short_place(label: str) -> str:
    """Shorten long place labels for compact route lines."""
    s = label.strip()
    if not s:
        return "—"
    if ", BC, Canada" in s:
        return s.split(", BC, Canada")[0]
    if ", WA, United States" in s:
        return s.split(", WA, United States")[0]
    if ", Canada" in s:
        return s.split(", Canada")[0]
    if ", United States" in s:
        return s.split(", United States")[0]
    return s


def _render_surface_legs(legs: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for leg in legs:
        kind = (leg.get("kind") or "drive").lower()
        icon = _SURFACE_ICON.get(kind, "•")
        fr = _esc(leg.get("from_label") or "—")
        to = _esc(leg.get("to_label") or "—")
        op = _esc(leg.get("operator_hint") or "")
        op_html = f'<div class="leg-op">{op}</div>' if op else ""
        rows.append(
            f'<li class="leg leg-{kind}">'
            f'<span class="leg-icon" aria-hidden="true">{icon}</span>'
            f'<div class="leg-body">'
            f'<div class="leg-kind">{_esc(kind)}</div>'
            f'<div class="leg-route">{fr} <span class="arrow">→</span> {to}</div>'
            f"{op_html}"
            f"</div></li>"
        )
    return f'<ul class="legs">{"".join(rows)}</ul>' if rows else ""


def _render_lodging(lod: dict[str, Any]) -> str:
    label = _esc(lod.get("destination_search_label") or "Lodging")
    cin = _esc(lod.get("check_in") or "")
    cout = _esc(lod.get("check_out") or "")
    area = _esc(lod.get("area_hint") or "")
    meta: list[str] = []
    if cin:
        meta.append(f"Check-in {cin}")
    if cout:
        meta.append(f"Check-out {cout}")
    rooms = lod.get("rooms")
    if isinstance(rooms, int) and rooms > 0:
        meta.append(f"{rooms} room{'s' if rooms != 1 else ''}")
    meta_html = " · ".join(meta)
    area_html = f'<div class="stay-area">{area}</div>' if area else ""
    return (
        f'<li class="leg leg-stay">'
        f'<span class="leg-icon" aria-hidden="true">🏨</span>'
        f'<div class="leg-body">'
        f'<div class="leg-kind">Stay</div>'
        f'<div class="leg-route">{label}</div>'
        f"{area_html}"
        f'<div class="stay-meta">{_esc(meta_html)}</div>'
        f"</div></li>"
    )


def _render_day_logistics(dl: dict[str, Any] | None) -> str:
    if not dl or not isinstance(dl, dict):
        return ""
    blocks: list[str] = []
    flight = dl.get("flight")
    if isinstance(flight, dict):
        segs = flight.get("segments") or []
        if isinstance(segs, list):
            for seg in segs:
                if not isinstance(seg, dict):
                    continue
                fr = _esc(seg.get("from_search_label") or "—")
                to = _esc(seg.get("to_search_label") or "—")
                role = _esc(seg.get("role") or "flight")
                blocks.append(
                    f'<li class="leg leg-flight">'
                    f'<span class="leg-icon">✈</span>'
                    f'<div class="leg-body">'
                    f'<div class="leg-kind">{role}</div>'
                    f'<div class="leg-route">{fr} <span class="arrow">→</span> {to}</div>'
                    f"</div></li>"
                )
    legs = dl.get("surface_legs")
    if isinstance(legs, list) and legs:
        blocks.append(_render_surface_legs([x for x in legs if isinstance(x, dict)]).replace("<ul class=\"legs\">", "").replace("</ul>", ""))
    lod = dl.get("lodging")
    if isinstance(lod, dict) and (lod.get("destination_search_label") or lod.get("check_in")):
        blocks.append(_render_lodging(lod))
    if not blocks:
        return ""
    return f'<ul class="legs day-logistics">{"".join(blocks)}</ul>'


def _render_day_logistics_compact(dl: dict[str, Any] | None) -> tuple[str, str]:
    if not dl or not isinstance(dl, dict):
        return "", ""
    route_parts: list[str] = []
    flight = dl.get("flight")
    if isinstance(flight, dict):
        for seg in flight.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            fr = _short_place(seg.get("from_search_label") or "")
            to = _short_place(seg.get("to_search_label") or "")
            route_parts.append(f"✈ {_esc(fr)} → {_esc(to)}")
    legs = dl.get("surface_legs")
    if isinstance(legs, list):
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            kind = (leg.get("kind") or "drive").lower()
            icon = _SURFACE_ICON.get(kind, "•")
            fr = _short_place(leg.get("from_label") or "")
            to = _short_place(leg.get("to_label") or "")
            if fr == to:
                route_parts.append(f"{icon} {_esc(fr)}")
            else:
                route_parts.append(f"{icon} {_esc(fr)} → {_esc(to)}")
    route_html = f'<p class="route-line">{" · ".join(route_parts)}</p>' if route_parts else ""
    stay_html = ""
    lod = dl.get("lodging")
    if isinstance(lod, dict) and (lod.get("destination_search_label") or lod.get("check_in")):
        label = _short_place(lod.get("destination_search_label") or "Stay")
        bits = [f"🏨 {_esc(label)}"]
        if lod.get("check_in"):
            bits.append(f"in {_esc(lod.get('check_in'))}")
        if lod.get("check_out"):
            bits.append(f"out {_esc(lod.get('check_out'))}")
        stay_html = f'<p class="stay-line">{" · ".join(bits)}</p>'
    return route_html, stay_html


def _render_day(day: dict[str, Any], *, compact: bool = True) -> str:
    n = day.get("day") or "?"
    label = _esc(day.get("label") or f"Day {n}")
    base = _esc(day.get("base") or "")
    dt = day.get("day_transport") or {}
    meta_bits: list[str] = []
    if base:
        meta_bits.append(f"Base: {base}")
    if isinstance(dt, dict) and dt.get("mode"):
        mode = _esc(dt.get("mode"))
        note = _esc(dt.get("note") or "")
        meta_bits.append(mode + (f" · {note}" if note else ""))
    meta_html = f'<p class="day-meta">{" · ".join(meta_bits)}</p>' if meta_bits else ""
    items = day.get("items") or []
    items_html = ""
    if isinstance(items, list) and items:
        lis = "".join(f"<li>{_esc(it)}</li>" for it in items if it)
        items_html = (
            f'<ul class="day-items compact">{lis}</ul>' if compact else f'<ol class="day-items">{lis}</ol>'
        )
    if compact:
        route_html, stay_html = _render_day_logistics_compact(day.get("day_logistics"))
        return (
            f'<article class="day-card compact">'
            f'<header class="day-head">'
            f'<span class="day-num">{_esc(n)}</span>'
            f"<div><h3 class=\"day-label\">{label}</h3>{meta_html}</div>"
            f"</header>"
            f"{route_html}{stay_html}{items_html}"
            f"</article>"
        )
    base_html = f'<p class="day-base">Base: {base}</p>' if base else ""
    transport_html = ""
    if isinstance(dt, dict) and dt.get("mode"):
        note = _esc(dt.get("note") or "")
        note_bit = f" · {note}" if note else ""
        transport_html = (
            f'<p class="day-transport">Transport: <strong>{_esc(dt.get("mode"))}</strong>{note_bit}</p>'
        )
    logistics_html = _render_day_logistics(day.get("day_logistics"))
    return (
        f'<article class="day-card">'
        f'<header class="day-header">'
        f'<span class="day-num">{_esc(n)}</span>'
        f"<div><h3 class=\"day-label\">{label}</h3>{base_html}{transport_html}</div>"
        f"</header>"
        f"{logistics_html}"
        f"{items_html}"
        f"</article>"
    )


def build_itinerary_print_html(
    dest: dict[str, Any],
    profile_pills: list[dict[str, Any]] | None = None,
    *,
    brand: str = "Wanderly",
    generated_at: datetime | None = None,
) -> str:
    """Build a light, print-ready HTML page for one destination card."""
    profile_pills = profile_pills or []
    name = _esc(dest.get("name") or "Destination")
    country = _esc(dest.get("country") or "")
    hook = _esc(dest.get("hook") or "")
    score = dest.get("score")
    score_html = f'<span class="score">Match {int(score)}%</span>' if isinstance(score, (int, float)) else ""
    vibe = dest.get("vibe") or []
    vibe_html = ""
    if isinstance(vibe, list) and vibe:
        tags = "".join(f'<span class="tag">{_esc(v)}</span>' for v in vibe if v)
        vibe_html = f'<div class="tags">{tags}</div>'

    pills_html = ""
    if profile_pills:
        chips = []
        for p in profile_pills:
            if not isinstance(p, dict):
                continue
            em = _pill_emoji(p)
            chips.append(
                f'<span class="pill"><span class="pill-em">{em}</span>'
                f'<span class="pill-lbl">{_pill_label(p)}</span> '
                f'<span class="pill-val">{_pill_value(p)}</span></span>'
            )
        pills_html = f'<div class="pills">{"".join(chips)}</div>'

    bg = dest.get("budget_guidance") or {}
    budget_bits: list[str] = []
    if isinstance(bg, dict):
        for key, label in (
            ("trip_usd_pp_short", "Trip estimate"),
            ("lodging_band_short", "Lodging"),
            ("flight_band_short", "Flights"),
        ):
            val = bg.get(key)
            if val:
                budget_bits.append(f"<dt>{label}</dt><dd>{_esc(val)}</dd>")
        notes = bg.get("notes")
        if notes:
            budget_bits.append(f'<dt>Notes</dt><dd class="budget-notes">{_esc(notes)}</dd>')
    budget_html = f'<dl class="budget">{"".join(budget_bits)}</dl>' if budget_bits else ""

    tp = dest.get("transport_plan") or {}
    transport_summary = ""
    lodging_moves = ""
    if isinstance(tp, dict):
        summary = _esc(tp.get("summary") or "")
        mode = _esc(tp.get("primary_mode") or "")
        if summary or mode:
            mode_bit = f'<span class="mode-pill">{mode}</span>' if mode else ""
            transport_summary = (
                f'<section class="section transport">'
                f"<h2>Getting there</h2>{mode_bit}"
                f'<p class="transport-summary">{summary}</p>'
            )
        moves = tp.get("lodging_moves") or []
        if isinstance(moves, list) and moves:
            rows = []
            for m in moves:
                if not isinstance(m, dict):
                    continue
                area = _esc(m.get("area") or "")
                dr = _esc(m.get("day_range") or "")
                note = _esc(m.get("note") or "")
                rows.append(f"<li><strong>{area}</strong> ({dr}) — {note}</li>")
            lodging_moves = (
                f'<h3 class="subhead">Where you stay</h3><ul class="lodging-moves">{"".join(rows)}</ul>'
            )
        if lodging_moves:
            transport_summary += lodging_moves + "</section>"
        elif transport_summary:
            transport_summary += "</section>"

    itinerary = dest.get("itinerary") or []
    days_html = ""
    if isinstance(itinerary, list) and itinerary:
        days_html = "".join(_render_day(d) for d in itinerary if isinstance(d, dict))

    when = datetime.now(timezone.utc) if generated_at is None else generated_at
    stamp = when.strftime("%B %d, %Y")
    day_count = len(itinerary) if isinstance(itinerary, list) else 0

    hero_url = _esc(_pick_hero_image(dest))
    if hero_url:
        cover_html = (
            f'<div class="cover">'
            f'<img class="cover-img" src="{hero_url}" alt="{name}"/>'
            f'<div class="cover-overlay">'
            f'<div class="brand cover-brand">{_esc(brand)}</div>'
            f"<h1 class=\"cover-title\">{name}{score_html}</h1>"
            f'<p class="cover-meta">{country} · {day_count} days</p>'
            f'{f"<p class=\"cover-hook\">{hook}</p>" if hook else ""}'
            f"{vibe_html.replace('class=\"tags\"', 'class=\"tags cover-tags\"') if vibe_html else ''}"
            f"</div></div>"
        )
        intro_html = ""
    else:
        cover_html = ""
        intro_html = (
            f'<div class="brand">{_esc(brand)}</div>'
            f'<header class="hero">'
            f"<h1>{name}{score_html}</h1>"
            f'<p class="meta">{country} · {day_count} days</p>'
            f'{f"<p class=\"hook\">{hook}</p>" if hook else ""}'
            f"{vibe_html}"
            f"</header>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{name} — Itinerary</title>
  <style>
    @page {{ size: A4; margin: 18mm 16mm; }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
      font-size: 10.5pt;
      line-height: 1.45;
      color: #1a1a2e;
      margin: 0;
      background: #fff;
    }}
    .page {{ max-width: 720px; margin: 0 auto; }}
    .cover {{ position: relative; margin: 0 0 16px; border-radius: 12px; overflow: hidden;
      height: 200px; break-inside: avoid; }}
    .cover-img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
    .cover-overlay {{ position: absolute; inset: 0; display: flex; flex-direction: column;
      justify-content: flex-end; padding: 16px 18px;
      background: linear-gradient(180deg, rgba(15,23,42,0.05) 0%, rgba(15,23,42,0.72) 68%, rgba(15,23,42,0.88) 100%); }}
    .cover-brand {{ color: #c7d2fe; }}
    .cover-title {{ font-size: 22pt; margin: 4px 0 2px; font-weight: 700; color: #fff; }}
    .cover-meta {{ margin: 0; color: #e2e8f0; font-size: 10pt; }}
    .cover-hook {{ margin: 6px 0 0; font-size: 10pt; color: #f1f5f9; max-width: 58ch; line-height: 1.35; }}
    .cover-tags .tag {{ background: rgba(255,255,255,0.15); color: #f8fafc; }}
    .cover .score {{ background: rgba(255,255,255,0.2); color: #fff; }}
    .brand {{ font-size: 9pt; letter-spacing: 0.12em; text-transform: uppercase; color: #6366f1; font-weight: 600; }}
    .hero {{ margin: 8px 0 16px; padding-bottom: 14px; border-bottom: 2px solid #e8e8f4; }}
    h1 {{ font-size: 22pt; margin: 4px 0 6px; font-weight: 700; color: #0f172a; }}
    .meta {{ color: #64748b; font-size: 10pt; }}
    .hook {{ margin: 10px 0 0; font-size: 11pt; color: #334155; max-width: 62ch; }}
    .score {{ display: inline-block; margin-left: 8px; padding: 2px 8px; border-radius: 999px;
      background: #eef2ff; color: #4338ca; font-size: 9pt; font-weight: 600; vertical-align: middle; }}
    .tags {{ margin-top: 8px; }}
    .tag {{ display: inline-block; margin-right: 6px; padding: 2px 10px; border-radius: 999px;
      background: #f1f5f9; font-size: 9pt; color: #475569; }}
    .pills {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 14px 0; }}
    .pill {{ display: inline-flex; align-items: baseline; gap: 4px; padding: 5px 10px;
      border: 1px solid #e2e8f0; border-radius: 8px; font-size: 9pt; background: #f8fafc; }}
    .pill-val {{ font-weight: 600; color: #0f172a; }}
    .section {{ margin: 18px 0; }}
    h2 {{ font-size: 11pt; text-transform: uppercase; letter-spacing: 0.06em;
      color: #6366f1; margin: 0 0 8px; font-weight: 700; }}
    .subhead {{ font-size: 10pt; color: #475569; margin: 12px 0 6px; font-weight: 600; }}
    .mode-pill {{ display: inline-block; margin-bottom: 6px; padding: 2px 8px; border-radius: 6px;
      background: #eef2ff; color: #4338ca; font-size: 8.5pt; text-transform: uppercase; }}
    .transport-summary {{ margin: 0; color: #334155; }}
    .lodging-moves {{ margin: 0; padding-left: 18px; color: #475569; font-size: 10pt; }}
    dl.budget {{ display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0;
      padding: 12px; background: #f8fafc; border-radius: 10px; border: 1px solid #e2e8f0; font-size: 9.5pt; }}
    dl.budget dt {{ font-weight: 600; color: #64748b; }}
    dl.budget dd {{ margin: 0; color: #1e293b; }}
  .budget-notes {{ grid-column: 1 / -1; margin-top: 4px; color: #64748b; }}
    .day-card.compact {{ break-inside: avoid; margin-bottom: 8px; padding: 8px 10px;
      border: 1px solid #e2e8f0; border-radius: 8px; background: #fafbfc; }}
    .day-head {{ display: flex; gap: 8px; align-items: flex-start; margin-bottom: 4px; }}
    .day-card.compact .day-num {{ width: 24px; height: 24px; font-size: 10pt; border-radius: 6px;
      flex-shrink: 0; display: flex; align-items: center; justify-content: center;
      background: #e0e7ff; color: #3730a3; font-weight: 700; }}
    .day-card.compact .day-label {{ margin: 0; font-size: 10.5pt; line-height: 1.25; color: #0f172a; }}
    .day-meta {{ margin: 2px 0 0; font-size: 8pt; color: #64748b; }}
    .route-line, .stay-line {{ margin: 0 0 3px; padding: 3px 6px; border-radius: 4px;
      font-size: 7.5pt; line-height: 1.35; color: #475569; background: #fff; border: 1px solid #eef2f7; }}
    .stay-line {{ background: #fffbeb; border-color: #fde68a; color: #713f12; }}
    .day-items.compact {{ margin: 4px 0 0; padding-left: 16px; font-size: 9pt; color: #1e293b; }}
    .day-items.compact li {{ margin-bottom: 2px; line-height: 1.3; }}
    .day-card {{ break-inside: avoid; margin-bottom: 14px; padding: 12px 14px;
      border: 1px solid #e2e8f0; border-radius: 12px; background: #fafbfc; }}
    .day-header {{ display: flex; gap: 12px; align-items: flex-start; margin-bottom: 8px; }}
    .day-num {{ flex-shrink: 0; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center;
      border-radius: 8px; background: #e0e7ff; color: #3730a3; font-weight: 700; font-size: 12pt; }}
    .day-label {{ margin: 0; font-size: 12pt; color: #0f172a; }}
    .day-base, .day-transport {{ margin: 4px 0 0; font-size: 9pt; color: #64748b; }}
    .legs {{ list-style: none; margin: 8px 0; padding: 0; }}
    .leg {{ display: flex; gap: 8px; margin-bottom: 6px; padding: 8px 10px; border-radius: 8px;
      background: #fff; border: 1px solid #e8ecf4; font-size: 9pt; }}
    .leg-stay {{ border-color: #fde68a; background: #fffbeb; }}
    .leg-ferry {{ border-color: #bae6fd; background: #f0f9ff; }}
    .leg-icon {{ font-size: 12pt; line-height: 1; }}
    .leg-kind {{ text-transform: uppercase; font-size: 8pt; letter-spacing: 0.05em; color: #94a3b8; font-weight: 600; }}
    .leg-route {{ color: #334155; margin-top: 2px; }}
    .arrow {{ color: #cbd5e1; }}
    .leg-op, .stay-meta, .stay-area {{ font-size: 8.5pt; color: #64748b; margin-top: 2px; }}
    .day-items {{ margin: 8px 0 0; padding-left: 20px; color: #1e293b; font-size: 10pt; }}
    .day-items li {{ margin-bottom: 4px; }}
    footer {{ margin-top: 24px; padding-top: 10px; border-top: 1px solid #e2e8f0;
      font-size: 8.5pt; color: #94a3b8; }}
  </style>
</head>
<body>
  <div class="page">
    {cover_html}
    {intro_html}
    {pills_html}
    {f'<section class="section"><h2>Budget</h2>{budget_html}</section>' if budget_html else ''}
    {transport_summary}
    <section class="section">
      <h2>Day by day</h2>
      {days_html}
    </section>
    <footer>
      Generated {stamp} · Draft itinerary for planning — verify ferry times, tides, and bookings before travel.
    </footer>
  </div>
</body>
</html>"""


def itinerary_pdf_filename(dest: dict[str, Any]) -> str:
    """Safe attachment filename for a destination itinerary PDF."""
    slug = re.sub(r"[^\w\-]+", "-", str(dest.get("id") or dest.get("name") or "itinerary")).strip("-").lower()
    return f"{slug or 'itinerary'}-itinerary.pdf"


def build_itinerary_pdf_bytes(
    dest: dict[str, Any],
    profile_pills: list[dict[str, Any]] | None = None,
) -> bytes:
    """Build print HTML and render to PDF bytes (Playwright Chromium)."""
    html_doc = build_itinerary_print_html(dest, profile_pills)
    return html_to_pdf_bytes(html_doc)


def html_to_pdf_bytes(html: str) -> bytes:
    """Render HTML string to PDF bytes via Playwright Chromium."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="load", timeout=45_000)
        if page.locator("img.cover-img").count():
            try:
                page.locator("img.cover-img").first.wait_for(state="visible", timeout=12_000)
            except Exception:
                pass
        pdf_bytes = page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        browser.close()
    return pdf_bytes


def html_to_pdf(html: str, pdf_path: Path, *, html_debug_path: Path | None = None) -> Path:
    """Render HTML string to PDF file via Playwright Chromium."""
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if html_debug_path:
        Path(html_debug_path).write_text(html, encoding="utf-8")
    pdf_path.write_bytes(html_to_pdf_bytes(html))
    return pdf_path


def load_trip_destination(trip_path: Path, dest_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = json.loads(Path(trip_path).read_text(encoding="utf-8"))
    session = data.get("session") or {}
    dests = session.get("last_destinations") or []
    dest = next((d for d in dests if d.get("id") == dest_id), None)
    if not dest:
        raise KeyError(f"Destination {dest_id!r} not found in {trip_path}")
    pills = session.get("profile_pills") or []
    return dest, pills if isinstance(pills, list) else []
