import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Review screen: open -> click a box on the scan -> its row highlights in
// the card list -> edit a cell -> undo it. Leaves the document exactly as
// it found it (the undo restores the fact's OCR value and status; the two
// audit entries it writes are append-only by design).
//
// Needs a real document that has template-extracted facts, and a session
// for a role that can edit (records_officer, operator or it_admin):
//   E2E_REVIEW_DOC_ID   document id
//   E2E_ACCESS_TOKEN    a bearer token for that tenant, or
//   E2E_EMAIL / E2E_PASSWORD   credentials to log in with
const DOC = process.env.E2E_REVIEW_DOC_ID;
const TOKEN = process.env.E2E_ACCESS_TOKEN;
const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;

test.skip(!DOC || (!TOKEN && !(EMAIL && PASSWORD)), "set E2E_REVIEW_DOC_ID and E2E_ACCESS_TOKEN (or E2E_EMAIL/E2E_PASSWORD)");

test.beforeEach(async ({ page }) => {
  await page.goto("/login");
  if (TOKEN) {
    await page.evaluate((t) => localStorage.setItem("access_token", t), TOKEN);
  } else {
    await page.getByLabel(/email/i).fill(EMAIL!);
    await page.getByLabel(/^password/i).fill(PASSWORD!);
    await page.getByRole("button", { name: /sign in|log in/i }).click();
    await page.waitForURL(/\/drive/);
  }
});

test("click box on scan -> card highlights -> edit cell -> undo", async ({ page }) => {
  await page.goto(`/review?doc=${DOC}&page=1`);
  await expect(page.getByTestId("page-indicator")).toContainText("page 1 /", { timeout: 30_000 });
  await expect(page.getByTestId("review-badge")).toHaveText("reviewed · no changes");

  // Find a page with at least one row box on it.
  const rowBoxes = page.locator("[data-overlay^='r-']");
  for (let i = 0; i < 5 && (await rowBoxes.count()) === 0; i++) {
    await page.getByRole("button", { name: "Next page" }).click();
    await page.waitForTimeout(500);
  }
  await expect(rowBoxes.first()).toBeVisible();

  // Click a box on the scan -> exactly one row is highlighted and scrolled into view.
  await rowBoxes.first().click();
  const highlighted = page.locator("tr[data-testid=review-row].bg-\\[\\#e8f0fe\\]");
  await expect(highlighted).toHaveCount(1);
  await expect(highlighted).toBeInViewport();

  // Edit a cell of that row.
  await page.getByRole("button", { name: "Edit mode off" }).click();
  const cell = highlighted.locator("[data-testid=review-cell]").first();
  const original = (await cell.locator("button").first().innerText()).trim();
  await cell.locator("button").first().click();
  const editor = page.locator("textarea[aria-label^='Edit ']");
  await editor.fill("E2E-REVIEW-VALUE");
  await editor.press("Enter");

  await expect(page.getByTestId("review-badge")).toHaveText("reviewed · 1 changed");
  const edited = page.locator("[data-testid=review-cell][data-edited=true]");
  await expect(edited).toHaveCount(1);
  await expect(edited).toContainText("E2E-REVIEW-VALUE");
  await expect(edited.getByRole("button", { name: /Undo edit/ })).toHaveAttribute("title", /original OCR/);

  // Undo it.
  await edited.getByRole("button", { name: /Undo edit/ }).click();
  await expect(page.getByTestId("review-badge")).toHaveText("reviewed · no changes");
  await expect(page.locator("[data-testid=review-cell][data-edited=true]")).toHaveCount(0);
  if (original !== "empty") await expect(cell).toContainText(original);

  // The loaded screen (not just an empty shell) meets the same bar as T96.
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(results.violations.flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(" ")} — ${n.failureSummary}`))).toEqual([]);
});
