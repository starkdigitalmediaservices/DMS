import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Uses the shared authenticated storageState from global.setup.ts (the
// project's default `use.storageState` in playwright.config.ts) — no
// override needed here, unlike public-pages.spec.ts.
const AUTHENTICATED_PAGES = [
  "/drive",
  "/workbench",
  "/workbench?tab=documents",
  "/entities",
  "/completeness",
  "/upload",
  "/profile",
  "/admin",
  "/admin/settings",
  "/admin/templates",
  "/admin/users",
  "/admin/departments",
];

for (const path of AUTHENTICATED_PAGES) {
  test(`${path} has no WCAG 2.1 AA violations`, async ({ page }) => {
    await page.goto(path);
    await page.waitForLoadState("networkidle");

    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();

    expect(
      results.violations,
      formatViolations(path, results.violations),
    ).toEqual([]);
  });
}

function formatViolations(path: string, violations: any[]): string {
  if (violations.length === 0) return "";
  return [
    `${path}: ${violations.length} WCAG 2.1 AA violation(s)`,
    ...violations.map(
      (v) =>
        `  [${v.impact}] ${v.id} — ${v.help} (${v.nodes.length} element(s))\n` +
        v.nodes.map((n: any) => `    ${n.target.join(" ")}`).join("\n"),
    ),
  ].join("\n");
}
