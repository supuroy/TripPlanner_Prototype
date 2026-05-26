import { useCallback, useEffect } from "react";
import {
  registerCardStreamOverlaySink,
  setCardStreamOverlayArmed,
  type CardStreamOpenOpts,
} from "../lib/cardStreamOverlayBridge";
import { useStreamingOverlayState } from "./useStreamingOverlayState";

export type CardStreamingOverlayView = {
  isVisible: boolean;
  title: string;
  subtitle: string;
  streamText: string;
  isStreaming: boolean;
};

/**
 * Expanded-card overlay: registers a per-card sink, shows after ~2s of busy work,
 * accumulates captions from planner SSE and local itinerary streams.
 */
export function useCardStreamingOverlay(destId: string, destName: string) {
  const { overlay, open, push, close, clearShowTimer, clearCloseTimer } = useStreamingOverlayState(
    `Working on ${destName}…`,
  );

  const openArmed = useCallback(
    (opts: CardStreamOpenOpts) => {
      setCardStreamOverlayArmed(true);
      open(opts);
    },
    [open],
  );

  const closeArmed = useCallback(
    (delayMs?: number) => {
      setCardStreamOverlayArmed(false);
      close(delayMs);
    },
    [close],
  );

  useEffect(() => {
    registerCardStreamOverlaySink({
      destId,
      open: openArmed,
      push,
      close: closeArmed,
    });
    return () => {
      registerCardStreamOverlaySink(null);
      setCardStreamOverlayArmed(false);
      clearShowTimer();
      clearCloseTimer();
    };
  }, [destId, openArmed, push, closeArmed, clearShowTimer, clearCloseTimer]);

  return { overlay, open: openArmed, push, close: closeArmed };
}
