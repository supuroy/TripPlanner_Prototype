/**
 * Lightweight booking helpers for destination card faces (no OTA search / timeline UI).
 */
import type { DestCard, FlightOption, HotelOption, ProfilePill } from "../components/TripStore";

export function parsePriceDigits(s: string | undefined): number {
  const n = parseInt(String(s ?? "").replace(/[^\d]/g, ""), 10);
  return Number.isFinite(n) ? n : 0;
}

export function liveBookingExtractLooksActionable(dest: DestCard): boolean {
  const flights = dest.flights || [];
  const hotels = dest.hotels || [];

  const flightsPass = (): boolean => {
    if (!flights.length) return false;
    for (const f of flights) {
      const route = (f.route || "").trim();
      if (!route || /^tbd$/i.test(route)) return false;
      if (parsePriceDigits(f.price) <= 0) return false;
    }
    return true;
  };

  const hotelsPass = (): boolean => {
    if (!hotels.length) return false;
    for (const h of hotels) {
      const nm = (h.name || "").trim().toLowerCase();
      if (nm === "lodging results" || nm === "hotel listing" || nm === "") return false;
      const pr = (h.price || "").trim();
      if (/^see expedia$/i.test(pr)) return false;
      if (parsePriceDigits(pr) <= 0) return false;
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
