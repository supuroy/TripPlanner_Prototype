#!/usr/bin/env node
/**
 * Reads ``e2e/perf/results/latest.json`` (wanderly-perf-run/v1) and prints:
 *   - milestone timeline
 *   - segment gaps (slow hops between marks)
 *   - aggregate network cost by pathname (mean / max / count)
 *   - largest single requests (likely bottlenecks)
 *
 * Usage:
 *   node e2e/perf/scripts/analyze-timeline.mjs
 *   node e2e/perf/scripts/analyze-timeline.mjs path/to/run.json
 */
import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const defaultPath = join(__dirname, "..", "results", "latest.json");
const file = process.argv[2] || defaultPath;

if (!existsSync(file)) {
  console.error(`No perf artifact at ${file}\nRun: npm run test:e2e:perf`);
  process.exit(1);
}

const data = JSON.parse(readFileSync(file, "utf8"));
if (data.schema !== "wanderly-perf-run/v1") {
  console.error("Unknown schema:", data.schema);
  process.exit(1);
}

const marks = [...data.marks].sort((a, b) => a.offsetMs - b.offsetMs);
const net = data.network || [];

function overlaps(a0, a1, b0, b1) {
  return b0 < a1 && b1 > a0;
}

console.log("\n=== Wanderly perf run ===\n");
console.log(`Journey: ${data.journeyId}`);
console.log(`Generated: ${data.generatedAt}`);
console.log(`Marks: ${marks.length}  Network: ${net.length}\n`);

console.log("--- Milestones (offset ms from journey_start) ---\n");
for (const m of marks) {
  const note = m.note ? `  (${m.note})` : "";
  console.log(`${String(m.offsetMs).padStart(8)} ms  ${m.event}${note}`);
}

console.log("\n--- Segment gaps (consecutive marks) ---\n");
for (let i = 1; i < marks.length; i++) {
  const prev = marks[i - 1];
  const cur = marks[i];
  const gap = cur.offsetMs - prev.offsetMs;
  const segStart = prev.offsetMs;
  const segEnd = cur.offsetMs;
  const inSeg = net.filter((r) => overlaps(segStart, segEnd, r.startOffsetMs, r.endOffsetMs));
  const sorted = [...inSeg].sort((a, b) => b.durationMs - a.durationMs);
  const top = sorted[0];
  const topStr = top ? `${top.pathname}  ${top.durationMs}ms` : "(no overlapping HTTP)";
  console.log(`${prev.event}  →  ${cur.event}`);
  console.log(`  wall_gap_ms: ${gap}`);
  console.log(`  slowest_in_segment: ${topStr}`);
  if (sorted.length > 1) {
    console.log(
      `  next: ${sorted
        .slice(1, 4)
        .map((r) => `${r.pathname}:${r.durationMs}ms`)
        .join("  |  ")}`,
    );
  }
  console.log("");
}

const byPath = new Map();
for (const r of net) {
  const k = r.pathname || "/";
  if (!byPath.has(k)) byPath.set(k, { count: 0, total: 0, max: 0 });
  const o = byPath.get(k);
  o.count += 1;
  o.total += r.durationMs;
  o.max = Math.max(o.max, r.durationMs);
}

console.log("--- Aggregate by pathname (desc max duration) ---\n");
const rows = [...byPath.entries()]
  .map(([pathname, o]) => ({
    pathname,
    count: o.count,
    mean: Math.round(o.total / o.count),
    max: o.max,
  }))
  .sort((a, b) => b.max - a.max);

for (const r of rows.slice(0, 25)) {
  console.log(
    `${String(r.max).padStart(7)}ms max  ${String(r.mean).padStart(6)}ms mean  x${String(r.count).padStart(3)}  ${r.pathname}`,
  );
}

console.log("\n--- Top 15 single requests (wall) ---\n");
const byDur = [...net].sort((a, b) => b.durationMs - a.durationMs).slice(0, 15);
for (const r of byDur) {
  console.log(
    `${String(r.durationMs).padStart(7)}ms  ${String(r.status).padStart(3)}  ${r.method.padEnd(6)}  ${r.pathname}`,
  );
}

const total = marks.length >= 2 ? marks[marks.length - 1].offsetMs - marks[0].offsetMs : 0;
const netSum = net.reduce((s, r) => s + r.durationMs, 0);
console.log(`\nTotal journey span (first→last mark): ${total} ms`);
console.log(
  `Sum of all request durations (parallel overlap — not wall time): ${netSum} ms\n`,
);

console.log("--- Suggested optimization focus (by max single-request latency) ---\n");
for (const r of rows.slice(0, 5)) {
  console.log(`  • ${r.pathname} — max ${r.max} ms over ${r.count} calls (mean ${r.mean} ms)`);
}
console.log("");
