import React, { createContext, useContext, useState, useCallback, useRef, useEffect } from "react";
import type { ItineraryBudgetGuidance, WanderlyProfilePillDTO } from "../../types/workspace";
import { planReview, fetchWorkspace, newTrip, syncUi } from "../../api/wanderly";
import { logItin } from "../lib/itineraryDebug";
import { registerItineraryLaneDepthSink } from "../lib/lazyItineraryPrefetch";
import { waitForItinerariesBeforePlanReview } from "../lib/deferPlanReview";
import { planReviewMarkdownForUi } from "../lib/planReviewMarkdown";

export type Phase = "idle" | "thinking" | "streaming" | "refining" | "advising" | "complete";

export interface ProfilePill {
  key: string;
  label: string;
  value: string;
  icon: string;
  editable?: boolean;
  options?: string[];
}

export interface ConversationMessage {
  id: string;
  role: "user" | "agent";
  text: string;
  ts: number;
  /** Optional main-planner instruction; UI shows Yes / Not now without retyping /trip. */
  refinePrompt?: string;
  /** User chose Yes (refine queued) or Not now. */
  refineCtaResolved?: boolean;
  /** Advisor admitted missing facts in JSON; UI can offer opening a browser search (no server web API). */
  offerWebSearch?: boolean;
  webSearchQuery?: string;
  webSearchCtaResolved?: boolean;
}

export interface DestCard {
  id: string;
  name: string;
  country: string;
  hook: string;
  image: string;
  images?: string[]; // Multiple hero images for carousel
  temp: string;
  flight: string;
  cost: string;
  vibe: string[];
  score: number;
  dismissed: boolean;
  pinned: boolean;
  warnings?: string[];
  itinerary?: ItinDay[];
  flights?: FlightOption[];
  hotels?: HotelOption[];
  activities?: ActivityOption[];
  /** v4: enrichment / weather line from backend mapper */
  weather_summary?: string;
  /** v5: commerce placeholder metadata */
  commerce?: { disclaimer?: string; typical_range_usd?: number | null; destination?: string };
  /** Rough budget bands from lazy itinerary build (LLM), optional. */
  budget_guidance?: ItineraryBudgetGuidance;
  /** True when trip criteria changed and lazy itinerary should refresh on open / background for pinned. */
  itinerary_dirty?: boolean;
  /** Destination-scoped chat (expanded card conversation panel). */
  conversation?: ConversationMessage[];
  /** User confirmed "Ready to Book" — enables flip to detailed booking view. */
  readyToBook?: boolean;
  /** When true, normal-view flight/hotel strips use live search summaries; otherwise planner ``budget_guidance`` bands. */
  liveBookingEstimates?: boolean;
  /** Optional explicit lodging outline for OTA booking (mirrors server ``booking_search_outline``). */
  booking_search_outline?: { stays?: Array<{ area?: string; nights?: number; label?: string }> };
  /** Optional Expedia-oriented flight labels from the itinerary LLM (concrete cities for URL building). */
  booking_search_hints?: { flight_to_label?: string; flight_return_from_label?: string };
}

export interface ItinDay {
  day: number;
  label: string;
  items: string[];
  /** Overnight base from lazy itinerary LLM (used server-side for return flight origin). */
  base?: string;
  accent?: string;
}

export interface FlightOption {
  airline: string;
  route: string;
  duration: string;
  price: string;
  best?: boolean;
  /** Expedia Flights-Search URL used for this snapshot (same as API ``search_url`` on the step). */
  search_url?: string;
  /** True when URL includes return leg (``leg2``) per trip end date. */
  round_trip?: boolean;
  /** Outbound vs return when using separate one-way searches (``flight_leg`` from API). */
  leg?: "outbound" | "return";
  /** UI-only: seeded itinerary placeholder before live result replacement. */
  isPlaceholder?: boolean;
}

export interface HotelOption {
  name: string;
  area: string;
  price: string;
  nights: number;
  image: string;
  best?: boolean;
  /** Expedia results URL when populated by live booking search. */
  listing_url?: string;
  /** UI-only: seeded itinerary placeholder before live result replacement. */
  isPlaceholder?: boolean;
}

export interface ActivityOption {
  name: string;
  day: number;
  type: string;
  price: string;
  duration: string;
}

export interface AgentMessage {
  id: string;
  role: "user" | "agent";
  text: string;
  ts: number;
  pinned?: boolean;
  // Action result messages can be undone
  undoId?: string;
}

export interface SuggestedAction {
  id: string;
  label: string;
  description: string;
  icon: string; // emoji
  /** When set, Play runs this text as a real refine turn (main planner), not demo logic. */
  refinePrompt?: string;
}

export interface AgentInsight {
  id: string;
  type: "info" | "warning" | "success" | "tip";
  text: string;
  relatedTo?: string;
  ts: number;
  dismissed?: boolean;
  actions?: SuggestedAction[];
}

export interface AgentResponse {
  id: string;
  type: "message" | "insight" | "comparison" | "warning" | "success" | "tip" | "info" | "destination_detail";
  role?: "user" | "agent"; // For message types
  text: string;
  ts: number;
  dismissed?: boolean;
  undoId?: string; // For actions that can be undone
  actions?: SuggestedAction[];
  relatedTo?: string; // For destination-specific insights
  destinationId?: string; // For destination_detail type
  destinationName?: string; // For destination_detail type
}

export interface UndoEntry {
  id: string;
  label: string;
  destinations: DestCard[];
  profile: ProfilePill[];
  ts: number;
}

interface TripStoreType {
  phase: Phase;
  setPhase: (p: Phase) => void;
  profile: ProfilePill[];
  setProfile: (p: ProfilePill[]) => void;
  updateProfilePill: (key: string, value: string) => void;
  destinations: DestCard[];
  setDestinations: (d: DestCard[] | ((prev: DestCard[]) => DestCard[])) => void;
  
  // Legacy (backward compatibility)
  messages: AgentMessage[];
  addMessage: (role: "user" | "agent", text: string, undoId?: string) => void;
  insights: AgentInsight[];
  addInsight: (type: AgentInsight["type"], text: string, relatedTo?: string, actions?: SuggestedAction[]) => void;
  dismissInsight: (id: string) => void;
  
  // New unified responses
  responses: AgentResponse[];
  addResponse: (response: Omit<AgentResponse, "id" | "ts">) => void;
  dismissResponse: (id: string) => void;

  addConversationMessage: (
    destId: string,
    role: "user" | "agent",
    text: string,
    opts?: { refinePrompt?: string; offerWebSearch?: boolean; webSearchQuery?: string },
  ) => void;
  resolveConversationRefineCta: (destId: string, messageId: string) => void;
  resolveConversationWebSearchCta: (destId: string, messageId: string) => void;
  getConversation: (destId: string) => ConversationMessage[];

  agentStatus: string;
  setAgentStatus: (s: string) => void;
  /** Live progress from planner SSE, orchestrate/advisor wait spinners, and card itinerary builds — shown in ``AgentResponses`` / expanded card. */
  agentStreamCaption: string;
  setAgentStreamCaption: (s: string) => void;
  selectedDest: string | null;
  setSelectedDest: (id: string | null) => void;
  expandedDest: string | null;
  setExpandedDest: (id: string | null) => void;
  // Main-canvas presentation mode: collapse destination cards into compact chips to maximize chat space.
  isCanvasCollapsed: boolean;
  setIsCanvasCollapsed: (v: boolean) => void;
  /** Count of lazy itinerary HTTP jobs queued or running (serialized lane); for UI hints only — does not block opening cards. */
  itineraryLaneQueueDepth: number;
  hasStarted: boolean;
  setHasStarted: (v: boolean) => void;
  sessionId: string;
  setSessionId: (id: string) => void;
  enrichmentPending: boolean;
  setEnrichmentPending: (v: boolean) => void;
  /** When set (and workspace has destinations), CommandBar runs a refine with this text. */
  pendingRefineText: string | null;
  setPendingRefineText: (t: string | null) => void;
  /** One-shot: main bar input is filled from advisor “Try asking” chips, then cleared. */
  composerPrefill: string | null;
  setComposerPrefill: (t: string | null) => void;
  undoStack: UndoEntry[];
  pushUndo: (label: string) => void;
  performUndo: (id: string) => void;
  /** Queue advisor plan_review after lazy itineraries settle; cancels prior queued runs. */
  queuePlanReviewAfterItineraries: (sessionId: string) => void;
  /** When true, expanded refine allows switching other destination cards (orchestrator hint). */
  refineUnlockCardNavigation: boolean;
  setRefineUnlockCardNavigation: (v: boolean) => void;
  /** Active planner HTTP stream — call from CommandBar before smoke/refine SSE. */
  setPlannerAbortController: (ac: AbortController | null) => void;
  /** Abort in-flight planner work and clear server-side Cursor reuse for a fresh trip. */
  startNewTrip: () => Promise<void>;

const Ctx = createContext<TripStoreType | null>(null);

// ── Primary sample destinations ──

const DESTINATIONS: DestCard[] = [
  {
    id: "bali",
    name: "Bali",
    country: "Indonesia",
    hook: "Spiritual jungle meets world-class beach culture",
    image: "https://images.unsplash.com/photo-1532186651327-6ac23687d189?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxCYWxpJTIwaW5maW5pdHklMjBwb29sJTIwanVuZ2xlJTIwc3Vuc2V0fGVufDF8fHx8MTc3MjUwMjU1Nnww&ixlib=rb-4.1.0&q=80&w=1080",
    images: [
      "https://images.unsplash.com/photo-1532186651327-6ac23687d189?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxCYWxpJTIwaW5maW5pdHklMjBwb29sJTIwanVuZ2xlJTIwc3Vuc2V0fGVufDF8fHx8MTc3MjUwMjU1Nnww&ixlib=rb-4.1.0&q=80&w=1080",
      "https://images.unsplash.com/photo-1656247203824-3d6f99461ba4?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxCYWxpJTIwcmljZSUyMHRlcnJhY2VzJTIwdWJ1ZHxlbnwxfHx8fDE3NzI1MTM2ODZ8MA&ixlib=rb-4.1.0&q=80&w=1080",
      "https://images.unsplash.com/photo-1765553736610-99cbaaeb16e3?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxCYWxpJTIwdGVtcGxlJTIwd2F0ZXIlMjBzcGlyaXR1YWx8ZW58MXx8fHwxNzcyNTEzNjg3fDA&ixlib=rb-4.1.0&q=80&w=1080",
    ],
    temp: "82°F",
    flight: "20h",
    cost: "",
    vibe: ["Culture", "Food", "Beach", "Spiritual"],
    score: 94,
    dismissed: false,
    pinned: false,
    itinerary: [
      { day: 1, label: "Arrive → Ubud", items: ["Land DPS", "Private transfer to Ubud", "Check in villa", "Sunset at Tegallalang"], accent: "emerald" },
      { day: 2, label: "Temples & Craft", items: ["Tirta Empul purification", "Ubud Palace & art market", "Cooking class with local chef", "Kecak fire dance at dusk"] },
      { day: 3, label: "Adventure", items: ["Mt. Batur sunrise trek (4am)", "Hot springs recovery", "Afternoon: Ayung rafting", "Dinner at Locavore"] },
      { day: 4, label: "Ubud → Seminyak", items: ["Monkey Forest morning walk", "Transfer to Seminyak (1.5h)", "Beach club afternoon", "Sarong dinner"], accent: "blue" },
      { day: 5, label: "Coast Day", items: ["Surf lesson at Echo Beach", "Uluwatu temple & cliffs", "Single Fin sunset drinks", "Seafood at Jimbaran Bay"] },
      { day: 6, label: "Slow Day", items: ["Sleep in, villa breakfast", "Traditional Balinese spa", "Last shopping in Seminyak", "Farewell dinner at Merah Putih"] },
      { day: 7, label: "Fly Home", items: ["Late breakfast", "Airport transfer", "Depart DPS evening"] },
    ],
    flights: [
      { airline: "Singapore Air", route: "JFK → SIN → DPS", duration: "22h 15m", price: "$1,180", best: true },
      { airline: "Qatar Airways", route: "JFK → DOH → DPS", duration: "24h 30m", price: "$1,050" },
      { airline: "Cathay Pacific", route: "JFK → HKG → DPS", duration: "23h 45m", price: "$1,220" },
    ],
    hotels: [
      { name: "Bisma Eight", area: "Ubud", price: "$165/n", nights: 3, image: "https://images.unsplash.com/photo-1613278103929-b945a707411a?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&w=400", best: true },
      { name: "The Legian", area: "Seminyak", price: "$210/n", nights: 3, image: "https://images.unsplash.com/photo-1728049006343-9ee0187643d5?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&w=400", best: true },
    ],
    activities: [
      { name: "Mt. Batur sunrise trek", day: 3, type: "Adventure", price: "$55", duration: "6h" },
      { name: "Balinese cooking class", day: 2, type: "Food", price: "$45", duration: "4h" },
      { name: "Surf lesson", day: 5, type: "Beach", price: "$35", duration: "2h" },
      { name: "Traditional spa ritual", day: 6, type: "Wellness", price: "$60", duration: "2h" },
      { name: "Kecak fire dance", day: 2, type: "Culture", price: "$12", duration: "1.5h" },
      { name: "Ayung River rafting", day: 3, type: "Adventure", price: "$40", duration: "3h" },
    ],
  },
  {
    id: "santorini",
    name: "Santorini",
    country: "Greece",
    hook: "Volcanic drama, wine sunsets, and Aegean blue",
    image: "https://images.unsplash.com/photo-1654118891010-3c067d8c25cc?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxTYW50b3JpbmklMjBjYWxkZXJhJTIwYmx1ZSUyMGRvbWUlMjBhZXJpYWx8ZW58MXx8fHwxNzcyNTAyNTU3fDA&ixlib=rb-4.1.0&q=80&w=1080",
    images: [
      "https://images.unsplash.com/photo-1654118891010-3c067d8c25cc?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxTYW50b3JpbmklMjBjYWxkZXJhJTIwYmx1ZSUyMGRvbWUlMjBhZXJpYWx8ZW58MXx8fHwxNzcyNTAyNTU3fDA&ixlib=rb-4.1.0&q=80&w=1080",
      "https://images.unsplash.com/photo-1761047726930-d1443d5f490f?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxTYW50b3JpbmklMjB3aGl0ZSUyMGJ1aWxkaW5ncyUyMGNhbGRlcmF8ZW58MXx8fHwxNzcyNTEzNjg3fDA&ixlib=rb-4.1.0&q=80&w=1080",
      "https://images.unsplash.com/photo-1667526273488-378aa6f5f113?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxTYW50b3JpbmklMjBzdW5zZXQlMjB3aW5lJTIwZ3JlZWNlfGVufDF8fHx8MTc3MjUxMzY4N3ww&ixlib=rb-4.1.0&q=80&w=1080",
    ],
    temp: "68°F",
    flight: "12h",
    cost: "",
    vibe: ["Romance", "Wine", "History", "Scenic"],
    score: 82,
    dismissed: false,
    pinned: false,
    itinerary: [
      { day: 1, label: "Arrive → Oia", items: ["Fly in via Athens", "Ferry or domestic flight to JTR", "Check in cliffside suite", "First caldera sunset"], accent: "blue" },
      { day: 2, label: "Caldera & Wine", items: ["Morning: Fira to Oia hike (3h)", "Lunch with caldera views", "Santo Wines tasting", "Dinner at Ammoudi Bay"] },
      { day: 3, label: "Sea & History", items: ["Catamaran cruise around caldera", "Swim at hot springs", "Visit Ancient Akrotiri", "Sunset from Oia castle"] },
      { day: 4, label: "East Side", items: ["Red Beach morning swim", "Pyrgos village walk", "Traditional taverna lunch", "Afternoon at villa pool"] },
      { day: 5, label: "Slow Santorini", items: ["Sleep in", "Brunch at Mama Thira", "Shopping in Fira", "Farewell dinner at Selene"] },
      { day: 6, label: "Fly Home", items: ["Morning check-out", "Ferry to Athens", "Connect to JFK flight"] },
    ],
    flights: [
      { airline: "Delta via Athens", route: "JFK → ATH → JTR", duration: "13h", price: "$980", best: true },
      { airline: "Emirates", route: "JFK → DXB → ATH → JTR", duration: "18h", price: "$1,100" },
    ],
    hotels: [
      { name: "Andronis Luxury", area: "Oia", price: "$380/n", nights: 5, image: "https://images.unsplash.com/photo-1654118891010-3c067d8c25cc?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&w=400", best: true },
    ],
    activities: [
      { name: "Catamaran caldera cruise", day: 3, type: "Sea", price: "$120", duration: "5h" },
      { name: "Wine tasting tour", day: 2, type: "Food", price: "$65", duration: "3h" },
      { name: "Akrotiri archaeological site", day: 3, type: "History", price: "$15", duration: "2h" },
      { name: "Fira-Oia cliffside hike", day: 2, type: "Adventure", price: "$0", duration: "3h" },
    ],
  },
  {
    id: "lisbon",
    name: "Lisbon",
    country: "Portugal",
    hook: "Europe's coolest capital — pastéis, fado, and golden light",
    image: "https://images.unsplash.com/photo-1762335358426-72c27db3f5ff?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxMaXNib24lMjB5ZWxsb3clMjB0cmFtJTIwaGlzdG9yaWN8ZW58MXx8fHwxNzcyNTAyNTU3fDA&ixlib=rb-4.1.0&q=80&w=1080",
    images: [
      "https://images.unsplash.com/photo-1762335358426-72c27db3f5ff?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxMaXNib24lMjB5ZWxsb3clMjB0cmFtJTIwaGlzdG9yaWN8ZW58MXx8fHwxNzcyNTAyNTU3fDA&ixlib=rb-4.1.0&q=80&w=1080",
      "https://images.unsplash.com/photo-1549802923-03442f1b2418?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxMaXNib24lMjBjb2xvcmZ1bCUyMGFyY2hpdGVjdHVyZSUyMHN0cmVldHN8ZW58MXx8fHwxNzcyNTEzNjg4fDA&ixlib=rb-4.1.0&q=80&w=1080",
      "https://images.unsplash.com/photo-1764016491558-19add5f6e990?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxMaXNib24lMjB3YXRlcmZyb250JTIwaGlzdG9yaWMlMjB0b3dlcnxlbnwxfHx8fDE3NzI1MTM2ODh8MA&ixlib=rb-4.1.0&q=80&w=1080",
    ],
    temp: "64°F",
    flight: "7h",
    cost: "",
    vibe: ["Food", "History", "Nightlife", "Art"],
    score: 88,
    dismissed: false,
    pinned: false,
    itinerary: [
      { day: 1, label: "Arrive → Lisbon", items: ["Direct JFK → LIS (7h)", "Check in Alfama boutique hotel", "Evening walk & first pastéis de nata", "Fado show in Alfama"], accent: "amber" },
      { day: 2, label: "Historic Heart", items: ["Tram 28 ride", "São Jorge Castle", "Lunch at Time Out Market", "Belém Tower & Jerónimos"] },
      { day: 3, label: "Lisbon → Sintra", items: ["Train to Sintra (40min)", "Pena Palace & gardens", "Lunch in village", "Cabo da Roca (westernmost point)"] },
      { day: 4, label: "Food & Art", items: ["Morning: LX Factory markets", "Cooking class: Portuguese cuisine", "MAAT museum", "Sunset at Miradouro da Graça"] },
      { day: 5, label: "Lisbon → Cascais", items: ["Train to Cascais", "Beach morning", "Seafood lunch by the ocean", "Back to Lisbon, Bairro Alto nightlife"] },
      { day: 6, label: "Fly Home", items: ["Morning pastéis run", "Last walk through Baixa", "Fly LIS → JFK direct"] },
    ],
    flights: [
      { airline: "TAP Portugal", route: "JFK → LIS direct", duration: "7h 10m", price: "$620", best: true },
      { airline: "Delta", route: "JFK → LIS direct", duration: "7h 25m", price: "$680" },
    ],
    hotels: [
      { name: "Memmo Alfama", area: "Alfama", price: "$195/n", nights: 5, image: "https://images.unsplash.com/photo-1762335358426-72c27db3f5ff?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&w=400", best: true },
    ],
    activities: [
      { name: "Fado evening experience", day: 1, type: "Culture", price: "$45", duration: "2h" },
      { name: "Sintra day trip", day: 3, type: "History", price: "$35", duration: "Full day" },
      { name: "Portuguese cooking class", day: 4, type: "Food", price: "$55", duration: "3h" },
      { name: "Cascais beach trip", day: 5, type: "Beach", price: "$10", duration: "Full day" },
    ],
  },
];

// ── Reserve destinations (can be swapped in by agent actions) ──

const RESERVE_DESTINATIONS: DestCard[] = [
  {
    id: "cartagena",
    name: "Cartagena",
    country: "Colombia",
    hook: "Caribbean warmth, colonial charm, and $15 ceviche feasts",
    image: "https://images.unsplash.com/photo-1742648778984-36dea890552a?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxDYXJ0YWdlbmElMjBDb2xvbWJpYSUyMGNvbG9yZnVsJTIwY29sb25pYWwlMjBzdHJlZXRzfGVufDF8fHx8MTc3MjUxMDIwOXww&ixlib=rb-4.1.0&q=80&w=1080",
    temp: "86°F",
    flight: "5.5h",
    cost: "",
    vibe: ["Culture", "Food", "Beach", "History"],
    score: 91,
    dismissed: false,
    pinned: false,
    itinerary: [
      { day: 1, label: "Arrive Cartagena", items: ["Direct JFK → CTG (5.5h)", "Check in Old City boutique hotel", "Walk the walled city at sunset", "Dinner on Plaza Santo Domingo"], accent: "amber" },
      { day: 2, label: "Old City & Food", items: ["Street food tour of Getsemaní", "Castillo San Felipe fortress", "Lunch at La Cevichería", "Salsa dancing lesson at night"] },
      { day: 3, label: "Island Day", items: ["Boat to Rosario Islands", "Snorkel crystal-clear reefs", "Beach lunch on Isla Barú", "Sunset cocktails back in town"] },
      { day: 4, label: "Culture & Color", items: ["Getsemaní street art tour", "Gold Museum visit", "Cooking class: Caribbean cuisine", "Rooftop dinner at Celele"] },
      { day: 5, label: "Fly Home", items: ["Morning at Bazurto Market", "Last walk through the walled city", "Fly CTG → JFK direct"] },
    ],
    flights: [
      { airline: "JetBlue", route: "JFK → CTG direct", duration: "5h 30m", price: "$420", best: true },
      { airline: "Avianca", route: "JFK → BOG → CTG", duration: "8h", price: "$380" },
    ],
    hotels: [
      { name: "Casa San Agustín", area: "Old City", price: "$145/n", nights: 4, image: "https://images.unsplash.com/photo-1742648778984-36dea890552a?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&w=400", best: true },
    ],
    activities: [
      { name: "Walled city food tour", day: 2, type: "Food", price: "$35", duration: "3h" },
      { name: "Rosario Islands boat trip", day: 3, type: "Beach", price: "$60", duration: "Full day" },
      { name: "Salsa dancing class", day: 2, type: "Culture", price: "$20", duration: "2h" },
      { name: "Caribbean cooking class", day: 4, type: "Food", price: "$40", duration: "3h" },
    ],
  },
  {
    id: "mexico-city",
    name: "Mexico City",
    country: "Mexico",
    hook: "World-class food scene, ancient history, and buzzing energy — insane value",
    image: "https://images.unsplash.com/photo-1760076000027-b4391b83850b?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxNZXhpY28lMjBDaXR5JTIwYWVyaWFsJTIwdmlldyUyMHVyYmFuJTIwc2t5bGluZXxlbnwxfHx8fDE3NzI1MTAyMTB8MA&ixlib=rb-4.1.0&q=80&w=1080",
    temp: "72°F",
    flight: "5h",
    cost: "",
    vibe: ["Food", "Culture", "History", "Art"],
    score: 93,
    dismissed: false,
    pinned: false,
    itinerary: [
      { day: 1, label: "Arrive CDMX", items: ["Direct JFK → MEX (5h)", "Check in Roma Norte hotel", "Evening tacos al pastor crawl", "Mezcal bar in Condesa"], accent: "emerald" },
      { day: 2, label: "Historic Center", items: ["Templo Mayor ruins", "National Palace murals", "Lunch at Pujol (world #12)", "Chapultepec Castle at sunset"] },
      { day: 3, label: "Food & Art", items: ["Frida Kahlo Museum (Casa Azul)", "Street food in Coyoacán", "MUAC contemporary art", "Lucha Libre wrestling night"] },
      { day: 4, label: "Day Trip: Teotihuacán", items: ["Pyramids of the Sun & Moon", "Traditional pulque tasting", "Lunch at La Gruta (cave restaurant)", "Evening: Roma Norte mezcalería hopping"] },
      { day: 5, label: "Fly Home", items: ["Churros at El Moro", "Last walk through Condesa", "Fly MEX → JFK direct"] },
    ],
    flights: [
      { airline: "Delta", route: "JFK → MEX direct", duration: "5h 10m", price: "$340", best: true },
      { airline: "Aeromexico", route: "JFK → MEX direct", duration: "5h 20m", price: "$310" },
    ],
    hotels: [
      { name: "Condesa DF", area: "Condesa", price: "$120/n", nights: 4, image: "https://images.unsplash.com/photo-1760076000027-b4391b83850b?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&w=400", best: true },
    ],
    activities: [
      { name: "Taco street food crawl", day: 1, type: "Food", price: "$25", duration: "3h" },
      { name: "Teotihuacán pyramids", day: 4, type: "History", price: "$45", duration: "Full day" },
      { name: "Frida Kahlo Museum", day: 3, type: "Culture", price: "$15", duration: "2h" },
      { name: "Lucha Libre wrestling", day: 3, type: "Culture", price: "$20", duration: "3h" },
    ],
  },
];

function readOrCreateSessionId(): string {
  try {
    const k = "wanderly_session_id";
    let existing: string | null = null;
    if (typeof localStorage !== "undefined") {
      existing = localStorage.getItem(k);
    }
    if (!existing && typeof sessionStorage !== "undefined") {
      const legacy = sessionStorage.getItem(k);
      if (legacy) {
        try {
          localStorage.setItem(k, legacy);
          sessionStorage.removeItem(k);
        } catch {
          /* ignore */
        }
        existing = legacy;
      }
    }
    if (existing) return existing;
    const id = crypto.randomUUID();
    try {
      localStorage.setItem(k, id);
    } catch {
      try {
        sessionStorage.setItem(k, id);
      } catch {
        /* ignore */
      }
    }
    return id;
  } catch {
    return crypto.randomUUID();
  }
}

export function TripProvider({ children }: { children: React.ReactNode }) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [profile, setProfile] = useState<ProfilePill[]>([]);
  const [destinations, setDestinations] = useState<DestCard[]>([]);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [insights, setInsights] = useState<AgentInsight[]>([]);
  const [responses, setResponses] = useState<AgentResponse[]>([]);
  const [agentStatus, setAgentStatus] = useState("");
  const [agentStreamCaption, setAgentStreamCaption] = useState("");
  const [selectedDest, setSelectedDest] = useState<string | null>(null);
  const [expandedDest, setExpandedDest] = useState<string | null>(null);
  const [isCanvasCollapsed, setIsCanvasCollapsed] = useState(false);
  const [itineraryLaneQueueDepth, setItineraryLaneQueueDepth] = useState(0);
  const [hasStarted, setHasStarted] = useState(false);
  const [sessionId, setSessionId] = useState<string>(readOrCreateSessionId);
  const [enrichmentPending, setEnrichmentPending] = useState(false);
  const [pendingRefineText, setPendingRefineText] = useState<string | null>(null);
  const [composerPrefill, setComposerPrefill] = useState<string | null>(null);
  const [undoStack, setUndoStack] = useState<UndoEntry[]>([]);
  const [refineUnlockCardNavigation, setRefineUnlockCardNavigation] = useState(false);
  const plannerAbortControllerRef = useRef<AbortController | null>(null);

  const setPlannerAbortController = useCallback((ac: AbortController | null) => {
    plannerAbortControllerRef.current = ac;
  }, []);

  // Use refs to avoid stale closures in pushUndo
  const destinationsRef = useRef(destinations);
  destinationsRef.current = destinations;
  const profileRef = useRef(profile);
  profileRef.current = profile;

  useEffect(() => {
    try {
      if (sessionId) localStorage.setItem("wanderly_session_id", sessionId);
    } catch {
      /* ignore */
    }
  }, [sessionId]);

  useEffect(() => {
    const sid = sessionId;
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetchWorkspace(sid);
        if (cancelled) return;
        if (!res.wanderly_snapshot_restored) return;
        const wanderly = res.wanderly;
        const dests = (wanderly.destinations || []) as DestCard[];
        const pills = (wanderly.profile_pills || []) as ProfilePill[];
        if (dests.length > 0) {
          setDestinations(dests);
        }
        if (pills.length > 0) {
          setProfile(pills);
        }
        if (dests.length > 0 || pills.length >= 4) {
          setHasStarted(true);
        }
      } catch {
        /* offline or server down */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const addMessage = useCallback((role: "user" | "agent", text: string, undoId?: string) => {
    setMessages((prev) => [...prev, { id: crypto.randomUUID(), role, text, ts: Date.now(), undoId }]);
  }, []);

  const addInsight = useCallback((type: AgentInsight["type"], text: string, relatedTo?: string, actions?: SuggestedAction[]) => {
    setInsights((prev) => [...prev, { id: crypto.randomUUID(), type, text, relatedTo, ts: Date.now(), actions }]);
  }, []);

  const dismissInsight = useCallback((id: string) => {
    setInsights((prev) => prev.map(i => i.id === id ? { ...i, dismissed: true } : i));
  }, []);

  const updateProfilePill = useCallback((key: string, value: string) => {
    logItin("sidebar_profile_pill_edit", {
      key,
      valuePreview: value.slice(0, 80),
      note: "Marks itinerary_dirty=true on ALL destinations (sidebar edit)",
    });
    setProfile((prev) => prev.map((p) => (p.key === key ? { ...p, value } : p)));
    setDestinations((prev) => prev.map((d) => ({ ...d, itinerary_dirty: true })));
  }, []);

  // Push current state to undo stack
  const pushUndo = useCallback((label: string) => {
    setUndoStack((prev) => {
      const entry: UndoEntry = {
        id: crypto.randomUUID(),
        label,
        destinations: JSON.parse(JSON.stringify(destinationsRef.current)),
        profile: JSON.parse(JSON.stringify(profileRef.current)),
        ts: Date.now(),
      };
      return [...prev.slice(-9), entry]; // keep last 10
    });
  }, []);

  // Restore from undo entry
  const performUndo = useCallback((id: string) => {
    setUndoStack((prev) => {
      const idx = prev.findIndex(e => e.id === id);
      if (idx < 0) return prev;
      const entry = prev[idx];
      setDestinations(entry.destinations);
      setProfile(entry.profile);
      setExpandedDest(null);
      addMessage("agent", `Undone: "${entry.label}". Your previous plans have been restored.`);
      return prev.filter(e => e.id !== id);
    });
  }, [addMessage]);

  const addResponse = useCallback((response: Omit<AgentResponse, "id" | "ts">) => {
    setResponses((prev) => [...prev, { id: crypto.randomUUID(), ts: Date.now(), ...response }]);
  }, []);

  const abortPlannerOperation = useCallback(() => {
    plannerAbortControllerRef.current?.abort();
    plannerAbortControllerRef.current = null;
    setPhase("complete");
    setAgentStatus("");
    setAgentStreamCaption("");
    setRefineUnlockCardNavigation(false);
    setResponses((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        ts: Date.now(),
        type: "warning",
        text: "Stopped the running planner request. You can switch cards and try again.",
      },
    ]);
  }, []);

  const startNewTrip = useCallback(async () => {
    plannerAbortControllerRef.current?.abort();
    plannerAbortControllerRef.current = null;
    const out = await newTrip(sessionId);
    const sid = out.session_id;
    try {
      localStorage.setItem("wanderly_session_id", sid);
    } catch {
      /* ignore */
    }
    setSessionId(sid);
    setDestinations([]);
    setProfile([]);
    setResponses([]);
    setMessages([]);
    setInsights([]);
    setUndoStack([]);
    setExpandedDest(null);
    setSelectedDest(null);
    setHasStarted(false);
    setPhase("idle");
    setAgentStatus("");
    setAgentStreamCaption("");
    setEnrichmentPending(false);
    setPendingRefineText(null);
    setComposerPrefill(null);
    setRefineUnlockCardNavigation(false);
    await syncUi(sid, []).catch(() => {});
  }, [sessionId]);

  const dismissResponse = useCallback((id: string) => {
    setResponses((prev) => prev.map(i => i.id === id ? { ...i, dismissed: true } : i));
  }, []);

  const addConversationMessage = useCallback(
    (
      destId: string,
      role: "user" | "agent",
      text: string,
      opts?: { refinePrompt?: string; offerWebSearch?: boolean; webSearchQuery?: string },
    ) => {
      const rp = opts?.refinePrompt?.trim();
      const ow = Boolean(opts?.offerWebSearch);
      const wq = opts?.webSearchQuery?.trim();
      setDestinations((prev) =>
        prev.map((d) => {
          if (d.id !== destId) return d;
          const newMessage: ConversationMessage = {
            id: crypto.randomUUID(),
            role,
            text,
            ts: Date.now(),
            ...(rp ? { refinePrompt: rp } : {}),
            ...(ow && wq ? { offerWebSearch: true, webSearchQuery: wq.slice(0, 500) } : {}),
          };
          return { ...d, conversation: [...(d.conversation || []), newMessage] };
        }),
      );
    },
    [],
  );

  const resolveConversationRefineCta = useCallback((destId: string, messageId: string) => {
    setDestinations((prev) =>
      prev.map((d) => {
        if (d.id !== destId) return d;
        return {
          ...d,
          conversation: (d.conversation || []).map((m) =>
            m.id === messageId ? { ...m, refineCtaResolved: true } : m,
          ),
        };
      }),
    );
  }, []);

  const resolveConversationWebSearchCta = useCallback((destId: string, messageId: string) => {
    setDestinations((prev) =>
      prev.map((d) => {
        if (d.id !== destId) return d;
        return {
          ...d,
          conversation: (d.conversation || []).map((m) =>
            m.id === messageId ? { ...m, webSearchCtaResolved: true } : m,
          ),
        };
      }),
    );
  }, []);

  const getConversation = useCallback(
    (destId: string): ConversationMessage[] => {
      const dest = destinations.find((d) => d.id === destId);
      return dest?.conversation || [];
    },
    [destinations],
  );

  const itineraryLaneDepthRef = useRef(itineraryLaneQueueDepth);
  itineraryLaneDepthRef.current = itineraryLaneQueueDepth;
  const planReviewGenRef = useRef(0);

  const queuePlanReviewAfterItineraries = useCallback(
    (sid: string) => {
      const gen = ++planReviewGenRef.current;
      void (async () => {
        logItin("plan_review_defer_wait_begin", { sessionId: sid.slice(0, 12) });
        await waitForItinerariesBeforePlanReview(() => ({
          destinations: destinationsRef.current,
          itineraryLaneQueueDepth: itineraryLaneDepthRef.current,
        }));
        if (gen !== planReviewGenRef.current) return;
        logItin("plan_review_defer_wait_done", { sessionId: sid.slice(0, 12) });
        try {
          const pills = profileRef.current as unknown as WanderlyProfilePillDTO[];
          const dests = destinationsRef.current as unknown as Record<string, unknown>[];
          const r = await planReview(sid, pills, dests);
          if (gen !== planReviewGenRef.current) return;
          const rev = r.review;
          if (!rev || rev.skipped) return;
          const md = planReviewMarkdownForUi(rev.assistant_markdown);
          if (md) addResponse({ type: "message", role: "agent", text: `**Trip review (advisor)**\n\n${md}` });
          for (const pr of rev.suggested_prompts || []) {
            if (typeof pr === "string" && pr.trim()) addResponse({ type: "tip", text: `Try asking: ${pr}` });
          }
        } catch {
          /* non-fatal */
        }
      })();
    },
    [addResponse],
  );

  useEffect(() => {
    registerItineraryLaneDepthSink(setItineraryLaneQueueDepth);
    return () => registerItineraryLaneDepthSink(null);
  }, []);

  return (
    <Ctx.Provider
      value={{
        phase, setPhase,
        profile, setProfile,
        updateProfilePill,
        destinations, setDestinations,
        messages, addMessage,
        insights, addInsight, dismissInsight,
        responses, addResponse, dismissResponse,
        addConversationMessage,
        resolveConversationRefineCta,
        resolveConversationWebSearchCta,
        getConversation,
        agentStatus, setAgentStatus,
        agentStreamCaption, setAgentStreamCaption,
        selectedDest, setSelectedDest,
        expandedDest, setExpandedDest,
        isCanvasCollapsed,
        setIsCanvasCollapsed,
        itineraryLaneQueueDepth,
        hasStarted, setHasStarted,
        sessionId, setSessionId,
        enrichmentPending, setEnrichmentPending,
        pendingRefineText, setPendingRefineText,
        composerPrefill, setComposerPrefill,
        undoStack, pushUndo, performUndo,
        queuePlanReviewAfterItineraries,
        refineUnlockCardNavigation,
        setRefineUnlockCardNavigation,
        setPlannerAbortController,
        startNewTrip,
        abortPlannerOperation,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useTrip() {
  const c = useContext(Ctx);
  if (!c) throw new Error("wrap in TripProvider");
  return c;
}

export { DESTINATIONS, RESERVE_DESTINATIONS };
export default TripProvider;