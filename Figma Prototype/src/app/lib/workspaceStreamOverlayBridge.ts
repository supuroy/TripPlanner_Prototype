/**
 * Workspace (multi-card grid) streaming overlay — CommandBar refine/smoke and TripStore
 * captions feed here when no destination card is expanded.
 *
 * Queues open/push until a sink registers (first-trip smoke runs before DestinationCanvas mounts).
 */

import type { StreamingOverlayOpenOpts } from "../hooks/useStreamingOverlayState";

type WorkspaceStreamOverlaySink = {
  open: (opts: StreamingOverlayOpenOpts) => void;
  push: (caption: string) => void;
  close: (delayMs?: number) => void;
};

let sink: WorkspaceStreamOverlaySink | null = null;
let pendingOpen: StreamingOverlayOpenOpts | null = null;
const pendingPushes: string[] = [];

function flushPending(): void {
  if (!sink || !pendingOpen) return;
  sink.open(pendingOpen);
  pendingOpen = null;
  for (const c of pendingPushes) sink.push(c);
  pendingPushes.length = 0;
}

function clearPending(): void {
  pendingOpen = null;
  pendingPushes.length = 0;
}

export function registerWorkspaceStreamOverlaySink(next: WorkspaceStreamOverlaySink | null): void {
  sink = next;
  if (next) {
    flushPending();
  } else {
    clearPending();
  }
}

export function workspaceStreamOpen(opts: StreamingOverlayOpenOpts): void {
  if (sink) {
    sink.open(opts);
    return;
  }
  pendingOpen = opts;
  pendingPushes.length = 0;
}

export function workspaceStreamPush(caption: string): void {
  const c = caption.trim();
  if (!c) return;
  if (sink) {
    sink.push(c);
    return;
  }
  if (pendingOpen) pendingPushes.push(c);
}

export function workspaceStreamClose(delayMs = 1000): void {
  if (sink) {
    sink.close(delayMs);
    return;
  }
  clearPending();
}

/** Test-only reset */
export function resetWorkspaceStreamOverlayBridgeForTests(): void {
  sink = null;
  clearPending();
}
