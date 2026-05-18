import { CheckCircle2, Circle, Loader2 } from "lucide-react";
import type { BookingProgressStep } from "../lib/bookingSearchProgress";

export function BookingSearchProgressStrip({
  primary,
  secondary,
  streamPreview,
  steps,
  variant = "strip",
}: {
  primary: string;
  secondary?: string;
  /** Rolling LLM stream tail from the server. */
  streamPreview?: string;
  steps?: BookingProgressStep[];
  /** `strip` = banner above timeline; `inline` = in-place flight/hotel slot. */
  variant?: "strip" | "inline";
}) {
  const headline = primary.trim() || streamPreview?.trim() || "";
  const recentSteps = (steps ?? []).filter((s) => s.status !== "pending").slice(-8);
  if (!headline && !recentSteps.length) return null;
  return (
    <div
      className={
        variant === "strip"
          ? "mb-4 rounded-xl border border-sky-500/25 bg-sky-500/[0.08] px-3 py-2.5"
          : "min-h-[7.5rem] rounded-xl border border-sky-500/25 bg-sky-500/[0.08] px-3 py-3"
      }
      role="status"
      aria-live="polite"
    >
      {headline ? (
        <div className="flex items-start gap-2.5">
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-sky-300/90" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-[12px] font-medium leading-snug text-sky-100/95">{headline}</p>
            {secondary?.trim() ? (
              <p className="mt-0.5 text-[11px] leading-snug text-sky-200/55">{secondary}</p>
            ) : null}
            {streamPreview?.trim() && streamPreview.trim() !== headline ? (
              <p className="mt-1.5 max-h-16 overflow-hidden text-[10px] leading-snug text-sky-200/45">
                {streamPreview.trim()}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
      {recentSteps.length ? (
        <ul className={`space-y-1 ${headline ? "mt-2.5 border-t border-sky-500/15 pt-2" : ""}`}>
          {recentSteps.map((s) => (
            <li key={s.key} className="flex items-start gap-2 text-[10px] leading-snug text-sky-200/70">
              {s.status === "done" ? (
                <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-400/90" aria-hidden />
              ) : s.status === "active" ? (
                <Loader2 className="mt-0.5 h-3 w-3 shrink-0 animate-spin text-sky-300/80" aria-hidden />
              ) : (
                <Circle className="mt-0.5 h-3 w-3 shrink-0 text-sky-400/40" aria-hidden />
              )}
              <span className="min-w-0">
                <span className="font-medium text-sky-100/85">{s.label}</span>
                {s.detail ? <span className="text-sky-200/50"> — {s.detail}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** Same UI as the progress strip, sized for a timeline row (flight/hotel slot). */
export function BookingSearchProgressCard(
  props: Omit<Parameters<typeof BookingSearchProgressStrip>[0], "variant">,
) {
  return <BookingSearchProgressStrip {...props} variant="inline" />;
}
