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

export interface WanderlyWorkspaceDTO {
  assistant_markdown: string;
  profile_pills: WanderlyProfilePillDTO[];
  destinations: Record<string, unknown>[];
  agent_status_hint?: string | null;
  enrichment_pending?: boolean;
  /** Set after POST /api/wanderly/enrich_heroes when images were resolved. */
  hero_images_enriched?: boolean;
  weather_by_destination_id?: Record<string, string>;
  commerce_disclaimer?: string;
  advisor_suggestion?: Record<string, unknown> | null;
  breadcrumb?: { stage?: string; status?: string }[];
}
