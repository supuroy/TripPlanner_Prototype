/** Mirrors Python WanderlyWorkspaceDTO / smoke responses (subset for UI). */

export interface WanderlyProfilePillDTO {
  key: string;
  label: string;
  value: string;
  icon: string;
  editable?: boolean;
  options?: string[];
}

export interface WanderlyCommerceStub {
  destination?: string;
  typical_range_usd?: number | null;
  currency?: string;
  disclaimer?: string;
}

/** Rough bands from lazy itinerary LLM — not quotes or live fares. */
export interface ItineraryBudgetGuidance {
  trip_ballpark_usd_pp?: string;
  lodging_style?: string;
  flight_ballpark?: string;
  notes?: string;
  /** ≤~40 chars for compact destination cards (parallel to long fields). */
  trip_usd_pp_short?: string;
  flight_band_short?: string;
  lodging_band_short?: string;
}

/** Structured transport from lazy itinerary LLM — primary signal for OTA flight vs ground-only booking. */
export interface WanderlyTransportPlan {
  primary_mode?: "ground_only" | "commercial_air" | "mixed" | string;
  summary?: string;
  commercial_flight_segments?: Array<{
    role?: string;
    from_search_label?: string;
    to_search_label?: string;
    day_hint?: number;
    day_offset?: number;
  }>;
  lodging_moves?: Array<{ day_range?: string; area?: string; note?: string; nights?: number }>;
}

/** One commercial leg for Expedia-style URL builders (day scope). */
export interface DayLogisticsFlightSegment {
  from_search_label?: string;
  to_search_label?: string;
  role?: "inbound" | "within_trip" | "return";
}

/** Omit or set segments empty at authoring time; server normalizes to null when empty. */
export interface DayLogisticsFlight {
  segments: DayLogisticsFlightSegment[];
}

export interface DayLogisticsGuests {
  adults?: number;
  children?: number;
  infants_in_seat?: number;
}

export interface DayLogisticsTravelerParty {
  /** adults + children + infants_in_seat */
  total?: number;
  adults?: number;
  children?: number;
  infants_in_seat?: number;
}

/** Hotel search slice for this calendar day (check-in night). */
export interface DayLogisticsLodging {
  destination_search_label?: string;
  area_hint?: string;
  /** ISO YYYY-MM-DD when known from traveler context */
  check_in?: string;
  check_out?: string;
  rooms?: number;
  guests?: DayLogisticsGuests;
  /** Explicit headcount mirror for OTA party fields (server fills from guests when omitted). */
  traveler_party?: DayLogisticsTravelerParty;
  /** When true, hotel search should apply pet-friendly filters; false means explicitly no pet filter. */
  pet_friendly?: boolean;
  room_preferences?: string[];
  /** Short tags for search/ranking (accessibility, late arrival, EV, ferry risk, …). */
  booking_factors?: string[];
}

export interface DayLogisticsSurfaceLeg {
  kind: "drive" | "ferry" | "train" | "walk" | "other";
  from_label?: string;
  to_label?: string;
  operator_hint?: string;
}

/**
 * Machine-readable logistics for one itinerary day (parallel to prose bullets).
 * Flight is null when no airline segment that day; lodging null when no overnight stay.
 */
export interface DayLogistics {
  flight: DayLogisticsFlight | null;
  lodging: DayLogisticsLodging | null;
  surface_legs?: DayLogisticsSurfaceLeg[];
}

export interface WanderlyBookingHooks {
  version?: number;
  transport?: WanderlyTransportPlan;
  search_preferences?: Record<string, unknown>;
}

export interface WanderlyTripMeta {
  trip_id: string;
  trip_name: string;
}

export interface WanderlyWorkspaceDTO {
  assistant_markdown: string;
  profile_pills: WanderlyProfilePillDTO[];
  destinations: Record<string, unknown>[];
  /** Client-owned trip label + stable id (also persisted server-side via ``sync_ui``). */
  trip_meta?: WanderlyTripMeta | null;
  agent_status_hint?: string | null;
  enrichment_pending?: boolean;
  /** Set after POST /api/wanderly/enrich_heroes when images were resolved. */
  hero_images_enriched?: boolean;
  weather_by_destination_id?: Record<string, string>;
  commerce_disclaimer?: string;
  advisor_suggestion?: Record<string, unknown> | null;
  breadcrumb?: { stage?: string; status?: string }[];
}
