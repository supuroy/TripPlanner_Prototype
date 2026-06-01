"""Dump AllTrails URLs from active trip JSON."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
trip_path = ROOT / ".cache" / "wanderly_trips" / "trip-0303b791485e.json"
raw = trip_path.read_text(encoding="utf-8")
t = json.loads(raw)
sess = t.get("session") or {}
dests = sess.get("ui_destinations") or sess.get("last_destinations") or t.get("destinations") or []
print(f"(source: {'ui_destinations' if sess.get('ui_destinations') else 'last_destinations/other'})\n")
print(f"Trip: {t.get('trip_name')} | {len(dests)} cards\n")
urls_all: list[str] = sorted(set(re.findall(r"https://www\.alltrails\.com[^\s\"\\]+", raw)))
for d in dests:
    name = d.get("name")
    did = d.get("id")
    print("=" * 55)
    print(f"{name} ({did})")
    ctx = d.get("alltrails_search_context") or {}
    if ctx:
        print("  alltrails_search_context:")
        for k in (
            "trail_name",
            "parent_alltrails_url",
            "verified",
            "verification_status",
            "search_query",
            "unverified_url_hint",
        ):
            if ctx.get(k) is not None:
                print(f"    {k}: {ctx[k]}")
    else:
        print("  alltrails_search_context: (none)")
    tl = d.get("trail_lookups") or {}
    print(f"  trail_lookups: {len(tl)} entries")
    seen: set[str] = set()
    for e in tl.values():
        if not isinstance(e, dict):
            continue
        u = str(e.get("url") or "").strip()
        if u and u not in seen:
            seen.add(u)
            print(f"    {e.get('name')}: {u}")
print("\n--- All alltrails.com URLs anywhere in trip file ---")
for u in urls_all:
    print(u)
if not urls_all:
    print("(none)")
