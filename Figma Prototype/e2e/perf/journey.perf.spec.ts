import { test, expect } from "@playwright/test";
import { PerfHarness } from "./lib/perf-harness";
import { join } from "node:path";

const LATEST_JSON = join(process.cwd(), "e2e", "perf", "results", "latest.json");

/** Heavy initial prompt to stress planner + mapper (mimics choosy traveler). */
const INITIAL_TRIP_PROMPT =
  "Two travelers from Seattle, mid-March, about 9 days total, warm-but-not-brutal heat, strong food + walkable cities. " +
  "Budget mid-range (~4k USD total for both). We want 3–4 destination options with realistic flights from the US west coast, " +
  "day-by-day pacing that is not rushed, one rest day, and one beach or waterfront day where it fits.";

test.describe.configure({ mode: "serial" });

test("perf journey — plan, per-card itineraries, targeted refines, profile-wide refresh", async ({ page }) => {
  const perf = new PerfHarness(page);
  perf.mark("journey_start", "perf harness attached");

  await page.goto("/");
  perf.mark("shell_loaded", "after goto /");

  const cmd = page.getByTestId("wanderly-command-input");
  await expect(cmd).toBeVisible({ timeout: 120_000 });
  perf.mark("command_bar_ready");

  await cmd.fill(INITIAL_TRIP_PROMPT);
  perf.mark("first_plan_prompt_entered");
  await cmd.press("Enter");
  perf.mark("first_plan_request_sent", "smoke_turn_stream or plan per env");

  await expect(page.getByTestId("destination-grid-card").first()).toBeVisible({ timeout: 600_000 });
  perf.mark("first_destination_card_visible", "shortlist rendered");

  const cards = page.getByTestId("destination-grid-card");
  const n = await cards.count();
  expect(n).toBeGreaterThan(0);
  perf.mark("destination_count_known", `n=${n}`);

  const names: string[] = [];
  for (let i = 0; i < n; i++) {
    const card = cards.nth(i);
    const name = (await card.getByTestId("destination-name").textContent())?.trim() || `card-${i}`;
    names.push(name);
    const destId = await card.getAttribute("data-destination-id");
    await card.click();
    perf.mark(`itin_expand_${name}_clicked`, destId ?? undefined);

    const loading = page.getByTestId("expanded-itinerary-loading");
    try {
      await loading.waitFor({ state: "visible", timeout: 15_000 });
      perf.mark(`itin_build_started_${name}`);
      await loading.waitFor({ state: "hidden", timeout: 600_000 });
    } catch {
      /* Already had itinerary — no loader */
    }
    await expect(page.getByText(/-Day Plan/i).first()).toBeVisible({ timeout: 120_000 });
    perf.mark(`itin_ready_${name}`, "MiniItinerary visible");

    await page.getByRole("img", { name }).click({ force: true });
    perf.mark(`itin_expand_closed_${name}`);
  }

  perf.mark("all_destination_itineraries_touched");

  /* Targeted refines on first two cards (command bar — real Cursor). */
  const refine0 = `Focused tweak for "${names[0]}": add one slower morning on day 2 (lighter schedule, same highlights).`;
  await cmd.fill(refine0);
  await cmd.press("Enter");
  perf.mark("refine_prompt_0_sent", names[0]);
  await expect(cmd).toBeEnabled({ timeout: 600_000 });
  perf.mark("refine_prompt_0_complete", "phase idle / input enabled");

  if (n > 1) {
    const refine1 = `For "${names[1]}": swap one shopping block for a short local food market visit on any day.`;
    await cmd.fill(refine1);
    await cmd.press("Enter");
    perf.mark("refine_prompt_1_sent", names[1]);
    await expect(cmd).toBeEnabled({ timeout: 600_000 });
    perf.mark("refine_prompt_1_complete");
  }

  /* Traveler profile: change "When" to another preset → profile_patch + possible global itinerary rebuild. */
  const whenBtn = page.getByRole("button", { name: /When/i }).first();
  const whenLabel = ((await whenBtn.textContent()) || "").replace(/\s+/g, " ").trim();
  await whenBtn.click();
  const whenChoices = ["Mar 8–14", "Mar 15–22", "Apr 1–8", "Flexible", "Mar 1–7"] as const;
  let picked: string | null = null;
  for (const label of whenChoices) {
    if (whenLabel.includes(label)) continue;
    const b = page.getByRole("button", { name: label, exact: true });
    if (await b.isVisible().catch(() => false)) {
      await b.click();
      picked = label;
      break;
    }
  }
  if (!picked) {
    const fallback = page.locator(".absolute.top-full.left-0.right-0 button").first();
    await fallback.click();
    picked = "first_dropdown_option";
  }
  perf.mark("profile_when_changed", picked);

  const planUpdating = page.getByText("Plan updating…");
  await expect(planUpdating).toHaveCount(0, { timeout: 600_000 });
  perf.mark("profile_refresh_chips_cleared");

  perf.mark("journey_end");
  perf.writeJson(LATEST_JSON);
});
