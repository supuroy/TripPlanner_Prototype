import { describe, expect, it, beforeEach } from "vitest";
import {
  registerWorkspaceStreamOverlaySink,
  resetWorkspaceStreamOverlayBridgeForTests,
  workspaceStreamClose,
  workspaceStreamOpen,
  workspaceStreamPush,
} from "./workspaceStreamOverlayBridge";

describe("workspaceStreamOverlayBridge", () => {
  beforeEach(() => {
    resetWorkspaceStreamOverlayBridgeForTests();
  });

  it("queues open and push until a sink registers (first-trip smoke race)", () => {
    const log: string[] = [];
    workspaceStreamOpen({ title: "Planning your trip", subtitle: "Streaming…" });
    workspaceStreamPush("Understanding trip type…");
    workspaceStreamPush("Building destination shortlist…");

    registerWorkspaceStreamOverlaySink({
      open: (opts) => log.push(`open:${opts.title}`),
      push: (c) => log.push(`push:${c}`),
      close: () => log.push("close"),
    });

    expect(log).toEqual([
      "open:Planning your trip",
      "push:Understanding trip type…",
      "push:Building destination shortlist…",
    ]);
  });

  it("close before register clears pending open", () => {
    const log: string[] = [];
    workspaceStreamOpen({ title: "Planning your trip" });
    workspaceStreamPush("Should not replay");
    workspaceStreamClose();

    registerWorkspaceStreamOverlaySink({
      open: (opts) => log.push(`open:${opts.title}`),
      push: (c) => log.push(`push:${c}`),
      close: () => log.push("close"),
    });

    expect(log).toEqual([]);
  });
});
