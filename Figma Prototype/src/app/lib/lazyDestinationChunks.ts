import { lazy } from "react";

type LogisticsModule = typeof import("../components/LogisticsPlanFlipFace");
type CostModule = typeof import("../components/BookingCostBreakdown");

let logisticsChunk: Promise<LogisticsModule> | null = null;
let costChunk: Promise<CostModule> | null = null;

/** Start loading logistics flip UI (hover/focus on route button or before flip). */
export function prefetchLogisticsFlipFace(): void {
  logisticsChunk ??= import("../components/LogisticsPlanFlipFace");
  void logisticsChunk;
}

/** Start loading cost breakdown when live booking rows may appear. */
export function prefetchBookingCostBreakdown(): void {
  costChunk ??= import("../components/BookingCostBreakdown");
  void costChunk;
}

export const LogisticsPlanFlipFaceLazy = lazy(() =>
  (logisticsChunk ?? import("../components/LogisticsPlanFlipFace")).then((m) => ({
    default: m.LogisticsPlanFlipFace,
  })),
);

export const BookingCostBreakdownLazy = lazy(() =>
  (costChunk ?? import("../components/BookingCostBreakdown")).then((m) => ({
    default: m.BookingCostBreakdown,
  })),
);
