import type { Page, Request } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export type PerfMark = {
  event: string;
  wallMs: number;
  offsetMs: number;
  note?: string;
};

export type PerfNetworkEntry = {
  url: string;
  pathname: string;
  method: string;
  startOffsetMs: number;
  endOffsetMs: number;
  durationMs: number;
  status: number;
};

export type PerfRunPayload = {
  schema: "wanderly-perf-run/v1";
  generatedAt: string;
  journeyId: string;
  t0EpochMs: number;
  marks: PerfMark[];
  network: PerfNetworkEntry[];
};

/**
 * Wall-clock + HTTP/SSE timing for one browser session. Attach before navigation.
 */
export class PerfHarness {
  readonly t0EpochMs = Date.now();
  readonly journeyId = `perf-${this.t0EpochMs}`;
  readonly marks: PerfMark[] = [];
  readonly network: PerfNetworkEntry[] = [];
  private readonly pending = new Map<Request, { url: string; method: string; start: number }>();

  constructor(private readonly page: Page) {
    page.on("request", (req) => {
      const u = req.url();
      if (!/\/api\/|\/state\/|\/plan\/|\/traveler\/|\/profiles\//.test(u)) return;
      this.pending.set(req, { url: u, method: req.method(), start: Date.now() });
    });
    page.on("requestfailed", (req) => {
      this.pending.delete(req);
    });
    page.on("requestfinished", (req) => {
      const p = this.pending.get(req);
      if (!p) return;
      this.pending.delete(req);
      const end = Date.now();
      let pathname = "";
      try {
        pathname = new URL(p.url).pathname;
      } catch {
        pathname = p.url;
      }
      /* Playwright: ``response.status`` is a number property, not ``status()``. */
      const status = req.response()?.status ?? 0;
      this.network.push({
        url: p.url,
        pathname,
        method: p.method,
        startOffsetMs: p.start - this.t0EpochMs,
        endOffsetMs: end - this.t0EpochMs,
        durationMs: end - p.start,
        status,
      });
    });
  }

  mark(event: string, note?: string): void {
    const wallMs = Date.now();
    this.marks.push({
      event,
      wallMs,
      offsetMs: wallMs - this.t0EpochMs,
      note,
    });
  }

  payload(): PerfRunPayload {
    return {
      schema: "wanderly-perf-run/v1",
      generatedAt: new Date().toISOString(),
      journeyId: this.journeyId,
      t0EpochMs: this.t0EpochMs,
      marks: [...this.marks],
      network: [...this.network],
    };
  }

  writeJson(filePath: string): void {
    mkdirSync(dirname(filePath), { recursive: true });
    writeFileSync(filePath, JSON.stringify(this.payload(), null, 2), "utf8");
  }
}
