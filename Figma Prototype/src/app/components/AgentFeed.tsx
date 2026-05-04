import { useState } from "react";
import { useTrip } from "./TripStore";
import { motion, AnimatePresence } from "motion/react";
import { Bot, User, ChevronDown, ChevronUp, MessageSquare, Undo2, CheckCircle } from "lucide-react";
import { renderInlineBold } from "../lib/markdownBold";

/**
 * AgentFeed v3 — Contextual + Undo-aware.
 * Shows the latest agent message prominently with undo capability.
 * Action results include a one-click undo button.
 * Previous messages are collapsed and accessible via toggle.
 */
export function AgentFeed() {
  const { messages, undoStack, performUndo } = useTrip();
  const [showHistory, setShowHistory] = useState(false);
  const [undoneIds, setUndoneIds] = useState<Set<string>>(new Set());

  if (messages.length === 0) return null;

  // Split messages: latest 2 are "current", rest are history
  const agentMessages = messages.filter(m => m.role === "agent");
  const latestAgent = agentMessages.length > 0 ? agentMessages[agentMessages.length - 1] : null;
  const olderMessages = messages.slice(0, Math.max(0, messages.length - 2));

  const handleUndo = (undoId: string) => {
    // Find the matching undo entry (most recent one)
    const entry = undoStack[undoStack.length - 1];
    if (entry) {
      performUndo(entry.id);
      setUndoneIds(prev => new Set(prev).add(undoId));
    }
  };

  return (
    <div className="mt-4">
      {/* Latest agent message — always visible, prominent */}
      <AnimatePresence mode="wait">
        {latestAgent && (
          <motion.div
            key={latestAgent.id}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex gap-3 items-start"
          >
            <div className="w-7 h-7 rounded-lg bg-indigo-500/20 border border-indigo-500/20 flex items-center justify-center shrink-0 mt-0.5">
              <Bot className="w-3.5 h-3.5 text-indigo-400" />
            </div>
            <div className="flex-1 min-w-0">
              {/* Message content — renders newlines as real line breaks */}
              <div className="px-4 py-3 rounded-2xl rounded-bl-md bg-white/[0.04] border border-white/[0.06]">
                {latestAgent.text.split("\n").map((line, i) => {
                  const trimmed = line.trimStart();
                  if (trimmed.startsWith("- ")) {
                    return (
                      <div key={i} className="flex gap-2 mb-0.5">
                        <span className="text-white/30 shrink-0">•</span>
                        <span className="text-[13px] text-white/60 leading-relaxed flex-1 min-w-0">
                          {renderInlineBold(trimmed.slice(2))}
                        </span>
                      </div>
                    );
                  }
                  if (line === "") return <span key={i} className="block h-1.5" />;
                  return (
                    <span key={i} className="block text-[13px] text-white/60 leading-relaxed">
                      {renderInlineBold(line)}
                    </span>
                  );
                })}

                {/* Undo button for action results */}
                {latestAgent.undoId && !undoneIds.has(latestAgent.undoId) && undoStack.length > 0 && (
                  <motion.div
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.3 }}
                    className="mt-3 pt-3 border-t border-white/[0.06]"
                  >
                    <button
                      onClick={() => handleUndo(latestAgent.undoId!)}
                      className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/[0.06] border border-white/[0.08] hover:bg-white/[0.1] hover:border-white/[0.15] transition-all group"
                    >
                      <Undo2 className="w-3.5 h-3.5 text-white/30 group-hover:text-amber-400 transition-colors" />
                      <span className="text-[12px] text-white/40 group-hover:text-white/60 transition-colors">Undo this change</span>
                    </button>
                  </motion.div>
                )}

                {/* Already undone */}
                {latestAgent.undoId && undoneIds.has(latestAgent.undoId) && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className="mt-3 pt-3 border-t border-white/[0.06] flex items-center gap-2"
                  >
                    <CheckCircle className="w-3.5 h-3.5 text-emerald-400/50" />
                    <span className="text-[12px] text-white/25 italic">This change was undone</span>
                  </motion.div>
                )}
              </div>

              {/* History toggle */}
              {olderMessages.length > 0 && (
                <button
                  onClick={() => setShowHistory(!showHistory)}
                  className="flex items-center gap-1.5 mt-2 ml-1 text-[11px] text-white/20 hover:text-white/40 transition-colors"
                >
                  <MessageSquare className="w-3 h-3" />
                  <span>{showHistory ? "Hide" : "Show"} {olderMessages.length} earlier message{olderMessages.length > 1 ? "s" : ""}</span>
                  {showHistory ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                </button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Collapsed history */}
      <AnimatePresence>
        {showHistory && olderMessages.length > 0 && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mt-3 ml-10 space-y-2 overflow-hidden"
          >
            {olderMessages.map((msg) => (
              <motion.div
                key={msg.id}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className={`flex gap-2 items-start ${msg.role === "user" ? "justify-end" : ""}`}
              >
                {msg.role === "agent" && (
                  <div className="w-5 h-5 rounded-md bg-indigo-500/10 flex items-center justify-center shrink-0 mt-0.5">
                    <Bot className="w-2.5 h-2.5 text-indigo-400/50" />
                  </div>
                )}
                <div
                  className={`px-3 py-1.5 rounded-xl text-[11px] leading-relaxed max-w-lg ${
                    msg.role === "user"
                      ? "bg-white/[0.04] text-white/30"
                      : "bg-white/[0.02] border border-white/[0.04] text-white/30"
                  }`}
                >
                  {msg.text.split("\n").filter(l => l.trim()).slice(0, 2).join(" ").slice(0, 120)}
                  {msg.text.length > 120 && "..."}
                  {/* Mini undo for older action messages */}
                  {msg.undoId && !undoneIds.has(msg.undoId) && (
                    <span className="text-[10px] text-white/15 ml-1 italic">(had changes)</span>
                  )}
                  {msg.undoId && undoneIds.has(msg.undoId) && (
                    <span className="text-[10px] text-emerald-400/30 ml-1 italic">(undone)</span>
                  )}
                </div>
                {msg.role === "user" && (
                  <div className="w-5 h-5 rounded-md bg-white/[0.04] flex items-center justify-center shrink-0 mt-0.5">
                    <User className="w-2.5 h-2.5 text-white/20" />
                  </div>
                )}
              </motion.div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
