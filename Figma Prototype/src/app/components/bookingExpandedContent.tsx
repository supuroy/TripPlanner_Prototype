/**
 * Expanded-card booking experience (summary strips, flip back-face, timeline, cost).
 * Spec: TRAVEL_BOOKING_IMPLEMENTATION.md
 */
import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { flushSync } from "react-dom";
import { motion, AnimatePresence } from "motion/react";
import {
  Plane,
  Hotel,
  MapPin,
  Clock,
  Star,
  CheckCircle2,
  Calendar,
  Luggage,
  ChevronDown,
} from "lucide-react";
import {
  flightWithSelectedRank,
  mergeFlightFromSearch,
  otherRankedFlightPicks,
  resolveDisplayedFlight,
} from "../lib/flightRankedPicks";
import {
  hotelGuestRatingDisplay,
  hotelWithSelectedRank,
  otherRankedPicks,
  resolveDisplayedHotel,
} from "../lib/hotelRankedPicks";
import type { DestCard, FlightOption, HotelOption, ActivityOption, ProfilePill } from "./TripStore";
import { useTrip } from "./TripStore";
import type { WanderlyProfilePillDTO } from "../../types/workspace";
import { ImageWithFallback } from "./figma/ImageWithFallback";
import {
  bookingSearchPrepareStream,
  bookingSearchStepStream,
  syncUi,
  type BookingDateHints,
} from "../../api/wanderly";
import {
  commercialFlightSegmentsFromDest,
  transportPlanSummary,
  type PlannedFlightSegment,
} from "../lib/bookingFlightPlan";

function destinationForBookingApi(dest: DestCard): Record<string, unknown> {
  return dest as unknown as Record<string, unknown>;
}
import {
  applyBookingStreamEvent,
  BOOKING_PROGRESS_MESSAGES,
  useRotatingProgressMessage,
  type BookingProgressPhase,
  type BookingProgressStep,
} from "../lib/bookingSearchProgress";
import type { BookingStreamDrafts } from "../lib/bookingStreamCaption";
import { BookingSearchProgressCard, BookingSearchProgressStrip } from "./BookingSearchProgressStrip";

import {
  parsePriceDigits,
  priceLooksLikeLiveBookableAmount,
  liveBookingExtractLooksActionable,
  hotelRowIsActionableHotel,
} from "../lib/bookingCardGuards";

export {
  parsePriceDigits,
  priceLooksLikeLiveBookableAmount,
  liveBookingExtractLooksActionable,
  hotelRowIsActionableHotel,
};

/** True when this card still has merged live / restored OTA rows worth clearing manually. */
export function destHasPersistentBookingSummary(dest: DestCard): boolean {
  if (liveBookingExtractLooksActionable(dest)) return true;
  if (dest.liveBookingEstimates && ((dest.flights?.length ?? 0) > 0 || (dest.hotels?.length ?? 0) > 0)) {
    return true;
  }
  return false;
}

export function flightPriceRangeLabel(flights: FlightOption[]): string | null {
  if (!flights.length) return null;
  const nums = flights.map((f) => parsePriceDigits(f.price)).filter((n) => n > 0);
  if (!nums.length) return null;
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  if (lo === hi) return `$${lo.toLocaleString()}`;
  return `$${lo.toLocaleString()} - $${hi.toLocaleString()}`;
}

export function hotelAvgPerNightLabel(hotels: HotelOption[]): string | null {
  if (!hotels.length) return null;
  const perNights = hotels.map((h) => {
    const p = parsePriceDigits(h.price);
    return h.nights > 0 ? p : p;
  });
  const avg = Math.round(perNights.reduce((a, b) => a + b, 0) / hotels.length);
  return `$${avg}/n avg`;
}

export function totalHotelNights(hotels: HotelOption[]): number {
  return hotels.reduce((s, h) => s + (h.nights || 0), 0);
}

function hotelStayTotal(h: HotelOption): number {
  const per = parsePriceDigits(h.price);
  return per * (h.nights || 0);
}

export function estimatedTripRangeLabel(dest: DestCard): string | null {
  const flights = dest.flights || [];
  const hotels = dest.hotels || [];
  if (!flights.length && !hotels.length) return null;
  /** Sum all flight legs (outbound + return are separate rows). */
  const flightSum = flights.reduce((s, f) => s + parsePriceDigits(f.price), 0);
  const hSum = hotels.reduce((s, h) => s + hotelStayTotal(h), 0);
  if (flights.length && hotels.length) {
    return `$${(flightSum + hSum).toLocaleString()}`;
  }
  if (hotels.length) return `$${hSum.toLocaleString()}`;
  if (flights.length) {
    return `$${flightSum.toLocaleString()}`;
  }
  return null;
}

export function SummaryFlightStrip({ dest }: { dest: DestCard }) {
  const flights = dest.flights || [];
  if (!flights.length) return null;
  const range = flightPriceRangeLabel(flights);
  return (
    <div className="mt-4">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-[11px] uppercase tracking-widest text-white/30">Flight Options</p>
        <span className="text-[10px] text-white/25">{flights.length} available</span>
      </div>
      <div className="rounded-xl border border-dashed border-white/[0.06] bg-white/[0.04] p-4">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white/[0.06]">
            <Plane className="h-4 w-4 text-white/20" aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[13px] text-white/70">
              {flights.length} airline{flights.length === 1 ? "" : "s"} available
            </p>
            <p className="mt-1 text-[11px] text-white/40">View details in booking mode</p>
          </div>
          {range && (
            <p className="shrink-0 text-right text-[14px] font-medium tabular-nums text-white/90">{range}</p>
          )}
        </div>
      </div>
    </div>
  );
}

export function SummaryHotelStrip({ dest }: { dest: DestCard }) {
  const hotels = dest.hotels || [];
  if (!hotels.length) return null;
  const nights = totalHotelNights(hotels);
  const avg = hotelAvgPerNightLabel(hotels);
  return (
    <div className="mt-4">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-[11px] uppercase tracking-widest text-white/30">Accommodation</p>
        <span className="text-[10px] text-white/25">{hotels.length} stay{hotels.length === 1 ? "" : "s"}</span>
      </div>
      <div className="rounded-xl border border-dashed border-white/[0.06] bg-white/[0.04] p-4">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white/[0.06]">
            <Hotel className="h-4 w-4 text-white/25" aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[13px] text-white/70">
              {hotels.length} accommodation{hotels.length === 1 ? "" : "s"}
            </p>
            <p className="mt-1 text-[11px] text-white/40">
              {nights} total nights · View in booking mode
            </p>
          </div>
          {avg && (
            <p className="shrink-0 text-right text-[14px] font-medium tabular-nums text-white/90">{avg}</p>
          )}
        </div>
      </div>
    </div>
  );
}

function PlaceholderProgressHint({ message }: { message: string }) {
  if (!message.trim()) return null;
  return (
    <p className="mt-2 flex items-center gap-1.5 rounded-lg border border-sky-500/20 bg-sky-500/[0.06] px-2.5 py-1.5 text-[11px] leading-snug text-sky-200/85">
      <span className="inline-block h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-sky-400/90" aria-hidden />
      {message}
    </p>
  );
}

export function DetailedFlightCard({
  flight,
  isSelected,
  onClick,
  onSelectRank,
  loadingHint,
}: {
  flight: FlightOption;
  isSelected?: boolean;
  onClick?: () => void;
  onSelectRank?: (rank: number) => void;
  /** Shown under placeholder rows while live OTA search runs. */
  loadingHint?: string;
}) {
  const [altOpen, setAltOpen] = useState(false);
  const displayed = resolveDisplayedFlight(flight);
  const searchHref = (displayed.search_url || flight.search_url || "").trim();
  const others = otherRankedFlightPicks(flight);
  const showAlternatives = others.length > 0 && !flight.isPlaceholder;
  const stopsLabel = (displayed.stops || "").trim();
  return (
    <>
    <button
      type="button"
      onClick={onClick}
      className={`w-full rounded-xl border p-4 text-left transition-colors focus-visible:outline focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0a0a0f] ${
        isSelected
          ? "border-indigo-500/40 bg-indigo-500/10"
          : "border-white/[0.06] bg-white/[0.04] hover:border-white/[0.12]"
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white/[0.06]">
          <Plane className="h-4 w-4 text-blue-400" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[13px] font-medium text-white/90">{displayed.airline}</p>
            {flight.isPlaceholder ? (
              <span className="rounded border border-white/20 bg-white/10 px-1.5 py-0.5 text-[10px] text-white/70">
                Planned
              </span>
            ) : null}
            {displayed.leg === "return" ? (
              <span className="rounded border border-violet-500/25 bg-violet-500/15 px-1.5 py-0.5 text-[10px] text-violet-200/90">
                Return
              </span>
            ) : displayed.leg === "outbound" ? (
              <span className="rounded border border-sky-500/25 bg-sky-500/15 px-1.5 py-0.5 text-[10px] text-sky-200/90">
                Outbound
              </span>
            ) : null}
            {displayed.best ? (
              <span
                className="inline-flex items-center gap-0.5 rounded border border-amber-500/30 bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-200"
                title={
                  flight.selected_rank && flight.selected_rank > 1
                    ? `Your pick (#${flight.selected_rank})`
                    : "Top recommendation"
                }
              >
                <Star className="h-2.5 w-2.5" />
                Best value
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 text-[12px] text-white/50">{displayed.route}</p>
          {stopsLabel ? <p className="mt-1 text-[11px] text-white/42">{stopsLabel}</p> : null}
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className="flex items-center gap-1 text-[11px] text-white/40">
              <Clock className="h-3 w-3" aria-hidden />
              {displayed.duration}
            </span>
            <span className="text-[16px] font-medium tabular-nums text-white/90">{displayed.price}</span>
          </div>
        </div>
      </div>
      {flight.isPlaceholder && loadingHint ? <PlaceholderProgressHint message={loadingHint} /> : null}
      <div className="mt-3 border-t border-white/[0.06] pt-2 text-center">
        <span className="inline-flex items-center gap-1.5 text-[12px] text-indigo-400 hover:text-indigo-300">
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
          Select &amp; Book
        </span>
      </div>
    </button>
    {showAlternatives ? (
      <div className="mt-2">
        <button
          type="button"
          onClick={() => setAltOpen((o) => !o)}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[11px] text-white/55 transition-colors hover:border-white/[0.14] hover:bg-white/[0.05] hover:text-white/75"
          aria-expanded={altOpen}
        >
          <ChevronDown
            className={`h-3.5 w-3.5 shrink-0 transition-transform ${altOpen ? "rotate-180" : ""}`}
            aria-hidden
          />
          {altOpen ? "Hide other options" : `Other options (${others.length})`}
        </button>
        {altOpen ? (
          <ul className="mt-2 space-y-1.5" role="listbox" aria-label="Other flight options">
            {others.map((pick) => (
              <li key={pick.rank}>
                <button
                  type="button"
                  role="option"
                  className="w-full rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-2.5 text-left transition-colors hover:border-indigo-500/30 hover:bg-indigo-500/10"
                  onClick={() => {
                    onSelectRank?.(pick.rank);
                    setAltOpen(false);
                  }}
                >
                  <p className="text-[12px] font-medium text-white/85">
                    #{pick.rank} · {pick.airline}
                  </p>
                  <p className="mt-0.5 text-[10px] text-white/45">
                    {pick.price}
                    {pick.stops ? ` · ${pick.stops}` : ""}
                    {pick.duration ? ` · ${pick.duration}` : ""}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    ) : null}
    {searchHref ? (
      <a
        href={searchHref}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-2 block text-center text-[11px] text-white/45 underline decoration-white/20 underline-offset-2 hover:text-indigo-300 hover:decoration-indigo-400/50"
        onClick={(e) => e.stopPropagation()}
      >
        Open flight search on Expedia
      </a>
    ) : null}
    {displayed.round_trip ? (
      <p className="mt-1.5 text-center text-[10px] leading-snug text-white/38">
        Round trip: return leg is included in this Expedia search (return date = last day of your trip window).
      </p>
    ) : null}
    </>
  );
}

export function DetailedHotelCard({
  hotel,
  isSelected,
  onClick,
  onSelectRank,
  loadingHint,
}: {
  hotel: HotelOption;
  isSelected?: boolean;
  onClick?: () => void;
  onSelectRank?: (rank: number) => void;
  loadingHint?: string;
}) {
  const [altOpen, setAltOpen] = useState(false);
  const displayed = resolveDisplayedHotel(hotel);
  const listingHref = (displayed.listing_url || "").trim();
  const per = parsePriceDigits(displayed.price);
  const total = per * (displayed.nights || 0);
  const others = otherRankedPicks(hotel);
  const showAlternatives = others.length > 0 && !hotel.isPlaceholder;
  const rating = hotelGuestRatingDisplay(displayed);
  return (
    <>
    <button
      type="button"
      onClick={onClick}
      className={`w-full rounded-xl border p-4 text-left transition-colors focus-visible:outline focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0a0a0f] ${
        isSelected
          ? "border-indigo-500/40 bg-indigo-500/10"
          : "border-white/[0.06] bg-white/[0.04] hover:border-white/[0.12]"
      }`}
    >
      <div className="flex gap-3">
        <ImageWithFallback
          src={displayed.image}
          alt={displayed.name}
          className="h-20 w-20 shrink-0 rounded-lg object-cover"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <p className="text-[13px] font-medium text-white/90">{displayed.name}</p>
            {displayed.isPlaceholder ? (
              <span className="rounded border border-white/20 bg-white/10 px-1.5 py-0.5 text-[10px] text-white/70">
                Planned
              </span>
            ) : null}
            {displayed.best ? (
              <span
                className="rounded border border-amber-500/30 bg-amber-500/20 p-1 text-amber-300"
                title={
                  hotel.selected_rank && hotel.selected_rank > 1
                    ? `Your pick (#${hotel.selected_rank})`
                    : "Top recommendation"
                }
              >
                <Star className="h-3 w-3" aria-hidden />
              </span>
            ) : null}
          </div>
          <p className="text-[11px] text-white/45">{dedupeMiddleDotSegments(displayed.area)}</p>
          {rating && !displayed.isPlaceholder ? (
            <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
              <span className="inline-flex items-center gap-0.5 rounded border border-emerald-500/25 bg-emerald-500/10 px-1.5 py-0.5 font-medium tabular-nums text-emerald-200/95">
                <Star className="h-2.5 w-2.5 fill-emerald-400/40 text-emerald-300/90" aria-hidden />
                {rating.scoreText}
              </span>
              {rating.detailText ? (
                <span className="text-white/42">{rating.detailText}</span>
              ) : null}
            </p>
          ) : null}
          <p className="mt-1 flex items-center gap-1 text-[11px] text-white/40">
            <Calendar className="h-3 w-3 shrink-0" aria-hidden />
            {displayed.nights} nights × {displayed.price}
          </p>
          {hotel.isPlaceholder && loadingHint ? <PlaceholderProgressHint message={loadingHint} /> : null}
          <div className="mt-2 flex items-center justify-between border-t border-white/[0.06] pt-2">
            <span className="text-[11px] text-white/35">Total stay</span>
            <span className="text-[13px] font-medium tabular-nums text-white/80">${total.toLocaleString()}</span>
          </div>
        </div>
      </div>
      <div className="mt-3 border-t border-white/[0.06] pt-2 text-center">
        <span className="inline-flex items-center gap-1.5 text-[12px] text-indigo-400 hover:text-indigo-300">
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
          Select &amp; Book
        </span>
      </div>
    </button>
    {showAlternatives ? (
      <div className="mt-2">
        <button
          type="button"
          onClick={() => setAltOpen((o) => !o)}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[11px] text-white/55 transition-colors hover:border-white/[0.14] hover:bg-white/[0.05] hover:text-white/75"
          aria-expanded={altOpen}
        >
          <ChevronDown
            className={`h-3.5 w-3.5 shrink-0 transition-transform ${altOpen ? "rotate-180" : ""}`}
            aria-hidden
          />
          {altOpen ? "Hide other options" : `Other options (${others.length})`}
        </button>
        {altOpen ? (
          <ul className="mt-2 space-y-1.5" role="listbox" aria-label="Other hotel options">
            {others.map((pick) => {
              const altTotal = parsePriceDigits(pick.price) * (pick.nights || displayed.nights || 0);
              return (
                <li key={pick.rank}>
                  <button
                    type="button"
                    role="option"
                    className="w-full rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-2.5 text-left transition-colors hover:border-indigo-500/30 hover:bg-indigo-500/10"
                    onClick={() => {
                      onSelectRank?.(pick.rank);
                      setAltOpen(false);
                    }}
                  >
                    <p className="text-[12px] font-medium text-white/85">
                      #{pick.rank} · {pick.name}
                    </p>
                    <p className="mt-0.5 text-[10px] text-white/45">
                      {pick.nights || displayed.nights} nights × {pick.price}
                      {altTotal > 0 ? ` · ~$${altTotal.toLocaleString()} total` : ""}
                      {pick.guest_rating != null ? ` · ${pick.guest_rating} rating` : ""}
                    </p>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>
    ) : null}
    {listingHref ? (
      <a
        href={listingHref}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-2 block text-center text-[11px] text-white/45 underline decoration-white/20 underline-offset-2 hover:text-indigo-300 hover:decoration-indigo-400/50"
        onClick={(e) => e.stopPropagation()}
      >
        Open hotel listing on Expedia
      </a>
    ) : null}
    </>
  );
}

export function FlightLoadSkeleton({
  label = "Searching flights…",
  hints,
}: {
  label?: string;
  hints?: readonly string[];
}) {
  const rotating = useRotatingProgressMessage(hints ?? [], Boolean(hints?.length));
  const display = rotating || label;
  return (
    <div className="animate-pulse rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
      <div className="mb-3 flex items-center gap-3">
        <motion.div className="h-10 w-10 rounded-lg bg-white/[0.06]" />
        <div className="flex-1 space-y-2">
          <div className="h-3 w-24 rounded bg-white/[0.06]" />
          <div className="h-2 w-32 rounded bg-white/[0.06]" />
        </div>
      </div>
      <p className="text-center text-[11px] text-sky-300/75">{display}</p>
    </div>
  );
}

export function HotelLoadSkeleton({
  label,
  hints,
}: {
  label: string;
  hints?: readonly string[];
}) {
  const rotating = useRotatingProgressMessage(hints ?? [], Boolean(hints?.length));
  const display = rotating || label;
  return (
    <div className="animate-pulse rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
      <div className="mb-3 flex items-center gap-3">
        <div className="h-16 w-16 shrink-0 rounded-lg bg-white/[0.06]" />
        <div className="flex-1 space-y-2">
          <div className="h-3 w-28 rounded bg-white/[0.06]" />
          <div className="h-2 w-36 rounded bg-white/[0.06]" />
        </div>
      </div>
      <p className="text-center text-[11px] text-amber-200/75">{display}</p>
    </div>
  );
}

function activitySum(activities: ActivityOption[] | undefined): number {
  if (!activities?.length) return 0;
  return activities.reduce((s, a) => s + parsePriceDigits(a.price), 0);
}

const MONTH_NAME_TO_INDEX: [string, number][] = [
  ["january", 0],
  ["february", 1],
  ["march", 2],
  ["april", 3],
  ["may", 4],
  ["june", 5],
  ["july", 6],
  ["august", 7],
  ["september", 8],
  ["october", 9],
  ["november", 10],
  ["december", 11],
  ["jan", 0],
  ["feb", 1],
  ["mar", 2],
  ["apr", 3],
  ["jun", 5],
  ["jul", 6],
  ["aug", 7],
  ["sep", 8],
  ["oct", 9],
  ["nov", 10],
  ["dec", 11],
].sort((a, b) => b[0].length - a[0].length);

function findMonthIndexInText(s: string): number | undefined {
  const low = s.toLowerCase();
  for (const [name, idx] of MONTH_NAME_TO_INDEX) {
    if (new RegExp(`\\b${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(low)) {
      return idx;
    }
  }
  return undefined;
}

function seasonDayAnchor(sLow: string): number {
  if (/\b(early|beginning|start|first\s+half)\b/i.test(sLow)) return 7;
  if (/\b(late|end|last\s+half)\b/i.test(sLow)) return 23;
  if (/\b(mid|middle)\b/i.test(sLow)) return 15;
  return 15;
}

function toIso(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function isoShort(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** ``Sep 19–Sep 21`` / ``Sep 19-Sep 21`` chunks we append after `` · `` in ``buildPlannedHotelRows``. */
const HOTEL_AREA_DATE_SPAN = /^[A-Za-z]{3,9}\s+\d{1,2}\s*[\u2013\-]\s*[A-Za-z]{3,9}\s+\d{1,2}$/;

/** Collapse ``foo · bar · bar`` → ``foo · bar`` (bad joins / retries). */
function dedupeMiddleDotSegments(s: string): string {
  const t = (s || "").trim();
  if (!t) return t;
  const parts = t.split(/\s*·\s*/).map((p) => p.trim()).filter(Boolean);
  const out: string[] = [];
  for (const p of parts) {
    if (out.length && out[out.length - 1] === p) continue;
    out.push(p);
  }
  return out.join(" · ");
}

/**
 * Before appending check-in/out hints, drop prior ``Mon DD–Mon DD`` tokens. Otherwise a second
 * ``booking_search_prepare`` can lift ``dest.hotels`` from the session (``card_multi_fitted``) whose
 * ``area`` already contains our suffix — and we would stack ``· Sep … · Sep …`` indefinitely.
 */
function hotelAreaStripDateSpansForPlaceholderBase(raw: string | undefined, idx: number): string {
  const parts = (raw || "").split(/\s*·\s*/).map((p) => p.trim()).filter(Boolean);
  const baseParts = parts.filter((p) => !HOTEL_AREA_DATE_SPAN.test(p));
  const joined = dedupeMiddleDotSegments(baseParts.join(" · ").trim());
  if (joined) return joined;
  return `Planned stay ${idx + 1}`;
}

function buildPlannedHotelRows(
  sourceHotels: HotelOption[] | undefined,
  startIso: string | null | undefined,
): HotelOption[] {
  const rows = sourceHotels || [];
  let cursor = startIso ? new Date(`${startIso.slice(0, 10)}T12:00:00`) : null;
  return rows.map((h, idx) => {
    const nights = Math.max(1, h.nights || 1);
    let area = hotelAreaStripDateSpansForPlaceholderBase(h.area, idx);
    if (cursor && !Number.isNaN(cursor.getTime())) {
      const checkInIso = toIso(cursor);
      const checkOut = new Date(cursor);
      checkOut.setDate(checkOut.getDate() + nights);
      const checkOutIso = toIso(checkOut);
      area = dedupeMiddleDotSegments(`${area} · ${isoShort(checkInIso)}-${isoShort(checkOutIso)}`);
      cursor = checkOut;
    }
    return {
      ...h,
      name: h.name || `Planned stay ${idx + 1}`,
      area,
      price: h.price || "Searching Expedia…",
      best: true,
      isPlaceholder: true,
    };
  });
}

/** True when this slot holds a merged OTA row ready for the full hotel card. */
function hotelRowIsPersistedSearchMerge(h: HotelOption | undefined): h is HotelOption {
  return hotelRowIsActionableHotel(h);
}

/**
 * ``buildPlannedHotelRows`` replaces every stay with placeholders. When searching stay
 * ``activeSearchIndex`` (>= 0), only that row should reset; other rows keep prior merges so UI
 * and ``sync_ui`` do not wipe already-found hotels. Use ``activeSearchIndex === -1`` to keep
 * every persisted row (e.g. full booking pipeline prepare after partial single-stay searches).
 */
function mergePlaceholderHotelsKeepPersisted(
  freshPlaceholders: HotelOption[],
  prevHotels: HotelOption[] | undefined,
  activeSearchIndex: number,
): HotelOption[] {
  const prev = Array.isArray(prevHotels) ? prevHotels : [];
  return freshPlaceholders.map((ph, i) => {
    if (activeSearchIndex >= 0 && i === activeSearchIndex) return ph;
    const existing = prev[i];
    if (hotelRowIsPersistedSearchMerge(existing)) return existing;
    return ph;
  });
}

function buildStructuredFlightPlaceholders(
  segments: PlannedFlightSegment[],
  prep: { start_date?: string | null; end_date?: string | null },
  dateHintsPayload: BookingDateHints,
): FlightOption[] {
  const startIso = prep.start_date || dateHintsPayload.start_iso || "";
  const endIso = prep.end_date || dateHintsPayload.end_iso || "";
  return segments.map((seg) => {
    const dateBit =
      (seg.role || "").toLowerCase() === "return" && endIso
        ? isoShort(endIso)
        : startIso
          ? isoShort(startIso)
          : "";
    const routeSuffix = dateBit ? ` · ${dateBit}` : "";
    return {
      airline: "Searching Expedia flights…",
      route: `${seg.fromLabel} → ${seg.toLabel}${routeSuffix}`,
      duration: "Checking routes…",
      price: "Searching Expedia…",
      best: true,
      leg: (seg.role === "return" ? "return" : "outbound") as FlightOption["leg"],
      isPlaceholder: true,
    };
  });
}

function flightRowIsPersistedSearchMerge(f: FlightOption | undefined): f is FlightOption {
  if (!f || typeof f !== "object") return false;
  if (f.isPlaceholder === true) return false;
  if (String(f.search_url || "").trim()) return true;
  if (f.isPlaceholder === false) return true;
  return false;
}

function mergePlaceholderFlightsKeepPersisted(
  freshPlaceholders: FlightOption[],
  prevFlights: FlightOption[] | undefined,
  activeSearchIndex: number,
): FlightOption[] {
  const prev = Array.isArray(prevFlights) ? prevFlights : [];
  return freshPlaceholders.map((ph, i) => {
    if (activeSearchIndex >= 0 && i === activeSearchIndex) return ph;
    const existing = prev[i];
    if (flightRowIsPersistedSearchMerge(existing)) return existing;
    return ph;
  });
}

function bumpStartToFuture(start: Date, end: Date, today: Date): { start: Date; end: Date } {
  const spanDays = Math.max(
    0,
    Math.round((end.getTime() - start.getTime()) / 86_400_000),
  );
  const s = new Date(start);
  while (s < today) {
    s.setFullYear(s.getFullYear() + 1);
  }
  const e = new Date(s);
  e.setDate(e.getDate() + spanDays);
  return { start: s, end: e };
}

function parseExactWhenRange(whenRaw: string, itineraryDayCount: number): BookingDateHints | null {
  const match = whenRaw.trim().match(/(\w+)\s+(\d+)(?:–|-)(\d+)/);
  if (!match) return null;
  const [, month, startDayStr] = match;
  const monthMap: Record<string, number> = {
    Jan: 0,
    Feb: 1,
    Mar: 2,
    Apr: 3,
    May: 4,
    Jun: 5,
    Jul: 6,
    Aug: 7,
    Sep: 8,
    Oct: 9,
    Nov: 10,
    Dec: 11,
  };
  const monthNum = monthMap[month];
  if (monthNum === undefined) return null;
  const year = new Date().getFullYear();
  const start = new Date(year, monthNum, parseInt(startDayStr, 10));
  const end = new Date(start);
  const span = Math.max(1, itineraryDayCount);
  end.setDate(end.getDate() + span - 1);
  return { start_iso: toIso(start), end_iso: toIso(end) };
}

function guessVagueWhenHints(whenRaw: string, itineraryDayCount: number): BookingDateHints | null {
  const raw = whenRaw.trim();
  if (!raw) return null;
  const sLow = raw.toLowerCase();
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  // Match server ``ota_booking_search._guess_dates_from_vague_when``: explicit month
  // (e.g. Mid-June) wins over generic "(flexible dates)" — otherwise we'd use today+21d.
  const mo = findMonthIndexInText(raw);
  if (mo !== undefined) {
    const day = seasonDayAnchor(sLow);
    const y = today.getFullYear();
    const start = new Date(y, mo, day);
    const end = new Date(start);
    end.setDate(end.getDate() + Math.max(1, itineraryDayCount) - 1);
    const bumped = bumpStartToFuture(start, end, today);
    return { start_iso: toIso(bumped.start), end_iso: toIso(bumped.end) };
  }

  if (/\b(flexible|anytime|any\s*time|tbd|not\s*sure|whenever|open)\b/i.test(sLow)) {
    const start = new Date(today);
    start.setDate(start.getDate() + 21);
    const end = new Date(start);
    end.setDate(end.getDate() + Math.max(1, itineraryDayCount) - 1);
    return { start_iso: toIso(start), end_iso: toIso(end) };
  }

  return null;
}

function defaultSearchHints(itineraryDayCount: number): BookingDateHints {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const start = new Date(today);
  start.setDate(start.getDate() + 21);
  const end = new Date(start);
  end.setDate(end.getDate() + Math.max(1, itineraryDayCount) - 1);
  return { start_iso: toIso(start), end_iso: toIso(end) };
}

/**
 * ISO hints for booking search — matches server ``ota_booking_search`` heuristics:
 * exact ``Mar 15–22`` first, then vague month/season (before generic ``flexible``), then ~3 weeks out.
 */
export function computeBookingDateHints(profile: ProfilePill[], itineraryDayCount: number): BookingDateHints {
  const whenPill = profile.find((p) => p.key === "when");
  const whenRaw = whenPill?.value?.trim() ?? "";
  if (!whenRaw) return defaultSearchHints(itineraryDayCount);

  const exact = parseExactWhenRange(whenRaw, itineraryDayCount);
  if (exact?.start_iso && exact?.end_iso) {
    const start = new Date(exact.start_iso + "T12:00:00");
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    if (start < today) {
      const end = new Date(exact.end_iso + "T12:00:00");
      const bumped = bumpStartToFuture(start, end, today);
      return { start_iso: toIso(bumped.start), end_iso: toIso(bumped.end) };
    }
    return exact;
  }

  const vague = guessVagueWhenHints(whenRaw, itineraryDayCount);
  if (vague?.start_iso && vague?.end_iso) return vague;

  return defaultSearchHints(itineraryDayCount);
}

export type RunSingleHotelSearchResult =
  | { ok: true; hotel: HotelOption; hotelIndex: number }
  | { ok: false; error: string };

function resolveHotelPlanIndexAfterPrepare(
  prep: {
    hotel_count?: number;
    hotels_for_session?: Array<Record<string, unknown>> | null;
  },
  clickedIndex: number,
  clickedTitle: string,
): { index: number; error?: string } {
  const plan = prep.hotels_for_session;
  const n =
    typeof prep.hotel_count === "number" && prep.hotel_count >= 0
      ? prep.hotel_count
      : Array.isArray(plan)
        ? plan.length
        : 0;
  if (!n || !Array.isArray(plan) || plan.length === 0) {
    return { index: -1, error: "No lodging plan on the server for this card yet." };
  }
  const norm = (s: string) => s.trim().toLowerCase();
  const title = norm(clickedTitle);
  if (clickedIndex >= 0 && clickedIndex < plan.length) {
    const row = plan[clickedIndex] as { area?: string; label?: string; name?: string };
    const area = norm(String(row.area || row.label || row.name || ""));
    if (!title || !area || area.includes(title) || title.includes(area)) {
      return { index: clickedIndex };
    }
  }
  for (let i = 0; i < plan.length; i++) {
    const row = plan[i] as { area?: string; label?: string };
    const area = norm(String(row.area || row.label || ""));
    if (area && title && (area.includes(title) || title.includes(area))) {
      return { index: i };
    }
  }
  if (clickedIndex >= 0 && clickedIndex < plan.length) return { index: clickedIndex };
  return { index: -1, error: "Could not match this stay to the server lodging plan." };
}

/**
 * Run Expedia hotel search + LLM parse for one planned stay (same server path as the full booking timeline).
 * Refreshes placeholder hotel rows for the card, then fills in ``hotelIndex``.
 */
export async function runSingleHotelBookingSearch(args: {
  sessionId: string;
  wanderlyTripId?: string;
  /** With ``wanderlyTripId``, sent as ``trip_meta`` so ``sync_ui`` can bind the manifest row reliably. */
  tripName?: string;
  profile: ProfilePill[];
  dest: DestCard;
  /** Row index from trip-wide stays UI (aligned with ``tripWideStaysFromDest`` order). */
  hotelPlanIndex: number;
  /** Stay label for fuzzy match against ``hotels_for_session`` when order drifts. */
  stayTitle: string;
  setDestinations: Dispatch<SetStateAction<DestCard[]>>;
  beginTripDiskSave: () => void;
  completeTripDiskSave: (dests: DestCard[], opts?: { savedToDisk?: boolean }) => void;
  failTripDiskSave: () => void;
  onProgress?: (ev: Record<string, unknown>) => void;
}): Promise<RunSingleHotelSearchResult> {
  const {
    sessionId,
    wanderlyTripId,
    tripName,
    profile,
    dest,
    hotelPlanIndex,
    stayTitle,
    setDestinations,
    beginTripDiskSave,
    completeTripDiskSave,
    failTripDiskSave,
    onProgress,
  } = args;

  const tidSync = wanderlyTripId?.trim();
  const tnameSync = tripName?.trim();
  const syncUiTripOpts = {
    tripId: tidSync || undefined,
    ...(tidSync && tnameSync ? { tripMeta: { trip_id: tidSync, trip_name: tnameSync } } : {}),
    profilePills: profile,
  };

  const itineraryDayCount = Math.max(1, dest.itinerary?.length ?? 1);
  const dateHints = computeBookingDateHints(profile, itineraryDayCount);
  const dateHintsPayload = {
    ...dateHints,
    adults: adultsFromProfileForBooking(profile),
  };
  const pillsPayload = profilePillsForBookingApi(profile);

  try {
    const prep = await bookingSearchPrepareStream(
      {
        session_id: sessionId,
        destination_id: dest.id,
        destination: destinationForBookingApi(dest),
        date_hints: dateHintsPayload,
        profile_pills: pillsPayload,
        skip_parallel_prefetch: true,
      },
      onProgress ?? (() => undefined),
    );

    const { index: resolvedIndex, error: resolveErr } = resolveHotelPlanIndexAfterPrepare(
      prep,
      hotelPlanIndex,
      stayTitle,
    );
    if (resolvedIndex < 0) {
      return { ok: false, error: resolveErr || "Invalid hotel plan index." };
    }

    const planHotels = prep.hotels_for_session;
    const nStays =
      typeof prep.hotel_count === "number" && prep.hotel_count >= 0
        ? prep.hotel_count
        : Array.isArray(planHotels)
          ? planHotels.length
          : 0;
    if (!nStays || !Array.isArray(planHotels) || !planHotels.length) {
      return { ok: false, error: "No stays to search for this destination." };
    }

    const placeholderHotels = buildPlannedHotelRows(
      planHotels.slice(0, nStays) as unknown as HotelOption[],
      prep.start_date || dateHintsPayload.start_iso,
    );

    const withPlaceholders = (prev: DestCard[]): DestCard[] =>
      prev.map((d) => {
        if (d.id !== dest.id) return d;
        const mergedHotels = mergePlaceholderHotelsKeepPersisted(
          placeholderHotels,
          d.hotels,
          resolvedIndex,
        );
        return { ...d, hotels: mergedHotels, liveBookingEstimates: true };
      });

    let committed: DestCard[] = [];
    setDestinations((prev) => {
      committed = withPlaceholders(prev);
      return committed;
    });
    beginTripDiskSave();
    try {
      const r = await syncUi(sessionId, committed as unknown[], syncUiTripOpts);
      completeTripDiskSave(committed, { savedToDisk: Boolean(r.saved_to_disk) });
    } catch {
      failTripDiskSave();
    }

    const hr = await bookingSearchStepStream(
      {
        session_id: sessionId,
        destination_id: dest.id,
        destination: destinationForBookingApi(dest),
        step: "hotel",
        hotel_index: resolvedIndex,
        date_hints: dateHintsPayload,
        profile_pills: pillsPayload,
      },
      onProgress ?? (() => undefined),
    );

    if (!hr.ok || !hr.hotel) {
      return { ok: false, error: hr.error || "Hotel search did not return a result." };
    }

    let mergedForDisk: DestCard[] = [];
    flushSync(() => {
      setDestinations((prev) => {
        mergedForDisk = prev.map((d) => {
          if (d.id !== dest.id) return d;
          const nextHotels = [...(d.hotels || [])];
          const ix = resolvedIndex;
          if (ix < 0 || ix >= nextHotels.length) return d;
          nextHotels[ix] = {
            ...nextHotels[ix],
            ...hr.hotel!,
            best: true,
            listing_url: hr.hotel!.listing_url || nextHotels[ix].listing_url,
            ranked_picks: hr.hotel!.ranked_picks ?? nextHotels[ix].ranked_picks,
            selected_rank: hr.hotel!.selected_rank ?? nextHotels[ix].selected_rank ?? 1,
            isPlaceholder: false,
          };
          return { ...d, hotels: nextHotels, liveBookingEstimates: true };
        });
        return mergedForDisk;
      });
    });

    beginTripDiskSave();
    try {
      const r2 = await syncUi(sessionId, mergedForDisk as unknown[], syncUiTripOpts);
      completeTripDiskSave(mergedForDisk, { savedToDisk: Boolean(r2.saved_to_disk) });
    } catch {
      failTripDiskSave();
    }

    return { ok: true, hotel: hr.hotel, hotelIndex: resolvedIndex };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return { ok: false, error: msg };
  }
}

export type RunSingleFlightSearchResult =
  | { ok: true; flight: FlightOption; flightSegmentIndex: number }
  | { ok: false; error: string };

/**
 * Run Expedia flight search + parse for one **commercial** segment (inbound, within-trip air, return)
 * from ``booking_hooks`` / ``transport_plan`` — same server path as structured rows in the full booking timeline.
 * Does not walk day-level hops; use ``commercialFlightSegmentsFromDest`` for the row list.
 */
export async function runSingleFlightBookingSearch(args: {
  sessionId: string;
  wanderlyTripId?: string;
  tripName?: string;
  profile: ProfilePill[];
  dest: DestCard;
  flightSegmentIndex: number;
  setDestinations: Dispatch<SetStateAction<DestCard[]>>;
  beginTripDiskSave: () => void;
  completeTripDiskSave: (dests: DestCard[], opts?: { savedToDisk?: boolean }) => void;
  failTripDiskSave: () => void;
  onProgress?: (ev: Record<string, unknown>) => void;
}): Promise<RunSingleFlightSearchResult> {
  const {
    sessionId,
    wanderlyTripId,
    tripName,
    profile,
    dest,
    flightSegmentIndex,
    setDestinations,
    beginTripDiskSave,
    completeTripDiskSave,
    failTripDiskSave,
    onProgress,
  } = args;

  const segments = commercialFlightSegmentsFromDest(dest, profile);
  if (!segments.length) {
    return {
      ok: false,
      error:
        "No commercial flight segments on this card yet. Only long-haul / intercity legs from the transport plan can be searched here (not short surface hops).",
    };
  }
  const resolvedIndex = flightSegmentIndex;
  if (resolvedIndex < 0 || resolvedIndex >= segments.length) {
    return { ok: false, error: "Invalid flight segment index." };
  }

  const tidSync = wanderlyTripId?.trim();
  const tnameSync = tripName?.trim();
  const syncUiTripOpts = {
    tripId: tidSync || undefined,
    ...(tidSync && tnameSync ? { tripMeta: { trip_id: tidSync, trip_name: tnameSync } } : {}),
    profilePills: profile,
  };

  const itineraryDayCount = Math.max(1, dest.itinerary?.length ?? 1);
  const dateHints = computeBookingDateHints(profile, itineraryDayCount);
  const dateHintsPayload = {
    ...dateHints,
    adults: adultsFromProfileForBooking(profile),
  };
  const pillsPayload = profilePillsForBookingApi(profile);

  try {
    const prep = await bookingSearchPrepareStream(
      {
        session_id: sessionId,
        destination_id: dest.id,
        destination: destinationForBookingApi(dest),
        date_hints: dateHintsPayload,
        profile_pills: pillsPayload,
        skip_parallel_prefetch: true,
      },
      onProgress ?? (() => undefined),
    );

    if (prep.include_flight_search === false) {
      return {
        ok: false,
        error:
          "Commercial flight search is disabled for this itinerary (ground / ferry / road-only routing).",
      };
    }

    const placeholderFlights = buildStructuredFlightPlaceholders(segments, prep, dateHintsPayload);

    const withFlightPlaceholders = (prev: DestCard[]): DestCard[] =>
      prev.map((d) => {
        if (d.id !== dest.id) return d;
        const mergedFlights = mergePlaceholderFlightsKeepPersisted(
          placeholderFlights,
          d.flights,
          resolvedIndex,
        );
        return { ...d, flights: mergedFlights, liveBookingEstimates: true };
      });

    let committed: DestCard[] = [];
    setDestinations((prev) => {
      committed = withFlightPlaceholders(prev);
      return committed;
    });
    beginTripDiskSave();
    try {
      const r = await syncUi(sessionId, committed as unknown[], syncUiTripOpts);
      completeTripDiskSave(committed, { savedToDisk: Boolean(r.saved_to_disk) });
    } catch {
      failTripDiskSave();
    }

    const fr = await bookingSearchStepStream(
      {
        session_id: sessionId,
        destination_id: dest.id,
        destination: destinationForBookingApi(dest),
        step: "flight",
        flight_segment_index: resolvedIndex,
        date_hints: dateHintsPayload,
        profile_pills: pillsPayload,
      },
      onProgress ?? (() => undefined),
    );

    if (fr.skipped) {
      return {
        ok: false,
        error: "Flight search was skipped for this trip (ground-only itinerary).",
      };
    }
    if (!fr.ok || !fr.flight) {
      return { ok: false, error: fr.error || "Flight search did not return a result." };
    }

    const segLeg =
      segments[resolvedIndex]?.role === "return"
        ? ("return" as const)
        : segments[resolvedIndex]?.role === "within_trip"
          ? ("outbound" as const)
          : resolvedIndex === 0
            ? ("outbound" as const)
            : ("return" as const);

    let mergedForDisk: DestCard[] = [];
    flushSync(() => {
      setDestinations((prev) => {
        mergedForDisk = prev.map((d) => {
          if (d.id !== dest.id) return d;
          let nextFlights = [...(d.flights || [])];
          if (nextFlights.length < placeholderFlights.length) {
            nextFlights = mergePlaceholderFlightsKeepPersisted(placeholderFlights, nextFlights, -1);
          }
          const ix = resolvedIndex;
          if (ix < 0 || ix >= nextFlights.length) return d;
          nextFlights[ix] = mergeFlightFromSearch(nextFlights[ix], {
            ...fr.flight!,
            leg: fr.flight!.leg ?? segLeg,
            search_url: fr.flight!.search_url || nextFlights[ix].search_url,
            round_trip: fr.flight!.round_trip ?? nextFlights[ix].round_trip,
            isPlaceholder: false,
          });
          return { ...d, flights: nextFlights, liveBookingEstimates: true };
        });
        return mergedForDisk;
      });
    });

    beginTripDiskSave();
    try {
      const r2 = await syncUi(sessionId, mergedForDisk as unknown[], syncUiTripOpts);
      completeTripDiskSave(mergedForDisk, { savedToDisk: Boolean(r2.saved_to_disk) });
    } catch {
      failTripDiskSave();
    }

    return { ok: true, flight: fr.flight, flightSegmentIndex: resolvedIndex };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return { ok: false, error: msg };
  }
}

export { BookingCostBreakdown } from "./BookingCostBreakdown";

type RunMode = "cached" | "live" | "demo";

function profilePillsForBookingApi(profile: ProfilePill[]): WanderlyProfilePillDTO[] {
  return profile.map((p) => ({
    key: p.key,
    label: p.label,
    value: p.value,
    icon: p.icon,
    editable: p.editable !== false,
  }));
}

/** Match server ``_parse_adults_from_who`` / URL ``adults:N``. */
function adultsFromProfileForBooking(profile: ProfilePill[]): number {
  const w = profile.find((x) => x.key === "who")?.value?.trim() ?? "";
  const low = w.toLowerCase();
  if (
    /\bsolo\b/.test(low) ||
    /\bsolo\s+trip\b/.test(low) ||
    /\bsolo\s+travel/.test(low) ||
    /\bsolo\s+travell?er\b/.test(low) ||
    /travel(ing)?\s+alone/.test(low) ||
    /travell?ing\s+alone/.test(low) ||
    /\btravel\s+alone\b/.test(low) ||
    /\bjust\s+me\b/.test(low) ||
    /\bonly\s+me\b/.test(low) ||
    /\bby\s+myself\b/.test(low) ||
    /single\s+travel/.test(low) ||
    /single\s+travell?er/.test(low) ||
    /\bone\s+travel/.test(low) ||
    /\b1\s+travel/.test(low) ||
    /\bgoing\s+solo\b/.test(low)
  ) {
    return 1;
  }
  const m = w.match(/(\d+)\s*adults?/i);
  if (m) return Math.min(9, Math.max(1, parseInt(m[1], 10)));
  if (/couple|pair|two\s+of\s+us/i.test(w)) return 2;
  const m2 = w.match(/^\s*(\d+)\b/);
  if (m2) return Math.min(9, Math.max(1, parseInt(m2[1], 10)));
  return 2;
}

export function bookingPaxFromProfile(profile: ProfilePill[]): number {
  return adultsFromProfileForBooking(profile);
}

export function BookingTimelineFace({
  dest,
  sessionId,
  profile,
  useCachedResults,
  dateHints,
  onSearchFinished,
}: {
  dest: DestCard;
  sessionId: string | null;
  profile: ProfilePill[];
  useCachedResults: boolean;
  dateHints: BookingDateHints;
  onSearchFinished: () => void;
}) {
  const { setDestinations, wanderlyTripId, beginTripDiskSave, completeTripDiskSave, failTripDiskSave } = useTrip();
  const flights = dest.flights || [];
  const hotels = dest.hotels || [];

  const runMode: RunMode = useCachedResults ? "cached" : sessionId ? "live" : "demo";

  const profileSig = useMemo(() => JSON.stringify(profile.map((p) => [p.key, p.value])), [profile]);
  const dateHintsKey = useMemo(() => JSON.stringify(dateHints), [dateHints]);

  const doneRef = useRef(onSearchFinished);
  doneRef.current = onSearchFinished;

  /** Demo timers (no session): mimic progressive reveal. */
  const [demoFlightReady, setDemoFlightReady] = useState(() => runMode !== "demo");
  const [demoHotelsReady, setDemoHotelsReady] = useState(() => (runMode !== "demo" ? hotels.length : 0));
  const demoTimers = useRef<number[]>([]);

  /** Live search pipeline */
  const [criteriaLines, setCriteriaLines] = useState<string[]>([]);
  const [preparePending, setPreparePending] = useState(() => runMode === "live");
  const [prepareError, setPrepareError] = useState<string | null>(null);
  const [flightPhase, setFlightPhase] = useState<"idle" | "loading" | "done" | "error">(() =>
    runMode === "live" ? "idle" : "done",
  );
  const [flightStepError, setFlightStepError] = useState<string | null>(null);
  const [hotelPhases, setHotelPhases] = useState<("pending" | "loading" | "done" | "error")[]>(() =>
    runMode === "live" ? hotels.map(() => "pending") : hotels.map(() => "done"),
  );
  const [hotelStepErrors, setHotelStepErrors] = useState<Record<number, string | undefined>>({});
  const [prepPax, setPrepPax] = useState(2);
  const [bookingStatus, setBookingStatus] = useState<string | null>(null);
  const [progressSteps, setProgressSteps] = useState<BookingProgressStep[]>([]);
  const [streamPreview, setStreamPreview] = useState("");
  const streamDraftsRef = useRef<BookingStreamDrafts>({
    assistant: { text: "" },
    thinking: { text: "" },
  });
  const [returnFlightPhase, setReturnFlightPhase] = useState<"na" | "pending" | "loading" | "done" | "error">("na");
  const [returnFlightError, setReturnFlightError] = useState<string | null>(null);
  /** Which commercial flight segment index is currently on Expedia (structured multi-leg). */
  const [busyFlightSegmentIndex, setBusyFlightSegmentIndex] = useState<number | null>(null);
  const [criteriaExpanded, setCriteriaExpanded] = useState(false);
  /** When prepare says ``include_flight_search: false`` (ground / road trip), hide flight UI and skip flight steps. */
  const [liveSkipFlights, setLiveSkipFlights] = useState(false);

  /** Bumps on each live-pipeline mount so Strict Mode / prop churn does not merge stale step results. */
  const livePipelineGen = useRef(0);

  const pushBookingEvent = useCallback((ev: Record<string, unknown>) => {
    setProgressSteps((prev) => {
      const out = applyBookingStreamEvent(prev, ev, streamDraftsRef.current);
      if (out.primary) setBookingStatus(out.primary);
      if (out.streamPreview) setStreamPreview(out.streamPreview);
      return out.steps;
    });
  }, []);

  const selectHotelRank = useCallback(
    (stayIndex: number, rank: number) => {
      setDestinations((prev) => {
        const next = prev.map((d) => {
          if (d.id !== dest.id) return d;
          const nextHotels = [...(d.hotels || [])];
          if (stayIndex < 0 || stayIndex >= nextHotels.length) return d;
          nextHotels[stayIndex] = hotelWithSelectedRank(nextHotels[stayIndex], rank);
          return { ...d, hotels: nextHotels };
        });
        if (sessionId) {
          beginTripDiskSave();
          void syncUi(sessionId, next as unknown[], {
            tripId: wanderlyTripId || undefined,
            profilePills: profile,
          })
            .then((r) => completeTripDiskSave(next as DestCard[], { savedToDisk: Boolean(r.saved_to_disk) }))
            .catch(() => failTripDiskSave());
        }
        return next;
      });
    },
    [
      dest.id,
      sessionId,
      wanderlyTripId,
      profile,
      setDestinations,
      beginTripDiskSave,
      completeTripDiskSave,
      failTripDiskSave,
    ],
  );

  const selectFlightRank = useCallback(
    (flightIndex: number, rank: number) => {
      setDestinations((prev) => {
        const next = prev.map((d) => {
          if (d.id !== dest.id) return d;
          const nextFlights = [...(d.flights || [])];
          if (flightIndex < 0 || flightIndex >= nextFlights.length) return d;
          nextFlights[flightIndex] = flightWithSelectedRank(nextFlights[flightIndex], rank);
          return { ...d, flights: nextFlights };
        });
        if (sessionId) {
          beginTripDiskSave();
          void syncUi(sessionId, next as unknown[], {
            tripId: wanderlyTripId || undefined,
            profilePills: profile,
          })
            .then((r) => completeTripDiskSave(next as DestCard[], { savedToDisk: Boolean(r.saved_to_disk) }))
            .catch(() => failTripDiskSave());
        }
        return next;
      });
    },
    [
      dest.id,
      sessionId,
      wanderlyTripId,
      profile,
      setDestinations,
      beginTripDiskSave,
      completeTripDiskSave,
      failTripDiskSave,
    ],
  );

  /* ── Cached: instant + mark hydration ── */
  useEffect(() => {
    if (runMode !== "cached") return;
    doneRef.current();
  }, [runMode, dest.id]);

  /* ── Demo: staged timers ── */
  useEffect(() => {
    if (runMode !== "demo") return;
    setDemoFlightReady(!flights.length);
    setDemoHotelsReady(0);
    demoTimers.current.forEach((id) => window.clearTimeout(id));
    demoTimers.current = [];

    const markDone = () => doneRef.current();

    if (flights.length) {
      demoTimers.current.push(window.setTimeout(() => setDemoFlightReady(true), 800));
    } else {
      setDemoFlightReady(true);
    }

    const hotelBase = flights.length ? 1600 : 800;
    hotels.forEach((_, i) => {
      const delay = hotelBase + i * 1200;
      demoTimers.current.push(
        window.setTimeout(() => {
          setDemoHotelsReady((n) => Math.max(n, i + 1));
          if (i === hotels.length - 1) markDone();
        }, delay),
      );
    });

    if (hotels.length === 0) {
      const d = flights.length ? 800 : 0;
      demoTimers.current.push(window.setTimeout(() => markDone(), d));
    }

    return () => {
      demoTimers.current.forEach((id) => window.clearTimeout(id));
      demoTimers.current = [];
    };
  }, [runMode, dest.id, flights.length, hotels.length]);

  /* ── Live: prepare criteria → flight → each hotel sequentially ── */
  useEffect(() => {
    if (runMode !== "live" || !sessionId) return;

    let cancelled = false;
    const ac = new AbortController();
    livePipelineGen.current += 1;
    const gen = livePipelineGen.current;

    const mergeOutboundFlight = (flight: FlightOption) => {
      if (gen !== livePipelineGen.current) return;
      const incoming = { ...flight, leg: flight.leg ?? ("outbound" as const), isPlaceholder: false };
      setDestinations((prev) =>
        prev.map((d) => {
          if (d.id !== dest.id) return d;
          const nextFlights = [...(d.flights || [])];
          const placeholder: FlightOption = {
            airline: "",
            route: "",
            duration: "",
            price: "",
            best: true,
            leg: "outbound",
            isPlaceholder: true,
          };
          if (nextFlights.length) {
            nextFlights[0] = mergeFlightFromSearch(nextFlights[0], incoming);
          } else {
            nextFlights.push(mergeFlightFromSearch(placeholder, incoming));
          }
          return { ...d, flights: nextFlights, liveBookingEstimates: true };
        }),
      );
    };

    const mergeReturnFlight = (flight: FlightOption) => {
      if (gen !== livePipelineGen.current) return;
      const incoming = { ...flight, leg: "return" as const, isPlaceholder: false };
      setDestinations((prev) =>
        prev.map((d) => {
          if (d.id !== dest.id) return d;
          const withoutRet = [...(d.flights || [])].filter((f) => f.leg !== "return");
          const prevReturn = (d.flights || []).find((f) => f.leg === "return");
          const placeholder: FlightOption = {
            airline: "",
            route: "",
            duration: "",
            price: "",
            best: true,
            leg: "return",
            isPlaceholder: true,
          };
          const row = mergeFlightFromSearch(prevReturn ?? placeholder, incoming);
          return { ...d, flights: [...withoutRet, row], liveBookingEstimates: true };
        }),
      );
    };

    const mergeFlightAtIndex = (index: number, flight: FlightOption, leg: FlightOption["leg"]) => {
      if (gen !== livePipelineGen.current) return;
      const incoming = { ...flight, leg, isPlaceholder: false };
      setDestinations((prev) =>
        prev.map((d) => {
          if (d.id !== dest.id) return d;
          const nextFlights = [...(d.flights || [])];
          while (nextFlights.length <= index) {
            nextFlights.push({
              airline: "Searching Expedia flights…",
              route: "…",
              duration: "…",
              price: "…",
              best: true,
              leg: "outbound",
              isPlaceholder: true,
            });
          }
          nextFlights[index] = mergeFlightFromSearch(nextFlights[index], incoming);
          return { ...d, flights: nextFlights, liveBookingEstimates: true };
        }),
      );
    };

    const mergeHotel = (index: number, hotel: HotelOption) => {
      if (gen !== livePipelineGen.current) return;
      setDestinations((prev) =>
        prev.map((d) => {
          if (d.id !== dest.id) return d;
          const nextHotels = [...(d.hotels || [])];
          if (index < 0 || index >= nextHotels.length) return { ...d, liveBookingEstimates: true };
          const prevRank = nextHotels[index].selected_rank;
          nextHotels[index] = {
            ...nextHotels[index],
            ...hotel,
            best: true,
            listing_url: hotel.listing_url || nextHotels[index].listing_url,
            ranked_picks: hotel.ranked_picks ?? nextHotels[index].ranked_picks,
            selected_rank: hotel.selected_rank ?? prevRank ?? 1,
            isPlaceholder: false,
          };
          return { ...d, hotels: nextHotels, liveBookingEstimates: true };
        }),
      );
    };

    (async () => {
      setPrepareError(null);
      setFlightStepError(null);
      setHotelStepErrors({});
      setCriteriaLines([]);
      setPreparePending(true);
      setFlightPhase("idle");
      setReturnFlightPhase("na");
      setReturnFlightError(null);
      setLiveSkipFlights(false);
      setBookingStatus("Summarizing trip criteria…");
      setProgressSteps([]);
      setStreamPreview("");
      streamDraftsRef.current = { assistant: { text: "" }, thinking: { text: "" } };
      setCriteriaExpanded(false);
      setHotelPhases(hotels.map(() => "pending"));

      try {
        const dateHintsPayload: BookingDateHints = {
          ...dateHints,
          adults: adultsFromProfileForBooking(profile),
        };
        const pillsPayload = profilePillsForBookingApi(profile);
        const prep = await bookingSearchPrepareStream(
          {
            session_id: sessionId,
            destination_id: dest.id,
            destination: destinationForBookingApi(dest),
            date_hints: dateHintsPayload,
            profile_pills: pillsPayload,
          },
          pushBookingEvent,
          { signal: ac.signal },
        );
        if (cancelled || gen !== livePipelineGen.current) return;
        setCriteriaLines(prep.criteria_lines || []);
        const pax = typeof prep.adults === "number" && prep.adults >= 1 ? prep.adults : dateHintsPayload.adults ?? 2;
        setPrepPax(pax);
        setPreparePending(false);
        const skipFlights = prep.include_flight_search === false;
        setLiveSkipFlights(skipFlights);
        const multiDayStay = !!(prep.start_date && prep.end_date && prep.start_date < prep.end_date);
        const structuredSegments = commercialFlightSegmentsFromDest(dest, profile);
        const useStructuredFlights = !skipFlights && structuredSegments.length > 0;
        const wantsReturnFlightSearch = !skipFlights && !useStructuredFlights && multiDayStay;
        setReturnFlightPhase(wantsReturnFlightSearch ? "pending" : "na");

        const planHotels = prep.hotels_for_session;
        const nStays =
          typeof prep.hotel_count === "number" && prep.hotel_count >= 0
            ? prep.hotel_count
            : Array.isArray(planHotels)
              ? planHotels.length
              : hotels.length;
        const fromLabel = profile.find((p) => p.key === "from")?.value?.trim() || "Home";
        const toLabel = [dest.name, dest.country].filter(Boolean).join(", ") || dest.name || "Destination";
        const outboundDate = prep.start_date || dateHintsPayload.start_iso || "";
        const returnDate = prep.end_date || dateHintsPayload.end_iso || "";
        const segmentPlaceholders: FlightOption[] = useStructuredFlights
          ? structuredSegments.map((seg) => ({
              airline: "Searching Expedia flights…",
              route: `${seg.fromLabel} → ${seg.toLabel}${outboundDate ? ` · ${isoShort(outboundDate)}` : ""}`,
              duration: "Checking routes…",
              price: "Searching Expedia…",
              best: true,
              leg: seg.role === "return" ? ("return" as const) : ("outbound" as const),
              isPlaceholder: true,
            }))
          : [];
        const outboundPlaceholder: FlightOption = {
          airline: "Searching Expedia flights…",
          route: `${fromLabel} → ${toLabel}${outboundDate ? ` · ${isoShort(outboundDate)}` : ""}`,
          duration: "Checking routes…",
          price: "Searching Expedia…",
          best: true,
          leg: "outbound",
          isPlaceholder: true,
        };
        const returnPlaceholder: FlightOption | null = wantsReturnFlightSearch
          ? {
              airline: "Searching Expedia flights…",
              route: `${toLabel} → ${fromLabel}${returnDate ? ` · ${isoShort(returnDate)}` : ""}`,
              duration: "Checking routes…",
              price: "Searching Expedia…",
              best: true,
              leg: "return",
              isPlaceholder: true,
            }
          : null;
        const placeholderHotels = buildPlannedHotelRows(
          (Array.isArray(planHotels) ? (planHotels as unknown as HotelOption[]) : hotels).slice(0, nStays),
          prep.start_date || dateHintsPayload.start_iso,
        );
        if (Array.isArray(planHotels) && planHotels.length > 0) {
          setHotelPhases(planHotels.map(() => "pending"));
        } else {
          setHotelPhases(Array.from({ length: nStays }, () => "pending"));
        }
        setDestinations((prev) => {
          const next = prev.map((d) => {
            if (d.id !== dest.id) return d;
            const mergedHotels = mergePlaceholderHotelsKeepPersisted(placeholderHotels, d.hotels, -1);
            if (skipFlights) {
              return { ...d, hotels: mergedHotels, flights: [] };
            }
            return {
              ...d,
              hotels: mergedHotels,
              flights: useStructuredFlights
                ? segmentPlaceholders
                : returnPlaceholder
                  ? [outboundPlaceholder, returnPlaceholder]
                  : [outboundPlaceholder],
            };
          });
          beginTripDiskSave();
          void syncUi(sessionId, next as unknown[], {
            tripId: wanderlyTripId || undefined,
            profilePills: profile,
          })
            .then((r) => completeTripDiskSave(next as DestCard[], { savedToDisk: Boolean(r.saved_to_disk) }))
            .catch(() => failTripDiskSave());
          return next;
        });

        console.info("[wanderly:booking_search_prepare]", {
          destination_id: dest.id,
          start_date: prep.start_date,
          end_date: prep.end_date,
          hotel_count: prep.hotel_count,
          adults: prep.adults,
          outline: prep.booking_outline_source,
          stays: nStays,
          include_flight_search: prep.include_flight_search,
        });

        let fr: Awaited<ReturnType<typeof bookingSearchStepStream>> | null = null;
        if (!skipFlights) {
          if (useStructuredFlights) {
            for (let si = 0; si < structuredSegments.length; si++) {
              if (cancelled || gen !== livePipelineGen.current) return;
              setBookingStatus(
                structuredSegments.length > 1
                  ? `Searching flight ${si + 1} of ${structuredSegments.length}…`
                  : "Searching flights…",
              );
              setBusyFlightSegmentIndex(si);
              setFlightPhase("loading");
              const segFr = await bookingSearchStepStream(
                {
                  session_id: sessionId,
                  destination_id: dest.id,
                  destination: destinationForBookingApi(dest),
                  step: "flight",
                  flight_segment_index: si,
                  date_hints: dateHintsPayload,
                  profile_pills: pillsPayload,
                },
                pushBookingEvent,
              );
              fr = segFr;
              if (cancelled || gen !== livePipelineGen.current) return;
              const segLeg =
                structuredSegments[si]?.role === "return"
                  ? ("return" as const)
                  : structuredSegments[si]?.role === "within_trip"
                    ? ("outbound" as const)
                    : si === 0
                      ? ("outbound" as const)
                      : ("return" as const);
              if (segFr.ok && segFr.flight) {
                mergeFlightAtIndex(si, segFr.flight, segLeg);
                setFlightStepError(null);
              } else if (!segFr.ok) {
                setBusyFlightSegmentIndex(null);
                setFlightPhase("error");
                setFlightStepError(segFr.error || "Flight search did not return a result.");
                break;
              }
            }
            if (gen === livePipelineGen.current) {
              setBusyFlightSegmentIndex(null);
              setFlightPhase("done");
            }
          } else {
            setBookingStatus("Searching outbound flights…");
            setBusyFlightSegmentIndex(0);
            setFlightPhase("loading");
            fr = await bookingSearchStepStream(
              {
                session_id: sessionId,
                destination_id: dest.id,
                destination: destinationForBookingApi(dest),
                step: "flight",
                flight_leg: "outbound",
                date_hints: dateHintsPayload,
                profile_pills: pillsPayload,
              },
              pushBookingEvent,
            );
            if (cancelled || gen !== livePipelineGen.current) return;
            console.info("[wanderly:booking_search_step]", {
              destination_id: dest.id,
              step: "flight",
              ok: fr.ok,
              search_url: fr.search_url,
              error: fr.error,
              flight: fr.flight,
              debug_parse: fr.debug_parse,
            });
            if (fr.ok && fr.flight) {
              mergeOutboundFlight({
                ...fr.flight,
                leg: fr.flight_leg === "return" ? "return" : fr.flight.leg ?? "outbound",
              });
              if (gen !== livePipelineGen.current) return;
              setBusyFlightSegmentIndex(null);
              setFlightPhase("done");
              setFlightStepError(null);
            } else {
              if (gen !== livePipelineGen.current) return;
              setBusyFlightSegmentIndex(null);
              setFlightPhase("error");
              setFlightStepError(fr.error || "Flight search did not return a result.");
            }

            if (multiDayStay && !fr.ok) {
              setReturnFlightPhase("na");
            }
          }
        } else {
          setFlightPhase("done");
          setFlightStepError(null);
        }

        setBookingStatus(nStays ? "Searching accommodations…" : null);

        for (let i = 0; i < nStays; i++) {
          if (cancelled || gen !== livePipelineGen.current) return;
          setBookingStatus(
            nStays > 1 ? `Searching accommodations (${i + 1} of ${nStays})…` : "Searching accommodations…",
          );
          setHotelPhases((prev) => {
            const next = [...prev];
            if (next[i] !== undefined) next[i] = "loading";
            return next;
          });
          const hr = await bookingSearchStepStream(
            {
              session_id: sessionId,
              destination_id: dest.id,
              destination: destinationForBookingApi(dest),
              step: "hotel",
              hotel_index: i,
              date_hints: dateHintsPayload,
              profile_pills: pillsPayload,
            },
            pushBookingEvent,
          );
          if (cancelled || gen !== livePipelineGen.current) return;
          console.info("[wanderly:booking_search_step]", {
            destination_id: dest.id,
            step: "hotel",
            hotel_index: i,
            ok: hr.ok,
            search_url: hr.search_url,
            error: hr.error,
            hotel: hr.hotel,
            debug_parse: hr.debug_parse,
          });
          if (hr.ok && hr.hotel) {
            const candidate = { ...hr.hotel, isPlaceholder: false as const };
            if (hotelRowIsActionableHotel(candidate)) {
              mergeHotel(i, hr.hotel);
              setHotelPhases((prev) => {
                const next = [...prev];
                if (next[i] !== undefined) next[i] = "done";
                return next;
              });
            } else {
              setHotelStepErrors((prev) => ({
                ...prev,
                [i]:
                  hr.error ||
                  "Expedia did not return a complete hotel listing for this stay. Expand search criteria or try again.",
              }));
              setHotelPhases((prev) => {
                const next = [...prev];
                if (next[i] !== undefined) next[i] = "error";
                return next;
              });
            }
          } else {
            if (gen !== livePipelineGen.current) return;
            setHotelStepErrors((prev) => ({
              ...prev,
              [i]: hr.error || "Hotel search failed.",
            }));
            setHotelPhases((prev) => {
              const next = [...prev];
              if (next[i] !== undefined) next[i] = "error";
              return next;
            });
          }
        }

        if (!skipFlights && !useStructuredFlights && multiDayStay && fr && fr.ok && fr.flight) {
          setReturnFlightPhase("loading");
          setBusyFlightSegmentIndex(null);
          setBookingStatus("Searching return flights…");
          const rr = await bookingSearchStepStream(
            {
              session_id: sessionId,
              destination_id: dest.id,
              destination: destinationForBookingApi(dest),
              step: "flight",
              flight_leg: "return",
              date_hints: dateHintsPayload,
              profile_pills: pillsPayload,
            },
            pushBookingEvent,
          );
          if (cancelled || gen !== livePipelineGen.current) return;
          console.info("[wanderly:booking_search_step]", {
            destination_id: dest.id,
            step: "flight",
            flight_leg: "return",
            ok: rr.ok,
            search_url: rr.search_url,
            error: rr.error,
            flight: rr.flight,
          });
          if (rr.ok && rr.flight) {
            mergeReturnFlight({ ...rr.flight, leg: "return" });
            setReturnFlightPhase("done");
            setReturnFlightError(null);
          } else {
            setReturnFlightPhase("error");
            setReturnFlightError(rr.error || "Return flight search did not return a result.");
          }
        }

        if (!cancelled && gen === livePipelineGen.current) {
          setBookingStatus(null);
          setStreamPreview("");
          setProgressSteps([]);
          streamDraftsRef.current = { assistant: { text: "" }, thinking: { text: "" } };
        }
      } catch (e: unknown) {
        if (!cancelled && gen === livePipelineGen.current) {
          if (e instanceof Error && e.name === "AbortError") {
            /* React Strict Mode or navigation cancelled the prepare request */
          } else {
            const msg = e instanceof Error ? e.message : String(e);
            setPrepareError(msg);
            setCriteriaExpanded(true);
            setPreparePending(false);
            setFlightPhase((p) => (p === "idle" ? "error" : p));
          }
        }
      } finally {
        if (!cancelled && gen === livePipelineGen.current) doneRef.current();
      }
    })();

    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [runMode, sessionId, dest.id, profileSig, dateHintsKey, setDestinations]);

  const structuredFlightSegments = useMemo(
    () => commercialFlightSegmentsFromDest(dest, profile),
    [dest.id, dest.transport_plan, dest.booking_hooks, profileSig],
  );
  const multiSegmentFlights = structuredFlightSegments.length > 1;
  const extraSegmentFlights = useMemo(
    () => (multiSegmentFlights ? flights.slice(1, -1) : []),
    [flights, multiSegmentFlights],
  );
  const groundTransportNote = useMemo(() => {
    if (!liveSkipFlights) return null;
    return transportPlanSummary(dest);
  }, [liveSkipFlights, dest.transport_plan, dest.booking_hooks]);
  const outboundFlight = useMemo(
    () => flights.find((f) => f.leg !== "return") ?? flights[0],
    [flights],
  );
  const returnFlight = useMemo(() => flights.find((f) => f.leg === "return"), [flights]);
  const outboundFlightIndex = useMemo(() => {
    const ix = flights.findIndex((f) => f.leg !== "return");
    return ix >= 0 ? ix : 0;
  }, [flights]);
  const returnFlightIndex = useMemo(() => flights.findIndex((f) => f.leg === "return"), [flights]);

  const outboundFlightResolved =
    runMode !== "live" ||
    Boolean(prepareError) ||
    flightPhase === "done" ||
    flightPhase === "error";

  const returnFlightResolved =
    runMode !== "live" ||
    returnFlightPhase === "na" ||
    returnFlightPhase === "done" ||
    returnFlightPhase === "error";

  const flightResolved =
    runMode === "cached" || runMode === "demo"
      ? runMode === "demo"
        ? demoFlightReady || !flights.length
        : true
      : (flightPhase === "done" || flightPhase === "error" || Boolean(prepareError)) &&
        returnFlightResolved;

  const showFlightSkeleton =
    runMode === "demo"
      ? flights.length > 0 && !demoFlightReady
      : runMode === "live" &&
          prepareError === null &&
          !preparePending &&
          flightPhase === "loading" &&
          !outboundFlight?.isPlaceholder;

  const showReturnSkeleton =
    runMode === "live" &&
    prepareError === null &&
    returnFlightPhase === "loading" &&
    !returnFlight?.isPlaceholder;

  const showReturnFlightCard =
    runMode === "live" &&
    returnFlightPhase !== "na" &&
    Boolean(returnFlight);

  const showReturnFlightError = runMode === "live" && returnFlightPhase === "error" && Boolean(returnFlightError);

  const showReturnTimeline =
    !multiSegmentFlights && ((runMode === "live" && returnFlightPhase !== "na") || Boolean(returnFlight));

  const returnSearchSettled =
    returnFlightPhase === "na" || returnFlightPhase === "done" || returnFlightPhase === "error";

  const showFlightCard =
    outboundFlight &&
    (runMode === "demo"
      ? demoFlightReady || !flights.length
      : runMode === "cached"
        ? true
        : Boolean(prepareError) ||
          flightPhase === "loading" ||
          flightPhase === "done" ||
          flightPhase === "error");

  const showHotelRow = (idx: number) => {
    if (runMode === "demo") return true;
    if (runMode === "cached") return true;
    if (prepareError) return true;
    if (runMode === "live" && preparePending && !prepareError) return false;
    // In live mode, render the full planned lodging path immediately.
    // Placeholder rows are prebuilt from itinerary and later replaced in-place by real results.
    if (hotels[idx]?.isPlaceholder) return true;
    if (!outboundFlightResolved) return false;
    if (idx === 0) return true;
    const p = hotelPhases[idx - 1];
    return p === "done" || p === "error";
  };

  const showHotelSkeleton = (idx: number) => {
    if (runMode === "demo") return demoHotelsReady <= idx;
    if (runMode === "cached") return false;
    if (prepareError) return false;
    const hp = hotelPhases[idx];
    return (hp === "pending" || hp === "loading") && !hotels[idx]?.isPlaceholder;
  };

  const showHotelCard = (idx: number) => {
    if (runMode === "demo") return demoHotelsReady > idx;
    if (runMode === "cached") return true;
    if (prepareError) return true;
    const hp = hotelPhases[idx];
    return hp === "pending" || hp === "loading" || hp === "done" || hp === "error";
  };

  const showFlightSection =
    (flights.length > 0 || runMode === "live" || runMode === "demo") &&
    !(runMode === "live" && liveSkipFlights) &&
    /* No flight / hotel timeline chrome until prepare returns — avoids empty plane icons during criteria summary. */
    !(runMode === "live" && preparePending && !prepareError);

  /** Hide the vertical spine and timeline nodes while live criteria prepare is in flight (cleaner first paint). */
  const hideLivePrepareTimelineChrome = runMode === "live" && preparePending && !prepareError;
  const showCriteriaBlock = runMode === "live" && sessionId;

  const livePipelineIdle =
    runMode !== "live" ||
    (!preparePending &&
      flightPhase !== "loading" &&
      returnSearchSettled &&
      hotelPhases.every((p) => p !== "pending" && p !== "loading"));

  const progressPhase = useMemo((): BookingProgressPhase | null => {
    if (runMode !== "live" || livePipelineIdle) return null;
    if (preparePending && !prepareError) return "prepare";
    if (returnFlightPhase === "loading") return "return_flight";
    if (flightPhase === "loading") return "flight";
    if (hotelPhases.some((p) => p === "loading" || p === "pending")) return "hotel";
    return null;
  }, [
    runMode,
    livePipelineIdle,
    preparePending,
    prepareError,
    returnFlightPhase,
    flightPhase,
    hotelPhases,
  ]);

  const rotatingProgress = useRotatingProgressMessage(
    progressPhase ? BOOKING_PROGRESS_MESSAGES[progressPhase] : [],
    progressPhase !== null,
  );

  const activeProgressLabel = bookingStatus || streamPreview || rotatingProgress;

  const hotelProgressHint = (idx: number) => {
    if (bookingStatus && hotelPhases[idx] === "loading") return bookingStatus;
    if (hotelPhases[idx] === "loading" || hotelPhases[idx] === "pending") {
      return rotatingProgress || "Searching accommodations…";
    }
    return activeProgressLabel;
  };

  const progressCardProps = {
    primary: activeProgressLabel || "Searching live prices…",
    streamPreview,
    steps: progressSteps,
  };

  const showOutboundFlightProgress =
    runMode === "live" &&
    prepareError === null &&
    !preparePending &&
    !flightRowIsPersistedSearchMerge(outboundFlight) &&
    (flightPhase === "loading" ||
      (multiSegmentFlights && busyFlightSegmentIndex === outboundFlightIndex));

  const showSegmentFlightProgress = (segmentListIndex: number) => {
    const flightIndex = segmentListIndex + 1;
    if (runMode !== "live" || prepareError || preparePending) return false;
    const f = flights[flightIndex];
    if (flightRowIsPersistedSearchMerge(f)) return false;
    if (multiSegmentFlights) return busyFlightSegmentIndex === flightIndex;
    return false;
  };

  const showSegmentFlightWait = (segmentListIndex: number) => {
    const flightIndex = segmentListIndex + 1;
    if (runMode !== "live" || prepareError || preparePending) return false;
    const f = flights[flightIndex];
    if (!f?.isPlaceholder) return false;
    return busyFlightSegmentIndex !== null && busyFlightSegmentIndex < flightIndex;
  };

  const showReturnFlightProgress =
    runMode === "live" &&
    prepareError === null &&
    !flightRowIsPersistedSearchMerge(returnFlight) &&
    returnFlightPhase === "loading";

  const showHotelProgressCard = (idx: number) => {
    if (runMode !== "live" || prepareError) return false;
    if (hotelRowIsPersistedSearchMerge(hotels[idx])) return false;
    return hotelPhases[idx] === "loading";
  };

  const showHotelPendingWait = (idx: number) =>
    runMode === "live" &&
    !prepareError &&
    hotelPhases[idx] === "pending" &&
    Boolean(hotels[idx]?.isPlaceholder);

  const demoTimelineSettled =
    flightResolved &&
    (hotels.length === 0 || demoHotelsReady >= hotels.length);

  const showCostBreakdownPanel = useMemo(() => {
    if (runMode === "live") {
      if (!livePipelineIdle) return false;
      if (!dest.liveBookingEstimates) return Boolean(dest.flights?.length || dest.hotels?.length);
      return liveBookingExtractLooksActionable(dest);
    }
    if (runMode === "demo") {
      if (!demoTimelineSettled) return false;
      return Boolean(dest.flights?.length || dest.hotels?.length);
    }
    if (!dest.liveBookingEstimates) return Boolean(dest.flights?.length || dest.hotels?.length);
    return liveBookingExtractLooksActionable(dest);
  }, [
    runMode,
    livePipelineIdle,
    demoTimelineSettled,
    dest.liveBookingEstimates,
    dest.flights,
    dest.hotels,
    flightResolved,
    demoHotelsReady,
    hotels.length,
  ]);

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
      <div className="relative lg:col-span-2">
        {!hideLivePrepareTimelineChrome ? (
          <div
            className="pointer-events-none absolute bottom-0 left-9 top-8 w-px bg-gradient-to-b from-indigo-500/40 via-purple-500/40 to-indigo-500/40"
            aria-hidden
          />
        ) : null}
        <div className="relative space-y-6 pl-12">
          <div className="mb-2 flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2">
            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" aria-hidden />
            <div>
              <p className="text-[12px] font-medium text-emerald-200/95">Booking Details</p>
              <p className="text-[10px] text-emerald-300/70">Review flights &amp; accommodations</p>
            </div>
          </div>

          {showCriteriaBlock ? (
            <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-[10px] uppercase tracking-wider text-white/35">Search criteria</p>
                <button
                  type="button"
                  onClick={() => setCriteriaExpanded((v) => !v)}
                  className="text-[10px] text-white/45 underline decoration-white/25 underline-offset-2 hover:text-white/70"
                >
                  {criteriaExpanded ? "Collapse" : "Expand"}
                </button>
              </div>
              {bookingStatus ? (
                <p className="mt-2 text-[12px] text-sky-300/85">{bookingStatus}</p>
              ) : null}
              {criteriaExpanded ? (
                preparePending && !prepareError ? (
                  <div className="mt-2">
                    <BookingSearchProgressCard
                      primary={activeProgressLabel || "Summarizing what we'll look up…"}
                      streamPreview={streamPreview}
                      steps={progressSteps}
                    />
                  </div>
                ) : prepareError ? (
                  <p className="mt-2 text-[12px] text-rose-300/90">{prepareError}</p>
                ) : criteriaLines.length ? (
                  <ul className="mt-2 list-disc space-y-1 pl-4 text-[12px] leading-snug text-white/70">
                    {criteriaLines.map((line, i) => (
                      <li key={i} className={line.startsWith("  ") ? "list-none -translate-x-4 text-white/55" : ""}>
                        {line.trimStart()}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-2 text-[12px] text-white/45">No criteria returned.</p>
                )
              ) : preparePending && !prepareError ? (
                <div className="mt-2">
                  <BookingSearchProgressCard
                    primary={activeProgressLabel || "Summarizing what we'll look up…"}
                    streamPreview={streamPreview}
                    steps={progressSteps}
                  />
                </div>
              ) : (
                <p className="mt-2 text-[11px] text-white/45">
                  {prepareError
                    ? "Search summary has an error. Expand to view details."
                    : criteriaLines.length
                      ? "Using itinerary-based placeholder path; expand for full summary."
                      : "Summary hidden while we build the booking path."}
                </p>
              )}
            </div>
          ) : null}

          {groundTransportNote ? (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-3"
            >
              <p className="text-[10px] uppercase tracking-wider text-white/35">Ground transport</p>
              <p className="mt-1 text-[12px] leading-snug text-white/70">{groundTransportNote}</p>
              <p className="mt-1 text-[11px] text-white/45">No commercial flight search for this trip.</p>
            </motion.div>
          ) : null}

          {/* Flight event */}
          {showFlightSection && (
            <div className="relative">
              <div className="absolute -left-8 top-1 flex h-10 w-10 items-center justify-center rounded-full border border-blue-500/40 bg-blue-500/20">
                <Plane className="h-3.5 w-3.5 text-blue-300" aria-hidden />
              </div>
              <p className="text-[11px] uppercase tracking-wider text-white/35">Depart</p>
              <div className="mt-2">
                <AnimatePresence mode="wait">
                  {runMode === "demo" && showFlightSkeleton ? (
                    <motion.div key="sk" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                      <FlightLoadSkeleton hints={BOOKING_PROGRESS_MESSAGES.flight} />
                    </motion.div>
                  ) : showOutboundFlightProgress ? (
                    <motion.div key="fp" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                      {flightStepError ? (
                        <p className="mb-2 text-[11px] text-amber-300/85">{flightStepError}</p>
                      ) : null}
                      <BookingSearchProgressCard {...progressCardProps} />
                    </motion.div>
                  ) : preparePending && runMode === "live" && !prepareError ? (
                    <p key="wait" className="text-[11px] text-white/35">
                      Flight search runs as soon as the summary above is ready.
                    </p>
                  ) : showFlightCard &&
                    outboundFlight &&
                    (flightRowIsPersistedSearchMerge(outboundFlight) || !outboundFlight.isPlaceholder) ? (
                    <motion.div
                      key="fc"
                      initial={{ opacity: 0, rotateY: -12 }}
                      animate={{ opacity: 1, rotateY: 0 }}
                      transition={{ duration: 0.35 }}
                    >
                      {flightStepError ? (
                        <p className="mb-2 text-[11px] text-amber-300/85">{flightStepError}</p>
                      ) : null}
                      <DetailedFlightCard
                        flight={outboundFlight}
                        onClick={() => console.info("[booking] flight select outbound", outboundFlight.airline)}
                        onSelectRank={(rank) => selectFlightRank(outboundFlightIndex, rank)}
                      />
                    </motion.div>
                  ) : runMode === "live" &&
                    outboundFlight?.isPlaceholder &&
                    flightPhase !== "loading" &&
                    busyFlightSegmentIndex !== outboundFlightIndex ? (
                    <p key="flight-wait" className="text-[11px] text-white/38">
                      This flight runs after earlier legs finish.
                    </p>
                  ) : runMode === "live" && !outboundFlight && (flightPhase === "error" || prepareError) ? (
                    <p className="text-[12px] text-amber-300/85">
                      {prepareError || flightStepError || "Flight search unavailable."}
                    </p>
                  ) : null}
                </AnimatePresence>
              </div>
            </div>
          )}

          {extraSegmentFlights.map((segFlight, si) => (
            <div key={`seg-${si + 1}`} className="relative">
              <motion.div
                initial={{ opacity: 0, rotateY: -12 }}
                animate={{ opacity: 1, rotateY: 0 }}
                transition={{ duration: 0.35 }}
              >
                <motion.div className="absolute -left-8 top-1 flex h-10 w-10 items-center justify-center rounded-full border border-blue-500/40 bg-blue-500/20">
                  <Plane className="h-3.5 w-3.5 text-blue-300" aria-hidden />
                </motion.div>
                <p className="text-[11px] uppercase tracking-wider text-white/35">
                  {structuredFlightSegments[si + 1]?.role === "within_trip" ? "Connect" : "Flight"}
                </p>
                <div className="mt-2">
                  {showSegmentFlightProgress(si) ? (
                    <BookingSearchProgressCard {...progressCardProps} />
                  ) : showSegmentFlightWait(si) ? (
                    <p className="text-[11px] text-white/38">This flight runs after earlier legs finish.</p>
                  ) : flightRowIsPersistedSearchMerge(segFlight) || !segFlight.isPlaceholder ? (
                    <DetailedFlightCard
                      flight={segFlight}
                      onClick={() => console.info("[booking] flight select segment", segFlight.airline)}
                      onSelectRank={(rank) => selectFlightRank(si + 1, rank)}
                    />
                  ) : null}
                </div>
              </motion.div>
            </div>
          ))}

          {/* Map pin: arrival before stays */}
          {hotels.length > 0 && outboundFlightResolved && (
            <div className="relative">
              <div className="absolute -left-8 top-1 flex h-10 w-10 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.06]">
                <MapPin className="h-3.5 w-3.5 text-white/50" aria-hidden />
              </div>
              <p className="text-[11px] uppercase tracking-wider text-white/35">Arrive</p>
            </div>
          )}

          {hotels.map((h, idx) =>
            showHotelRow(idx) ? (
              <div key={`${h.name}-${idx}`} className="relative">
                <div className="absolute -left-8 top-1 flex h-10 w-10 items-center justify-center rounded-full border border-purple-500/40 bg-purple-500/20">
                  <Hotel className="h-3.5 w-3.5 text-purple-200" aria-hidden />
                </div>
                <p className="text-[11px] uppercase tracking-wider text-white/35">Stay · {dedupeMiddleDotSegments(h.area)}</p>
                <div className="mt-2">
                  <AnimatePresence mode="wait">
                    {runMode === "demo" && showHotelSkeleton(idx) ? (
                      <motion.div
                        key={`sk-${idx}`}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                      >
                        <HotelLoadSkeleton
                          label={hotelProgressHint(idx) || "Searching accommodations…"}
                          hints={BOOKING_PROGRESS_MESSAGES.hotel}
                        />
                      </motion.div>
                    ) : showHotelProgressCard(idx) ? (
                      <motion.div
                        key={`hp-${idx}`}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                      >
                        {hotelStepErrors[idx] ? (
                          <p className="mb-2 text-[11px] text-amber-300/85">{hotelStepErrors[idx]}</p>
                        ) : null}
                        <BookingSearchProgressCard
                          primary={hotelProgressHint(idx) || progressCardProps.primary}
                          streamPreview={streamPreview}
                          steps={progressSteps}
                        />
                      </motion.div>
                    ) : showHotelPendingWait(idx) ? (
                      <p key={`hw-${idx}`} className="text-[11px] text-white/38">
                        {idx === 0
                          ? "Accommodation search runs after outbound flights."
                          : "This stay runs after the previous accommodation search."}
                      </p>
                    ) : hotelPhases[idx] === "error" ||
                      (hotelPhases[idx] === "done" && !hotelRowIsActionableHotel(h)) ? (
                      <p key={`he-${idx}`} className="text-[12px] leading-snug text-amber-300/85">
                        {hotelStepErrors[idx] ||
                          "We could not load a complete hotel listing from Expedia for this stay."}
                      </p>
                    ) : hotelRowIsActionableHotel(h) ? (
                      <motion.div
                        key={`hc-${idx}`}
                        initial={{ opacity: 0, rotateY: -12 }}
                        animate={{ opacity: 1, rotateY: 0 }}
                        transition={{ duration: 0.35 }}
                      >
                        {hotelStepErrors[idx] ? (
                          <p className="mb-2 text-[11px] text-amber-300/85">{hotelStepErrors[idx]}</p>
                        ) : null}
                        <DetailedHotelCard
                          hotel={h}
                          onClick={() => console.info("[booking] hotel select", h.name)}
                          onSelectRank={(rank) => selectHotelRank(idx, rank)}
                        />
                      </motion.div>
                    ) : null}
                  </AnimatePresence>
                </div>
              </div>
            ) : null,
          )}

          {/* Return flight — after all stays (matches search order) */}
          {showFlightSection && showReturnTimeline ? (
            <div className="relative">
              <div className="absolute -left-8 top-1 flex h-10 w-10 items-center justify-center rounded-full border border-violet-500/40 bg-violet-500/20">
                <Plane className="h-3.5 w-3.5 rotate-180 text-violet-200" aria-hidden />
              </div>
              <p className="text-[11px] uppercase tracking-wider text-white/35">Return</p>
              <div className="mt-2">
                <AnimatePresence mode="wait">
                  {runMode === "demo" && showReturnSkeleton ? (
                    <motion.div key="r-sk" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                      <FlightLoadSkeleton
                        label="Searching return flights…"
                        hints={BOOKING_PROGRESS_MESSAGES.return_flight}
                      />
                    </motion.div>
                  ) : showReturnFlightProgress ? (
                    <motion.div key="r-fp" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                      <BookingSearchProgressCard
                        primary={bookingStatus || activeProgressLabel || "Searching return flights…"}
                        streamPreview={streamPreview}
                        steps={progressSteps}
                      />
                    </motion.div>
                  ) : showReturnFlightCard &&
                    returnFlight &&
                    (flightRowIsPersistedSearchMerge(returnFlight) || !returnFlight.isPlaceholder) ? (
                    <motion.div
                      key="r-fc"
                      initial={{ opacity: 0, rotateY: -12 }}
                      animate={{ opacity: 1, rotateY: 0 }}
                      transition={{ duration: 0.35 }}
                    >
                      <DetailedFlightCard
                        flight={returnFlight}
                        onClick={() => console.info("[booking] flight select return", returnFlight.airline)}
                        onSelectRank={
                          returnFlightIndex >= 0
                            ? (rank) => selectFlightRank(returnFlightIndex, rank)
                            : undefined
                        }
                      />
                    </motion.div>
                  ) : showReturnFlightError ? (
                    <p key="r-err" className="text-[12px] text-amber-300/85">
                      {returnFlightError}
                    </p>
                  ) : runMode === "live" && returnFlightPhase === "pending" ? (
                    <p key="r-wait" className="text-[11px] text-white/38">
                      {!outboundFlightResolved
                        ? "Return flight runs after outbound and your stays."
                        : "Return flight search runs after your accommodations."}
                    </p>
                  ) : runMode !== "live" && returnFlight ? (
                    <motion.div
                      key="r-cached"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ duration: 0.35 }}
                    >
                      <DetailedFlightCard
                        flight={returnFlight}
                        onClick={() => console.info("[booking] flight select return", returnFlight.airline)}
                        onSelectRank={
                          returnFlightIndex >= 0
                            ? (rank) => selectFlightRank(returnFlightIndex, rank)
                            : undefined
                        }
                      />
                    </motion.div>
                  ) : null}
                </AnimatePresence>
              </div>
            </div>
          ) : null}
        </div>
      </div>
      <div className="min-w-0">
        {showCostBreakdownPanel ? (
          <BookingCostBreakdown dest={dest} paxCount={prepPax} variant="timeline" />
        ) : (
          <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-4 lg:sticky lg:top-4">
            <p className="text-[11px] font-medium uppercase tracking-wider text-white/35">Cost breakdown</p>
            <p className="mt-2 text-[12px] leading-relaxed text-white/45">
              {runMode === "live" && !livePipelineIdle
                ? activeProgressLabel || "Fetching live prices from Expedia…"
                : "Totals appear once flight and hotel rows look like real search results (not heuristic placeholders). Use the browser console for each step’s search_url; on the server see Travel Planner Prototype/.cache/booking_search_trace.log (or BOOKING_SEARCH_TRACE_LOG)."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
