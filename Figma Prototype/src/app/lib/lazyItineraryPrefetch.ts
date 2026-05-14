import { buildItinerary, syncUi } from "../../api/wanderly";
import type { WanderlyWorkspaceDTO } from "../../types/workspace";
import type { DestCard, ProfilePill } from "../components/TripStore";
import { destinationsFromWorkspace, mergeProfilePillsConservative } from "./applyWorkspaceDto";
import { mergeItineraryBuildIntoDest } from "./itineraryMergeCard";
import { logItin } from "./itineraryDebug";

type SetDestinations = (u: DestCard[] | ((prev: DestCard[]) => DestCard[])) => void;

const chainByKey = new Map<string, Promise<void>>();
const inFlight = new Set<string>();

function itineraryParallelDestClient(): boolean {
  return import.meta.env.VITE_WANDERLY_ITINERARY_PARALLEL_DEST === "1";
}

/** Queue depth (scheduled + currently running) for UI hints — cards stay openable; work is serialized server-side. */
type LaneDepthSink = (depth: number) => void;
let laneDepthSink: LaneDepthSink | null = null;
let pendingLaneJobs = 0;

function notifyLaneDepth(): void {
  laneDepthSink?.(pendingLaneJobs);
}

/** TripProvider registers this for optional “N itinerary updates queued” UI (header / expanded card). */
export function registerItineraryLaneDepthSink(sink: LaneDepthSink | null): void {
  laneDepthSink = sink;
  if (sink) sink(pendingLaneJobs);
}

/** @deprecated use registerItineraryLaneDepthSink — kept so older imports don’t break if any exist */
export function registerItineraryLaneBusySink(sink: ((busy: boolean) => void) | null): void {
  registerItineraryLaneDepthSink(sink ? (depth) => sink(depth > 0) : null);
}

/**
 * Serialize every itinerary HTTP call (background JSON builds + expanded-card SSE).
 * Cursor reuses one agent per session lane; overlapping runs return ``409 Conflict``.
 *
 * The queue must never leave ``pendingLaneJobs`` elevated: if ``chain`` were ever rejected,
 * a bare ``chain.then(run)`` would skip ``run`` while we already incremented — locking the UI
 * with the queue counter stuck high. Normalize with ``Promise.resolve(chain).catch`` and
 * swallow the terminal rejection so the next enqueue always schedules.
 */
/**
 * Serialize itinerary HTTP work. With ``VITE_WANDERLY_ITINERARY_PARALLEL_DEST=1`` and a
 * non-empty ``queueKey`` (e.g. destination id), each card uses its own chain so builds can
 * overlap — server must set ``WANDERLY_ITINERARY_PARALLEL_DEST=1`` for matching Cursor lanes.
 */
export function enqueueItineraryLane(fn: () => Promise<void>, queueKey?: string | null): void {
  const key =
    itineraryParallelDestClient() && queueKey && String(queueKey).trim()
      ? `d:${String(queueKey).trim()}`
      : "__default__";
  pendingLaneJobs += 1;
  notifyLaneDepth();
  const prev = chainByKey.get(key) ?? Promise.resolve();
  const next = Promise.resolve(prev)
    .catch(() => {})
    .then(async () => {
      try {
        await fn();
      } finally {
        pendingLaneJobs -= 1;
        if (pendingLaneJobs <= 0) pendingLaneJobs = 0;
        notifyLaneDepth();
      }
    })
    .catch(() => {});
  chainByKey.set(key, next);
}

function requestId(prefix: string): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? `${prefix}-${crypto.randomUUID()}`
    : `${prefix}-${Date.now()}`;
}

/** One serialized background build; skips if the same destination is already queued or running. */
export function enqueueBackgroundItineraryBuild(args: {
  sessionId: string;
  dest: DestCard;
  profile: ProfilePill[];
  setDestinations: SetDestinations;
  source: string;
}): void {
  const id = String(args.dest.id);
  if (inFlight.has(id)) return;
  if (args.dest.dismissed || args.dest.itinerary_dirty !== true) return;
  inFlight.add(id);
  enqueueItineraryLane(async () => {
    const rid = requestId(`itin-${args.source}`);
    try {
      logItin("background_prefetch_start", { requestId: rid, destId: id, source: args.source });
      const res = await buildItinerary(
        args.sessionId,
        args.dest as unknown as Record<string, unknown>,
        args.profile,
        { requestId: rid },
      );
      const rows = res.itinerary || [];
      const budget = res.budget_guidance ?? null;
      logItin("background_prefetch_done", {
        requestId: rid,
        destId: id,
        rowCount: rows.length,
        source: args.source,
      });
      args.setDestinations((prev) => {
        const next = prev.map((x) => {
          if (String(x.id) !== id) return x;
          return mergeItineraryBuildIntoDest(x, rows, budget, res.booking_search_hints ?? null);
        });
        queueMicrotask(() => {
          void syncUi(args.sessionId, next as unknown[]).catch(() => {});
        });
        return next;
      });
    } catch (err: unknown) {
      logItin("background_prefetch_error", { destId: id, message: String(err), source: args.source });
      args.setDestinations((prev) =>
        prev.map((x) => (String(x.id) !== id ? x : { ...x, itinerary_dirty: false })),
      );
    } finally {
      inFlight.delete(id);
    }
  }, id);
}

/**
 * After a workspace DTO lands, queue lazy itinerary builds for every non-dismissed card that is still
 * ``itinerary_dirty`` (including unpinned). Skips the expanded card so ExpandedCard's stream is the only active build.
 */
export function scheduleWorkspaceItineraryPrefetch(opts: {
  sessionId: string;
  wanderly: WanderlyWorkspaceDTO;
  priorDestinations: DestCard[];
  priorProfile: ProfilePill[];
  setDestinations: SetDestinations;
  excludeDestinationId?: string | null;
  delayMs?: number;
}): void {
  const profile = opts.wanderly.profile_pills?.length
    ? mergeProfilePillsConservative(opts.priorProfile, opts.wanderly.profile_pills)
    : opts.priorProfile;
  const merged = destinationsFromWorkspace(opts.wanderly, opts.priorDestinations) ?? [];
  const ex = opts.excludeDestinationId;
  const targets = merged
    .filter(
      (d) =>
        !d.dismissed &&
        d.itinerary_dirty === true &&
        (!ex || String(d.id) !== String(ex)),
    )
    .sort((a, b) => {
      const pb = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned));
      if (pb !== 0) return pb;
      const sb = (Number(b.score) || 0) - (Number(a.score) || 0);
      if (sb !== 0) return sb;
      return String(a.name || "").localeCompare(String(b.name || ""));
    });
  if (targets.length === 0) return;
  const delayMs = opts.delayMs ?? 450;
  logItin("background_prefetch_scheduled", {
    sessionId: opts.sessionId.slice(0, 12),
    delayMs,
    targetIds: targets.map((x) => x.id),
    expandedExcluded: ex ?? null,
    source: "workspace_apply",
  });
  window.setTimeout(() => {
    for (const d of targets) {
      enqueueBackgroundItineraryBuild({
        sessionId: opts.sessionId,
        dest: d,
        profile,
        setDestinations: opts.setDestinations,
        source: "workspace_apply",
      });
    }
  }, delayMs);
}

/** Same as workspace prefetch but uses the live store snapshot (e.g. canvas effect after pin/dismiss). */
export function scheduleCardsItineraryPrefetch(opts: {
  sessionId: string;
  cards: DestCard[];
  profile: ProfilePill[];
  setDestinations: SetDestinations;
  excludeDestinationId?: string | null;
  delayMs?: number;
}): void {
  const ex = opts.excludeDestinationId;
  const targets = opts.cards
    .filter(
      (d) =>
        !d.dismissed &&
        d.itinerary_dirty === true &&
        (!ex || String(d.id) !== String(ex)),
    )
    .sort((a, b) => {
      const pb = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned));
      if (pb !== 0) return pb;
      const sb = (Number(b.score) || 0) - (Number(a.score) || 0);
      if (sb !== 0) return sb;
      return String(a.name || "").localeCompare(String(b.name || ""));
    });
  if (targets.length === 0) return;
  const delayMs = opts.delayMs ?? 600;
  logItin("background_prefetch_scheduled", {
    sessionId: opts.sessionId.slice(0, 12),
    delayMs,
    targetIds: targets.map((x) => x.id),
    expandedExcluded: ex ?? null,
    source: "canvas_store",
  });
  window.setTimeout(() => {
    for (const d of targets) {
      enqueueBackgroundItineraryBuild({
        sessionId: opts.sessionId,
        dest: d,
        profile: opts.profile,
        setDestinations: opts.setDestinations,
        source: "canvas_store",
      });
    }
  }, delayMs);
}
