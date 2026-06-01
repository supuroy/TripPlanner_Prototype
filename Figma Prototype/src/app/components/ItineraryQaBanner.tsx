import { AlertTriangle } from "lucide-react";
import type { ItineraryQaResult } from "../../api/wanderly";

type Props = {
  qa?: ItineraryQaResult | null;
};

export function ItineraryQaBanner({ qa }: Props) {
  if (!qa || qa.ok !== false) return null;
  const issues = (qa.issues ?? []).filter((x) => x.severity === "error" || x.severity === "warning");
  if (!issues.length) return null;

  return (
    <div
      className="mb-4 rounded-xl border border-amber-500/35 bg-amber-500/[0.08] px-3 py-3"
      role="alert"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-medium text-amber-100/95">Route continuity check</p>
          <p className="mt-0.5 text-[10px] text-amber-100/65">
            {qa.error_count ?? 0} error(s), {qa.warning_count ?? 0} warning(s) — verify camps and hike
            legs before relying on this plan in the backcountry.
          </p>
          <ul className="mt-2 space-y-1.5">
            {issues.slice(0, 5).map((issue, i) => (
              <li key={`${issue.code}-${issue.day}-${i}`} className="text-[10px] leading-snug text-amber-50/80">
                {issue.day ? <span className="font-medium text-amber-100/90">Day {issue.day}: </span> : null}
                {issue.message}
              </li>
            ))}
          </ul>
          {issues.length > 5 ? (
            <p className="mt-1 text-[10px] text-amber-100/50">+{issues.length - 5} more</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
