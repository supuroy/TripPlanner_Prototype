import { apiFetch, apiPostSseJson } from "./client";
import type { ItineraryBudgetGuidance, WanderlyProfilePillDTO, WanderlyWorkspaceDTO } from "../types/workspace";
import type { FlightOption, HotelOption, ItinDay, ProfilePill } from "../app/components/TripStore";

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
  opts?: { signal?: AbortSignal },
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
        result = {
          session_id: String(obj.session_id),
          wanderly: obj.wanderly as WanderlyWorkspaceDTO,
          parse_ok: Boolean(obj.parse_ok),
        };
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
};

export async function refineTurn(
  sessionId: string,
  message: string,
  opts?: { confirmWorkspaceDrop?: boolean },
): Promise<WanderlyRefineResponse> {
  const body: Record<string, unknown> = { session_id: sessionId, message };
  if (opts?.confirmWorkspaceDrop) body.confirm_workspace_drop = true;
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
  opts?: { confirmWorkspaceDrop?: boolean; signal?: AbortSignal },
): Promise<WanderlyRefineResponse> {
  let result: WanderlyRefineResponse | undefined;
  const body: Record<string, unknown> = { session_id: sessionId, message };
  if (opts?.confirmWorkspaceDrop) body.confirm_workspace_drop = true;
  await apiPostSseJson(
    "/api/wanderly/refine_stream",
    body,
    (obj) => {
      onEvent(obj);
      if (obj.type === "error" || obj.type === "stream_error") {
        throw new Error(String((obj as { message?: string }).message || "Stream error"));
      }
      if (obj.type === "done" && obj.session_id != null && obj.wanderly != null) {
        result = {
          session_id: String(obj.session_id),
          wanderly: obj.wanderly as WanderlyWorkspaceDTO,
          parse_ok: Boolean(obj.parse_ok),
          shortlist_drop_blocked: Boolean(obj.shortlist_drop_blocked),
          model_omitted_destination_ids: Array.isArray(obj.model_omitted_destination_ids)
            ? (obj.model_omitted_destination_ids as string[])
            : undefined,
        };
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

export async function fetchWorkspace(sessionId: string) {
  const q = new URLSearchParams({ session_id: sessionId });
  return apiFetch<{ wanderly: WanderlyWorkspaceDTO }>(`/api/wanderly/workspace?${q.toString()}`);
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

export type SyncUiResponse = {
  ok: boolean;
  /** False when session is not linked to a manifest trip (nothing written under ``wanderly_trips/``). */
  saved_to_disk?: boolean;
  trip_id?: string | null;
};

export async function syncUi(sessionId: string, destinations: unknown[], opts?: { tripId?: string }) {
  const body: Record<string, unknown> = { session_id: sessionId, destinations };
  if (opts?.tripId) body.trip_id = opts.tripId;
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

export type BookingSearchHints = {
  flight_to_label?: string;
  flight_return_from_label?: string;
};

export async function buildItinerary(
  sessionId: string,
  destination: Record<string, unknown>,
  profilePills: ProfilePill[],
  options?: { days?: number; signal?: AbortSignal; requestId?: string },
) {
  const body: Record<string, unknown> = {
    session_id: sessionId,
    destination,
    profile_pills: profilePills,
  };
  const days = options?.days;
  if (days != null && days > 0) body.days = days;
  const headers: Record<string, string> = {};
  if (options?.requestId) headers["X-Wanderly-Request-Id"] = options.requestId;
  return apiFetchWith409Retry<{
    session_id: string;
    destination_id: string;
    itinerary: ItinDay[];
    budget_guidance?: ItineraryBudgetGuidance | null;
    booking_search_hints?: BookingSearchHints | null;
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
  options?: { days?: number; signal?: AbortSignal; requestId?: string },
): Promise<{
  session_id: string;
  destination_id: string;
  itinerary: ItinDay[];
  budget_guidance?: ItineraryBudgetGuidance | null;
  booking_search_hints?: BookingSearchHints | null;
}> {
  const body: Record<string, unknown> = {
    session_id: sessionId,
    destination,
    profile_pills: profilePills,
  };
  const days = options?.days;
  if (days != null && days > 0) body.days = days;
  const headers: Record<string, string> = {};
  if (options?.requestId) headers["X-Wanderly-Request-Id"] = options.requestId;
  let result:
    | {
        session_id: string;
        destination_id: string;
        itinerary: ItinDay[];
        budget_guidance?: ItineraryBudgetGuidance | null;
        booking_search_hints?: BookingSearchHints | null;
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
            const it = obj.itinerary;
            const bgRaw = (obj as { budget_guidance?: unknown }).budget_guidance;
            const bhRaw = (obj as { booking_search_hints?: unknown }).booking_search_hints;
            let budget_guidance: ItineraryBudgetGuidance | undefined;
            if (bgRaw && typeof bgRaw === "object" && !Array.isArray(bgRaw)) {
              budget_guidance = bgRaw as ItineraryBudgetGuidance;
            }
            let booking_search_hints: BookingSearchHints | undefined;
            if (bhRaw && typeof bhRaw === "object" && !Array.isArray(bhRaw)) {
              booking_search_hints = bhRaw as BookingSearchHints;
            }
            result = {
              session_id: String(obj.session_id),
              destination_id: String(obj.destination_id),
              itinerary: Array.isArray(it) ? (it as ItinDay[]) : [],
              budget_guidance,
              booking_search_hints,
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
};

export type BookingSearchStepResponse = {
  ok: boolean;
  step?: string;
  error?: string;
  flight?: FlightOption;
  hotel?: HotelOption;
  hotel_index?: number;
  search_url?: string;
  /** True when flight search used catalog round-trip (out + return on last trip day). */
  round_trip?: boolean;
  /** ``outbound`` (default) or ``return`` — separate one-way searches for each leg. */
  flight_leg?: "outbound" | "return";
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
  },
  options?: { signal?: AbortSignal },
) {
  const body: Record<string, unknown> = {
    session_id: args.session_id,
    destination_id: args.destination_id,
    date_hints: args.date_hints,
  };
  if (args.profile_pills?.length) body.profile_pills = args.profile_pills;
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
}) {
  const body: Record<string, unknown> = {
    session_id: args.session_id,
    destination_id: args.destination_id,
    step: args.step,
    date_hints: args.date_hints,
  };
  if (args.step === "hotel" && args.hotel_index != null) body.hotel_index = args.hotel_index;
  if (args.profile_pills?.length) body.profile_pills = args.profile_pills;
  if (args.step === "flight" && args.flight_leg) body.flight_leg = args.flight_leg;
  return apiFetch<BookingSearchStepResponse>("/api/wanderly/booking_search_step", {
    method: "POST",
    body: JSON.stringify(body),
  });
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
  return apiFetch<{
    advisor_suggestion: Record<string, unknown>;
    feed_markdown: string;
    /** When set, card UI can offer “Yes” to run the main planner with this prompt (no /trip prefix). */
    refine_prompt?: string | null;
    /** Model could not ground the answer in trip JSON; UI may offer opening a web search (no extra API until user acts). */
    offer_web_search?: boolean;
    suggested_search_query?: string | null;
    /** Server ran Tavily/Brave + a second advisor pass (only when ``runWebSearch`` was sent). */
    web_search_used?: boolean;
    web_sources?: { title: string; url: string }[];
    wanderly: WanderlyWorkspaceDTO;
  }>("/api/wanderly/advisor_feed", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
