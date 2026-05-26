import type { DestCard, HotelOption, ItinDay } from "../components/TripStore";
import { collectLodgingStayBlocksForDest, collectLodgingStayBlocksFromItinerary } from "./logisticsBreadcrumb";
import { resetDestinationBookingState } from "./resetDestinationBooking";

/** Stable signature for itinerary length, labels, and lodging anchors (breadcrumb + dirty detection). */
export function itineraryLogisticsSignature(itinerary: ItinDay[] | undefined): string {
  if (!itinerary?.length) return "";
  const stays = collectLodgingStayBlocksFromItinerary(itinerary);
  const days = itinerary.map((d) => ({
    day: d.day,
    label: String(d.label || "").trim(),
    items: (Array.isArray(d.items) ? d.items : []).map((x) => String(x).trim()),
    lodging: d.day_logistics?.lodging
      ? {
          label: d.day_logistics.lodging.destination_search_label?.trim(),
          ci: d.day_logistics.lodging.check_in?.trim(),
          co: d.day_logistics.lodging.check_out?.trim(),
        }
      : undefined,
  }));
  return JSON.stringify({ len: itinerary.length, days, stays });
}

export function totalLodgingNights(itinerary: ItinDay[] | undefined): number {
  return collectLodgingStayBlocksFromItinerary(itinerary).reduce((sum, s) => sum + s.nights, 0);
}

export function cardTripDaysLabel(itinerary: ItinDay[] | undefined): string | null {
  const n = itinerary?.length ?? 0;
  if (n <= 0) return null;
  return `${n} day${n === 1 ? "" : "s"}`;
}

function parseIsoDayMs(s: string | undefined): number | null {
  if (!s || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;
  const t = Date.parse(`${s}T12:00:00Z`);
  return Number.isNaN(t) ? null : t;
}

function formatMonthDay(iso: string): string {
  const t = parseIsoDayMs(iso);
  if (t === null) return iso;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** Trip date chip from lodging check-in/out when the profile ``when`` pill lags a refine. */
export function deriveWhenRangeFromItinerary(itinerary: ItinDay[] | undefined): string | null {
  let minMs: number | null = null;
  let maxMs: number | null = null;
  for (const row of itinerary || []) {
    const lod = row.day_logistics?.lodging;
    if (!lod) continue;
    for (const iso of [lod.check_in, lod.check_out]) {
      const v = iso?.trim();
      if (!v) continue;
      const ms = parseIsoDayMs(v);
      if (ms === null) continue;
      if (minMs === null || ms < minMs) minMs = ms;
      if (maxMs === null || ms > maxMs) maxMs = ms;
    }
  }
  if (minMs === null || maxMs === null) return null;
  const start = new Date(minMs).toISOString().slice(0, 10);
  const end = new Date(maxMs).toISOString().slice(0, 10);
  if (start === end) return formatMonthDay(start);
  return `${formatMonthDay(start)}–${formatMonthDay(end)}`;
}

function deriveHotelsFromItinerary(card: DestCard): HotelOption[] {
  const stays = collectLodgingStayBlocksForDest(card);
  if (!stays.length) return [];
  const totalNights = totalLodgingNights(card.itinerary);
  const primary = stays[0];
  return [
    {
      name: primary.label,
      area: card.name,
      price: "-",
      nights: totalNights > 0 ? totalNights : primary.nights,
      image: card.hotels?.find((h) => h.best)?.image || card.hotels?.[0]?.image || card.image || "",
    },
  ];
}

/**
 * After itinerary merge/refine, align placeholder hotel nights and clear stale booking rows
 * when logistics anchors changed.
 */
export function syncCardLogisticsFromItinerary(card: DestCard, prior?: DestCard): DestCard {
  if (!card.itinerary?.length) return card;
  const sig = itineraryLogisticsSignature(card.itinerary);
  const priorSig = prior?.itinerary?.length ? itineraryLogisticsSignature(prior.itinerary) : "";
  let next = card;
  if (priorSig && sig !== priorSig) {
    next = resetDestinationBookingState(next);
  }
  if (!priorSig || sig !== priorSig) {
    const derivedHotels = deriveHotelsFromItinerary(next);
    if (derivedHotels.length) {
      next = { ...next, hotels: derivedHotels };
    } else if (priorSig) {
      next = { ...next, hotels: [] };
    }
  }
  return next;
}
