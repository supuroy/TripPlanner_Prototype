import { defineConfig, devices } from "@playwright/test";

/**
 * E2E tests assume the Wanderly UI at the Vite dev server (no auth).
 * `npm run test:e2e` starts the dev server unless one is already running.
 */
export default defineConfig({
  testDir: "./e2e",
  /* Perf journeys use ``playwright.perf.config.ts`` (real backend, long timeouts). */
  testIgnore: "**/perf/**",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
