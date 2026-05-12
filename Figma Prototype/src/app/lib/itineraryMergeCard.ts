import type { DestCard, ItinDay } from "../components/TripStore";
import type { ItineraryBudgetGuidance } from "../../types/workspace";

/** Merge lazy itinerary rows + optional LLM budget bands (+ optional booking URL hints) onto a destination card. */
export function mergeItineraryBuildIntoDest(
  card: DestCard,
  rows: ItinDay[],
  budget?: ItineraryBudgetGuidance | null,
  bookingSearchHints?: DestCard["booking_search_hints"] | null,
): DestCard {
  if (rows.length === 0) return { ...card, itinerary_dirty: false };
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
  const flightLine = flightShort || flightB || "";
  const lodgingLine = lodgingShort || lodging || "";
  let next: DestCard = {
    ...card,
    itinerary: rows,
    itinerary_dirty: false,
    ...(hasBg && budget ? { budget_guidance: { ...budget } } : {}),
    ...(bookingSearchHints && (bookingSearchHints.flight_to_label || bookingSearchHints.flight_return_from_label)
      ? { booking_search_hints: { ...bookingSearchHints } }
      : {}),
  };
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
  if (flightLine && next.flights?.length) {
    next = { ...next, flights: next.flights.map((f, i) => (i === 0 ? { ...f, price: flightLine } : f)) };
  }
  if (lodgingLine && next.hotels?.length) {
    next = { ...next, hotels: next.hotels.map((h, i) => (i === 0 ? { ...h, price: lodgingLine } : h)) };
  }
  return next;
}
