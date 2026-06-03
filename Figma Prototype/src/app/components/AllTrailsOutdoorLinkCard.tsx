import { ExternalLink, Mountain } from "lucide-react";
import { allTrailsLinkLabelForDest, resolveAllTrailsUrlForDest } from "../lib/formatHikingLogistics";
import { isOutdoorAdventureProfile } from "../lib/outdoorTrip";
import type { DestCard, ProfilePill } from "./TripStore";

type Props = {
  dest: DestCard;
  profile: ProfilePill[];
};

/** Catalog- or build-verified AllTrails trail page on the logistics flip face. */
export function AllTrailsOutdoorLinkCard({ dest, profile }: Props) {
  if (!isOutdoorAdventureProfile(profile)) return null;
  const url = resolveAllTrailsUrlForDest(dest);
  if (!url) return null;
  const label = allTrailsLinkLabelForDest(dest);
  const catalog = dest.grounding?.status === "catalog";

  return (
    <div className="mb-3 rounded-xl border border-emerald-500/20 bg-emerald-950/25">
      <div className="flex items-start gap-2.5 px-3 py-2.5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-emerald-500/15">
          <Mountain className="h-4 w-4 text-emerald-300/90" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-medium uppercase tracking-wider text-emerald-300/75">AllTrails</p>
          <p className="mt-0.5 text-[12px] font-medium leading-snug text-white/85">{label}</p>
          <p className="mt-0.5 text-[10px] text-white/40">
            {catalog ? "Trail stats from offline catalog" : "Trail reference from your plan"}
          </p>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-2 inline-flex items-center gap-1 rounded-md border border-emerald-500/25 bg-emerald-500/10 px-2.5 py-1.5 text-[11px] font-medium text-emerald-200/95 transition-colors hover:bg-emerald-500/15"
          >
            Open on AllTrails
            <ExternalLink className="h-3 w-3 shrink-0 opacity-80" aria-hidden />
          </a>
        </div>
      </div>
    </div>
  );
}
