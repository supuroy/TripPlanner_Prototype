import { defineConfig, devices } from "@playwright/test";

/**
 * Performance journey: hits the **real** Vite app + backend (no API mocks).
 * Run with backend on `127.0.0.1:8000` (Vite proxy). One worker, long timeouts.
 *
 *   npm run test:e2e:perf
 *   npm run perf:analyze
 */
export default defineConfig({
  testDir: "./e2e/perf",
  testMatch: "**/*.perf.spec.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 900_000,
  expect: { timeout: 120_000 },
  reporter: [["list"], ["json", { outputFile: "e2e/perf/results/playwright-perf-report.json" }]],
  use: {
    baseURL: "http://localhost:5173",
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
    trace: process.env.WANDERLY_PERF_TRACE === "1" ? "on" : "off",
    video: process.env.WANDERLY_PERF_VIDEO === "1" ? "on" : "off",
  },
  projects: [{ name: "chromium-perf", use: {} }],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
