/**
 * Expanded-card booking experience (summary strips, flip back-face, timeline, cost).
 * Spec: TRAVEL_BOOKING_IMPLEMENTATION.md
 */
import { useEffect, useMemo, useRef, useState } from "react";
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
} from "lucide-react";
import type { DestCard, FlightOption, HotelOption, ActivityOption, ProfilePill } from "./TripStore";
import { useTrip } from "./TripStore";
import type { WanderlyProfilePillDTO } from "../../types/workspace";
import { ImageWithFallback } from "./figma/ImageWithFallback";
import {
  bookingSearchPrepare,
  bookingSearchStep,
  syncUi,
  type BookingDateHints,
} from "../../api/wanderly";

export function parsePriceDigits(s: string | undefined): number {
  const n = parseInt(String(s ?? "").replace(/[^\d]/g, ""), 10);
  return Number.isFinite(n) ? n : 0;
}

/**
 * True when live OTA merge looks like a real parse (not heuristic placeholders).
 * Used to avoid showing a cost breakdown on scraped noise.
 */
export function liveBookingExtractLooksActionable(dest: DestCard): boolean {
  const flights = dest.flights || [];
  const hotels = dest.hotels || [];
  if (!flights.length) return false;
  for (const f of flights) {
    const route = (f.route || "").trim();
    if (!route || /^tbd$/i.test(route)) return false;
    if (parsePriceDigits(f.price) <= 0) return false;
  }

  for (const h of hotels) {
    const nm = (h.name || "").trim().toLowerCase();
    if (nm === "lodging results" || nm === "hotel listing" || nm === "") return false;
    const pr = (h.price || "").trim();
    if (/^see expedia$/i.test(pr)) return false;
    if (parsePriceDigits(pr) <= 0) return false;
  }
  return true;
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

export function DetailedFlightCard({
  flight,
  isSelected,
  onClick,
}: {
  flight: FlightOption;
  isSelected?: boolean;
  onClick?: () => void;
}) {
  const searchHref = (flight.search_url || "").trim();
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
            <p className="text-[13px] font-medium text-white/90">{flight.airline}</p>
            {flight.isPlaceholder ? (
              <span className="rounded border border-white/20 bg-white/10 px-1.5 py-0.5 text-[10px] text-white/70">
                Planned
              </span>
            ) : null}
            {flight.leg === "return" ? (
              <span className="rounded border border-violet-500/25 bg-violet-500/15 px-1.5 py-0.5 text-[10px] text-violet-200/90">
                Return
              </span>
            ) : flight.leg === "outbound" ? (
              <span className="rounded border border-sky-500/25 bg-sky-500/15 px-1.5 py-0.5 text-[10px] text-sky-200/90">
                Outbound
              </span>
            ) : null}
            {flight.best ? (
              <span className="inline-flex items-center gap-0.5 rounded border border-amber-500/30 bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-200">
                <Star className="h-2.5 w-2.5" />
                Best value
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 text-[12px] text-white/50">{flight.route}</p>
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className="flex items-center gap-1 text-[11px] text-white/40">
              <Clock className="h-3 w-3" aria-hidden />
              {flight.duration}
            </span>
            <span className="text-[16px] font-medium tabular-nums text-white/90">{flight.price}</span>
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
    {flight.round_trip ? (
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
}: {
  hotel: HotelOption;
  isSelected?: boolean;
  onClick?: () => void;
}) {
  const listingHref = (hotel.listing_url || "").trim();
  const per = parsePriceDigits(hotel.price);
  const total = per * (hotel.nights || 0);
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
          src={hotel.image}
          alt={hotel.name}
          className="h-20 w-20 shrink-0 rounded-lg object-cover"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <p className="text-[13px] font-medium text-white/90">{hotel.name}</p>
            {hotel.isPlaceholder ? (
              <span className="rounded border border-white/20 bg-white/10 px-1.5 py-0.5 text-[10px] text-white/70">
                Planned
              </span>
            ) : null}
            {hotel.best ? (
              <span className="rounded border border-amber-500/30 bg-amber-500/20 p-1 text-amber-300">
                <Star className="h-3 w-3" aria-hidden />
              </span>
            ) : null}
          </div>
          <p className="text-[11px] text-white/45">{hotel.area}</p>
          <p className="mt-1 flex items-center gap-1 text-[11px] text-white/40">
            <Calendar className="h-3 w-3 shrink-0" aria-hidden />
            {hotel.nights} nights × {hotel.price}
          </p>
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

function FlightLoadSkeleton({ label = "Searching flights…" }: { label?: string }) {
  return (
    <div className="animate-pulse rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
      <div className="mb-3 flex items-center gap-3">
        <div className="h-10 w-10 rounded-lg bg-white/[0.06]" />
        <div className="flex-1 space-y-2">
          <div className="h-3 w-24 rounded bg-white/[0.06]" />
          <div className="h-2 w-32 rounded bg-white/[0.06]" />
        </div>
      </div>
      <p className="text-center text-[11px] text-white/30">{label}</p>
    </div>
  );
}

function HotelLoadSkeleton({ label }: { label: string }) {
  return (
    <div className="animate-pulse rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
      <div className="mb-3 flex items-center gap-3">
        <div className="h-16 w-16 shrink-0 rounded-lg bg-white/[0.06]" />
        <div className="flex-1 space-y-2">
          <div className="h-3 w-28 rounded bg-white/[0.06]" />
          <div className="h-2 w-36 rounded bg-white/[0.06]" />
        </div>
      </div>
      <p className="text-center text-[11px] text-white/30">{label}</p>
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

function buildPlannedHotelRows(
  sourceHotels: HotelOption[] | undefined,
  startIso: string | null | undefined,
): HotelOption[] {
  const rows = sourceHotels || [];
  let cursor = startIso ? new Date(`${startIso.slice(0, 10)}T12:00:00`) : null;
  return rows.map((h, idx) => {
    const nights = Math.max(1, h.nights || 1);
    let area = h.area || "Planned stay";
    if (cursor && !Number.isNaN(cursor.getTime())) {
      const checkInIso = toIso(cursor);
      const checkOut = new Date(cursor);
      checkOut.setDate(checkOut.getDate() + nights);
      const checkOutIso = toIso(checkOut);
      area = `${area} · ${isoShort(checkInIso)}-${isoShort(checkOutIso)}`;
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

  if (/\b(flexible|anytime|any\s*time|tbd|not\s*sure|whenever|open)\b/i.test(sLow)) {
    const start = new Date(today);
    start.setDate(start.getDate() + 21);
    const end = new Date(start);
    end.setDate(end.getDate() + Math.max(1, itineraryDayCount) - 1);
    return { start_iso: toIso(start), end_iso: toIso(end) };
  }

  const mo = findMonthIndexInText(raw);
  if (mo === undefined) return null;

  const day = seasonDayAnchor(sLow);
  const y = today.getFullYear();
  let start = new Date(y, mo, day);
  const end = new Date(start);
  end.setDate(end.getDate() + Math.max(1, itineraryDayCount) - 1);
  const bumped = bumpStartToFuture(start, end, today);
  return { start_iso: toIso(bumped.start), end_iso: toIso(bumped.end) };
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
 * exact ``Mar 15–22`` first, then vague ``mid March`` / ``Flexible``, then ~3 weeks out.
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

export function BookingCostBreakdown({ dest, paxCount = 2 }: { dest: DestCard; paxCount?: number }) {
  const flights = dest.flights || [];
  const hotels = dest.hotels || [];
  const acts = dest.activities || [];
  const flightNum = flights.reduce((s, f) => s + parsePriceDigits(f.price), 0);
  const hotelSum = hotels.reduce((s, h) => s + hotelStayTotal(h), 0);
  const actSum = activitySum(acts);
  const total = flightNum + hotelSum + actSum;
  return (
    <div className="rounded-xl border border-indigo-500/20 bg-gradient-to-br from-indigo-500/10 to-purple-500/10 p-4 lg:sticky lg:top-4">
      <p className="text-[11px] font-medium uppercase tracking-wider text-white/50">
        Cost breakdown · {paxCount} traveler{paxCount === 1 ? "" : "s"}
      </p>
      <div className="mt-3 space-y-2">
        <div className="flex items-center justify-between gap-2 text-[12px]">
          <span className="flex min-w-0 items-center gap-2 text-white/60">
            <Plane className="h-3.5 w-3.5 shrink-0 text-blue-400" aria-hidden />
            <span className="truncate">Flights</span>
          </span>
          <span className="shrink-0 tabular-nums text-white/85">${flightNum.toLocaleString()}</span>
        </div>
        <div className="flex items-center justify-between gap-2 text-[12px]">
          <span className="flex min-w-0 items-center gap-2 text-white/60">
            <Hotel className="h-3.5 w-3.5 shrink-0 text-purple-400" aria-hidden />
            <span className="truncate">Accommodation</span>
          </span>
          <span className="shrink-0 tabular-nums text-white/85">${hotelSum.toLocaleString()}</span>
        </div>
        <div className="flex items-center justify-between gap-2 text-[12px]">
          <span className="flex min-w-0 items-center gap-2 text-white/60">
            <Luggage className="h-3.5 w-3.5 shrink-0 text-emerald-400" aria-hidden />
            <span className="truncate">Activities</span>
          </span>
          <span className="shrink-0 tabular-nums text-white/85">${actSum.toLocaleString()}</span>
        </div>
      </div>
      <div className="mt-3 border-t border-white/[0.08] pt-2">
        <div className="flex items-center justify-between">
          <span className="text-[13px] text-white/50">Total</span>
          <span className="text-[18px] font-medium tabular-nums text-white/90">${total.toLocaleString()}</span>
        </div>
      </div>
      <p className="mt-2 text-[10px] leading-snug text-white/30">
        Book flights and hotels individually from the timeline
      </p>
    </div>
  );
}

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
  const { setDestinations } = useTrip();
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
  const [returnFlightPhase, setReturnFlightPhase] = useState<"na" | "pending" | "loading" | "done" | "error">("na");
  const [returnFlightError, setReturnFlightError] = useState<string | null>(null);
  const [criteriaExpanded, setCriteriaExpanded] = useState(false);

  /** Bumps on each live-pipeline mount so Strict Mode / prop churn does not merge stale step results. */
  const livePipelineGen = useRef(0);

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
    livePipelineGen.current += 1;
    const gen = livePipelineGen.current;

    const mergeOutboundFlight = (flight: FlightOption) => {
      if (gen !== livePipelineGen.current) return;
      const row = { ...flight, best: true, leg: flight.leg ?? ("outbound" as const), isPlaceholder: false };
      setDestinations((prev) =>
        prev.map((d) => {
          if (d.id !== dest.id) return d;
          const nextFlights = [...(d.flights || [])];
          if (nextFlights.length) {
            nextFlights[0] = row;
          } else {
            nextFlights.push(row);
          }
          return { ...d, flights: nextFlights, liveBookingEstimates: true };
        }),
      );
    };

    const mergeReturnFlight = (flight: FlightOption) => {
      if (gen !== livePipelineGen.current) return;
      const row = { ...flight, best: true, leg: "return" as const, isPlaceholder: false };
      setDestinations((prev) =>
        prev.map((d) => {
          if (d.id !== dest.id) return d;
          const withoutRet = [...(d.flights || [])].filter((f) => f.leg !== "return");
          return { ...d, flights: [...withoutRet, row], liveBookingEstimates: true };
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
          nextHotels[index] = {
            ...nextHotels[index],
            ...hotel,
            best: true,
            listing_url: hotel.listing_url || nextHotels[index].listing_url,
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
      setBookingStatus("Summarizing trip criteria…");
      setCriteriaExpanded(false);
      setHotelPhases(hotels.map(() => "pending"));

      try {
        const dateHintsPayload: BookingDateHints = {
          ...dateHints,
          adults: adultsFromProfileForBooking(profile),
        };
        const pillsPayload = profilePillsForBookingApi(profile);
        const prep = await bookingSearchPrepare({
          session_id: sessionId,
          destination_id: dest.id,
          date_hints: dateHintsPayload,
          profile_pills: pillsPayload,
        });
        if (cancelled || gen !== livePipelineGen.current) return;
        setCriteriaLines(prep.criteria_lines || []);
        const pax = typeof prep.adults === "number" && prep.adults >= 1 ? prep.adults : dateHintsPayload.adults ?? 2;
        setPrepPax(pax);
        setPreparePending(false);
        const wantsReturnTrip = !!(prep.start_date && prep.end_date && prep.start_date < prep.end_date);
        setReturnFlightPhase(wantsReturnTrip ? "pending" : "na");

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
        const outboundPlaceholder: FlightOption = {
          airline: "Searching Expedia flights…",
          route: `${fromLabel} → ${toLabel}${outboundDate ? ` · ${isoShort(outboundDate)}` : ""}`,
          duration: "Checking routes…",
          price: "Searching Expedia…",
          best: true,
          leg: "outbound",
          isPlaceholder: true,
        };
        const returnPlaceholder: FlightOption | null = wantsReturnTrip
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
          const next = prev.map((d) =>
            d.id === dest.id
              ? {
                  ...d,
                  hotels: placeholderHotels,
                  flights: returnPlaceholder ? [outboundPlaceholder, returnPlaceholder] : [outboundPlaceholder],
                }
              : d,
          );
          void syncUi(sessionId, next as unknown[]);
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
        });

        setBookingStatus("Searching outbound flights…");
        setFlightPhase("loading");
        const fr = await bookingSearchStep({
          session_id: sessionId,
          destination_id: dest.id,
          step: "flight",
          flight_leg: "outbound",
          date_hints: dateHintsPayload,
          profile_pills: pillsPayload,
        });
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
          setFlightPhase("done");
          setFlightStepError(null);
        } else {
          if (gen !== livePipelineGen.current) return;
          setFlightPhase("error");
          setFlightStepError(fr.error || "Flight search did not return a result.");
        }

        if (wantsReturnTrip && !fr.ok) {
          setReturnFlightPhase("na");
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
          const hr = await bookingSearchStep({
            session_id: sessionId,
            destination_id: dest.id,
            step: "hotel",
            hotel_index: i,
            date_hints: dateHintsPayload,
            profile_pills: pillsPayload,
          });
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
            mergeHotel(i, hr.hotel);
            if (gen !== livePipelineGen.current) return;
            setHotelPhases((prev) => {
              const next = [...prev];
              if (next[i] !== undefined) next[i] = "done";
              return next;
            });
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

        if (wantsReturnTrip && fr.ok) {
          setReturnFlightPhase("loading");
          setBookingStatus("Searching return flights…");
          const rr = await bookingSearchStep({
            session_id: sessionId,
            destination_id: dest.id,
            step: "flight",
            flight_leg: "return",
            date_hints: dateHintsPayload,
            profile_pills: pillsPayload,
          });
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
        }
      } catch (e: unknown) {
        if (!cancelled && gen === livePipelineGen.current) {
          const msg = e instanceof Error ? e.message : String(e);
          setPrepareError(msg);
          setCriteriaExpanded(true);
          setPreparePending(false);
          setFlightPhase((p) => (p === "idle" ? "error" : p));
        }
      } finally {
        if (!cancelled && gen === livePipelineGen.current) doneRef.current();
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [runMode, sessionId, dest.id, profileSig, dateHintsKey, setDestinations]);

  const outboundFlight = useMemo(
    () => flights.find((f) => f.leg !== "return") ?? flights[0],
    [flights],
  );
  const returnFlight = useMemo(() => flights.find((f) => f.leg === "return"), [flights]);

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
    (runMode === "live" && returnFlightPhase !== "na") || Boolean(returnFlight);

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

  const showFlightSection = flights.length > 0 || runMode === "live" || runMode === "demo";
  const showCriteriaBlock = runMode === "live" && sessionId;

  const livePipelineIdle =
    runMode !== "live" ||
    (!preparePending &&
      flightPhase !== "loading" &&
      returnSearchSettled &&
      hotelPhases.every((p) => p !== "pending" && p !== "loading"));

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
        <div
          className="pointer-events-none absolute bottom-0 left-9 top-8 w-px bg-gradient-to-b from-indigo-500/40 via-purple-500/40 to-indigo-500/40"
          aria-hidden
        />
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
                preparePending ? (
                  <p className="mt-2 text-[12px] text-white/45">Summarizing what we&apos;ll look up…</p>
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

          {/* Flight event */}
          {showFlightSection && (
            <div className="relative">
              <div className="absolute -left-8 top-1 flex h-10 w-10 items-center justify-center rounded-full border border-blue-500/40 bg-blue-500/20">
                <Plane className="h-3.5 w-3.5 text-blue-300" aria-hidden />
              </div>
              <p className="text-[11px] uppercase tracking-wider text-white/35">Depart</p>
              <div className="mt-2">
                <AnimatePresence mode="wait">
                  {showFlightSkeleton ? (
                    <motion.div
                      key="sk"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                    >
                      <FlightLoadSkeleton />
                    </motion.div>
                  ) : preparePending && runMode === "live" && !prepareError ? (
                    <p key="wait" className="text-[11px] text-white/35">
                      Flight search runs as soon as the summary above is ready.
                    </p>
                  ) : showFlightCard && outboundFlight ? (
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
                      />
                    </motion.div>
                  ) : runMode === "live" && !outboundFlight && (flightPhase === "error" || prepareError) ? (
                    <p className="text-[12px] text-amber-300/85">
                      {prepareError || flightStepError || "Flight search unavailable."}
                    </p>
                  ) : null}
                </AnimatePresence>
              </div>
            </div>
          )}

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
                <p className="text-[11px] uppercase tracking-wider text-white/35">Stay · {h.area}</p>
                <div className="mt-2">
                  <AnimatePresence mode="wait">
                    {showHotelSkeleton(idx) ? (
                      <motion.div
                        key={`sk-${idx}`}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                      >
                        <HotelLoadSkeleton label="Searching accommodations…" />
                      </motion.div>
                    ) : showHotelCard(idx) ? (
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
                  {showReturnSkeleton ? (
                    <motion.div
                      key="r-sk"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                    >
                      <FlightLoadSkeleton label="Searching return flights…" />
                    </motion.div>
                  ) : showReturnFlightCard && returnFlight ? (
                    <motion.div
                      key="r-fc"
                      initial={{ opacity: 0, rotateY: -12 }}
                      animate={{ opacity: 1, rotateY: 0 }}
                      transition={{ duration: 0.35 }}
                    >
                      <DetailedFlightCard
                        flight={returnFlight}
                        onClick={() => console.info("[booking] flight select return", returnFlight.airline)}
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
          <BookingCostBreakdown dest={dest} paxCount={prepPax} />
        ) : (
          <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-4 lg:sticky lg:top-4">
            <p className="text-[11px] font-medium uppercase tracking-wider text-white/35">Cost breakdown</p>
            <p className="mt-2 text-[12px] leading-relaxed text-white/45">
              {runMode === "live" && !livePipelineIdle
                ? "Fetching live prices…"
                : "Totals appear once flight and hotel rows look like real search results (not heuristic placeholders). Use the browser console for each step’s search_url; on the server see Travel Planner Prototype/.cache/booking_search_trace.log (or BOOKING_SEARCH_TRACE_LOG)."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
