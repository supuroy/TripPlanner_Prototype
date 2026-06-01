import { buildItineraryStream, syncUi } from "../../api/wanderly";
import type { WanderlyWorkspaceDTO } from "../../types/workspace";
import type { DestCard, ProfilePill } from "../components/TripStore";
import { destinationsFromWorkspace, mergeProfilePillsConservative } from "./applyWorkspaceDto";
import { mergeItineraryBuildIntoDest } from "./itineraryMergeCard";
import { cardStreamClose, cardStreamOpen, cardStreamPush } from "./cardStreamOverlayBridge";
import { createCardStreamFromSseHandler } from "./cardStreamFromSse";
import { logItin } from "./itineraryDebug";
import { startRotatingWaitCaption, WAIT_ORCHESTRATE_CAPTIONS } from "./streamCaption";

type SetDestinations = (u: DestCard[] | ((prev: DestCard[]) => DestCard[])) => void;

const chainByKey = new Map<string, Promise<void>>();
/** Destination ids with a build scheduled or in flight (background + expanded share this). */
const activeDestBuilds = new Set<string>();
/** Avoid scheduling the same dirty-card wave twice (workspace apply + canvas effect). */
const lastPrefetchSigBySession = new Map<string, string>();

function itineraryParallelDestClient(): boolean {
  return import.meta.env.VITE_WANDERLY_ITINERARY_PARALLEL_DEST === "1";
}

/** Queue depth (unique cards + any non-destination lane jobs) for UI hints. */
type LaneDepthSink = (depth: number) => void;
let laneDepthSink: LaneDepthSink | null = null;
let pendingGlobalLaneJobs = 0;

function itineraryLaneQueueDepth(): number {
  return activeDestBuilds.size + pendingGlobalLaneJobs;
}

function notifyLaneDepth(): void {
  laneDepthSink?.(itineraryLaneQueueDepth());
}

/** TripProvider registers this for optional “N itinerary updates queued” UI (header / expanded card). */
export function registerItineraryLaneDepthSink(sink: LaneDepthSink | null): void {
  laneDepthSink = sink;
  if (sink) sink(itineraryLaneQueueDepth());
}

/** @deprecated use registerItineraryLaneDepthSink — kept so older imports don’t break if any exist */
export function registerItineraryLaneBusySink(sink: ((busy: boolean) => void) | null): void {
  registerItineraryLaneDepthSink(sink ? (depth) => sink(depth > 0) : null);
}

/** True when a background or expanded itinerary build is already queued or running for this card. */
export function isDestinationItineraryBuildActive(destId: string): boolean {
  const id = String(destId || "").trim();
  return id.length > 0 && activeDestBuilds.has(id);
}

export function tryClaimDestinationItineraryBuild(destId: string): boolean {
  const id = String(destId || "").trim();
  if (!id || activeDestBuilds.has(id)) return false;
  activeDestBuilds.add(id);
  notifyLaneDepth();
  return true;
}

export function releaseDestinationItineraryBuild(destId: string): void {
  const id = String(destId || "").trim();
  if (!id || !activeDestBuilds.delete(id)) return;
  notifyLaneDepth();
}

/**
 * Serialize every itinerary HTTP call (background JSON builds + expanded-card SSE).
 * Cursor reuses one agent per session lane; overlapping runs return ``409 Conflict``.
 *
 * With ``VITE_WANDERLY_ITINERARY_PARALLEL_DEST=1`` and a destination ``queueKey``, each card
 * uses its own chain so builds can overlap — server must set ``WANDERLY_ITINERARY_PARALLEL_DEST=1``.
 *
 * Returns false when the same destination already has a lane job (caller should not start a duplicate HTTP build).
 */
export function enqueueItineraryLane(fn: () => Promise<void>, queueKey?: string | null): boolean {
  const destId = queueKey != null ? String(queueKey).trim() : "";
  const key =
    itineraryParallelDestClient() && destId.length > 0 ? `d:${destId}` : "__default__";

  if (!destId.length) {
    pendingGlobalLaneJobs += 1;
    notifyLaneDepth();
  }

  const prev = chainByKey.get(key) ?? Promise.resolve();
  const next = Promise.resolve(prev)
    .catch(() => {})
    .then(async () => {
      try {
        await fn();
      } finally {
        if (!destId.length) {
          pendingGlobalLaneJobs = Math.max(0, pendingGlobalLaneJobs - 1);
          notifyLaneDepth();
        }
      }
    })
    .catch(() => {});
  chainByKey.set(key, next);
  return true;
}

function requestId(prefix: string): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? `${prefix}-${crypto.randomUUID()}`
    : `${prefix}-${Date.now()}`;
}

function prefetchWaveSig(dirtyIds: string[]): string {
  return dirtyIds
    .map((id) => String(id).trim())
    .filter(Boolean)
    .sort()
    .join(",");
}

function shouldSchedulePrefetchWave(sessionId: string, dirtyIds: string[]): boolean {
  const sid = sessionId.trim();
  const sig = prefetchWaveSig(dirtyIds);
  if (!sid || !sig) return false;
  if (lastPrefetchSigBySession.get(sid) === sig) return false;
  lastPrefetchSigBySession.set(sid, sig);
  return true;
}

function dirtyNonDismissedIds(cards: DestCard[]): string[] {
  return cards.filter((d) => !d.dismissed && d.itinerary_dirty === true).map((d) => String(d.id));
}

/** One serialized background build; skips if the same destination is already queued or running. */
export function enqueueBackgroundItineraryBuild(args: {
  sessionId: string;
  dest: DestCard;
  profile: ProfilePill[];
  setDestinations: SetDestinations;
  source: string;
  /** When true, trip-wide rebuilds share one client lane (avoids parallel Cursor 409s). */
  serializeLane?: boolean;
}): void {
  const id = String(args.dest.id);
  if (args.dest.dismissed || args.dest.itinerary_dirty !== true) return;
  if (!tryClaimDestinationItineraryBuild(id)) return;

  const enqueued = enqueueItineraryLane(async () => {
    const rid = requestId(`itin-${args.source}`);
    const destLabel = String(args.dest.name || id).trim() || id;
    cardStreamOpen({
      title: "Rebuilding itinerary",
      subtitle: `Day-by-day plan for ${destLabel}…`,
      destId: id,
    });
    let stopSpin: (() => void) | null = startRotatingWaitCaption(
      (cap) => cardStreamPush(cap, id),
      WAIT_ORCHESTRATE_CAPTIONS,
      2400,
    );
    const onStreamEvent = createCardStreamFromSseHandler({
      destId: id,
      structuredStreamMode: "itinerary",
      onLiveCaption: () => {
        stopSpin?.();
        stopSpin = null;
      },
    });
    try {
      logItin("background_prefetch_start", { requestId: rid, destId: id, source: args.source });
      const res = await buildItineraryStream(
        args.sessionId,
        args.dest as unknown as Record<string, unknown>,
        args.profile,
        onStreamEvent,
        {
          requestId: rid,
          refinementMessage: args.dest.itinerary_refinement_hint,
        },
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
          return mergeItineraryBuildIntoDest(
            x,
            rows,
            budget,
            res.booking_search_hints ?? null,
            typeof res.needs_commercial_flight === "boolean" ? res.needs_commercial_flight : null,
            res.transport_plan ?? null,
            res.booking_hooks ?? null,
            res.itinerary_qa ?? null,
            res.alltrails_search_context ?? null,
          );
        });
        queueMicrotask(() => {
          void syncUi(args.sessionId, next as unknown[], { profilePills: args.profile }).catch(() => {});
        });
        return next;
      });
    } catch (err: unknown) {
      logItin("background_prefetch_error", { destId: id, message: String(err), source: args.source });
      args.setDestinations((prev) =>
        prev.map((x) => (String(x.id) !== id ? x : { ...x, itinerary_dirty: false })),
      );
    } finally {
      stopSpin?.();
      cardStreamClose(1000, id);
      releaseDestinationItineraryBuild(id);
    }
  }, args.serializeLane ? null : id);

  if (!enqueued) releaseDestinationItineraryBuild(id);
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
  const dirtyIds = dirtyNonDismissedIds(merged);
  if (targets.length === 0) return;
  if (!shouldSchedulePrefetchWave(opts.sessionId, dirtyIds)) {
    logItin("background_prefetch_skipped_duplicate_wave", {
      sessionId: opts.sessionId.slice(0, 12),
      dirtyIds,
      targetIds: targets.map((x) => x.id),
      source: "workspace_apply",
    });
    return;
  }
  const delayMs = opts.delayMs ?? 450;
  logItin("background_prefetch_scheduled", {
    sessionId: opts.sessionId.slice(0, 12),
    delayMs,
    dirtyIds,
    targetIds: targets.map((x) => x.id),
    expandedExcluded: ex ?? null,
    source: "workspace_apply",
  });
  const serializeLane = targets.length > 1;
  window.setTimeout(() => {
    for (const d of targets) {
      enqueueBackgroundItineraryBuild({
        sessionId: opts.sessionId,
        dest: d,
        profile,
        setDestinations: opts.setDestinations,
        source: "workspace_apply",
        serializeLane,
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
  const dirtyIds = dirtyNonDismissedIds(opts.cards);
  if (targets.length === 0) return;
  if (!shouldSchedulePrefetchWave(opts.sessionId, dirtyIds)) {
    logItin("background_prefetch_skipped_duplicate_wave", {
      sessionId: opts.sessionId.slice(0, 12),
      dirtyIds,
      targetIds: targets.map((x) => x.id),
      source: "canvas_store",
    });
    return;
  }
  const delayMs = opts.delayMs ?? 600;
  logItin("background_prefetch_scheduled", {
    sessionId: opts.sessionId.slice(0, 12),
    delayMs,
    dirtyIds,
    targetIds: targets.map((x) => x.id),
    expandedExcluded: ex ?? null,
    source: "canvas_store",
  });
  const serializeLane = targets.length > 1;
  window.setTimeout(() => {
    for (const d of targets) {
      enqueueBackgroundItineraryBuild({
        sessionId: opts.sessionId,
        dest: d,
        profile: opts.profile,
        setDestinations: opts.setDestinations,
        source: "canvas_store",
        serializeLane,
      });
    }
  }, delayMs);
}

/** Reset prefetch dedupe when switching trips (TripStore should call on trip id change). */
export function resetItineraryPrefetchSession(sessionId: string): void {
  const sid = sessionId.trim();
  if (sid) lastPrefetchSigBySession.delete(sid);
}
