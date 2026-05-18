/**
 * Maps booking-search SSE events to traveler-facing captions (server progress + LLM stream).
 */

export type BookingStreamDrafts = {
  assistant: { text: string };
  thinking: { text: string };
};

export function formatBookingStepKey(key: string): string {
  const k = key.trim();
  if (!k) return "Step";
  if (k === "prepare") return "Prepare search";
  if (k === "prepare_bundle") return "Advisor bundle";
  if (k === "prepare_dates") return "Trip dates";
  if (k === "prepare_filters") return "Expedia filters";
  if (k === "prefetch") return "Background prefetch";
  if (k === "html_fetch") return "Load Expedia page";
  if (k === "flight") return "Flights";
  if (k === "flight_parse") return "Parse flights";
  if (k === "return_flight") return "Return flight";
  if (k.startsWith("hotel_")) {
    const m = /^hotel_(\d+)(?:_parse)?$/.exec(k);
    if (m) {
      const n = Number(m[1]) + 1;
      return m[0].endsWith("_parse") ? `Parse stay ${n}` : `Stay ${n}`;
    }
    return "Hotels";
  }
  return k.replace(/_/g, " ");
}

/** Map booking SSE payloads to a primary status line. */
export function bookingStreamEventToCaption(
  ev: Record<string, unknown>,
  drafts: BookingStreamDrafts,
): string {
  if (ev.type === "phase" && typeof ev.message === "string") {
    if (/parsing/i.test(ev.message)) drafts.thinking.text = "";
    return ev.message.trim();
  }
  if (ev.type === "progress" && typeof ev.message === "string") {
    return ev.message.trim();
  }
  if (ev.type === "stream_preview" && typeof ev.message === "string") {
    return ev.message.trim();
  }
  if (ev.type === "thinking_delta" && typeof ev.delta === "string") {
    drafts.thinking.text += ev.delta;
    const t = drafts.thinking.text.replace(/\s+/g, " ").trim();
    if (!t) return "";
    const windowed = t.length > 300 ? `…${t.slice(-300)}` : t;
    return `Considering options — ${windowed}`;
  }
  if (ev.type === "assistant_delta" && typeof ev.delta === "string") {
    drafts.assistant.text += ev.delta;
    const oneLine = drafts.assistant.text.replace(/\s+/g, " ").trim();
    if (!oneLine) return "";
    const looksJson =
      '"listings"' in oneLine ||
      '"stays"' in oneLine ||
      '"start_iso"' in oneLine ||
      '"best_index"' in oneLine;
    if (looksJson) {
      const tail = oneLine.length > 380 ? `…${oneLine.slice(-380)}` : oneLine;
      return `Parsing Expedia results — ${tail}`;
    }
    const prose = oneLine.length > 420 ? `…${oneLine.slice(-420)}` : oneLine;
    return `Advisor drafting — ${prose}`;
  }
  if (ev.type === "run_status" && typeof ev.status === "string") {
    const u = ev.status.toUpperCase();
    if (u === "CREATING") return "Connecting to the advisor…";
    if (u === "RUNNING") return "Advisor is working on this search…";
    if (u === "FINISHED" || u === "COMPLETED") return "Wrapping up…";
  }
  if (ev.type === "tool_call") return "Gathering extra context…";
  return "";
}
