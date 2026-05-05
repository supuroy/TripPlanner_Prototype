import type { AgentResponse, DestCard, ProfilePill } from "../components/TripStore";
import type { WanderlyWorkspaceDTO } from "../../types/workspace";
import { logItin } from "./itineraryDebug";

function asDestCards(raw: unknown[]): DestCard[] {
  return raw.filter(Boolean).map((d) => {
    const o = { ...(d as object) } as Record<string, unknown>;
    if ("id" in o && o.id != null) o.id = String(o.id).trim();
    /* Streaming / partial JSON often omits arrays; UI assumes arrays (map/slice) and stable hook counts. */
    if (!Array.isArray(o.vibe)) o.vibe = [];
    if (!Array.isArray(o.itinerary)) o.itinerary = [];
    if (!Array.isArray(o.flights)) o.flights = [];
    if (!Array.isArray(o.hotels)) o.hotels = [];
    if (!Array.isArray(o.images)) o.images = [];
    return o;
  }) as DestCard[];
}

/** Infer dirty flag from server payload when the field is omitted. */
function inferItineraryDirtyFromPayload(d: DestCard): boolean {
  if (typeof d.itinerary_dirty === "boolean") return d.itinerary_dirty;
  return !(d.itinerary && d.itinerary.length > 0);
}

function isPlaceholderishImage(url: string): boolean {
  const u = url.trim().toLowerCase();
  return !u || u.includes("placehold.co");
}

/**
 * Merge pin/dismiss from the client for matching ids, and itinerary_dirty:
 * stays true if the client already marked stale, or server says dirty / empty itinerary.
 * Preserves hero URLs and existing itineraries when the server sends placeholder-only rows (refine/smoke).
 * When ``bypassItineraryPreserve`` is true (itineraries marked stale), do not keep a prior itinerary if the
 * server omitted or cleared days — otherwise the UI keeps showing the old plan until a manual refresh.
 */
export function mergeDestinationUiState(
  next: DestCard[],
  prior: DestCard[],
  opts?: { bypassItineraryPreserve?: boolean },
): DestCard[] {
  if (!prior.length) {
    return next.map((d) => ({ ...d, itinerary_dirty: inferItineraryDirtyFromPayload(d) }));
  }
  const bypassPreserve = Boolean(opts?.bypassItineraryPreserve);
  const priorById = new Map(prior.map((pd) => [String(pd.id), pd]));
  return next.map((d) => {
    const p = priorById.get(String(d.id));
    let row: DestCard = { ...d };
    let preservedItinerary = false;
    if (p) {
      const curImg = String(row.image || "");
      const pImg = String(p.image || "");
      if (isPlaceholderishImage(curImg) && !isPlaceholderishImage(pImg)) {
        row = {
          ...row,
          image: p.image,
          images: p.images?.length ? [...p.images] : [p.image],
        };
      }
      const curIt = row.itinerary?.length ?? 0;
      const pIt = p.itinerary?.length ?? 0;
      if (!bypassPreserve && curIt === 0 && pIt > 0 && p.itinerary) {
        preservedItinerary = true;
        row = {
          ...row,
          itinerary: [...p.itinerary],
          itinerary_dirty: false,
        };
      }
      if (!(row.weather_summary || "").trim() && (p.weather_summary || "").trim()) {
        row = { ...row, weather_summary: p.weather_summary };
      }
      if (!row.commerce && p.commerce) {
        row = { ...row, commerce: p.commerce };
      }
    }
    const pinned = p?.pinned ?? row.pinned;
    const dismissed = p?.dismissed ?? row.dismissed;
    const fromServer = inferItineraryDirtyFromPayload(row);
    const itinerary_dirty = preservedItinerary
      ? false
      : p?.itinerary_dirty === true || fromServer;
    return { ...row, pinned, dismissed, itinerary_dirty };
  });
}

/** Same merge as applyWanderlyWorkspace uses for destinations (for follow-up calls like hero enrich). */
export function destinationsFromWorkspace(
  w: WanderlyWorkspaceDTO,
  priorDestinations?: DestCard[],
): DestCard[] | null {
  if (!w.destinations?.length) return null;
  const incoming = asDestCards(w.destinations);
  return mergeDestinationUiState(
    incoming,
    priorDestinations?.length ? priorDestinations : [],
  );
}

export function needsHeroImageEnrichment(cards: DestCard[]): boolean {
  return cards.some((d) => {
    const u = String(d.image || "").trim();
    return !u || u.includes("placehold.co");
  });
}

export function mergeHeroImagesFromEnriched(
  base: DestCard[],
  enriched: Record<string, unknown>[],
): DestCard[] {
  const byId = new Map(
    enriched
      .filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === "object")
      .map((row) => [String(row.id ?? ""), row]),
  );
  return base.map((d) => {
    const row = byId.get(String(d.id));
    if (!row) return d;
    const img = row.image;
    if (typeof img !== "string" || !img.trim()) return d;
    const imgsRaw = row.images;
    const imgs = Array.isArray(imgsRaw)
      ? imgsRaw.filter((x): x is string => typeof x === "string")
      : undefined;
    const capped = imgs && imgs.length ? imgs.slice(0, 3) : [img];
    return {
      ...d,
      image: capped[0] ?? img,
      images: capped,
      itinerary_dirty: d.itinerary_dirty,
    };
  });
}

/** Merge a single hero-enriched row (from ``/enrich_hero_one``) into the matching card by id. */
export function mergeHeroOneCard(cards: DestCard[], row: Record<string, unknown>): DestCard[] {
  const id = String(row.id ?? "").trim();
  if (!id) return cards;
  const img = row.image;
  if (typeof img !== "string" || !img.trim()) return cards;
  const imgsRaw = row.images;
  const imgs = Array.isArray(imgsRaw)
    ? imgsRaw.filter((x): x is string => typeof x === "string")
    : undefined;
  const capped = imgs && imgs.length ? imgs.slice(0, 3) : [img];
  return cards.map((d) => {
    if (String(d.id) !== id) return d;
    return {
      ...d,
      image: capped[0] ?? img,
      images: capped,
      itinerary_dirty: d.itinerary_dirty,
    };
  });
}

export function mapDtoPillsToProfile(raw: WanderlyWorkspaceDTO["profile_pills"]): ProfilePill[] {
  return (raw || [])
    .filter((p) => String(p.key ?? "").trim() !== "tags")
    .map((p) => ({
      key: p.key,
      label: p.label,
      value: p.value,
      icon: p.icon || "📌",
      editable: p.editable !== false,
      options: p.options,
    }));
}

/** Overlay server pills onto the current sidebar so partial JSON never drops keys like ``from``. */
export function mergeProfilePillsFromDto(
  prior: ProfilePill[],
  incoming: WanderlyWorkspaceDTO["profile_pills"],
): ProfilePill[] {
  if (!incoming?.length) return prior;
  const mapped = mapDtoPillsToProfile(incoming);
  const byKey = new Map(prior.map((p) => [p.key, { ...p }]));
  for (const p of mapped) {
    byKey.set(p.key, p);
  }
  const order: string[] = [];
  for (const p of prior) {
    if (!order.includes(p.key)) order.push(p.key);
  }
  for (const p of mapped) {
    if (!order.includes(p.key)) order.push(p.key);
  }
  return order
    .map((k) => byKey.get(k))
    .filter((x): x is ProfilePill => Boolean(x))
    .filter((p) => p.key !== "tags");
}

/** Regex: user message explicitly asks to change day-by-day / schedule wording. */
const ITINERARY_INVALIDATE_MESSAGE_RE =
  /\b(itinerary|schedule|day-by-day|day\s*\d|days\s*\d|replace\s+day|change\s+day|revise(\s+the)?\s+plan|update\s+the\s+plan|update\s+(the\s+)?(itinerary|trip)|edit\s+days|swap\s+day|adjust\s+days|move\s+day|replan|redo\s+days|rebuild|regenerate|refresh\s+(the\s+)?(itinerary|plan)|shorten|extend|fewer\s+days|more\s+days)\b/i;

/** Normalize pill text so LLM dash/whitespace churn does not force itinerary rebuild. */
function normalizePillValueForSig(raw: string): string {
  let s = raw.trim().replace(/\s+/g, " ");
  s = s.replace(/\u2013|\u2014/g, "-");
  return s;
}

function profilePillsSignature(rows: ProfilePill[]): string {
  return [...rows]
    .map((x) => `${x.key}=${normalizePillValueForSig(x.value)}`)
    .sort()
    .join("|");
}

/**
 * Explains why a refine turn will mark every merged destination with ``itinerary_dirty: true``
 * (see ``applyWanderlyWorkspace``), which triggers lazy ``buildItinerary*`` for dirty cards (main-screen prefetch + expanded view).
 */
export function explainItineraryInvalidate(
  message: string,
  priorProfile: ProfilePill[],
  incomingPills: WanderlyWorkspaceDTO["profile_pills"] | null | undefined,
): { shouldInvalidate: boolean; reasons: string[] } {
  const reasons: string[] = [];
  const m = message.trim();
  if (ITINERARY_INVALIDATE_MESSAGE_RE.test(m)) {
    reasons.push("user_message_itinerary_keywords");
  }
  if (incomingPills?.length) {
    const merged = mergeProfilePillsFromDto(priorProfile, incomingPills);
    if (profilePillsSignature(merged) !== profilePillsSignature(priorProfile)) {
      reasons.push("profile_pills_changed_vs_prior");
    }
  }
  return { shouldInvalidate: reasons.length > 0, reasons };
}

/** After a refine message: only force lazy itinerary rebuild when profile changed or message targets the plan. */
export function refineShouldInvalidateItineraries(
  message: string,
  priorProfile: ProfilePill[],
  incomingPills: WanderlyWorkspaceDTO["profile_pills"] | null | undefined,
): boolean {
  return explainItineraryInvalidate(message, priorProfile, incomingPills).shouldInvalidate;
}

/** Apply server Wanderly DTO into TripStore setters (v0 smoke + v1 orchestrator). */
export function applyWanderlyWorkspace(
  w: WanderlyWorkspaceDTO,
  opts: {
    setProfile: (p: ProfilePill[]) => void;
    setDestinations: (d: DestCard[] | ((prev: DestCard[]) => DestCard[])) => void;
    addResponse: (r: Omit<AgentResponse, "id" | "ts">) => void;
    setEnrichmentPending?: (v: boolean) => void;
    /** Fallback when the store is still empty (first paint); merge otherwise uses latest ``prev`` from ``setDestinations``. */
    priorDestinations?: DestCard[];
    /** After a prompt-side criteria change (refine), treat all itineraries as stale until rebuilt. */
    markItinerariesStale?: boolean;
    /** Current sidebar pills — merged with server pills by key so partial updates keep ``from``, etc. */
    priorProfile?: ProfilePill[];
  },
) {
  const { setProfile, setDestinations, addResponse, setEnrichmentPending, priorDestinations, markItinerariesStale, priorProfile } =
    opts;
  if (w.profile_pills?.length) {
    setProfile(mergeProfilePillsFromDto(priorProfile ?? [], w.profile_pills));
  }
  if (w.destinations?.length) {
    const incoming = asDestCards(w.destinations);
    // Merge onto latest client cards (functional update) so a lazy-built itinerary that
    // landed while refine/smoke was in flight is not replaced by a stale prior snapshot
    // from when the pipeline started.
    setDestinations((prev) => {
      const mergeBase = prev.length > 0 ? prev : priorDestinations ?? [];
      const merged = mergeDestinationUiState(incoming, mergeBase, {
        bypassItineraryPreserve: Boolean(markItinerariesStale),
      });
      const final = markItinerariesStale ? merged.map((d) => ({ ...d, itinerary_dirty: true })) : merged;
      if (markItinerariesStale) {
        logItin("apply_workspace_mark_itineraries_stale", {
          note: "All merged destinations get itinerary_dirty=true → CommandBar + canvas schedule serialized build_itinerary prefetch",
          destCount: final.length,
          perCard: final.map((d) => ({
            id: d.id,
            pinned: d.pinned,
            itinLen: d.itinerary?.length ?? 0,
          })),
        });
      }
      return final;
    });
  }
  if (w.assistant_markdown?.trim()) {
    addResponse({ type: "message", role: "agent", text: w.assistant_markdown.trim() });
  }
  if (w.agent_status_hint) {
    addResponse({ type: "tip", text: w.agent_status_hint });
  }
  const adv = w.advisor_suggestion;
  if (adv && typeof adv === "object") {
    const msg = String((adv as { assistant_message?: string }).assistant_message || "").trim();
    if (msg) addResponse({ type: "info", text: msg.slice(0, 4000) });
  }
  setEnrichmentPending?.(Boolean(w.enrichment_pending));
}
