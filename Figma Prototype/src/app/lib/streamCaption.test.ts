import { describe, expect, it } from "vitest";
import {
  looksLikePartialStructuredPlannerPayload,
  streamEventToCaption,
  OVERLAY_STRUCTURED_PLANNER_STATUS,
} from "./streamCaption";

describe("streamEventToCaption overlay structured leak", () => {
  it("hides partial assistant_markdown JSON chunks", () => {
    const assistantDraft = { text: "" };
    const thinkingDraft = { text: "" };
    const cap = streamEventToCaption(
      { type: "assistant_delta", delta: '{"assistant_markdown' },
      assistantDraft,
      thinkingDraft,
      { overlay: true, structuredStreamMode: "workspace" },
    );
    expect(cap).toBe(OVERLAY_STRUCTURED_PLANNER_STATUS.workspace);
    expect(cap).not.toContain("assistant_markdown");
  });

  it("detects partial structured payloads", () => {
    expect(looksLikePartialStructuredPlannerPayload('{"destinations":')).toBe(true);
    expect(looksLikePartialStructuredPlannerPayload("Planning Seattle")).toBe(false);
  });
});
