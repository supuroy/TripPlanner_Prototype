import { useEffect, useState } from "react";
import { bookingStreamEventToCaption, formatBookingStepKey, type BookingStreamDrafts } from "./bookingStreamCaption";

export type BookingProgressStepStatus = "pending" | "active" | "done" | "error";

export type BookingProgressStep = {
  key: string;
  label: string;
  status: BookingProgressStepStatus;
  detail?: string;
};

export function applyBookingStreamEvent(
  steps: BookingProgressStep[],
  ev: Record<string, unknown>,
  drafts: BookingStreamDrafts,
): { steps: BookingProgressStep[]; primary: string; streamPreview: string } {
  let primary = "";
  let streamPreview = "";
  const cap = bookingStreamEventToCaption(ev, drafts);
  if (ev.type === "stream_preview" && typeof ev.message === "string") {
    streamPreview = ev.message.trim();
  } else if (cap) {
    primary = cap;
  }

  const stepKey = typeof ev.step_key === "string" ? ev.step_key : "";
  const stepStatus = ev.step_status as BookingProgressStepStatus | undefined;
  if (!stepKey || !stepStatus) {
    return { steps, primary, streamPreview };
  }

  const label = formatBookingStepKey(stepKey);
  const detail =
    typeof ev.detail === "string" && ev.detail.trim()
      ? ev.detail.trim()
      : ev.type === "progress" && typeof ev.message === "string"
        ? ev.message.trim()
        : undefined;

  const ix = steps.findIndex((s) => s.key === stepKey);
  const next: BookingProgressStep = {
    key: stepKey,
    label,
    status: stepStatus,
    detail: detail || (ix >= 0 ? steps[ix]?.detail : undefined),
  };
  if (ix >= 0) {
    const copy = [...steps];
    copy[ix] = { ...copy[ix], ...next };
    return { steps: copy, primary, streamPreview };
  }
  return { steps: [...steps, next], primary, streamPreview };
}

export type BookingProgressPhase =
  | "prepare"
  | "flight"
  | "return_flight"
  | "hotel"
  | "single_flight"
  | "single_hotel";

export const BOOKING_PROGRESS_MESSAGES: Record<BookingProgressPhase, string[]> = {
  prepare: [
    "Reading your itinerary and traveler profile…",
    "Matching trip dates to search windows…",
    "Building Expedia search criteria…",
  ],
  flight: [
    "Opening Expedia flight results in the browser…",
    "Waiting for live prices to render…",
    "Parsing top options for your route…",
  ],
  return_flight: [
    "Searching your return leg on Expedia…",
    "Comparing return fares and timing…",
    "Almost done with flights…",
  ],
  hotel: [
    "Opening hotel listings for this stay…",
    "Scanning nightly rates and guest ratings…",
    "Picking the best match for your area…",
  ],
  single_flight: [
    "Opening Expedia for this flight leg…",
    "Fetching live fares for your dates…",
    "Parsing ranked flight options…",
  ],
  single_hotel: [
    "Opening Expedia for this stay…",
    "Loading hotel photos and nightly rates…",
    "Parsing ranked hotel options…",
  ],
};

/** Cycles through friendly status lines while a long OTA step runs. */
export function useRotatingProgressMessage(
  messages: readonly string[],
  active: boolean,
  intervalMs = 2800,
): string {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (!active) {
      setIndex(0);
      return;
    }
    if (messages.length <= 1) return;
    const id = window.setInterval(() => {
      setIndex((i) => (i + 1) % messages.length);
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [active, messages, intervalMs]);

  if (!messages.length) return "";
  return messages[Math.min(index, messages.length - 1)] ?? messages[0];
}
