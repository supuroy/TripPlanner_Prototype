/**
 * Merge planner SSE / wait-spinner captions into a rolling log for the card streaming overlay.
 */

import { isRotatingWaitCaption, OVERLAY_STRUCTURED_PLANNER_STATUS } from "./streamCaption";

const OVERLAY_PLANNER_STATUS_LINES = new Set<string>(Object.values(OVERLAY_STRUCTURED_PLANNER_STATUS));

function normalizeStreamLine(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

const STRUCTURED_PLANNER_SECTION =
  /(?:Filling sidebar & trip cards —|Building your day-by-day outline —)/;

function isStructuredPlannerStreamCaption(s: string): boolean {
  return STRUCTURED_PLANNER_SECTION.test(s);
}

/** JSON / outline tail after the `` — `` section header (used for incremental compare). */
function structuredPlannerTail(s: string): string {
  const m = s.match(/(?:Filling sidebar & trip cards —|Building your day-by-day outline —)([\s\S]*)$/);
  return m ? m[1].trim() : s.trim();
}

/** True when ``next`` is the same stream line growing (thinking / JSON tail), not a new thought. */
function isIncrementalStreamUpdate(last: string, next: string): boolean {
  const a = normalizeStreamLine(last);
  const b = normalizeStreamLine(next);
  if (!a || !b) return false;
  if (b === a) return true;
  if (b.startsWith(a) || a.startsWith(b)) return true;
  const plen = Math.min(40, a.length, b.length);
  if (plen >= 20 && a.slice(0, plen) === b.slice(0, plen)) return true;
  if (b.startsWith("…") && a.length > 60) {
    const tail = a.slice(-72);
    if (b.includes(tail.slice(0, 36))) return true;
  }
  /** Sliding windows (``…`` + last N chars) — same stream, window moved forward. */
  if (a.length > 48 && b.length > 48) {
    const tailLen = Math.min(96, a.length, b.length);
    const aTail = a.slice(-tailLen);
    const bTail = b.slice(-tailLen);
    if (aTail === bTail) return true;
    if (bTail.startsWith(aTail.slice(0, Math.min(48, aTail.length)))) return true;
    if (aTail.startsWith(bTail.slice(0, Math.min(48, bTail.length)))) return true;
  }
  if (isStructuredPlannerStreamCaption(last) || isStructuredPlannerStreamCaption(next)) {
    const aJson = structuredPlannerTail(last);
    const bJson = structuredPlannerTail(next);
    if (aJson && bJson && (bJson.startsWith(aJson) || aJson.startsWith(bJson))) return true;
  }
  return false;
}

/** Replace the last paragraph when the same section prefix updates (e.g. growing JSON preview). */
export function mergeStreamCaptionLog(log: string, nextCaption: string): string {
  const next = nextCaption.trim();
  if (!next) return log;
  if (!log) return next;
  if (log === next) return log;
  if (next.startsWith(log)) return next;
  if (log.endsWith(next)) return log;

  /**
   * Planner workspace/itinerary overlay captions are two paragraphs
   * (thinking + ``Filling sidebar… — {json tail}``). Only the last paragraph was
   * compared before, so each token appended the full dual-paragraph string again.
   */
  if (OVERLAY_PLANNER_STATUS_LINES.has(next)) {
    if (!log || OVERLAY_PLANNER_STATUS_LINES.has(log) || isIncrementalStreamUpdate(log, next)) {
      return next;
    }
  }

  if (isStructuredPlannerStreamCaption(next)) {
    if (!log || isStructuredPlannerStreamCaption(log)) {
      const logTail = structuredPlannerTail(log);
      const nextTail = structuredPlannerTail(next);
      if (
        !log ||
        isIncrementalStreamUpdate(log, next) ||
        isIncrementalStreamUpdate(logTail, nextTail)
      ) {
        return next;
      }
    }
  }

  const parts = log.split("\n\n");
  const last = parts[parts.length - 1] ?? "";

  if (last && isIncrementalStreamUpdate(last, next)) {
    parts[parts.length - 1] = next;
    return parts.join("\n\n");
  }

  /** Orchestrate/advisor/web spinners cycle the same 2–3 lines — swap in place only. */
  if (isRotatingWaitCaption(next)) {
    if (isRotatingWaitCaption(last)) {
      parts[parts.length - 1] = next;
      return parts.join("\n\n");
    }
    /** Replace routing / short status lines; do not clobber long live model streams. */
    if (last && !isRotatingWaitCaption(last)) {
      if (last.startsWith("Routing:") || last.length < 140) {
        parts[parts.length - 1] = next;
        return parts.join("\n\n");
      }
      return log;
    }
    return log ? `${log}\n\n${next}` : next;
  }

  /** First real model line after a spinner — replace the spinner slot. */
  if (!isRotatingWaitCaption(next) && isRotatingWaitCaption(last)) {
    parts[parts.length - 1] = next;
    return parts.join("\n\n");
  }

  const section = next.includes(" — ") ? next.split(" — ")[0]?.trim() : "";
  if (section && last.startsWith(section)) {
    parts[parts.length - 1] = next;
    return parts.join("\n\n");
  }

  return `${log}\n\n${next}`;
}
