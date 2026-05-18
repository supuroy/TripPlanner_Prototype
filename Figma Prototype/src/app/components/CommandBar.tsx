import { useState, useRef, useEffect, useCallback } from "react";
import { useTrip, type AgentResponse, type DestCard, type ProfilePill } from "./TripStore";
import { motion, AnimatePresence } from "motion/react";
import { ArrowUp, Loader2 } from "lucide-react";
import {
  smokeTurnStream,
  refineTurnStream,
  refineTurn,
  planWithOrchestrator,
  enrichHeroes,
  orchestrateTurn,
  advisorFeed,
} from "../../api/wanderly";
import {
  applyWanderlyWorkspace,
  destinationsFromWorkspace,
  formatProfileProposalDiffLines,
  isBlankTravelerProfile,
  isItineraryPrimaryRefineMessage,
  mergeHeroImagesFromEnriched,
  mergeProfilePillsConservative,
  mergeProfilePillsFromDto,
  profilePillsMeaningfullyDiffer,
  needsHeroImageEnrichment,
  workspaceUserExplicitTravelerProfileEditMessage,
} from "../lib/applyWorkspaceDto";
import { logItin } from "../lib/itineraryDebug";
import { parallelPostRefineEnabled } from "../lib/parallelPostRefine";
import { runPostRefineWorkspaceEffects } from "../lib/postRefineWorkspaceEffects";
import type { WanderlyWorkspaceDTO } from "../../types/workspace";
import type { WanderlyOrchestrateResponse } from "../../api/wanderly";
import { newWanderlyTraceId, setWanderlyTurnTrace } from "../../api/traceContext";
import {
  streamEventToCaption,
  startRotatingWaitCaption,
  WAIT_ORCHESTRATE_CAPTIONS,
  WAIT_ADVISOR_CAPTIONS,
} from "../lib/streamCaption";

const SUGGESTIONS = [
  "My partner and I want a warm beach trip in mid-March, about a week. We love food and culture.",
  "Solo adventure trip — hiking, surfing, somewhere tropical. Flexible on dates.",
  "Romantic anniversary getaway — somewhere scenic with great wine and food.",
];

const useGeminiFirst = import.meta.env.VITE_USE_GEMINI_FIRST === "true";

/** When false, workspace ``profile_pills`` are not merged into the sidebar until the user confirms in chat. */
function workspaceProfilePillMergeModeForTurn(
  priorProfile: ProfilePill[],
  message: string,
): "full" | "skip" {
  const autoApply =
    isBlankTravelerProfile(priorProfile) || workspaceUserExplicitTravelerProfileEditMessage(message);
  if (!autoApply || isItineraryPrimaryRefineMessage(message)) return "skip";
  return "full";
}

function maybeStageAgentProfileAfterWorkspace(args: {
  priorProfile: ProfilePill[];
  message: string;
  wanderly: WanderlyWorkspaceDTO;
  stageAgentProfileProposal: (p: ProfilePill[]) => void;
  addResponse: (r: Omit<AgentResponse, "id" | "ts">) => void;
}): void {
  const { priorProfile, message, wanderly, stageAgentProfileProposal, addResponse } = args;
  const autoApply =
    isBlankTravelerProfile(priorProfile) || workspaceUserExplicitTravelerProfileEditMessage(message);
  if (autoApply) return;
  const incoming = wanderly.profile_pills;
  if (!incoming?.length) return;
  const proposed = mergeProfilePillsFromDto(priorProfile, incoming);
  if (!profilePillsMeaningfullyDiffer(priorProfile, proposed)) return;
  stageAgentProfileProposal(proposed);
  addResponse({
    type: "info",
    relatedTo: "wanderly_profile_proposal",
    text:
      "The planner suggested **different traveler profile** values than your sidebar. Destination cards used your current profile.\n\n" +
      formatProfileProposalDiffLines(priorProfile, proposed) +
      "\n\nUse the buttons below only if you want to adopt the planner's version.",
  });
}

function scheduleHeroEnrichment(args: {
  sessionId: string;
  wanderly: WanderlyWorkspaceDTO;
  priorDestinations?: DestCard[];
  setDestinations: (cards: DestCard[]) => void;
}) {
  const { sessionId, wanderly, priorDestinations, setDestinations } = args;
  const nextDests = destinationsFromWorkspace(wanderly, priorDestinations);
  if (!nextDests?.length || !needsHeroImageEnrichment(nextDests)) return;
  void (async () => {
    const need = nextDests.filter((d) => {
      const u = String(d.image || "").trim();
      return !u || u.includes("placehold.co");
    });
    if (need.length === 0) return;
    try {
      /* One HTTP call + one Cursor hint pass for all placeholders (was N parallel enrich_hero_one). */
      const out = await enrichHeroes(sessionId, need as unknown[]);
      const rows = out.wanderly?.destinations;
      if (Array.isArray(rows) && rows.length > 0) {
        setDestinations((cur) => mergeHeroImagesFromEnriched(cur, rows as Record<string, unknown>[]));
      }
    } catch {
      /* non-fatal */
    }
  })();
}

export function CommandBar() {
  const {
    phase,
    setPhase,
    setProfile,
    setDestinations,
    setAgentStatus,
    setAgentStreamCaption,
    agentStreamCaption,
    hasStarted,
    setHasStarted,
    destinations,
    profile,
    addResponse,
    addInsight,
    sessionId,
    setSessionId,
    setEnrichmentPending,
    pendingRefineText,
    setPendingRefineText,
    composerPrefill,
    setComposerPrefill,
    expandedDest,
    queuePlanReviewAfterItineraries,
    setPlannerAbortController,
    abortPlannerOperation,
    setRefineUnlockCardNavigation,
    stageAgentProfileProposal,
    newTripTransitioning,
    tripSwitchTransitioning,
  } = useTrip();
  const sessionTransitioning = newTripTransitioning || tripSwitchTransitioning;
  const [input, setInput] = useState("");
  const [showSuggestions, setShowSuggestions] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const streamDraftRef = useRef({ text: "" });
  const thinkingDraftRef = useRef({ text: "" });
  useEffect(() => {
    if (!hasStarted) {
      const t = setTimeout(() => setShowSuggestions(true), 600);
      return () => clearTimeout(t);
    }
  }, [hasStarted]);

  useEffect(() => {
    if (composerPrefill == null || !String(composerPrefill).trim()) return;
    const t = String(composerPrefill).trim();
    setInput(t);
    setComposerPrefill(null);
    setHasStarted(true);
    setShowSuggestions(false);
    queueMicrotask(() => inputRef.current?.focus());
  }, [composerPrefill, setComposerPrefill, setHasStarted]);

  const runRefinePipeline = useCallback(
    async (
      text: string,
      priorDestinations: DestCard[],
      priorProfile: ProfilePill[],
      confirmWorkspaceDrop = false,
      orchestrationHint: WanderlyOrchestrateResponse | null = null,
    ) => {
      if (sessionTransitioning) return;
      setPhase("refining");
      setAgentStatus("Refining with AI…");
      streamDraftRef.current = { text: "" };
      thinkingDraftRef.current = { text: "" };
      setAgentStreamCaption("Starting refine…");
      const ac = new AbortController();
      setPlannerAbortController(ac);
      let orchResolved: WanderlyOrchestrateResponse | null = orchestrationHint;
      try {
        if (!orchResolved?.parse_ok || !orchResolved.orchestration) {
          const stopOrchSpin = startRotatingWaitCaption(setAgentStreamCaption, WAIT_ORCHESTRATE_CAPTIONS);
          try {
            try {
              orchResolved = await orchestrateTurn(sessionId, text, {
                focusDestinationId: expandedDest ?? undefined,
              });
            } catch {
              orchResolved = { session_id: sessionId, parse_ok: false, orchestration: null };
            }
          } finally {
            stopOrchSpin();
          }
        }
        const orch = orchResolved?.orchestration;
        setRefineUnlockCardNavigation(Boolean(orch && orch.lock_canvas_navigation === false));

        setWanderlyTurnTrace(newWanderlyTraceId());
        const prior = priorDestinations;
        const parallelPost = parallelPostRefineEnabled();
        let workspaceEffectsApplied = false;
        const profilePillMergeMode = workspaceProfilePillMergeModeForTurn(priorProfile, text);
        const applyWorkspaceEffects = (ready: Awaited<ReturnType<typeof refineTurnStream>>) => {
          if (workspaceEffectsApplied) return;
          workspaceEffectsApplied = true;
          if (ready.session_id) setSessionId(ready.session_id);
          runPostRefineWorkspaceEffects({
            out: ready,
            text,
            priorDestinations: prior,
            priorProfile,
            orch,
            expandedDest: expandedDest ?? null,
            sessionId,
            setProfile,
            setDestinations,
            addResponse,
            setEnrichmentPending,
            profilePillMergeMode,
            stageAgentProfileProposal,
            queuePlanReviewAfterItineraries,
            scheduleHero: (a) => scheduleHeroEnrichment({ ...a, setDestinations }),
            maybeStageAgentProfile: maybeStageAgentProfileAfterWorkspace,
          });
        };
        const out = useGeminiFirst
          ? await refineTurn(sessionId, text, { confirmWorkspaceDrop: confirmWorkspaceDrop })
          : await refineTurnStream(
              sessionId,
              text,
              (ev) => {
                if (ev.type === "advisor_profile") {
                  const pills = (ev as { profile_pills?: unknown }).profile_pills;
                  if (Array.isArray(pills)) {
                    const allowStreamProfile =
                      workspaceUserExplicitTravelerProfileEditMessage(text) ||
                      isBlankTravelerProfile(priorProfile);
                    if (allowStreamProfile) {
                      const sans = pills.filter(
                        (p) =>
                          p &&
                          typeof p === "object" &&
                          String((p as { key?: unknown }).key ?? "").trim() !== "tags",
                      );
                      setProfile((cur) =>
                        mergeProfilePillsConservative(cur, sans as WanderlyWorkspaceDTO["profile_pills"]),
                      );
                    }
                  }
                  const note = (ev as { advisor_note?: unknown }).advisor_note;
                  if (typeof note === "string" && note.trim()) addInsight("info", note.trim());
                }
                const cap = streamEventToCaption(ev, streamDraftRef.current, thinkingDraftRef.current);
                if (cap) setAgentStreamCaption(cap);
              },
              {
                confirmWorkspaceDrop: confirmWorkspaceDrop,
                signal: ac.signal,
                onWorkspaceReady: parallelPost ? applyWorkspaceEffects : undefined,
              },
            );
        if (!workspaceEffectsApplied) {
          applyWorkspaceEffects(out);
        }

        if (
          !confirmWorkspaceDrop &&
          out.shortlist_drop_blocked &&
          (out.model_omitted_destination_ids?.length ?? 0) > 0
        ) {
          const ids = out.model_omitted_destination_ids ?? [];
          const names = prior
            .filter((d) => ids.includes(String(d.id)))
            .map((d) => String(d.name || d.id).trim())
            .filter(Boolean)
            .join(", ");
          const ok = window.confirm(
            `The assistant omitted ${names || ids.join(", ")} from the shortlist in JSON. Those cards were kept.\n\n` +
              `OK: run refine again and allow removing those destinations. Cancel: keep the full shortlist as shown.`,
          );
          if (ok) {
            await runRefinePipeline(text, priorDestinations, priorProfile, true, orchResolved);
          }
          return;
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        const msg = String(err instanceof Error ? err.message : err);
        if (/abort/i.test(msg)) return;
        addResponse({
          type: "warning",
          text: `Refine request failed: ${msg}`,
        });
      } finally {
        setPlannerAbortController(null);
        setRefineUnlockCardNavigation(false);
        setWanderlyTurnTrace(null);
        setPhase("complete");
        setAgentStatus("");
        setAgentStreamCaption("");
      }
    },
    [
      sessionId,
      setSessionId,
      setPhase,
      setAgentStatus,
      setAgentStreamCaption,
      setProfile,
      setDestinations,
      addResponse,
      addInsight,
      setEnrichmentPending,
      expandedDest,
      queuePlanReviewAfterItineraries,
      setPlannerAbortController,
      setRefineUnlockCardNavigation,
      stageAgentProfileProposal,
      sessionTransitioning,
    ],
  );

  useEffect(() => {
    if (!pendingRefineText?.trim()) return;
    if (phase === "thinking" || phase === "streaming" || phase === "refining" || phase === "advising") return;
    const t = pendingRefineText.trim();
    setPendingRefineText(null);
    if (destinations.length === 0) return;
    addResponse({ type: "message", role: "user", text: t });
    setInput("");
    void runRefinePipeline(t, destinations, profile);
  }, [
    pendingRefineText,
    phase,
    destinations,
    profile,
    addResponse,
    setPendingRefineText,
    runRefinePipeline,
  ]);

  const runAgent = async (text: string) => {
    if (sessionTransitioning) return;
    addResponse({ type: "message", role: "user", text });
    setHasStarted(true);
    setShowSuggestions(false);
    setInput("");

    if (destinations.length > 0) {
      setPhase("advising");
      setAgentStatus("Understanding your request…");
      streamDraftRef.current = { text: "" };
      thinkingDraftRef.current = { text: "" };
      try {
        setWanderlyTurnTrace(newWanderlyTraceId());
        let orch: Awaited<ReturnType<typeof orchestrateTurn>>;
        const stopOrchSpin = startRotatingWaitCaption(setAgentStreamCaption, WAIT_ORCHESTRATE_CAPTIONS);
        try {
          try {
            orch = await orchestrateTurn(sessionId, text, {
              focusDestinationId: expandedDest ?? undefined,
            });
          } catch {
            orch = { session_id: sessionId, parse_ok: false, orchestration: null };
          }
        } finally {
          stopOrchSpin();
        }
        const action =
          orch.parse_ok && orch.orchestration?.workspace_action
            ? orch.orchestration.workspace_action
            : "ambiguous";
        logItin("command_bar_orchestrate", {
          action,
          parseOk: orch.parse_ok,
          confidence: orch.orchestration?.confidence,
          messagePreview: text.slice(0, 160),
        });
        const plannerActions = new Set([
          "refine_workspace",
          "targeted_itinerary_edit",
          "explicit_remove_destinations",
        ]);
        if (!plannerActions.has(action) || !orch.parse_ok) {
          setAgentStatus("Travel agent…");
          const stopAdvSpin = startRotatingWaitCaption(setAgentStreamCaption, WAIT_ADVISOR_CAPTIONS);
          let out: Awaited<ReturnType<typeof advisorFeed>>;
          try {
            out = await advisorFeed(sessionId, text, {});
          } finally {
            stopAdvSpin();
          }
          const md = String(out.feed_markdown || "").trim();
          if (md) {
            addResponse({ type: "message", role: "agent", text: md });
          } else {
            addResponse({
              type: "warning",
              text: "Travel agent returned an empty reply. Try rephrasing, or prefix with /trip to run the full planner.",
            });
          }
          return;
        }
        if (action === "explicit_remove_destinations") {
          const ok = window.confirm(
            "The assistant thinks you want to remove place(s) from your shortlist. Run the full trip planner? " +
              "(If the model shrinks the board, you will get a confirmation before cards disappear.)",
          );
          if (!ok) {
            addResponse({
              type: "info",
              text: "Skipped planner — use **X** on a card to remove a destination, or ask again.",
            });
            return;
          }
        }
        await runRefinePipeline(text, destinations, profile, false, orch);
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        const msg = String(err instanceof Error ? err.message : err);
        if (/abort/i.test(msg)) return;
        addResponse({
          type: "warning",
          text: `Request failed: ${msg}`,
        });
      } finally {
        setWanderlyTurnTrace(null);
        setPhase("complete");
        setAgentStatus("");
        setAgentStreamCaption("");
      }
      return;
    }

    const priorProfileForPlanner = profile.map((p) => ({ ...p }));

    setPhase("thinking");
    setAgentStatus(useGeminiFirst ? "Running travel planner…" : "Calling Cursor agent…");
    streamDraftRef.current = { text: "" };
    thinkingDraftRef.current = { text: "" };
    setAgentStreamCaption(useGeminiFirst ? "Running full planner (may take a few minutes)…" : "Getting your trip ready…");
    try {
      setWanderlyTurnTrace(newWanderlyTraceId());
      const plannerAbort = new AbortController();
      setPlannerAbortController(plannerAbort);
      try {
      if (useGeminiFirst) {
        const stopPlanSpin = startRotatingWaitCaption(setAgentStreamCaption, WAIT_ORCHESTRATE_CAPTIONS);
        let out: Awaited<ReturnType<typeof planWithOrchestrator>>;
        try {
          out = await planWithOrchestrator(sessionId, text);
        } finally {
          stopPlanSpin();
        }
        if (out.session_id) setSessionId(out.session_id);
        const plannerMerge = workspaceProfilePillMergeModeForTurn(priorProfileForPlanner, text);
        applyWanderlyWorkspace(out.wanderly, {
          setProfile,
          setDestinations,
          addResponse,
          setEnrichmentPending,
          priorProfile: priorProfileForPlanner,
          profilePillMergeMode: plannerMerge,
        });
        maybeStageAgentProfileAfterWorkspace({
          priorProfile: priorProfileForPlanner,
          message: text,
          wanderly: out.wanderly,
          stageAgentProfileProposal,
          addResponse,
        });
        /* Hero enrichment is fire-and-forget; does not gate planner completion. */
        scheduleHeroEnrichment({
          sessionId: out.session_id || sessionId,
          wanderly: out.wanderly,
          setDestinations,
        });
        scheduleWorkspaceItineraryPrefetch({
          sessionId: out.session_id || sessionId,
          wanderly: out.wanderly,
          priorDestinations: [],
          priorProfile: priorProfileForPlanner,
          setDestinations,
          excludeDestinationId: expandedDest ?? null,
          delayMs: 400,
        });
        queuePlanReviewAfterItineraries(out.session_id || sessionId);
      } else {
        const parallelPost = parallelPostRefineEnabled();
        let workspaceEffectsApplied = false;
        const smokeMerge = workspaceProfilePillMergeModeForTurn(priorProfileForPlanner, text);
        const applySmokeWorkspaceEffects = (ready: Awaited<ReturnType<typeof smokeTurnStream>>) => {
          if (workspaceEffectsApplied) return;
          workspaceEffectsApplied = true;
          if (ready.session_id) setSessionId(ready.session_id);
          runPostRefineWorkspaceEffects({
            out: ready,
            text,
            priorDestinations: destinations,
            priorProfile: priorProfileForPlanner,
            orch: null,
            expandedDest: expandedDest ?? null,
            sessionId,
            setProfile,
            setDestinations,
            addResponse,
            setEnrichmentPending,
            profilePillMergeMode: smokeMerge,
            stageAgentProfileProposal,
            queuePlanReviewAfterItineraries,
            scheduleHero: (a) => scheduleHeroEnrichment({ ...a, setDestinations }),
            maybeStageAgentProfile: maybeStageAgentProfileAfterWorkspace,
          });
        };
        const out = await smokeTurnStream(
          sessionId,
          text,
          (ev) => {
            if (ev.type === "advisor_profile") {
              const pills = (ev as { profile_pills?: unknown }).profile_pills;
              if (Array.isArray(pills)) {
                const allowStreamProfile =
                  workspaceUserExplicitTravelerProfileEditMessage(text) ||
                  isBlankTravelerProfile(priorProfileForPlanner);
                if (allowStreamProfile) {
                  const sans = pills.filter(
                    (p) =>
                      p &&
                      typeof p === "object" &&
                      String((p as { key?: unknown }).key ?? "").trim() !== "tags",
                  );
                  setProfile((cur) =>
                    mergeProfilePillsConservative(cur, sans as WanderlyWorkspaceDTO["profile_pills"]),
                  );
                }
              }
              const note = (ev as { advisor_note?: unknown }).advisor_note;
              if (typeof note === "string" && note.trim()) addInsight("info", note.trim());
            }
            const cap = streamEventToCaption(ev, streamDraftRef.current, thinkingDraftRef.current);
            if (cap) setAgentStreamCaption(cap);
          },
          {
            signal: plannerAbort.signal,
            onWorkspaceReady: parallelPost ? applySmokeWorkspaceEffects : undefined,
          },
        );
        if (!workspaceEffectsApplied) {
          applySmokeWorkspaceEffects(out);
        }
      }
      } finally {
        setPlannerAbortController(null);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      const msg = String(err instanceof Error ? err.message : err);
      if (/abort/i.test(msg)) return;
      addResponse({
        type: "warning",
        text: `Planner request failed: ${msg}`,
      });
    } finally {
      setWanderlyTurnTrace(null);
      setPhase("complete");
      setAgentStatus("");
      setAgentStreamCaption("");
    }
  };

  const handleSubmit = () => {
    if (sessionTransitioning) return;
    if (!input.trim() || phase === "thinking" || phase === "streaming" || phase === "refining" || phase === "advising")
      return;
    void runAgent(input.trim());
  };

  const isWorking =
    phase === "thinking" || phase === "streaming" || phase === "refining" || phase === "advising";

  const inputPlaceholder = (() => {
    if (newTripTransitioning) return "Starting new trip…";
    if (tripSwitchTransitioning) return "Loading saved trip…";
    if (isWorking) {
      const cap = agentStreamCaption.trim();
      if (phase === "refining") return cap || "Refining your trip…";
      if (phase === "advising") return cap || "Travel agent reply…";
      return cap || "Working…";
    }
    return hasStarted ? "Tell me what to change or refine..." : "Describe your dream trip...";
  })();

  return (
    <div className="relative z-40 w-full shrink-0 border-t border-white/[0.06] bg-[#0a0a0f]/95 backdrop-blur-xl">
      <AnimatePresence>
        {showSuggestions && !hasStarted && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 10 }}
            className="mx-auto mb-3 max-w-2xl px-4 sm:px-6"
          >
            <p className="text-[11px] text-white/30 mb-2 px-1 uppercase tracking-widest">Try saying</p>
            <div className="flex flex-col gap-2">
              {SUGGESTIONS.map((s, i) => (
                <motion.button
                  key={s}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.1 }}
                  onClick={() => {
                    setInput(s);
                    inputRef.current?.focus();
                  }}
                  className="text-left px-4 py-2.5 rounded-xl bg-white/[0.05] border border-white/[0.08] text-white/50 text-[13px] hover:bg-white/[0.08] hover:text-white/70 transition-all backdrop-blur-sm"
                >
                  &quot;{s}&quot;
                </motion.button>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="bg-gradient-to-t from-[#0a0a0f] via-[#0a0a0f]/95 to-transparent px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3 sm:px-6 sm:pb-5 sm:pt-4">
        <div className="mx-auto w-full max-w-2xl">
          {(phase === "thinking" || phase === "streaming" || phase === "refining") && (
            <div className="mb-2 flex justify-end">
              <button
                type="button"
                onClick={() => abortPlannerOperation()}
                className="text-[11px] px-3 py-1.5 rounded-full border border-amber-500/35 bg-amber-500/10 text-amber-200/90 hover:bg-amber-500/20 transition-colors"
              >
                Stop planner
              </button>
            </div>
          )}
          <div className="flex items-center gap-3 bg-white/[0.07] border border-white/[0.1] rounded-2xl px-5 py-3 backdrop-blur-xl focus-within:border-white/[0.2] transition-all">
            <input
              ref={inputRef}
              data-testid="wanderly-command-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
              placeholder={inputPlaceholder}
              aria-busy={isWorking}
              className="flex-1 bg-transparent text-white/90 placeholder:text-white/25 outline-none text-[15px]"
              disabled={
                sessionTransitioning ||
                phase === "thinking" ||
                phase === "streaming" ||
                phase === "refining" ||
                phase === "advising"
              }
            />
            <button
              onClick={handleSubmit}
              disabled={
                sessionTransitioning ||
                !input.trim() ||
                phase === "thinking" ||
                phase === "streaming" ||
                phase === "refining" ||
                phase === "advising"
              }
              className="w-9 h-9 rounded-xl bg-white/10 flex items-center justify-center text-white/40 hover:bg-white/20 hover:text-white/80 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
            >
              {isWorking ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <ArrowUp className="w-4 h-4" />
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
