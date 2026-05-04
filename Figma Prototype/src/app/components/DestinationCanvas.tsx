import { useState, useMemo, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  X, Pin, PinOff, ChevronRight, Plane, Clock, Thermometer,
  DollarSign, Star, Hotel, Zap, AlertTriangle, ChevronLeft, ArrowRight,
  MapPin, Calendar, CheckCircle2, Loader2,
} from "lucide-react";
import { ImageWithFallback } from "./figma/ImageWithFallback";
import { useTrip } from "./TripStore";
import type { DestCard } from "./TripStore";
import { syncUi, buildItinerary, buildItineraryStream, planReview } from "../../api/wanderly";
import type { WanderlyProfilePillDTO } from "../../types/workspace";
import { logItin } from "../lib/itineraryDebug";
import { ConversationPanel } from "./ConversationPanel";

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
  
  if (!dest.itinerary) return null;

  // Parse trip dates from profile
  const tripDates = useMemo(() => {
    const whenPill = profile.find(p => p.key === "when");
    if (!whenPill) return null;
    
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
    endDate.setDate(endDate.getDate() + (dest.itinerary?.length || 7) - 1);
    
    return { 
      startDate, 
      endDate,
      month, 
      startDay: parseInt(startDay), 
      endDay: endDate.getDate(),
      displayMonth: month,
    };
  }, [profile, dest.itinerary]);

  // Generate calendar dates for each day
  const daysWithDates = useMemo(() => {
    if (!tripDates) return dest.itinerary;
    
    return dest.itinerary?.map((day) => {
      const date = new Date(tripDates.startDate);
      date.setDate(date.getDate() + (day.day - 1));
      
      return {
        ...day,
        date: date.getDate(),
        month: date.toLocaleString('en', { month: 'short' }),
      };
    });
  }, [dest.itinerary, tripDates]);

  return (
    <div className="mt-4">
      <div className="flex items-center justify-between mb-3">
        <p className="text-[11px] text-white/30 uppercase tracking-widest">
          {dest.itinerary.length}-Day Plan
          {tripDates && (
            <span className="ml-2 text-white/20">
              {tripDates.displayMonth} {tripDates.startDay}–{tripDates.endDay}
            </span>
          )}
        </p>
      </div>

      <div className="space-y-1">
        {daysWithDates?.map((day) => (
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
              <p className="text-[13px] text-white/70">{day.label}</p>
              <div className="flex flex-wrap gap-1 mt-1">
                {day.items.slice(0, 2).map((item, i) => (
                  <span key={i} className="text-[11px] text-white/30">{item}{i < 1 && day.items.length > 1 ? " ·" : ""}</span>
                ))}
                {day.items.length > 2 && (
                  <span className="text-[11px] text-white/20">+{day.items.length - 2} more</span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function FlightStrip({ dest, currentIndex, onPrev, onNext }: { 
  dest: DestCard; 
  currentIndex: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  if (!dest.flights || dest.flights.length === 0) return null;
  
  const flight = dest.flights[currentIndex];
  const showActual = dest.pinned;
  
  return (
    <motion.div 
      className="mt-4"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: showActual ? 0.6 : 0.3 }}
    >
      <div className="flex items-center justify-between mb-2">
        <p className="text-[11px] text-white/30 uppercase tracking-widest">
          {showActual ? "Flight" : "Flight Options"}
        </p>
        {!showActual && (
          <span className="text-[10px] text-white/20">Researching...</span>
        )}
      </div>
      
      <div className="relative group">
        {showActual ? (
          // Actual flight with details
          <motion.div 
            className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-white/[0.04] border border-white/[0.06]"
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.7 }}
          >
            <Plane className="w-4 h-4 text-blue-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-[13px] text-white/70">{flight.airline}</p>
              <p className="text-[11px] text-white/30">{flight.route} · {flight.duration}</p>
            </div>
            <span className="text-[14px] text-white/80">{flight.price}</span>
            {flight.best && <Star className="w-3.5 h-3.5 text-amber-400" />}
          </motion.div>
        ) : (
          // Generic placeholder while researching
          <div className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-white/[0.04] border border-white/[0.06] border-dashed">
            <Plane className="w-4 h-4 text-white/20 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-[13px] text-white/40">Multiple airlines available</p>
              <p className="text-[11px] text-white/20">Searching best routes & times</p>
            </div>
            <span className="text-[14px] text-white/50">$950-$1,200</span>
          </div>
        )}

        {/* Navigation controls - only show when pinned and multiple options */}
        {showActual && dest.flights.length > 1 && (
          <>
            <button
              onClick={onPrev}
              className="absolute left-0 top-1/2 -translate-y-1/2 -translate-x-2 w-6 h-6 rounded-full bg-white/10 backdrop-blur-sm flex items-center justify-center text-white/40 hover:text-white hover:bg-white/20 transition-all"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={onNext}
              className="absolute right-0 top-1/2 -translate-y-1/2 translate-x-2 w-6 h-6 rounded-full bg-white/10 backdrop-blur-sm flex items-center justify-center text-white/40 hover:text-white hover:bg-white/20 transition-all"
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
            <div className="absolute -bottom-5 left-1/2 -translate-x-1/2 text-[10px] text-white/30">
              {currentIndex + 1} of {dest.flights.length} options
            </div>
          </>
        )}
      </div>
    </motion.div>
  );
}

function HotelStrip({ dest, currentIndex, onPrev, onNext }: { 
  dest: DestCard;
  currentIndex: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  if (!dest.hotels || dest.hotels.length === 0) return null;
  
  const hotel = dest.hotels[currentIndex];
  const showActual = dest.pinned;
  
  return (
    <motion.div 
      className="mt-4"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: showActual ? 0.8 : 0.4 }}
    >
      <div className="flex items-center justify-between mb-2">
        <p className="text-[11px] text-white/30 uppercase tracking-widest">
          {showActual ? "Stay" : "Stay Options"}
        </p>
        {!showActual && (
          <span className="text-[10px] text-white/20">Searching...</span>
        )}
      </div>
      
      <div className="relative group">
        {showActual ? (
          // Actual hotel with details
          <motion.div 
            className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-white/[0.04] border border-white/[0.06]"
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.9 }}
          >
            <div className="w-10 h-10 rounded-lg overflow-hidden shrink-0">
              <ImageWithFallback src={hotel.image} alt={hotel.name} className="w-full h-full object-cover" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[13px] text-white/70">{hotel.name}</p>
              <p className="text-[11px] text-white/30">{hotel.area} · {hotel.nights} nights</p>
            </div>
            <span className="text-[13px] text-white/60">{hotel.price}</span>
            {hotel.best && <Star className="w-3.5 h-3.5 text-amber-400" />}
          </motion.div>
        ) : (
          // Generic placeholder while searching
          <div className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-white/[0.04] border border-white/[0.06] border-dashed">
            <div className="w-10 h-10 rounded-lg bg-white/[0.06] shrink-0 flex items-center justify-center">
              <Hotel className="w-4 h-4 text-white/20" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[13px] text-white/40">Various accommodations</p>
              <p className="text-[11px] text-white/20">Comparing hotels & areas</p>
            </div>
            <span className="text-[13px] text-white/50">$90-$180/n</span>
          </div>
        )}

        {/* Navigation controls - only show when pinned and multiple options */}
        {showActual && dest.hotels.length > 1 && (
          <>
            <button
              onClick={onPrev}
              className="absolute left-0 top-1/2 -translate-y-1/2 -translate-x-2 w-6 h-6 rounded-full bg-white/10 backdrop-blur-sm flex items-center justify-center text-white/40 hover:text-white hover:bg-white/20 transition-all"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={onNext}
              className="absolute right-0 top-1/2 -translate-y-1/2 translate-x-2 w-6 h-6 rounded-full bg-white/10 backdrop-blur-sm flex items-center justify-center text-white/40 hover:text-white hover:bg-white/20 transition-all"
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
            <div className="absolute -bottom-5 left-1/2 -translate-x-1/2 text-[10px] text-white/30">
              {currentIndex + 1} of {dest.hotels.length} options
            </div>
          </>
        )}
      </div>
    </motion.div>
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

/* ── Comparison Mini Card (shown in sidebar when another card is expanded) ── */
function ComparisonMiniCard({ dest, onExpand }: { dest: DestCard; onExpand: () => void }) {
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
      onClick={onExpand}
      className={`relative rounded-xl overflow-hidden border cursor-pointer transition-all group hover:border-white/[0.15] ${
        dest.pinned ? "border-indigo-500/30 bg-indigo-500/[0.04]" : "border-white/[0.06] bg-white/[0.02]"
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

      {/* Quick stats row */}
      <div className="px-2.5 py-2">
        <div className="grid grid-cols-3 gap-1 text-[10px]">
          <div className="flex flex-col items-center px-1.5 py-1.5 rounded-lg bg-white/[0.03]">
            <Plane className="w-3 h-3 text-blue-400 mb-0.5" />
            <span className="text-white/50">{dest.flight}</span>
            <span className="text-white/25">{bestFlight?.price || "—"}</span>
          </div>
          <div className="flex flex-col items-center px-1.5 py-1.5 rounded-lg bg-white/[0.03]">
            <Hotel className="w-3 h-3 text-purple-400 mb-0.5" />
            <span className="text-white/50">{bestHotel?.price || "—"}</span>
            <span className="text-white/25">{bestHotel?.nights || "—"}n</span>
          </div>
          <div className="flex flex-col items-center px-1.5 py-1.5 rounded-lg bg-white/[0.03]">
            <DollarSign className="w-3 h-3 text-emerald-400 mb-0.5" />
            <span className="text-white/50">{dest.cost}</span>
            <span className="text-white/25">total</span>
          </div>
        </div>

        {/* Vibes - compact */}
        <div className="flex flex-wrap gap-1 mt-2">
          {dest.vibe.slice(0, 3).map(v => (
            <span key={v} className={`px-1.5 py-0.5 rounded text-[9px] ${VIBE_COLORS[v] || "bg-white/5 text-white/40"}`}>
              {v}
            </span>
          ))}
          {dest.vibe.length > 3 && <span className="text-[9px] text-white/20">+{dest.vibe.length - 3}</span>}
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
  const { setDestinations, setExpandedDest, addResponse, sessionId, profile } = useTrip();
  const [currentImageIndex, setCurrentImageIndex] = useState(0);
  const [currentFlightIndex, setCurrentFlightIndex] = useState(0);
  const [currentHotelIndex, setCurrentHotelIndex] = useState(0);
  const [conversationMode, setConversationMode] = useState<"minimized" | "footer" | "panel">("minimized");
  const [itinLoading, setItinLoading] = useState(false);
  const [itinError, setItinError] = useState(false);
  const [itinThought, setItinThought] = useState("");

  useEffect(() => {
    setItinThought("");
    setItinError(false);
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
    setItinError(false);
    setItinThought("");
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
      void buildItineraryStream(
        sessionId,
        dest as unknown as Record<string, unknown>,
        profile,
        (ev) => {
          if (ac.signal.aborted) return;
          if (ev.type === "phase" && typeof ev.message === "string") {
            const line = ev.message.trim();
            if (line) setItinThought((t) => `${(t + "\n\n" + line).trim()}\n`.slice(-14000));
          }
          if (ev.type === "thinking_delta" && typeof ev.delta === "string") {
            setItinThought((t) => (t + ev.delta).slice(-14000));
          }
        },
        { signal: ac.signal, requestId },
      )
        .then((res) => {
          if (ac.signal.aborted) {
            logItin("expanded_fetch_aborted_after_resolve", { requestId, destId: dest.id });
            return;
          }
          const rows = res.itinerary || [];
          logItin("expanded_fetch_done", {
            requestId,
            destId: dest.id,
            rowCount: rows.length,
            destMatch: res.destination_id === dest.id,
          });
          if (rows.length === 0) {
            setItinError(true);
            setDestinations((prev) =>
              prev.map((d) => (d.id === dest.id ? { ...d, itinerary_dirty: false } : d)),
            );
            return;
          }
          setDestinations((prev) => {
            const next = prev.map((d) =>
              d.id === dest.id ? { ...d, itinerary: rows, itinerary_dirty: false } : d,
            );
            queueMicrotask(() => {
              void syncUi(sessionId, next as unknown[]).catch(() => {});
              void planReview(
                sessionId,
                profile as unknown as WanderlyProfilePillDTO[],
                next as unknown as Record<string, unknown>[],
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
            });
            return next;
          });
        })
        .catch((err: unknown) => {
          if (ac.signal.aborted) {
            logItin("expanded_fetch_abort_error", { requestId, destId: dest.id, name: (err as Error)?.name });
            return;
          }
          logItin("expanded_fetch_error", { requestId, destId: dest.id, message: String(err) });
          setItinError(true);
        })
        .finally(() => {
          if (!ac.signal.aborted) setItinLoading(false);
        });
    }, debounceMs);
    return () => {
      window.clearTimeout(startTimer);
      logItin("expanded_effect_cleanup_abort", { requestId, destId: dest.id });
      ac.abort();
    };
    // Keep deps minimal so a long-running build is not aborted by redundant identity churn
    // (e.g. ``profile`` ref vs stable ``profileSig``).
  }, [sessionId, dest.id, needsItinFetch, profileSig, addResponse, setDestinations]);

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
        text: `📌 ${dest.name} is now pinned. When you adjust your travel profile, pinned destinations stay in your workspace while unpinned ones may be swapped out.`,
        relatedTo: dest.id
      });
    }
  };

  // Extract location flow for trip visualization
  const locationFlow = useMemo(() => {
    if (!dest.itinerary) return null;
    
    const locations: Array<{ name: string; isTransfer?: boolean }> = [{ name: "Home" }];
    
    dest.itinerary.forEach((day) => {
      // Look for location indicators in the label
      if (day.label.includes("→")) {
        const parts = day.label.split("→").map(p => p.trim());
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
          <div className="absolute top-2.5 right-2.5 flex gap-1.5">
            <button onClick={togglePin} className={`w-7 h-7 rounded-lg flex items-center justify-center transition-all ${dest.pinned ? "bg-indigo-500 text-white" : "bg-black/40 text-white/60 hover:text-white backdrop-blur-sm"}`}>
              {dest.pinned ? <PinOff className="w-3.5 h-3.5" /> : <Pin className="w-3.5 h-3.5" />}
            </button>
            <button onClick={dismiss} className="w-7 h-7 rounded-lg bg-black/40 text-white/60 hover:text-white flex items-center justify-center backdrop-blur-sm">
              <X className="w-3.5 h-3.5" />
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
                <button
                  type="button"
                  onClick={togglePin}
                  className={`w-8 h-8 rounded-lg flex items-center justify-center transition-all ${
                    dest.pinned ? "bg-indigo-500 text-white" : "bg-white/[0.06] text-white/60 hover:text-white border border-white/[0.08]"
                  }`}
                  aria-label={dest.pinned ? "Unpin destination" : "Pin destination"}
                >
                  {dest.pinned ? <PinOff className="w-3.5 h-3.5" /> : <Pin className="w-3.5 h-3.5" />}
                </button>
                <button
                  type="button"
                  onClick={dismiss}
                  className="w-8 h-8 rounded-lg bg-white/[0.06] text-white/60 hover:text-white flex items-center justify-center border border-white/[0.08]"
                  aria-label="Remove destination"
                >
                  <X className="w-3.5 h-3.5" />
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
            <p className="text-[13px] text-white/50 leading-relaxed max-w-md">{dest.hook}</p>
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
            <span className="flex items-center gap-1"><DollarSign className="w-3 h-3" />{dest.cost}</span>
          </div>

          {/* Vibes */}
          <div className="flex flex-wrap gap-1 mt-3">
            {dest.vibe.map(v => (
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
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-6">
                <div>
                  {itinLoading ? (
                    <div className="mt-4 min-h-[120px] flex flex-col rounded-xl border border-white/[0.06] bg-white/[0.02]">
                      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-white/[0.05]">
                        <Loader2 className="w-4 h-4 animate-spin shrink-0 text-indigo-400/80" aria-hidden />
                        <span className="text-[12px] text-white/45">Building itinerary</span>
                      </div>
                      <div className="px-3 py-2">
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
                  ) : itinError ? (
                    <div className="mt-4 space-y-3">
                      <p className="text-[12px] text-amber-400/80">
                        Could not refresh the day-by-day plan (the model returned no usable days). Your previous
                        outline is unchanged. Try a shorter ask or check the planner service.
                      </p>
                      {dest.itinerary && dest.itinerary.length > 0 ? <MiniItinerary dest={dest} /> : null}
                    </div>
                  ) : (
                    <MiniItinerary dest={dest} />
                  )}
                  <ActivityStrip dest={dest} />
                </div>
                <div>
                  <FlightStrip 
                    dest={dest} 
                    currentIndex={currentFlightIndex}
                    onPrev={() => setCurrentFlightIndex((prev) => (prev - 1 + dest.flights!.length) % dest.flights!.length)}
                    onNext={() => setCurrentFlightIndex((prev) => (prev + 1) % dest.flights!.length)}
                  />
                  <HotelStrip 
                    dest={dest} 
                    currentIndex={currentHotelIndex}
                    onPrev={() => setCurrentHotelIndex((prev) => (prev - 1 + dest.hotels!.length) % dest.hotels!.length)}
                    onNext={() => setCurrentHotelIndex((prev) => (prev + 1) % dest.hotels!.length)}
                  />

                  {/* Total */}
                  <motion.div 
                    className="mt-6 px-4 py-3 rounded-xl bg-gradient-to-r from-indigo-500/10 to-purple-500/10 border border-indigo-500/20"
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.5 }}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-[12px] text-white/40">
                        {dest.pinned ? "Total Cost (2 pax)" : "Estimated Range (2 pax)"}
                      </span>
                      <span className="text-[18px] text-white/90">
                        {dest.pinned ? (
                          // Actual price when pinned
                          `$${dest.id === "bali" ? "4,850" : dest.id === "santorini" ? "5,600" : "3,800"}`
                        ) : (
                          // Range when unpinned  
                          "$4,200-$6,500"
                        )}
                      </span>
                    </div>
                    {dest.pinned && (
                      <p className="text-[10px] text-indigo-300/50 mt-1">Includes all flights, hotels & activities</p>
                    )}
                  </motion.div>
                </div>
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
function GridCard({ dest }: { dest: DestCard }) {
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
        text: `📌 ${dest.name} is now pinned. When you adjust your travel profile, pinned destinations stay in your workspace while unpinned ones may be swapped out.`,
        relatedTo: dest.id
      });
    }
  };

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 30, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.9, y: -20 }}
      transition={{ type: "spring", damping: 25, stiffness: 200 }}
      className={`relative rounded-2xl overflow-hidden border transition-all cursor-pointer group ${
        dest.pinned
          ? "border-indigo-500/40 bg-indigo-500/[0.05]"
          : "border-white/[0.08] bg-white/[0.03] hover:border-white/[0.15]"
      }`}
      onClick={() => setExpandedDest(dest.id)}
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

      {/* Image */}
      <div className="relative h-40 overflow-hidden">
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
          <button onClick={togglePin} className={`w-7 h-7 rounded-lg flex items-center justify-center transition-all ${dest.pinned ? "bg-indigo-500 text-white" : "bg-black/40 text-white/60 hover:text-white backdrop-blur-sm"}`}>
            {dest.pinned ? <PinOff className="w-3.5 h-3.5" /> : <Pin className="w-3.5 h-3.5" />}
          </button>
          <button onClick={dismiss} className="w-7 h-7 rounded-lg bg-black/40 text-white/60 hover:text-white flex items-center justify-center backdrop-blur-sm">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Score */}
        <div className="absolute top-2.5 left-2.5">
          <ScoreBadge score={dest.score} />
        </div>

        {/* Name overlay */}
        <div className="absolute bottom-3 left-3 right-3">
          <h3 className="text-white text-[18px]">{dest.name}</h3>
          <p className="text-white/50 text-[12px]">{dest.country}</p>
        </div>
      </div>

      {/* Content */}
      <div className="p-4">
        <p className="text-[13px] text-white/50 leading-relaxed">{dest.hook}</p>

        {/* Quick stats */}
        <div className="flex items-center gap-3 mt-3 text-[11px] text-white/30">
          <span className="flex items-center gap-1"><Thermometer className="w-3 h-3" />{dest.temp}</span>
          <span className="flex items-center gap-1"><Plane className="w-3 h-3" />{dest.flight}</span>
          <span className="flex items-center gap-1"><DollarSign className="w-3 h-3" />{dest.cost}</span>
        </div>
        {dest.weather_summary && (
          <p className="text-[10px] text-sky-300/80 mt-2 leading-snug">{dest.weather_summary}</p>
        )}
        {dest.commerce?.disclaimer && (
          <p className="text-[9px] text-white/25 mt-1">{dest.commerce.disclaimer}</p>
        )}

        {/* Vibes */}
        <div className="flex flex-wrap gap-1 mt-3">
          {dest.vibe.map(v => (
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
          <span>Tap to see full plan</span>
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
export function DestinationCanvas() {
  const { destinations, expandedDest, setExpandedDest, sessionId, profile, setDestinations } = useTrip();
  const [comparisonStartIndex, setComparisonStartIndex] = useState(0);
  const syncTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
    if (!sessionId) return;
    const targets = destinations.filter(
      (d) => d.pinned && d.itinerary_dirty === true && d.id !== expandedDest,
    );
    if (targets.length === 0) return;
    const ac = new AbortController();
    logItin("background_prefetch_scheduled", {
      sessionId: sessionId.slice(0, 12),
      delayMs: 800,
      targetIds: targets.map((x) => x.id),
      expandedDest: expandedDest ?? null,
    });
    const t = window.setTimeout(() => {
      void (async () => {
        for (const d of targets) {
          if (ac.signal.aborted) return;
          const requestId =
            typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
              ? `itin-bg-${crypto.randomUUID()}`
              : `itin-bg-${Date.now()}`;
          logItin("background_prefetch_start", { requestId, destId: d.id, expandedDest: expandedDest ?? null });
          try {
            const res = await buildItinerary(sessionId, d as unknown as Record<string, unknown>, profile, {
              signal: ac.signal,
              requestId,
            });
            if (ac.signal.aborted) return;
            const rows = res.itinerary || [];
            logItin("background_prefetch_done", { requestId, destId: d.id, rowCount: rows.length });
            setDestinations((prev) =>
              prev.map((x) => {
                if (x.id !== d.id) return x;
                if (rows.length === 0) return { ...x, itinerary_dirty: false };
                return { ...x, itinerary: rows, itinerary_dirty: false };
              }),
            );
          } catch (err: unknown) {
            if (ac.signal.aborted) {
              logItin("background_prefetch_aborted", { destId: d.id });
              return;
            }
            logItin("background_prefetch_error", { destId: d.id, message: String(err) });
            break;
          }
        }
      })();
    }, 800);
    return () => {
      clearTimeout(t);
      ac.abort();
    };
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
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className={expandedDest ? "h-full flex flex-col" : "mt-6"}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-white/80 text-[16px]">Destinations</h2>
          <span className="text-[11px] text-white/20">{visible.length} options · {visible.filter(d => d.pinned).length} pinned</span>
        </div>
      </div>

      {expandedDest && expandedCard ? (
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
      ) : (
        /* ── Horizontal Carousel view ── */
        <div className="relative -mx-6 px-6">
          <div className="overflow-x-auto overflow-y-visible pb-4 scrollbar-thin">
            <div className="flex gap-4" style={{ minWidth: 'min-content' }}>
              <AnimatePresence>
                {sorted.map(d => (
                  <div key={d.id} className="w-[340px] flex-shrink-0">
                    <GridCard dest={d} />
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
      )}
    </motion.div>
  );
}