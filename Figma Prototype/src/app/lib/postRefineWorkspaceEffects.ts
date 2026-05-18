import type { WanderlyOrchestrateResponse, WanderlyRefineResponse } from "../../api/wanderly";
import type { WanderlyWorkspaceDTO } from "../../types/workspace";
import type { AgentResponse, DestCard, ProfilePill } from "../components/TripStore";
import {
  applyWanderlyWorkspace,
  buildItineraryStalePlan,
  explainItineraryInvalidate,
  isPlanCompareOnlyQuestion,
  type ItineraryStalePlan,
} from "./applyWorkspaceDto";
import { logItin } from "./itineraryDebug";
import { scheduleWorkspaceItineraryPrefetch } from "./lazyItineraryPrefetch";

export type PostRefineWorkspaceEffectsArgs = {
  out: WanderlyRefineResponse | { session_id: string; wanderly: WanderlyWorkspaceDTO };
  text: string;
  priorDestinations: DestCard[];
  priorProfile: ProfilePill[];
  orch: WanderlyOrchestrateResponse["orchestration"] | null | undefined;
  expandedDest: string | null;
  sessionId: string;
  setProfile: (p: ProfilePill[] | ((prev: ProfilePill[]) => ProfilePill[])) => void;
  setDestinations: (d: DestCard[] | ((prev: DestCard[]) => DestCard[])) => void;
  addResponse: (r: Omit<AgentResponse, "id" | "ts">) => void;
  setEnrichmentPending?: (v: boolean) => void;
  profilePillMergeMode: "full" | "conservative" | "skip";
  stageAgentProfileProposal: (proposed: ProfilePill[]) => void;
  queuePlanReviewAfterItineraries: (sessionId: string) => void;
  scheduleHero: (args: {
    sessionId: string;
    wanderly: WanderlyWorkspaceDTO;
    priorDestinations?: DestCard[];
  }) => void;
  maybeStageAgentProfile: (args: {
    priorProfile: ProfilePill[];
    message: string;
    wanderly: WanderlyWorkspaceDTO;
    stageAgentProfileProposal: (proposed: ProfilePill[]) => void;
    addResponse: (r: Omit<AgentResponse, "id" | "ts">) => void;
  }) => void;
};

/** Apply workspace, hero enrichment, itinerary prefetch, and plan review queue (shared refine + smoke paths). */
export function runPostRefineWorkspaceEffects(args: PostRefineWorkspaceEffectsArgs): void {
  const {
    out,
    text,
    priorDestinations,
    priorProfile,
    orch,
    expandedDest,
    sessionId,
    setProfile,
    setDestinations,
    addResponse,
    setEnrichmentPending,
    profilePillMergeMode,
    stageAgentProfileProposal,
    queuePlanReviewAfterItineraries,
    scheduleHero,
    maybeStageAgentProfile,
  } = args;

  const prior = priorDestinations;
  const incomingCardIds = (out.wanderly.destinations ?? [])
    .map((row) => {
      const id = (row as { id?: unknown })?.id;
      return id != null ? String(id).trim() : "";
    })
    .filter((id) => id.length > 0);
  const validIds = new Set<string>([
    ...priorDestinations.map((d) => String(d.id)),
    ...incomingCardIds,
  ]);
  const stalePlan: ItineraryStalePlan = buildItineraryStalePlan(
    text,
    priorProfile,
    out.wanderly.profile_pills,
    orch ?? null,
    validIds,
    expandedDest ?? null,
  );
  const invExplain = explainItineraryInvalidate(text, priorProfile, out.wanderly.profile_pills);
  logItin("refine_apply_workspace", {
    itineraryStalePlan: stalePlan,
    compareOnlyBypass: isPlanCompareOnlyQuestion(text),
    invalidateReasons: invExplain.reasons,
    messagePreview: text.slice(0, 160),
  });

  applyWanderlyWorkspace(out.wanderly, {
    setProfile,
    setDestinations,
    addResponse,
    setEnrichmentPending,
    priorDestinations: prior,
    priorProfile,
    itineraryStalePlan: stalePlan,
    profilePillMergeMode,
  });
  maybeStageAgentProfile({
    priorProfile,
    message: text,
    wanderly: out.wanderly,
    stageAgentProfileProposal,
    addResponse,
  });
  scheduleHero({
    sessionId: out.session_id || sessionId,
    wanderly: out.wanderly,
    priorDestinations: prior,
  });
  scheduleWorkspaceItineraryPrefetch({
    sessionId: out.session_id || sessionId,
    wanderly: out.wanderly,
    priorDestinations: prior,
    priorProfile,
    setDestinations,
    excludeDestinationId: expandedDest ?? null,
    delayMs: 400,
  });
  queuePlanReviewAfterItineraries(out.session_id || sessionId);
}
