import { useState, useRef, useEffect } from "react";
import { motion } from "motion/react";
import { Send, X, MessageSquare, ChevronDown, Sparkles } from "lucide-react";
import { advisorFeed } from "../../api/wanderly";
import { renderInlineBold } from "../lib/markdownBold";
import { logItin } from "../lib/itineraryDebug";
import { useTrip, type ConversationMessage } from "./TripStore";

/** Same inline `**bold**` handling as the main agent thread (`AgentResponses`). */
function AgentMessageBody({ text, className }: { text: string; className?: string }) {
  return (
    <div className={className}>
      {text.split("\n").map((line, i) => (
        <span key={i} className="block whitespace-pre-wrap">
          {renderInlineBold(line)}
        </span>
      ))}
    </div>
  );
}

interface ConversationPanelProps {
  destId: string;
  destName: string;
  mode: "minimized" | "footer" | "panel";
  onModeChange: (mode: "minimized" | "footer" | "panel") => void;
}

/**
 * Detect messages that should run the real workspace refine (same as CommandBar)
 * vs lightweight Q&A (cheap advisor API with offline fallback).
 */
function wantsWorkspaceRefine(raw: string): boolean {
  const t = raw.trim().toLowerCase();
  if (!t) return false;
  // Prefix forces a planner refine regardless of wording
  if (/^(>|!plan|\/trip)\s+/i.test(t)) return true;

  const tripObject =
    /\b(itinerary|day\s*\d+|the plan|my plan|this trip|schedule|trip|hotels?|flights?|activities|booking|destinations|shortlist)\b/i.test(
      t,
    );
  /* Avoid treating "how different are…" / compare-only asks as planner work: no bare ``different``. */
  const action =
    /\b(change|update|modify|adjust|swap|replace|remove|add|delete|move|shift|reschedule|replan|redo|tweak|revise|shorten|extend|refresh|rebuild|regenerate|slow|faster|pace|include|exclude|skip|drop|cancel|upgrade|downgrade|cheaper|affordable|free day|rest day)\b/i.test(
      t,
    );
  const imperative =
    /^(make|turn|set|put|give me|can you (change|update|add|remove)|i want|i need|please (add|remove|change|update))\b/i.test(
      t,
    );

  if (tripObject && (action || imperative)) return true;
  if (/\b(refresh|rebuild|regenerate)\s+(the\s+)?(itinerary|plan)\b/i.test(t)) return true;
  if (/\block\b.*\b(in|for)\b.*\b(day|itinerary|trip)\b/i.test(t)) return true;
  return false;
}

export function ConversationPanel({ destId, destName, mode, onModeChange }: ConversationPanelProps) {
  const {
    getConversation,
    addConversationMessage,
    resolveConversationRefineCta,
    setPendingRefineText,
    phase,
    sessionId,
    destinations,
  } = useTrip();
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const replyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refineAwaitingReplyRef = useRef(false);
  const sawRefiningPhaseRef = useRef(false);
  const conversation = getConversation(destId);

  const globalBusy =
    phase === "thinking" || phase === "streaming" || phase === "refining";

  useEffect(() => {
    return () => {
      if (replyTimerRef.current) clearTimeout(replyTimerRef.current);
    };
  }, []);

  // After a conversation-triggered refine, turn off typing and confirm in-thread.
  useEffect(() => {
    if (!refineAwaitingReplyRef.current) return;
    if (phase === "refining" || phase === "thinking" || phase === "streaming") {
      sawRefiningPhaseRef.current = true;
    }
    if (phase === "complete" && sawRefiningPhaseRef.current) {
      sawRefiningPhaseRef.current = false;
      refineAwaitingReplyRef.current = false;
      setIsTyping(false);
      addConversationMessage(
        destId,
        "agent",
        "I sent that to the trip planner. Workspace and profile update from the model; if you changed trip length or day-by-day details, the card may clear the old plan briefly while the itinerary rebuilds — scroll the card for the new days.",
      );
    }
  }, [phase, destId, addConversationMessage]);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [conversation, isTyping]);

  const handleSend = async () => {
    if (!input.trim() || globalBusy) return;

    const userMessage = input.trim();
    setInput("");

    addConversationMessage(destId, "user", userMessage);

    if (mode === "minimized") {
      onModeChange("footer");
    }

    const runRefine = wantsWorkspaceRefine(userMessage);

    if (runRefine) {
      if (!sessionId?.trim() || destinations.length === 0) {
        addConversationMessage(
          destId,
          "agent",
          "I need an active trip shortlist before I can change the plan. Finish a first plan from the bar below (when no card is expanded), then try again here.",
        );
        return;
      }
      const stripped = userMessage.replace(/^(>|!plan|\/trip)\s+/i, "").trim() || userMessage;
      const forceImmediate = /^(>|!plan|\/trip)\s+/i.test(userMessage);

      logItin("conversation_panel_route", {
        branch: "planner_candidate",
        forceImmediate,
        messagePreview: userMessage.slice(0, 160),
      });

      if (forceImmediate) {
        const refinePayload = `The traveler is focused on destination "${destName}" (id: ${destId}). ${stripped}`;
        refineAwaitingReplyRef.current = true;
        sawRefiningPhaseRef.current = false;
        setIsTyping(true);
        setPendingRefineText(refinePayload);
        return;
      }

      addConversationMessage(
        destId,
        "agent",
        "That wording will run the **full trip planner** (day-by-day plans on your cards may refresh). For a quick comparison only, tap **Not now** and ask again in neutral wording — or start your message with `/trip` next time to skip this check.\n\nTap **Yes, update trip** to run the planner with your last message.",
        { refinePrompt: stripped },
      );
      return;
    }

    setIsTyping(true);
    if (replyTimerRef.current) clearTimeout(replyTimerRef.current);
    replyTimerRef.current = null;
    void (async () => {
      try {
        const sid = sessionId?.trim();
        if (!sid) {
          addConversationMessage(destId, "agent", generateMockResponse(userMessage, destName));
          return;
        }
        const out = await advisorFeed(sid, userMessage, { focusDestinationId: destId });
        const sug = out.advisor_suggestion as { assistant_message?: string; refine_prompt?: string | null } | undefined;
        const md = String(out.feed_markdown || sug?.assistant_message || "").trim();
        if (!md) {
          addConversationMessage(destId, "agent", generateMockResponse(userMessage, destName));
          return;
        }
        const refine =
          (typeof out.refine_prompt === "string" && out.refine_prompt.trim()) ||
          (typeof sug?.refine_prompt === "string" && sug.refine_prompt.trim()) ||
          undefined;
        addConversationMessage(destId, "agent", md, refine ? { refinePrompt: refine } : undefined);
      } catch (e) {
        const msg = (e as Error)?.message || String(e);
        addConversationMessage(
          destId,
          "agent",
          `Quick advisor request failed (${msg}). You can still use /trip in this chat to update the full plan.`,
        );
      } finally {
        setIsTyping(false);
      }
    })();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  /** Runs the same refine path as typing /trip, using the advisor-supplied planner prompt. */
  const applyRefineFromAssistantMessage = (msg: ConversationMessage) => {
    const inner = msg.refinePrompt?.trim();
    if (!inner || !sessionId?.trim() || destinations.length === 0) return;
    resolveConversationRefineCta(destId, msg.id);
    refineAwaitingReplyRef.current = true;
    sawRefiningPhaseRef.current = false;
    setIsTyping(true);
    const refinePayload = `The traveler is focused on destination "${destName}" (id: ${destId}). ${inner}`;
    setPendingRefineText(refinePayload);
  };

  // Render minimized (just input bar)
  if (mode === "minimized") {
    return (
      <div className="flex items-center gap-2 px-4 py-3 border-t border-white/[0.06] bg-black/20">
        <MessageSquare className="w-4 h-4 text-white/30" />
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={globalBusy}
          placeholder="Ask about the place or plan tweaks — open chat to tap Yes on suggestions, or /trip to always run the planner."
          className="flex-1 bg-transparent text-[13px] text-white placeholder:text-white/30 outline-none disabled:opacity-40"
        />
        <button
          onClick={handleSend}
          disabled={!input.trim() || globalBusy}
          className="w-7 h-7 rounded-lg bg-indigo-500/20 text-indigo-400 hover:bg-indigo-500/30 disabled:opacity-30 disabled:cursor-not-allowed flex items-center justify-center transition-all"
        >
          <Send className="w-3.5 h-3.5" />
        </button>
        {conversation.length > 0 && (
          <button
            onClick={() => onModeChange("footer")}
            className="text-[11px] text-white/40 hover:text-white/60 transition-colors whitespace-nowrap"
          >
            See chat ({conversation.length})
          </button>
        )}
      </div>
    );
  }

  // Render footer (input + last message)
  if (mode === "footer") {
    const lastAgent = [...conversation].reverse().find((m) => m.role === "agent");
    const lastUser = [...conversation].reverse().find((m) => m.role === "user");
    const preview =
      isTyping && lastUser
        ? { role: "agent" as const, text: "…", id: "typing-placeholder" }
        : lastAgent ?? lastUser;

    return (
      <div className="border-t border-white/[0.06] bg-black/20">
        {/* Last agent message preview (or latest user prompt while awaiting reply) */}
        {preview && preview.id !== "typing-placeholder" && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="px-4 py-2 border-b border-white/[0.04] cursor-pointer hover:bg-white/[0.02] transition-colors"
            onClick={() => onModeChange("panel")}
          >
            <div className="flex items-start gap-2">
              {preview.role === "agent" ? (
                <Sparkles className="w-3.5 h-3.5 text-indigo-400 mt-0.5 shrink-0" />
              ) : (
                <MessageSquare className="w-3.5 h-3.5 text-white/35 mt-0.5 shrink-0" />
              )}
              <div className="flex-1 min-w-0">
                {preview.role === "user" ? (
                  <p className="text-[12px] text-white/70 line-clamp-2">You: {preview.text}</p>
                ) : (
                  <div className="text-[12px] text-white/70 line-clamp-2">
                    <AgentMessageBody text={preview.text} />
                  </div>
                )}
              </div>
              <span className="text-[10px] text-white/30 hover:text-white/50 flex items-center gap-1 shrink-0">
                <span>Full thread</span>
                <ChevronDown className="w-3 h-3 -rotate-90" />
              </span>
            </div>
          </motion.div>
        )}
        {preview && preview.id === "typing-placeholder" && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="px-4 py-2 border-b border-white/[0.04] flex items-center gap-2"
          >
            <Sparkles className="w-3.5 h-3.5 text-indigo-400 shrink-0 animate-pulse" />
            <p className="text-[12px] text-white/40">Wanderly is thinking…</p>
          </motion.div>
        )}

        {/* Input bar */}
        <div className="flex items-center gap-2 px-4 py-3">
          <MessageSquare className="w-4 h-4 text-white/30" />
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={globalBusy}
            placeholder="Ask or describe changes — Yes on advisor replies applies them; /trip always runs the planner."
            className="flex-1 bg-transparent text-[13px] text-white placeholder:text-white/30 outline-none disabled:opacity-40"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || globalBusy}
            className="w-7 h-7 rounded-lg bg-indigo-500/20 text-indigo-400 hover:bg-indigo-500/30 disabled:opacity-30 disabled:cursor-not-allowed flex items-center justify-center transition-all"
          >
            <Send className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => onModeChange("minimized")}
            className="w-7 h-7 rounded-lg bg-white/[0.05] text-white/40 hover:text-white/60 hover:bg-white/[0.08] flex items-center justify-center transition-all"
          >
            <ChevronDown className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    );
  }

  // Render panel (full conversation thread) — enter/exit motion lives on parent in DestinationCanvas
  return (
    <div className="flex flex-col h-full min-h-0 border-l border-white/[0.06] bg-black/20">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06]">
        <div className="flex items-center gap-2">
          <MessageSquare className="w-4 h-4 text-indigo-400" />
          <h3 className="text-[13px] text-white/80">Conversation</h3>
          <span className="text-[11px] text-white/30">({conversation.length})</span>
        </div>
        <button
          onClick={() => onModeChange("footer")}
          className="w-6 h-6 rounded-lg bg-white/[0.05] text-white/40 hover:text-white/60 hover:bg-white/[0.08] flex items-center justify-center transition-all"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {conversation.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Sparkles className="w-8 h-8 text-white/20 mb-2" />
            <p className="text-[12px] text-white/40">Start a conversation</p>
            <p className="text-[11px] text-white/25 mt-1">Ask questions or refine the itinerary</p>
          </div>
        ) : (
          <>
            {conversation.map((msg) => (
              <motion.div
                key={msg.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[85%] rounded-lg px-3 py-2 ${
                    msg.role === "user"
                      ? "bg-indigo-500/20 text-white/90 border border-indigo-500/30"
                      : "bg-white/[0.05] text-white/80 border border-white/[0.06]"
                  }`}
                >
                  {msg.role === "agent" && (
                    <div className="flex items-center gap-1.5 mb-1">
                      <Sparkles className="w-3 h-3 text-indigo-400" />
                      <span className="text-[10px] text-white/40">Wanderly</span>
                    </div>
                  )}
                  {msg.role === "user" ? (
                    <p className="text-[12px] leading-relaxed whitespace-pre-wrap">{msg.text}</p>
                  ) : (
                    <AgentMessageBody text={msg.text} className="text-[12px] leading-relaxed text-white/80" />
                  )}
                  {msg.role === "agent" && msg.refinePrompt?.trim() && !msg.refineCtaResolved && !globalBusy && (
                    <div className="mt-2 pt-2 border-t border-white/[0.08] space-y-1.5">
                      <p className="text-[11px] text-white/45">Apply this to your itinerary?</p>
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => applyRefineFromAssistantMessage(msg)}
                          disabled={!sessionId?.trim() || destinations.length === 0}
                          className="px-2.5 py-1 rounded-md bg-indigo-500 text-[11px] text-white hover:bg-indigo-600 disabled:opacity-35 disabled:cursor-not-allowed"
                        >
                          Yes, update trip
                        </button>
                        <button
                          type="button"
                          onClick={() => resolveConversationRefineCta(destId, msg.id)}
                          className="px-2.5 py-1 rounded-md bg-white/[0.08] text-[11px] text-white/65 hover:bg-white/[0.12]"
                        >
                          Not now
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </motion.div>
            ))}
            {isTyping && (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex justify-start"
              >
                <div className="max-w-[85%] rounded-lg px-3 py-2 bg-white/[0.05] border border-white/[0.06]">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Sparkles className="w-3 h-3 text-indigo-400" />
                    <span className="text-[10px] text-white/40">Wanderly</span>
                  </div>
                  <div className="flex gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-white/40 animate-bounce" style={{ animationDelay: "0ms" }} />
                    <span className="w-1.5 h-1.5 rounded-full bg-white/40 animate-bounce" style={{ animationDelay: "150ms" }} />
                    <span className="w-1.5 h-1.5 rounded-full bg-white/40 animate-bounce" style={{ animationDelay: "300ms" }} />
                  </div>
                </div>
              </motion.div>
            )}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Input area */}
      <div className="border-t border-white/[0.06] px-4 py-3 bg-black/10">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={globalBusy}
            placeholder="Ask naturally; use Yes on an advisor reply to apply itinerary changes, or /trip … to always run the planner."
            rows={2}
            className="flex-1 bg-white/[0.05] border border-white/[0.08] rounded-lg px-3 py-2 text-[12px] text-white placeholder:text-white/30 outline-none resize-none focus:border-indigo-500/30 transition-colors disabled:opacity-40"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || globalBusy}
            className="w-9 h-9 rounded-lg bg-indigo-500 text-white hover:bg-indigo-600 disabled:opacity-30 disabled:cursor-not-allowed flex items-center justify-center transition-all shrink-0"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
        <p className="text-[10px] text-white/25 mt-2">Press Enter to send, Shift+Enter for new line</p>
      </div>
    </div>
  );
}

// Mock response generator
function generateMockResponse(userMessage: string, destName: string): string {
  const lower = userMessage.toLowerCase();

  // Itinerary / plan questions
  if (
    lower.includes("itinerary") ||
    lower.includes("schedule") ||
    lower.includes("day by day") ||
    lower.includes("what's planned") ||
    lower.includes("whats planned")
  ) {
    return `Here's the gist of your ${destName} plan: arrival day settles you in, mid-trip days mix highlights with buffer time, and the last day is lighter for travel. Tell me a day number or theme (food, rest, adventure) and I can suggest tweaks — or open the full thread to walk through each day.`;
  }

  // Weather & Climate
  if (lower.includes("weather") || lower.includes("temperature") || lower.includes("climate") || lower.includes("rain")) {
    return `${destName} has a tropical climate with warm temperatures year-round. The best time to visit is typically during the dry season. I can adjust the itinerary to avoid rainy periods if you'd like.`;
  }

  // Food & Dining
  if (lower.includes("food") || lower.includes("restaurant") || lower.includes("eat") || lower.includes("dining")) {
    return `Great question! ${destName} has an incredible food scene. I've included some must-try local dishes in your itinerary. Would you like me to add more specific restaurant recommendations or food tours?`;
  }

  // Budget & Cost
  if (lower.includes("budget") || lower.includes("cost") || lower.includes("price") || lower.includes("cheaper") || lower.includes("expensive")) {
    return `I can help adjust the budget! There are several ways to reduce costs: choosing different accommodation, adjusting activities, or modifying travel dates. Which aspect would you like me to focus on?`;
  }

  // Hotels & Accommodation
  if (lower.includes("hotel") || lower.includes("accommodation") || lower.includes("stay") || lower.includes("where to sleep")) {
    return `I've selected hotels that balance location, quality, and value. Would you like me to suggest more affordable options, upgrade to luxury properties, or focus on a different neighborhood?`;
  }

  // Day-specific changes
  if (lower.match(/day\s*\d+/) || lower.includes("first day") || lower.includes("last day")) {
    const dayMatch = lower.match(/day\s*(\d+)/);
    const dayNum = dayMatch ? dayMatch[1] : "that";
    return `I can definitely modify Day ${dayNum}! What would you like to change? I can swap activities, adjust the pace, add more free time, or completely redesign the day based on your preferences.`;
  }

  // Activity changes
  if (lower.includes("activity") || lower.includes("activities") || lower.includes("thing to do")) {
    return `I can adjust the activities to better match your interests. Would you like more cultural experiences, outdoor adventures, relaxation time, or a different balance? I can also add or remove specific activities.`;
  }

  // Pace & Timing
  if (lower.includes("pace") || lower.includes("rushed") || lower.includes("slow") || lower.includes("relax") || lower.includes("free time")) {
    return `I can adjust the pacing! Would you like me to add more downtime, reduce the number of activities per day, or spread things out differently? Many travelers prefer a more relaxed pace with built-in flexibility.`;
  }

  // General changes
  if (lower.includes("change") || lower.includes("modify") || lower.includes("different") || lower.includes("instead") || lower.includes("swap")) {
    return `I'd be happy to modify the itinerary! Can you tell me specifically what you'd like to change? For example, swap a day activity, change hotels, adjust the pace, or focus on different experiences?`;
  }

  // Transportation
  if (lower.includes("transport") || lower.includes("getting around") || lower.includes("how to get") || lower.includes("travel between")) {
    return `For getting around ${destName}, I recommend a mix of local transport and private transfers depending on the day. I can add detailed transportation notes to each day of your itinerary if that would be helpful.`;
  }

  // Family-friendly
  if (lower.includes("kid") || lower.includes("child") || lower.includes("family") || lower.includes("toddler") || lower.includes("baby")) {
    return `I can definitely adapt this itinerary for families! I'll adjust activities to be more kid-friendly and suggest accommodation with family amenities. How old are the children traveling?`;
  }

  // Neighborhoods & Areas
  if (lower.includes("neighborhood") || lower.includes("area") || lower.includes("district") || lower.includes("where to stay")) {
    return `${destName} has several distinct neighborhoods, each with its own character. Based on your vibe, I've chosen areas that balance convenience, atmosphere, and local flavor. Want me to explain the different areas or suggest alternatives?`;
  }

  // Culture & History
  if (lower.includes("culture") || lower.includes("history") || lower.includes("tradition") || lower.includes("local")) {
    return `${destName} has a rich cultural heritage! I've woven in some authentic experiences, but I can add more temples, museums, traditional ceremonies, or local interactions if that's what you're after.`;
  }

  // Default response
  return `That's a great point about ${destName}! Let me look into that for you. Based on your current itinerary, I'd suggest keeping an eye on timing for Day 3 and Day 5. Would you like me to make any specific adjustments?`;
}
