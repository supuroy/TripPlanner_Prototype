import { test, expect } from "@playwright/test";
import { installWanderlyApiMocks } from "./helpers/install-wanderly-mocks";

test.describe("Wanderly comprehensive (mocked API)", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(120_000);

  test("first plan from command bar → shortlist + card chat advisor", async ({ page }) => {
    await installWanderlyApiMocks(page);
    await page.goto("/");
    const cmd = page.getByTestId("wanderly-command-input");
    await expect(cmd).toBeVisible({ timeout: 15_000 });

    await cmd.fill("Long weekend in Lisbon for food.");
    await cmd.press("Enter");

    await expect(page.getByText("Lisbon", { exact: true }).first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Portugal").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Agent Chat" })).toBeVisible();

    await page.getByText("Lisbon", { exact: true }).first().click();
    await expect(page.getByText("Arrive", { exact: false }).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Explore", { exact: false }).first()).toBeVisible();

    const cardChat = page.getByTestId("wanderly-card-chat-input");
    await expect(cardChat).toBeVisible({ timeout: 10_000 });
    await cardChat.fill("What is Lisbon known for food-wise?");
    await cardChat.press("Enter");
    await expect(page.getByText(/pastéis de nata|custard tarts/i).first()).toBeVisible({ timeout: 15_000 });
  });

  test("refine from command bar adds second destination", async ({ page }) => {
    await installWanderlyApiMocks(page);
    await page.goto("/");
    const cmd = page.getByTestId("wanderly-command-input");
    await cmd.fill("Lisbon trip");
    await cmd.press("Enter");
    await expect(page.getByText("Lisbon", { exact: true }).first()).toBeVisible({ timeout: 30_000 });

    await cmd.fill("Please add Porto for wine country.");
    await cmd.press("Enter");

    await expect(page.getByText("Porto", { exact: true }).first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Douro", { exact: false }).first()).toBeVisible();
  });

  test("collapse canvas then restore cards strip", async ({ page }) => {
    await installWanderlyApiMocks(page);
    await page.goto("/");
    const cmd = page.getByTestId("wanderly-command-input");
    await cmd.fill("Lisbon");
    await cmd.press("Enter");
    await expect(page.getByText("Lisbon", { exact: true }).first()).toBeVisible({ timeout: 30_000 });

    await page.getByRole("button", { name: /Maximize chat/i }).first().click();
    const showCards = page.getByRole("button", { name: /Show cards/i }).first();
    await expect(showCards).toBeVisible({ timeout: 10_000 });
    await showCards.click();
    await expect(page.getByText("Tap to see full plan").first()).toBeVisible({ timeout: 10_000 });
  });

  test("expanded card returns to grid when hero image is clicked", async ({ page }) => {
    await installWanderlyApiMocks(page);
    await page.goto("/");
    const cmd = page.getByTestId("wanderly-command-input");
    await cmd.fill("Lisbon weekend");
    await cmd.press("Enter");
    await expect(page.getByText("Lisbon", { exact: true }).first()).toBeVisible({ timeout: 30_000 });

    await page.getByText("Lisbon", { exact: true }).first().click();
    await expect(page.getByText("Arrive", { exact: false }).first()).toBeVisible({ timeout: 15_000 });

    /* Gradient overlay sits above the hero img; force click targets the image hit area. */
    await page.getByRole("img", { name: "Lisbon" }).click({ force: true });
    await expect(page.getByText("Tap to see full plan").first()).toBeVisible({ timeout: 10_000 });
  });
});
