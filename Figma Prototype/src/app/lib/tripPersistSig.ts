import type { DestCard, FlightOption, HotelOption, ProfilePill } from "../components/TripStore";

function hotelPersistSlice(h: HotelOption): unknown[] {
  return [
    h.name,
    h.area,
    h.price,
    h.nights,
    h.listing_url ?? "",
    h.selected_rank ?? 0,
    h.guest_rating ?? null,
    h.review_count ?? null,
    (h.ranked_picks ?? []).map((p) => [p.rank, p.name, p.price, p.listing_url ?? ""]),
  ];
}

function flightPersistSlice(f: FlightOption): unknown[] {
  return [
    f.airline,
    f.route,
    f.price,
    f.duration,
    f.stops ?? "",
    f.leg ?? "",
    f.search_url ?? "",
    f.selected_rank ?? 0,
    (f.ranked_picks ?? []).map((p) => [
      p.rank,
      p.airline,
      p.price,
      p.stops ?? "",
      p.duration,
    ]),
  ];
}

function destCardPersistSlice(d: DestCard): unknown {
  return {
    id: d.id,
    pinned: Boolean(d.pinned),
    dismissed: Boolean(d.dismissed),
    cost: d.cost,
    score: d.score,
    itinerary_len: d.itinerary?.length ?? 0,
    itinerary_dirty: Boolean(d.itinerary_dirty),
    hotels: (d.hotels ?? []).map(hotelPersistSlice),
    grounding: d.grounding?.trail_id ?? "",
    nps_outdoor: d.nps_outdoor?.fetched_at ?? Boolean(d.nps_outdoor?.ok),
    flights: (d.flights ?? []).map(flightPersistSlice),
    activities_len: d.activities?.length ?? 0,
  };
}

/** Destination-only fingerprint (booking + itinerary shape). */
export function computeTripDestDiskSig(destinations: DestCard[]): string {
  return JSON.stringify(destinations.map(destCardPersistSlice));
}

/** Full client payload fingerprint for ``sync_ui`` dirty gating. */
export function computeTripPersistSig(
  destinations: DestCard[],
  profile: ProfilePill[],
  tripId = "",
  tripName = "",
): string {
  return JSON.stringify({
    destinations: destinations.map(destCardPersistSlice),
    profile: profile.map((p) => [p.key, p.value]),
    trip_id: tripId.trim(),
    trip_name: (tripName.trim() || "Untitled trip").slice(0, 200),
  });
}
