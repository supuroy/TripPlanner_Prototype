import type { DestCard, ItinDay } from "../components/TripStore";
import type { ItineraryQaResult } from "../../api/wanderly";
import type { ItineraryBudgetGuidance, WanderlyBookingHooks, WanderlyTransportPlan } from "../../types/workspace";
import { syncCardLogisticsFromItinerary } from "./syncCardLogisticsFromItinerary";
import { logItin } from "./itineraryDebug";

/** Merge lazy itinerary rows + optional LLM budget bands (+ optional booking URL hints) onto a destination card. */
export function mergeItineraryBuildIntoDest(
  card: DestCard,
  rows: ItinDay[],
  budget?: ItineraryBudgetGuidance | null,
  bookingSearchHints?: DestCard["booking_search_hints"] | null,
  needsCommercialFlight?: boolean | null,
  transportPlan?: WanderlyTransportPlan | null,
  bookingHooks?: WanderlyBookingHooks | null,
  itineraryQa?: ItineraryQaResult | null,
  alltrailsSearchContext?: DestCard["alltrails_search_context"] | null,
): DestCard {
  if (rows.length === 0) return { ...card, itinerary_dirty: false, itinerary_refinement_hint: undefined };
  const ballpark = budget?.trip_ballpark_usd_pp?.trim();
  const ballparkShort = budget?.trip_usd_pp_short?.trim();
  const flightB = budget?.flight_ballpark?.trim();
  const flightShort = budget?.flight_band_short?.trim();
  const lodging = budget?.lodging_style?.trim();
  const lodgingShort = budget?.lodging_band_short?.trim();
  const notes = budget?.notes?.trim();
  const hasBg = Boolean(
    ballpark || ballparkShort || flightB || flightShort || lodging || lodgingShort || notes,
  );
  const costLine = ballparkShort || ballpark || "";
  let next: DestCard = {
    ...card,
    itinerary: rows,
    itinerary_dirty: false,
    itinerary_refinement_hint: undefined,
    ...(hasBg && budget ? { budget_guidance: { ...budget } } : {}),
    ...(bookingSearchHints && (bookingSearchHints.flight_to_label || bookingSearchHints.flight_return_from_label)
      ? { booking_search_hints: { ...bookingSearchHints } }
      : {}),
    ...(typeof needsCommercialFlight === "boolean" ? { needs_commercial_flight: needsCommercialFlight } : {}),
    ...(transportPlan && String(transportPlan.primary_mode || "").trim() ? { transport_plan: transportPlan } : {}),
    ...(bookingHooks && bookingHooks.transport ? { booking_hooks: bookingHooks } : {}),
    ...(itineraryQa && !itineraryQa.skipped ? { itinerary_qa: itineraryQa } : {}),
    ...(alltrailsSearchContext?.trail_name?.trim()
      ? { alltrails_search_context: alltrailsSearchContext }
      : {}),
  };
  if (alltrailsSearchContext?.trail_name?.trim()) {
    logItin("merge_itinerary_trail_context", {
      destId: card.id,
      hasParentUrl: Boolean(
        (alltrailsSearchContext as { parent_alltrails_url?: string }).parent_alltrails_url?.trim(),
      ),
    });
  }
  if (costLine) next = { ...next, cost: costLine };
  else next = { ...next, cost: "" };
  const baseDisc = "Broad planning estimate from itinerary pass — not a quote or live fare.";
  if (notes || ballpark || ballparkShort) {
    next = {
      ...next,
      commerce: {
        destination: next.name,
        typical_range_usd: next.commerce?.typical_range_usd ?? null,
        disclaimer: [baseDisc, notes].filter(Boolean).join(" ").slice(0, 900),
      },
    };
  }
  return syncCardLogisticsFromItinerary(next, card);
}
