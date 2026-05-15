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

function inferInternationalAirOutline(dest: DestCard, tp: WanderlyTransportPlan | undefined): boolean {
  const mode = String(tp?.primary_mode || "").toLowerCase();
  const segs = tp?.commercial_flight_segments || [];
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
    if (c === "Canada" && !/\b(british columbia|vancouver|victoria|whistler|calgary|banff|alberta)\b/i.test(id)) {
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

/**
 * Distinct lodging anchors from day_logistics (dedupes repeated multi-night rows).
 */
export function collectLodgingStayBlocks(
  itinerary: ItinDay[] | undefined,
): Array<{ label: string; nights: number }> {
  const days = [...(itinerary || [])].sort((x, y) => (x.day ?? 0) - (y.day ?? 0));
  const blocks: Array<{ key: string; label: string; nights: number }> = [];
  for (const row of days) {
    const lod = row.day_logistics?.lodging;
    if (!lod?.destination_search_label?.trim()) continue;
    const label = lod.destination_search_label.trim();
    const ci = lod.check_in?.trim();
    const co = lod.check_out?.trim();
    const key = `${label}|${ci || ""}|${co || ""}`;
    const last = blocks[blocks.length - 1];
    if (last && last.key === key) continue;
    const nights = nightsBetweenLodging(ci, co);
    blocks.push({ key, label, nights });
  }
  return blocks.map(({ label, nights }) => ({ label, nights }));
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
    !airOff &&
    (segs.length > 0 || syntheticInternational) &&
    (syntheticInternational ||
      mode === "commercial_air" ||
      mode === "mixed" ||
      (!mode && segs.length > 0) ||
      (mode === "ground_only" && segs.length > 0));

  const { hasDrive, hasFerry, hasTrain } = scanSurfaceModes(dest.itinerary);
  const stays = collectLodgingStayBlocks(dest.itinerary);

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
