import type { DestCard } from "../components/TripStore";
import type { DayLogisticsCampNight, DayLogisticsSurfaceLeg, DayLogisticsTrailSegment } from "../../types/workspace";

export type TrailStatsSource = Pick<
  DayLogisticsTrailSegment,
  "name" | "distance_mi" | "elevation_gain_ft" | "elevation_loss_ft" | "notes"
> &
  Pick<DayLogisticsSurfaceLeg, "trail_name" | "distance_mi" | "elevation_gain_ft" | "elevation_loss_ft">;

/** Resolve trail name + distance/elevation from a walk leg and/or day-level trail_segment. */
export function resolveTrailStats(
  leg: DayLogisticsSurfaceLeg | undefined,
  dayTrail: DayLogisticsTrailSegment | undefined,
): TrailStatsSource | null {
  const name = (leg?.trail_name || dayTrail?.name || "").trim();
  const distance_mi = leg?.distance_mi ?? dayTrail?.distance_mi;
  const elevation_gain_ft = leg?.elevation_gain_ft ?? dayTrail?.elevation_gain_ft;
  const elevation_loss_ft = leg?.elevation_loss_ft ?? dayTrail?.elevation_loss_ft;
  const notes = dayTrail?.notes?.trim();
  const hasStats =
    Boolean(name) ||
    typeof distance_mi === "number" ||
    typeof elevation_gain_ft === "number" ||
    typeof elevation_loss_ft === "number";
  if (!hasStats) return null;
  return { name: name || undefined, distance_mi, elevation_gain_ft, elevation_loss_ft, notes };
}

/** Pull canonical segment URL from trail authority notes (``day_logistics.trail_segment.notes``). */
export function extractAllTrailsUrlFromNotes(notes?: string): string | undefined {
  const raw = (notes || "").trim();
  if (!raw) return undefined;
  const m = raw.match(/https:\/\/www\.alltrails\.com\/(?:trail|explore\/trail)\/[^\s)]+/i);
  return m?.[0]?.replace(/[.,;]+$/, "");
}

/** Best AllTrails trail page URL for the logistics flip face (catalog, verified, or legacy hint). */
export function resolveAllTrailsUrlForDest(dest: DestCard): string | undefined {
  const atc = dest.alltrails_search_context;
  const parent = atc?.parent_alltrails_url?.trim();
  if (parent) return parent;
  const hint = (atc as { unverified_url_hint?: string } | undefined)?.unverified_url_hint?.trim();
  if (hint && hint.includes("alltrails.com")) return hint;
  const g = dest.grounding;
  const catalogUrl = g?.status === "catalog" ? String(g.source_url || "").trim() : "";
  if (catalogUrl && catalogUrl.includes("alltrails.com")) return catalogUrl;
  for (const day of dest.itinerary ?? []) {
    const fromNotes = extractAllTrailsUrlFromNotes(day.day_logistics?.trail_segment?.notes);
    if (fromNotes) return fromNotes;
    const segUrl = (day.day_logistics?.trail_segment as { alltrails_url?: string } | undefined)?.alltrails_url;
    if (typeof segUrl === "string" && segUrl.includes("alltrails.com")) return segUrl.trim();
  }
  return undefined;
}

export function allTrailsLinkLabelForDest(dest: DestCard): string {
  const name =
    dest.alltrails_search_context?.trail_name?.trim() ||
    dest.grounding?.trail_id?.replace(/^[^:]+:/, "").replace(/-/g, " ") ||
    dest.name?.trim() ||
    "Trail";
  return name.length > 48 ? `${name.slice(0, 45)}…` : name;
}

export function formatTrailStatsLine(stats: TrailStatsSource): string | null {
  const parts: string[] = [];
  const dist = stats.distance_mi;
  if (typeof dist === "number" && dist >= 0) {
    parts.push(`${dist % 1 === 0 ? dist.toFixed(0) : dist.toFixed(1)} mi`);
  }
  const gain = stats.elevation_gain_ft;
  const loss = stats.elevation_loss_ft;
  if (typeof gain === "number" && gain > 0) parts.push(`↑ ${gain.toLocaleString()} ft`);
  if (typeof loss === "number" && loss > 0) parts.push(`↓ ${loss.toLocaleString()} ft`);
  if (typeof gain === "number" && gain === 0 && typeof loss === "number" && loss === 0) {
    parts.push("minimal elevation");
  }
  return parts.length ? parts.join(" · ") : null;
}

export function campKindLabel(kind: string | undefined): string {
  const k = (kind || "other").toLowerCase();
  if (k === "backcountry") return "Backcountry camp";
  if (k === "campground") return "Campground";
  if (k === "hut") return "Hut";
  if (k === "hostel") return "Hostel bunk";
  return "Camp";
}

/** Parse ``Heart Lake TH to 8H2 Sheridan Creek`` when the model omitted ``surface_legs``. */
export function parseDayLabelEndpoints(label: string): { from: string; to: string } | null {
  const raw = (label || "").trim();
  if (!raw) return null;
  const head = raw.split(/\s*→\s*|\s*->\s*/)[0]?.trim() ?? "";
  const parts = head.split(/\s+to\s+/i);
  if (parts.length !== 2) return null;
  const from = parts[0]?.trim() ?? "";
  const to = parts[1]?.trim() ?? "";
  if (from.length < 3 || to.length < 3) return null;
  return { from, to };
}

export function formatCampNightSubtitle(camp: DayLogisticsCampNight): string | null {
  const bits: string[] = [];
  if (camp.permit_required) bits.push("Permit required");
  if (camp.booking_hint?.trim()) bits.push(camp.booking_hint.trim());
  if (camp.notes?.trim()) bits.push(camp.notes.trim());
  return bits.length ? bits.join(" · ") : null;
}
