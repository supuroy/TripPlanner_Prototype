import type { DestCard } from "../components/TripStore";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Yield so React 18 can commit ``setState`` from ``applyWanderlyWorkspace`` before we read refs. */
function yieldToReactCommit(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

export type PlanReviewReadinessSnapshot = {
  destinations: DestCard[];
  itineraryLaneQueueDepth: number;
};

/**
 * Wait until destination cards exist in state, lazy ``build_itinerary`` queue is idle, and no
 * non-dismissed card is still ``itinerary_dirty``, so plan review runs against real data.
 */
export async function waitForItinerariesBeforePlanReview(
  getSnapshot: () => PlanReviewReadinessSnapshot,
  opts?: { pollMs?: number; maxWaitMs?: number },
): Promise<void> {
  const pollMs = opts?.pollMs ?? 450;
  const maxWaitMs = opts?.maxWaitMs ?? 600_000;
  await yieldToReactCommit();
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    const { destinations, itineraryLaneQueueDepth } = getSnapshot();
    if (itineraryLaneQueueDepth > 0) {
      await sleep(pollMs);
      continue;
    }
    const active = destinations.filter((d) => !d.dismissed);
    if (active.length === 0) {
      await sleep(pollMs);
      continue;
    }
    const anyDirty = active.some((d) => d.itinerary_dirty === true);
    if (!anyDirty) return;
    await sleep(pollMs);
  }
}
