import { useState, useRef, useEffect, useCallback } from "react";
import { useTrip, type DestCard, type ProfilePill } from "./TripStore";
import { motion, AnimatePresence } from "motion/react";
import { ArrowUp, Loader2 } from "lucide-react";
import {
  smokeTurnStream,
  refineTurnStream,
  refineTurn,
  planWithOrchestrator,
  enrichHeroes,
  planReview,
} from "../../api/wanderly";
import {
  applyWanderlyWorkspace,
  destinationsFromWorkspace,
  explainItineraryInvalidate,
  mergeHeroImagesFromEnriched,
  mergeProfilePillsFromDto,
  needsHeroImageEnrichment,
} from "../lib/applyWorkspaceDto";
import { logItin } from "../lib/itineraryDebug";
import type { WanderlyWorkspaceDTO } from "../../types/workspace";

const SUGGESTIONS = [
  "My partner and I want a warm beach trip in mid-March, about a week. We love food and culture.",
  "Solo adventure trip — hiking, surfing, somewhere tropical. Flexible on dates.",
  "Romantic anniversary getaway — somewhere scenic with great wine and food.",
];

const useGeminiFirst = import.meta.env.VITE_USE_GEMINI_FIRST === "true";

function runStatusCaption(status: string): string | null {
  const u = status.toUpperCase();
  if (u === "CREATING") return "Getting your travel agent ready…";
  if (u === "RUNNING") return "Working through your trip request…";
  if (u === "FINISHED" || u === "COMPLETED") return "Putting the finishing touches on your answer…";
  return null;
}

/** Map Cursor SSE + our phase events to short, traveler-friendly status lines. */
function streamEventToCaption(
  ev: Record<string, unknown>,
  assistantDraft: { text: string },
  thinkingDraft: { text: string },
): string {
  if (ev.type === "phase" && typeof ev.message === "string") {
    if (/parsing/i.test(ev.message)) thinkingDraft.text = "";
    return ev.message;
  }
  if (ev.type === "thinking_delta" && typeof ev.delta === "string") {
    thinkingDraft.text += ev.delta;
    const t = thinkingDraft.text.replace(/\s+/g, " ").trim();
    if (!t) return "";
    const tail = t.length > 200 ? `…${t.slice(-200)}` : t;
    return `Considering options — ${tail}`;
  }
  if (ev.type === "run_status" && typeof ev.status === "string") {
    return runStatusCaption(ev.status) ?? "";
  }
  if (ev.type === "tool_call") {
    return "Gathering a bit more context to compare places…";
  }
  if (ev.type === "run_result" && typeof ev.status === "string") {
    return runStatusCaption(ev.status) ?? "";
  }
  if (ev.type === "assistant_delta" && typeof ev.delta === "string") {
    assistantDraft.text += ev.delta;
    if (assistantDraft.text.length > 80) thinkingDraft.text = "";
    const oneLine = assistantDraft.text.replace(/\s+/g, " ").trim();
    if (!oneLine) return "";
    const looksLikeStructuredPayload =
      /"destinations"\s*:/.test(oneLine) ||
      /"profile_pills"\s*:/.test(oneLine) ||
      /"itinerary"\s*:/.test(oneLine) ||
      (/[\[{]/.test(oneLine[0] || "") && /"day"\s*:/.test(oneLine));
    if (looksLikeStructuredPayload) {
      return "Shaping your trip (parsing structured data)…";
    }
    const tail = oneLine.length <= 160 ? oneLine : `…${oneLine.slice(-160)}`;
    return `Writing your plan — ${tail}`;
  }
  /* Unknown / low-signal Cursor events — keep prior caption */
  if (ev.type === "cursor_event") return "";
  return "";
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
    hasStarted,
    setHasStarted,
    destinations,
    profile,
    addResponse,
    sessionId,
    setSessionId,
    setEnrichmentPending,
    pendingRefineText,
    setPendingRefineText,
  } = useTrip();
  const [input, setInput] = useState("");
  const [streamCaption, setStreamCaption] = useState("");
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

  const runRefinePipeline = useCallback(
    async (text: string, priorDestinations: DestCard[], priorProfile: ProfilePill[]) => {
      setPhase("refining");
      setAgentStatus("Refining with AI…");
      streamDraftRef.current = { text: "" };
      thinkingDraftRef.current = { text: "" };
      setStreamCaption("Starting refine…");
      try {
        const prior = priorDestinations;
        const out = useGeminiFirst
          ? await refineTurn(sessionId, text)
          : await refineTurnStream(sessionId, text, (ev) => {
              if (ev.type === "advisor_profile" && Array.isArray((ev as { profile_pills?: unknown }).profile_pills)) {
                const raw = (ev as { profile_pills: { key: string; label: string; value: string; icon?: string }[] }).profile_pills;
                setProfile((cur) => mergeProfilePillsFromDto(cur, raw));
              }
              const cap = streamEventToCaption(ev, streamDraftRef.current, thinkingDraftRef.current);
              if (cap) setStreamCaption(cap);
            });
        if (out.session_id) setSessionId(out.session_id);
        const inv = explainItineraryInvalidate(text, priorProfile, out.wanderly.profile_pills);
        const markStale = inv.shouldInvalidate;
        logItin("refine_apply_workspace", {
          markItinerariesStale: markStale,
          invalidateReasons: inv.reasons,
          messagePreview: text.slice(0, 160),
        });
        applyWanderlyWorkspace(out.wanderly, {
          setProfile,
          setDestinations,
          addResponse,
          setEnrichmentPending,
          priorDestinations: prior,
          priorProfile: priorProfile,
          markItinerariesStale: markStale,
        });
        scheduleHeroEnrichment({
          sessionId: out.session_id || sessionId,
          wanderly: out.wanderly,
          priorDestinations: prior,
          setDestinations,
        });
        const sidR = out.session_id || sessionId;
        void planReview(
          sidR,
          out.wanderly.profile_pills ?? [],
          (out.wanderly.destinations ?? []) as Record<string, unknown>[],
        )
          .then((r) => {
            const rev = r.review;
            if (!rev || rev.skipped) return;
            const md = (rev.assistant_markdown || "").trim();
            if (md) addResponse({ type: "message", role: "agent", text: `**Trip review (advisor)**\n\n${md}` });
            for (const pr of rev.suggested_prompts || []) {
              if (typeof pr === "string" && pr.trim()) addResponse({ type: "tip", text: `Try asking: ${pr}` });
            }
          })
          .catch(() => {});
      } catch (err) {
        addResponse({
          type: "warning",
          text: `Refine request failed: ${String(err)}`,
        });
      } finally {
        setPhase("complete");
        setAgentStatus("");
        setStreamCaption("");
      }
    },
    [
      sessionId,
      setSessionId,
      setPhase,
      setAgentStatus,
      setProfile,
      setDestinations,
      addResponse,
      setEnrichmentPending,
    ],
  );

  useEffect(() => {
    if (!pendingRefineText?.trim()) return;
    if (phase === "thinking" || phase === "streaming" || phase === "refining") return;
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
    addResponse({ type: "message", role: "user", text });
    setHasStarted(true);
    setShowSuggestions(false);
    setInput("");

    if (destinations.length > 0) {
      await runRefinePipeline(text, destinations, profile);
      return;
    }

    setPhase("thinking");
    setAgentStatus(useGeminiFirst ? "Running travel planner…" : "Calling Cursor agent…");
    streamDraftRef.current = { text: "" };
    thinkingDraftRef.current = { text: "" };
    setStreamCaption(useGeminiFirst ? "Running full planner (may take a few minutes)…" : "Getting your trip ready…");
    try {
      if (useGeminiFirst) {
        const out = await planWithOrchestrator(sessionId, text);
        if (out.session_id) setSessionId(out.session_id);
        applyWanderlyWorkspace(out.wanderly, {
          setProfile,
          setDestinations,
          addResponse,
          setEnrichmentPending,
          priorProfile: profile,
        });
        scheduleHeroEnrichment({
          sessionId: out.session_id || sessionId,
          wanderly: out.wanderly,
          setDestinations,
        });
      } else {
        const out = await smokeTurnStream(sessionId, text, (ev) => {
          if (ev.type === "advisor_profile" && Array.isArray((ev as { profile_pills?: unknown }).profile_pills)) {
            const raw = (ev as { profile_pills: { key: string; label: string; value: string; icon?: string }[] }).profile_pills;
            setProfile((cur) => mergeProfilePillsFromDto(cur, raw));
          }
          const cap = streamEventToCaption(ev, streamDraftRef.current, thinkingDraftRef.current);
          if (cap) setStreamCaption(cap);
        });
        if (out.session_id) setSessionId(out.session_id);
        applyWanderlyWorkspace(out.wanderly, {
          setProfile,
          setDestinations,
          addResponse,
          setEnrichmentPending,
          priorProfile: profile,
        });
        scheduleHeroEnrichment({
          sessionId: out.session_id || sessionId,
          wanderly: out.wanderly,
          setDestinations,
        });
        const sidS = out.session_id || sessionId;
        void planReview(
          sidS,
          out.wanderly.profile_pills ?? [],
          (out.wanderly.destinations ?? []) as Record<string, unknown>[],
        )
          .then((r) => {
            const rev = r.review;
            if (!rev || rev.skipped) return;
            const md = (rev.assistant_markdown || "").trim();
            if (md) addResponse({ type: "message", role: "agent", text: `**Trip review (advisor)**\n\n${md}` });
            for (const pr of rev.suggested_prompts || []) {
              if (typeof pr === "string" && pr.trim()) addResponse({ type: "tip", text: `Try asking: ${pr}` });
            }
          })
          .catch(() => {});
      }
    } catch (err) {
      addResponse({
        type: "warning",
        text: `Planner request failed: ${String(err)}`,
      });
    } finally {
      setPhase("complete");
      setAgentStatus("");
      setStreamCaption("");
    }
  };

  const handleSubmit = () => {
    if (!input.trim() || phase === "thinking" || phase === "streaming" || phase === "refining") return;
    void runAgent(input.trim());
  };

  const isWorking = phase === "thinking" || phase === "streaming" || phase === "refining";

  const inputPlaceholder = (() => {
    if (isWorking) {
      return (
        streamCaption ||
        (phase === "refining" ? "Adjusting your shortlist…" : "Working with Cursor…")
      );
    }
    return hasStarted ? "Tell me what to change or refine..." : "Describe your dream trip...";
  })();

  return (
    <div className="fixed bottom-0 left-0 right-0 z-40">
      <AnimatePresence>
        {showSuggestions && !hasStarted && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 10 }}
            className="max-w-2xl mx-auto px-6 mb-3"
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

      <AnimatePresence>
        {isWorking && (
          <motion.div
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="max-w-2xl mx-auto px-6 mb-2"
          >
            <div className="flex items-center gap-2 px-4 py-2 rounded-full bg-white/[0.05] backdrop-blur-md border border-white/[0.08] w-fit">
              <Loader2 className="w-3.5 h-3.5 text-indigo-400 animate-spin" />
              <span className="text-[12px] text-white/50">
                {phase === "refining" ? "Adjusting…" : "Working…"}
              </span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="bg-gradient-to-t from-[#0a0a0f] via-[#0a0a0f]/95 to-transparent pt-6 pb-5 px-6">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-center gap-3 bg-white/[0.07] border border-white/[0.1] rounded-2xl px-5 py-3 backdrop-blur-xl focus-within:border-white/[0.2] transition-all">
            <input
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
              placeholder={inputPlaceholder}
              title={isWorking && streamCaption ? streamCaption : undefined}
              aria-busy={isWorking}
              className="flex-1 bg-transparent text-white/90 placeholder:text-white/25 outline-none text-[15px]"
              disabled={phase === "thinking" || phase === "streaming" || phase === "refining"}
            />
            <button
              onClick={handleSubmit}
              disabled={!input.trim() || phase === "thinking" || phase === "streaming" || phase === "refining"}
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
