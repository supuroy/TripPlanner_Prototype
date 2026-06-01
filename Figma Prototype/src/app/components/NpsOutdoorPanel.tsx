import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Loader2,
  MapPin,
  RefreshCw,
  Shield,
  Tent,
  Ticket,
} from "lucide-react";
import {
  fetchNpsOutdoorEnrich,
  syncUi,
  type NpsOutdoorEnrichResponse,
} from "../../api/wanderly";
import { googleMapsPinUrl } from "../lib/mapLinks";
import { canOfferNpsOutdoorEnrich } from "../lib/outdoorTrip";
import { useTrip, type DestCard, type ProfilePill } from "./TripStore";

type Props = {
  dest: DestCard;
  profile: ProfilePill[];
};

function CollapsibleSubCard({
  title,
  subtitle,
  defaultOpen = false,
  borderClass = "border-white/10",
  children,
}: {
  title: ReactNode;
  subtitle?: string;
  defaultOpen?: boolean;
  borderClass?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className={`rounded-lg border bg-black/20 ${borderClass}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-start gap-2 px-2.5 py-2 text-left transition-colors hover:bg-white/[0.03]"
      >
        <div className="min-w-0 flex-1">{title}</div>
        {!open && subtitle ? (
          <span className="max-w-[45%] shrink-0 truncate text-[10px] text-white/40">{subtitle}</span>
        ) : null}
        {open ? (
          <ChevronUp className="mt-0.5 h-3.5 w-3.5 shrink-0 text-white/35" aria-hidden />
        ) : (
          <ChevronDown className="mt-0.5 h-3.5 w-3.5 shrink-0 text-white/35" aria-hidden />
        )}
      </button>
      {open ? <div className="border-t border-white/[0.06] px-2.5 pb-2.5 pt-2">{children}</div> : null}
    </div>
  );
}

function LinkRow({ label, url }: { label: string; url: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-[11px] text-sky-300/90 underline decoration-sky-500/30 underline-offset-2 hover:text-sky-200"
    >
      {label}
      <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
    </a>
  );
}

export function NpsOutdoorPanel({ dest, profile }: Props) {
  const {
    sessionId,
    wanderlyTripId,
    tripName,
    destinations,
    setDestinations,
    beginTripDiskSave,
    completeTripDiskSave,
    failTripDiskSave,
  } = useTrip();

  const [data, setData] = useState<NpsOutdoorEnrichResponse | null>(dest.nps_outdoor ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(Boolean(dest.nps_outdoor));

  const eligible = canOfferNpsOutdoorEnrich(dest, profile);

  useEffect(() => {
    setData(dest.nps_outdoor ?? null);
    if (dest.nps_outdoor) setExpanded(true);
  }, [dest.id, dest.nps_outdoor]);

  const persistNps = useCallback(
    (nps: NpsOutdoorEnrichResponse) => {
      const sid = sessionId.trim();
      if (!sid) return;
      setDestinations((prev) => {
        const next = prev.map((d) => (d.id === dest.id ? { ...d, nps_outdoor: nps } : d));
        beginTripDiskSave();
        void syncUi(sid, next as unknown[], {
          tripId: wanderlyTripId?.trim() || undefined,
          profilePills: profile.map((p) => ({
            key: p.key,
            label: p.label,
            value: p.value,
            icon: p.icon,
            editable: p.editable !== false,
          })),
        })
          .then((r) => completeTripDiskSave(next, { savedToDisk: Boolean(r.saved_to_disk) }))
          .catch(() => failTripDiskSave());
        return next;
      });
    },
    [
      sessionId,
      dest.id,
      wanderlyTripId,
      profile,
      setDestinations,
      beginTripDiskSave,
      completeTripDiskSave,
      failTripDiskSave,
    ],
  );

  const load = useCallback(
    async (refresh: boolean) => {
      const sid = sessionId.trim();
      if (!sid || !eligible) return;
      setLoading(true);
      setError(null);
      setExpanded(true);
      try {
        const peerDestinationNames = destinations
          .filter((d) => d.id !== dest.id)
          .map((d) => d.name.trim())
          .filter(Boolean);
        const res = await fetchNpsOutdoorEnrich(
          sid,
          dest as unknown as Record<string, unknown>,
          profile.map((p) => ({
            key: p.key,
            label: p.label,
            value: p.value,
            icon: p.icon,
          })),
          { refresh, tripName, peerDestinationNames },
        );
        setData(res);
        persistNps(res);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [sessionId, dest, profile, eligible, persistNps, tripName, destinations],
  );

  if (!eligible) return null;

  const alerts = data?.alerts ?? [];
  const campMatches = data?.camp_matches ?? [];
  const parks = data?.parks ?? [];
  const permits = data?.permits_from_plan ?? [];
  const feeLinks = data?.fee_links ?? [];
  const hasLoaded = Boolean(data?.fetched_at || data?.ok);

  if (!expanded && !hasLoaded) {
    return (
      <div className="mt-4 rounded-xl border border-dashed border-amber-500/25 bg-amber-500/[0.03] px-3 py-3">
        <p className="text-[10px] font-medium uppercase tracking-wider text-amber-200/80">
          U.S. National Park Service
        </p>
        <p className="mt-1 text-[11px] leading-relaxed text-white/45">
          Optional: pull live alerts, developed campground listings, and entrance fees from the{" "}
          <a
            href="https://www.nps.gov/subjects/developer/api-documentation.htm"
            target="_blank"
            rel="noopener noreferrer"
            className="text-amber-200/70 underline hover:text-amber-100"
          >
            NPS Data API
          </a>{" "}
          for this U.S. destination. Results are saved to your trip file.
        </p>
        <button
          type="button"
          data-testid="wanderly-nps-load-button"
          onClick={() => void load(false)}
          disabled={loading || !sessionId.trim()}
          className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg border border-amber-500/35 bg-amber-500/15 px-3 py-2.5 text-[12px] font-medium text-amber-100/95 transition-colors hover:border-amber-400/45 hover:bg-amber-500/22 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden />
          ) : (
            <Shield className="h-4 w-4 shrink-0" aria-hidden />
          )}
          {loading ? "Loading NPS data…" : "Check NPS alerts, camps & fees"}
        </button>
        {error ? <p className="mt-2 text-[11px] text-rose-300/90">{error}</p> : null}
      </div>
    );
  }

  return (
    <section
      className="mt-4 rounded-xl border border-amber-500/20 bg-amber-500/[0.04]"
      aria-labelledby={`nps-outdoor-${dest.id}`}
    >
      <div className="flex items-start justify-between gap-2 border-b border-amber-500/15 px-3 py-2.5">
        <div className="min-w-0">
          <h4
            id={`nps-outdoor-${dest.id}`}
            className="text-[10px] font-semibold uppercase tracking-wider text-amber-200/90"
          >
            Park alerts & permits
          </h4>
          <p className="mt-0.5 text-[10px] text-white/40">
            U.S. NPS · saved to trip
            {data?.park_codes?.length
              ? ` · ${data.park_codes.join(", ").toUpperCase()}`
              : null}
            {data?.resolve_method ? ` · ${data.resolve_method}` : null}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load(true)}
          disabled={loading}
          className="inline-flex shrink-0 items-center gap-1 rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-[10px] font-medium text-amber-100/90 hover:bg-amber-500/15 disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="h-3 w-3" aria-hidden />
          )}
          Refresh
        </button>
      </div>

      <div className="space-y-3 px-3 py-3">
        {error ? (
          <p className="text-[11px] text-rose-300/90">{error}</p>
        ) : null}

        {data?.message ? (
          <p className="text-[11px] leading-relaxed text-white/55">{data.message}</p>
        ) : null}

        {!data?.configured && data?.in_scope !== false ? (
          <p className="text-[11px] text-amber-200/80">
            Add <code className="text-[10px] text-white/60">NPS_API_KEY</code> to the server{" "}
            <code className="text-[10px] text-white/60">.env</code> (free from{" "}
            <a
              href="https://www.nps.gov/subjects/developer/get-started.htm"
              target="_blank"
              rel="noopener noreferrer"
              className="underline"
            >
              developer.nps.gov
            </a>
            ).
          </p>
        ) : null}

        {loading && !data ? (
          <p className="flex items-center gap-2 text-[11px] text-white/45">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            Loading NPS alerts and campground data…
          </p>
        ) : null}

        {alerts.length > 0 ? (
          <div>
            <p className="mb-1.5 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-amber-300/80">
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
              Active alerts
            </p>
            <ul className="space-y-2">
              {alerts.slice(0, 8).map((a, i) => (
                <li key={`${a.title}-${i}`}>
                  <CollapsibleSubCard
                    borderClass="border-amber-500/20"
                    defaultOpen={i === 0}
                    subtitle={a.category ?? "Alert"}
                    title={
                      <p className="text-[11px] font-medium text-amber-100/95">{a.title}</p>
                    }
                  >
                    {a.category ? (
                      <p className="text-[9px] uppercase tracking-wide text-white/35">{a.category}</p>
                    ) : null}
                    {a.description ? (
                      <p className="mt-1 text-[10px] leading-snug text-white/50">{a.description}</p>
                    ) : null}
                    {a.url ? (
                      <div className="mt-1.5">
                        <LinkRow label="Details on NPS.gov" url={a.url} />
                      </div>
                    ) : null}
                  </CollapsibleSubCard>
                </li>
              ))}
            </ul>
          </div>
        ) : hasLoaded && data?.configured ? (
          <p className="text-[10px] text-white/40">No active NPS alerts for this park right now.</p>
        ) : null}

        {campMatches.length > 0 ? (
          <div>
            <p className="mb-1.5 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-lime-300/80">
              <Tent className="h-3.5 w-3.5" aria-hidden />
              Camps in your plan (NPS listings)
            </p>
            <ul className="space-y-2">
              {campMatches.map((c, idx) => {
                const mapUrl = googleMapsPinUrl(c.latitude, c.longitude, c.name ?? undefined);
                return (
                <li key={`${c.plan_camp_label ?? "camp"}-${idx}`}>
                  <CollapsibleSubCard
                    borderClass="border-lime-500/15"
                    defaultOpen
                    subtitle={c.name ? `Matched: ${c.name}` : "No NPS listing"}
                    title={
                      <p className="text-[11px] font-medium text-white/85">{c.plan_camp_label}</p>
                    }
                  >
                    {c.name ? (
                      <p className="flex items-center gap-1.5 text-[10px] text-lime-200/80">
                        {mapUrl ? (
                          <a
                            href={mapUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Open campground on Google Maps"
                            aria-label={`Open ${c.name} on Google Maps`}
                            className="inline-flex shrink-0 rounded p-0.5 text-lime-300/90 transition-colors hover:bg-lime-500/15 hover:text-lime-200"
                          >
                            <MapPin className="h-3.5 w-3.5" aria-hidden />
                          </a>
                        ) : (
                          <MapPin className="h-3 w-3 shrink-0 opacity-40" aria-hidden />
                        )}
                        <span>
                          Matched: {c.name}
                          {mapUrl ? (
                            <>
                              {" "}
                              ·{" "}
                              <a
                                href={mapUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-sky-300/90 underline decoration-sky-500/30 underline-offset-2 hover:text-sky-200"
                              >
                                Map
                              </a>
                            </>
                          ) : null}
                        </span>
                      </p>
                    ) : (
                      <p className="text-[10px] text-white/45">
                        {c.notes ?? "Not listed as an NPS developed campground (may be backcountry)."}
                      </p>
                    )}
                    {c.operating_hours ? (
                      <p className="mt-1 text-[10px] text-white/40">Hours: {c.operating_hours}</p>
                    ) : null}
                    {c.fee_summary?.length ? (
                      <p className="mt-0.5 text-[10px] text-white/40">
                        Fees: {c.fee_summary.join(" · ")}
                      </p>
                    ) : null}
                    {c.reservation_url || mapUrl ? (
                      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
                        {mapUrl ? <LinkRow label="View on Google Maps" url={mapUrl} /> : null}
                        {c.reservation_url ? (
                          <LinkRow label="Reservations" url={c.reservation_url} />
                        ) : null}
                      </div>
                    ) : null}
                  </CollapsibleSubCard>
                </li>
              );
              })}
            </ul>
          </div>
        ) : null}

        {(permits.length > 0 ||
          parks.some((p) => p.entrance_fees?.length || p.entrance_passes?.length)) ? (
          <div>
            <p className="mb-1.5 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-indigo-300/80">
              <Ticket className="h-3.5 w-3.5" aria-hidden />
              Permits & fees
            </p>
            <div className="space-y-2">
              {permits.map((p, i) => (
                <CollapsibleSubCard
                  key={`${p.name}-${i}`}
                  borderClass="border-indigo-500/15"
                  defaultOpen={i === 0}
                  subtitle={p.agency ?? (p.required ? "Required" : "Permit")}
                  title={
                    <p className="text-[11px] font-medium text-white/85">
                      {p.name}
                      {p.required ? (
                        <span className="ml-1 text-[9px] font-normal text-rose-300/80">(required)</span>
                      ) : null}
                    </p>
                  }
                >
                  {p.agency ? <p className="text-[10px] text-white/45">{p.agency}</p> : null}
                  {p.quota_window ? (
                    <p className="mt-0.5 text-[10px] text-white/45">{p.quota_window}</p>
                  ) : null}
                  {p.notes ? (
                    <p className="mt-1 text-[10px] leading-snug text-white/50">{p.notes}</p>
                  ) : null}
                  {p.booking_url_hint ? (
                    <p className="mt-1 text-[10px] text-sky-300/80">{p.booking_url_hint}</p>
                  ) : null}
                </CollapsibleSubCard>
              ))}
              {parks.map((park) => {
                const feeN = park.entrance_fees?.length ?? 0;
                const passN = park.entrance_passes?.length ?? 0;
                const summary =
                  feeN + passN > 0
                    ? `${feeN} entrance fee${feeN === 1 ? "" : "s"}${passN ? ` · ${passN} pass${passN === 1 ? "" : "es"}` : ""}`
                    : "Fees on NPS.gov";
                return (
                  <CollapsibleSubCard
                    key={park.park_code ?? park.park_name}
                    borderClass="border-indigo-500/15"
                    defaultOpen={false}
                    subtitle={summary}
                    title={
                      park.park_name ? (
                        <p className="text-[11px] font-medium text-white/85">{park.park_name}</p>
                      ) : (
                        <p className="text-[11px] font-medium text-white/85">Park fees</p>
                      )
                    }
                  >
                    {park.entrance_fees?.map((ef, j) => (
                      <p key={j} className="mt-1 text-[10px] text-white/55 first:mt-0">
                        <span className="text-white/70">{ef.title}</span>
                        {ef.cost ? ` — ${ef.cost}` : ""}
                        {ef.description ? (
                          <span className="block text-white/40">{ef.description}</span>
                        ) : null}
                      </p>
                    ))}
                    {park.entrance_passes?.map((ep, j) => (
                      <p key={`p-${j}`} className="mt-1 text-[10px] text-white/55">
                        <span className="text-white/70">{ep.title}</span>
                        {ep.cost ? ` — ${ep.cost}` : ""}
                      </p>
                    ))}
                    {park.url ? (
                      <div className="mt-1.5">
                        <LinkRow label="Park fees & passes on NPS.gov" url={park.url} />
                      </div>
                    ) : null}
                  </CollapsibleSubCard>
                );
              })}
            </div>
            {feeLinks.map((fl) =>
              fl.url ? (
                <div key={fl.url} className="mt-1">
                  <LinkRow label={fl.label ?? "Fee information"} url={fl.url} />
                </div>
              ) : null,
            )}
          </div>
        ) : null}
      </div>
    </section>
  );
}
