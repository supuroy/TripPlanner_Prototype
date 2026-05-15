import { useTrip, type AgentResponse } from "./TripStore";
import { useRef, useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Bot, User, Lightbulb, AlertTriangle, CheckCircle, Info, X, Play, RotateCcw, Loader2 } from "lucide-react";
import { renderInlineBold } from "../lib/markdownBold";

/** Advisor plan-review tips use this prefix; payload is inserted into the main bar when clicked. */
function tryAskingPromptFromTip(text: string): string | null {
  const m = text.trim().match(/^Try asking:\s*(.+)$/is);
  return m?.[1]?.trim() ? m[1].trim() : null;
}

function TryAskingTipBody({ text, onUsePrompt }: { text: string; onUsePrompt: (prompt: string) => void }) {
  const payload = tryAskingPromptFromTip(text);
  if (!payload) {
    return (
      <div className="text-white/80 text-[13px] leading-relaxed space-y-1">
        {text.split("\n").map((line, i) => (
          <span key={i} className="block">
            {renderInlineBold(line)}
          </span>
        ))}
      </div>
    );
  }
  return (
    <div className="text-[13px] leading-relaxed">
      <span className="text-white/45">Try asking: </span>
      <button
        type="button"
        onClick={() => onUsePrompt(payload)}
        className="inline max-w-none cursor-pointer border-0 bg-transparent p-0 text-left font-sans text-[13px] font-normal leading-relaxed text-indigo-300 underline decoration-dotted decoration-indigo-500/35 underline-offset-2 hover:text-indigo-200 hover:decoration-indigo-400/60 hover:decoration-solid"
        aria-label="Insert this prompt into the chat bar below"
      >
        {renderInlineBold(payload, "font-semibold text-indigo-200")}
      </button>
      <p className="mt-2 text-[10px] text-white/35">Click the suggestion to copy it into the input below. Edit before sending if you like.</p>
    </div>
  );
}

// Markdown parser - simple implementation
function parseMarkdown(text: string) {
  const lines = text.split("\n");
  const elements: JSX.Element[] = [];
  
  lines.forEach((line, idx) => {
    // H3 headers
    if (line.startsWith("### ")) {
      elements.push(
        <h3 key={idx} className="text-[15px] text-white/90 mt-4 mb-2">
          {renderInlineBold(line.replace("### ", ""))}
        </h3>
      );
    }
    // H2 headers
    else if (line.startsWith("## ")) {
      elements.push(
        <h2 key={idx} className="text-[17px] text-white/95 mt-5 mb-3">
          {renderInlineBold(line.replace("## ", ""))}
        </h2>
      );
    }
    // Horizontal rules
    else if (line.trim() === "---") {
      elements.push(
        <hr key={idx} className="border-white/[0.08] my-3" />
      );
    }
    // Bullet points
    else if (line.startsWith("- ")) {
      const content = line.replace("- ", "");
      elements.push(
        <div key={idx} className="flex gap-2 mb-1">
          <span className="text-white/30 mt-0.5">•</span>
          <span className="text-white/70 text-[13px] flex-1">{renderInlineBold(content)}</span>
        </div>
      );
    }
    // Regular paragraph
    else if (line.trim()) {
      elements.push(
        <p key={idx} className="text-white/70 text-[13px] mb-2 leading-relaxed">
          {renderInlineBold(line)}
        </p>
      );
    }
    // Empty line - just spacing
    else {
      elements.push(<div key={idx} className="h-2" />);
    }
  });
  
  return elements;
}

/** Shimmer blocks while the main planner / refine stream has not yet returned workspace JSON. */
function AgentPlanningSkeleton() {
  return (
    <div className="space-y-3 py-1" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="animate-pulse rounded-xl border border-white/[0.06] bg-white/[0.02] p-4"
          style={{ animationDelay: `${i * 120}ms` }}
        >
          <div className="mb-3 h-3 w-40 rounded bg-white/15" />
          <div className="mb-2 h-2.5 w-full rounded bg-white/[0.07]" />
          <div className="mb-2 h-2.5 w-[92%] rounded bg-white/[0.06]" />
          <div className="h-2.5 w-[70%] rounded bg-white/[0.05]" />
        </div>
      ))}
    </div>
  );
}

function ResponseCard({ response }: { response: AgentResponse }) {
  const { dismissResponse, performUndo, undoStack, setComposerPrefill } = useTrip();
  const [isExpanded, setIsExpanded] = useState(false);
  
  // Determine styling based on type
  const getTypeConfig = () => {
    switch (response.type) {
      case "warning":
        return {
          icon: AlertTriangle,
          bgColor: "bg-amber-500/10",
          borderColor: "border-amber-500/20",
          iconColor: "text-amber-400",
        };
      case "success":
        return {
          icon: CheckCircle,
          bgColor: "bg-emerald-500/10",
          borderColor: "border-emerald-500/20",
          iconColor: "text-emerald-400",
        };
      case "tip":
      case "insight":
        return {
          icon: Lightbulb,
          bgColor: "bg-indigo-500/10",
          borderColor: "border-indigo-500/20",
          iconColor: "text-indigo-400",
        };
      case "destination_detail":
      case "comparison":
        return {
          icon: Info,
          bgColor: "bg-purple-500/10",
          borderColor: "border-purple-500/20",
          iconColor: "text-purple-400",
        };
      case "info":
        return {
          icon: Info,
          bgColor: "bg-blue-500/10",
          borderColor: "border-blue-500/20",
          iconColor: "text-blue-400",
        };
      case "message":
        return null; // Messages use different styling
    }
  };
  
  // Handle message type separately
  if (response.type === "message") {
    const isUser = response.role === "user";
    
    return (
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className={`flex ${isUser ? "justify-end" : "justify-start"} mb-3`}
      >
        <div className={`flex gap-3 max-w-[80%] ${isUser ? "flex-row-reverse" : ""}`}>
          {!isUser && (
            <div className="w-7 h-7 rounded-full bg-indigo-500/20 flex items-center justify-center flex-shrink-0">
              <Bot className="w-4 h-4 text-indigo-400" />
            </div>
          )}
          
          <div className={`rounded-2xl px-4 py-2.5 ${
            isUser
              ? "bg-white/[0.08] text-white/80"
              : "bg-[#1a1a2e] text-white/70 border border-white/[0.06]"
          }`}>
            <div className="text-[13px] leading-relaxed">
              {response.text.split("\n").map((line, i) => (
                <span key={i} className="block whitespace-pre-wrap">
                  {isUser ? line : renderInlineBold(line)}
                </span>
              ))}
            </div>
            
            {/* Undo button for agent messages with undoId */}
            {!isUser && response.undoId && (
              <button
                onClick={() => performUndo(response.undoId!)}
                className="flex items-center gap-1.5 mt-2 text-[11px] text-amber-400/70 hover:text-amber-400 transition-colors"
              >
                <RotateCcw className="w-3 h-3" />
                Undo this change
              </button>
            )}
          </div>
          
          {isUser && (
            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center flex-shrink-0 text-[11px] text-white">
              J
            </div>
          )}
        </div>
      </motion.div>
    );
  }
  
  // Handle insight/warning/success/tip/comparison types
  const config = getTypeConfig();
  if (!config) return null;
  
  const Icon = config.icon;
  const isComparison = response.type === "comparison";
  const isDestinationDetail = response.type === "destination_detail";
  const isExpandable = isComparison || isDestinationDetail;
  
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className={`rounded-xl p-4 mb-4 ${config.bgColor} border ${config.borderColor} relative`}
    >
      {/* Close button */}
      <button
        onClick={() => dismissResponse(response.id)}
        className="absolute top-3 right-3 text-white/30 hover:text-white/60 transition-colors"
      >
        <X className="w-4 h-4" />
      </button>
      
      {/* Header */}
      <div className="flex items-start gap-3 mb-3">
        <Icon className={`w-5 h-5 ${config.iconColor} flex-shrink-0 mt-0.5`} />
        <div className="flex-1 pr-6">
          {isExpandable ? (
            <div>
              {/* Show preview or full content */}
              <div className={isExpanded ? "" : "max-h-[200px] overflow-hidden relative"}>
                {parseMarkdown(response.text)}
                
                {/* Fade overlay when collapsed */}
                {!isExpanded && (
                  <div className="absolute bottom-0 left-0 right-0 h-20 bg-gradient-to-t from-purple-500/10 to-transparent pointer-events-none" />
                )}
              </div>
              
              {/* Expand/Collapse button */}
              <button
                onClick={() => setIsExpanded(!isExpanded)}
                className="mt-3 text-[12px] text-purple-400 hover:text-purple-300 transition-colors flex items-center gap-1.5"
              >
                {isExpanded ? (
                  <>
                    <span>Show less</span>
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
                    </svg>
                  </>
                ) : (
                  <>
                    <span>{isDestinationDetail ? `Show full details` : `Show full comparison`}</span>
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </>
                )}
              </button>
            </div>
          ) : response.type === "tip" && tryAskingPromptFromTip(response.text) ? (
            <TryAskingTipBody text={response.text} onUsePrompt={(p) => setComposerPrefill(p)} />
          ) : (
            <div className="text-white/80 text-[13px] leading-relaxed space-y-1">
              {response.text.split("\n").map((line, i) => (
                <span key={i} className="block">
                  {renderInlineBold(line)}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
      
      {/* Suggested Actions */}
      {response.actions && response.actions.length > 0 && (
        <div className="mt-4 pt-4 border-t border-white/[0.08]">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-1 h-1 rounded-full bg-indigo-400" />
            <span className="text-[10px] text-white/40 uppercase tracking-wider">
              Suggested Actions
            </span>
          </div>
          
          <div className="space-y-2">
            {response.actions.map((action) => (
              <button
                key={action.id}
                className="w-full flex items-center gap-3 p-3 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.06] hover:border-white/[0.1] transition-all group"
              >
                <span className="text-lg">{action.icon}</span>
                <div className="flex-1 text-left">
                  <div className="text-[13px] text-white/90 mb-0.5">{action.label}</div>
                  <div className="text-[11px] text-white/40">{action.description}</div>
                </div>
                <Play className="w-4 h-4 text-indigo-400/60 group-hover:text-indigo-400 transition-colors" />
              </button>
            ))}
          </div>
        </div>
      )}
      
      {/* Undo button if applicable */}
      {response.undoId && (
        <button
          onClick={() => performUndo(response.undoId!)}
          className="flex items-center gap-1.5 mt-3 text-[11px] text-amber-400/70 hover:text-amber-400 transition-colors"
        >
          <RotateCcw className="w-3 h-3" />
          Undo this change
        </button>
      )}
    </motion.div>
  );
}

export function AgentResponses() {
  const {
    responses,
    phase,
    agentStatus,
    pendingAgentProfileProposal,
    confirmAgentProfileProposal,
    dismissAgentProfileProposal,
  } = useTrip();
  const scrollRef = useRef<HTMLDivElement>(null);
  const isWorking =
    phase === "thinking" || phase === "streaming" || phase === "refining" || phase === "advising";

  // Auto-scroll to bottom when a new agent response is appended (conversation grows).
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [responses.length]);
  
  // Filter out dismissed responses
  const activeResponses = responses.filter(r => !r.dismissed);
  
  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto bg-[#0a0a0f]/50 px-4 py-4 sm:px-6 sm:py-6">
      {activeResponses.length === 0 && isWorking ? (
        <div className="flex min-h-[min(60vh,420px)] flex-col">
          <div className="mb-3 flex items-start gap-2.5">
            <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-indigo-400" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="mb-1 text-[10px] uppercase tracking-wider text-white/30">Live progress</p>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.03] px-3 py-2.5">
                <p className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-white/70">
                  {agentStatus.trim() || "Trip planner is running…"}
                </p>
                <p className="mt-2 text-[11px] leading-snug text-white/35">
                  Detailed status rotates in the trip bar input below.
                </p>
              </div>
            </div>
          </div>
          <p className="mb-4 text-[12px] text-white/25">
            Hang tight—the first shortlist can take a little while.
          </p>
          <AgentPlanningSkeleton />
        </div>
      ) : activeResponses.length === 0 ? (
        <div className="flex h-full items-center justify-center">
          <div className="text-center">
            <Bot className="mx-auto mb-3 h-8 w-8 text-white/10" />
            <p className="text-[13px] text-white/20">The agent will respond here…</p>
          </div>
        </div>
      ) : (
        <>
          {pendingAgentProfileProposal && (
            <div
              className="mb-4 rounded-xl border border-amber-500/25 bg-amber-500/[0.07] p-4"
              role="region"
              aria-label="Pending traveler profile changes from the planner"
            >
              <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-amber-200/90">
                Confirm traveler profile
              </p>
              <p className="mb-3 text-[12px] leading-relaxed text-white/65">
                The planner suggested different profile values than your sidebar. Destination cards already used your
                current profile. Apply only if you agree with the planner&apos;s version.
              </p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => confirmAgentProfileProposal()}
                  className="rounded-lg bg-amber-500/90 px-3 py-2 text-[12px] font-medium text-zinc-950 hover:bg-amber-400"
                >
                  Apply suggested profile
                </button>
                <button
                  type="button"
                  onClick={() => dismissAgentProfileProposal()}
                  className="rounded-lg border border-white/15 bg-white/[0.04] px-3 py-2 text-[12px] text-white/80 hover:bg-white/[0.07]"
                >
                  Keep my profile
                </button>
              </div>
            </div>
          )}
          <AnimatePresence>
            {activeResponses.map((response) => (
              <ResponseCard key={response.id} response={response} />
            ))}
          </AnimatePresence>
          {isWorking && (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="mt-4 rounded-xl border border-indigo-500/20 bg-indigo-500/[0.05] px-4 py-3"
            >
              <div className="mb-2 flex items-center gap-2 text-[12px] text-indigo-200/85">
                <Loader2 className="h-4 w-4 shrink-0 animate-spin text-indigo-400" aria-hidden />
                <span>{agentStatus || "Still working…"}</span>
              </div>
              <AgentPlanningSkeleton />
            </motion.div>
          )}
        </>
      )}
    </div>
  );
}