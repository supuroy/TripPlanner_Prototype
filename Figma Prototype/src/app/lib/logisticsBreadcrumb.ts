import type { DestCard, ItinDay } from "../components/TripStore";
import type {
  DayLogisticsFlightSegment,
  DayLogisticsSurfaceLeg,
  WanderlyTransportPlan,
} from "../../types/workspace";

/**
 * Destinations where a US-origin trip is almost always long-haul air, even if the itinerary
 * LLM wrongly emits ``ground_only`` with empty flight segments (in-country legs only).
 */
const COUNTRY_TYPICALLY_REQUIRES_OVERSEAS_FLIGHT = new Set<string>([
  "New Zealand",
  "Australia",
  "Japan",
  "South Korea",
  "Iceland",
  "Norway",
  "Sweden",
  "Finland",
  "Denmark",
  "United Kingdom",
  "Ireland",
  "France",
  "Germany",
  "Italy",
  "Spain",
  "Portugal",
  "Greece",
  "Netherlands",
  "Belgium",
  "Switzerland",
  "Austria",
  "Poland",
  "Czech Republic",
  "Croatia",
  "South Africa",
  "Singapore",
  "Thailand",
  "Vietnam",
  "India",
  "Brazil",
  "Argentina",
  "Chile",
  "Peru",
  "Colombia",
  "Philippines",
  "Indonesia",
  "Malaysia",
  "Taiwan",
  "Hong Kong",
  "Israel",
  "United Arab Emirates",
  "Turkey",
  "Egypt",
  "Morocco",
  "Kenya",
  "Tanzania",
  "Mexico",
  "Canada",
]);

export type LogisticsCrumbKind = "flight_in" | "flight_return" | "hotel" | "car" | "ferry" | "train";

export interface LogisticsCrumb {
  kind: LogisticsCrumbKind;
  /** Hotel nights when kind === "hotel" */
  nights?: number;
}

function effectiveTransport(dest: DestCard): WanderlyTransportPlan | undefined {
  return dest.booking_hooks?.transport ?? dest.transport_plan;
}

function parseIsoDay(s: string | undefined): number | null {
  if (!s || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;
  const t = Date.parse(`${s}T12:00:00Z`);
  return Number.isNaN(t) ? null : Math.floor(t / 86400000);
}

function nightsBetweenLodging(checkIn: string | undefined, checkOut: string | undefined): number {
  const a = parseIsoDay(checkIn);
  const b = parseIsoDay(checkOut);
  if (a === null || b === null || b <= a) return 1;
  return Math.max(1, b - a);
}

function collectFlightSegmentsFromItinerary(
  itinerary: ItinDay[] | undefined,
): Array<{ role?: string }> {
  const out: Array<{ role?: string }> = [];
  const days = [...(itinerary || [])].sort((x, y) => (x.day ?? 0) - (y.day ?? 0));
  for (const row of days) {
    const fl = row.day_logistics?.flight;
    if (!fl || typeof fl !== "object") continue;
    const segs = (fl as { segments?: DayLogisticsFlightSegment[] }).segments;
    if (!Array.isArray(segs)) continue;
    for (const s of segs) {
      if (s && typeof s === "object") out.push({ role: s.role });
    }
  }
  return out;
}

/** BC / PNW destinations commonly reached by ferry + drive without a commercial flight leg. */
const CANADA_GROUND_TRIP_HINT =
  /\b(british columbia|bc\b|vancouver|victoria|vancouver island|tofino|ucluelet|nanaimo|cowichan|sunshine coast|gulf islands|pender|saltspring|salt spring|whistler|squamish|pemberton|kelowna|okanagan|calgary|banff|alberta|fernie|revelstoke)\b/i;

function inferInternationalAirOutline(dest: DestCard, tp: WanderlyTransportPlan | undefined): boolean {
  const mode = String(tp?.primary_mode || "").toLowerCase();
  const segs = tp?.commercial_flight_segments || [];
  if (mode === "ground_only") return false;
  if (dest.needs_commercial_flight === false) return false;
  if (segs.length > 0) return false;
  if (collectFlightSegmentsFromItinerary(dest.itinerary).length > 0) return false;
  if (dest.needs_commercial_flight === true) return false;
  if (mode && mode !== "ground_only") return false;
  const c = String(dest.country || "").trim();
  if (!c) return false;
  if (!COUNTRY_TYPICALLY_REQUIRES_OVERSEAS_FLIGHT.has(c)) return false;
  /* Canada/Mexico can be road trips from the US — only infer air when the card id hints long-haul regions. */
  if (c === "Canada" || c === "Mexico") {
    const id = `${dest.id || ""} ${dest.name || ""}`.toLowerCase();
    if (c === "Canada" && !CANADA_GROUND_TRIP_HINT.test(id)) {
      return true;
    }
    if (c === "Mexico" && !/\b(tijuana|baja|sonora|nuevo leon|monterrey)\b/i.test(id)) {
      return true;
    }
    return false;
  }
  return true;
}

function scanSurfaceModes(itinerary: ItinDay[] | undefined): { hasDrive: boolean; hasFerry: boolean; hasTrain: boolean } {
  let hasDrive = false;
  let hasFerry = false;
  let hasTrain = false;
  for (const row of itinerary || []) {
    const legs = row.day_logistics?.surface_legs;
    if (!Array.isArray(legs)) continue;
    for (const leg of legs as DayLogisticsSurfaceLeg[]) {
      const k = String(leg?.kind || "").toLowerCase();
      if (k === "drive") hasDrive = true;
      if (k === "ferry") hasFerry = true;
      if (k === "train") hasTrain = true;
    }
  }
  return { hasDrive, hasFerry, hasTrain };
}

function normalizeLodgingLabel(label: string): string {
  return label.replace(/\s+/g, " ").trim().toLowerCase();
}

/** Matches server ``parse_day_range_nights`` (inclusive day span on the itinerary). */
export function parseDayRangeNights(raw: string | undefined): number {
  const s = (raw ?? "").trim();
  if (!s) return 1;
  const mRange = /^(\d+)\s*[-–]\s*(\d+)$/.exec(s);
  if (mRange) {
    const a = Number(mRange[1]);
    const b = Number(mRange[2]);
    if (Number.isFinite(a) && Number.isFinite(b) && a <= b) {
      return Math.max(1, b - a + 1);
    }
  }
  if (/^(\d+)$/.test(s)) return 1;
  return 1;
}

function lodgingMovesStayBlocks(dest: DestCard): Array<{ label: string; nights: number }> {
  const tp = dest.booking_hooks?.transport ?? dest.transport_plan;
  const moves = tp?.lodging_moves;
  if (!Array.isArray(moves) || !moves.length) return [];
  const out: Array<{ label: string; nights: number }> = [];
  for (const m of moves) {
    const label = String(m.area || "").trim();
    if (!label) continue;
    const nights =
      typeof m.nights === "number" && m.nights > 0
        ? m.nights
        : parseDayRangeNights(m.day_range ? String(m.day_range) : undefined);
    out.push({ label, nights });
  }
  return out;
}

/**
 * Distinct lodging anchors from day_logistics — merges consecutive days at the same property.
 */
export function collectLodgingStayBlocksFromItinerary(
  itinerary: ItinDay[] | undefined,
): Array<{ label: string; nights: number }> {
  const days = [...(itinerary || [])].sort((x, y) => (x.day ?? 0) - (y.day ?? 0));
  const runs: Array<{ label: string; checkIn?: string; checkOut?: string; dayRows: number }> = [];

  for (const row of days) {
    const lod = row.day_logistics?.lodging;
    if (!lod?.destination_search_label?.trim()) continue;
    const label = lod.destination_search_label.trim();
    const ci = lod.check_in?.trim() || undefined;
    const co = lod.check_out?.trim() || undefined;
    const last = runs[runs.length - 1];

    if (last && normalizeLodgingLabel(last.label) === normalizeLodgingLabel(label)) {
      last.dayRows += 1;
      if (ci) {
        const ciDay = parseIsoDay(ci);
        const lastIn = last.checkIn ? parseIsoDay(last.checkIn) : null;
        if (lastIn === null || (ciDay !== null && ciDay < lastIn)) last.checkIn = ci;
      }
      if (co) {
        const coDay = parseIsoDay(co);
        const lastOut = last.checkOut ? parseIsoDay(last.checkOut) : null;
        if (lastOut === null || (coDay !== null && coDay > lastOut)) last.checkOut = co;
      }
      continue;
    }
    runs.push({ label, checkIn: ci, checkOut: co, dayRows: 1 });
  }

  return runs.map((r) => {
    let nights = nightsBetweenLodging(r.checkIn, r.checkOut);
    if (r.dayRows > 1) {
      const fromRows = Math.max(1, r.dayRows - 1);
      nights = Math.max(nights, fromRows);
    }
    return { label: r.label, nights };
  });
}

/** @deprecated Prefer ``collectLodgingStayBlocksForDest`` when transport ``lodging_moves`` exist. */
export function collectLodgingStayBlocks(
  itinerary: ItinDay[] | undefined,
): Array<{ label: string; nights: number }> {
  return collectLodgingStayBlocksFromItinerary(itinerary);
}

/**
 * Best-effort stay blocks for card icons: itinerary lodging rows, with ``lodging_moves`` when
 * per-day check-in/out under-count multi-night bases (common on 7-day ski trips).
 */
export function collectLodgingStayBlocksForDest(dest: DestCard): Array<{ label: string; nights: number }> {
  const fromItin = collectLodgingStayBlocksFromItinerary(dest.itinerary);
  const fromMoves = lodgingMovesStayBlocks(dest);
  const itinNights = fromItin.reduce((s, b) => s + b.nights, 0);
  const moveNights = fromMoves.reduce((s, b) => s + b.nights, 0);
  const tripDays = dest.itinerary?.length ?? 0;
  const softMin = tripDays > 1 ? tripDays - 1 : tripDays;

  if (fromMoves.length > 0 && (fromItin.length === 0 || (moveNights >= itinNights && moveNights >= softMin))) {
    return fromMoves;
  }
  if (fromItin.length > 0) return fromItin;
  return fromMoves;
}

export function buildLogisticsBreadcrumb(dest: DestCard): LogisticsCrumb[] {
  const tp = effectiveTransport(dest);
  const mode = String(tp?.primary_mode || "").toLowerCase();
  let segs = [...(tp?.commercial_flight_segments || [])];
  const dayAirSegs = collectFlightSegmentsFromItinerary(dest.itinerary);
  if (dayAirSegs.length > 0) {
    segs = dayAirSegs as typeof segs;
  }

  const syntheticInternational = inferInternationalAirOutline(dest, tp);
  const airOff = dest.needs_commercial_flight === false && !syntheticInternational;

  const treatAsAir =
    mode !== "ground_only" &&
    !airOff &&
    (segs.length > 0 || syntheticInternational) &&
    (syntheticInternational ||
      mode === "commercial_air" ||
      mode === "mixed" ||
      (!mode && segs.length > 0));

  const { hasDrive, hasFerry, hasTrain } = scanSurfaceModes(dest.itinerary);
  const stays = collectLodgingStayBlocksForDest(dest);

  const hasReturnAir =
    treatAsAir &&
    (syntheticInternational || segs.some((s) => String(s.role || "").toLowerCase() === "return"));
  const allReturn =
    treatAsAir &&
    segs.length > 0 &&
    segs.every((s) => String(s.role || "").toLowerCase() === "return");
  const hasInboundAir =
    treatAsAir &&
    (syntheticInternational ||
      segs.some((s) => {
        const r = String(s.role || "").toLowerCase();
        return r === "inbound" || r === "within_trip";
      }) ||
      (segs.length > 0 && !allReturn));

  const crumbs: LogisticsCrumb[] = [];

  if (treatAsAir && hasInboundAir) {
    crumbs.push({ kind: "flight_in" });
  } else if (treatAsAir && allReturn) {
    /* Rare card-level slice that only lists return legs — still air trip */
    crumbs.push({ kind: "flight_in" });
  } else {
    if (hasDrive) crumbs.push({ kind: "car" });
    if (hasFerry) crumbs.push({ kind: "ferry" });
    if (hasTrain) crumbs.push({ kind: "train" });
    if (!hasDrive && !hasFerry && !hasTrain) crumbs.push({ kind: "car" });
  }

  for (const s of stays) {
    crumbs.push({ kind: "hotel", nights: s.nights });
  }

  if (treatAsAir && hasReturnAir) {
    crumbs.push({ kind: "flight_return" });
  } else if (!treatAsAir && (hasDrive || hasFerry || hasTrain)) {
    if (hasFerry) crumbs.push({ kind: "ferry" });
    if (hasTrain) crumbs.push({ kind: "train" });
    if (hasDrive) crumbs.push({ kind: "car" });
    if (!hasDrive && !hasFerry && !hasTrain) crumbs.push({ kind: "car" });
  }

  if (crumbs.length === 0) {
    if (dest.needs_commercial_flight === true) return [{ kind: "flight_in" }, { kind: "flight_return" }];
    return [{ kind: "car" }];
  }

  return crumbs;
}
