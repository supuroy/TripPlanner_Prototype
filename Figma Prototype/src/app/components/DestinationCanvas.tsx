import { useState, useMemo, useEffect, useRef, useCallback, useReducer, type ReactNode } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Trash2, Pin, PinOff, ChevronRight, Plane, Clock, Thermometer,
  DollarSign, Hotel, Zap, AlertTriangle, ChevronLeft, ArrowRight,
  MapPin, Calendar, CheckCircle2, Loader2, ChevronDown, ChevronUp,
  RefreshCw, RotateCcw,
} from "lucide-react";
import { ImageWithFallback } from "./figma/ImageWithFallback";
import { useTrip } from "./TripStore";
import type { DestCard, ItinDay } from "./TripStore";
import { syncUi, buildItineraryStream } from "../../api/wanderly";
import {
  clearBookingPipelineCompletedFor,
  isBookingPipelineCompletedFor,
  markBookingPipelineCompleted,
} from "../lib/bookingProgressIds";
import { resetDestinationBookingState } from "../lib/resetDestinationBooking";
import { logItin } from "../lib/itineraryDebug";
import { mergeItineraryBuildIntoDest } from "../lib/itineraryMergeCard";
import { enqueueItineraryLane, scheduleCardsItineraryPrefetch } from "../lib/lazyItineraryPrefetch";
import { streamEventToCaption } from "../lib/streamCaption";
import { ConversationPanel } from "./ConversationPanel";
import {
  SummaryFlightStrip,
  SummaryHotelStrip,
  BookingTimelineFace,
  BookingCostBreakdown,
  bookingPaxFromProfile,
  computeBookingDateHints,
  destHasPersistentBookingSummary,
  liveBookingExtractLooksActionable,
} from "./bookingExpandedContent";

/** Auto-advance hero slideshow when multiple URLs (enrich / planner). */
const HERO_SLIDE_MS = 4200;

const VIBE_COLORS: Record<string, string> = {
  Culture: "bg-purple-500/20 text-purple-300",
  Food: "bg-orange-500/20 text-orange-300",
  Beach: "bg-cyan-500/20 text-cyan-300",
  Spiritual: "bg-amber-500/20 text-amber-300",
  Romance: "bg-pink-500/20 text-pink-300",
  Wine: "bg-red-500/20 text-red-300",
  History: "bg-amber-500/20 text-amber-300",
  Scenic: "bg-emerald-500/20 text-emerald-300",
  Nightlife: "bg-violet-500/20 text-violet-300",
  Art: "bg-rose-500/20 text-rose-300",
  Adventure: "bg-green-500/20 text-green-300",
};

/** Renders ``**inline**`` as bold (same convention as advisor cards / chat). */
function renderHookWithBold(text: string): ReactNode {
  if (!text.includes("**")) return text;
  const parts = text.split(/\*\*(.*?)\*\*/g);
  return parts.map((part, j) =>
    j % 2 === 1 ? (
      <strong key={j} className="font-semibold text-white/80">
        {part}
      </strong>
    ) : (
      <span key={j}>{part}</span>
    ),
  );
}

type ExpandedItinErrorKind = "empty_response" | "request_failed";

function expandedItineraryErrorMessage(kind: ExpandedItinErrorKind, hasPriorOutline: boolean): string {
  if (kind === "request_failed") {
    return hasPriorOutline
      ? "We could not refresh the day-by-day plan just now (the planner was busy or the connection dropped—often a momentary conflict when several trip cards update at once). The outline below is unchanged; try again in a few seconds."
      : "We could not load a day-by-day plan (planner busy or connection issue). Try again in a few seconds.";
  }
  return hasPriorOutline
    ? "The latest run did not return usable day rows, so we kept your previous outline below. You can try again or use a shorter ask."
    : "The planner did not return usable day rows. Try a shorter ask or check the planner service.";
}

/** Shown in commerce strips until the itinerary pass fills ``cost`` / ``budget_guidance`` — no fake ranges. */
function estimateOrDash(s: string | undefined | null): string {
  let t = (s ?? "").trim();
  if (/^tbd\s*\(placeholder\)$/i.test(t) || /^tbd$/i.test(t)) t = "";
  return t || "-";
}

/** True when we should show itinerary-pass copy (not empty / dash / TBD noise). */
function hasMeaningfulEstimateText(s: string | undefined | null): boolean {
  let t = (s ?? "").trim();
  if (!t || t === "-") return false;
  if (/^tbd\s*\(placeholder\)$/i.test(t) || /^tbd$/i.test(t)) return false;
  return true;
}

function destHeroUrls(dest: DestCard): string[] {
  const imgs = (dest.images || []).filter((u): u is string => typeof u === "string" && u.trim().length > 0);
  if (imgs.length) return imgs.slice(0, 6);
  const one = dest.image?.trim();
  return one ? [one] : [];
}

function useRotatingHeroSrc(
  dest: DestCard,
  intervalMs: number,
): { src: string; count: number; index: number } {
  const urls = useMemo(() => destHeroUrls(dest), [dest.image, dest.images]);
  const key = urls.join("\0");
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    setIdx(0);
  }, [key]);
  useEffect(() => {
    if (urls.length <= 1) return;
    const id = window.setInterval(() => {
      setIdx((i) => (i + 1) % urls.length);
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [urls, intervalMs, key]);
  const index = urls.length ? idx % urls.length : 0;
  const src = urls.length ? urls[index] : dest.image;
  return { src: src || dest.image, count: urls.length, index };
}

function ScoreBadge({ score }: { score: number }) {
  const color = score >= 90 ? "text-emerald-400 border-emerald-500/30 bg-emerald-500/20" :
    score >= 80 ? "text-blue-400 border-blue-500/30 bg-blue-500/20" :
    "text-white/40 border-white/10 bg-white/20";
  return (
    <div className={`px-2.5 py-1 rounded-lg border text-[12px] tabular-nums backdrop-blur-sm ${color}`}>
      {score}% match
    </div>
  );
}

function MiniItinerary({ dest }: { dest: DestCard }) {
  const { profile } = useTrip();
  /** Per day number: show all activity bullets vs first two only. */
  const [expandedDayNums, setExpandedDayNums] = useState<Record<number, boolean>>({});
  const itinerary = dest.itinerary ?? [];

  // Parse trip dates from profile
  const tripDates = useMemo(() => {
    const whenPill = profile.find(p => p.key === "when");
    if (!whenPill?.value) return null;

    // Parse "Mar 15–22" format
    const match = whenPill.value.match(/(\w+)\s+(\d+)(?:–|-)(\d+)/);
    if (!match) return null;
    
    const [, month, startDay, endDay] = match;
    const monthMap: Record<string, number> = {
      Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5,
      Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11
    };
    
    const monthNum = monthMap[month];
    if (monthNum === undefined) return null;
    
    const year = new Date().getFullYear();
    const startDate = new Date(year, monthNum, parseInt(startDay));
    
    // Calculate end date based on itinerary length
    const endDate = new Date(startDate);
    endDate.setDate(endDate.getDate() + (itinerary.length || 7) - 1);

    return {
      startDate,
      endDate,
      month,
      startDay: parseInt(startDay),
      endDay: endDate.getDate(),
      displayMonth: month,
    };
  }, [profile, itinerary]);

  // Generate calendar dates for each day
  const daysWithDates = useMemo(() => {
    if (!tripDates) return itinerary;

    return itinerary.map((day) => {
      const date = new Date(tripDates.startDate);
      date.setDate(date.getDate() + ((day.day ?? 1) - 1));
      
      return {
        ...day,
        date: date.getDate(),
        month: date.toLocaleString('en', { month: 'short' }),
      };
    });
  }, [itinerary, tripDates]);

  if (!itinerary.length) return null;

  return (
    <div className="mt-4">
      <div className="flex items-center justify-between mb-3">
        <p className="text-[11px] text-white/30 uppercase tracking-widest">
          {itinerary.length}-Day Plan
          {tripDates && (
            <span className="ml-2 text-white/20">
              {tripDates.displayMonth} {tripDates.startDay}–{tripDates.endDay}
            </span>
          )}
        </p>
      </div>

      <div className="space-y-1">
        {daysWithDates?.map((day) => {
          const items = day.items ?? [];
          const expanded = Boolean(expandedDayNums[day.day]);
          const extra = items.length - 2;
          const toggle = () =>
            setExpandedDayNums((prev) => ({ ...prev, [day.day]: !prev[day.day] }));
          return (
            <div key={day.day} className="flex items-start gap-3 group">
              <div className="w-9 h-9 rounded-md bg-white/[0.06] flex flex-col items-center justify-center shrink-0 mt-0.5 border border-white/[0.04]">
                {'date' in day && day.date ? (
                  <>
                    <span className="text-[9px] text-white/20 uppercase leading-none">{'month' in day && day.month}</span>
                    <span className="text-[13px] text-white/60 font-medium leading-none mt-0.5">{day.date}</span>
                  </>
                ) : (
                  <span className="text-[11px] text-white/30">{day.day}</span>
                )}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-[13px] text-white/70">{day.label ?? ""}</p>
                {expanded ? (
                  <ul className="mt-1.5 space-y-1.5 pl-0.5" role="list">
                    {items.map((item, i) => (
                      <li
                        key={i}
                        className="text-[11px] text-white/40 leading-relaxed border-l border-white/[0.12] pl-2.5"
                      >
                        {item}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="flex flex-wrap items-baseline gap-x-1 gap-y-0.5 mt-1">
                    {items.slice(0, 2).map((item, i) => (
                      <span key={i} className="text-[11px] text-white/30">
                        {item}
                        {i < 1 && items.length > 1 ? " ·" : ""}
                      </span>
                    ))}
                  </div>
                )}
                {items.length > 2 && (
                  <button
                    type="button"
                    onClick={toggle}
                    aria-expanded={expanded}
                    className="mt-1 flex items-center gap-1 rounded-md py-0.5 pr-1 text-left text-[11px] text-sky-400/90 hover:text-sky-300 hover:underline underline-offset-2 focus-visible:outline focus-visible:outline-1 focus-visible:outline-sky-400/50"
                  >
                    {expanded ? (
                      <>
                        <ChevronUp className="h-3 w-3 shrink-0 opacity-80" aria-hidden />
                        Show less
                      </>
                    ) : (
                      <>
                        <ChevronDown className="h-3 w-3 shrink-0 opacity-80" aria-hidden />
                        +{extra} more
                      </>
                    )}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ActivityStrip({ dest }: { dest: DestCard }) {
  if (!dest.activities) return null;
  return (
    <div className="mt-4">
      <p className="text-[11px] text-white/30 uppercase tracking-widest mb-2">Curated Activities</p>
      <div className="flex flex-wrap gap-1.5">
        {dest.activities.map((a, i) => (
          <span key={i} className="px-2.5 py-1 rounded-lg bg-white/[0.04] border border-white/[0.06] text-[11px] text-white/50">
            {a.name} <span className="text-white/25">· {a.price}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/** Itinerary-planner flight band (until live booking search returns real offers). */
function PlannerFlightStrip({ dest }: { dest: DestCard }) {
  const flightBallpark = (
    dest.budget_guidance?.flight_ballpark || dest.budget_guidance?.flight_band_short || ""
  ).trim();
  const showFlightGuidance = hasMeaningfulEstimateText(flightBallpark);
  if (!showFlightGuidance && (dest.flights?.length ?? 0) === 0) return null;
  return (
    <motion.div
      className="mt-4"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.3 }}
    >
      <div className="mb-2 flex items-center justify-between">
        <p className="text-[11px] uppercase tracking-widest text-white/30">Flight Options</p>
      </div>
      <div className="relative group">
        {showFlightGuidance ? (
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.04] px-3 py-2.5">
            <p className="break-words text-[11px] leading-snug text-white/70">{flightBallpark}</p>
          </div>
        ) : (
          <div className="min-h-[44px] rounded-xl border border-white/[0.05] bg-white/[0.02]" aria-label="Flight estimate pending" />
        )}
      </div>
    </motion.div>
  );
}

/** Itinerary-planner lodging band (until live booking search). */
function PlannerHotelStrip({ dest }: { dest: DestCard }) {
  const lodgingNote = (
    dest.budget_guidance?.lodging_style || dest.budget_guidance?.lodging_band_short || ""
  ).trim();
  const showLodgingGuidance = hasMeaningfulEstimateText(lodgingNote);
  if (!showLodgingGuidance && (dest.hotels?.length ?? 0) === 0) return null;
  return (
    <motion.div
      className="mt-4"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.4 }}
    >
      <div className="mb-2 flex items-center justify-between">
        <p className="text-[11px] uppercase tracking-widest text-white/30">Stay Options</p>
      </div>
      <div className="relative group">
        {showLodgingGuidance ? (
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.04] px-3 py-2.5">
            <p className="break-words text-[11px] leading-snug text-white/70">{lodgingNote}</p>
          </div>
        ) : (
          <div className="min-h-[44px] rounded-xl border border-white/[0.05] bg-white/[0.02]" aria-label="Lodging estimate pending" />
        )}
      </div>
    </motion.div>
  );
}

/* ── Comparison Mini Card (shown in sidebar when another card is expanded) ── */
function ComparisonMiniCard({
  dest,
  onExpand,
  expandLocked,
}: {
  dest: DestCard;
  onExpand: () => void;
  expandLocked: boolean;
}) {
  const { setDestinations } = useTrip();
  const { src: heroSrc, count: heroCount, index: heroIndex } = useRotatingHeroSrc(dest, HERO_SLIDE_MS);
  const bestFlight = dest.flights?.find(f => f.best) || dest.flights?.[0];
  const bestHotel = dest.hotels?.find(h => h.best) || dest.hotels?.[0];

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      onClick={() => {
        if (expandLocked) return;
        onExpand();
      }}
      title={expandLocked ? "Planner is updating trip cards—try again in a moment." : undefined}
      className={`relative rounded-xl overflow-hidden border transition-all group ${
        expandLocked
          ? "cursor-not-allowed opacity-55 border-white/[0.06] bg-white/[0.02]"
          : `cursor-pointer hover:border-white/[0.15] ${
              dest.pinned ? "border-indigo-500/30 bg-indigo-500/[0.04]" : "border-white/[0.06] bg-white/[0.02]"
            }`
      }`}
    >
      {/* Warning badge */}
      {dest.warnings && dest.warnings.length > 0 && (
        <div className="absolute top-2 right-2 z-10">
          <div className="w-5 h-5 rounded-full bg-amber-500/20 border border-amber-500/30 flex items-center justify-center">
            <AlertTriangle className="w-3 h-3 text-amber-400" />
          </div>
        </div>
      )}

      {/* Compact image + overlay */}
      <div className="relative h-20 overflow-hidden">
        <ImageWithFallback src={heroSrc} alt={dest.name} className="w-full h-full object-cover" />
        <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/30 to-transparent" />
        {heroCount > 1 && (
          <div className="absolute bottom-1 left-1/2 -translate-x-1/2 flex gap-0.5">
            {Array.from({ length: heroCount }).map((_, i) => (
              <span
                key={i}
                className={`h-0.5 rounded-full transition-all ${i === heroIndex ? "w-2.5 bg-white/90" : "w-2 bg-white/35"}`}
              />
            ))}
          </div>
        )}
        <div className="absolute bottom-2 left-2.5 right-2.5 flex items-end justify-between">
          <div>
            <h4 className="text-white text-[14px]">{dest.name}</h4>
            <p className="text-white/40 text-[10px]">{dest.country}</p>
          </div>
          <ScoreBadge score={dest.score} />
        </div>
        {dest.pinned && (
          <div className="absolute top-0 left-0 w-full h-0.5 bg-gradient-to-r from-indigo-500 to-purple-500" />
        )}
      </div>

      {/* Quick stats row — short bands in price rows; icons separated so text never overlaps. */}
      <div className="px-2.5 py-2">
        <div className="grid grid-cols-3 gap-1 text-[10px]">
          <div className="flex min-w-0 flex-col items-stretch gap-1 px-1 py-1.5 rounded-lg bg-white/[0.03]">
            <div className="flex justify-center">
              <Plane className="w-3 h-3 shrink-0 text-blue-400" aria-hidden />
            </div>
            <div className="min-w-0 text-center">
              <p className="text-[9px] leading-tight text-white/40 line-clamp-1">{estimateOrDash(dest.flight)}</p>
              <p className="mt-0.5 text-[9px] leading-snug text-white/55 line-clamp-3 break-words">
                {bestFlight?.price?.trim() || "-"}
              </p>
            </div>
          </div>
          <div className="flex min-w-0 flex-col items-stretch gap-1 px-1 py-1.5 rounded-lg bg-white/[0.03]">
            <div className="flex justify-center">
              <Hotel className="w-3 h-3 shrink-0 text-purple-400" aria-hidden />
            </div>
            <div className="min-w-0 text-center">
              <p className="text-[9px] leading-snug text-white/55 line-clamp-3 break-words">
                {bestHotel?.price?.trim() || "-"}
              </p>
              <p className="mt-0.5 text-[9px] text-white/35">{bestHotel?.nights != null ? `${bestHotel.nights}n` : "-"}</p>
            </div>
          </div>
          <div className="flex min-w-0 flex-col items-stretch gap-1 px-1 py-1.5 rounded-lg bg-white/[0.03]">
            <div className="flex justify-center">
              <DollarSign className="w-3 h-3 shrink-0 text-emerald-400" aria-hidden />
            </div>
            <div className="min-w-0 text-center">
              <p className="text-[9px] leading-snug text-white/55 line-clamp-3 break-words">
                {estimateOrDash(dest.cost)}
              </p>
              <p className="mt-0.5 text-[9px] text-white/35">total</p>
            </div>
          </div>
        </div>

        {/* Vibes - compact */}
        <div className="flex flex-wrap gap-1 mt-2">
          {(dest.vibe ?? []).slice(0, 3).map((v) => (
            <span key={v} className={`px-1.5 py-0.5 rounded text-[9px] ${VIBE_COLORS[v] || "bg-white/5 text-white/40"}`}>
              {v}
            </span>
          ))}
          {(dest.vibe ?? []).length > 3 && (
            <span className="text-[9px] text-white/20">+{(dest.vibe ?? []).length - 3}</span>
          )}
        </div>

        {/* Warning inline */}
        {dest.warnings && dest.warnings.length > 0 && (
          <div className="mt-2 px-2 py-1.5 rounded-lg bg-amber-500/[0.08] border border-amber-500/20">
            <p className="text-[10px] text-amber-300/80 leading-tight">{dest.warnings[0]}</p>
          </div>
        )}

        {/* Expand hint */}
        <div className="flex items-center justify-center gap-1 mt-2 text-[10px] text-white/15 group-hover:text-white/30 transition-colors">
          <span>View full plan</span>
          <ArrowRight className="w-2.5 h-2.5" />
        </div>
      </div>
    </motion.div>
  );
}

/* ── Expanded card view (detailed itinerary, flights, hotels) ── */
function ExpandedCard({ dest }: { dest: DestCard }) {
  const {
    setDestinations,
    setExpandedDest,
    addResponse,
    sessionId,
    profile,
    itineraryLaneQueueDepth,
    queuePlanReviewAfterItineraries,
  } = useTrip();
  const [currentImageIndex, setCurrentImageIndex] = useState(0);
  const [conversationMode, setConversationMode] = useState<"minimized" | "footer" | "panel">("minimized");
  const [itinLoading, setItinLoading] = useState(false);
  const [itinErrorKind, setItinErrorKind] = useState<ExpandedItinErrorKind | null>(null);
  const [itinThought, setItinThought] = useState("");
  const itinStreamAssistantRef = useRef({ text: "" });
  const itinStreamThinkingRef = useRef({ text: "" });
  const [bookingFace, setBookingFace] = useState(false);
  /** True while the live Playwright booking pipeline is running for this card (blocks premature "cached" mode). */
  const liveBookingPipelineStartedRef = useRef(false);
  /** Increments when pipeline bookkeeping changes so we re-evaluate cached vs live (refs alone do not re-render). */
  const [bookingTimelineRevision, bumpBookingTimelineRevision] = useReducer((x: number) => x + 1, 0);

  const useCachedBookingTimeline =
    bookingTimelineRevision >= 0 &&
    (isBookingPipelineCompletedFor(dest.id) ||
      (Boolean(dest.readyToBook) &&
        liveBookingExtractLooksActionable(dest) &&
        !liveBookingPipelineStartedRef.current));

  useEffect(() => {
    setItinThought("");
    itinStreamAssistantRef.current = { text: "" };
    itinStreamThinkingRef.current = { text: "" };
    setItinErrorKind(null);
    setBookingFace(false);
    liveBookingPipelineStartedRef.current = false;
  }, [dest.id]);

  const profileSig = useMemo(() => JSON.stringify(profile.map((p) => [p.key, p.value])), [profile]);
  const needsItinFetch =
    Boolean(sessionId) && (!(dest.itinerary?.length) || dest.itinerary_dirty === true);
  const fetchTriggerReason = useMemo(() => {
    if (!sessionId) return "no_session";
    if (!(dest.itinerary?.length)) return "missing_itinerary";
    if (dest.itinerary_dirty === true) return "itinerary_dirty_true";
    return "none";
  }, [sessionId, dest.itinerary?.length, dest.itinerary_dirty]);

  useEffect(() => {
    if (!sessionId || !needsItinFetch) {
      setItinLoading(false);
      return;
    }
    setItinErrorKind(null);
    setItinThought("");
    itinStreamAssistantRef.current = { text: "" };
    itinStreamThinkingRef.current = { text: "" };
    const ac = new AbortController();
    const requestId =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? `itin-exp-${crypto.randomUUID()}`
        : `itin-exp-${Date.now()}`;
    /** Absorb React Strict Mode mount→cleanup→remount double effect in dev (two itinerary calls). */
    const debounceMs = import.meta.env.DEV ? 140 : 0;
    logItin("expanded_effect_start", {
      requestId,
      destId: dest.id,
      sessionId: sessionId.slice(0, 12),
      needsItinFetch,
      fetchTriggerReason,
      itinerary_dirty: dest.itinerary_dirty,
      itineraryLen: dest.itinerary?.length ?? 0,
      profileSigLen: profileSig.length,
      debounceMs,
    });
    const startTimer = window.setTimeout(() => {
      if (ac.signal.aborted) return;
      setItinLoading(true);
      enqueueItineraryLane(async () => {
        if (ac.signal.aborted) {
          setItinLoading(false);
          return;
        }
        try {
          const res = await buildItineraryStream(
            sessionId,
            dest as unknown as Record<string, unknown>,
            profile,
            (ev) => {
              if (ac.signal.aborted) return;
              const cap = streamEventToCaption(ev, itinStreamAssistantRef.current, itinStreamThinkingRef.current, {
                structuredStreamMode: "itinerary",
              });
              if (cap) setItinThought(cap);
            },
            { signal: ac.signal, requestId },
          );
          if (ac.signal.aborted) {
            logItin("expanded_fetch_aborted_after_resolve", { requestId, destId: dest.id });
            return;
          }
          const rows = res.itinerary || [];
          const budget = res.budget_guidance ?? null;
          logItin("expanded_fetch_done", {
            requestId,
            destId: dest.id,
            rowCount: rows.length,
            destMatch: res.destination_id === dest.id,
          });
          if (rows.length === 0) {
            setItinErrorKind("empty_response");
            setDestinations((prev) =>
              prev.map((d) => (d.id === dest.id ? { ...d, itinerary_dirty: false } : d)),
            );
            return;
          }
          setItinErrorKind(null);
          setDestinations((prev) => {
            const next = prev.map((d) => {
              if (d.id !== dest.id) return d;
              return mergeItineraryBuildIntoDest(d, rows, budget, res.booking_search_hints ?? null);
            });
            queueMicrotask(() => {
              void syncUi(sessionId, next as unknown[]).catch(() => {});
              queuePlanReviewAfterItineraries(sessionId);
            });
            return next;
          });
        } catch (err: unknown) {
          if (ac.signal.aborted) {
            logItin("expanded_fetch_abort_error", { requestId, destId: dest.id, name: (err as Error)?.name });
            return;
          }
          logItin("expanded_fetch_error", { requestId, destId: dest.id, message: String(err) });
          setItinErrorKind("request_failed");
        } finally {
          if (!ac.signal.aborted) setItinLoading(false);
        }
      }, dest.id);
    }, debounceMs);
    return () => {
      window.clearTimeout(startTimer);
      logItin("expanded_effect_cleanup_abort", { requestId, destId: dest.id });
      ac.abort();
    };
    // Keep deps minimal so a long-running build is not aborted by redundant identity churn
    // (e.g. ``profile`` ref vs stable ``profileSig``).
  }, [sessionId, dest.id, needsItinFetch, profileSig, addResponse, setDestinations, queuePlanReviewAfterItineraries]);

  const images = useMemo(() => {
    const imgs = (dest.images || []).filter((u): u is string => typeof u === "string" && u.trim().length > 0);
    if (imgs.length) return imgs;
    return dest.image?.trim() ? [dest.image] : [];
  }, [dest.images, dest.image]);
  const imageKey = images.join("|");

  useEffect(() => {
    setCurrentImageIndex(0);
  }, [imageKey, dest.id]);

  // Loop hero slideshow while expanded
  useEffect(() => {
    if (images.length <= 1) return;
    const interval = window.setInterval(() => {
      setCurrentImageIndex((prev) => (prev + 1) % images.length);
    }, HERO_SLIDE_MS);
    return () => window.clearInterval(interval);
  }, [images.length, imageKey, dest.id]);

  const currentImage = images[currentImageIndex] ?? dest.image;

  const nextImage = () => setCurrentImageIndex((prev) => (prev + 1) % images.length);
  const prevImage = () => setCurrentImageIndex((prev) => (prev - 1 + images.length) % images.length);

  const dismiss = (e: React.MouseEvent) => {
    e.stopPropagation();
    setDestinations((prev: DestCard[]) => prev.map(d => d.id === dest.id ? { ...d, dismissed: true } : d));
  };

  const togglePin = (e: React.MouseEvent) => {
    e.stopPropagation();
    const willPin = !dest.pinned;
    setDestinations((prev: DestCard[]) => prev.map(d => d.id === dest.id ? { ...d, pinned: !d.pinned } : d));
    
    // Show feedback about what pinning does
    if (willPin) {
      addResponse({
        type: "success",
        text: `📌 ${dest.name} is now bookmarked for comparison. Click 'Ready to Book' when you want to see detailed flight and hotel options.`,
        relatedTo: dest.id,
      });
    }
  };

  // Extract location flow for trip visualization
  const locationFlow = useMemo(() => {
    if (!dest.itinerary?.length) return null;

    const locations: Array<{ name: string; isTransfer?: boolean }> = [{ name: "Home" }];

    dest.itinerary.forEach((day) => {
      const label = typeof day.label === "string" ? day.label : "";
      if (label.includes("→")) {
        const parts = label.split("→").map(p => p.trim());
        parts.forEach((loc) => {
          // Skip "Arrive" and other action words
          if (!loc.match(/arrive|fly|depart/i) && loc) {
            const exists = locations.find(l => l.name.toLowerCase() === loc.toLowerCase());
            if (!exists && loc.length > 0) {
              locations.push({ name: loc, isTransfer: true });
            }
          }
        });
      }
    });
    
    locations.push({ name: "Home" });
    return locations.filter((loc, idx, arr) => 
      idx === 0 || idx === arr.length - 1 || loc.isTransfer
    );
  }, [dest.itinerary]);

  const onBookingFirstLoad = useCallback(() => {
    liveBookingPipelineStartedRef.current = false;
    if (!isBookingPipelineCompletedFor(dest.id)) {
      markBookingPipelineCompleted(dest.id);
    }
    bumpBookingTimelineRevision();
  }, [dest.id]);

  const showReadyToBookCta = useMemo(() => {
    if (dest.readyToBook) return false;
    if ((dest.flights?.length ?? 0) > 0 || (dest.hotels?.length ?? 0) > 0) return true;
    const fp = (dest.budget_guidance?.flight_ballpark || dest.budget_guidance?.flight_band_short || "").trim();
    const lp = (dest.budget_guidance?.lodging_style || dest.budget_guidance?.lodging_band_short || "").trim();
    return hasMeaningfulEstimateText(fp) || hasMeaningfulEstimateText(lp);
  }, [dest.readyToBook, dest.flights?.length, dest.hotels?.length, dest.budget_guidance]);

  const refreshBookingSummary = useCallback(() => {
    clearBookingPipelineCompletedFor(dest.id);
    liveBookingPipelineStartedRef.current = false;
    setBookingFace(false);
    setDestinations((prev) => {
      const next = prev.map((d) => (d.id === dest.id ? resetDestinationBookingState(d) : d));
      if (sessionId) {
        queueMicrotask(() => {
          void syncUi(sessionId, next as unknown[]).catch(() => {});
        });
      }
      return next;
    });
    bumpBookingTimelineRevision();
    addResponse({
      type: "tip",
      text: `Cleared saved flight and hotel results for ${dest.name}. Tap “Ready to Book” when you want fresh live prices.`,
    });
  }, [dest.id, dest.name, sessionId, setDestinations, addResponse]);

  const readyToBook = Boolean(dest.readyToBook);
  const flipTooltip = !readyToBook
    ? "Open the booking view after you tap Ready to Book on this card"
    : bookingFace
      ? "Return to the itinerary view"
      : "Open the booking view (flights and hotels)";
  const pinTooltip = dest.pinned
    ? "Unpin this destination (remove the comparison highlight)"
    : "Pin this destination to keep it highlighted while you compare";
  const dismissTooltip = "Remove this destination from your trip (discards this card)";

  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.95 }}
      transition={{ type: "spring", damping: 25, stiffness: 200 }}
      className={`relative rounded-2xl overflow-hidden border h-full flex flex-col ${
        dest.pinned
          ? "border-indigo-500/40 bg-indigo-500/[0.05]"
          : "border-white/[0.08] bg-white/[0.03]"
      }`}
    >
      {/* Warning banner */}
      {dest.warnings && dest.warnings.length > 0 && (
        <div className="px-4 py-2 bg-amber-500/[0.08] border-b border-amber-500/20 flex items-center gap-2">
          <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
          <p className="text-[12px] text-amber-300/80">{dest.warnings[0]}</p>
        </div>
      )}

      <div className="flex flex-1 min-h-0">
        <div
          className={`flex flex-col flex-1 min-h-0 transition-all ${
            conversationMode === "panel" ? "lg:w-[60%]" : "w-full"
          }`}
        >
          <div className="flex flex-col lg:flex-row flex-1 min-h-0">
        {/* Image — hidden on lg when conversation panel is open to free horizontal space */}
        <div
          className={`relative overflow-hidden lg:w-72 h-48 lg:h-auto shrink-0 cursor-pointer group ${
            conversationMode === "panel" ? "lg:hidden" : ""
          }`}
          onClick={() => setExpandedDest(null)}
        >
          <ImageWithFallback src={currentImage} alt={dest.name} className="w-full h-full object-cover" />
          <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-black/20" />

          {/* Image navigation - only show if multiple images */}
          {images.length > 1 && (
            <>
              {/* Prev/Next buttons */}
              <button
                onClick={(e) => { e.stopPropagation(); prevImage(); }}
                className="absolute left-2 top-1/2 -translate-y-1/2 w-8 h-8 rounded-full bg-black/50 backdrop-blur-sm flex items-center justify-center text-white/70 hover:text-white hover:bg-black/70 transition-all opacity-0 group-hover:opacity-100"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); nextImage(); }}
                className="absolute right-2 top-1/2 -translate-y-1/2 w-8 h-8 rounded-full bg-black/50 backdrop-blur-sm flex items-center justify-center text-white/70 hover:text-white hover:bg-black/70 transition-all opacity-0 group-hover:opacity-100"
              >
                <ChevronRight className="w-4 h-4" />
              </button>

              {/* Dot indicators */}
              <div className="absolute bottom-16 left-1/2 -translate-x-1/2 flex gap-1.5">
                {images.map((_, idx) => (
                  <button
                    key={idx}
                    onClick={(e) => { e.stopPropagation(); setCurrentImageIndex(idx); }}
                    className={`w-1.5 h-1.5 rounded-full transition-all ${
                      idx === currentImageIndex 
                        ? "bg-white w-4" 
                        : "bg-white/30 hover:bg-white/50"
                    }`}
                  />
                ))}
              </div>
            </>
          )}

          {/* Controls */}
          <div className="absolute top-2.5 right-2.5 z-20 flex gap-1.5">
            <motion.button
              type="button"
              title={flipTooltip}
              aria-label={flipTooltip}
              disabled={!readyToBook}
              onClick={(e) => {
                e.stopPropagation();
                if (!readyToBook) return;
                setBookingFace((v) => !v);
              }}
              whileTap={readyToBook ? { rotate: 180, scale: 0.9 } : undefined}
              transition={{ duration: 0.3 }}
              className={`flex h-7 w-7 items-center justify-center rounded-lg backdrop-blur-sm transition-all ${
                readyToBook
                  ? "cursor-pointer bg-indigo-500 text-white hover:bg-indigo-600"
                  : "cursor-not-allowed bg-black/40 text-white/30 opacity-50"
              }`}
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            </motion.button>
            <button
              type="button"
              onClick={togglePin}
              title={pinTooltip}
              className={`w-7 h-7 rounded-lg flex items-center justify-center transition-all ${dest.pinned ? "bg-indigo-500 text-white" : "bg-black/40 text-white/60 hover:text-white backdrop-blur-sm"}`}
              aria-label={pinTooltip}
            >
              {dest.pinned ? <PinOff className="w-3.5 h-3.5" /> : <Pin className="w-3.5 h-3.5" />}
            </button>
            <button
              type="button"
              onClick={dismiss}
              title={dismissTooltip}
              className="w-7 h-7 rounded-lg bg-black/40 text-white/60 hover:text-white flex items-center justify-center backdrop-blur-sm"
              aria-label={dismissTooltip}
            >
              <Trash2 className="w-3.5 h-3.5" aria-hidden />
            </button>
          </div>

          {/* Score */}
          <div className="absolute top-2.5 left-2.5">
            <ScoreBadge score={dest.score} />
          </div>

          {/* Name */}
          <div className="absolute bottom-3 left-3 right-3">
            <h3 className="text-white text-[20px]">{dest.name}</h3>
            <p className="text-white/50 text-[12px]">{dest.country}</p>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 min-h-0 p-5 overflow-y-auto overflow-x-auto">
          {/* When hero is hidden (split view), show title + actions in the scroll column */}
          {conversationMode === "panel" && (
            <div className="mb-4 hidden lg:flex items-start justify-between gap-3 border-b border-white/[0.06] pb-3">
              <div className="min-w-0">
                <p className="text-[10px] text-white/30 uppercase tracking-widest">Destination</p>
                <h3 className="text-white text-[18px] font-medium leading-tight truncate">{dest.name}</h3>
                <p className="text-[12px] text-white/45">{dest.country}</p>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <ScoreBadge score={dest.score} />
                <motion.button
                  type="button"
                  title={flipTooltip}
                  aria-label={flipTooltip}
                  disabled={!readyToBook}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (!readyToBook) return;
                    setBookingFace((v) => !v);
                  }}
                  whileTap={readyToBook ? { rotate: 180, scale: 0.9 } : undefined}
                  transition={{ duration: 0.3 }}
                  className={`flex h-8 w-8 items-center justify-center rounded-lg border transition-all ${
                    readyToBook
                      ? "cursor-pointer border-indigo-500/40 bg-indigo-500 text-white hover:bg-indigo-600"
                      : "cursor-not-allowed border-white/[0.08] bg-white/[0.04] text-white/30 opacity-50"
                  }`}
                >
                  <RefreshCw className="h-3.5 w-3.5" aria-hidden />
                </motion.button>
                <button
                  type="button"
                  onClick={togglePin}
                  title={pinTooltip}
                  className={`w-8 h-8 rounded-lg flex items-center justify-center transition-all ${
                    dest.pinned ? "bg-indigo-500 text-white" : "bg-white/[0.06] text-white/60 hover:text-white border border-white/[0.08]"
                  }`}
                  aria-label={pinTooltip}
                >
                  {dest.pinned ? <PinOff className="w-3.5 h-3.5" /> : <Pin className="w-3.5 h-3.5" />}
                </button>
                <button
                  type="button"
                  onClick={dismiss}
                  title={dismissTooltip}
                  className="w-8 h-8 rounded-lg bg-white/[0.06] text-white/60 hover:text-white flex items-center justify-center border border-white/[0.08]"
                  aria-label={dismissTooltip}
                >
                  <Trash2 className="w-3.5 h-3.5" aria-hidden />
                </button>
              </div>
            </div>
          )}
          {/* Trip flow visualization with icons - moved to top */}
          {locationFlow && locationFlow.length > 2 && (
            <div className="mb-4 flex flex-wrap items-center gap-2 px-3 py-2 rounded-lg bg-white/[0.04] border border-white/[0.06]">
              {locationFlow.map((loc, idx) => (
                <div key={idx} className="flex items-center gap-1.5">
                  <div className="flex items-center gap-1.5">
                    {idx === 0 || idx === locationFlow.length - 1 ? (
                      <MapPin className="w-3 h-3 text-white/30" />
                    ) : (
                      <MapPin className="w-3 h-3 text-blue-400" />
                    )}
                    <span className="text-[11px] text-white/50 whitespace-nowrap">{loc.name}</span>
                  </div>
                  {idx < locationFlow.length - 1 && (
                    <ArrowRight className="w-3 h-3 text-white/20 shrink-0" />
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="flex items-center justify-between">
            <p className="text-[13px] text-white/50 leading-relaxed max-w-md">{renderHookWithBold(dest.hook)}</p>
            <button
              onClick={() => setExpandedDest(null)}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.06] text-[11px] text-white/30 hover:text-white/50 transition-colors shrink-0"
            >
              <ChevronLeft className="w-3 h-3" />
              All options
            </button>
          </div>

          {/* Quick stats */}
          <div className="flex items-center gap-3 mt-3 text-[11px] text-white/30">
            <span className="flex items-center gap-1"><Thermometer className="w-3 h-3" />{dest.temp}</span>
            <span className="flex items-center gap-1"><Plane className="w-3 h-3" />{dest.flight}</span>
            <span className="flex items-center gap-1"><DollarSign className="w-3 h-3" />{estimateOrDash(dest.cost)}</span>
          </div>

          {/* Vibes */}
          <div className="flex flex-wrap gap-1 mt-3">
            {(dest.vibe ?? []).map((v) => (
              <span key={v} className={`px-2 py-0.5 rounded-md text-[10px] ${VIBE_COLORS[v] || "bg-white/5 text-white/40"}`}>
                {v}
              </span>
            ))}
          </div>

          {/* Full plan */}
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }}>
            {/* Itinerary Confirmed Badge - only when pinned */}
            {dest.pinned && (
              <motion.div 
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                className="mb-4 flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-500/10 border border-emerald-500/30"
              >
                <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                <div className="flex-1">
                  <p className="text-[12px] text-emerald-300 font-medium">Itinerary Confirmed</p>
                  <p className="text-[10px] text-emerald-400/60">All prices locked & bookings finalized</p>
                </div>
              </motion.div>
            )}

            <div className="mt-4 pt-4 border-t border-white/[0.06]">
              <div className="[perspective:1200px]">
                <motion.div
                  className="relative grid grid-cols-1 [transform-style:preserve-3d]"
                  style={{ transformStyle: "preserve-3d" }}
                  animate={{ rotateY: bookingFace ? 180 : 0 }}
                  transition={{ duration: 0.4, ease: [0.4, 0, 0.2, 1] }}
                >
                  {/* Normal face — same grid cell as back; height follows content; outer column scrolls only */}
                  <div
                    className="col-start-1 row-start-1 w-full min-w-0 [backface-visibility:hidden]"
                    style={{
                      WebkitBackfaceVisibility: "hidden",
                      transform: "rotateY(0deg) translateZ(0)",
                      pointerEvents: bookingFace ? "none" : "auto",
                    }}
                  >
                    <div className="grid grid-cols-1 gap-x-6 lg:grid-cols-2">
                      <div>
                        {itinLoading ? (
                          <div
                            data-testid="expanded-itinerary-loading"
                            className="mt-4 min-h-[120px] flex flex-col rounded-xl border border-white/[0.06] bg-white/[0.02]"
                          >
                            <div className="flex items-center gap-2 px-3 py-2.5 border-b border-white/[0.05]">
                              <Loader2 className="w-4 h-4 animate-spin shrink-0 text-indigo-400/80" aria-hidden />
                              <span className="text-[12px] text-white/45">Building itinerary</span>
                            </div>
                            <div className="px-3 py-2 space-y-2">
                              {itineraryLaneQueueDepth > 1 ? (
                                <p className="text-[10px] text-indigo-300/55 leading-snug border border-indigo-500/15 rounded-lg px-2 py-1.5 bg-indigo-500/[0.06]">
                                  {itineraryLaneQueueDepth} day-by-day updates are in the server queue (one at a time so plans
                                  don’t collide). You can browse other cards; this one will fill in when its turn runs.
                                </p>
                              ) : null}
                              {itinThought.trim() ? (
                                <p className="text-[11px] text-white/35 leading-relaxed whitespace-pre-wrap font-sans">
                                  {itinThought.trim()}
                                </p>
                              ) : (
                                <p className="text-[11px] text-white/25 leading-relaxed">
                                  Working through pacing, highlights, and day structure…
                                </p>
                              )}
                            </div>
                          </div>
                        ) : itinErrorKind ? (
                          <div className="mt-4 space-y-3">
                            <p className="text-[12px] text-amber-400/80">
                              {expandedItineraryErrorMessage(
                                itinErrorKind,
                                Boolean(dest.itinerary && dest.itinerary.length > 0),
                              )}
                            </p>
                            {dest.itinerary && dest.itinerary.length > 0 ? <MiniItinerary dest={dest} /> : null}
                          </div>
                        ) : (
                          <MiniItinerary dest={dest} />
                        )}
                        <ActivityStrip dest={dest} />
                      </div>
                      <div>
                        {liveBookingExtractLooksActionable(dest) ? (
                          <BookingCostBreakdown
                            dest={dest}
                            paxCount={bookingPaxFromProfile(profile)}
                            variant="summary"
                          />
                        ) : (
                          <>
                            {dest.liveBookingEstimates ? (
                              <>
                                <SummaryFlightStrip dest={dest} />
                                <SummaryHotelStrip dest={dest} />
                              </>
                            ) : (
                              <>
                                <PlannerFlightStrip dest={dest} />
                                <PlannerHotelStrip dest={dest} />
                              </>
                            )}

                            <motion.div
                              className="mt-6 px-4 py-3 rounded-xl bg-gradient-to-r from-indigo-500/10 to-purple-500/10 border border-indigo-500/20"
                              initial={{ opacity: 0, y: 10 }}
                              animate={{ opacity: 1, y: 0 }}
                              transition={{ delay: 0.5 }}
                            >
                              {(() => {
                                let costLine = (dest.budget_guidance?.trip_ballpark_usd_pp ?? "").trim();
                                if (!costLine) costLine = (dest.cost ?? "").trim();
                                if (/^tbd\s*\(placeholder\)$/i.test(costLine) || /^tbd$/i.test(costLine)) costLine = "";
                                const hasCost = hasMeaningfulEstimateText(costLine);
                                return (
                                  <>
                                    <span className="block text-[12px] text-white/40">
                                      {dest.pinned ? "Total Cost (2 pax)" : "Estimated Range (2 pax)"}
                                    </span>
                                    {hasCost ? (
                                      <p className="mt-1.5 text-[12px] sm:text-[13px] leading-snug text-white/85 break-words">
                                        {costLine}
                                      </p>
                                    ) : (
                                      <div className="mt-1.5 min-h-[2.25rem]" aria-label="Trip total estimate pending" />
                                    )}
                                    {dest.pinned && (
                                      <p className="text-[10px] text-indigo-300/50 mt-2">Includes all flights, hotels & activities</p>
                                    )}
                                  </>
                                );
                              })()}
                            </motion.div>
                          </>
                        )}

                        {destHasPersistentBookingSummary(dest) ? (
                          <button
                            type="button"
                            className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl border border-amber-500/35 bg-amber-500/10 py-3 text-[13px] font-medium text-amber-100/95 transition-colors hover:bg-amber-500/20 focus-visible:outline focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0a0a0f]"
                            onClick={refreshBookingSummary}
                          >
                            <RotateCcw className="h-4 w-4 shrink-0" aria-hidden />
                            Reset booking
                          </button>
                        ) : showReadyToBookCta ? (
                          <button
                            type="button"
                            className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 py-3 text-[13px] font-medium text-white transition-colors hover:from-indigo-600 hover:to-purple-700 focus-visible:outline focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0a0a0f]"
                            onClick={() => {
                              clearBookingPipelineCompletedFor(dest.id);
                              liveBookingPipelineStartedRef.current = true;
                              setDestinations((prev) =>
                                prev.map((d) => (d.id === dest.id ? { ...d, readyToBook: true } : d)),
                              );
                              addResponse({
                                type: "message",
                                role: "agent",
                                text: `🔍 Searching for the best flight and hotel options for ${dest.name}...`,
                              });
                              setBookingFace(true);
                            }}
                          >
                            <Plane className="h-4 w-4 shrink-0" aria-hidden />
                            Ready to Book
                          </button>
                        ) : null}
                      </div>
                    </div>
                  </div>

                  {/* Booking face — mount timeline only when flipped open so search runs after “Ready to Book”, not on expand */}
                  <div
                    className="col-start-1 row-start-1 w-full min-w-0 [backface-visibility:hidden]"
                    style={{
                      WebkitBackfaceVisibility: "hidden",
                      transform: "rotateY(180deg) translateZ(0)",
                      pointerEvents: bookingFace ? "auto" : "none",
                    }}
                  >
                    {bookingFace ? (
                      <BookingTimelineFace
                        key={`booking-${dest.id}`}
                        dest={dest}
                        sessionId={sessionId}
                        profile={profile}
                        useCachedResults={useCachedBookingTimeline}
                        dateHints={computeBookingDateHints(profile, dest.itinerary?.length ?? 7)}
                        onSearchFinished={onBookingFirstLoad}
                      />
                    ) : (
                      <div className="min-h-[200px]" aria-hidden />
                    )}
                  </div>
                </motion.div>
              </div>
            </div>

            {/* Action hint — hide when conversation has expanded beyond minimized (per UX spec). */}
            {conversationMode === "minimized" && (
              <div className="mt-4 flex items-center gap-2 text-[12px] text-white/25">
                <Zap className="w-3 h-3" />
                <span>Say "let's do {dest.name}" to lock this in, or "no {dest.name}" to remove it</span>
              </div>
            )}
          </motion.div>
        </div>
          </div>
          {conversationMode !== "panel" && (
            <ConversationPanel
              destId={dest.id}
              destName={dest.name}
              mode={conversationMode}
              onModeChange={setConversationMode}
            />
          )}
        </div>

        <AnimatePresence mode="popLayout">
          {conversationMode === "panel" && (
            <motion.div
              key={`conv-panel-${dest.id}`}
              initial={{ opacity: 0, x: 24 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 24 }}
              transition={{ type: "spring", damping: 28, stiffness: 320 }}
              className="hidden lg:flex w-[40%] shrink-0 min-h-0 flex-col"
            >
              <ConversationPanel
                destId={dest.id}
                destName={dest.name}
                mode={conversationMode}
                onModeChange={setConversationMode}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Pinned indicator */}
      {dest.pinned && (
        <div className="absolute top-0 left-0 w-full h-0.5 bg-gradient-to-r from-indigo-500 to-purple-500" />
      )}
    </motion.div>
  );
}

/* ── Grid card (collapsed view) ── */
function GridCard({ dest, expandLocked }: { dest: DestCard; expandLocked: boolean }) {
  const { setDestinations, setExpandedDest, addResponse } = useTrip();
  const { src: heroSrc, count: heroCount, index: heroIndex } = useRotatingHeroSrc(dest, HERO_SLIDE_MS);

  const dismiss = (e: React.MouseEvent) => {
    e.stopPropagation();
    setDestinations((prev: DestCard[]) => prev.map(d => d.id === dest.id ? { ...d, dismissed: true } : d));
  };

  const togglePin = (e: React.MouseEvent) => {
    e.stopPropagation();
    const willPin = !dest.pinned;
    setDestinations((prev: DestCard[]) => prev.map(d => d.id === dest.id ? { ...d, pinned: !d.pinned } : d));
    
    // Show feedback about what pinning does
    if (willPin) {
      addResponse({
        type: "success",
        text: `📌 ${dest.name} is now bookmarked for comparison. Click 'Ready to Book' when you want to see detailed flight and hotel options.`,
        relatedTo: dest.id,
      });
    }
  };

  const pinTooltip = dest.pinned
    ? "Unpin this destination (remove the comparison highlight)"
    : "Pin this destination to keep it highlighted while you compare";
  const dismissTooltip = "Remove this destination from your trip (discards this card)";

  return (
    <motion.div
      layout
      data-testid="destination-grid-card"
      data-destination-id={dest.id}
      initial={{ opacity: 0, y: 30, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.9, y: -20 }}
      transition={{ type: "spring", damping: 25, stiffness: 200 }}
      title={
        expandLocked
          ? "Trip planner is updating your shortlist—try again when the status bar is idle."
          : dest.itinerary_dirty
            ? "Day-by-day may still be rebuilding in the background; you can open this card anytime."
            : undefined
      }
      className={`relative rounded-2xl overflow-hidden border transition-all group ${
        expandLocked
          ? "cursor-not-allowed opacity-60 border-white/[0.08] bg-white/[0.03]"
          : `cursor-pointer ${
              dest.pinned
                ? "border-indigo-500/40 bg-indigo-500/[0.05]"
                : "border-white/[0.08] bg-white/[0.03] hover:border-white/[0.15]"
            }`
      }`}
      onClick={() => {
        if (expandLocked) return;
        setExpandedDest(dest.id);
      }}
    >
      {/* Warning badge */}
      {dest.warnings && dest.warnings.length > 0 && (
        <div className="absolute top-2.5 right-12 z-10">
          <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-amber-500/20 border border-amber-500/30 backdrop-blur-sm">
            <AlertTriangle className="w-3 h-3 text-amber-400" />
            <span className="text-[10px] text-amber-300">Issue</span>
          </div>
        </div>
      )}

      {dest.itinerary_dirty && !expandLocked ? (
        <div className="absolute bottom-2 left-2 z-10 max-w-[85%]">
          <div className="flex items-center gap-1 px-2 py-1 rounded-md bg-indigo-500/15 border border-indigo-400/25 backdrop-blur-sm">
            <Loader2 className="w-3 h-3 shrink-0 animate-spin text-indigo-300/90" aria-hidden />
            <span className="text-[9px] text-indigo-200/80 leading-tight">Plan updating…</span>
          </div>
        </div>
      ) : null}

      {/* Image */}
      <div className="relative h-36 overflow-hidden sm:h-40">
        <ImageWithFallback src={heroSrc} alt={dest.name} className="w-full h-full object-cover" />
        <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-black/20" />
        {heroCount > 1 && (
          <div className="absolute bottom-12 left-1/2 -translate-x-1/2 flex gap-1">
            {Array.from({ length: heroCount }).map((_, i) => (
              <span
                key={i}
                className={`h-1 rounded-full transition-all ${i === heroIndex ? "w-2 bg-white/90" : "w-1 bg-white/40"}`}
              />
            ))}
          </div>
        )}

        {/* Top controls */}
        <div className="absolute top-2.5 right-2.5 flex gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            type="button"
            title={pinTooltip}
            aria-label={pinTooltip}
            onClick={togglePin}
            className={`w-7 h-7 rounded-lg flex items-center justify-center transition-all ${dest.pinned ? "bg-indigo-500 text-white" : "bg-black/40 text-white/60 hover:text-white backdrop-blur-sm"}`}
          >
            {dest.pinned ? <PinOff className="w-3.5 h-3.5" /> : <Pin className="w-3.5 h-3.5" />}
          </button>
          <button
            type="button"
            title={dismissTooltip}
            aria-label={dismissTooltip}
            onClick={dismiss}
            className="w-7 h-7 rounded-lg bg-black/40 text-white/60 hover:text-white flex items-center justify-center backdrop-blur-sm"
          >
            <Trash2 className="w-3.5 h-3.5" aria-hidden />
          </button>
        </div>

        {/* Score */}
        <div className="absolute top-2.5 left-2.5">
          <ScoreBadge score={dest.score} />
        </div>

        {/* Name overlay */}
        <div className="absolute bottom-3 left-3 right-3">
          <h3 data-testid="destination-name" className="text-[16px] text-white sm:text-[18px]">{dest.name}</h3>
          <p className="text-[11px] text-white/50 sm:text-[12px]">{dest.country}</p>
        </div>
      </div>

      {/* Content */}
      <div className="p-3 sm:p-4">
        <p className="text-[12px] leading-relaxed text-white/50 sm:text-[13px]">{renderHookWithBold(dest.hook)}</p>

        {/* Quick stats */}
        <div className="flex items-center gap-3 mt-3 text-[11px] text-white/30">
          <span className="flex items-center gap-1"><Thermometer className="w-3 h-3" />{dest.temp}</span>
          <span className="flex items-center gap-1"><Plane className="w-3 h-3" />{dest.flight}</span>
          <span className="flex items-center gap-1"><DollarSign className="w-3 h-3" />{estimateOrDash(dest.cost)}</span>
        </div>
        {dest.weather_summary && (
          <p className="text-[10px] text-sky-300/80 mt-2 leading-snug">{dest.weather_summary}</p>
        )}
        {dest.commerce?.disclaimer && (
          <p className="text-[9px] text-white/25 mt-1">{dest.commerce.disclaimer}</p>
        )}

        {/* Vibes */}
        <div className="flex flex-wrap gap-1 mt-3">
          {(dest.vibe ?? []).map((v) => (
            <span key={v} className={`px-2 py-0.5 rounded-md text-[10px] ${VIBE_COLORS[v] || "bg-white/5 text-white/40"}`}>
              {v}
            </span>
          ))}
        </div>

        {/* Warning inline */}
        {dest.warnings && dest.warnings.length > 0 && (
          <div className="mt-3 px-2.5 py-2 rounded-lg bg-amber-500/[0.06] border border-amber-500/20">
            <p className="text-[11px] text-amber-300/70 leading-relaxed">{dest.warnings[0]}</p>
          </div>
        )}

        {/* Expand hint */}
        <div className="mt-3 flex items-center gap-1 text-[11px] text-white/20">
          <span>{expandLocked ? "Wait for planner—then tap to open" : "Tap to see full plan"}</span>
          <ChevronRight className="w-3 h-3" />
        </div>
      </div>

      {/* Pinned indicator */}
      {dest.pinned && (
        <div className="absolute top-0 left-0 w-full h-0.5 bg-gradient-to-r from-indigo-500 to-purple-500" />
      )}
    </motion.div>
  );
}

/* ── Main Canvas ── */
function CollapsedDestinationStrip({
  destinations,
  onExpand,
}: {
  destinations: DestCard[];
  onExpand: () => void;
}) {
  const { setExpandedDest, setIsCanvasCollapsed } = useTrip();

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      className="mb-4"
    >
      <div className="flex flex-wrap items-center gap-3">
        {destinations.map((dest) => (
          <motion.button
            key={dest.id}
            layout
            onClick={() => {
              setIsCanvasCollapsed(false);
              setExpandedDest(dest.id);
            }}
            className={`group flex items-center gap-2 rounded-xl px-3 py-2 transition-all ${
              dest.pinned
                ? "border border-indigo-500/40 bg-indigo-500/20 text-white/90"
                : "border border-white/[0.08] bg-white/[0.05] text-white/60 hover:bg-white/[0.08] hover:text-white/80"
            }`}
          >
            <div className="h-6 w-6 shrink-0 overflow-hidden rounded-md">
              <ImageWithFallback src={dest.image} alt={dest.name} className="h-full w-full object-cover" />
            </div>
            <span className="text-[13px] font-medium">{dest.name}</span>
            <span className={`text-[11px] ${dest.pinned ? "text-indigo-300" : "text-white/30"}`}>{dest.score}%</span>
            {dest.pinned ? <Pin className="h-3 w-3 text-indigo-400" /> : null}
            {dest.warnings && dest.warnings.length > 0 ? <AlertTriangle className="h-3 w-3 text-amber-400" /> : null}
          </motion.button>
        ))}
        <button
          onClick={onExpand}
          className="flex items-center gap-1.5 rounded-xl border border-white/[0.06] bg-white/[0.03] px-3 py-2 text-white/40 transition-all hover:bg-white/[0.05] hover:text-white/60"
        >
          <ChevronDown className="h-3.5 w-3.5" />
          <span className="text-[12px]">Show cards</span>
        </button>
      </div>
    </motion.div>
  );
}

/* ── Main Canvas ── */
export function DestinationCanvas() {
  const {
    destinations,
    expandedDest,
    setExpandedDest,
    isCanvasCollapsed,
    setIsCanvasCollapsed,
    sessionId,
    profile,
    setDestinations,
    phase,
    refineUnlockCardNavigation,
  } = useTrip();
  const [comparisonStartIndex, setComparisonStartIndex] = useState(0);
  const syncTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /**
   * Lock switching cards during smoke/stream, or refine unless orchestrator allows browsing (**refineUnlockCardNavigation**).
   */
  const expandInteractionsLocked = useMemo(
    () =>
      phase === "thinking" ||
      phase === "streaming" ||
      (phase === "refining" && !refineUnlockCardNavigation),
    [phase, refineUnlockCardNavigation],
  );

  useEffect(() => {
    if (!sessionId || destinations.length === 0) return;
    if (syncTimer.current) clearTimeout(syncTimer.current);
    syncTimer.current = setTimeout(() => {
      void syncUi(sessionId, destinations).catch(() => {});
    }, 450);
    return () => {
      if (syncTimer.current) clearTimeout(syncTimer.current);
    };
  }, [destinations, sessionId]);

  const profileSig = useMemo(() => JSON.stringify(profile.map((p) => [p.key, p.value])), [profile]);

  useEffect(() => {
    if (!sessionId || destinations.length === 0) return;
    const t = window.setTimeout(() => {
      scheduleCardsItineraryPrefetch({
        sessionId,
        cards: destinations,
        profile,
        setDestinations,
        excludeDestinationId: expandedDest ?? null,
        delayMs: 0,
      });
    }, 650);
    return () => clearTimeout(t);
  }, [sessionId, destinations, expandedDest, profileSig, profile, setDestinations]);

  // Reset pagination when expanded card changes - MUST be before early returns
  useEffect(() => {
    setComparisonStartIndex(0);
  }, [expandedDest]);
  
  const visible = destinations.filter(d => !d.dismissed);
  const sorted = [...visible].sort((a, b) => {
    if (a.pinned && !b.pinned) return -1;
    if (!a.pinned && b.pinned) return 1;
    return b.score - a.score;
  });

  if (visible.length === 0) return null;

  const expandedCard = sorted.find(d => d.id === expandedDest);
  const otherCards = sorted.filter(d => d.id !== expandedDest);
  
  // Paginate comparison cards: show 3 at a time
  const CARDS_PER_PAGE = 3;
  const visibleComparisonCards = otherCards.slice(comparisonStartIndex, comparisonStartIndex + CARDS_PER_PAGE);
  const hasMoreAbove = comparisonStartIndex > 0;
  const hasMoreBelow = comparisonStartIndex + CARDS_PER_PAGE < otherCards.length;

  const scrollComparisonUp = () => {
    if (hasMoreAbove) {
      setComparisonStartIndex(Math.max(0, comparisonStartIndex - CARDS_PER_PAGE));
    }
  };

  const scrollComparisonDown = () => {
    if (hasMoreBelow) {
      setComparisonStartIndex(Math.min(otherCards.length - CARDS_PER_PAGE, comparisonStartIndex + CARDS_PER_PAGE));
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className={expandedDest ? "flex h-full min-h-0 flex-col" : "mt-0 sm:mt-1"}
    >
      {isCanvasCollapsed && !expandedDest ? (
        <CollapsedDestinationStrip destinations={sorted} onExpand={() => setIsCanvasCollapsed(false)} />
      ) : !expandedDest ? (
        <div className="mb-4 flex items-center justify-between">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
            <h2 className="text-[16px] text-white/80">Destinations</h2>
            <span className="text-[11px] text-white/20">
              {visible.length} options · {visible.filter((d) => d.pinned).length} pinned
            </span>
            {expandInteractionsLocked && (
              <span className="max-w-[min(100%,28rem)] text-[11px] text-amber-300/75 sm:ml-auto">
                Planner busy—destination cards are paused from opening so runs do not overlap.
              </span>
            )}
          </div>
        </div>
      ) : null}

      {!isCanvasCollapsed && expandedDest && expandedCard ? (
        /* ── Expanded: main card + comparison sidebar ── */
        <div className="flex gap-4 flex-1 min-h-0">
          {/* Main expanded card */}
          <div className="flex-1 min-w-0 h-full">
            <AnimatePresence mode="wait">
              <ExpandedCard key={expandedCard.id} dest={expandedCard} />
            </AnimatePresence>
          </div>

          {/* Comparison sidebar with navigation */}
          {otherCards.length > 0 && (
            <motion.div
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              className="w-56 shrink-0 flex flex-col"
            >
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div className="w-1 h-1 rounded-full bg-white/20" />
                  <span className="text-[10px] text-white/25 uppercase tracking-widest">Compare</span>
                </div>
                {otherCards.length > CARDS_PER_PAGE && (
                  <span className="text-[9px] text-white/20">
                    {comparisonStartIndex + 1}-{Math.min(comparisonStartIndex + CARDS_PER_PAGE, otherCards.length)} of {otherCards.length}
                  </span>
                )}
              </div>

              {/* Up arrow */}
              {hasMoreAbove && (
                <button
                  onClick={scrollComparisonUp}
                  className="w-full py-2 mb-2 rounded-lg bg-white/[0.05] border border-white/[0.08] flex items-center justify-center text-white/40 hover:text-white/70 hover:bg-white/[0.08] transition-all group"
                >
                  <ChevronLeft className="w-3.5 h-3.5 rotate-90" />
                  <span className="text-[10px] ml-1">More above</span>
                </button>
              )}

              {/* Visible comparison cards */}
              <div className="flex flex-col gap-3 flex-1 min-h-0">
                <AnimatePresence>
                  {visibleComparisonCards.map(d => (
                    <ComparisonMiniCard
                      key={d.id}
                      dest={d}
                      expandLocked={expandInteractionsLocked}
                      onExpand={() => setExpandedDest(d.id)}
                    />
                  ))}
                </AnimatePresence>
              </div>

              {/* Down arrow */}
              {hasMoreBelow && (
                <button
                  onClick={scrollComparisonDown}
                  className="w-full py-2 mt-2 rounded-lg bg-white/[0.05] border border-white/[0.08] flex items-center justify-center text-white/40 hover:text-white/70 hover:bg-white/[0.08] transition-all group"
                >
                  <span className="text-[10px] mr-1">More below</span>
                  <ChevronLeft className="w-3.5 h-3.5 -rotate-90" />
                </button>
              )}
            </motion.div>
          )}
        </div>
      ) : !isCanvasCollapsed ? (
        /* ── Horizontal Carousel view — card width scales with viewport (narrow phones). ── */
        <div className="relative -mx-3 px-3 sm:-mx-6 sm:px-6">
          <div className="overflow-x-auto overflow-y-visible pb-3 scrollbar-thin sm:pb-4">
            <div className="flex gap-3 sm:gap-4" style={{ minWidth: "min-content" }}>
              <AnimatePresence>
                {sorted.map(d => (
                  <div key={d.id} className="w-[clamp(220px,min(76vw,340px),340px)] flex-shrink-0">
                    <GridCard dest={d} expandLocked={expandInteractionsLocked} />
                  </div>
                ))}
              </AnimatePresence>
            </div>
          </div>
          
          {/* Scroll hint gradient on right edge */}
          {visible.length > 2 && (
            <div className="absolute right-0 top-0 bottom-4 w-20 bg-gradient-to-l from-[#0a0a0f] to-transparent pointer-events-none" />
          )}
        </div>
      ) : null}
    </motion.div>
  );
}