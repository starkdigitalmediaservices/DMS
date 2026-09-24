import { defineConfig } from "@playwright/test";

// Functional end-to-end specs (tests/e2e), separate from the T96 a11y scan
// config. They drive a real backend with real extracted data, so each spec
// names the data it needs through environment variables and skips itself
// when they are absent -- see tests/e2e/review.spec.ts.
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report-e2e" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3000",
    channel: "chrome",
    viewport: { width: 1600, height: 1000 },
  },
});
