import type { FlightOption, FlightRankedPick } from "../components/TripStore";

/** Resolve card fields for the user's current selection (default rank 1). */
export function resolveDisplayedFlight(flight: FlightOption): FlightOption {
  const rank = flight.selected_rank ?? 1;
  const picks = flight.ranked_picks;
  if (!picks?.length) return { ...flight, best: true };
  const pick = picks.find((p) => p.rank === rank) ?? picks[0];
  return {
    ...flight,
    airline: pick.airline,
    route: pick.route,
    duration: pick.duration,
    price: pick.price,
    stops: pick.stops ?? flight.stops,
    departure_time: pick.departure_time ?? flight.departure_time,
    arrival_time: pick.arrival_time ?? flight.arrival_time,
    search_url: pick.search_url ?? flight.search_url,
    airline_logo_url: pick.airline_logo_url ?? flight.airline_logo_url,
    pick_score: pick.pick_score,
    leg: flight.leg,
    round_trip: flight.round_trip,
    selected_rank: rank,
    ranked_picks: picks,
    best: true,
    isPlaceholder: flight.isPlaceholder,
  };
}

export function flightWithSelectedRank(flight: FlightOption, rank: number): FlightOption {
  const picks = flight.ranked_picks;
  if (!picks?.length) return { ...flight, selected_rank: rank, best: true };
  const pick = picks.find((p) => p.rank === rank);
  if (!pick) return { ...flight, best: true };
  return {
    ...flight,
    airline: pick.airline,
    route: pick.route,
    duration: pick.duration,
    price: pick.price,
    stops: pick.stops,
    departure_time: pick.departure_time,
    arrival_time: pick.arrival_time,
    search_url: pick.search_url ?? flight.search_url,
    airline_logo_url: pick.airline_logo_url ?? flight.airline_logo_url,
    pick_score: pick.pick_score,
    selected_rank: rank,
    ranked_picks: picks,
    best: true,
    isPlaceholder: false,
  };
}

export function otherRankedFlightPicks(flight: FlightOption): FlightRankedPick[] {
  const rank = flight.selected_rank ?? 1;
  return (flight.ranked_picks ?? []).filter((p) => p.rank !== rank);
}

/** Merge API search result onto a destination flight row (preserve alternatives). */
export function mergeFlightFromSearch(existing: FlightOption, incoming: FlightOption): FlightOption {
  const prevRank = existing.selected_rank ?? incoming.selected_rank ?? 1;
  return {
    ...existing,
    ...incoming,
    ranked_picks: incoming.ranked_picks ?? existing.ranked_picks,
    selected_rank: incoming.selected_rank ?? prevRank,
    search_url: incoming.search_url || existing.search_url,
    leg: incoming.leg ?? existing.leg,
    round_trip: incoming.round_trip ?? existing.round_trip,
    best: true,
    isPlaceholder: false,
  };
}
