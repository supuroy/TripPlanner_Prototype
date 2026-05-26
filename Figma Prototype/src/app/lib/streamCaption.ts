/**
 * Maps Cursor / Wanderly SSE events to traveler-facing progress captions.
 * Shared by CommandBar (smoke/refine), ConversationPanel (blocking waits), and
 * DestinationCanvas (per-card itinerary stream).
 */

export type StructuredStreamMode = "workspace" | "itinerary";

/** Rotating lines while awaiting non-streaming HTTP (orchestrate, advisor). Keeps the UI from feeling frozen. */
export const WAIT_ORCHESTRATE_CAPTIONS = [
  "Understanding how your message fits the trip…",
  "Routing intent — planner update vs quick answer…",
  "Using your session context (destinations & sidebar profile)…",
] as const;

export const WAIT_ADVISOR_CAPTIONS = [
  "Reading your workspace and sidebar profile…",
  "Grounding the reply in your trip JSON…",
  "Drafting an answer…",
] as const;

export const WAIT_WEB_CAPTIONS = [
  "Searching the web for up-to-date details…",
  "Summarizing sources into a concise reply…",
] as const;

/** Spinner lines — overlay should replace in place, not append each rotation. */
export const ALL_ROTATING_WAIT_CAPTIONS: ReadonlySet<string> = new Set([
  ...WAIT_ORCHESTRATE_CAPTIONS,
  ...WAIT_ADVISOR_CAPTIONS,
  ...WAIT_WEB_CAPTIONS,
]);

export function isRotatingWaitCaption(caption: string): boolean {
  return ALL_ROTATING_WAIT_CAPTIONS.has(caption.trim());
}

/**
 * Cycles through lines every ``intervalMs`` so long blocking requests still feel alive.
 * Call the returned function to stop (e.g. in finally).
 */
export function startRotatingWaitCaption(
  setCaption: (s: string) => void,
  lines: readonly string[],
  intervalMs = 2200,
): () => void {
  if (!lines.length) return () => {};
  let i = 0;
  setCaption(lines[0] ?? "");
  const id = window.setInterval(() => {
    i = (i + 1) % lines.length;
    setCaption(lines[i] ?? "");
  }, intervalMs);
  return () => window.clearInterval(id);
}

export function runStatusCaption(status: string): string | null {
  const u = status.toUpperCase();
  if (u === "CREATING") return "Getting your travel agent ready…";
  if (u === "RUNNING") return "Working through your trip request…";
  if (u === "FINISHED" || u === "COMPLETED") return "Putting the finishing touches on your answer…";
  return null;
}

/** Human status while structured JSON streams — overlay only (no raw payload). */
export const OVERLAY_STRUCTURED_PLANNER_STATUS: Record<StructuredStreamMode, string> = {
  workspace: "Updating your trip cards and traveler profile…",
  itinerary: "Building your day-by-day outline…",
};

function overlayThinkingCaption(thinkingDraft: { text: string }, thinkWindow: number): string {
  const t = thinkingDraft.text.replace(/\s+/g, " ").trim();
  if (!t) return "";
  return t.length > thinkWindow ? `…${t.slice(-thinkWindow)}` : t;
}

/** Rolling preview of streamed assistant JSON so the UI does not look frozen while tokens arrive. */
export function structuredPlannerStreamCaption(
  oneLine: string,
  thinkingDraft: { text: string },
  mode: StructuredStreamMode = "workspace",
  tailMax = 420,
): string {
  const tail = oneLine.length > tailMax ? oneLine.slice(-tailMax) : oneLine;
  const thinking = thinkingDraft.text.replace(/\s+/g, " ").trim();
  const mid =
    mode === "itinerary"
      ? `Building your day-by-day outline — ${tail || "…"}`
      : `Filling sidebar & trip cards — ${tail || "…"}`;
  if (!thinking) return mid;
  return `Considering options — ${thinking.slice(-320)}\n\n${mid}`;
}

/** Overlay stream: model reasoning only, or a short status — never raw workspace/itinerary JSON. */
export function overlayStructuredPlannerCaption(
  thinkingDraft: { text: string },
  mode: StructuredStreamMode,
  thinkWindow: number,
): string {
  const thinking = overlayThinkingCaption(thinkingDraft, thinkWindow);
  if (thinking) return thinking;
  return OVERLAY_STRUCTURED_PLANNER_STATUS[mode];
}

export function looksLikeStructuredPlannerPayload(oneLine: string): boolean {
  return (
    /"assistant_markdown"\s*:/.test(oneLine) ||
    /"destinations"\s*:/.test(oneLine) ||
    /"profile_pills"\s*:/.test(oneLine) ||
    /"itinerary"\s*:/.test(oneLine) ||
    (/[\[{]/.test(oneLine[0] || "") && /"day"\s*:/.test(oneLine))
  );
}

export type StreamCaptionOpts = {
  /** Workspace JSON vs day-by-day itinerary JSON labels */
  structuredStreamMode?: StructuredStreamMode;
  /** Longer tails for the expanded-card streaming overlay log */
  overlay?: boolean;
};

/** Map Cursor SSE + our phase events to traveler-friendly status lines. */
export function streamEventToCaption(
  ev: Record<string, unknown>,
  assistantDraft: { text: string },
  thinkingDraft: { text: string },
  opts?: StreamCaptionOpts,
): string {
  const structuredMode = opts?.structuredStreamMode ?? "workspace";
  const overlay = Boolean(opts?.overlay);
  const thinkWindow = overlay ? 3200 : 360;
  const structuredTail = overlay ? 560 : 420;
  const proseWindowMax = overlay ? 1400 : 900;

  if (ev.type === "phase" && typeof ev.message === "string") {
    if (/parsing/i.test(ev.message)) thinkingDraft.text = "";
    return ev.message;
  }
  if (ev.type === "shortlist_drop_blocked") {
    const ids = (ev as { destination_ids?: unknown }).destination_ids;
    const n = Array.isArray(ids) ? ids.length : 0;
    if (n > 0) return `Kept ${n} destination card(s) the model left out — confirm if you want to remove them…`;
    return "";
  }
  if (ev.type === "thinking_delta" && typeof ev.delta === "string") {
    thinkingDraft.text += ev.delta;
    const t = thinkingDraft.text.replace(/\s+/g, " ").trim();
    if (!t) return "";
    const windowed = t.length > thinkWindow ? `…${t.slice(-thinkWindow)}` : t;
    return overlay ? windowed : `Considering options — ${windowed}`;
  }
  if (ev.type === "run_status" && typeof ev.status === "string") {
    return runStatusCaption(ev.status) ?? "";
  }
  if (ev.type === "tool_call") {
    return "Gathering a bit more context to compare places…";
  }
  if (ev.type === "run_result" && typeof ev.status === "string") {
    return runStatusCaption(ev.status) ?? "";
  }
  if (ev.type === "assistant_delta" && typeof ev.delta === "string") {
    assistantDraft.text += ev.delta;
    const oneLine = assistantDraft.text.replace(/\s+/g, " ").trim();
    if (!oneLine) return "";
    if (looksLikeStructuredPlannerPayload(oneLine)) {
      if (overlay) {
        return overlayStructuredPlannerCaption(thinkingDraft, structuredMode, thinkWindow);
      }
      return structuredPlannerStreamCaption(oneLine, thinkingDraft, structuredMode, structuredTail);
    }
    const proseWindow = oneLine.length > proseWindowMax ? `…${oneLine.slice(-proseWindowMax)}` : oneLine;
    return overlay ? proseWindow : `Writing your plan — ${proseWindow}`;
  }
  if (ev.type === "cursor_event") return "";
  return "";
}
