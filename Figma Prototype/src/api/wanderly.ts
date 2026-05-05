import { apiFetch, apiPostSseJson } from "./client";
import type { ItineraryBudgetGuidance, WanderlyProfilePillDTO, WanderlyWorkspaceDTO } from "../types/workspace";
import type { ItinDay, ProfilePill } from "../app/components/TripStore";

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
    undefined,
  );
  if (!result) throw new Error("Stream closed before completion");
  return result;
}

export async function refineTurn(sessionId: string, message: string) {
  return apiFetch<{ session_id: string; wanderly: WanderlyWorkspaceDTO; parse_ok?: boolean }>(
    "/api/wanderly/refine",
    {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, message }),
    },
  );
}

/** Cursor refine with SSE progress. */
export async function refineTurnStream(
  sessionId: string,
  message: string,
  onEvent: (e: Record<string, unknown>) => void,
): Promise<{ session_id: string; wanderly: WanderlyWorkspaceDTO; parse_ok?: boolean }> {
  let result: { session_id: string; wanderly: WanderlyWorkspaceDTO; parse_ok?: boolean } | undefined;
  await apiPostSseJson(
    "/api/wanderly/refine_stream",
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
    undefined,
  );
  if (!result) throw new Error("Stream closed before completion");
  return result;
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

export async function syncUi(sessionId: string, destinations: unknown[]) {
  return apiFetch<{ ok: boolean }>("/api/wanderly/sync_ui", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, destinations }),
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
            let budget_guidance: ItineraryBudgetGuidance | undefined;
            if (bgRaw && typeof bgRaw === "object" && !Array.isArray(bgRaw)) {
              budget_guidance = bgRaw as ItineraryBudgetGuidance;
            }
            result = {
              session_id: String(obj.session_id),
              destination_id: String(obj.destination_id),
              itinerary: Array.isArray(it) ? (it as ItinDay[]) : [],
              budget_guidance,
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

export async function advisorFeed(
  sessionId: string,
  message: string,
  opts?: { focusDestinationId?: string },
) {
  const body: Record<string, unknown> = { session_id: sessionId, message };
  const fid = opts?.focusDestinationId?.trim();
  if (fid) body.focus_destination_id = fid;
  return apiFetch<{
    advisor_suggestion: Record<string, unknown>;
    feed_markdown: string;
    /** When set, card UI can offer “Yes” to run the main planner with this prompt (no /trip prefix). */
    refine_prompt?: string | null;
    wanderly: WanderlyWorkspaceDTO;
  }>("/api/wanderly/advisor_feed", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
