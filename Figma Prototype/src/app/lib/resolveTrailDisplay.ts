import type { WanderlyTrailRecord } from "../../api/wanderly";

function rawField(trail: WanderlyTrailRecord, key: string): unknown {
  const raw = trail.source_raw;
  if (!raw || typeof raw !== "object") return undefined;
  return (raw as Record<string, unknown>)[key];
}

/** Resolve AllTrails page URL (saved rows may omit top-level ``url``). */
export function resolveAllTrailsUrl(trail: WanderlyTrailRecord): string | undefined {
  const direct = (trail.url || "").trim();
  if (direct.startsWith("http")) return direct;
  const fromRaw = String(rawField(trail, "trailUrl") || rawField(trail, "url") || "").trim();
  if (fromRaw.startsWith("http")) return fromRaw;
  const slug = String(rawField(trail, "slug") || "").trim().replace(/^\//, "");
  if (slug) return `https://www.alltrails.com/${slug}`;
  return undefined;
}

function formatMinutes(mins: number): string {
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m > 0 ? `${h} h ${m} min` : `${h} h`;
}

/** Human-readable hike duration from Apify fields. */
export function resolveEstimatedTime(trail: WanderlyTrailRecord): string | undefined {
  const direct = (trail.estimated_time || "").trim();
  if (direct) return direct;
  const fromRaw = String(rawField(trail, "estimatedTime") || "").trim();
  if (fromRaw) return fromRaw;
  const mins =
    trail.duration_minutes ??
    (typeof rawField(trail, "durationMinutes") === "number"
      ? (rawField(trail, "durationMinutes") as number)
      : typeof rawField(trail, "durationHiking") === "number"
        ? (rawField(trail, "durationHiking") as number)
        : undefined);
  if (typeof mins === "number" && mins > 0) return formatMinutes(Math.round(mins));
  return undefined;
}

export function enrichTrailRecord(trail: WanderlyTrailRecord): WanderlyTrailRecord {
  return {
    ...trail,
    url: resolveAllTrailsUrl(trail) ?? trail.url,
    estimated_time: resolveEstimatedTime(trail) ?? trail.estimated_time,
  };
}
