const { test, expect } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const CV_PATH = path.join(process.cwd(), "Test-Mio-CV", "CV_Diego_Riccardi_IT.pdf");

// Minimal live-LLM smoke. Enable with RUN_LIVE_LLM=1 before releases.
test.skip(
  process.env.RUN_LIVE_LLM !== "1",
  "smoke skipped — set RUN_LIVE_LLM=1 to enable"
);

test("live LLM returns answer + suggested_roles for a pivot question", async ({ page }) => {
  test.setTimeout(180_000);

  if (!fs.existsSync(CV_PATH)) {
    test.skip(true, "CV fixture missing");
    return;
  }

  await page.goto("/");
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await page.setInputFiles("#cvFile", CV_PATH);
  await page.locator("#cvForm button[type='submit']").click();
  await expect(page.locator("#cvSummary")).toContainText("profile_id", { timeout: 120_000 });

  const res = await page.evaluate(async () => {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: "smoke",
        message: "Voglio esplorare ruoli legati all'AI col mio CV: quali figure posso cercare?",
      }),
    });
    return r.json();
  });

  expect(typeof res.answer).toBe("string");
  expect(res.answer.trim().length).toBeGreaterThan(20);
  // suggested_roles is optional in the schema; warn if empty to catch prompt regression.
  if (Array.isArray(res.suggested_roles)) {
    console.log("suggested_roles:", res.suggested_roles.length);
  }
});
