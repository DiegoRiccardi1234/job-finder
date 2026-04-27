const { test, expect } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const CV_PATH = path.join(process.cwd(), "Test-Mio-CV", "CV_Diego_Riccardi_IT.pdf");

// These tests hit a real LLM provider. Opt in via RUN_LIVE_LLM=1.
test.skip(
  process.env.RUN_LIVE_LLM !== "1",
  "live LLM tests skipped — set RUN_LIVE_LLM=1 to enable"
);

async function postJson(page, url, payload) {
  return page.evaluate(async ({ endpoint, body }) => {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const text = await response.text();
    let data = {};
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }

    return {
      ok: response.ok,
      status: response.status,
      data,
      text,
    };
  }, { endpoint: url, body: payload });
}

async function ensureCvLoaded(page) {
  if (!fs.existsSync(CV_PATH)) {
    throw new Error("CV IT non trovato in Test-Mio-CV.");
  }

  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await expect(page.locator("#view-settings")).toHaveClass(/is-active/);

  await page.setInputFiles("#cvFile", CV_PATH);
  await page.locator("#cvForm button[type='submit']").click();
  await expect(page.locator("#cvSummary")).toContainText("profile_id", { timeout: 120000 });
}

test("chat should suggest roles/search terms for 'che figure lavorative devo cercare con il mio cv'", async ({ page }) => {
  test.setTimeout(120000);

  await page.goto("/");
  await ensureCvLoaded(page);

  const res = await postJson(page, "/api/chat", {
    session_id: "default",
    message: "che figure lavorative devo cercare con il mio cv?",
  });

  expect(res.ok).toBeTruthy();
  expect(res.data.action).toBeTruthy();
  expect(res.data.action.type).toBe("FILL_SCAN_FORM");
  expect(Array.isArray(res.data.action.keywords)).toBeTruthy();
  expect(res.data.action.keywords.length).toBeGreaterThan(0);

  const answer = String(res.data.answer || "").toLowerCase();
  expect(answer).not.toContain("messaggio salvato");
});

test("chat should suggest roles even when user asks 'quali figure lavorative sono adatte al mio cv'", async ({ page }) => {
  test.setTimeout(120000);

  await page.goto("/");
  await ensureCvLoaded(page);

  const res = await postJson(page, "/api/chat", {
    session_id: "default",
    message: "In base al mio CV, quali figure lavorative sono adatte a me?",
  });

  expect(res.ok).toBeTruthy();
  expect(res.data.action).toBeTruthy();
  expect(res.data.action.type).toBe("FILL_SCAN_FORM");
  expect(Array.isArray(res.data.action.keywords)).toBeTruthy();
  expect(res.data.action.keywords.length).toBeGreaterThan(0);

  const answer = String(res.data.answer || "").toLowerCase();
  expect(answer).not.toContain("messaggio salvato");
});
