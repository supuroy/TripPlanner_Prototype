/**
 * Client-side debug for lazy itinerary fetches (expanded card vs background prefetch).
 * Enable: dev server, or `localStorage.setItem("WANDERLY_DEBUG_ITIN","1")`, or `VITE_WANDERLY_DEBUG_ITIN=1` in .env
 */
export function wanderlyItinDebugEnabled(): boolean {
  if (import.meta.env.DEV) return true;
  if (import.meta.env.VITE_WANDERLY_DEBUG_ITIN === "1") return true;
  try {
    return typeof window !== "undefined" && window.localStorage?.getItem("WANDERLY_DEBUG_ITIN") === "1";
  } catch {
    return false;
  }
}

export function logItin(phase: string, data: Record<string, unknown>): void {
  if (!wanderlyItinDebugEnabled()) return;
  const c = globalThis.console;
  if (c && typeof c.info === "function") c.info("[Wanderly:itin]", phase, data);
}
