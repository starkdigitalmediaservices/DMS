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
  await page.goto(`/workbench?doc=${DOC}&page=1`);
  await expect(page.getByTestId("page-indicator")).toContainText("page 1 /", { timeout: 30_000 });
  // The document may already carry real corrections; everything below is
  // relative to where it started, and ends back there.
  const badge = page.getByTestId("review-badge");
  const startChanged = parseInt((await badge.innerText()).match(/(\d+) changed/)?.[1] || "0", 10);
  const badgeFor = (n: number) => (n === 0 ? "reviewed · no changes" : `reviewed · ${n} changed`);
  const editedCells = page.locator("[data-testid=review-cell][data-edited=true]");
  const startEdited = await editedCells.count();

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

  // Edit a not-yet-edited cell of that row.
  await page.getByRole("button", { name: "Edit mode off" }).click();
  const cell = highlighted.locator("[data-testid=review-cell][data-edited=false]").first();
  const original = (await cell.locator("button").first().innerText()).trim();
  await cell.locator("button").first().click();
  const editor = page.locator("textarea[aria-label^='Edit ']");
  await editor.fill("E2E-REVIEW-VALUE");
  await editor.press("Enter");

  await expect(badge).toHaveText(badgeFor(startChanged + 1));
  await expect(editedCells).toHaveCount(startEdited + 1);
  const edited = page.locator("[data-testid=review-cell][data-edited=true]", { hasText: "E2E-REVIEW-VALUE" });
  await expect(edited).toHaveCount(1);
  await expect(edited.getByRole("button", { name: /Undo edit/ })).toHaveAttribute("title", /original OCR/);

  // Undo it.
  await edited.getByRole("button", { name: /Undo edit/ }).click();
  await expect(badge).toHaveText(badgeFor(startChanged));
  await expect(editedCells).toHaveCount(startEdited);
  if (original !== "empty") await expect(highlighted.getByText(original, { exact: true }).first()).toBeVisible();

  // The loaded screen (not just an empty shell) meets the same bar as T96.
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(results.violations.flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(" ")} — ${n.failureSummary}`))).toEqual([]);
});

test("workbench: documents tab -> review -> back, and queue item -> its cell in the document", async ({ page }) => {
  await page.goto("/workbench?tab=documents");
  const rows = page.getByTestId("review-document-row");
  await expect(rows.first()).toBeVisible({ timeout: 20_000 });
  await rows.first().getByRole("link", { name: /^Review / }).click();
  await expect(page).toHaveURL(/\/workbench\?doc=.*from=documents/);
  await expect(page.getByTestId("page-indicator")).toContainText("page 1 /", { timeout: 30_000 });
  await page.getByRole("link", { name: "Back to documents" }).click();
  await expect(page).toHaveURL(/tab=documents/);

  await page.goto("/workbench");
  await page.waitForLoadState("networkidle");
  const open = page.getByRole("link", { name: "Open in document" });
  test.skip((await open.count()) === 0, "the low-confidence queue is empty");
  await open.first().click();
  await expect(page).toHaveURL(/fact=.*from=queue/);
  // exactly the queue item's cell is selected in the list, and outlined on the scan
  await expect(page.locator("td[data-testid=review-cell].outline")).toHaveCount(1, { timeout: 60_000 });
  await expect(page.locator("div.border-amber-500")).toHaveCount(1, { timeout: 30_000 });
  await expect(page.getByRole("link", { name: "Back to queue" })).toBeVisible();

  // old /review links still land in the Workbench
  await page.goto(`/review?doc=${DOC}&page=2`);
  await expect(page).toHaveURL(/\/workbench\?doc=.*page=2/);
  await expect(page.getByTestId("page-indicator")).toContainText("page 2 /", { timeout: 30_000 });
});
