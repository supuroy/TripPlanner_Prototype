/** Correlates one user turn (smoke/refine) with server ``TIMELINE`` + itinerary logs via ``X-Wanderly-Trace-Id``. */

let activeTrace: string | null = null;

export function newWanderlyTraceId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `tr-${crypto.randomUUID()}`;
  }
  return `tr-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function setWanderlyTurnTrace(id: string | null): void {
  activeTrace = id && id.trim() ? id.trim().slice(0, 128) : null;
}

export function wanderlyActiveTraceHeaders(): Record<string, string> {
  if (!activeTrace) return {};
  return { "X-Wanderly-Trace-Id": activeTrace };
}
