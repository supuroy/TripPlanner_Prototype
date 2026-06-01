import { describe, expect, it } from "vitest";
import type { DestCard } from "../components/TripStore";
import { buildLogisticsBreadcrumb, collectCampBlocksForDest } from "./logisticsBreadcrumb";

describe("logisticsBreadcrumb camps", () => {
  it("collects camp nights from itinerary days", () => {
    const dest = {
      id: "wct",
      name: "West Coast Trail",
      itinerary: [
        {
          day: 1,
          label: "d1",
          items: ["a", "b"],
          day_logistics: {
            camp_night: { kind: "backcountry", label: "Michigan Creek Campsite" },
          },
        },
        {
          day: 2,
          label: "d2",
          items: ["a", "b"],
          day_logistics: {
            camp_night: { kind: "backcountry", label: "Darling River Campsite" },
          },
        },
      ],
    } as DestCard;
    expect(collectCampBlocksForDest(dest).map((c) => c.label)).toEqual([
      "Michigan Creek Campsite",
      "Darling River Campsite",
    ]);
  });

  it("uses tent crumbs instead of hotel for trail lodging_moves", () => {
    const dest = {
      id: "wct",
      name: "West Coast Trail (Pachena half)",
      country: "Canada",
      needs_commercial_flight: false,
      transport_plan: {
        primary_mode: "ground_only",
        commercial_flight_segments: [],
        lodging_moves: [
          {
            area: "North West Coast Trail — Michigan Creek, Darling River camps",
            day_range: "1-3",
            nights: 3,
          },
        ],
      },
      itinerary: [
        {
          day: 1,
          label: "d1",
          items: ["a", "b"],
          day_logistics: {
            camp_night: { kind: "backcountry", label: "Michigan Creek Campsite" },
          },
        },
        {
          day: 2,
          label: "d2",
          items: ["a", "b"],
          day_logistics: {
            camp_night: { kind: "backcountry", label: "Darling River Campsite" },
          },
        },
        {
          day: 3,
          label: "d3",
          items: ["a", "b"],
          day_logistics: {
            camp_night: { kind: "backcountry", label: "Tsusiat Falls Campsite" },
          },
        },
      ],
    } as DestCard;
    const crumbs = buildLogisticsBreadcrumb(dest);
    expect(crumbs.filter((c) => c.kind === "camp")).toHaveLength(3);
    expect(crumbs.some((c) => c.kind === "hotel" && c.nights === 3)).toBe(false);
  });
});
