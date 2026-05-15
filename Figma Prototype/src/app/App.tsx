import TripProvider, { useTrip } from "./components/TripStore";
import { CommandBar } from "./components/CommandBar";
import { ProfilePanel } from "./components/ProfilePanel";
import { DestinationCanvas } from "./components/DestinationCanvas";
import { AgentResponses } from "./components/AgentResponses";
import { SavedTripsMenu } from "./components/SavedTripsMenu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./components/ui/select";
import { motion, AnimatePresence } from "motion/react";
import { Compass, Sparkles, Maximize2, Minimize2, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { DestCard } from "./components/TripStore";
import { fetchWorkspace } from "../api/wanderly";

function IdleState() {
  const { wanderlyWorkspaceBootstrapped } = useTrip();
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
      {wanderlyWorkspaceBootstrapped && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.45 }}
          className="relative mt-8 flex w-full max-w-md flex-col items-center"
        >
          <SavedTripsMenu variant="idle" />
        </motion.div>
      )}
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

function TripDiskPersistBadge() {
  const { wanderlyTripId, savedTrips, tripDiskPersist } = useTrip();
  if (!wanderlyTripId && savedTrips.length === 0) return null;
  const { status, savedAtMs, saveError } = tripDiskPersist;
  let text: string;
  if (status === "unknown") text = "Trip file: idle";
  else if (status === "saving") text = "Saving to trip file…";
  else if (status === "dirty") {
    text = saveError ? "Save failed — trip file may be stale" : "Unsaved card changes (auto-save pending)";
  } else {
    const t =
      savedAtMs != null
        ? new Date(savedAtMs).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" })
        : "";
    text = t ? `Trip file saved · ${t}` : "Trip file saved";
  }
  const colorClass =
    status === "dirty"
      ? "text-amber-300/90"
      : status === "saving"
        ? "text-sky-300/90"
        : status === "saved"
          ? "text-emerald-300/85"
          : "text-white/35";
  return (
    <span className={`max-w-[min(320px,85vw)] truncate text-center text-[10px] sm:text-left ${colorClass}`} title={text}>
      {text}
    </span>
  );
}

function SavedTripsBar() {
  const {
    savedTrips,
    wanderlyTripId,
    wanderlyWorkspaceBootstrapped,
    tripName,
    setTripName,
    switchToSavedTrip,
    createEmptyTrip,
    renameTrip,
    deleteTrip,
  } = useTrip();
  const [busy, setBusy] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);

  const current = savedTrips.find((t) => t.trip_id.trim() === wanderlyTripId.trim());
  /** Manifest row id (authoritative); avoids rename 404 when UI state id drifts from disk. */
  const tripIdForRename = (current?.trip_id ?? wanderlyTripId).trim();
  const selectTripValue =
    wanderlyTripId && savedTrips.some((t) => t.trip_id === wanderlyTripId) ? wanderlyTripId : undefined;

  if (savedTrips.length === 0) return null;

  const chip =
    "rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-40";
  const chipBtn = `${chip} text-white/55 hover:bg-white/[0.07] hover:text-white/85`;

  return (
    <div className="flex max-w-full flex-col items-center gap-1 text-[12px] text-white/40">
      <div className="flex w-full min-w-0 max-w-full flex-nowrap items-center justify-center gap-x-2 overflow-x-auto [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      {wanderlyTripId ? (
        <>
          <label className="sr-only" htmlFor="wanderly-trip-name-input">
            Trip name
          </label>
          <input
            id="wanderly-trip-name-input"
            type="text"
            value={tripName}
            onChange={(e) => {
              setRenameError(null);
              setTripName(e.target.value);
            }}
            disabled={busy || !wanderlyWorkspaceBootstrapped}
            className={`min-w-0 max-w-[min(220px,40vw)] shrink ${chip} text-white/80 outline-none placeholder:text-white/25 hover:bg-white/[0.05]`}
            placeholder="Name this trip"
            aria-label="Trip name"
            maxLength={120}
          />
          <button
            type="button"
            disabled={busy || !wanderlyWorkspaceBootstrapped}
            className={`${chipBtn} shrink-0 whitespace-nowrap`}
            onClick={() => {
              if (!tripIdForRename) {
                setRenameError("Pick a trip in the list before saving the name.");
                return;
              }
              setRenameError(null);
              setBusy(true);
              void renameTrip(tripIdForRename, tripName.trim() || "Untitled trip")
                .then(() => setRenameError(null))
                .catch((e: unknown) => {
                  const msg = e instanceof Error ? e.message : "Could not save trip name.";
                  setRenameError(msg);
                })
                .finally(() => setBusy(false));
            }}
          >
            Save trip name
          </button>
        </>
      ) : null}
      <span className="hidden shrink-0 text-white/40 sm:inline">Trips</span>
      <Select
        value={selectTripValue}
        onValueChange={(id) => {
          if (!id || id === wanderlyTripId) return;
          setBusy(true);
          void switchToSavedTrip(id).finally(() => setBusy(false));
        }}
        disabled={busy}
      >
        <SelectTrigger
          size="sm"
          aria-label="Active saved trip"
          className="h-8 max-w-[min(240px,50vw)] min-w-[8rem] shrink-0 gap-1.5 rounded-lg border-white/[0.08] bg-white/[0.04] px-3 py-0 text-[12px] font-normal text-white/75 shadow-none hover:bg-white/[0.07] focus-visible:border-indigo-500/35 focus-visible:ring-1 focus-visible:ring-indigo-500/25 data-[size=sm]:h-8 dark:border-white/[0.08] dark:bg-white/[0.04] dark:hover:bg-white/[0.07] [&_svg]:size-3.5 [&_svg]:opacity-60"
        >
          <SelectValue placeholder="Choose trip" />
        </SelectTrigger>
        <SelectContent
          position="popper"
          sideOffset={4}
          className="z-[100] max-h-[min(50vh,16rem)] min-w-[var(--radix-select-trigger-width)] border border-white/[0.1] bg-[#12121a] text-white/85 shadow-xl"
        >
          {savedTrips.map((t) => (
            <SelectItem
              key={t.trip_id}
              value={t.trip_id}
              className="cursor-pointer rounded-md py-1.5 pr-8 pl-2 text-[12px] text-white/80 focus:bg-white/[0.1] focus:text-white data-[highlighted]:bg-white/[0.08] data-[highlighted]:text-white data-[state=checked]:bg-indigo-500/15 data-[state=checked]:text-white [&_svg]:text-indigo-300/90"
            >
              {t.trip_name || t.trip_id}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <button
        type="button"
        disabled={busy}
        className={`${chipBtn} shrink-0 whitespace-nowrap`}
        onClick={() => {
          const label = window.prompt("Name the new trip (optional). Cancel to abort.", "");
          if (label === null) return;
          setBusy(true);
          void createEmptyTrip(label.trim() || undefined).finally(() => setBusy(false));
        }}
      >
        New
      </button>
      {wanderlyTripId ? (
        <button
          type="button"
          disabled={busy}
          title="Delete this trip"
          aria-label="Delete this trip"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/[0.08] bg-white/[0.04] text-amber-300/80 transition-colors hover:bg-white/[0.07] hover:text-amber-200 disabled:cursor-not-allowed disabled:opacity-40"
          onClick={() => {
            if (
              !window.confirm(
                `Delete “${current?.trip_name || wanderlyTripId}”?\n\nThis removes the trip from your list and deletes its saved file.`,
              )
            ) {
              return;
            }
            setBusy(true);
            void deleteTrip(wanderlyTripId).finally(() => setBusy(false));
          }}
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden />
        </button>
      ) : null}
      </div>
      {renameError ? (
        <span className="w-full max-w-full px-1 text-center text-[10px] text-amber-200/90" role="alert">
          {renameError}
        </span>
      ) : null}
    </div>
  );
}

function Workspace() {
  const {
    hasStarted,
    wanderlyWorkspaceBootstrapped,
    wanderlyTripId,
    savedTrips,
    agentStatus,
    profile,
    expandedDest,
    sessionId,
    enrichmentPending,
    setDestinations,
    setEnrichmentPending,
    itineraryLaneQueueDepth,
    tripSwitchTransitioning,
  } = useTrip();

  const showTripFileStatus = Boolean(wanderlyTripId || savedTrips.length > 0);
  const showPlannerHeaderStatus = Boolean(agentStatus || itineraryLaneQueueDepth > 0);
  const showHeaderStatusRow = showTripFileStatus || showPlannerHeaderStatus;

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
                const pinned = typeof s.pinned === "boolean" ? s.pinned : p.pinned;
                const dismissed = typeof s.dismissed === "boolean" ? s.dismissed : p.dismissed;
                return { ...p, ...s, pinned, dismissed };
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

  /** Show profile rail whenever the workspace is active — do not hide it when ``profile_pills`` is temporarily empty. */
  const hasSidebar = hasStarted;

  return (
    <div className="flex h-[100dvh] max-h-[100dvh] flex-col overflow-hidden bg-[#0a0a0f] text-white">
      {/* Header */}
      <header className="shrink-0 z-30 border-b border-white/[0.04] bg-[#0a0a0f]/90 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1400px] flex-col gap-2 px-4 py-3 sm:px-6">
          {/* Top row: full width for trip name + trip switcher; no planner status here. */}
          <div className="flex min-w-0 flex-row items-center gap-2 sm:gap-3">
            <div className="flex shrink-0 flex-col gap-1.5">
              <div className="flex items-center gap-2.5">
                <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
                  <Compass className="w-3.5 h-3.5 text-white" />
                </div>
                <span className="text-[15px] text-white/70">Wanderly</span>
              </div>
            </div>
            <div className="flex min-w-0 flex-1 justify-center px-1 sm:px-2">
              <SavedTripsBar />
            </div>
            <div className="flex shrink-0 items-center gap-2 sm:gap-3">
              <div className="w-7 h-7 rounded-full bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center text-[11px] text-white">
                J
              </div>
            </div>
          </div>
          {/* Second row: trip file / save line + planner queue status share one line. */}
          {showHeaderStatusRow ? (
            <div className="flex min-w-0 flex-col gap-1.5 border-t border-white/[0.05] pt-2 sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:border-t-0 sm:pt-0">
              {showTripFileStatus ? (
                <div className="flex min-w-0 flex-1 justify-center sm:justify-start">
                  <TripDiskPersistBadge />
                </div>
              ) : (
                <div className="hidden min-w-0 flex-1 sm:block" aria-hidden />
              )}
              {showPlannerHeaderStatus ? (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="flex min-w-0 w-full max-w-full shrink-0 flex-col items-center gap-0.5 text-center sm:w-auto sm:max-w-[min(560px,72vw)] sm:items-end sm:text-right"
                >
                  <div className="flex items-center justify-center gap-2 sm:justify-end">
                    <div className="w-1.5 h-1.5 shrink-0 rounded-full bg-indigo-400 animate-pulse" />
                    <span className="text-[12px] text-white/30">
                      {agentStatus ||
                        `Refreshing day-by-day plans (${itineraryLaneQueueDepth} in queue, one at a time)…`}
                    </span>
                  </div>
                  {agentStatus && itineraryLaneQueueDepth > 0 ? (
                    <span className="max-w-full text-[10px] leading-snug text-white/22">
                      Itinerary lane: {itineraryLaneQueueDepth} job{itineraryLaneQueueDepth > 1 ? "s" : ""} queued — cards
                      stay open; updates apply as each run finishes.
                    </span>
                  ) : null}
                </motion.div>
              ) : null}
            </div>
          ) : null}
        </div>
      </header>

      {/* Main + command: flex fills viewport so agent panel sits flush above the input (no dead band). */}
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-hidden">
          <AnimatePresence mode="wait">
            {!hasStarted ? (
              !wanderlyWorkspaceBootstrapped ? (
                <div
                  key="workspace-boot"
                  className="mx-auto flex h-full max-w-[1400px] flex-col items-center justify-center px-4 py-6 sm:px-6"
                >
                  <div className="text-[13px] text-white/35">Loading your workspace…</div>
                </div>
              ) : tripSwitchTransitioning ? (
                <div
                  key="trip-switch"
                  className="mx-auto flex h-full max-w-[1400px] flex-col items-center justify-center px-4 py-6 sm:px-6"
                >
                  <div className="text-[13px] text-white/35">Loading saved trip…</div>
                </div>
              ) : (
                <div
                  key="idle"
                  className="mx-auto flex h-full max-w-[1400px] flex-col justify-center overflow-y-auto px-4 py-6 sm:px-6"
                >
                  <IdleState />
                </div>
              )
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