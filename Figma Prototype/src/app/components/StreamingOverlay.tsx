import { AnimatePresence, motion } from "motion/react";
import { Sparkles } from "lucide-react";
import { StreamingText } from "./StreamingText";

export interface StreamingOverlayProps {
  isVisible: boolean;
  title: string;
  subtitle?: string;
  streamText: string;
  isStreaming: boolean;
  onCancel?: () => void;
  "data-testid"?: string;
}

export function StreamingOverlay({
  isVisible,
  title,
  subtitle,
  streamText,
  isStreaming,
  onCancel,
  "data-testid": dataTestId,
}: StreamingOverlayProps) {
  return (
    <AnimatePresence>
      {isVisible ? (
        <motion.div
          data-testid={dataTestId}
          role="dialog"
          aria-modal="true"
          aria-labelledby="wanderly-stream-title"
          aria-describedby="wanderly-stream-content"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="absolute inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-md sm:p-6"
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ duration: 0.3, type: "spring", damping: 25 }}
            className="flex max-h-[min(85vh,720px)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-white/[0.12] bg-[#0a0a0f]/95 shadow-2xl"
          >
            <div className="flex items-center gap-3 border-b border-white/[0.08] px-5 pt-5 pb-4 sm:px-6">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-indigo-500/30 bg-gradient-to-br from-indigo-500/20 to-purple-500/20">
                <Sparkles className="h-5 w-5 animate-pulse text-indigo-400" aria-hidden />
              </div>
              <div className="min-w-0 flex-1">
                <h3 id="wanderly-stream-title" className="text-[15px] font-medium text-white/90">
                  {title}
                </h3>
                {subtitle ? <p className="text-[11px] text-white/40">{subtitle}</p> : null}
              </div>
              {isStreaming ? (
                <div className="flex shrink-0 items-center gap-2 rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-1">
                  <div className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" aria-hidden />
                  <span className="text-[11px] text-emerald-300">Thinking</span>
                </div>
              ) : null}
              {onCancel ? (
                <button
                  type="button"
                  onClick={onCancel}
                  className="shrink-0 text-[11px] text-white/40 transition-colors hover:text-white/70"
                >
                  Cancel
                </button>
              ) : null}
            </div>

            <div
              id="wanderly-stream-content"
              role="log"
              aria-live="polite"
              aria-atomic="false"
              className="min-h-0 flex-1 overflow-hidden px-5 py-4 sm:px-6"
            >
              {streamText.trim() ? (
                <StreamingText text={streamText} showCursor={isStreaming} />
              ) : (
                <p className="text-[13px] text-white/45 font-mono leading-relaxed">
                  Connecting to the travel agent…
                  {isStreaming ? (
                    <motion.span
                      animate={{ opacity: [1, 0] }}
                      transition={{ duration: 0.5, repeat: Infinity }}
                      className="inline-block w-[2px] h-[14px] bg-indigo-400 ml-0.5 align-middle"
                      aria-hidden
                    />
                  ) : null}
                </p>
              )}
            </div>

            <div className="border-t border-white/[0.06] bg-white/[0.02] px-5 py-3 sm:px-6">
              <p className="text-center text-[11px] text-white/30">
                {isStreaming ? "Streaming live from the AI model…" : "Finishing up…"}
              </p>
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
