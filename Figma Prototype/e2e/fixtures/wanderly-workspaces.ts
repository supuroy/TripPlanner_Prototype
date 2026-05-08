import type { WanderlyWorkspaceDTO } from "../../src/types/workspace";

const LISBON_IMAGE =
  "https://images.unsplash.com/photo-1762335358426-72c27db3f5ff?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&w=400";

/** Single-destination workspace after first smoke / plan turn. */
export function workspaceLisbonReady(sessionId: string): WanderlyWorkspaceDTO {
  return {
    assistant_markdown: "## Lisbon\nYour **Atlantic** city break is sketched.",
    profile_pills: [
      /* Keys must match ``PROFILE_OPTIONS`` in ProfilePanel so the sidebar shows preset choices. */
      { key: "when", label: "When", value: "Mar 1–7", icon: "📅" },
      { key: "budget", label: "Budget", value: "~$3-5k total", icon: "💰" },
    ],
    destinations: [
      {
        id: "lisbon-portugal",
        name: "Lisbon",
        country: "Portugal",
        hook: "Tiles, trams, and custard tarts",
        image: LISBON_IMAGE,
        images: [LISBON_IMAGE],
        temp: "64°F",
        flight: "7h",
        cost: "$1,400",
        vibe: ["Food", "History", "Art"],
        score: 91,
        dismissed: false,
        pinned: false,
        itinerary: [
          { day: 1, label: "Arrive", items: ["Check in Alfama", "Sunset miradouro"], accent: "amber" },
          { day: 2, label: "Explore", items: ["Tram 28", "Pastéis de Belém"], accent: "blue" },
        ],
        flights: [],
        hotels: [],
        activities: [],
        itinerary_dirty: false,
      },
    ],
    enrichment_pending: false,
  };
}

/** Lisbon + Porto after a refine turn. */
export function workspaceLisbonPorto(sessionId: string): WanderlyWorkspaceDTO {
  const base = workspaceLisbonReady(sessionId);
  return {
    ...base,
    assistant_markdown: "## Updated shortlist\nAdded **Porto** for wine country.",
    destinations: [
      ...(base.destinations as Record<string, unknown>[]),
      {
        id: "porto-portugal",
        name: "Porto",
        country: "Portugal",
        hook: "Douro views and port wine",
        image: LISBON_IMAGE,
        images: [],
        temp: "62°F",
        flight: "8h",
        cost: "$1,350",
        vibe: ["Wine", "River", "Food"],
        score: 89,
        dismissed: false,
        pinned: false,
        itinerary: [{ day: 1, label: "Ribeira", items: ["Walk the riverfront", "Port tasting"] }],
        flights: [],
        hotels: [],
        activities: [],
        itinerary_dirty: false,
      },
    ],
  };
}

/** Same Lisbon card after a planner refine that reshaped days (beach on day 2). */
export function workspaceLisbonBeachUpdate(_sessionId: string): WanderlyWorkspaceDTO {
  const w = workspaceLisbonReady(_sessionId);
  const d = { ...(w.destinations![0] as Record<string, unknown>) };
  d.itinerary = [
    { day: 1, label: "Arrive", items: ["Check in Alfama"], accent: "amber" },
    {
      day: 2,
      label: "City + coast",
      items: ["Morning: Carcavelos beach", "Afternoon: Belém towers"],
      accent: "blue",
    },
    { day: 3, label: "Fly home", items: ["Last pastel run", "Depart LIS"], accent: "emerald" },
  ];
  d.itinerary_dirty = false;
  return {
    ...w,
    assistant_markdown: "## Lisbon\nAdded **beach** time on day 2.",
    destinations: [d],
  };
}

export const MOCK_ITINERARY_DAYS = [
  { day: 1, label: "Mock day 1", items: ["Arrival coffee", "Evening fado"], accent: "emerald" },
  { day: 2, label: "Mock day 2", items: ["Tiles tour", "Seafood lunch"], accent: "blue" },
];
