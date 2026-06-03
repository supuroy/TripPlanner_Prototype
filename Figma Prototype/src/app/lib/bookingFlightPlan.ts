import type { DestCard, ProfilePill } from "../components/TripStore";
import type { WanderlyTransportPlan } from "../../types/workspace";

export interface PlannedFlightSegment {
  segmentIndex: number;
  fromLabel: string;
  toLabel: string;
  role?: string;
  dayOffset?: number | null;
}

export function transportFromDest(dest: DestCard): WanderlyTransportPlan | undefined {
  return dest.booking_hooks?.transport ?? dest.transport_plan;
}

function dayLogisticsHasCommercialFlights(dest: DestCard): boolean {
  for (const day of dest.itinerary ?? []) {
    const segs = day.day_logistics?.flight?.segments;
    if (!Array.isArray(segs)) continue;
    if (segs.some((s) => s && typeof s === "object" && String(s.from_search_label || "").trim() && String(s.to_search_label || "").trim())) {
      return true;
    }
  }
  return false;
}

/** Trip-wide mode label — prefer ``transport_plan`` but honor day-by-day airline legs when mismatched. */
export function effectiveTransportPrimaryMode(dest: DestCard): string | undefined {
  const tp = transportFromDest(dest);
  const mode = String(tp?.primary_mode || "").trim().toLowerCase();
  if (mode === "ground_only" && dayLogisticsHasCommercialFlights(dest)) {
    const hasSurface = (dest.itinerary ?? []).some(
      (d) => Array.isArray(d.day_logistics?.surface_legs) && d.day_logistics!.surface_legs!.length > 0,
    );
    return hasSurface ? "mixed" : "commercial_air";
  }
  return tp?.primary_mode;
}

export function transportPlanSummary(dest: DestCard): string | null {
  const s = transportFromDest(dest)?.summary?.trim();
  return s || null;
}

export function isGroundOnlyTransport(dest: DestCard): boolean {
  if (dayLogisticsHasCommercialFlights(dest)) return false;
  const mode = String(transportFromDest(dest)?.primary_mode || "")
    .trim()
    .toLowerCase();
  return mode === "ground_only";
}

function plannedSegmentsFromDayLogistics(
  dest: DestCard,
  fromProfile: string,
): PlannedFlightSegment[] {
  const out: PlannedFlightSegment[] = [];
  const seen = new Set<string>();
  const days = [...(dest.itinerary ?? [])].sort((a, b) => (a.day ?? 0) - (b.day ?? 0));
  for (const day of days) {
    const segs = day.day_logistics?.flight?.segments;
    if (!Array.isArray(segs)) continue;
    for (const x of segs) {
      if (!x || typeof x !== "object") continue;
      const fr = String(x.from_search_label || "").trim() || (out.length === 0 ? fromProfile : "");
      const to = String(x.to_search_label || "").trim();
      if (!fr && !to) continue;
      const key = `${fr}|${to}|${x.role || ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        segmentIndex: out.length,
        fromLabel: fr || "Home",
        toLabel: to || dest.name || "Destination",
        role: x.role,
        dayOffset: typeof day.day === "number" ? day.day - 1 : null,
      });
    }
  }
  return out;
}

export function commercialFlightSegmentsFromDest(
  dest: DestCard,
  profile: ProfilePill[],
): PlannedFlightSegment[] {
  const tp = transportFromDest(dest);
  const raw = tp?.commercial_flight_segments;
  const fromProfile = profile.find((p) => p.key === "from")?.value?.trim() || "";
  if (Array.isArray(raw) && raw.length > 0) {
    const out: PlannedFlightSegment[] = [];
    for (const x of raw) {
      const fr = (x.from_search_label || "").trim() || (out.length === 0 ? fromProfile : "");
      const to = (x.to_search_label || "").trim();
      if (!fr && !to) continue;
      const dayOff =
        typeof x.day_offset === "number"
          ? x.day_offset
          : typeof x.day_hint === "number"
            ? x.day_hint
            : null;
      out.push({
        segmentIndex: out.length,
        fromLabel: fr || "Home",
        toLabel: to || dest.name || "Destination",
        role: x.role,
        dayOffset: dayOff,
      });
    }
    return out;
  }
  return plannedSegmentsFromDayLogistics(dest, fromProfile);
}

export function hasStructuredFlightSegments(dest: DestCard, profile: ProfilePill[]): boolean {
  return commercialFlightSegmentsFromDest(dest, profile).length > 0;
}

/** Turn model day_range tokens like ``1-3`` or ``6`` into readable itinerary-day hints. */
export function formatTripDayRangeHint(raw: string | undefined): string | undefined {
  const s = (raw ?? "").trim();
  if (!s) return undefined;
  const mRange = /^(\d+)\s*[-–]\s*(\d+)$/.exec(s);
  if (mRange) {
    const a = Number(mRange[1]);
    const b = Number(mRange[2]);
    if (Number.isFinite(a) && Number.isFinite(b) && a <= b) {
      return a === b ? `Itinerary day ${a}` : `Itinerary days ${a}–${b}`;
    }
  }
  const mSingle = /^(\d+)$/.exec(s);
  if (mSingle) return `Itinerary day ${mSingle[1]}`;
  return s;
}

/** One row for the trip-wide “stays” column (outline, moves, or per-day lodging mirror). */
export interface TripWideStayRow {
  title: string;
  subtitle?: string;
}

/** Prefer server outline → transport lodging_moves → synthesized from ``day_logistics.lodging``. */
export function tripWideStaysFromDest(dest: DestCard): TripWideStayRow[] {
  const stays = dest.booking_search_outline?.stays;
  if (Array.isArray(stays) && stays.length > 0) {
    return stays
      .map((s) => {
        const title = (s.label || s.area || "Stay").trim() || "Stay";
        const parts: string[] = [];
        if (typeof s.nights === "number" && s.nights > 0) {
          parts.push(`${s.nights} night${s.nights === 1 ? "" : "s"}`);
        }
        return { title, subtitle: parts.length ? parts.join(" · ") : undefined };
      })
      .filter((r) => r.title);
  }

  const moves = transportFromDest(dest)?.lodging_moves;
  if (Array.isArray(moves) && moves.length > 0) {
    return moves
      .map((m) => {
        const area = (m.area || "").trim();
        const note = (m.note || "").trim();
        const title = area || note || "Stay";
        const parts: string[] = [];
        const dr = formatTripDayRangeHint(m.day_range ? String(m.day_range) : undefined);
        if (dr) parts.push(dr);
        if (typeof m.nights === "number" && m.nights > 0) {
          parts.push(`${m.nights} night${m.nights === 1 ? "" : "s"}`);
        }
        if (note && note !== area) parts.push(note);
        return { title, subtitle: parts.length ? parts.join(" · ") : undefined };
      })
      .filter((r) => r.title);
  }

  const rows: TripWideStayRow[] = [];
  const seen = new Set<string>();
  for (const day of dest.itinerary ?? []) {
    const lod = day.day_logistics?.lodging;
    if (!lod || typeof lod !== "object") continue;
    const title =
      (lod.destination_search_label || lod.area_hint || "").trim() ||
      `Overnight (day ${day.day})`;
    const cin = (lod.check_in || "").trim();
    const cout = (lod.check_out || "").trim();
    const dateLine = [cin, cout].filter(Boolean).join(" → ");
    const parts: string[] = [];
    if (dateLine) parts.push(dateLine);
    if (typeof lod.rooms === "number" && lod.rooms > 0) {
      parts.push(`${lod.rooms} room${lod.rooms === 1 ? "" : "s"}`);
    }
    const party = lod.traveler_party?.total ?? lod.guests?.adults;
    if (typeof party === "number" && party > 0) {
      parts.push(`${party} guest${party === 1 ? "" : "s"}`);
    }
    const key = `${title}|${cin}|${cout}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({ title, subtitle: parts.length ? parts.join(" · ") : undefined });
  }
  return rows;
}

/** Copy for the trip-wide car / ground-rental column (future per-step search). */
export function tripWideCarGroundHint(dest: DestCard): string[] {
  const tp = transportFromDest(dest);
  const mode = String(tp?.primary_mode || "")
    .trim()
    .toLowerCase();
  const lines: string[] = [];

  let drives = 0;
  let ferries = 0;
  let trains = 0;
  for (const d of dest.itinerary ?? []) {
    for (const leg of d.day_logistics?.surface_legs ?? []) {
      const k = String(leg.kind || "drive")
        .trim()
        .toLowerCase();
      if (k === "drive") drives += 1;
      else if (k === "ferry") ferries += 1;
      else if (k === "train") trains += 1;
    }
  }

  if (mode === "ground_only") {
    lines.push(
      "Ground-first trip — a trip-wide rental or ride bundle will pair with these legs in a later step.",
    );
  } else if (mode === "mixed") {
    lines.push(
      "Flights plus surface legs — add a rental block here if you want one vehicle across multiple days.",
    );
  } else if (mode === "commercial_air") {
    lines.push(
      "Flights are the main trunk; local drives below are day-scoped unless you add a trip-wide rental.",
    );
  } else {
    lines.push("Local surface moves are listed per day; trip-wide car search will hook in here next.");
  }

  const counts: string[] = [];
  if (drives) counts.push(`${drives} drive segment${drives === 1 ? "" : "s"}`);
  if (ferries) counts.push(`${ferries} ferry`);
  if (trains) counts.push(`${trains} train`);
  if (counts.length) lines.push(counts.join(" · "));

  if (!drives && !ferries && !trains && mode === "commercial_air") {
    lines.push("No drive / ferry / train rows in structured logistics yet.");
  }

  return lines;
}
