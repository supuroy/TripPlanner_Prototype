import type { WanderlyOrchestration } from "../../api/wanderly";

/** Server default is on; set ``VITE_WANDERLY_INLINE_ITINERARY=0`` to disable inline-in-refine attempts. */
export function inlineItineraryRefineEnabled(): boolean {
  const raw = String(import.meta.env.VITE_WANDERLY_INLINE_ITINERARY ?? "1").trim().toLowerCase();
  return raw !== "0" && raw !== "false" && raw !== "off";
}

function focusIds(orch: WanderlyOrchestration | null | undefined, expandedDestId: string | null): string[] {
  if (!orch) return [];
  const raw = Array.isArray(orch.focus_destination_ids) ? orch.focus_destination_ids : [];
  const ids = raw.map((x) => String(x).trim()).filter(Boolean);
  const ex = (expandedDestId || "").trim();
  if (ex && !ids.includes(ex)) ids.push(ex);
  return ids;
}

/**
 * True when orchestration implies rebuilding itineraries on the whole shortlist (not just the open card).
 */
export function needsTripWideItineraryConfirm(orch: WanderlyOrchestration | null | undefined): boolean {
  if (!orch) return false;
  const action = String(orch.workspace_action || "").trim();
  const refresh = String(orch.itinerary_refresh || "auto").toLowerCase().trim();
  if (refresh === "none") return false;
  if (refresh === "all") return true;
  if (action === "refine_workspace" && refresh !== "focus_only") return true;
  if (action === "explicit_remove_destinations") return true;
  return false;
}

export function confirmTripWideItineraryRebuild(destName?: string): boolean {
  const focus = destName?.trim()
    ? `You have **${destName}** open, but this request may refresh day-by-day plans on **every** destination card.`
    : "This request may refresh day-by-day plans on **every** destination card.";
  return window.confirm(
    `${focus}\n\nContinue with a trip-wide planner update? Choose Cancel to rephrase for just this card (e.g. "redo only ${destName || "this destination"}'s itinerary").`,
  );
}

/**
 * Skip fat refine; rebuild itinerary for the focused card only (lazy builder with user message).
 */
export function isLazyOnlyCardItineraryEdit(
  orch: WanderlyOrchestration | null | undefined,
  expandedDestId: string,
): boolean {
  if (!orch) return false;
  const action = String(orch.workspace_action || "").trim();
  if (action !== "targeted_itinerary_edit") return false;
  const refresh = String(orch.itinerary_refresh || "auto").toLowerCase().trim();
  if (refresh === "all") return false;
  const delivery = String(orch.itinerary_delivery || "auto").toLowerCase().trim();
  if (delivery === "inline_in_refine" || delivery === "none") return false;
  const focus = focusIds(orch, expandedDestId);
  if (focus.length !== 1 || focus[0] !== expandedDestId) return false;
  return delivery === "lazy_build" || refresh === "focus_only";
}

/** Bias orchestration for card chat when server omitted focus ids on a targeted edit. */
export function orchestrationForCardChat(
  orch: WanderlyOrchestration | null | undefined,
  expandedDestId: string,
): WanderlyOrchestration | null {
  if (!orch) return null;
  const action = String(orch.workspace_action || "").trim();
  if (action !== "targeted_itinerary_edit") return orch;
  const focus = focusIds(orch, expandedDestId);
  if (focus.length === 1 && focus[0] === expandedDestId) return orch;
  return {
    ...orch,
    itinerary_refresh: "focus_only",
    focus_destination_ids: [expandedDestId],
    itinerary_delivery:
      String(orch.itinerary_delivery || "auto").toLowerCase() === "inline_in_refine"
        ? "inline_in_refine"
        : "lazy_build",
  };
}
