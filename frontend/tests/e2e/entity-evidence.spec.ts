import { test, expect } from "@playwright/test";

// Entity 360 "Show on page" = read-only proof: only the original page(s),
// the value outlined, nothing to edit. Needs a real entity with a linked
// fact that has a recorded position:
//   E2E_ENTITY_ID        entity node id
//   E2E_ACCESS_TOKEN     bearer token for that tenant
const ENTITY = process.env.E2E_ENTITY_ID;
const TOKEN = process.env.E2E_ACCESS_TOKEN;

test.skip(!ENTITY || !TOKEN, "set E2E_ENTITY_ID and E2E_ACCESS_TOKEN");

test("Entity 360: Show on page opens only the proof page, highlighted, and Esc returns", async ({ page }) => {
  test.setTimeout(120_000);
  // short window so the entity page genuinely scrolls
  await page.setViewportSize({ width: 1280, height: 520 });
  await page.goto("/login");
  await page.evaluate((t) => localStorage.setItem("access_token", t), TOKEN!);
  await page.goto(`/entities?node=${ENTITY}`);
  const factsCard = page.locator("div", { has: page.getByRole("heading", { name: /^Linked facts/ }) }).last();
  await expect(page.getByRole("heading", { name: /^Linked facts/ })).toBeVisible({ timeout: 60_000 });

  // scroll down so we can prove the position is kept
  await page.getByRole("heading", { name: /^Appears in/ }).scrollIntoViewIfNeeded();
  const scrollBefore = await page.evaluate(() =>
    Array.from(document.querySelectorAll("*")).reduce((m, el) => Math.max(m, (el as HTMLElement).scrollTop || 0), window.scrollY)
  );
  expect(scrollBefore).toBeGreaterThan(0);

  await factsCard.getByRole("button", { name: "Show on page" }).first().click();
  const viewer = page.getByTestId("evidence-viewer");
  await expect(viewer).toBeVisible();
  await expect(viewer.getByRole("img", { name: /Scanned page/ })).toBeVisible({ timeout: 60_000 });
  await expect(viewer.locator("[data-overlay=focus]")).toBeVisible({ timeout: 30_000 });
  await expect(viewer.getByTestId("page-indicator")).toHaveText(/page \d+/);

  // nothing but the page: no extracted table, cards, or edit/undo controls
  await expect(viewer.locator("table")).toHaveCount(0);
  await expect(viewer.getByTestId("review-card")).toHaveCount(0);
  await expect(viewer.getByTestId("queue-item-card")).toHaveCount(0);
  await expect(viewer.getByRole("button", { name: /Undo|Save correction|Correct|Start editing|verify|checked/i })).toHaveCount(0);
  await expect(viewer).not.toContainText(/corrections?\b/i);

  await page.keyboard.press("Escape");
  await expect(viewer).toHaveCount(0);
  await expect(page).toHaveURL(/\/entities\?node=/);
  const scrollAfter = await page.evaluate(() =>
    Array.from(document.querySelectorAll("*")).reduce((m, el) => Math.max(m, (el as HTMLElement).scrollTop || 0), window.scrollY)
  );
  expect(scrollAfter).toBe(scrollBefore);
});

test("Entity 360: Appears in only moves between that document's mention pages", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/login");
  await page.evaluate((t) => localStorage.setItem("access_token", t), TOKEN!);
  await page.goto(`/entities?node=${ENTITY}`);
  const appears = page.locator("div", { has: page.getByRole("heading", { name: /^Appears in/ }) }).last();
  await expect(appears.getByRole("button", { name: "Show on page" }).first()).toBeVisible({ timeout: 60_000 });
  const summary = await appears.locator("summary").first().innerText();
  const listed = (summary.match(/pp?\. ([\d, ]+)/)?.[1] || "").split(",").map((x) => x.trim()).filter(Boolean);

  await appears.getByRole("button", { name: "Show on page" }).first().click();
  const viewer = page.getByTestId("evidence-viewer");
  await expect(viewer.locator("[data-overlay=focus]").first()).toBeVisible({ timeout: 60_000 });
  const seen = new Set<string>();
  for (let i = 0; i < 10; i++) {
    const m = (await viewer.getByTestId("page-indicator").innerText()).match(/page (\d+)/);
    if (m) seen.add(m[1]);
    const next = viewer.getByRole("button", { name: "Next page" });
    if (await next.isDisabled()) break;
    await next.click();
    await page.waitForTimeout(400);
  }
  expect([...seen].sort()).toEqual([...listed].sort());
  await page.keyboard.press("Escape");
  await expect(viewer).toHaveCount(0);
});
