import { useEffect } from "react";
import { registerWorkspaceStreamOverlaySink } from "../lib/workspaceStreamOverlayBridge";
import { useStreamingOverlayState } from "./useStreamingOverlayState";

/**
 * Multi-card grid overlay: registers a workspace sink for planner SSE / refine streams.
 */
export function useWorkspaceStreamingOverlay() {
  const { overlay, open, push, close, clearShowTimer, clearCloseTimer } = useStreamingOverlayState(
    "Updating your destinations…",
  );

  useEffect(() => {
    registerWorkspaceStreamOverlaySink({ open, push, close });
    return () => {
      registerWorkspaceStreamOverlaySink(null);
      clearShowTimer();
      clearCloseTimer();
    };
  }, [open, push, close, clearShowTimer, clearCloseTimer]);

  return { overlay };
}
