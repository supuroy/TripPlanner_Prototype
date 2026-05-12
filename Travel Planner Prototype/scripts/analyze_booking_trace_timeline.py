"""Summarize booking_search_trace.log: last session timeline + fetch/LLM gaps."""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

LOG = Path(__file__).resolve().parents[1] / ".cache" / "booking_search_trace.log"
SESSION_GAP_S = 900  # 15 min idle = new session
KEY_EVENTS = {
    "travel_window_llm_ok",
    "booking_dates_resolved",
    "outline_llm_ok",
    "outline_llm_exc",
    "params_llm_ok",
    "params_llm_exc",
    "booking_search_intent",
    "booking_hotels_plan",
    "step_begin",
    "html_fetch_start",
    "html_fetch_done",
    "html_fetch_retry_start",
    "html_fetch_retry_done",
    "flight_parse_llm_ok",
    "flight_parse_llm_fallback",
    "hotel_parse_llm_ok",
    "hotel_parse_heuristic",
    "step_ok",
    "step_fail",
}


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main() -> None:
    if not LOG.is_file():
        print(f"Missing log: {LOG}")
        return
    raw_lines = LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    rows: list[dict] = []
    for ln in raw_lines:
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    if not rows:
        print("Log empty")
        return

    # Last session: from last event backward until >SESSION_GAP_S gap
    last_t = parse_ts(rows[-1]["ts"])
    cut = 0
    for i in range(len(rows) - 1, 0, -1):
        t = parse_ts(rows[i]["ts"])
        if (last_t - t).total_seconds() > SESSION_GAP_S:
            cut = i + 1
            break
    sess = rows[cut:]

    print(f"Log: {LOG}")
    print(f"Session: {len(sess)} events (lines {cut + 1}-{len(rows)}), gap split > {SESSION_GAP_S}s")
    print(f"Wall span: {sess[0]['ts']} -> {sess[-1]['ts']}")
    dest = next(
        (str(r.get("destination_id") or r.get("dest_id") or "") for r in reversed(sess) if r.get("destination_id") or r.get("dest_id")),
        "",
    )
    if dest:
        print(f"Primary destination_id in tail: {dest}")
    print()

    prev: datetime | None = None
    for r in sess:
        ev = str(r.get("event") or "")
        if ev not in KEY_EVENTS and not ev.startswith(("flight_", "hotel_url")):
            continue
        t = parse_ts(r["ts"])
        dt = (t - prev).total_seconds() if prev else 0.0
        prev = t
        bits: list[str] = []
        for k in ("destination_id", "dest_id", "step", "hotel_index", "flight_leg", "source"):
            if k in r and r[k] is not None and r[k] != "":
                bits.append(f"{k}={r[k]}")
        if "fetch_elapsed_ms" in r:
            bits.append(f"fetch_ms={r['fetch_elapsed_ms']}")
        tail = " ".join(bits)
        print(f"{t.strftime('%H:%M:%S')}  +{dt:8.1f}s  {ev:<28} {tail}")

    # Gaps: html_fetch_done → flight_parse_llm_* (or step_ok flight if no LLM event)
    fl_done_to_llm: list[float] = []
    ht_done_to_step: list[float] = []
    i = 0
    while i < len(sess):
        r = sess[i]
        if r.get("event") == "html_fetch_done" and "/Flights-Search" in str(r.get("url", "")):
            t0 = parse_ts(r["ts"])
            j = i + 1
            while j < len(sess):
                r2 = sess[j]
                if r2.get("event") in ("flight_parse_llm_ok", "flight_parse_llm_fallback"):
                    fl_done_to_llm.append((parse_ts(r2["ts"]) - t0).total_seconds())
                    break
                if r2.get("event") == "step_ok" and r2.get("step") == "flight":
                    # heuristic-only path may skip flight_parse_llm_ok
                    fl_done_to_llm.append((parse_ts(r2["ts"]) - t0).total_seconds())
                    break
                j += 1
        if r.get("event") == "html_fetch_done" and "/Hotel-Search" in str(r.get("url", "")):
            t0 = parse_ts(r["ts"])
            j = i + 1
            while j < len(sess):
                r2 = sess[j]
                if r2.get("event") == "step_ok" and r2.get("step") == "hotel":
                    ht_done_to_step.append((parse_ts(r2["ts"]) - t0).total_seconds())
                    break
                j += 1
        i += 1

    print()
    print("Aggregates (this session)")
    fl_fetch: list[int] = []
    ht_fetch: list[int] = []
    for r in sess:
        if r.get("event") != "html_fetch_done":
            continue
        u = str(r.get("url", ""))
        ms = r.get("fetch_elapsed_ms")
        if not isinstance(ms, (int, float)):
            continue
        ms = int(ms)
        if "/Flights-Search" in u:
            fl_fetch.append(ms)
        elif "/Hotel-Search" in u:
            ht_fetch.append(ms)

    def avg_med(xs: list[float | int]) -> str:
        if not xs:
            return "n/a"
        return f"avg {statistics.mean(xs):.1f}s, median {statistics.median(xs):.1f}s"

    if fl_fetch:
        print(f"  Playwright flight fetch_elapsed_ms: n={len(fl_fetch)}, avg {statistics.mean(fl_fetch)/1000:.1f}s, max {max(fl_fetch)/1000:.1f}s")
    if ht_fetch:
        print(f"  Playwright hotel fetch_elapsed_ms: n={len(ht_fetch)}, avg {statistics.mean(ht_fetch)/1000:.1f}s, max {max(ht_fetch)/1000:.1f}s")
    if fl_done_to_llm:
        print(f"  After flight HTML: to parse_llm_ok/fallback or step_ok flight: {avg_med(fl_done_to_llm)}")
    if ht_done_to_step:
        print(f"  After hotel HTML: to step_ok hotel: {avg_med(ht_done_to_step)}")


if __name__ == "__main__":
    main()
