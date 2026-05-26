import { cardStreamPush } from "./cardStreamOverlayBridge";
import { isRotatingWaitCaption, streamEventToCaption, type StructuredStreamMode } from "./streamCaption";

export type CardStreamFromSseOpts = {
  destId: string;
  structuredStreamMode?: StructuredStreamMode;
  /** Called on first non-spinner caption (e.g. stop wait-spinner interval). */
  onLiveCaption?: () => void;
};

/**
 * SSE handler that maps Cursor events to overlay log lines for an expanded destination card.
 */
export function createCardStreamFromSseHandler(opts: CardStreamFromSseOpts) {
  const assistantDraft = { text: "" };
  const thinkingDraft = { text: "" };
  let sawLive = false;

  return (ev: Record<string, unknown>) => {
    const cap = streamEventToCaption(ev, assistantDraft, thinkingDraft, {
      structuredStreamMode: opts.structuredStreamMode,
      overlay: true,
    });
    if (!cap) return;
    if (!isRotatingWaitCaption(cap)) {
      if (!sawLive) {
        sawLive = true;
        opts.onLiveCaption?.();
      }
    }
    cardStreamPush(cap, opts.destId);
  };
}
