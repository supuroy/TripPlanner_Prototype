import { apiFetch, apiFetchBlob, apiPostSseJson } from "./client";
import { isPlaceholderProfilePills } from "../app/lib/wanderlyProfilePills";
import type {
  ItineraryBudgetGuidance,
  WanderlyBookingHooks,
  WanderlyProfilePillDTO,
  WanderlyTransportPlan,
  WanderlyTripMeta,
  WanderlyWorkspaceDTO,
} from "../types/workspace";
import type { FlightOption, HotelOption, ItinDay, ProfilePill } from "../app/components/TripStore";
import { logItin } from "../app/lib/itineraryDebug";

function sleepMs(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isLikely409Conflict(err: unknown): boolean {
  const msg = String(err instanceof Error ? err.message : err);
  return /\b409\b/i.test(msg) || /conflict/i.test(msg);
}

async function apiFetchWith409Retry<T>(path: string, init: RequestInit | undefined, attempts: number): Promise<T> {
  let last: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await apiFetch<T>(path, init);
    } catch (e) {
      last = e;
      if (i < attempts - 1 && isLikely409Conflict(e)) {
        await sleepMs(2000 + Math.floor(Math.random() * 1500));
        continue;
      }
      throw e;
    }
  }
  throw last;
}

/** Download a destination itinerary as PDF (server Playwright render). */
export async function exportItineraryPdf(
  sessionId: string,
  destination: Record<string, unknown>,
  profilePills: ProfilePill[],
  opts?: { signal?: AbortSignal },
): Promise<{ blob: Blob; filename: string }> {
  const destinationId = String(destination.id ?? "").trim();
  return apiFetchBlob("/api/wanderly/export_itinerary_pdf", {
    method: "POST",
    signal: opts?.signal,
    body: JSON.stringify({
      session_id: sessionId,
      destination_id: destinationId,
      destination,
      profile_pills: profilePills.map((p) => ({
        key: p.key,
        label: p.label,
        value: p.value,
        emoji: p.icon,
        icon: p.icon,
      })),
    }),
  });
}

export async function smokeTurn(sessionId: string, message: string) {
  return apiFetch<{ session_id: string; wanderly: WanderlyWorkspaceDTO; parse_ok?: boolean }>(
    "/api/wanderly/smoke_turn",
    {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, message }),
    },
  );
}

/** Cursor smoke with SSE progress (`data:` JSON lines). */
export async function smokeTurnStream(
  sessionId: string,
  message: string,
  onEvent: (e: Record<string, unknown>) => void,
  opts?: {
    signal?: AbortSignal;
    /** Fires when SSE ``done`` arrives (before stream close). Used for parallel post-refine prefetch. */
    onWorkspaceReady?: (res: {
      session_id: string;
      wanderly: WanderlyWorkspaceDTO;
      parse_ok?: boolean;
    }) => void;
  },
): Promise<{ session_id: string; wanderly: WanderlyWorkspaceDTO; parse_ok?: boolean }> {
  let result: { session_id: string; wanderly: WanderlyWorkspaceDTO; parse_ok?: boolean } | undefined;
  await apiPostSseJson(
    "/api/wanderly/smoke_turn_stream",
    { session_id: sessionId, message },
    (obj) => {
      onEvent(obj);
      if (obj.type === "error" || obj.type === "stream_error") {
        throw new Error(String((obj as { message?: string }).message || "Stream error"));
      }
      if (obj.type === "done" && obj.session_id != null && obj.wanderly != null) {
        const ready = {
          session_id: String(obj.session_id),
          wanderly: obj.wanderly as WanderlyWorkspaceDTO,
          parse_ok: Boolean(obj.parse_ok),
        };
        result = ready;
        opts?.onWorkspaceReady?.(ready);
      }
    },
    opts?.signal ? { signal: opts.signal } : undefined,
  );
  if (!result) throw new Error("Stream closed before completion");
  return result;
}

export type WanderlyRefineResponse = {
  session_id: string;
  wanderly: WanderlyWorkspaceDTO;
  parse_ok?: boolean;
  shortlist_drop_blocked?: boolean;
  model_omitted_destination_ids?: string[];
  /** Server validated inline day-by-day plans — skip lazy build for these ids. */
  inline_itinerary_applied_ids?: string[];
  /** Refine did not produce complete itineraries — client should lazy-build these ids. */
  lazy_itinerary_needed_ids?: string[];
};

export async function refineTurn(
  sessionId: string,
  message: string,
  opts?: {
    confirmWorkspaceDrop?: boolean;
    orchestration?: WanderlyOrchestration | null;
    focusDestinationId?: string;
    /** Live board cards — used when server session lost the shortlist. */
    clientDestinations?: Record<string, unknown>[];
  },
): Promise<WanderlyRefineResponse> {
  const body: Record<string, unknown> = { session_id: sessionId, message };
  if (opts?.confirmWorkspaceDrop) body.confirm_workspace_drop = true;
  if (opts?.orchestration) body.orchestration = opts.orchestration;
  if (opts?.focusDestinationId) body.focus_destination_id = opts.focusDestinationId;
  if (opts?.clientDestinations?.length) body.client_destinations = opts.clientDestinations;
  return apiFetch<WanderlyRefineResponse>("/api/wanderly/refine", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Cursor refine with SSE progress. */
export async function refineTurnStream(
  sessionId: string,
  message: string,
  onEvent: (e: Record<string, unknown>) => void,
  opts?: {
    confirmWorkspaceDrop?: boolean;
    signal?: AbortSignal;
    orchestration?: WanderlyOrchestration | null;
    focusDestinationId?: string;
    clientDestinations?: Record<string, unknown>[];
    /** Fires when SSE ``done`` arrives (before stream close). Used for parallel post-refine prefetch. */
    onWorkspaceReady?: (res: WanderlyRefineResponse) => void;
  },
): Promise<WanderlyRefineResponse> {
  let result: WanderlyRefineResponse | undefined;
  const body: Record<string, unknown> = { session_id: sessionId, message };
  if (opts?.confirmWorkspaceDrop) body.confirm_workspace_drop = true;
  if (opts?.orchestration) body.orchestration = opts.orchestration;
  if (opts?.focusDestinationId) body.focus_destination_id = opts.focusDestinationId;
  if (opts?.clientDestinations?.length) body.client_destinations = opts.clientDestinations;
  await apiPostSseJson(
    "/api/wanderly/refine_stream",
    body,
    (obj) => {
      onEvent(obj);
      if (obj.type === "error" || obj.type === "stream_error") {
        throw new Error(String((obj as { message?: string }).message || "Stream error"));
      }
      if (obj.type === "done" && obj.session_id != null && obj.wanderly != null) {
        const ready: WanderlyRefineResponse = {
          session_id: String(obj.session_id),
          wanderly: obj.wanderly as WanderlyWorkspaceDTO,
          parse_ok: Boolean(obj.parse_ok),
          shortlist_drop_blocked: Boolean(obj.shortlist_drop_blocked),
          model_omitted_destination_ids: Array.isArray(obj.model_omitted_destination_ids)
            ? (obj.model_omitted_destination_ids as string[])
            : undefined,
          inline_itinerary_applied_ids: Array.isArray(obj.inline_itinerary_applied_ids)
            ? (obj.inline_itinerary_applied_ids as string[])
            : obj.inline_itinerary_applied_ids === undefined
              ? undefined
              : [],
          lazy_itinerary_needed_ids: Array.isArray(obj.lazy_itinerary_needed_ids)
            ? (obj.lazy_itinerary_needed_ids as string[])
            : obj.lazy_itinerary_needed_ids === undefined
              ? undefined
              : [],
        };
        result = ready;
        opts?.onWorkspaceReady?.(ready);
      }
    },
    opts?.signal ? { signal: opts.signal } : undefined,
  );
  if (!result) throw new Error("Stream closed before completion");
  return result;
}

export type WanderlyWorkspaceAction =
  | "refine_workspace"
  | "targeted_itinerary_edit"
  | "compare_places"
  | "lightweight_qa"
  | "explicit_remove_destinations"
  | "ambiguous";

export type WanderlyOrchestration = {
  workspace_action: WanderlyWorkspaceAction;
  confidence: number;
  rationale: string;
  /** Which destinations need lazy itinerary rebuild after refine (server LLM). */
  itinerary_refresh?: "auto" | "none" | "all" | "focus_only" | string;
  /** Prefer inline itinerary in refine JSON vs dedicated lazy build. */
  itinerary_delivery?: "auto" | "inline_in_refine" | "lazy_build" | "none" | string;
  focus_destination_ids?: string[];
  /** When false, canvas may allow switching cards during refine (single-card edits). */
  lock_canvas_navigation?: boolean;
};

export type WanderlyOrchestrateResponse = {
  session_id: string;
  parse_ok: boolean;
  orchestration: WanderlyOrchestration | null;
  fallback_reason?: string;
};

/** Cheap LLM routing: planner vs advisor vs explicit removal (server). */
export async function orchestrateTurn(
  sessionId: string,
  message: string,
  opts?: { focusDestinationId?: string },
): Promise<WanderlyOrchestrateResponse> {
  const body: Record<string, unknown> = { session_id: sessionId, message };
  if (opts?.focusDestinationId) body.focus_destination_id = opts.focusDestinationId;
  return apiFetch<WanderlyOrchestrateResponse>("/api/wanderly/orchestrate", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function planWithOrchestrator(sessionId: string, input: string) {
  return apiFetch<{ session_id: string; wanderly: WanderlyWorkspaceDTO; run: Record<string, unknown> }>(
    "/api/wanderly/plan",
    {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, input }),
    },
  );
}

export async function newTrip(sessionId: string) {
  return apiFetch<{ session_id: string; wanderly: WanderlyWorkspaceDTO }>("/api/wanderly/new_trip", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export type WanderlyTripSummary = {
  trip_id: string;
  trip_name: string;
  session_id: string;
  updated_at_ms: number;
};

export type WanderlyTripsBootstrapResponse = {
  trip_id: string;
  session_id: string;
  active_trip_id: string;
  trips: WanderlyTripSummary[];
  wanderly: WanderlyWorkspaceDTO;
};

/** One JSON file per trip + manifest; reconciles browser hints with disk (durable trip switch). */
export async function bootstrapWanderlyTrips(opts?: { session_id?: string; trip_id?: string }) {
  return apiFetch<WanderlyTripsBootstrapResponse>("/api/wanderly/trips/bootstrap", {
    method: "POST",
    body: JSON.stringify({
      session_id: opts?.session_id,
      trip_id: opts?.trip_id,
    }),
  });
}

export async function activateWanderlyTrip(tripId: string) {
  return apiFetch<WanderlyTripsBootstrapResponse>("/api/wanderly/trips/activate", {
    method: "POST",
    body: JSON.stringify({ trip_id: tripId }),
  });
}

export async function createWanderlyTrip(name?: string) {
  return apiFetch<WanderlyTripsBootstrapResponse>("/api/wanderly/trips/create", {
    method: "POST",
    body: JSON.stringify(name ? { name } : {}),
  });
}

export type WanderlyTripRenameResponse = {
  trip_id: string;
  trip_name: string;
  session_id: string;
  active_trip_id: string;
  trips: WanderlyTripSummary[];
  wanderly: WanderlyWorkspaceDTO;
};

export async function renameWanderlyTrip(tripId: string, name: string, sessionIdHint?: string) {
  const body: Record<string, unknown> = { trip_id: tripId, name };
  if (sessionIdHint) body.session_id = sessionIdHint;
  return apiFetch<WanderlyTripRenameResponse>("/api/wanderly/trips/rename", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function deleteWanderlyTrip(tripId: string) {
  return apiFetch<WanderlyTripsBootstrapResponse>("/api/wanderly/trips/delete", {
    method: "POST",
    body: JSON.stringify({ trip_id: tripId }),
  });
}

export type SyncUiResponse = {
  ok: boolean;
  /** False when session is not linked to a manifest trip (nothing written under ``wanderly_trips/``). */
  saved_to_disk?: boolean;
  trip_id?: string | null;
};

export async function fetchWorkspace(sessionId: string) {
  const q = new URLSearchParams({ session_id: sessionId });
  return apiFetch<{ wanderly: WanderlyWorkspaceDTO; wanderly_snapshot_restored?: boolean }>(
    `/api/wanderly/workspace?${q.toString()}`,
  );
}

export async function syncUi(
  sessionId: string,
  destinations: unknown[],
  opts?: { tripId?: string; tripMeta?: WanderlyTripMeta; profilePills?: ProfilePill[] },
) {
  const body: Record<string, unknown> = { session_id: sessionId, destinations };
  if (opts?.tripId) body.trip_id = opts.tripId;
  if (opts?.tripMeta && (String(opts.tripMeta.trip_id || "").trim() || String(opts.tripMeta.trip_name || "").trim())) {
    body.trip_meta = {
      trip_id: String(opts.tripMeta.trip_id || "").trim(),
      trip_name: String(opts.tripMeta.trip_name || "").trim().slice(0, 200),
    };
  }
  if (opts?.profilePills != null && Array.isArray(opts.profilePills) && !isPlaceholderProfilePills(opts.profilePills)) {
    body.profile_pills = opts.profilePills;
  }
  return apiFetch<SyncUiResponse>("/api/wanderly/sync_ui", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Wikipedia / Commons hero URLs + optional light Cursor pass for search hints (server). */
export async function enrichHeroes(sessionId: string, destinations: unknown[]) {
  return apiFetch<{
    session_id: string;
    wanderly: { destinations: Record<string, unknown>[]; hero_images_enriched?: boolean };
  }>("/api/wanderly/enrich_heroes", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, destinations }),
  });
}

/** Progressive hero: enrich one destination card (returns that row in ``wanderly.destinations``). */
export async function enrichHeroOne(sessionId: string, destination: Record<string, unknown>) {
  return apiFetch<{ session_id: string; wanderly: { destinations: Record<string, unknown>[] } }>(
    "/api/wanderly/enrich_hero_one",
    {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, destination }),
    },
  );
}

export type NpsAlert = {
  title?: string;
  description?: string;
  category?: string;
  url?: string;
  park_code?: string;
  last_indexed?: string;
};

export type NpsCampgroundMatch = {
  plan_camp_label?: string;
  name?: string | null;
  description?: string;
  reservation_url?: string;
  park_code?: string;
  fee_summary?: string[];
  operating_hours?: string;
  amenities?: string[];
  status?: string;
  notes?: string;
  match_score?: number;
  latitude?: number | null;
  longitude?: number | null;
};

export type NpsParkFees = {
  park_name?: string;
  park_code?: string;
  url?: string;
  entrance_fees?: Array<{ title?: string; cost?: string; description?: string }>;
  entrance_passes?: Array<{ title?: string; cost?: string; description?: string }>;
  other_fees?: string[];
  reservation_info?: string;
};

export type NpsOutdoorEnrichResponse = {
  ok?: boolean;
  configured?: boolean;
  in_scope?: boolean;
  message?: string;
  park_codes?: string[];
  resolve_method?: string;
  alerts?: NpsAlert[];
  parks?: NpsParkFees[];
  campgrounds?: NpsCampgroundMatch[];
  camp_matches?: NpsCampgroundMatch[];
  /** Planned camp labels sent to the matcher (for debugging). */
  camp_labels_queried?: string[];
  /** First N developed campground names returned from NPS for the resolved park(s). */
  campground_pool_sample?: string[];
  permits_from_plan?: Array<{
    name?: string;
    agency?: string;
    quota_window?: string;
    booking_url_hint?: string;
    notes?: string;
    required?: boolean;
  }>;
  fee_links?: Array<{ label?: string; url?: string }>;
  source?: string;
  docs_url?: string;
  fetched_at?: string;
};

export type ItineraryQaIssue = {
  code?: string;
  severity?: "error" | "warning" | "info" | string;
  message?: string;
  day?: number;
  suggestion?: string;
};

export type ItineraryQaResult = {
  ok?: boolean;
  issues?: ItineraryQaIssue[];
  error_count?: number;
  warning_count?: number;
  skipped?: boolean;
};

/** U.S. NPS alerts, campgrounds, and fees for hiking/camping destinations (requires NPS_API_KEY on server). */
export async function fetchNpsOutdoorEnrich(
  sessionId: string,
  destination: Record<string, unknown>,
  profilePills: Array<{ key: string; label?: string; value: string; icon?: string }>,
  opts?: {
    refresh?: boolean;
    signal?: AbortSignal;
    tripName?: string;
    peerDestinationNames?: string[];
  },
): Promise<NpsOutdoorEnrichResponse> {
  return apiFetch<NpsOutdoorEnrichResponse>("/api/wanderly/nps_outdoor", {
    method: "POST",
    signal: opts?.signal,
    body: JSON.stringify({
      session_id: sessionId,
      destination,
      profile_pills: profilePills,
      refresh: Boolean(opts?.refresh),
      trip_name: opts?.tripName?.trim() || undefined,
      peer_destination_names: opts?.peerDestinationNames?.length
        ? opts.peerDestinationNames
        : undefined,
    }),
  });
}

export type BookingSearchHints = {
  flight_to_label?: string;
  flight_return_from_label?: string;
};

export async function buildItinerary(
  sessionId: string,
  destination: Record<string, unknown>,
  profilePills: ProfilePill[],
  options?: { days?: number; signal?: AbortSignal; requestId?: string; refinementMessage?: string },
) {
  const body: Record<string, unknown> = {
    session_id: sessionId,
    destination,
    profile_pills: profilePills,
  };
  const days = options?.days;
  if (days != null && days > 0) body.days = days;
  const rm = options?.refinementMessage?.trim();
  if (rm) body.refinement_message = rm;
  const headers: Record<string, string> = {};
  if (options?.requestId) headers["X-Wanderly-Request-Id"] = options.requestId;
  return apiFetchWith409Retry<{
    session_id: string;
    destination_id: string;
    itinerary: ItinDay[];
    budget_guidance?: ItineraryBudgetGuidance | null;
    booking_search_hints?: BookingSearchHints | null;
    needs_commercial_flight?: boolean;
    transport_plan?: WanderlyTransportPlan | null;
    booking_hooks?: WanderlyBookingHooks | null;
    alltrails_search_context?: Record<string, unknown> | null;
  }>(
    "/api/wanderly/build_itinerary",
    {
      method: "POST",
      body: JSON.stringify(body),
      headers: Object.keys(headers).length ? headers : undefined,
      signal: options?.signal,
    },
    2,
  );
}

/** SSE: thinking/status phases on the wire; final ``done`` includes parsed ``itinerary`` (no raw JSON deltas). */
export async function buildItineraryStream(
  sessionId: string,
  destination: Record<string, unknown>,
  profilePills: ProfilePill[],
  onEvent: (e: Record<string, unknown>) => void,
  options?: { days?: number; signal?: AbortSignal; requestId?: string; refinementMessage?: string },
): Promise<{
  session_id: string;
  destination_id: string;
  itinerary: ItinDay[];
  budget_guidance?: ItineraryBudgetGuidance | null;
  booking_search_hints?: BookingSearchHints | null;
  needs_commercial_flight?: boolean;
  transport_plan?: WanderlyTransportPlan | null;
  booking_hooks?: WanderlyBookingHooks | null;
  itinerary_qa?: ItineraryQaResult | null;
  alltrails_search_context?: Record<string, unknown> | null;
}> {
  const body: Record<string, unknown> = {
    session_id: sessionId,
    destination,
    profile_pills: profilePills,
  };
  const days = options?.days;
  if (days != null && days > 0) body.days = days;
  const rmStream = options?.refinementMessage?.trim();
  if (rmStream) body.refinement_message = rmStream;
  const headers: Record<string, string> = {};
  if (options?.requestId) headers["X-Wanderly-Request-Id"] = options.requestId;
  let result:
    | {
        session_id: string;
        destination_id: string;
        itinerary: ItinDay[];
        budget_guidance?: ItineraryBudgetGuidance | null;
        booking_search_hints?: BookingSearchHints | null;
        needs_commercial_flight?: boolean;
        transport_plan?: WanderlyTransportPlan | null;
        booking_hooks?: WanderlyBookingHooks | null;
        itinerary_qa?: ItineraryQaResult | null;
        alltrails_search_context?: Record<string, unknown> | null;
      }
    | undefined;
  for (let attempt = 0; attempt < 2; attempt++) {
    result = undefined;
    try {
      await apiPostSseJson(
        "/api/wanderly/build_itinerary_stream",
        body,
        (obj) => {
          onEvent(obj);
          if (obj.type === "error" || obj.type === "stream_error") {
            throw new Error(String((obj as { message?: string }).message || "Stream error"));
          }
          if (obj.type === "done" && obj.session_id != null && obj.destination_id != null) {
            const atcRaw = (obj as { alltrails_search_context?: unknown }).alltrails_search_context;
            const it = obj.itinerary;
            const bgRaw = (obj as { budget_guidance?: unknown }).budget_guidance;
            const bhRaw = (obj as { booking_search_hints?: unknown }).booking_search_hints;
            const ncfRaw = (obj as { needs_commercial_flight?: unknown }).needs_commercial_flight;
            const hooksRaw = (obj as { booking_hooks?: unknown }).booking_hooks;
            let budget_guidance: ItineraryBudgetGuidance | undefined;
            if (bgRaw && typeof bgRaw === "object" && !Array.isArray(bgRaw)) {
              budget_guidance = bgRaw as ItineraryBudgetGuidance;
            }
            let booking_search_hints: BookingSearchHints | undefined;
            if (bhRaw && typeof bhRaw === "object" && !Array.isArray(bhRaw)) {
              booking_search_hints = bhRaw as BookingSearchHints;
            }
            let needs_commercial_flight: boolean | undefined;
            if (typeof ncfRaw === "boolean") {
              needs_commercial_flight = ncfRaw;
            }
            const tpRaw = (obj as { transport_plan?: unknown }).transport_plan;
            let transport_plan: WanderlyTransportPlan | undefined;
            if (
              tpRaw &&
              typeof tpRaw === "object" &&
              !Array.isArray(tpRaw) &&
              String((tpRaw as { primary_mode?: unknown }).primary_mode || "").trim()
            ) {
              transport_plan = tpRaw as WanderlyTransportPlan;
            }
            let booking_hooks: WanderlyBookingHooks | undefined;
            if (hooksRaw && typeof hooksRaw === "object" && !Array.isArray(hooksRaw)) {
              booking_hooks = hooksRaw as WanderlyBookingHooks;
            }
            const iqaRaw = (obj as { itinerary_qa?: unknown }).itinerary_qa;
            let itinerary_qa: ItineraryQaResult | undefined;
            if (iqaRaw && typeof iqaRaw === "object" && !Array.isArray(iqaRaw)) {
              itinerary_qa = iqaRaw as ItineraryQaResult;
            }
            let alltrails_search_context: Record<string, unknown> | undefined;
            if (atcRaw && typeof atcRaw === "object" && !Array.isArray(atcRaw)) {
              alltrails_search_context = atcRaw as Record<string, unknown>;
            }
            const serverSentAtc = alltrails_search_context != null;
            const parsedIntoResult = serverSentAtc;
            logItin("stream_done_alltrails", {
              destinationId: obj.destination_id,
              serverSentAtc,
              parsedIntoResult,
              trailName:
                alltrails_search_context && typeof alltrails_search_context.trail_name === "string"
                  ? alltrails_search_context.trail_name
                  : undefined,
              verificationStatus:
                alltrails_search_context && typeof alltrails_search_context.verification_status === "string"
                  ? alltrails_search_context.verification_status
                  : undefined,
              hasParentUrl: Boolean(
                alltrails_search_context &&
                  typeof alltrails_search_context.parent_alltrails_url === "string" &&
                  alltrails_search_context.parent_alltrails_url.trim(),
              ),
            });
            result = {
              session_id: String(obj.session_id),
              destination_id: String(obj.destination_id),
              itinerary: Array.isArray(it) ? (it as ItinDay[]) : [],
              budget_guidance,
              booking_search_hints,
              needs_commercial_flight,
              transport_plan,
              booking_hooks,
              itinerary_qa,
              alltrails_search_context,
            };
          }
        },
        { signal: options?.signal, headers: Object.keys(headers).length ? headers : undefined },
      );
      if (result) return result;
    } catch (e) {
      if (attempt === 0 && isLikely409Conflict(e)) {
        await sleepMs(2000 + Math.floor(Math.random() * 1500));
        continue;
      }
      throw e;
    }
  }
  if (!result) throw new Error("Itinerary stream closed before completion");
  return result;
}

export async function profilePatch(sessionId: string, pills: WanderlyProfilePillDTO[]) {
  return apiFetch<{ wanderly: WanderlyWorkspaceDTO; traveler_profile: Record<string, unknown> }>(
    "/api/wanderly/profile_patch",
    {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, pills }),
    },
  );
}

/** Post-change QA: cheap Cursor model compares profile to destination itineraries (JSON context). */
export async function planReview(
  sessionId: string,
  profilePills: WanderlyProfilePillDTO[],
  destinations: Record<string, unknown>[],
) {
  return apiFetch<{
    session_id: string;
    review: {
      assistant_markdown?: string;
      issues?: { severity: string; text: string }[];
      suggested_prompts?: string[];
      skipped?: boolean;
      reason?: string;
      error?: string;
    };
  }>("/api/wanderly/plan_review", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      profile_pills: profilePills,
      destinations,
    }),
  });
}

export interface ProfileChangeAdvisorResult {
  skipped?: boolean;
  reason?: string;
  headline?: string;
  warnings?: { destination_id: string; text: string }[];
  suggestions?: { label: string; description: string; icon: string; refine_prompt: string }[];
  error?: string;
}

/** After a sidebar pill edit: cheap Cursor model suggests refine prompts + optional card warnings. */
export async function profileChangeAdvisor(args: {
  session_id: string;
  pill_key: string;
  old_value: string;
  new_value: string;
  profile_pills: WanderlyProfilePillDTO[];
  destinations: Record<string, unknown>[];
}) {
  return apiFetch<{ session_id: string; result: ProfileChangeAdvisorResult }>(
    "/api/wanderly/profile_change_advisor",
    {
      method: "POST",
      body: JSON.stringify(args),
    },
  );
}

/**
 * ISO dates (YYYY-MM-DD) aligned with MiniItinerary / server ``build_booking_criteria``.
 * Optional ``adults`` (1–9) overrides passenger count for Expedia URL query params.
 */
export type BookingDateHints = { start_iso?: string; end_iso?: string; adults?: number };

export type BookingSearchPrepareResponse = {
  criteria_lines: string[];
  adults: number;
  start_date: string | null;
  end_date: string | null;
  route_hint: string;
  hotel_count: number;
  /** Resolved multi-stay rows (outline / itinerary / advisor). Client should merge into the card + sync_ui. */
  hotels_for_session?: Array<Record<string, unknown>>;
  booking_outline_source?: string;
  /** When false, the server skipped commercial flight search (e.g. road / ferry-only trip). */
  include_flight_search?: boolean;
};

export type BookingSearchStepResponse = {
  ok: boolean;
  step?: string;
  error?: string;
  /** True when flight step was intentionally skipped (ground-only trip). */
  skipped?: boolean;
  reason?: string;
  flight?: FlightOption;
  hotel?: HotelOption;
  hotel_index?: number;
  search_url?: string;
  /** True when flight search used catalog round-trip (out + return on last trip day). */
  round_trip?: boolean;
  /** ``outbound`` (default) or ``return`` — legacy two-leg trips without structured segments. */
  flight_leg?: "outbound" | "return";
  /** Index into ``commercial_flight_segments`` when itinerary hooks list multiple airline legs. */
  flight_segment_index?: number;
  /** Present when server has BOOKING_SEARCH_API_DEBUG=1 — field source map for parse iteration. */
  debug_parse?: Record<string, unknown>;
};

export async function bookingSearchPrepare(
  args: {
    session_id: string;
    destination_id: string;
    date_hints?: BookingDateHints;
    /** When set, overrides server session snapshot (sidebar profile for adults / LLM context). */
    profile_pills?: WanderlyProfilePillDTO[];
    /**
     * When true, prepare still builds criteria + hotels_for_session but does **not** launch parallel
     * HTML prefetch for every flight/hotel URL (used for single-stay search from the logistics UI).
     */
    skip_parallel_prefetch?: boolean;
  },
  options?: { signal?: AbortSignal },
) {
  const body: Record<string, unknown> = {
    session_id: args.session_id,
    destination_id: args.destination_id,
    date_hints: args.date_hints,
  };
  if (args.profile_pills?.length) body.profile_pills = args.profile_pills;
  if (args.skip_parallel_prefetch) body.skip_parallel_prefetch = true;
  return apiFetch<BookingSearchPrepareResponse>("/api/wanderly/booking_search_prepare", {
    method: "POST",
    body: JSON.stringify(body),
    signal: options?.signal,
  });
}

export async function bookingSearchStep(args: {
  session_id: string;
  destination_id: string;
  step: "flight" | "hotel";
  hotel_index?: number;
  date_hints?: BookingDateHints;
  profile_pills?: WanderlyProfilePillDTO[];
  flight_leg?: "outbound" | "return";
  flight_segment_index?: number;
}) {
  const body: Record<string, unknown> = {
    session_id: args.session_id,
    destination_id: args.destination_id,
    step: args.step,
    date_hints: args.date_hints,
  };
  if (args.step === "hotel" && args.hotel_index != null) body.hotel_index = args.hotel_index;
  if (args.profile_pills?.length) body.profile_pills = args.profile_pills;
  if (args.step === "flight") {
    if (args.flight_leg) body.flight_leg = args.flight_leg;
    if (args.flight_segment_index != null) body.flight_segment_index = args.flight_segment_index;
  }
  return apiFetch<BookingSearchStepResponse>("/api/wanderly/booking_search_step", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** SSE: live progress + LLM stream previews; final ``done`` matches ``bookingSearchPrepare``. */
export async function bookingSearchPrepareStream(
  args: Parameters<typeof bookingSearchPrepare>[0],
  onEvent: (e: Record<string, unknown>) => void,
  options?: { signal?: AbortSignal },
): Promise<BookingSearchPrepareResponse> {
  const body: Record<string, unknown> = {
    session_id: args.session_id,
    destination_id: args.destination_id,
    date_hints: args.date_hints,
  };
  if (args.profile_pills?.length) body.profile_pills = args.profile_pills;
  if (args.skip_parallel_prefetch) body.skip_parallel_prefetch = true;
  let result: BookingSearchPrepareResponse | undefined;
  await apiPostSseJson(
    "/api/wanderly/booking_search_prepare_stream",
    body,
    (obj) => {
      onEvent(obj);
      if (obj.type === "error" || obj.type === "stream_error") {
        throw new Error(String((obj as { message?: string }).message || "Booking prepare stream error"));
      }
      if (obj.type === "done") {
        const { type: _t, ...rest } = obj as BookingSearchPrepareResponse & { type: string };
        result = rest as BookingSearchPrepareResponse;
      }
    },
    { signal: options?.signal },
  );
  if (!result) throw new Error("Booking prepare stream closed before completion");
  return result;
}

/** SSE: live progress during one flight/hotel step; final ``done`` matches ``bookingSearchStep``. */
export async function bookingSearchStepStream(
  args: Parameters<typeof bookingSearchStep>[0],
  onEvent: (e: Record<string, unknown>) => void,
  options?: { signal?: AbortSignal },
): Promise<BookingSearchStepResponse> {
  const body: Record<string, unknown> = {
    session_id: args.session_id,
    destination_id: args.destination_id,
    step: args.step,
    date_hints: args.date_hints,
  };
  if (args.step === "hotel" && args.hotel_index != null) body.hotel_index = args.hotel_index;
  if (args.profile_pills?.length) body.profile_pills = args.profile_pills;
  if (args.step === "flight") {
    if (args.flight_leg) body.flight_leg = args.flight_leg;
    if (args.flight_segment_index != null) body.flight_segment_index = args.flight_segment_index;
  }
  let result: BookingSearchStepResponse | undefined;
  await apiPostSseJson(
    "/api/wanderly/booking_search_step_stream",
    body,
    (obj) => {
      onEvent(obj);
      if (obj.type === "error" || obj.type === "stream_error") {
        throw new Error(String((obj as { message?: string }).message || "Booking step stream error"));
      }
      if (obj.type === "done") {
        const { type: _t, ...rest } = obj as BookingSearchStepResponse & { type: string };
        result = rest as BookingSearchStepResponse;
      }
    },
    { signal: options?.signal },
  );
  if (!result) throw new Error("Booking step stream closed before completion");
  return result;
}

export type AdvisorFeedResponse = {
  advisor_suggestion: Record<string, unknown>;
  feed_markdown: string;
  refine_prompt?: string | null;
  offer_web_search?: boolean;
  suggested_search_query?: string | null;
  web_search_used?: boolean;
  web_sources?: { title: string; url: string }[];
  wanderly: WanderlyWorkspaceDTO;
};

/** Card / travel-agent Q&A with live Cursor SSE (thinking + answer). */
export async function advisorFeedStream(
  sessionId: string,
  message: string,
  onEvent: (e: Record<string, unknown>) => void,
  opts?: {
    focusDestinationId?: string;
    runWebSearch?: boolean;
    searchQuery?: string;
    signal?: AbortSignal;
  },
): Promise<AdvisorFeedResponse> {
  const body: Record<string, unknown> = { session_id: sessionId, message };
  const fid = opts?.focusDestinationId?.trim();
  if (fid) body.focus_destination_id = fid;
  if (opts?.runWebSearch) {
    body.run_web_search = true;
    body.search_query = (opts.searchQuery || "").trim();
  }
  let result: AdvisorFeedResponse | undefined;
  await apiPostSseJson(
    "/api/wanderly/advisor_feed_stream",
    body,
    (obj) => {
      onEvent(obj);
      if (obj.type === "error" || obj.type === "stream_error") {
        throw new Error(String((obj as { message?: string }).message || "Advisor stream error"));
      }
      if (obj.type === "done" && obj.feed_markdown != null) {
        const { type: _t, ...rest } = obj as AdvisorFeedResponse & { type: string };
        result = rest as AdvisorFeedResponse;
      }
    },
    opts?.signal ? { signal: opts.signal } : undefined,
  );
  if (!result) throw new Error("Advisor stream closed before completion");
  return result;
}

export async function advisorFeed(
  sessionId: string,
  message: string,
  opts?: { focusDestinationId?: string; runWebSearch?: boolean; searchQuery?: string },
) {
  const body: Record<string, unknown> = { session_id: sessionId, message };
  const fid = opts?.focusDestinationId?.trim();
  if (fid) body.focus_destination_id = fid;
  if (opts?.runWebSearch) {
    body.run_web_search = true;
    body.search_query = (opts.searchQuery || "").trim();
  }
  return apiFetch<AdvisorFeedResponse>("/api/wanderly/advisor_feed", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
