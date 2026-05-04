import TripProvider, { useTrip } from "./components/TripStore";
import { CommandBar } from "./components/CommandBar";
import { ProfilePanel } from "./components/ProfilePanel";
import { DestinationCanvas } from "./components/DestinationCanvas";
import { AgentResponses } from "./components/AgentResponses";
import { motion, AnimatePresence } from "motion/react";
import { Compass, Sparkles } from "lucide-react";
import { useEffect } from "react";
import type { DestCard } from "./components/TripStore";
import { fetchWorkspace } from "../api/wanderly";

function IdleState() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      className="flex flex-col items-center justify-center min-h-[60vh] text-center px-6"
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
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      {/* Header */}
      <header className="fixed top-0 left-0 right-0 z-30 bg-[#0a0a0f]/80 backdrop-blur-xl border-b border-white/[0.04]">
        <div className="max-w-[1400px] mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
              <Compass className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="text-[15px] text-white/70">Wanderly</span>
          </div>
          {agentStatus && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex items-center gap-2"
            >
              <div className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" />
              <span className="text-[12px] text-white/30">{agentStatus}</span>
            </motion.div>
          )}
          <div className="flex items-center gap-3">
            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center text-[11px] text-white">
              J
            </div>
          </div>
        </div>
      </header>

      {/* Main layout */}
      <div className="pt-14 pb-32">
        <AnimatePresence mode="wait">
          {!hasStarted ? (
            <div key="idle" className="max-w-[1400px] mx-auto px-6">
              <IdleState />
            </div>
          ) : (
            <motion.div
              key="workspace"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className={expandedDest ? "h-[calc(100vh-8rem)]" : "h-[calc(100vh-14rem)]"}
            >
              <div className="flex gap-6 h-full max-w-[1400px] mx-auto px-6">
                {/* Left sidebar — profile only */}
                {hasSidebar && (
                  <motion.aside
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: 0.1 }}
                    className="w-64 shrink-0 hidden lg:block"
                  >
                    <div className="sticky top-20">
                      <ProfilePanel />
                    </div>
                  </motion.aside>
                )}

                {/* Main workspace — destinations + responses */}
                <div className="flex-1 min-w-0 h-full flex flex-col">
                  {/* Mobile profile (horizontal pills) */}
                  {hasSidebar && (
                    <div className="lg:hidden mb-4">
                      <ProfilePanel />
                    </div>
                  )}

                  {/* When expanded, show full-height destination canvas */}
                  {expandedDest ? (
                    <div className="flex-1 min-h-0">
                      <DestinationCanvas />
                    </div>
                  ) : (
                    <>
                      {/* Destination cards at top (fixed) */}
                      <div className="flex-shrink-0">
                        <DestinationCanvas />
                      </div>

                      {/* Divider */}
                      <div className="border-t border-white/[0.08] my-4" />

                      {/* Agent responses at bottom (scrollable) */}
                      <div className="flex-1 min-h-0 rounded-xl border border-white/[0.04] bg-[#0a0a0f]/30 overflow-hidden">
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

      {/* Command bar: visually hidden while a card is expanded, but stay mounted so
          `pendingRefineText` / refine pipeline from ConversationPanel still runs. */}
      <div hidden={!!expandedDest}>
        <CommandBar />
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