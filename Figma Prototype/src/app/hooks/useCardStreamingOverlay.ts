import { useCallback, useEffect, useRef, useState } from "react";
import { mergeStreamCaptionLog } from "../lib/cardStreamLog";
import {
  registerCardStreamOverlaySink,
  type CardStreamOpenOpts,
} from "../lib/cardStreamOverlayBridge";

const SHOW_AFTER_MS = 2000;
const DEFAULT_CLOSE_DELAY_MS = 1000;

export type CardStreamingOverlayView = {
  isVisible: boolean;
  title: string;
  subtitle: string;
  streamText: string;
  isStreaming: boolean;
};

/**
 * Expanded-card overlay: registers a global sink, shows after ~2s of busy work,
 * accumulates captions from planner SSE and local itinerary streams.
 */
export function useCardStreamingOverlay(destId: string, destName: string) {
  const [title, setTitle] = useState("AI agent working");
  const [subtitle, setSubtitle] = useState("");
  const [streamText, setStreamText] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [displayVisible, setDisplayVisible] = useState(false);

  const busyCountRef = useRef(0);
  const showTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearShowTimer = useCallback(() => {
    if (showTimerRef.current) {
      clearTimeout(showTimerRef.current);
      showTimerRef.current = null;
    }
  }, []);

  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const markBusy = useCallback(() => {
    busyCountRef.current += 1;
    setIsStreaming(true);
    clearCloseTimer();
    if (busyCountRef.current === 1) {
      clearShowTimer();
      showTimerRef.current = setTimeout(() => {
        setDisplayVisible(true);
        showTimerRef.current = null;
      }, SHOW_AFTER_MS);
    }
  }, [clearCloseTimer, clearShowTimer]);

  const markIdle = useCallback((delayMs = DEFAULT_CLOSE_DELAY_MS) => {
    busyCountRef.current = Math.max(0, busyCountRef.current - 1);
    if (busyCountRef.current > 0) return;
    setIsStreaming(false);
    clearShowTimer();
    clearCloseTimer();
    closeTimerRef.current = setTimeout(() => {
      setDisplayVisible(false);
      setStreamText("");
      setSubtitle("");
      closeTimerRef.current = null;
    }, delayMs);
  }, [clearCloseTimer, clearShowTimer]);

  const open = useCallback(
    (opts: CardStreamOpenOpts) => {
      setTitle(opts.title);
      setSubtitle(opts.subtitle ?? `Working on ${destName}…`);
      if (!opts.preserveLog) setStreamText("");
      markBusy();
      if (streamText.length > 60) setDisplayVisible(true);
    },
    [destName, markBusy, streamText.length],
  );

  const push = useCallback((caption: string) => {
    const c = caption.trim();
    if (!c) return;
    setStreamText((prev) => mergeStreamCaptionLog(prev, c));
    if (c.length > 48) setDisplayVisible(true);
  }, []);

  const close = useCallback(
    (delayMs = DEFAULT_CLOSE_DELAY_MS) => {
      busyCountRef.current = 0;
      setIsStreaming(false);
      clearShowTimer();
      clearCloseTimer();
      closeTimerRef.current = setTimeout(() => {
        setDisplayVisible(false);
        setStreamText("");
        setSubtitle("");
        closeTimerRef.current = null;
      }, delayMs);
    },
    [clearCloseTimer, clearShowTimer],
  );

  useEffect(() => {
    registerCardStreamOverlaySink({ destId, open, push, close });
    return () => {
      registerCardStreamOverlaySink(null);
      clearShowTimer();
      clearCloseTimer();
      busyCountRef.current = 0;
    };
  }, [destId, open, push, close, clearShowTimer, clearCloseTimer]);

  const overlay: CardStreamingOverlayView = {
    isVisible: displayVisible,
    title,
    subtitle,
    streamText,
    isStreaming,
  };

  return { overlay, open, push, close, markBusy, markIdle };
}
