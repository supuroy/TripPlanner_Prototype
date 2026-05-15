import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, FolderOpen, Trash2 } from "lucide-react";
import { listSavedTripsFromStorage, type SavedTripIndexEntry } from "../lib/tripMetaStorage";
import { useTrip } from "./TripStore";

function formatSavedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function SavedTripsMenu({ variant = "header" }: { variant?: "header" | "idle" }) {
  const {
    sessionId,
    wanderlyTripId,
    switchToSavedTrip,
    deleteSavedTrip,
    wanderlyWorkspaceBootstrapped,
    newTripTransitioning,
    tripSwitchTransitioning,
  } = useTrip();
  const [open, setOpen] = useState(false);
  const [listTick, setListTick] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const [confirm, setConfirm] = useState<{ sessionId: string; name: string } | null>(null);

  const busy = newTripTransitioning || tripSwitchTransitioning;

  useEffect(() => {
    if (!open) return;
    setListTick((n) => n + 1);
  }, [open]);

  const trips = useMemo(() => listSavedTripsFromStorage(), [open, listTick, sessionId]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (confirm) return;
      const el = rootRef.current;
      if (el && !el.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open, confirm]);

  const refreshList = useCallback(() => setListTick((n) => n + 1), []);

  const onOpenTrip = useCallback(
    async (t: SavedTripIndexEntry) => {
      const tripId = t.trip_id.trim();
      if (busy || !tripId || tripId === wanderlyTripId.trim()) return;
      setOpen(false);
      await switchToSavedTrip(tripId);
      refreshList();
    },
    [busy, wanderlyTripId, switchToSavedTrip, refreshList],
  );

  const requestDelete = useCallback((e: React.MouseEvent, t: SavedTripIndexEntry) => {
    e.stopPropagation();
    setConfirm({ sessionId: t.session_id, name: t.trip_name });
  }, []);

  const cancelDelete = useCallback(() => setConfirm(null), []);

  const confirmDelete = useCallback(async () => {
    if (!confirm) return;
    const sid = confirm.sessionId;
    setConfirm(null);
    setOpen(false);
    await deleteSavedTrip(sid);
    refreshList();
  }, [confirm, deleteSavedTrip, refreshList]);

  if (!wanderlyWorkspaceBootstrapped) return null;

  const btnClass =
    variant === "idle"
      ? "inline-flex items-center gap-2 rounded-xl border border-white/[0.12] bg-white/[0.06] px-4 py-2.5 text-[13px] text-white/75 transition-colors hover:bg-white/[0.1] hover:text-white/90 disabled:cursor-wait disabled:opacity-40"
      : "inline-flex items-center gap-1.5 rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-[12px] text-white/55 transition-colors hover:bg-white/[0.07] hover:text-white/85 disabled:cursor-wait disabled:opacity-40";

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        className={btnClass}
        disabled={busy}
        title={busy ? "Please wait…" : "Open a trip you saved on this device"}
        onClick={() => setOpen((o) => !o)}
      >
        <FolderOpen className={variant === "idle" ? "h-4 w-4 shrink-0 text-indigo-300/90" : "h-3.5 w-3.5 shrink-0"} />
        <span>Saved trips</span>
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div
          className={
            variant === "idle"
              ? "absolute left-0 right-0 top-full z-50 mt-2 w-full rounded-xl border border-white/[0.1] bg-[#12121a] py-2 shadow-xl"
              : "absolute right-0 top-full z-50 mt-1.5 w-[min(100vw-2rem,20rem)] rounded-xl border border-white/[0.1] bg-[#12121a] py-2 shadow-xl"
          }
          role="menu"
        >
          {trips.length === 0 ? (
            <p className="px-3 py-4 text-center text-[12px] text-white/40">
              No saved trips yet. Use <span className="text-white/55">New trip</span> when you want to park the
              current plan — it will appear here.
            </p>
          ) : (
            <ul className="max-h-[min(60vh,22rem)] overflow-y-auto">
              {trips.map((t) => {
                const isCurrent = t.trip_id === wanderlyTripId;
                return (
                  <li
                    key={t.session_id}
                    className="flex items-start gap-2 border-b border-white/[0.04] px-3 py-2.5 last:border-b-0"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-medium text-white/85">{t.trip_name}</p>
                      <p className="text-[10px] text-white/35">{formatSavedAt(t.saved_at)}</p>
                      {isCurrent ? (
                        <span className="mt-1 inline-block rounded bg-indigo-500/20 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-indigo-200/90">
                          Current
                        </span>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 flex-col gap-1">
                      <button
                        type="button"
                        role="menuitem"
                        disabled={busy || isCurrent}
                        onClick={() => void onOpenTrip(t)}
                        className="rounded-md bg-white/[0.08] px-2 py-1 text-[11px] text-white/75 hover:bg-white/[0.12] disabled:cursor-not-allowed disabled:opacity-35"
                      >
                        Open
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        disabled={busy}
                        title="Remove from this device’s saved list"
                        onClick={(e) => requestDelete(e, t)}
                        className="inline-flex items-center justify-center gap-1 rounded-md border border-red-500/25 px-2 py-1 text-[11px] text-red-200/90 hover:bg-red-500/10 disabled:opacity-35"
                      >
                        <Trash2 className="h-3 w-3" />
                        Delete
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      {confirm && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/65 px-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="saved-trip-delete-title"
        >
          <div className="w-full max-w-md rounded-2xl border border-white/[0.12] bg-[#14141c] p-5 shadow-2xl">
            <h2 id="saved-trip-delete-title" className="text-[15px] font-medium text-white/90">
              Delete saved trip?
            </h2>
            <p className="mt-2 text-[13px] leading-relaxed text-white/55">
              Remove <span className="font-medium text-white/80">&quot;{confirm.name}&quot;</span> from your saved
              list on this browser. This does not erase cloud data immediately, but you will not be able to reopen it
              from this list.
            </p>
            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <button
                type="button"
                onClick={cancelDelete}
                className="rounded-lg border border-white/[0.12] bg-white/[0.04] px-4 py-2 text-[12px] text-white/75 hover:bg-white/[0.08]"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmDelete()}
                disabled={busy}
                className="rounded-lg bg-red-600/90 px-4 py-2 text-[12px] font-medium text-white hover:bg-red-500 disabled:opacity-40"
              >
                Delete trip
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
