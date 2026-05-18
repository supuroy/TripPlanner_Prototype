/**
 * Lightweight booking helpers for destination card faces (no OTA search / timeline UI).
 */
import type { DestCard, FlightOption, HotelOption, ProfilePill } from "../components/TripStore";
import {
  commercialFlightSegmentsFromDest,
  isGroundOnlyTransport,
  transportFromDest,
  tripWideStaysFromDest,
} from "./bookingFlightPlan";
import { buildLogisticsBreadcrumb, collectLodgingStayBlocks } from "./logisticsBreadcrumb";

export function parsePriceDigits(s: string | undefined): number {
  const n = parseInt(String(s ?? "").replace(/[^\d]/g, ""), 10);
  return Number.isFinite(n) ? n : 0;
}

/**
 * True for a single OTA-style dollar amount — not planning bands like ``$360–660/n``
 * (which ``parsePriceDigits`` would misread as 360660).
 */
export function priceLooksLikeLiveBookableAmount(s: string | undefined): boolean {
  const raw = (s ?? "").trim();
  if (!raw || raw === "-" || /^tbd/i.test(raw) || /^see expedia$/i.test(raw)) return false;
  if (/[–—]/.test(raw)) return false;
  if (/\$?\s*[\d,.]+\s*-\s*\$?\s*[\d,.]+/.test(raw)) return false;
  if (/~\s*\$/.test(raw) && /-\s*\$?[\d]/.test(raw)) return false;
  if (/\bpp\b/i.test(raw) || /ballpark|estimate|\/night/i.test(raw)) return false;
  const n = parsePriceDigits(raw);
  if (n <= 0 || n > 50_000) return false;
  return true;
}

/** One merged hotel row is safe to show as a full OTA card (not a heuristic shell). */
export function hotelRowIsActionableHotel(h: HotelOption | undefined): boolean {
  if (!h || h.isPlaceholder === true) return false;
  const nm = (h.name || "").trim().toLowerCase();
  if (!nm || nm === "lodging results" || nm === "hotel listing") return false;
  if (!priceLooksLikeLiveBookableAmount(h.price)) return false;
  if (!String(h.listing_url || "").trim()) return false;
  return true;
}

export function liveBookingExtractLooksActionable(dest: DestCard): boolean {
  const flights = dest.flights || [];
  const hotels = dest.hotels || [];

  const flightsPass = (): boolean => {
    if (!flights.length) return false;
    for (const f of flights) {
      const route = (f.route || "").trim();
      if (!route || /^tbd$/i.test(route)) return false;
      if (!priceLooksLikeLiveBookableAmount(f.price)) return false;
    }
    return true;
  };

  const hotelsPass = (): boolean => {
    if (!hotels.length) return false;
    for (const h of hotels) {
      if (!hotelRowIsActionableHotel(h)) return false;
    }
    return true;
  };

  const fp = flightsPass();
  const hp = hotelsPass();
  if (!flights.length && hp) return true;
  if (fp && !hotels.length) return true;
  if (fp && hp) return true;
  return false;
}

export function destHasPersistentBookingSummary(dest: DestCard): boolean {
  if (liveBookingExtractLooksActionable(dest)) return true;
  if (dest.liveBookingEstimates && ((dest.flights?.length ?? 0) > 0 || (dest.hotels?.length ?? 0) > 0)) {
    return true;
  }
  return false;
}

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

export function hotelStayTotal(h: HotelOption): number {
  if (!priceLooksLikeLiveBookableAmount(h.price)) return 0;
  const per = parsePriceDigits(h.price);
  return per * (h.nights || 0);
}

export function activitySum(activities: { price?: string }[] | undefined): number {
  if (!activities?.length) return 0;
  return activities.reduce((s, a) => s + parsePriceDigits(a.price), 0);
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

/** Merged OTA flight row the user picked (or default rank) — same bar as logistics flip ``persistedFlightPick``. */
export function flightRowIsUserSelectedFlight(f: FlightOption | undefined): boolean {
  if (!f || f.isPlaceholder === true) return false;
  if (!priceLooksLikeLiveBookableAmount(f.price)) return false;
  if (String(f.search_url || "").trim()) return true;
  return f.isPlaceholder === false;
}

export function destHasUserSelectedBooking(dest: DestCard): boolean {
  return (
    (dest.flights || []).some((f) => flightRowIsUserSelectedFlight(f)) ||
    (dest.hotels || []).some((h) => hotelRowIsActionableHotel(h))
  );
}

/** Trip total from user-selected flight/hotel rows — matches ``BookingCostBreakdown`` when shown. */
export function destUserSelectedTripTotalUsd(dest: DestCard): number | null {
  if (!destHasUserSelectedBooking(dest)) return null;
  let sum = 0;
  for (const f of dest.flights || []) {
    if (flightRowIsUserSelectedFlight(f)) sum += parsePriceDigits(f.price);
  }
  for (const h of dest.hotels || []) {
    if (hotelRowIsActionableHotel(h)) sum += hotelStayTotal(h);
  }
  sum += activitySum(dest.activities);
  return sum > 0 ? sum : null;
}

export function destUserSelectedTripTotalLabel(dest: DestCard): string {
  const total = destUserSelectedTripTotalUsd(dest);
  if (total === null) return "-";
  return `$${total.toLocaleString()}`;
}

function formatRequiredBookingCount(n: number | null): string {
  if (n === null) return "-";
  return String(n);
}

/** Commercial flight legs to book (transport plan segments, else logistics outline). */
export function requiredCommercialFlightBookings(
  dest: DestCard,
  profile: ProfilePill[],
): number | null {
  const segs = commercialFlightSegmentsFromDest(dest, profile);
  if (segs.length > 0) return segs.length;
  if (isGroundOnlyTransport(dest) || dest.needs_commercial_flight === false) return 0;
  const crumbs = buildLogisticsBreadcrumb(dest);
  const flightLegs = crumbs.filter((c) => c.kind === "flight_in" || c.kind === "flight_return");
  if (flightLegs.length > 0) return flightLegs.length;
  if (!(dest.itinerary?.length) && !transportFromDest(dest)) return null;
  return 0;
}

/** Distinct hotel stays to book (outline / lodging_moves / itinerary). */
export function requiredHotelBookings(dest: DestCard): number | null {
  const stays = tripWideStaysFromDest(dest);
  if (stays.length > 0) return stays.length;
  const blocks = collectLodgingStayBlocks(dest.itinerary);
  if (blocks.length > 0) return blocks.length;
  if (!(dest.itinerary?.length) && !dest.booking_search_outline?.stays?.length) return null;
  return 0;
}

export function requiredCommercialFlightBookingsLabel(
  dest: DestCard,
  profile: ProfilePill[],
): string {
  return formatRequiredBookingCount(requiredCommercialFlightBookings(dest, profile));
}

export function requiredHotelBookingsLabel(dest: DestCard): string {
  return formatRequiredBookingCount(requiredHotelBookings(dest));
}
