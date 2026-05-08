import { test, expect } from "@playwright/test";

test.describe("Wanderly smoke", () => {
  test("idle home loads with hero and command input", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/Wanderly/i);
    await expect(page.getByRole("heading", { name: "Where to next?" })).toBeVisible();
    await expect(page.getByTestId("wanderly-command-input")).toBeVisible();
  });

  test("suggestion chips appear after idle animation", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Try saying")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: /warm beach trip/i })).toBeVisible();
  });

  test("typing in the command bar does not throw (no crash on mount)", async ({ page }) => {
    await page.goto("/");
    const input = page.getByTestId("wanderly-command-input");
    await input.fill("Test trip to Lisbon for a weekend.");
    await expect(input).toHaveValue("Test trip to Lisbon for a weekend.");
  });
});
