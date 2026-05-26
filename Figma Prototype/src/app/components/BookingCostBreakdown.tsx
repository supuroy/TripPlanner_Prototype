import { Plane, Hotel, Luggage } from "lucide-react";
import type { DestCard } from "./TripStore";
import {
  activitySum,
  commercialFlightsExpectedForCost,
  flightRowIsUserSelectedFlight,
  hotelRowIsActionableHotel,
  hotelStayTotal,
  parsePriceDigits,
} from "../lib/bookingCardGuards";

export function BookingCostBreakdown({
  dest,
  paxCount = 2,
  variant = "timeline",
}: {
  dest: DestCard;
  paxCount?: number;
  variant?: "timeline" | "summary";
}) {
  const flights = dest.flights || [];
  const hotels = dest.hotels || [];
  const acts = dest.activities || [];
  const flightsExpected = commercialFlightsExpectedForCost(dest);
  const flightNum = flightsExpected
    ? flights.filter((f) => flightRowIsUserSelectedFlight(f)).reduce((s, f) => s + parsePriceDigits(f.price), 0)
    : 0;
  const hotelSum = hotels
    .filter((h) => hotelRowIsActionableHotel(h))
    .reduce((s, h) => s + hotelStayTotal(h), 0);
  const actSum = activitySum(acts);
  const total = flightNum + hotelSum + actSum;
  return (
    <div className="rounded-xl border border-indigo-500/20 bg-gradient-to-br from-indigo-500/10 to-purple-500/10 p-4 lg:sticky lg:top-4">
      <p className="text-[11px] font-medium uppercase tracking-wider text-white/50">
        Cost breakdown · {paxCount} traveler{paxCount === 1 ? "" : "s"}
      </p>
      <div className="mt-3 space-y-2">
        {flightsExpected ? (
          <div className="flex items-center justify-between gap-2 text-[12px]">
            <span className="flex min-w-0 items-center gap-2 text-white/60">
              <Plane className="h-3.5 w-3.5 shrink-0 text-blue-400" aria-hidden />
              <span className="truncate">Flights</span>
            </span>
            <span className="shrink-0 tabular-nums text-white/85">${flightNum.toLocaleString()}</span>
          </div>
        ) : (
          <div className="flex items-center justify-between gap-2 text-[12px]">
            <span className="flex min-w-0 items-center gap-2 text-white/35 line-through decoration-white/25">
              <Plane className="h-3.5 w-3.5 shrink-0 text-blue-400/35" aria-hidden />
              <span className="truncate">Flights</span>
            </span>
            <span className="shrink-0 text-[11px] text-white/35">Drive only</span>
          </div>
        )}
        <div className="flex items-center justify-between gap-2 text-[12px]">
          <span className="flex min-w-0 items-center gap-2 text-white/60">
            <Hotel className="h-3.5 w-3.5 shrink-0 text-purple-400" aria-hidden />
            <span className="truncate">Accommodation</span>
          </span>
          <span className="shrink-0 tabular-nums text-white/85">${hotelSum.toLocaleString()}</span>
        </div>
        <div className="flex items-center justify-between gap-2 text-[12px]">
          <span className="flex min-w-0 items-center gap-2 text-white/60">
            <Luggage className="h-3.5 w-3.5 shrink-0 text-emerald-400" aria-hidden />
            <span className="truncate">Activities</span>
          </span>
          <span className="shrink-0 tabular-nums text-white/85">${actSum.toLocaleString()}</span>
        </div>
      </div>
      <div className="mt-3 border-t border-white/[0.08] pt-2">
        <div className="flex items-center justify-between">
          <span className="text-[13px] text-white/50">Total</span>
          <span className="text-[18px] font-medium tabular-nums text-white/90">${total.toLocaleString()}</span>
        </div>
      </div>
      <p className="mt-2 text-[10px] leading-snug text-white/30">
        {variant === "summary"
          ? "Use the logistics button (route icon) on the destination card for the full trip logistics view."
          : "Use the card flip for day-by-day logistics; per-step booking links return in a later iteration."}
      </p>
    </div>
  );
}
