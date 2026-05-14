import type { WanderlyTripMeta } from "../../types/workspace";

const TRIP_INDEX_KEY = "wanderly_trip_index_v1";
const MAX_INDEX = 50;
const MAX_NAME_LEN = 200;

function metaKey(sessionId: string): string {
  return `wanderly_trip_meta_${sessionId}`;
}

export function loadTripMetaForSession(sessionId: string): WanderlyTripMeta | null {
  const sid = String(sessionId || "").trim();
  if (!sid) return null;
  try {
    const raw = localStorage.getItem(metaKey(sid));
    if (!raw) return null;
    const o = JSON.parse(raw) as unknown;
    if (!o || typeof o !== "object") return null;
    const rec = o as Record<string, unknown>;
    const trip_id = String(rec.trip_id || "").trim();
    const trip_name = String(rec.trip_name || "").trim();
    if (!trip_id && !trip_name) return null;
    return { trip_id: trip_id || crypto.randomUUID(), trip_name: trip_name || "Untitled trip" };
  } catch {
    return null;
  }
}

export function saveTripMetaForSession(sessionId: string, meta: WanderlyTripMeta): void {
  const sid = String(sessionId || "").trim();
  if (!sid) return;
  const trip_id = String(meta.trip_id || "").trim() || crypto.randomUUID();
  const trip_name = (String(meta.trip_name || "").trim() || "Untitled trip").slice(0, MAX_NAME_LEN);
  try {
    localStorage.setItem(metaKey(sid), JSON.stringify({ trip_id, trip_name }));
  } catch {
    /* quota / private mode */
  }
}

export type SavedTripIndexEntry = {
  trip_id: string;
  session_id: string;
  trip_name: string;
  saved_at: string;
};

function readTripIndex(): SavedTripIndexEntry[] {
  try {
    const raw = localStorage.getItem(TRIP_INDEX_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw) as unknown;
    if (!Array.isArray(arr)) return [];
    return arr
      .filter((x) => x && typeof x === "object")
      .map((x) => x as Record<string, unknown>)
      .map((x) => ({
        trip_id: String(x.trip_id || "").trim(),
        session_id: String(x.session_id || "").trim(),
        trip_name: String(x.trip_name || "").trim().slice(0, MAX_NAME_LEN) || "Untitled trip",
        saved_at: String(x.saved_at || new Date().toISOString()),
      }))
      .filter((x) => x.session_id && x.trip_id);
  } catch {
    return [];
  }
}

function writeTripIndex(entries: SavedTripIndexEntry[]): void {
  try {
    localStorage.setItem(TRIP_INDEX_KEY, JSON.stringify(entries.slice(0, MAX_INDEX)));
  } catch {
    /* ignore */
  }
}

/** Read the ordered catalog of trips saved on this browser (newest first). */
export function listSavedTripsFromStorage(): SavedTripIndexEntry[] {
  return readTripIndex();
}

/** Remove one trip from the saved list (by server session id). */
export function removeTripFromSavedIndex(sessionId: string): void {
  const sid = String(sessionId || "").trim();
  if (!sid) return;
  const next = readTripIndex().filter((x) => x.session_id !== sid);
  writeTripIndex(next);
}

/** Drop local trip-name cache for a session (e.g. after delete). */
export function clearTripMetaForSession(sessionId: string): void {
  const sid = String(sessionId || "").trim();
  if (!sid) return;
  try {
    localStorage.removeItem(metaKey(sid));
  } catch {
    /* ignore */
  }
}

/** Append or refresh an entry so future steps can list saved trips (keyed by ``session_id``). */
export function recordTripInSavedIndex(entry: {
  trip_id: string;
  session_id: string;
  trip_name: string;
  saved_at?: string;
}): void {
  const session_id = String(entry.session_id || "").trim();
  const trip_id = String(entry.trip_id || "").trim();
  if (!session_id || !trip_id) return;
  const trip_name = (String(entry.trip_name || "").trim() || "Untitled trip").slice(0, MAX_NAME_LEN);
  const saved_at = entry.saved_at ?? new Date().toISOString();
  const next = readTripIndex().filter((x) => x.session_id !== session_id);
  next.unshift({ trip_id, session_id, trip_name, saved_at });
  writeTripIndex(next);
}

export function resolveTripMetaForSession(
  sessionId: string,
  serverTm?: WanderlyTripMeta | null,
): WanderlyTripMeta {
  const sid = String(sessionId || "").trim();
  const sId = String(serverTm?.trip_id || "").trim();
  const sName = String(serverTm?.trip_name || "").trim();
  const loc = sid ? loadTripMetaForSession(sid) : null;
  const trip_id = sId || String(loc?.trip_id || "").trim() || crypto.randomUUID();
  const trip_name = (sName || String(loc?.trip_name || "").trim() || "Untitled trip").slice(0, MAX_NAME_LEN);
  return { trip_id, trip_name };
}
