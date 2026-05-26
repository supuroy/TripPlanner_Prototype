/**
 * Workspace (multi-card grid) streaming overlay — CommandBar refine/smoke and TripStore
 * captions feed here when no destination card is expanded.
 */

import type { StreamingOverlayOpenOpts } from "../hooks/useStreamingOverlayState";

type WorkspaceStreamOverlaySink = {
  open: (opts: StreamingOverlayOpenOpts) => void;
  push: (caption: string) => void;
  close: (delayMs?: number) => void;
};

let sink: WorkspaceStreamOverlaySink | null = null;

export function registerWorkspaceStreamOverlaySink(next: WorkspaceStreamOverlaySink | null): void {
  sink = next;
}

export function workspaceStreamOpen(opts: StreamingOverlayOpenOpts): void {
  sink?.open(opts);
}

export function workspaceStreamPush(caption: string): void {
  const c = caption.trim();
  if (!c) return;
  sink?.push(c);
}

export function workspaceStreamClose(delayMs = 1000): void {
  sink?.close(delayMs);
}
