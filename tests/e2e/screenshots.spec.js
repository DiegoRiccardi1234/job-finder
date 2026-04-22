const { test } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const OUTPUT_DIR = path.join(process.cwd(), "screenshots");

function ensureOutputDir() {
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }
}

async function captureAllViews(page, suffix) {
  await page.goto("/");
  await page.waitForLoadState("networkidle");

  // 1. Dashboard Table View Output
  await page.screenshot({
    path: path.join(OUTPUT_DIR, `dashboard-${suffix}.png`),
    fullPage: true,
  });

  // 2. Dashboard Kanban View
  if (await page.locator('#viewKanbanBtn').isVisible()) {
      await page.locator('#viewKanbanBtn').click();
      await page.waitForTimeout(500);
      await page.screenshot({
        path: path.join(OUTPUT_DIR, `dashboard-kanban-${suffix}.png`),
        fullPage: true,
      });
  }

  // 3. Dashboard Dark Mode
  if (await page.locator('#themeToggle').isVisible()) {
      await page.locator('#themeToggle').click();
      await page.waitForTimeout(500);
      await page.screenshot({
        path: path.join(OUTPUT_DIR, `dashboard-dark-${suffix}.png`),
        fullPage: true,
      });
      // Toggle back
      await page.locator('#themeToggle').click();
      await page.waitForTimeout(500);
  }

  // 4. Job Detail Inline
  // Let's go back to table view
  if (await page.locator('#viewTableBtn').isVisible()) {
      await page.locator('#viewTableBtn').click();
      await page.waitForTimeout(500);
  }
  const detailBtn = page.locator('#jobsTableBody button[data-detail-id]').first();
  if (await detailBtn.isVisible()) {
      await detailBtn.click();
      await page.waitForTimeout(600);
      await page.screenshot({
        path: path.join(OUTPUT_DIR, `job-detail-${suffix}.png`),
        fullPage: true,
      });
      // Close inline detail
      const closeBtn = page.locator('#closeDetailBtn');
      if (await closeBtn.isVisible()) {
          await closeBtn.click();
          await page.waitForTimeout(500);
      }
  }

  // 5. Settings
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await page.waitForTimeout(350);
  await page.screenshot({
    path: path.join(OUTPUT_DIR, `settings-${suffix}.png`),
    fullPage: true,
  });
}

test("cattura screenshot completi desktop", async ({ page }) => {
  ensureOutputDir();
  await page.setViewportSize({ width: 1440, height: 900 });
  await captureAllViews(page, "desktop");
});
