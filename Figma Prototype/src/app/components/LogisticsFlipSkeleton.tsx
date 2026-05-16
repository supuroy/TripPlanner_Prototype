import { Loader2, Route } from "lucide-react";

/** Placeholder while the logistics flip chunk loads (code-split). */
export function LogisticsFlipSkeleton() {
  return (
    <div
      className="flex min-h-[280px] flex-col rounded-xl border border-white/[0.08] bg-[#0c0c12] p-6"
      role="status"
      aria-live="polite"
      aria-label="Loading trip logistics"
    >
      <div className="flex items-center gap-2 border-b border-white/[0.06] pb-3">
        <Route className="h-4 w-4 text-indigo-300/60" aria-hidden />
        <span className="text-[11px] font-medium uppercase tracking-wider text-indigo-300/70">
          Logistics &amp; transport
        </span>
        <Loader2 className="ml-auto h-4 w-4 animate-spin text-indigo-400/80" aria-hidden />
      </div>
      <div className="mt-6 flex-1 space-y-3 animate-pulse">
        <div className="h-10 rounded-lg bg-white/[0.06]" />
        <div className="grid grid-cols-2 gap-2">
          <div className="h-16 rounded-lg bg-white/[0.04]" />
          <div className="h-16 rounded-lg bg-white/[0.04]" />
        </div>
        <div className="h-24 rounded-lg bg-white/[0.04]" />
        <div className="h-20 rounded-lg bg-white/[0.04]" />
      </div>
    </div>
  );
}

