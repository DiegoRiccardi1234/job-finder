// Screenshot capture against a pre-seeded demo database.
//
// Prereq: run `python scripts/seed_demo.py --db data/demo.db --force` first,
// then launch the webapp with `SEARCHER_DB_PATH=data/demo.db`.
//
// Produces 3 README screenshots: dashboard (light + dark) and job search wizard.

const { test, expect } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const OUTPUT_DIR = path.join(process.cwd(), "screenshots", "readme");
const VIEWPORT = { width: 1440, height: 900 };

function ensureOutputDir() {
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }
}

async function shot(page, file) {
  await page.screenshot({
    path: path.join(OUTPUT_DIR, file),
    fullPage: false,
  });
}

async function setTheme(page, theme) {
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("theme", t);
  }, theme);
  await page.waitForTimeout(300);
}

test("demo screenshots (pre-seeded DB)", async ({ page }) => {
  test.setTimeout(120_000);
  ensureOutputDir();

  await page.setViewportSize(VIEWPORT);
  await page.addInitScript(() => {
    localStorage.setItem("tutorialSeen", "1");
  });
  await page.goto("/");
  await expect(page.locator(".brand")).toBeVisible();
  await page.evaluate(() => {
    document.querySelectorAll(".tutorial-overlay").forEach((el) => el.remove());
  });
  await page.waitForTimeout(300);

  // 1. Dashboard (light)
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(500);
  await shot(page, "dashboard-en.png");

  // 2. Job Search wizard (Step 1 analyzed + chips)
  await page.locator(".topnav .nav-link[data-view='job-search']").click();
  await page.waitForTimeout(300);
  await page.locator("#wizardAnalyzeBtn").click();
  await page.waitForTimeout(700);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "job-search-en.png");

  // 3. Dashboard dark mode
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await setTheme(page, "dark");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(500);
  await shot(page, "dashboard-dark-en.png");
  await setTheme(page, "light");
});
