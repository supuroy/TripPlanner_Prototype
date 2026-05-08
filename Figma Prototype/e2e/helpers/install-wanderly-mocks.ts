import type { Page } from "@playwright/test";
import type { WanderlyWorkspaceDTO } from "../../src/types/workspace";
import { workspaceLisbonPorto, workspaceLisbonReady, MOCK_ITINERARY_DAYS } from "../fixtures/wanderly-workspaces";

function sseBody(events: Record<string, unknown>[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
}

export type WanderlyMockOptions = {
  /** Override first smoke / plan stream completion payload. */
  initialWorkspace?: (sessionId: string) => WanderlyWorkspaceDTO;
  /** Override refine stream completion (defaults to Lisbon + Porto). */
  refineWorkspace?: (sessionId: string) => WanderlyWorkspaceDTO;
  /** When set, ``/api/wanderly/advisor_feed`` uses this instead of the static Lisbon-food reply. */
  advisorFeedResponder?: (
    message: string,
    sessionId: string,
  ) => {
    feed_markdown: string;
    refine_prompt?: string | null;
  };
};

/**
 * Stubs Wanderly HTTP + SSE endpoints so E2E does not require the Python backend.
 * Matches Vite dev proxy paths: browser calls same-origin `/api/wanderly/...`.
 */
export async function installWanderlyApiMocks(page: Page, opts: WanderlyMockOptions = {}): Promise<void> {
  const initialFn = opts.initialWorkspace ?? workspaceLisbonReady;
  const refineFn = opts.refineWorkspace ?? workspaceLisbonPorto;
  const advisorResponder = opts.advisorFeedResponder;

  const json = (data: unknown) => ({
    status: 200,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

  const sse = (events: Record<string, unknown>[]) => ({
    status: 200,
    headers: { "Content-Type": "text/event-stream; charset=utf-8" },
    body: sseBody(events),
  });

  await page.route("**/api/wanderly/smoke_turn_stream", async (route) => {
    const post = route.request().postDataJSON() as { session_id?: string };
    const sessionId = String(post?.session_id || "e2e-session");
    await route.fulfill(
      sse([
        { type: "phase", message: "E2E mock: planning…" },
        { type: "done", session_id: sessionId, wanderly: initialFn(sessionId), parse_ok: true },
      ]),
    );
  });

  await page.route("**/api/wanderly/refine_stream", async (route) => {
    const post = route.request().postDataJSON() as { session_id?: string };
    const sessionId = String(post?.session_id || "e2e-session");
    await route.fulfill(
      sse([
        { type: "phase", message: "E2E mock: refining…" },
        { type: "done", session_id: sessionId, wanderly: refineFn(sessionId), parse_ok: true },
      ]),
    );
  });

  await page.route("**/api/wanderly/build_itinerary", async (route) => {
    const post = route.request().postDataJSON() as { session_id?: string; destination?: { id?: string } };
    const sessionId = String(post?.session_id || "e2e-session");
    const destinationId = String(post?.destination?.id || "lisbon-portugal");
    await route.fulfill(
      json({
        session_id: sessionId,
        destination_id: destinationId,
        itinerary: MOCK_ITINERARY_DAYS,
        budget_guidance: { trip_usd_pp_short: "~$120/day", notes: "Mock" },
      }),
    );
  });

  await page.route("**/api/wanderly/build_itinerary_stream", async (route) => {
    const post = route.request().postDataJSON() as { session_id?: string; destination?: { id?: string } };
    const sessionId = String(post?.session_id || "e2e-session");
    const destinationId = String(post?.destination?.id || "lisbon-portugal");
    await route.fulfill(
      sse([
        { type: "phase", message: "E2E mock: itinerary…" },
        {
          type: "done",
          session_id: sessionId,
          destination_id: destinationId,
          itinerary: MOCK_ITINERARY_DAYS,
          budget_guidance: { trip_usd_pp_short: "~$120/day" },
        },
      ]),
    );
  });

  await page.route("**/api/wanderly/plan_review", async (route) => {
    const post = route.request().postDataJSON() as { session_id?: string };
    await route.fulfill(
      json({
        session_id: String(post?.session_id || "e2e-session"),
        review: { skipped: true, reason: "e2e_mock" },
      }),
    );
  });

  await page.route("**/api/wanderly/sync_ui", async (route) => {
    await route.fulfill(json({ ok: true }));
  });

  await page.route("**/api/wanderly/enrich_heroes", async (route) => {
    const post = route.request().postDataJSON() as { session_id?: string };
    await route.fulfill(
      json({
        session_id: String(post?.session_id || "e2e-session"),
        wanderly: { destinations: [], hero_images_enriched: true },
      }),
    );
  });

  await page.route("**/api/wanderly/advisor_feed", async (route) => {
    const post = route.request().postDataJSON() as {
      session_id?: string;
      message?: string;
      run_web_search?: boolean;
      search_query?: string;
    };
    const sessionId = String(post?.session_id || "e2e-session");
    const message = String(post?.message || "");
    const w = initialFn(sessionId);
    const fromResponder = advisorResponder?.(message, sessionId);
    const webRun = Boolean(post?.run_web_search);
    const feed_markdown = webRun
      ? "**Web lookup (e2e mock):** plausible snippet-style answer for testing."
      : fromResponder?.feed_markdown ??
        "**Lisbon food:** custard tarts (*pastéis de nata*), fresh seafood, and market snacks.";
    const refine_prompt = fromResponder?.refine_prompt ?? null;
    await route.fulfill(
      json({
        advisor_suggestion: {
          assistant_message: feed_markdown.replace(/\*\*/g, "").slice(0, 200),
          offer_web_search: false,
          suggested_search_query: null,
        },
        feed_markdown,
        refine_prompt,
        offer_web_search: false,
        suggested_search_query: null,
        web_search_used: webRun,
        web_sources: webRun ? [{ title: "E2E mock source", url: "https://example.com/mock" }] : [],
        wanderly: w,
      }),
    );
  });

  await page.route("**/api/wanderly/profile_patch", async (route) => {
    const post = route.request().postDataJSON() as {
      session_id?: string;
      pills?: WanderlyWorkspaceDTO["profile_pills"];
    };
    const sessionId = String(post?.session_id || "e2e-session");
    const pills = post?.pills?.length ? post.pills : initialFn(sessionId).profile_pills;
    const base = initialFn(sessionId);
    await route.fulfill(
      json({
        wanderly: { ...base, profile_pills: pills },
        traveler_profile: { version: 1 },
      }),
    );
  });

  await page.route("**/api/wanderly/profile_change_advisor", async (route) => {
    const post = route.request().postDataJSON() as { session_id?: string; new_value?: string };
    await route.fulfill(
      json({
        session_id: String(post?.session_id || "e2e-session"),
        result: {
          headline: `E2E mock: pill updated to “${String(post?.new_value || "").slice(0, 40)}”.`,
          suggestions: [],
        },
      }),
    );
  });

  await page.route("**/api/wanderly/workspace?**", async (route) => {
    const url = new URL(route.request().url());
    const sessionId = url.searchParams.get("session_id") || "e2e-session";
    await route.fulfill(json({ wanderly: initialFn(sessionId) }));
  });
}
