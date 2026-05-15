import type { ProfilePill } from "../components/TripStore";

/** Six placeholder slots — must not be persisted to trip disk via sync_ui. */
export function defaultWanderlyProfilePills(): ProfilePill[] {
  return [
    { key: "when", label: "When", value: "Not specified", icon: "📅", editable: true },
    { key: "who", label: "Who", value: "Travelers", icon: "👥", editable: true },
    { key: "from", label: "Flying from", value: "Not specified", icon: "✈️", editable: true },
    { key: "vibe", label: "Vibe", value: "leisure", icon: "✨", editable: true },
    { key: "budget", label: "Budget", value: "Flexible", icon: "💰", editable: true },
    { key: "duration", label: "Duration", value: "Not specified", icon: "⏱", editable: true },
  ];
}

/** True when pills match the UI placeholder template (empty / not yet loaded from server). */
export function isPlaceholderProfilePills(pills: ProfilePill[] | null | undefined): boolean {
  if (!pills?.length) return true;
  const byKey = new Map(pills.map((p) => [p.key, (p.value || "").trim()]));
  return (
    (byKey.get("when") === "Not specified" || !byKey.get("when")) &&
    (byKey.get("who") === "Travelers" || !byKey.get("who")) &&
    (byKey.get("from") === "Not specified" || !byKey.get("from")) &&
    (byKey.get("budget") === "Flexible" || !byKey.get("budget")) &&
    (byKey.get("duration") === "Not specified" || !byKey.get("duration"))
  );
}
