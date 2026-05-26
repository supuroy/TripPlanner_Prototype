/**
 * Merge planner SSE / wait-spinner captions into a rolling log for the card streaming overlay.
 */

import { isRotatingWaitCaption } from "./streamCaption";

function normalizeStreamLine(s: string): string {
  return s.replace(/\s+/g, " ").trim();
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
