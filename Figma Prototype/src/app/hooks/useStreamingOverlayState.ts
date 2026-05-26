import { useCallback, useRef, useState } from "react";
import { mergeStreamCaptionLog } from "../lib/cardStreamLog";

const SHOW_AFTER_MS = 2000;
const DEFAULT_CLOSE_DELAY_MS = 1000;

export type StreamingOverlayView = {
  isVisible: boolean;
  title: string;
  subtitle: string;
  streamText: string;
  isStreaming: boolean;
};

export type StreamingOverlayOpenOpts = {
  title: string;
  subtitle?: string;
  /** When true, keep existing log text (e.g. chained operations). */
  preserveLog?: boolean;
};

/**
 * Shared overlay state: delayed show, caption log merge, graceful close.
 * Card and workspace overlays register sinks that call these handlers.
 */
export function useStreamingOverlayState(defaultSubtitle: string) {
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

  const open = useCallback(
    (opts: StreamingOverlayOpenOpts) => {
      setTitle(opts.title);
      setSubtitle(opts.subtitle ?? defaultSubtitle);
      setStreamText((prev) => {
        if (prev.length > 60) setDisplayVisible(true);
        return opts.preserveLog ? prev : "";
      });
      markBusy();
    },
    [defaultSubtitle, markBusy],
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

  const overlay: StreamingOverlayView = {
    isVisible: displayVisible,
    title,
    subtitle,
    streamText,
    isStreaming,
  };

  return { overlay, open, push, close, clearShowTimer, clearCloseTimer };
}
