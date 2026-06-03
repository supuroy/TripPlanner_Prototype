/**
 * Back-of-card logistics timeline: structured plan from itinerary + transport_plan
 * (no live OTA / prices — visual expansion of the breadcrumb outline).
 */
import { useMemo, useState, useEffect, useCallback, useRef, type ReactNode } from "react";
import {
  Plane,
  PlaneTakeoff,
  PlaneLanding,
  Hotel,
  Car,
  Ship,
  Train,
  Footprints,
  MapPin,
  Users,
  Calendar,
  Route,
  Search,
  Loader2,
  ChevronDown,
  ChevronUp,
  Tent,
} from "lucide-react";
import type { DestCard, ItinDay, ProfilePill, HotelOption, FlightOption } from "./TripStore";
import { useTrip } from "./TripStore";
import { cardTripDaysLabel, deriveWhenRangeFromItinerary } from "../lib/syncCardLogisticsFromItinerary";
import {
  bookingPaxFromProfile,
  runSingleHotelBookingSearch,
  runSingleFlightBookingSearch,
  DetailedHotelCard,
  DetailedFlightCard,
} from "./bookingExpandedContent";
import { hotelWithSelectedRank } from "../lib/hotelRankedPicks";
import { flightWithSelectedRank, resolveDisplayedFlight } from "../lib/flightRankedPicks";
import { syncUi } from "../../api/wanderly";
import {
  commercialFlightSegmentsFromDest,
  effectiveTransportPrimaryMode,
  transportPlanSummary,
  tripWideStaysFromDest,
  tripWideCarGroundHint,
} from "../lib/bookingFlightPlan";
import { applyBookingStreamEvent, type BookingProgressStep } from "../lib/bookingSearchProgress";
import type { BookingStreamDrafts } from "../lib/bookingStreamCaption";
import { BookingSearchProgressCard } from "./BookingSearchProgressStrip";
import type {
  DayLogisticsCampNight,
  DayLogisticsFlightSegment,
  DayLogisticsSurfaceLeg,
  DayLogisticsTrailSegment,
} from "../../types/workspace";
import {
  campKindLabel,
  formatCampNightSubtitle,
  formatTrailStatsLine,
  parseDayLabelEndpoints,
  resolveTrailStats,
} from "../lib/formatHikingLogistics";
import { ItineraryQaBanner } from "./ItineraryQaBanner";
import { AllTrailsOutdoorLinkCard } from "./AllTrailsOutdoorLinkCard";
import { NpsOutdoorPanel } from "./NpsOutdoorPanel";

function formatTransportMode(mode: string | undefined): string {
  const m = (mode || "").trim().toLowerCase().replace(/-/g, "_");
  if (m === "commercial_air") return "Commercial flights";
  if (m === "mixed") return "Mixed: flights + surface";
  if (m === "ground_only") return "Ground / surface only";
  return m ? m.replace(/_/g, " ") : "—";
}

function rolePill(role: string | undefined): { label: string; className: string } {
  const r = (role || "").toLowerCase();
  if (r === "inbound") return { label: "Inbound", className: "bg-sky-500/20 text-sky-200/90 border-sky-500/30" };
  if (r === "return") return { label: "Return", className: "bg-violet-500/20 text-violet-200/90 border-violet-500/30" };
  if (r === "within_trip")
    return { label: "Within trip", className: "bg-emerald-500/15 text-emerald-200/85 border-emerald-500/25" };
  return { label: "Flight", className: "bg-white/[0.06] text-white/50 border-white/[0.08]" };
}

function SurfaceIcon({ kind }: { kind: string }) {
  const k = (kind || "").toLowerCase();
  if (k === "ferry") return <Ship className="h-3.5 w-3.5 shrink-0 text-cyan-300/85" aria-hidden />;
  if (k === "train") return <Train className="h-3.5 w-3.5 shrink-0 text-violet-300/85" aria-hidden />;
  if (k === "walk") return <Footprints className="h-3.5 w-3.5 shrink-0 text-white/45" aria-hidden />;
  return <Car className="h-3.5 w-3.5 shrink-0 text-emerald-300/85" aria-hidden />;
}

/** Trip-wide stay row index aligns with ``dest.hotels`` after ``bookingSearchPrepare`` placeholders + merge. */
function persistedHotelPickForStayRow(dest: DestCard, stayRowIndex: number): HotelOption | undefined {
  const list = dest.hotels;
  if (!Array.isArray(list) || stayRowIndex < 0 || stayRowIndex >= list.length) return undefined;
  const h = list[stayRowIndex];
  if (!h || typeof h !== "object") return undefined;
  if (h.isPlaceholder === true) return undefined;
  const listing = String(h.listing_url || "").trim();
  if (listing) return h as HotelOption;
  if (h.isPlaceholder === false) return h as HotelOption;
  return undefined;
}

function persistedFlightPickForSegment(dest: DestCard, segmentIndex: number): FlightOption | undefined {
  const list = dest.flights;
  if (!Array.isArray(list) || segmentIndex < 0 || segmentIndex >= list.length) return undefined;
  const f = list[segmentIndex];
  if (!f || typeof f !== "object") return undefined;
  if (f.isPlaceholder === true) return undefined;
  const su = String(f.search_url || "").trim();
  if (su) return f as FlightOption;
  if (f.isPlaceholder === false) return f as FlightOption;
  return undefined;
}

function TripWideBookingPrepBlock({ dest, profile }: { dest: DestCard; profile: ProfilePill[] }) {
  const segs = useMemo(() => commercialFlightSegmentsFromDest(dest, profile), [dest, profile]);
  const stays = useMemo(() => tripWideStaysFromDest(dest), [dest]);
  const carLines = useMemo(() => tripWideCarGroundHint(dest), [dest]);

  const {
    sessionId,
    wanderlyTripId,
    tripName,
    setDestinations,
    beginTripDiskSave,
    completeTripDiskSave,
    failTripDiskSave,
    addResponse,
  } = useTrip();

  const [hotelSearchBusyIndex, setHotelSearchBusyIndex] = useState<number | null>(null);
  const [flightSearchBusyIndex, setFlightSearchBusyIndex] = useState<number | null>(null);
  const [hotelSearchErrors, setHotelSearchErrors] = useState<Record<number, string>>({});
  const [flightSearchErrors, setFlightSearchErrors] = useState<Record<number, string>>({});
  const [hotelSearchResults, setHotelSearchResults] = useState<Record<number, HotelOption>>({});
  const [flightSearchResults, setFlightSearchResults] = useState<Record<number, FlightOption>>({});
  /** When false, hide the detailed hotel card for that row (summary row stays visible). */
  const [hotelDetailsExpanded, setHotelDetailsExpanded] = useState<Record<number, boolean>>({});
  const [flightDetailsExpanded, setFlightDetailsExpanded] = useState<Record<number, boolean>>({});
  const otaSearchLockRef = useRef(false);
  const [otaProgressSteps, setOtaProgressSteps] = useState<BookingProgressStep[]>([]);
  const [otaProgressLabel, setOtaProgressLabel] = useState("");
  const [otaStreamPreview, setOtaStreamPreview] = useState("");
  const otaStreamDraftsRef = useRef<BookingStreamDrafts>({
    assistant: { text: "" },
    thinking: { text: "" },
  });

  const pushOtaProgress = useCallback((ev: Record<string, unknown>) => {
    setOtaProgressSteps((prev) => {
      const out = applyBookingStreamEvent(prev, ev, otaStreamDraftsRef.current);
      if (out.primary) setOtaProgressLabel(out.primary);
      if (out.streamPreview) setOtaStreamPreview(out.streamPreview);
      return out.steps;
    });
  }, []);

  useEffect(() => {
    setHotelSearchErrors({});
    setHotelSearchResults({});
    setHotelSearchBusyIndex(null);
    setHotelDetailsExpanded({});
    setFlightSearchErrors({});
    setFlightSearchResults({});
    setFlightSearchBusyIndex(null);
    setFlightDetailsExpanded({});
    otaSearchLockRef.current = false;
  }, [dest.id]);

  const handleHotelSearch = useCallback(
    async (rowIndex: number, stayTitle: string) => {
      if (otaSearchLockRef.current) return;
      if (!sessionId.trim()) {
        addResponse({ type: "tip", text: "Start the planner so we have a session before running hotel search." });
        return;
      }

      otaSearchLockRef.current = true;
      setOtaProgressSteps([]);
      setOtaProgressLabel("");
      setOtaStreamPreview("");
      otaStreamDraftsRef.current = { assistant: { text: "" }, thinking: { text: "" } };
      setHotelSearchBusyIndex(rowIndex);
      setHotelSearchResults((prev) => {
        const next = { ...prev };
        delete next[rowIndex];
        return next;
      });
      setHotelDetailsExpanded((prev) => ({ ...prev, [rowIndex]: true }));
      setHotelSearchErrors((prev) => {
        const next = { ...prev };
        delete next[rowIndex];
        return next;
      });

      try {
        const res = await runSingleHotelBookingSearch({
          sessionId: sessionId.trim(),
          wanderlyTripId: wanderlyTripId?.trim() || undefined,
          tripName: tripName?.trim() || undefined,
          profile,
          dest,
          hotelPlanIndex: rowIndex,
          stayTitle,
          setDestinations,
          beginTripDiskSave,
          completeTripDiskSave,
          failTripDiskSave,
          onProgress: pushOtaProgress,
        });

        if (!res.ok) {
          const err = res.error;
          setHotelSearchErrors((prev) => ({ ...prev, [rowIndex]: err }));
          addResponse({ type: "tip", text: err });
          return;
        }

        setHotelSearchResults((prev) => ({ ...prev, [rowIndex]: res.hotel }));
        setHotelDetailsExpanded((prev) => ({ ...prev, [rowIndex]: true }));
        addResponse({
          type: "tip",
          text: `Hotel search complete: ${res.hotel.name} (${stayTitle}).`,
        });
      } finally {
        otaSearchLockRef.current = false;
        setHotelSearchBusyIndex(null);
      }
    },
    [
      sessionId,
      wanderlyTripId,
      tripName,
      profile,
      dest,
      setDestinations,
      beginTripDiskSave,
      completeTripDiskSave,
      failTripDiskSave,
      addResponse,
      pushOtaProgress,
    ],
  );

  const handleHotelRankSelect = useCallback(
    (rowIndex: number, rank: number) => {
      const base = hotelSearchResults[rowIndex] ?? persistedHotelPickForStayRow(dest, rowIndex);
      if (!base?.ranked_picks?.length) return;
      const updated = hotelWithSelectedRank(base, rank);
      setHotelSearchResults((prev) => ({ ...prev, [rowIndex]: updated }));
      setDestinations((prev) => {
        const next = prev.map((d) => {
          if (d.id !== dest.id) return d;
          const nextHotels = [...(d.hotels || [])];
          if (rowIndex < 0 || rowIndex >= nextHotels.length) return d;
          nextHotels[rowIndex] = { ...nextHotels[rowIndex], ...updated, best: true };
          return { ...d, hotels: nextHotels, liveBookingEstimates: true };
        });
        const sid = sessionId.trim();
        if (sid) {
          beginTripDiskSave();
          void syncUi(sid, next as unknown[], {
            tripId: wanderlyTripId?.trim() || undefined,
            profilePills: profile.map((p) => ({
              key: p.key,
              label: p.label,
              value: p.value,
              icon: p.icon,
              editable: p.editable !== false,
            })),
          })
            .then((r) => completeTripDiskSave(next as DestCard[], { savedToDisk: Boolean(r.saved_to_disk) }))
            .catch(() => failTripDiskSave());
        }
        return next;
      });
    },
    [
      dest,
      hotelSearchResults,
      sessionId,
      wanderlyTripId,
      profile,
      setDestinations,
      beginTripDiskSave,
      completeTripDiskSave,
      failTripDiskSave,
    ],
  );

  const handleFlightRankSelect = useCallback(
    (segmentIndex: number, rank: number) => {
      const base = flightSearchResults[segmentIndex] ?? persistedFlightPickForSegment(dest, segmentIndex);
      if (!base?.ranked_picks?.length) return;
      const updated = flightWithSelectedRank(base, rank);
      setFlightSearchResults((prev) => ({ ...prev, [segmentIndex]: updated }));
      setDestinations((prev) => {
        const next = prev.map((d) => {
          if (d.id !== dest.id) return d;
          const nextFlights = [...(d.flights || [])];
          if (segmentIndex < 0 || segmentIndex >= nextFlights.length) return d;
          nextFlights[segmentIndex] = { ...nextFlights[segmentIndex], ...updated, best: true };
          return { ...d, flights: nextFlights };
        });
        const sid = sessionId.trim();
        if (sid) {
          beginTripDiskSave();
          void syncUi(sid, next as unknown[], {
            tripId: wanderlyTripId?.trim() || undefined,
            profilePills: profile.map((p) => ({
              key: p.key,
              label: p.label,
              value: p.value,
              icon: p.icon,
              editable: p.editable !== false,
            })),
          })
            .then((r) => completeTripDiskSave(next as DestCard[], { savedToDisk: Boolean(r.saved_to_disk) }))
            .catch(() => failTripDiskSave());
        }
        return next;
      });
    },
    [
      dest,
      flightSearchResults,
      sessionId,
      wanderlyTripId,
      profile,
      setDestinations,
      beginTripDiskSave,
      completeTripDiskSave,
      failTripDiskSave,
    ],
  );

  const handleFlightSearch = useCallback(
    async (segmentIndex: number, routeSummary: string) => {
      if (otaSearchLockRef.current) return;
      if (!sessionId.trim()) {
        addResponse({ type: "tip", text: "Start the planner so we have a session before running flight search." });
        return;
      }

      otaSearchLockRef.current = true;
      setOtaProgressSteps([]);
      setOtaProgressLabel("");
      setOtaStreamPreview("");
      otaStreamDraftsRef.current = { assistant: { text: "" }, thinking: { text: "" } };
      setFlightSearchBusyIndex(segmentIndex);
      setFlightSearchResults((prev) => {
        const next = { ...prev };
        delete next[segmentIndex];
        return next;
      });
      setFlightDetailsExpanded((prev) => ({ ...prev, [segmentIndex]: true }));
      setFlightSearchErrors((prev) => {
        const next = { ...prev };
        delete next[segmentIndex];
        return next;
      });

      try {
        const res = await runSingleFlightBookingSearch({
          sessionId: sessionId.trim(),
          wanderlyTripId: wanderlyTripId?.trim() || undefined,
          tripName: tripName?.trim() || undefined,
          profile,
          dest,
          flightSegmentIndex: segmentIndex,
          setDestinations,
          beginTripDiskSave,
          completeTripDiskSave,
          failTripDiskSave,
          onProgress: pushOtaProgress,
        });

        if (!res.ok) {
          const err = res.error;
          setFlightSearchErrors((prev) => ({ ...prev, [segmentIndex]: err }));
          addResponse({ type: "tip", text: err });
          return;
        }

        setFlightSearchResults((prev) => ({ ...prev, [segmentIndex]: res.flight }));
        setFlightDetailsExpanded((prev) => ({ ...prev, [segmentIndex]: true }));
        addResponse({
          type: "tip",
          text: `Flight search complete: ${res.flight.airline} (${routeSummary}).`,
        });
      } finally {
        otaSearchLockRef.current = false;
        setFlightSearchBusyIndex(null);
      }
    },
    [
      sessionId,
      wanderlyTripId,
      tripName,
      profile,
      dest,
      setDestinations,
      beginTripDiskSave,
      completeTripDiskSave,
      failTripDiskSave,
      addResponse,
      pushOtaProgress,
    ],
  );

  const otaRowBusy = hotelSearchBusyIndex !== null || flightSearchBusyIndex !== null;

  return (
    <section className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-3">
      <p className="text-[10px] font-medium uppercase tracking-wider text-white/35">Trip-wide booking prep</p>
      <p className="mt-1 text-[11px] leading-snug text-white/40">
        Search one commercial flight leg at a time (inbound, within-trip air, return — not short surface hops) and one
        stay at a time on Expedia. Results merge into the card for pricing elsewhere.
      </p>

      <div className="mt-3 flex w-full flex-col gap-3">
        {/* Flights */}
        <div className="flex flex-col rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-sky-500/15">
                <Plane className="h-4 w-4 text-sky-300/90" aria-hidden />
              </div>
              <span className="text-[10px] font-medium uppercase tracking-wider text-sky-300/75">Flights</span>
            </div>
          </div>
          {segs.length ? (
            <ul className="space-y-3">
              {segs.map((s, i) => {
                const pill = rolePill(s.role);
                const routeSummary = `${s.fromLabel} → ${s.toLabel}`;
                const picked = flightSearchResults[i] ?? persistedFlightPickForSegment(dest, i);
                const displayedFlight = picked ? resolveDisplayedFlight(picked) : null;
                const detailsOpen = picked ? flightDetailsExpanded[i] !== false : true;
                const hasPickedFlight = Boolean(picked);
                return (
                  <li
                    key={`tp-${i}-${s.fromLabel}-${s.toLabel}`}
                    className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2"
                  >
                    <div className="flex items-start gap-2">
                      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-sky-500/12">
                        {s.role === "return" ? (
                          <PlaneLanding className="h-3 w-3 text-sky-300/90" aria-hidden />
                        ) : (
                          <PlaneTakeoff className="h-3 w-3 text-sky-300/90" aria-hidden />
                        )}
                      </div>
                      <div className="min-w-0 flex-1 pr-1">
                        <span
                          className={`inline-block rounded border px-1.5 py-0.5 text-[9px] font-medium uppercase ${pill.className}`}
                        >
                          {pill.label}
                        </span>
                        <p className="mt-1 text-[11px] leading-snug text-white/78">
                          <span className="text-white/50">{s.fromLabel}</span>
                          <span className="mx-1 text-white/22">→</span>
                          <span className="text-white/50">{s.toLabel}</span>
                        </p>
                        {hasPickedFlight && !detailsOpen ? (
                          <p className="mt-1.5 truncate text-[10px] leading-snug text-white/42">
                            Selected: {displayedFlight?.airline} · {displayedFlight?.price}
                          </p>
                        ) : null}
                      </div>
                      <div className="mt-0.5 flex shrink-0 flex-col gap-1">
                        <button
                          type="button"
                          title={
                            hasPickedFlight
                              ? `Re-run Expedia search for: ${routeSummary}`
                              : `Search this leg on Expedia: ${routeSummary}`
                          }
                          aria-label={
                            hasPickedFlight
                              ? `Re-search flight on Expedia: ${routeSummary}`
                              : `Search flight on Expedia: ${routeSummary}`
                          }
                          disabled={otaRowBusy}
                          onClick={() => void handleFlightSearch(i, routeSummary)}
                          className="flex h-7 w-7 items-center justify-center rounded-md border border-sky-500/30 bg-sky-500/10 text-sky-200/90 transition-colors hover:border-sky-400/45 hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-35"
                        >
                          {flightSearchBusyIndex === i ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                          ) : (
                            <Search className="h-3.5 w-3.5" aria-hidden />
                          )}
                        </button>
                        {hasPickedFlight ? (
                          <button
                            type="button"
                            aria-expanded={detailsOpen}
                            title={detailsOpen ? "Hide flight details" : "Show flight details"}
                            aria-label={detailsOpen ? "Collapse flight details" : "Expand flight details"}
                            disabled={otaRowBusy}
                            onClick={() =>
                              setFlightDetailsExpanded((p) => ({
                                ...p,
                                [i]: !((p[i] ?? true)),
                              }))
                            }
                            className="flex h-7 w-7 items-center justify-center rounded-md border border-white/[0.1] bg-white/[0.04] text-white/55 transition-colors hover:border-white/[0.18] hover:bg-white/[0.07] hover:text-white/75 disabled:cursor-not-allowed disabled:opacity-35"
                          >
                            {detailsOpen ? (
                              <ChevronUp className="h-3.5 w-3.5" aria-hidden />
                            ) : (
                              <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                            )}
                          </button>
                        ) : null}
                      </div>
                    </div>
                    {flightSearchBusyIndex === i ? (
                      <div className="mt-2">
                        <BookingSearchProgressCard
                          primary={otaProgressLabel || "Searching on Expedia…"}
                          streamPreview={otaStreamPreview}
                          steps={otaProgressSteps}
                        />
                      </div>
                    ) : null}
                    {flightSearchErrors[i] ? (
                      <p className="mt-2 text-[10px] leading-snug text-rose-300/90">{flightSearchErrors[i]}</p>
                    ) : null}
                    {hasPickedFlight && detailsOpen ? (
                      <div className="mt-2">
                        <DetailedFlightCard
                          flight={picked}
                          onClick={() => undefined}
                          onSelectRank={(rank) => handleFlightRankSelect(i, rank)}
                        />
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-[11px] leading-relaxed text-white/38">
              No commercial flight segments on file yet. Ground-only trips or segments still loading will show day-level
              moves below. Intercity airline legs appear here once the transport plan lists them (not short hops).
            </p>
          )}
        </div>

        {/* Stays */}
        <div className="flex flex-col rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
          <div className="mb-2 flex items-center gap-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-500/15">
              <Hotel className="h-4 w-4 text-amber-300/90" aria-hidden />
            </div>
            <span className="text-[10px] font-medium uppercase tracking-wider text-amber-300/75">Hotels</span>
          </div>
          {stays.length ? (
            <ul className="space-y-3">
              {stays.map((row, i) => {
                const picked = hotelSearchResults[i] ?? persistedHotelPickForStayRow(dest, i);
                const detailsOpen = picked ? hotelDetailsExpanded[i] !== false : true;
                const hasPickedHotel = Boolean(picked);
                return (
                <li
                  key={`stay-${i}-${row.title}`}
                  className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2"
                >
                  <div className="flex items-start gap-2">
                    <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-amber-500/12">
                      <MapPin className="h-3 w-3 text-amber-300/85" aria-hidden />
                    </div>
                    <div className="min-w-0 flex-1 pr-1">
                      <span className="inline-block rounded border border-amber-500/25 bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-medium uppercase text-amber-200/90">
                        Stay
                      </span>
                      <p className="mt-1 text-[11px] font-medium leading-snug text-white/82">{row.title}</p>
                      {row.subtitle ? (
                        <p className="mt-0.5 text-[10px] leading-snug text-white/48">{row.subtitle}</p>
                      ) : null}
                      {hasPickedHotel && !detailsOpen ? (
                        <p className="mt-1.5 truncate text-[10px] leading-snug text-white/42">
                          Selected: {picked.name} · {picked.price}
                        </p>
                      ) : null}
                    </div>
                    <div className="mt-0.5 flex shrink-0 flex-col gap-1">
                      <button
                        type="button"
                        title={
                          hasPickedHotel
                            ? `Re-run Expedia search for: ${row.title}`
                            : `Search this stay on Expedia: ${row.title}`
                        }
                        aria-label={
                          hasPickedHotel
                            ? `Re-search stay on Expedia: ${row.title}`
                            : `Search stay on Expedia: ${row.title}`
                        }
                        disabled={otaRowBusy}
                        onClick={() => void handleHotelSearch(i, row.title)}
                        className="flex h-7 w-7 items-center justify-center rounded-md border border-amber-500/30 bg-amber-500/10 text-amber-200/90 transition-colors hover:border-amber-400/45 hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-35"
                      >
                        {hotelSearchBusyIndex === i ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                        ) : (
                          <Search className="h-3.5 w-3.5" aria-hidden />
                        )}
                      </button>
                      {hasPickedHotel ? (
                        <button
                          type="button"
                          aria-expanded={detailsOpen}
                          title={detailsOpen ? "Hide hotel details" : "Show hotel details"}
                          aria-label={detailsOpen ? "Collapse hotel details" : "Expand hotel details"}
                          disabled={otaRowBusy}
                          onClick={() =>
                            setHotelDetailsExpanded((p) => ({
                              ...p,
                              [i]: !((p[i] ?? true)),
                            }))
                          }
                          className="flex h-7 w-7 items-center justify-center rounded-md border border-white/[0.1] bg-white/[0.04] text-white/55 transition-colors hover:border-white/[0.18] hover:bg-white/[0.07] hover:text-white/75 disabled:cursor-not-allowed disabled:opacity-35"
                        >
                          {detailsOpen ? (
                            <ChevronUp className="h-3.5 w-3.5" aria-hidden />
                          ) : (
                            <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                          )}
                        </button>
                      ) : null}
                    </div>
                  </div>
                  {hotelSearchBusyIndex === i ? (
                    <div className="mt-2">
                      <BookingSearchProgressCard
                        primary={otaProgressLabel || "Searching this stay on Expedia…"}
                        streamPreview={otaStreamPreview}
                        steps={otaProgressSteps}
                      />
                    </div>
                  ) : null}
                  {hotelSearchErrors[i] ? (
                    <p className="mt-2 text-[10px] leading-snug text-rose-300/90">{hotelSearchErrors[i]}</p>
                  ) : null}
                  {hasPickedHotel && detailsOpen ? (
                    <div className="mt-2">
                      <DetailedHotelCard
                        hotel={picked}
                        onClick={() => undefined}
                        onSelectRank={(rank) => handleHotelRankSelect(i, rank)}
                      />
                    </div>
                  ) : null}
                </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-[11px] leading-relaxed text-white/38">
              No consolidated stay outline yet — nightly stays still appear in each day card when the model emits
              lodging rows.
            </p>
          )}
        </div>

        {/* Car / ground */}
        <div className="flex flex-col rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
          <div className="mb-2 flex items-center gap-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-emerald-500/15">
              <Car className="h-4 w-4 text-emerald-300/90" aria-hidden />
            </div>
            <span className="text-[10px] font-medium uppercase tracking-wider text-emerald-300/75">Car & ground</span>
          </div>
          <ul className="space-y-1.5">
            {carLines.map((line, i) => (
              <li key={`car-${i}`} className="text-[11px] leading-relaxed text-white/45">
                {line}
              </li>
            ))}
          </ul>
          <button
            type="button"
            disabled
            title="Trip-wide car rental search — coming soon"
            className="mt-2 w-full shrink-0 rounded-lg border border-white/[0.08] bg-white/[0.03] py-2 text-[10px] font-medium uppercase tracking-wide text-white/30"
          >
            Search car (soon)
          </button>
        </div>
      </div>
    </section>
  );
}

function DayLogisticsEvents({
  day,
  dest,
}: {
  day: ItinDay;
  dest: DestCard;
}) {
  const dl = day.day_logistics;
  if (!dl || typeof dl !== "object") return null;

  const blocks: ReactNode[] = [];

  const flightSegs = dl.flight?.segments;
  if (Array.isArray(flightSegs) && flightSegs.length) {
    flightSegs.forEach((seg: DayLogisticsFlightSegment, i: number) => {
      const fr = (seg.from_search_label || "").trim();
      const to = (seg.to_search_label || "").trim();
      if (!fr && !to) return;
      const pill = rolePill(seg.role);
      blocks.push(
        <li
          key={`f-${day.day}-${i}`}
          className="flex gap-2.5 rounded-lg border border-white/[0.05] bg-sky-500/[0.04] px-2 py-2"
        >
          <Plane className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-300/80" aria-hidden />
          <div className="min-w-0 flex-1">
            <span className={`inline-block rounded border px-1.5 py-0.5 text-[9px] font-medium ${pill.className}`}>
              {pill.label}
            </span>
            <p className="mt-1 text-[11px] leading-snug text-white/75">
              <span className="text-white/50">{fr || "—"}</span>
              <span className="mx-1 text-white/20">→</span>
              <span className="text-white/50">{to || "—"}</span>
            </p>
          </div>
        </li>,
      );
    });
  }

  const legs = dl.surface_legs;
  const dayTrail = dl.trail_segment as DayLogisticsTrailSegment | undefined;
  const walkLegIndices =
    Array.isArray(legs) ? legs.map((leg, i) => ((leg.kind || "").toLowerCase() === "walk" ? i : -1)).filter((i) => i >= 0) : [];
  const primaryWalkIdx = walkLegIndices.length ? walkLegIndices[walkLegIndices.length - 1] : -1;

  if (Array.isArray(legs) && legs.length) {
    legs.forEach((leg: DayLogisticsSurfaceLeg, i: number) => {
      if (!leg || typeof leg !== "object") return;
      const k = (leg.kind || "drive").toLowerCase();
      const fr = (leg.from_label || "").trim();
      const to = (leg.to_label || "").trim();
      const op = (leg.operator_hint || "").trim();
      const isWalk = k === "walk";
      const trailStats = isWalk && i === primaryWalkIdx ? resolveTrailStats(leg, dayTrail) : null;
      const trailLine = trailStats ? formatTrailStatsLine(trailStats) : null;
      const trailName = (trailStats?.name || "").trim();
      blocks.push(
        <li
          key={`s-${day.day}-${i}`}
          className={`flex gap-2.5 rounded-lg border px-2 py-2 ${
            isWalk ? "border-emerald-500/15 bg-emerald-500/[0.05]" : "border-white/[0.05] bg-white/[0.02]"
          }`}
        >
          <SurfaceIcon kind={k} />
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-medium uppercase tracking-wide text-white/35">{isWalk ? "Hike" : k}</p>
            {trailName ? (
              <p className="mt-0.5 text-[11px] font-medium leading-snug text-emerald-200/90">{trailName}</p>
            ) : null}
            <p className={`${trailName ? "mt-0.5" : "mt-0.5"} text-[11px] leading-snug text-white/70`}>
              {fr || "—"}
              <span className="mx-1 text-white/20">→</span>
              {to || "—"}
            </p>
            {trailLine ? <p className="mt-1 text-[10px] font-medium text-emerald-300/75">{trailLine}</p> : null}
            {trailStats?.notes ? <p className="mt-0.5 text-[10px] text-white/40">{trailStats.notes}</p> : null}
            {op ? <p className="mt-0.5 text-[10px] text-white/40">{op}</p> : null}
          </div>
        </li>,
      );
    });
  }

  if (walkLegIndices.length === 0 && dayTrail) {
    const endpoints = parseDayLabelEndpoints(day.label || "");
    const campForEnds = dl.camp_night as DayLogisticsCampNight | undefined;
    const fr = endpoints?.from || (day.base || "").trim();
    const to = endpoints?.to || (campForEnds?.label || "").trim();
    if (fr && to && fr.toLowerCase() !== to.toLowerCase()) {
      const trailStats = resolveTrailStats(undefined, dayTrail);
      const trailLine = trailStats ? formatTrailStatsLine(trailStats) : null;
      const trailName = (trailStats?.name || "").trim();
      blocks.push(
        <li
          key={`s-inferred-${day.day}`}
          className="flex gap-2.5 rounded-lg border border-emerald-500/15 bg-emerald-500/[0.05] px-2 py-2"
        >
          <SurfaceIcon kind="walk" />
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-medium uppercase tracking-wide text-white/35">Hike</p>
            {trailName ? (
              <p className="mt-0.5 text-[11px] font-medium leading-snug text-emerald-200/90">{trailName}</p>
            ) : null}
            <p className="mt-0.5 text-[11px] leading-snug text-white/70">
              {fr}
              <span className="mx-1 text-white/20">→</span>
              {to}
            </p>
            {trailLine ? <p className="mt-1 text-[10px] font-medium text-emerald-300/75">{trailLine}</p> : null}
            <p className="mt-1 text-[10px] text-white/40">Inferred from day label — rebuild itinerary to persist walk legs.</p>
          </div>
        </li>,
      );
    }
  }

  const camp = dl.camp_night as DayLogisticsCampNight | undefined;
  if (camp && typeof camp === "object" && (camp.label || camp.kind)) {
    const label = (camp.label || "").trim() || "Camp";
    const subtitle = formatCampNightSubtitle(camp);
    blocks.push(
      <li
        key={`c-${day.day}`}
        className="flex gap-2.5 rounded-lg border border-lime-500/20 bg-lime-500/[0.06] px-2 py-2"
      >
        <Tent className="mt-0.5 h-3.5 w-3.5 shrink-0 text-lime-300/90" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-medium uppercase tracking-wide text-lime-300/70">
            {campKindLabel(camp.kind)}
          </p>
          <p className="mt-0.5 text-[11px] font-medium leading-snug text-white/85">{label}</p>
          {subtitle ? <p className="mt-1 text-[10px] leading-snug text-white/45">{subtitle}</p> : null}
        </div>
      </li>,
    );
  }

  const lod = dl.lodging;
  if (lod && typeof lod === "object" && (lod.destination_search_label || lod.check_in)) {
    const label = (lod.destination_search_label || "").trim() || "Lodging";
    const cin = (lod.check_in || "").trim();
    const cout = (lod.check_out || "").trim();
    const rooms = lod.rooms;
    const party = lod.traveler_party?.total ?? lod.guests?.adults;
    blocks.push(
      <li
        key={`h-${day.day}`}
        className="flex gap-2.5 rounded-lg border border-amber-500/15 bg-amber-500/[0.05] px-2 py-2"
      >
        <Hotel className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300/85" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-medium uppercase tracking-wide text-white/35">Stay</p>
          <p className="mt-0.5 text-[11px] leading-snug text-white/80">{label}</p>
          <div className="mt-1 flex flex-wrap gap-2 text-[10px] text-white/45">
            {cin ? <span>Check-in {cin}</span> : null}
            {cout ? <span>Check-out {cout}</span> : null}
            {typeof rooms === "number" && rooms > 0 ? <span>{rooms} room{rooms === 1 ? "" : "s"}</span> : null}
            {typeof party === "number" && party > 0 ? <span>{party} guests</span> : null}
          </div>
        </div>
      </li>,
    );
  }

  if (!blocks.length) return null;
  return <ul className="mt-2 space-y-1.5">{blocks}</ul>;
}

export function LogisticsPlanFlipFace({ dest, profile }: { dest: DestCard; profile: ProfilePill[] }) {
  const itinerary = dest.itinerary ?? [];
  const pax = bookingPaxFromProfile(profile);
  const fromPill = profile.find((p) => p.key === "from")?.value?.trim() || "";
  const whenPill = profile.find((p) => p.key === "when")?.value?.trim() || "";
  const whenDisplay = deriveWhenRangeFromItinerary(itinerary) || whenPill;
  const tripDaysLabel = cardTripDaysLabel(itinerary);
  const modeLabel = formatTransportMode(effectiveTransportPrimaryMode(dest));
  const summary = transportPlanSummary(dest);

  if (!itinerary.length) {
    return (
      <div className="flex min-h-[200px] flex-col items-center justify-center rounded-xl border border-dashed border-white/[0.08] p-6 text-center">
        <Route className="mb-2 h-8 w-8 text-white/20" aria-hidden />
        <p className="text-[13px] text-white/50">No day-by-day plan yet.</p>
        <p className="mt-1 max-w-xs text-[11px] text-white/35">Open this card after the itinerary loads to see logistics.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col rounded-xl border border-white/[0.08] bg-[#0c0c12]">
      <div className="border-b border-white/[0.06] px-4 py-3">
        <p className="text-[10px] font-medium uppercase tracking-wider text-indigo-300/70">Logistics & transport</p>
        <h3 className="mt-0.5 text-[15px] font-semibold leading-snug text-white/90">{dest.name}</h3>
        <p className="text-[11px] text-white/40">{dest.country}</p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          <span className="inline-flex items-center gap-1 rounded-full border border-white/[0.08] bg-white/[0.04] px-2.5 py-1 text-[10px] text-white/70">
            <Calendar className="h-3 w-3 shrink-0 text-white/35" aria-hidden />
            {tripDaysLabel ?? `${itinerary.length} days`}
          </span>
          <span className="inline-flex items-center gap-1 rounded-full border border-white/[0.08] bg-white/[0.04] px-2.5 py-1 text-[10px] text-white/70">
            <Users className="h-3 w-3 shrink-0 text-white/35" aria-hidden />
            {pax} traveler{pax === 1 ? "" : "s"}
          </span>
          {fromPill ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-white/[0.08] bg-white/[0.04] px-2.5 py-1 text-[10px] text-white/70">
              <PlaneTakeoff className="h-3 w-3 shrink-0 text-sky-400/70" aria-hidden />
              From {fromPill}
            </span>
          ) : null}
          {whenDisplay ? (
            <span className="inline-flex max-w-[200px] items-center gap-1 truncate rounded-full border border-white/[0.08] bg-white/[0.04] px-2.5 py-1 text-[10px] text-white/60">
              {whenDisplay}
            </span>
          ) : null}
          <span className="inline-flex items-center gap-1 rounded-full border border-indigo-500/25 bg-indigo-500/10 px-2.5 py-1 text-[10px] text-indigo-200/90">
            <Route className="h-3 w-3 shrink-0 text-indigo-300/70" aria-hidden />
            {modeLabel}
          </span>
        </div>
        {summary ? <p className="mt-2 text-[11px] leading-relaxed text-white/45">{summary}</p> : null}
      </div>

      <div className="px-3 py-3 sm:px-4">
        <TripWideBookingPrepBlock dest={dest} profile={profile} />

        <ItineraryQaBanner qa={dest.itinerary_qa} />

        <AllTrailsOutdoorLinkCard dest={dest} profile={profile} />

        <NpsOutdoorPanel dest={dest} profile={profile} />

        <p className="mb-2 mt-4 text-[10px] font-medium uppercase tracking-wider text-white/35">Day by day</p>
        <div className="space-y-3">
          {itinerary.map((day) => (
            <article
              key={day.day}
              className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3"
            >
              <div className="flex items-start gap-2">
                <div className="flex h-8 w-8 shrink-0 flex-col items-center justify-center rounded-lg bg-white/[0.06] text-[11px] font-semibold text-white/55">
                  {day.day}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] font-medium leading-snug text-white/85">{day.label}</p>
                  {day.base ? (
                    <p className="mt-0.5 flex items-center gap-1 text-[10px] text-white/40">
                      <MapPin className="h-3 w-3 shrink-0" aria-hidden />
                      Base: {day.base}
                    </p>
                  ) : null}
                  {day.day_transport?.mode ? (
                    <p className="mt-1 text-[10px] text-white/35">
                      Day transport: <span className="text-white/50">{day.day_transport.mode}</span>
                      {day.day_transport.note ? (
                        <span className="text-white/30"> · {day.day_transport.note}</span>
                      ) : null}
                    </p>
                  ) : null}
                </div>
              </div>
              <DayLogisticsEvents day={day} dest={dest} />
            </article>
          ))}
        </div>

      </div>
    </div>
  );
}
