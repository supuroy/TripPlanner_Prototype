import TripProvider, { useTrip } from "./components/TripStore";
import { CommandBar } from "./components/CommandBar";
import { ProfilePanel } from "./components/ProfilePanel";
import { DestinationCanvas } from "./components/DestinationCanvas";
import { AgentResponses } from "./components/AgentResponses";
import { motion, AnimatePresence } from "motion/react";
import { Compass, Sparkles, Maximize2, Minimize2 } from "lucide-react";
import { useEffect } from "react";
import type { DestCard } from "./components/TripStore";
import { fetchWorkspace } from "../api/wanderly";

function IdleState() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      className="flex flex-col items-center justify-center py-8 text-center"
    >
      <motion.div
        initial={{ scale: 0.8 }}
        animate={{ scale: 1 }}
        transition={{ type: "spring", delay: 0.1 }}
        className="w-16 h-16 rounded-2xl bg-gradient-to-br from-indigo-500/20 to-purple-500/20 border border-indigo-500/20 flex items-center justify-center mb-6"
      >
        <Compass className="w-7 h-7 text-indigo-400" />
      </motion.div>
      <motion.h1
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.2 }}
        className="text-white/80 text-[28px] mb-2"
      >
        Where to next?
      </motion.h1>
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.3 }}
        className="text-white/25 text-[15px] max-w-md leading-relaxed"
      >
        Describe your dream trip and I'll build complete plans — destinations, itineraries, flights, hotels, and activities — all at once.
      </motion.p>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.5 }}
        className="flex items-center gap-2 mt-6 text-[12px] text-white/15"
      >
        <Sparkles className="w-3.5 h-3.5" />
        <span>Powered by your personal travel agent</span>
      </motion.div>
    </motion.div>
  );
}

function AgentResponsesHeader() {
  const { isCanvasCollapsed, setIsCanvasCollapsed } = useTrip();
  return (
    <div className="flex items-center justify-between border-b border-white/[0.04] px-4 py-3">
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-indigo-400" />
        <h3 className="text-[13px] text-white/70">Agent Chat</h3>
      </div>
      <button
        onClick={() => setIsCanvasCollapsed(!isCanvasCollapsed)}
        className="flex items-center gap-1.5 rounded-lg border border-white/[0.06] bg-white/[0.03] px-3 py-1.5 text-white/50 transition-all hover:bg-white/[0.05] hover:text-white/80"
      >
        {isCanvasCollapsed ? (
          <>
            <Minimize2 className="h-3.5 w-3.5" />
            <span className="text-[11px]">Show cards</span>
          </>
        ) : (
          <>
            <Maximize2 className="h-3.5 w-3.5" />
            <span className="text-[11px]">Maximize chat</span>
          </>
        )}
      </button>
    </div>
  );
}

function Workspace() {
  const {
    hasStarted,
    agentStatus,
    profile,
    expandedDest,
    sessionId,
    enrichmentPending,
    setDestinations,
    setEnrichmentPending,
    itineraryLaneQueueDepth,
  } = useTrip();

  useEffect(() => {
    document.body.style.backgroundColor = "#0a0a0f";
    return () => { document.body.style.backgroundColor = ""; };
  }, []);

  useEffect(() => {
    if (import.meta.env.VITE_POLL_WORKSPACE !== "true" || !enrichmentPending) return;
    const id = window.setInterval(() => {
      void (async () => {
        try {
          const { wanderly } = await fetchWorkspace(sessionId);
          setEnrichmentPending(!!wanderly.enrichment_pending);
          if (wanderly.destinations?.length) {
            setDestinations((prev) => {
              const server = wanderly.destinations as DestCard[];
              const byId = new Map(server.map((d) => [d.id, d]));
              return prev.map((p) => {
                const s = byId.get(p.id);
                if (!s) return p;
                return { ...p, ...s, pinned: p.pinned, dismissed: p.dismissed };
              });
            });
          }
        } catch {
          /* ignore transient poll errors */
        }
      })();
    }, 5000);
    return () => clearInterval(id);
  }, [enrichmentPending, sessionId, setDestinations, setEnrichmentPending]);

  const hasSidebar = hasStarted && profile.length > 0;

  return (
    <div className="flex h-[100dvh] max-h-[100dvh] flex-col overflow-hidden bg-[#0a0a0f] text-white">
      {/* Header */}
      <header className="shrink-0 z-30 border-b border-white/[0.04] bg-[#0a0a0f]/90 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between px-4 py-3 sm:px-6">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
              <Compass className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="text-[15px] text-white/70">Wanderly</span>
          </div>
          {(agentStatus || itineraryLaneQueueDepth > 0) && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex max-w-[min(440px,58vw)] flex-col items-end gap-0.5 text-right"
            >
              <div className="flex items-center gap-2">
                <div className="w-1.5 h-1.5 shrink-0 rounded-full bg-indigo-400 animate-pulse" />
                <span className="text-[12px] text-white/30">
                  {agentStatus ||
                    `Refreshing day-by-day plans (${itineraryLaneQueueDepth} in queue, one at a time)…`}
                </span>
              </div>
              {agentStatus && itineraryLaneQueueDepth > 0 ? (
                <span className="text-[10px] text-white/22">
                  Itinerary lane: {itineraryLaneQueueDepth} job{itineraryLaneQueueDepth > 1 ? "s" : ""} queued — cards
                  stay open; updates apply as each run finishes.
                </span>
              ) : null}
            </motion.div>
          )}
          <div className="flex items-center gap-3">
            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center text-[11px] text-white">
              J
            </div>
          </div>
        </div>
      </header>

      {/* Main + command: flex fills viewport so agent panel sits flush above the input (no dead band). */}
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-hidden">
          <AnimatePresence mode="wait">
            {!hasStarted ? (
              <div
                key="idle"
                className="mx-auto flex h-full max-w-[1400px] flex-col justify-center overflow-y-auto px-4 py-6 sm:px-6"
              >
                <IdleState />
              </div>
            ) : (
              <motion.div
                key="workspace"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="flex h-full min-h-0 flex-col"
              >
                <div className="mx-auto flex h-full min-h-0 w-full max-w-[1400px] gap-4 px-3 sm:gap-6 sm:px-6">
                  {/* Left sidebar — profile only */}
                  {hasSidebar && (
                    <motion.aside
                      initial={{ opacity: 0, x: -20 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: 0.1 }}
                      className="hidden w-64 shrink-0 lg:block"
                    >
                      <div className="h-full overflow-y-auto py-4 pr-1">
                        <ProfilePanel />
                      </div>
                    </motion.aside>
                  )}

                  {/* Main workspace — destinations + responses */}
                  <div className="flex min-h-0 min-w-0 flex-1 flex-col py-3 sm:py-4">
                    {/* Mobile profile (horizontal pills) */}
                    {hasSidebar && (
                      <div className="mb-3 shrink-0 lg:hidden">
                        <ProfilePanel />
                      </div>
                    )}

                    {expandedDest ? (
                      <div className="min-h-0 flex-1">
                        <DestinationCanvas />
                      </div>
                    ) : (
                      <>
                        <div className="shrink-0">
                          <DestinationCanvas />
                        </div>
                        <div className="my-3 shrink-0 border-t border-white/[0.08] sm:my-4" />
                        <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-white/[0.04] bg-[#0a0a0f]/30 flex flex-col">
                          <AgentResponsesHeader />
                          <AgentResponses />
                        </div>
                      </>
                    )}
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Command bar: hidden while expanded, but stay mounted for refine pipeline. */}
        <div className="shrink-0" hidden={!!expandedDest}>
          <CommandBar />
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <TripProvider>
      <Workspace />
    </TripProvider>
  );
}