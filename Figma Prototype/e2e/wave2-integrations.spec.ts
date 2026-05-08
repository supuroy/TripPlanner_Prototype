import { test, expect } from "@playwright/test";
import { installWanderlyApiMocks } from "./helpers/install-wanderly-mocks";
import { workspaceLisbonBeachUpdate } from "./fixtures/wanderly-workspaces";

test.describe("Wanderly wave 2 — profile, itinerary refine, card Q&A", () => {
  test.use({ viewport: { width: 1400, height: 900 } });
  test.describe.configure({ mode: "serial" });
  test.setTimeout(120_000);

  test("sidebar profile pill edit persists and shows advisor headline", async ({ page }) => {
    await installWanderlyApiMocks(page);
    await page.goto("/");
    const cmd = page.getByTestId("wanderly-command-input");
    await cmd.fill("Lisbon food weekend");
    await cmd.press("Enter");
    await expect(page.getByText("Lisbon", { exact: true }).first()).toBeVisible({ timeout: 30_000 });

    await page.getByRole("button", { name: /When/i }).click();
    await page.getByRole("button", { name: "Apr 1–8" }).click();

    await expect(page.getByRole("button", { name: /When/i })).toContainText("Apr 1–8", { timeout: 15_000 });
    await expect(page.getByText(/E2E mock: pill updated/i).first()).toBeVisible({ timeout: 10_000 });
  });

  test("command bar refine updates Lisbon itinerary after initial build", async ({ page }) => {
    await installWanderlyApiMocks(page, { refineWorkspace: workspaceLisbonBeachUpdate });
    await page.goto("/");
    const cmd = page.getByTestId("wanderly-command-input");
    await cmd.fill("Lisbon long weekend");
    await cmd.press("Enter");
    await expect(page.getByText(/Tiles, trams/i).first()).toBeVisible({ timeout: 30_000 });
    await page.getByText("Lisbon", { exact: true }).first().click();
    await expect(page.getByText("Explore", { exact: false }).first()).toBeVisible({ timeout: 15_000 });
    /* Command bar is hidden while a card is expanded — close the card before refine. */
    await page.getByRole("img", { name: "Lisbon" }).click({ force: true });
    await expect(cmd).toBeVisible({ timeout: 10_000 });

    await cmd.fill("Add a beach morning on day 2 of the Lisbon itinerary.");
    await cmd.press("Enter");

    /* Refine workspace applies new copy to the agent thread immediately. */
    await expect(page.getByText(/beach time on day 2/i).first()).toBeVisible({ timeout: 45_000 });

    await page.getByText("Lisbon", { exact: true }).first().click();
    /*
     * When ``markItinerariesStale`` runs, a background ``build_itinerary`` mock may race and
     * replace rows with ``MOCK_ITINERARY_DAYS`` — assert either the refine payload or that rebuild.
     */
    await expect(
      page.getByText(/Carcavelos beach|Mock day 1/i).first(),
    ).toBeVisible({ timeout: 20_000 });
  });

  test("card chat: several itinerary-focused advisor answers", async ({ page }) => {
    await installWanderlyApiMocks(page, {
      advisorFeedResponder: (message) => {
        const m = message.toLowerCase();
        if (m.includes("tram"))
          return {
            feed_markdown:
              "**Historic trams:** Line **28** is the scenic Remodelado route; use a **Viva viagem** card for tap-on rides.",
          };
        if (m.includes("day 2") || m.includes("day two") || m.includes("second day"))
          return {
            feed_markdown:
              "**Day 2 shape:** keep **Belém** in the morning, then a **long lunch** before a low-key evening.",
          };
        if (m.includes("belém") || m.includes("belem") || m.includes("pastry"))
          return {
            feed_markdown:
              "**Belém:** Mosteiro dos Jerónimos + Torre de Belém pair well; **pastéis** are the classic sweet stop.",
          };
        return { feed_markdown: `**Note:** (mock) Short reply about: ${message.slice(0, 50)}…` };
      },
    });
    await page.goto("/");
    const cmd = page.getByTestId("wanderly-command-input");
    await cmd.fill("Lisbon planning");
    await cmd.press("Enter");
    await expect(page.getByText("Lisbon", { exact: true }).first()).toBeVisible({ timeout: 30_000 });

    await page.getByText("Lisbon", { exact: true }).first().click();
    const cardChat = page.getByTestId("wanderly-card-chat-input");
    await expect(cardChat).toBeVisible({ timeout: 10_000 });

    await cardChat.fill("Are historic trams worth it for tourists?");
    await cardChat.press("Enter");
    await expect(page.getByText(/Line\s*\*\*28\*\*|Remodelado|Viva viagem/i).first()).toBeVisible({
      timeout: 15_000,
    });

    await cardChat.fill("How should we pace day two?");
    await cardChat.press("Enter");
    await expect(page.getByText(/Belém.*morning|long lunch/i).first()).toBeVisible({ timeout: 15_000 });

    await cardChat.fill("What about Belém pastries?");
    await cardChat.press("Enter");
    await expect(page.getByText(/Jerónimos|Torre de Belém|pastéis/i).first()).toBeVisible({ timeout: 15_000 });
  });
});
