import { useCallback, useState } from "react";
import { FileDown, Loader2 } from "lucide-react";
import type { DestCard, ProfilePill } from "./TripStore";
import { useTrip } from "./TripStore";
import { exportItineraryPdf } from "../../api/wanderly";

function triggerBlobDownload(blob: Blob, filename: string) {
  if (blob.size < 512) {
    throw new Error("Export returned an empty file. Try again after the itinerary finishes loading.");
  }
  const header = blob.type === "application/pdf" || blob.type === "";
  if (!header && blob.type.includes("json")) {
    throw new Error("Server returned an error instead of a PDF.");
  }
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || "itinerary.pdf";
    a.rel = "noopener";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 4000);
  }
}

type ExportItineraryPdfButtonProps = {
  dest: DestCard;
  profile: ProfilePill[];
  /** Compact icon in expanded-card chrome vs labeled chip in content header */
  variant?: "toolbar" | "chip";
  className?: string;
};

export function ExportItineraryPdfButton({
  dest,
  profile,
  variant = "toolbar",
  className = "",
}: ExportItineraryPdfButtonProps) {
  const { sessionId, addResponse } = useTrip();
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const itineraryReady = (dest.itinerary?.length ?? 0) > 0;

  const onExport = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      e.preventDefault();
      const sid = sessionId.trim();
      if (!sid || !itineraryReady || busy) return;
      setBusy(true);
      setLocalError(null);
      try {
        const ac = new AbortController();
        const timer = window.setTimeout(() => ac.abort(), 120_000);
        try {
          const { blob, filename } = await exportItineraryPdf(
            sid,
            dest as unknown as Record<string, unknown>,
            profile,
            { signal: ac.signal },
          );
          triggerBlobDownload(blob, filename);
        } finally {
          window.clearTimeout(timer);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setLocalError(msg);
        addResponse({
          type: "warning",
          text: `Could not export itinerary PDF: ${msg}`,
          relatedTo: dest.id,
        });
      } finally {
        setBusy(false);
      }
    },
    [sessionId, dest, profile, itineraryReady, busy, addResponse],
  );

  if (!itineraryReady) return null;

  const disabled = busy || !sessionId.trim();
  const title = localError
    ? `Export failed: ${localError}`
    : "Download itinerary PDF";

  if (variant === "chip") {
    return (
      <button
        type="button"
        onClick={onExport}
        disabled={disabled}
        title={title}
        aria-busy={busy}
        className={`inline-flex items-center gap-1.5 rounded-lg border border-white/[0.1] bg-white/[0.06] px-2.5 py-1.5 text-[11px] font-medium text-white/85 transition hover:bg-white/[0.1] disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      >
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <FileDown className="h-3.5 w-3.5" aria-hidden />}
        {busy ? "Exporting…" : "Export PDF"}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onExport}
      disabled={disabled}
      title={title}
      aria-label={title}
      aria-busy={busy}
      className={`flex h-7 w-7 items-center justify-center rounded-lg backdrop-blur-sm transition-all ${
        disabled
          ? "cursor-not-allowed bg-black/40 text-white/30 opacity-50"
          : "cursor-pointer bg-black/50 text-white/80 hover:bg-black/70 hover:text-white"
      } ${className}`}
    >
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <FileDown className="h-3.5 w-3.5" aria-hidden />}
    </button>
  );
}
