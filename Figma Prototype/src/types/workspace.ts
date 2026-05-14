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
