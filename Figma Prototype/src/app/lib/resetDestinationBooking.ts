import type { DestCard } from "../components/TripStore";

/**
 * Clear live booking / “Ready to book” state when trip fundamentals change (profile, itinerary stale).
 * Avoids stale Expedia rows (wrong airport / dates) after lazy itinerary rebuilds.
 */
export function resetDestinationBookingState(d: DestCard): DestCard {
  return {
    ...d,
    readyToBook: false,
    liveBookingEstimates: false,
    flights: [],
    hotels: [],
    booking_search_outline: undefined,
    booking_search_hints: undefined,
  };
}
