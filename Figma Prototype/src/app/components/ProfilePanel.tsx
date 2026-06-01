import type { Dispatch, SetStateAction } from "react";
import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { profileChangeAdvisor, profilePatch } from "../../api/wanderly";
import type { WanderlyProfilePillDTO } from "../../types/workspace";
import { mapDtoPillsToProfile, mergeSubmittedProfileWithServerPills } from "../lib/applyWorkspaceDto";
import { useTrip, ProfilePill, SuggestedAction, RESERVE_DESTINATIONS, type DestCard } from "./TripStore";
import { motion, AnimatePresence } from "motion/react";
import {
  Pencil, Check, X, AlertTriangle, Lightbulb, CheckCircle, Info,
  Play, Loader2, Zap, Sparkles,
} from "lucide-react";

function profilesDiffer(a: ProfilePill[], b: ProfilePill[]): boolean {
  const sig = (rows: ProfilePill[]) =>
    [...rows]
      .filter((p) => p.key !== "tags")
      .map((p) => `${p.key}=${p.value.trim()}`)
      .sort()
      .join("\u0001");
  return sig(a) !== sig(b);
}

// ── Action execution engine ──
// SuggestedAction.refinePrompt runs a real Cursor refine via TripStore.pendingRefineText + CommandBar.
// Other ids remain legacy demo mutations for old canned insights (if any).

function useActionExecutor() {
  const {
    setDestinations, updateProfilePill, addMessage, addInsight,
    setAgentStatus, setPhase, pushUndo, setExpandedDest, destinations,
    setPendingRefineText,
  } = useTrip();

  const executeAction = useCallback((action: SuggestedAction) => {
    const refine = action.refinePrompt?.trim();
    if (refine) {
      setPendingRefineText(refine);
      setPhase("complete");
      setAgentStatus("");
      setExpandedDest(null);
      return;
    }

    // Save state for undo BEFORE making changes
    pushUndo(action.label);

    setPhase("refining");
    setAgentStatus(`Executing: ${action.label}...`);

    const undoId = action.id;

    // Route to the right handler
    switch (action.id) {
      case "find-budget-destinations": {
        // Dismiss expensive ones, add budget-friendly reserves
        setTimeout(() => {
          setDestinations((prev) => {
            const kept = prev.map(d => {
              if (d.id === "santorini") return { ...d, dismissed: true };
              return d;
            });
            // Add Cartagena & Mexico City from reserves
            const cartagena = RESERVE_DESTINATIONS.find(d => d.id === "cartagena");
            const mexicoCity = RESERVE_DESTINATIONS.find(d => d.id === "mexico-city");
            const newDests = [...kept];
            if (cartagena && !kept.find(d => d.id === "cartagena")) newDests.push({ ...cartagena });
            if (mexicoCity && !kept.find(d => d.id === "mexico-city")) newDests.push({ ...mexicoCity });
            return newDests;
          });
          addMessage("agent",
            "I've made some changes to fit your new budget:\n\n" +
            "• Removed Santorini ($4.1k pp — too expensive)\n" +
            "• Added Cartagena ($1.8k pp) — Caribbean warmth with incredible food and colonial culture\n" +
            "• Added Mexico City ($1.5k pp) — world-class food scene, 5h direct flight, unbeatable value\n" +
            "• Kept Lisbon ($2.5k pp) and Bali ($3.2k pp, at the edge but possible)\n\n" +
            "All plans include full itineraries, flights, and hotels. You can undo this if it's not what you wanted.",
            undoId
          );
          setPhase("complete");
          setAgentStatus("");
          setExpandedDest(null);
        }, 1500);
        break;
      }

      case "shorten-trips": {
        setTimeout(() => {
          updateProfilePill("duration", "5 days");
          setDestinations((prev) => prev.map(d => {
            if (d.dismissed) return d;
            // Trim itinerary to 5 days
            const trimmedItin = d.itinerary ? d.itinerary.slice(0, 5).map((day, i) => {
              if (i === 4) return { ...day, label: "Fly Home", items: ["Morning: last exploration", "Pack up & check out", "Airport transfer", "Fly home"] };
              return day;
            }) : undefined;
            // Adjust hotels
            const trimmedHotels = d.hotels?.map(h => ({ ...h, nights: Math.min(h.nights, 4) }));
            // Recalc cost estimate
            const costReduction = d.id === "bali" ? "$2.4k pp" : d.id === "santorini" ? "$3.2k pp" : d.id === "lisbon" ? "$1.9k pp" : d.cost;
            return {
              ...d,
              itinerary: trimmedItin,
              hotels: trimmedHotels,
              cost: costReduction,
              warnings: undefined, // clear old warnings
            };
          }));
          addMessage("agent",
            "I've shortened all trips from 7 days to 5 days to reduce costs:\n\n" +
            "• Bali: $3.2k → $2.4k pp (cut days 6-7, kept the highlights)\n" +
            "• Santorini: $4.1k → $3.2k pp (still over budget but closer)\n" +
            "• Lisbon: $2.5k → $1.9k pp (great value, removed beach day)\n\n" +
            "Duration updated to 5 days. Itineraries trimmed to keep the best experiences. You can undo this change.",
            undoId
          );
          setPhase("complete");
          setAgentStatus("");
        }, 1200);
        break;
      }

      case "remove-incompatible": {
        setTimeout(() => {
          setDestinations((prev) => {
            const updated = prev.map(d => {
              if (d.warnings && d.warnings.length > 0) {
                return { ...d, dismissed: true };
              }
              return d;
            });
            // Add a reserve if we're running low
            const active = updated.filter(d => !d.dismissed);
            if (active.length < 2) {
              const cartagena = RESERVE_DESTINATIONS.find(r => r.id === "cartagena");
              if (cartagena && !updated.find(d => d.id === "cartagena")) {
                updated.push({ ...cartagena });
              }
            }
            return updated;
          });
          const removed = destinations.filter(d => d.warnings && d.warnings.length > 0 && !d.dismissed).map(d => d.name);
          addMessage("agent",
            `Removed ${removed.length} destination${removed.length > 1 ? "s" : ""} with compatibility issues: ${removed.join(", ")}. ` +
            (removed.length > 0 ? "I've added Cartagena as a budget-friendly alternative that matches your profile. " : "") +
            "You can undo this if you'd like them back.",
            undoId
          );
          setPhase("complete");
          setAgentStatus("");
          setExpandedDest(null);
        }, 1000);
        break;
      }

      case "upgrade-hotels": {
        setTimeout(() => {
          setDestinations((prev) => prev.map(d => {
            if (d.dismissed) return d;
            const upgradedHotels = d.hotels?.map(h => {
              if (d.id === "bali") return { ...h, name: h.area === "Ubud" ? "Four Seasons Sayan" : "W Bali Seminyak", price: h.area === "Ubud" ? "$420/n" : "$380/n" };
              if (d.id === "santorini") return { ...h, name: "Canaves Oia Epitome", price: "$550/n" };
              if (d.id === "lisbon") return { ...h, name: "Bairro Alto Hotel", price: "$310/n" };
              return h;
            });
            return { ...d, hotels: upgradedHotels };
          }));
          addMessage("agent",
            "Hotels upgraded to luxury tier across all destinations:\n\n" +
            "• Bali: Four Seasons Sayan ($420/n) + W Seminyak ($380/n)\n" +
            "• Santorini: Canaves Oia Epitome ($550/n)\n" +
            "• Lisbon: Bairro Alto Hotel ($310/n)\n\n" +
            "Total trip costs have increased accordingly. You can undo this upgrade.",
            undoId
          );
          setPhase("complete");
          setAgentStatus("");
        }, 1000);
        break;
      }

      case "replace-adventure": {
        setTimeout(() => {
          setDestinations((prev) => {
            const updated = prev.map(d => {
              if (d.id === "santorini") return { ...d, dismissed: true };
              return { ...d, warnings: undefined };
            });
            const cartagena = RESERVE_DESTINATIONS.find(r => r.id === "cartagena");
            if (cartagena && !updated.find(d => d.id === "cartagena")) {
              updated.push({ ...cartagena, score: 89, vibe: ["Culture", "Food", "Beach", "Adventure"] });
            }
            return updated;
          });
          addMessage("agent",
            "Swapped Santorini (scenic/romantic) for Cartagena which has much better adventure vibes:\n\n" +
            "• Snorkeling at Rosario Islands\n" +
            "• Street food exploration through Getsemaní\n" +
            "• Caribbean kayaking & island-hopping available\n\n" +
            "Bali and Lisbon kept — both have great adventure potential. You can undo this swap.",
            undoId
          );
          setPhase("complete");
          setAgentStatus("");
          setExpandedDest(null);
        }, 1200);
        break;
      }

      case "add-family-activities": {
        setTimeout(() => {
          setDestinations((prev) => prev.map(d => {
            if (d.dismissed) return d;
            if (d.id === "santorini") return { ...d, dismissed: true };
            const familyActivities = d.activities ? [...d.activities] : [];
            if (d.id === "bali") {
              familyActivities.push(
                { name: "Bali Safari & Marine Park", day: 4, type: "Family", price: "$40", duration: "4h" },
                { name: "Waterbom water park", day: 5, type: "Family", price: "$35", duration: "5h" },
              );
            }
            if (d.id === "lisbon") {
              familyActivities.push(
                { name: "Lisbon Oceanarium", day: 2, type: "Family", price: "$25", duration: "3h" },
                { name: "Sintra fairy-tale palaces", day: 3, type: "Family", price: "$30", duration: "Full day" },
              );
            }
            return { ...d, activities: familyActivities, warnings: undefined };
          }));
          addMessage("agent",
            "Adjusted plans for family of 4:\n\n" +
            "• Removed Santorini (steep stairs, not kid-friendly)\n" +
            "• Added family activities to Bali: Safari Park, Waterbom water park\n" +
            "• Added family activities to Lisbon: Oceanarium, fairy-tale Sintra palaces\n" +
            "• All pricing recalculated for 4 travelers\n\n" +
            "You can undo this to go back to the couple's plan.",
            undoId
          );
          setPhase("complete");
          setAgentStatus("");
          setExpandedDest(null);
        }, 1300);
        break;
      }

      case "focus-lisbon": {
        setTimeout(() => {
          setDestinations((prev) => prev.map(d => {
            if (d.id === "lisbon") return { ...d, pinned: true, score: 98, warnings: undefined };
            if (d.id !== "lisbon") return { ...d, dismissed: true };
            return d;
          }));
          setExpandedDest("lisbon");
          addMessage("agent",
            "Focused on Lisbon — your best match for a short trip:\n\n" +
            "• 7h direct flight (vs 20h Bali, 12h Santorini)\n" +
            "• Fits perfectly in 3-4 days\n" +
            "• Most walkable, no car needed\n\n" +
            "Other destinations hidden. You can undo to see all options again.",
            undoId
          );
          setPhase("complete");
          setAgentStatus("");
        }, 1000);
        break;
      }

      case "combine-destinations": {
        setTimeout(() => {
          setDestinations((prev) => prev.map(d => {
            if (d.id === "lisbon") {
              const extendedItin = d.itinerary ? [...d.itinerary.slice(0, 5),
                { day: 6, label: "Fly to Santorini", items: ["Morning flight LIS → ATH (3.5h)", "Connect ATH → JTR (45min)", "Check in Oia", "First caldera sunset"] },
                { day: 7, label: "Caldera Day", items: ["Fira to Oia hike", "Wine tasting at Santo Wines", "Catamaran cruise", "Dinner at Ammoudi Bay"] },
                { day: 8, label: "Sea & History", items: ["Red Beach morning", "Ancient Akrotiri", "Afternoon at pool", "Farewell dinner at Selene"] },
                { day: 9, label: "Fly Home", items: ["Morning check-out", "Ferry to Athens", "Fly ATH → JFK"] },
              ] : undefined;
              return {
                ...d,
                name: "Lisbon + Santorini",
                hook: "Best of both — Lisbon's food & fado, then Santorini's caldera sunsets",
                cost: "$4.2k pp",
                itinerary: extendedItin,
                score: 95,
                warnings: undefined,
              };
            }
            if (d.id === "santorini") return { ...d, dismissed: true };
            return d;
          }));
          updateProfilePill("duration", "10-14 days");
          addMessage("agent",
            "Created a combined Lisbon + Santorini itinerary:\n\n" +
            "• Days 1-5: Lisbon (food, fado, Sintra, history)\n" +
            "• Day 6: Fly LIS → ATH → Santorini\n" +
            "• Days 7-8: Santorini highlights (caldera, wine, beaches)\n" +
            "• Day 9: Fly home via Athens\n\n" +
            "Total ~$4.2k pp. Duration updated to 10 days. You can undo to separate them.",
            undoId
          );
          setPhase("complete");
          setAgentStatus("");
          setExpandedDest("lisbon");
        }, 1500);
        break;
      }

      default: {
        setTimeout(() => {
          addMessage("agent", `I've noted your preference for "${action.label}". Adjusting plans accordingly...`);
          setPhase("complete");
          setAgentStatus("");
        }, 800);
      }
    }
  }, [
    setDestinations,
    updateProfilePill,
    addMessage,
    addInsight,
    setAgentStatus,
    setPhase,
    pushUndo,
    setExpandedDest,
    destinations,
    setPendingRefineText,
  ]);

  return { executeAction };
}

// ── Simulated "LLM" that generates contextual actions based on profile changes ──

interface ProfileReaction {
  warnings: { destId: string; text: string }[];
  insights: { text: string; actions: SuggestedAction[] }[];
}

function generateReaction(key: string, value: string): ProfileReaction | null {
  const reactions: Record<string, Record<string, ProfileReaction>> = {
    budget: {
      "~$2-3k total": {
        warnings: [
          { destId: "santorini", text: "Santorini exceeds your new budget — $4.1k pp won't work." },
          { destId: "bali", text: "Bali is right at the edge at $3.2k pp. Consider shorter stay." },
        ],
        insights: [
          {
            text: "Budget reduced. Lisbon fits well, but others need changes. Here's what I'd suggest:",
            actions: [
              { id: "find-budget-destinations", label: "Find cheaper destinations", description: "Replace expensive options with budget-friendly alternatives like Cartagena and Mexico City", icon: "🔍" },
              { id: "shorten-trips", label: "Shorten to 5 days", description: "Reduce all trips from 7 to 5 days to cut costs ~30%", icon: "✂️" },
              { id: "remove-incompatible", label: "Remove over-budget trips", description: "Drop destinations that don't fit and find replacements", icon: "🗑️" },
            ],
          },
        ],
      },
      "~$5-8k total": {
        warnings: [],
        insights: [
          {
            text: "More budget room! I can enhance your options:",
            actions: [
              { id: "upgrade-hotels", label: "Upgrade all hotels", description: "Switch to luxury properties across all destinations", icon: "✨" },
              { id: "combine-destinations", label: "Combine Lisbon + Santorini", description: "Create a multi-city trip covering both for ~10 days", icon: "🔗" },
            ],
          },
        ],
      },
      "~$8k+ total": {
        warnings: [],
        insights: [
          {
            text: "Premium budget — you can go all out:",
            actions: [
              { id: "upgrade-hotels", label: "Upgrade to luxury", description: "Business class flights and 5-star properties everywhere", icon: "💎" },
              { id: "combine-destinations", label: "Multi-city itinerary", description: "Combine Lisbon + Santorini into one epic trip", icon: "🌍" },
            ],
          },
        ],
      },
    },
    vibe: {
      "Adventure, hiking, nature": {
        warnings: [
          { destId: "santorini", text: "Santorini is scenic/romantic — not great for adventure seekers." },
        ],
        insights: [
          {
            text: "Adventure vibe shift detected. Some destinations don't match:",
            actions: [
              { id: "replace-adventure", label: "Swap Santorini for adventure pick", description: "Replace with Cartagena — snorkeling, island-hopping, Caribbean adventure", icon: "🏔️" },
              { id: "remove-incompatible", label: "Remove mismatched trips", description: "Drop scenic-only destinations that lack adventure", icon: "🗑️" },
            ],
          },
        ],
      },
      "Relaxation, spa, beaches": {
        warnings: [
          { destId: "lisbon", text: "Lisbon is more of a city trip — beach access requires day trips." },
        ],
        insights: [
          {
            text: "Relaxation mode! Bali is perfect, but Lisbon's a stretch:",
            actions: [
              { id: "remove-incompatible", label: "Remove city-focused trips", description: "Drop destinations without great beach/spa access", icon: "🏖️" },
              { id: "find-budget-destinations", label: "Find more beach options", description: "Add tropical alternatives like Cartagena", icon: "🔍" },
            ],
          },
        ],
      },
    },
    who: {
      "Solo traveler": {
        warnings: [],
        insights: [
          {
            text: "Adjusted for solo travel. Pricing updated to single occupancy. Lisbon is especially great for solo trips.",
            actions: [],
          },
        ],
      },
      "Family (4)": {
        warnings: [
          { destId: "santorini", text: "Santorini can be tricky with kids — steep stairs, limited beaches." },
        ],
        insights: [
          {
            text: "Family trip! Some destinations need adjustments for kids:",
            actions: [
              { id: "add-family-activities", label: "Add family activities", description: "Replace adult-focused experiences with kid-friendly options", icon: "👨‍👩‍👧‍👦" },
              { id: "remove-incompatible", label: "Remove kid-unfriendly spots", description: "Drop destinations that aren't practical for families", icon: "🗑️" },
            ],
          },
        ],
      },
    },
    duration: {
      "3-4 days": {
        warnings: [
          { destId: "bali", text: "3-4 days is too short for Bali — 20h flight doesn't justify it." },
          { destId: "santorini", text: "Santorini in 3 days means missing half the experience." },
        ],
        insights: [
          {
            text: "Short trip! Long-haul destinations don't make sense:",
            actions: [
              { id: "focus-lisbon", label: "Focus on Lisbon only", description: "Lisbon is perfect for 3-4 days: direct 7h flight, walkable city", icon: "🎯" },
              { id: "remove-incompatible", label: "Remove long-haul trips", description: "Drop destinations that need 12+ hours of flying", icon: "✈️" },
              { id: "find-budget-destinations", label: "Find closer alternatives", description: "Add Mexico City or Cartagena — both under 6h flight", icon: "🔍" },
            ],
          },
        ],
      },
      "10-14 days": {
        warnings: [],
        insights: [
          {
            text: "Extended trip! You could combine multiple destinations:",
            actions: [
              { id: "combine-destinations", label: "Combine Lisbon + Santorini", description: "5 days Lisbon → 4 days Santorini, one epic trip", icon: "🔗" },
            ],
          },
        ],
      },
    },
  };

  return reactions[key]?.[value] || null;
}

// ── Components ──

/** Profile-change advisor uses a blocking Cursor call (server timeout ~120s). */
const PROFILE_ADVISOR_CLIENT_TIMEOUT_MS = 90_000;

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) => {
      window.setTimeout(() => reject(new Error(`${label} timed out after ${Math.round(ms / 1000)}s`)), ms);
    }),
  ]);
}

const PROFILE_OPTIONS: Record<string, string[]> = {
  when: ["Mar 1–7", "Mar 8–14", "Mar 15–22", "Apr 1–8", "Flexible"],
  who: ["Solo traveler", "2 travelers", "Family (4)", "Group (6+)"],
  from: ["Seattle (SEA)", "San Francisco (SFO)", "Los Angeles (LAX)", "New York (JFK)", "Chicago (ORD)", "Denver (DEN)"],
  vibe: ["Warm, culture, food", "Adventure, hiking, nature", "Relaxation, spa, beaches", "Romance, wine, scenery"],
  trip_style: [
    "General leisure",
    "Backpacking / multi-day hike",
    "Car camping",
    "Road trip",
    "City break",
  ],
  budget: ["~$2-3k total", "~$3-5k total", "~$5-8k total", "~$8k+ total"],
  duration: ["3-4 days", "7 days", "10-14 days", "2+ weeks"],
};

function applyLocalGenerateReaction(
  key: string,
  value: string,
  label: string,
  addInsight: (type: "warning" | "tip" | "info", text: string, relatedTo?: string, actions?: SuggestedAction[]) => void,
  setDestinations: Dispatch<SetStateAction<DestCard[]>>,
) {
  const reaction = generateReaction(key, value);
  if (reaction) {
    if (reaction.warnings.length > 0) {
      setTimeout(() => {
        setDestinations((prev) =>
          prev.map((d) => {
            const warning = reaction.warnings.find((w) => w.destId === d.id);
            if (warning) return { ...d, warnings: [warning.text] };
            return { ...d, warnings: undefined };
          }),
        );
      }, 300);
    } else {
      setTimeout(() => {
        setDestinations((prev) => prev.map((d) => ({ ...d, warnings: undefined })));
      }, 300);
    }
    reaction.insights.forEach((ins, i) => {
      setTimeout(() => {
        addInsight(
          reaction.warnings.length > 0 ? "warning" : "tip",
          ins.text,
          undefined,
          ins.actions.length > 0 ? ins.actions : undefined,
        );
      }, 500 + i * 400);
    });
  } else {
    setTimeout(() => {
      addInsight(
        "info",
        `Updated ${label.toLowerCase()} to "${value}". Your saved itineraries were not changed; use the trip bar if you want the planner to rebuild them.`,
      );
      setDestinations((prev) => prev.map((d) => ({ ...d, warnings: undefined })));
    }, 300);
  }
}

function EditablePill({
  pill,
  committedValue,
  onPillChange,
}: {
  pill: ProfilePill;
  committedValue: string;
  onPillChange: (key: string, value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [showOptions, setShowOptions] = useState(false);
  const [customValue, setCustomValue] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const modified = pill.value !== committedValue;

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setEditing(false);
        setShowOptions(false);
        setCustomValue("");
      }
    };
    if (editing) document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [editing]);

  const handleSelect = (value: string) => {
    if (value === pill.value) {
      setEditing(false);
      setShowOptions(false);
      return;
    }
    onPillChange(pill.key, value);
    setEditing(false);
    setShowOptions(false);
    setCustomValue("");
  };

  const handleCustomSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (customValue.trim()) {
      handleSelect(customValue.trim());
    }
  };

  return (
    <div ref={ref} className="relative">
      <motion.button
        onClick={() => {
          setEditing(!editing);
          setShowOptions(!showOptions);
        }}
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
        className={`flex items-center gap-2 w-full px-3 py-2 rounded-xl text-left transition-all ${
          editing
            ? "bg-indigo-500/15 border border-indigo-500/30"
            : modified
              ? "bg-amber-500/10 border border-amber-400/25 hover:border-amber-400/40"
              : "bg-white/[0.04] border border-white/[0.06] hover:border-white/[0.12] hover:bg-white/[0.06]"
        }`}
      >
        <span className="text-[14px]">{pill.icon}</span>
        <div className="flex-1 min-w-0">
          <p className="text-[10px] text-white/30 uppercase tracking-wider">{pill.label}</p>
          <p className="text-[13px] text-white/80 truncate">{pill.value}</p>
        </div>
        <Pencil className="w-3 h-3 text-white/20 shrink-0" />
      </motion.button>

      <AnimatePresence>
        {showOptions && (
          <motion.div
            initial={{ opacity: 0, y: -4, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.95 }}
            className="absolute top-full left-0 right-0 mt-1.5 z-50 bg-[#1a1a2e] border border-white/[0.1] rounded-xl overflow-hidden shadow-xl shadow-black/40"
          >
            {(PROFILE_OPTIONS[pill.key] || []).map((opt) => (
              <button
                key={opt}
                onClick={() => handleSelect(opt)}
                className={`w-full text-left px-3 py-2 text-[13px] transition-colors ${
                  opt === pill.value
                    ? "bg-indigo-500/20 text-indigo-300"
                    : "text-white/60 hover:bg-white/[0.06] hover:text-white/80"
                }`}
              >
                {opt}
                {opt === pill.value && <Check className="w-3 h-3 inline ml-2 text-indigo-400" />}
              </button>
            ))}
            {pill.key !== "vibe" && (
              <p className="px-3 py-2 text-[10px] text-white/25 border-t border-white/[0.06] leading-snug">
                For wording that is not listed here, describe it in the main trip chat or this card&apos;s chat — then
                use <span className="text-white/35">Update trip</span> if the sidebar should stay structured.
              </p>
            )}

            {/* Custom input field for vibe */}
            {pill.key === "vibe" && (
              <form onSubmit={handleCustomSubmit} className="border-t border-white/[0.08] p-2">
                <div className="flex items-center gap-1.5">
                  <input
                    type="text"
                    value={customValue}
                    onChange={(e) => setCustomValue(e.target.value)}
                    placeholder="Or type your own vibe..."
                    className="flex-1 px-2.5 py-1.5 bg-white/[0.04] border border-white/[0.08] rounded-lg text-[12px] text-white/80 placeholder:text-white/30 focus:outline-none focus:border-indigo-500/40 focus:bg-white/[0.06]"
                    autoFocus={false}
                  />
                  <button
                    type="submit"
                    disabled={!customValue.trim()}
                    className="px-2.5 py-1.5 bg-indigo-500/20 border border-indigo-500/30 rounded-lg text-[11px] text-indigo-300 hover:bg-indigo-500/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Set
                  </button>
                </div>
                <p className="text-[9px] text-white/20 mt-1 ml-0.5">Any keywords, the AI will understand</p>
              </form>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function ActionButton({ action, onExecute }: { action: SuggestedAction; onExecute: () => void }) {
  const [loading, setLoading] = useState(false);

  const handleClick = () => {
    setLoading(true);
    onExecute();
    // Loading state auto-clears when the phase changes
    setTimeout(() => setLoading(false), 2000);
  };

  return (
    <motion.button
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.97 }}
      onClick={handleClick}
      disabled={loading}
      className="flex items-center gap-2 w-full px-3 py-2 rounded-lg bg-white/[0.04] border border-white/[0.08] hover:bg-white/[0.08] hover:border-white/[0.14] transition-all text-left group disabled:opacity-50"
    >
      <span className="text-[13px] shrink-0">{action.icon}</span>
      <div className="flex-1 min-w-0">
        <p className="text-[12px] text-white/70 group-hover:text-white/90 transition-colors">{action.label}</p>
        <p className="text-[10px] text-white/30 leading-tight truncate">{action.description}</p>
      </div>
      {loading ? (
        <Loader2 className="w-3.5 h-3.5 text-indigo-400 animate-spin shrink-0" />
      ) : (
        <Play className="w-3 h-3 text-white/20 group-hover:text-indigo-400 transition-colors shrink-0" />
      )}
    </motion.button>
  );
}

function InsightCard({
  insight,
  onDismiss,
  onExecuteAction,
}: {
  insight: { id: string; type: string; text: string; actions?: SuggestedAction[] };
  onDismiss: () => void;
  onExecuteAction: (action: SuggestedAction) => void;
}) {
  const icons = {
    warning: <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />,
    tip: <Lightbulb className="w-3.5 h-3.5 text-indigo-400" />,
    success: <CheckCircle className="w-3.5 h-3.5 text-emerald-400" />,
    info: <Info className="w-3.5 h-3.5 text-blue-400" />,
    comparison: <Info className="w-3.5 h-3.5 text-purple-400" />,
  };
  const borders = {
    warning: "border-amber-500/20 bg-amber-500/[0.06]",
    tip: "border-indigo-500/20 bg-indigo-500/[0.06]",
    success: "border-emerald-500/20 bg-emerald-500/[0.06]",
    info: "border-blue-500/20 bg-blue-500/[0.06]",
    comparison: "border-purple-500/20 bg-purple-500/[0.06]",
  };
  const hasActions = insight.actions && insight.actions.length > 0;
  const isLongContent = insight.text.length > 500; // Comparison texts are long

  return (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -10 }}
      layout
      className={`rounded-xl border overflow-hidden ${borders[insight.type as keyof typeof borders] || borders.info}`}
    >
      {/* Message */}
      <div className="flex gap-2 px-3 py-2.5">
        <div className="shrink-0 mt-0.5">{icons[insight.type as keyof typeof icons] || icons.info}</div>
        <div 
          className={`flex-1 text-[12px] text-white/60 leading-relaxed ${
            isLongContent ? "max-h-[400px] overflow-y-auto pr-2 scrollbar-thin" : ""
          }`}
        >
          {/* Render markdown-style content */}
          {insight.text.split('\n').map((line, i) => {
            // Headers
            if (line.startsWith('### ')) {
              return <h3 key={i} className="text-[13px] font-semibold text-white/80 mt-3 mb-1.5 first:mt-0">{line.replace('### ', '')}</h3>;
            }
            if (line.startsWith('#### ')) {
              return <h4 key={i} className="text-[12px] font-semibold text-white/75 mt-2.5 mb-1 first:mt-0">{line.replace('#### ', '')}</h4>;
            }
            if (line.startsWith('## ')) {
              return <h2 key={i} className="text-[14px] font-semibold text-white/90 mb-2 first:mt-0">{line.replace('## ', '')}</h2>;
            }
            // Bold text
            if (line.includes('**')) {
              const parts = line.split(/\*\*(.*?)\*\*/g);
              return (
                <p key={i} className="mb-1">
                  {parts.map((part, j) => 
                    j % 2 === 1 ? <strong key={j} className="text-white/80">{part}</strong> : part
                  )}
                </p>
              );
            }
            // Horizontal rule
            if (line === '---') {
              return <hr key={i} className="border-white/10 my-3" />;
            }
            // Bullet points
            if (line.startsWith('- ')) {
              return <p key={i} className="ml-3 mb-0.5">• {line.replace('- ', '')}</p>;
            }
            // Empty lines
            if (line.trim() === '') {
              return <div key={i} className="h-1" />;
            }
            // Regular text
            return <p key={i} className="mb-1">{line}</p>;
          })}
        </div>
        <button onClick={onDismiss} className="shrink-0 text-white/20 hover:text-white/40 mt-0.5">
          <X className="w-3 h-3" />
        </button>
      </div>

      {/* Suggested actions */}
      {hasActions && (
        <div className="px-2.5 pb-2.5 flex flex-col gap-1">
          <div className="flex items-center gap-1.5 px-1 mb-0.5">
            <Zap className="w-2.5 h-2.5 text-indigo-400" />
            <span className="text-[9px] text-white/25 uppercase tracking-widest">Suggested Actions</span>
          </div>
          {insight.actions!.map((action, i) => (
            <ActionButton
              key={action.id}
              action={action}
              onExecute={() => {
                onExecuteAction(action);
                onDismiss();
              }}
            />
          ))}
        </div>
      )}
    </motion.div>
  );
}

export function ProfilePanel() {
  const {
    profile,
    insights,
    dismissInsight,
    applyProfilePillBatch,
    sessionId,
    destinations,
    addInsight,
    setDestinations,
  } = useTrip();
  const { executeAction } = useActionExecutor();
  const [draftProfile, setDraftProfile] = useState<ProfilePill[] | null>(null);
  const [applyBusy, setApplyBusy] = useState(false);
  const [advisorBusy, setAdvisorBusy] = useState(false);

  const profileSig = useMemo(
    () =>
      [...profile]
        .filter((p) => p.key !== "tags")
        .map((p) => `${p.key}=${p.value.trim()}`)
        .sort()
        .join("\u0001"),
    [profile],
  );

  useEffect(() => {
    setDraftProfile(null);
  }, [profileSig]);

  const displayProfile = draftProfile ?? profile;
  const hasPending = draftProfile !== null && profilesDiffer(draftProfile, profile);

  const onPillChange = useCallback(
    (key: string, value: string) => {
      setDraftProfile((prev) => {
        const base = prev ?? profile;
        return base.map((p) => (p.key === key ? { ...p, value } : p));
      });
    },
    [profile],
  );

  const handleDiscard = useCallback(() => {
    setDraftProfile(null);
  }, []);

  const handleApplyProfile = useCallback(async () => {
    const nextDraft = draftProfile;
    if (!nextDraft || !profilesDiffer(nextDraft, profile)) return;
    const priorProfile = profile.map((p) => ({ ...p }));
    const destForAdvisor = destinations as unknown as Record<string, unknown>[];

    if (!sessionId || destinations.length === 0) {
      applyProfilePillBatch(nextDraft);
      setDraftProfile(null);
      const changed = nextDraft.find(
        (p) => (priorProfile.find((o) => o.key === p.key)?.value ?? "") !== p.value,
      );
      if (changed) {
        applyLocalGenerateReaction(changed.key, changed.value, changed.label, addInsight, setDestinations);
      }
      return;
    }

    setApplyBusy(true);
    let deltas: Array<{ key: string; old: string; new: string; label: string }> = [];
    let finalPills: ProfilePill[] = nextDraft;
    try {
      const { wanderly } = await profilePatch(sessionId, nextDraft as WanderlyProfilePillDTO[]);
      const serverPills = wanderly.profile_pills?.length
        ? mapDtoPillsToProfile(wanderly.profile_pills)
        : null;
      finalPills = mergeSubmittedProfileWithServerPills(nextDraft, priorProfile, serverPills);
      applyProfilePillBatch(finalPills);
      setDraftProfile(null);

      deltas = finalPills.flatMap((p) => {
        const o = priorProfile.find((x) => x.key === p.key);
        if (!o || o.value === p.value) return [];
        return [{ key: p.key, old: o.value, new: p.value, label: p.label }];
      });
    } catch {
      addInsight("warning", "Could not save traveler profile to the server. Check your connection and try again.");
      return;
    } finally {
      setApplyBusy(false);
    }

    if (deltas.length === 0) return;

    const runAdvisor = async () => {
      const pill_key = deltas.length === 1 ? deltas[0].key : "multiple";
      const old_value =
        deltas.length === 1 ? deltas[0].old : deltas.map((d) => `${d.key}: ${d.old}`).join(" | ");
      const new_value =
        deltas.length === 1 ? deltas[0].new : deltas.map((d) => `${d.key}: ${d.new}`).join(" | ");
      const { result } = await withTimeout(
        profileChangeAdvisor({
          session_id: sessionId,
          pill_key,
          old_value,
          new_value,
          profile_pills: finalPills as unknown as WanderlyProfilePillDTO[],
          destinations: destForAdvisor,
        }),
        PROFILE_ADVISOR_CLIENT_TIMEOUT_MS,
        "Profile change check",
      );
      if (result.skipped || (!result.headline && !(result.suggestions?.length))) {
        const d0 = deltas[0];
        applyLocalGenerateReaction(d0.key, d0.new, d0.label, addInsight, setDestinations);
        return;
      }
      if (result.warnings && result.warnings.length > 0) {
        setTimeout(() => {
          setDestinations((prev) =>
            prev.map((d) => {
              const hit = result.warnings!.find((w) => w.destination_id === d.id);
              if (hit) return { ...d, warnings: [hit.text] };
              return { ...d, warnings: undefined };
            }),
          );
        }, 200);
      } else {
        setTimeout(() => {
          setDestinations((prev) => prev.map((d) => ({ ...d, warnings: undefined })));
        }, 200);
      }
      const actions: SuggestedAction[] = (result.suggestions || []).map((s, i) => ({
        id: `advisor-refine:${i}`,
        label: s.label,
        description: s.description,
        icon: s.icon || "✨",
        refinePrompt: s.refine_prompt,
      }));
      const headline = (result.headline || "").trim();
      if (actions.length > 0 && headline) {
        setTimeout(() => {
          addInsight(result.warnings?.length ? "warning" : "tip", headline, undefined, actions);
        }, 450);
      } else if (headline) {
        setTimeout(() => addInsight("info", headline), 450);
      } else if (actions.length > 0) {
        setTimeout(
          () => addInsight("tip", "Here are some next steps for your planner:", undefined, actions),
          450,
        );
      }
    };

    setAdvisorBusy(true);
    void (async () => {
      try {
        await runAdvisor();
      } catch {
        const d0 = deltas[0];
        applyLocalGenerateReaction(d0.key, d0.new, d0.label, addInsight, setDestinations);
      } finally {
        setAdvisorBusy(false);
      }
    })();
  }, [
    draftProfile,
    profile,
    sessionId,
    destinations,
    applyProfilePillBatch,
    addInsight,
    setDestinations,
  ]);

  const activeInsights = insights
    .filter((i) => !i.dismissed)
    .sort((a, b) => b.ts - a.ts)
    .slice(0, 5);

  if (profile.length === 0) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-4"
      >
        <div className="flex items-center gap-2 mb-2">
          <motion.div
            className="w-1.5 h-1.5 rounded-full bg-indigo-400"
            animate={{ opacity: [0.35, 1, 0.35] }}
            transition={{ duration: 1.4, repeat: Infinity }}
          />
          <span className="text-[11px] text-white/30 uppercase tracking-widest">Traveler Profile</span>
        </div>
        <p className="text-[12px] leading-relaxed text-white/40">
          Loading traveler profile from your saved trip…
        </p>
      </motion.div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Profile section */}
      <div>
        <div className="flex items-center gap-2 mb-2.5">
          <div className="w-1.5 h-1.5 rounded-full bg-indigo-400" />
          <span className="text-[11px] text-white/30 uppercase tracking-widest">Traveler Profile</span>
        </div>
        {applyBusy && (
          <div className="mb-2 rounded-lg border border-indigo-400/25 bg-indigo-500/10 px-2.5 py-2 text-[11px] text-indigo-100/90 leading-snug">
            Saving traveler profile to your trip file…
          </div>
        )}
        {advisorBusy && !applyBusy && (
          <div className="mb-2 rounded-lg border border-indigo-400/20 bg-indigo-500/5 px-2.5 py-2 text-[11px] text-white/55 leading-snug">
            Travel agent is checking how your change fits each destination (usually under a minute)…
          </div>
        )}
        {hasPending && (
          <div className="mb-2 rounded-lg border border-amber-400/25 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-100/90 leading-snug">
            You have unsaved traveler settings. Press <span className="font-medium text-white">Update trip</span> to save
            them—existing itineraries and booking results stay until you ask the planner to refresh or follow a travel
            agent suggestion.
          </div>
        )}
        {hasPending && (
          <div className="flex items-center gap-2 mb-2">
            <button
              type="button"
              disabled={applyBusy}
              onClick={() => void handleApplyProfile()}
              className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-[12px] font-medium text-white shadow-sm shadow-indigo-900/30 transition hover:bg-indigo-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {applyBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
              Update trip
            </button>
            <button
              type="button"
              disabled={applyBusy}
              onClick={handleDiscard}
              className="shrink-0 rounded-lg border border-white/[0.12] px-2.5 py-2 text-[11px] text-white/55 transition hover:border-white/[0.2] hover:text-white/80 disabled:opacity-40"
            >
              Discard
            </button>
          </div>
        )}
        <div className="flex flex-col gap-1.5">
          {displayProfile
            .filter((p) => p.key !== "tags")
            .map((p) => (
              <EditablePill
                key={p.key}
                pill={p}
                committedValue={profile.find((x) => x.key === p.key)?.value ?? p.value}
                onPillChange={onPillChange}
              />
            ))}
        </div>
      </div>

      {/* Agent insights with actions */}
      <AnimatePresence>
        {activeInsights.length > 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="mt-1"
          >
            <div className="flex items-center gap-2 mb-2">
              <Sparkles className="h-3.5 w-3.5 shrink-0 text-indigo-400" aria-hidden />
              <span className="text-[11px] font-medium text-white/45 tracking-wide">Travel agent</span>
            </div>
            <div className="flex flex-col gap-2">
              <AnimatePresence>
                {activeInsights.map((insight) => (
                  <InsightCard
                    key={insight.id}
                    insight={insight}
                    onDismiss={() => dismissInsight(insight.id)}
                    onExecuteAction={executeAction}
                  />
                ))}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}