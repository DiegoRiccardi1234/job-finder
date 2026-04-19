const { test, expect } = require("@playwright/test");

test("dashboard loads and navigation works", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Job Finder")).toBeVisible();
  await expect(page.getByRole("button", { name: "Dashboard" })).toBeVisible();

  await expect(page.getByText("AI Career Coach")).toBeVisible();

  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await expect(page.getByRole("heading", { name: "Profile Settings" })).toBeVisible();
});

test("settings contains multi-provider fields", async ({ page }) => {
  await page.goto("/");
  await page.locator(".topnav .nav-link[data-view='settings']").click();

  const primaryProvider = page.locator("#primaryProvider");
  if ((await primaryProvider.count()) === 0) {
    const showKeysFormBtn = page.locator("#showKeysFormBtn");
    if (await showKeysFormBtn.isVisible()) {
      await showKeysFormBtn.click();
    }
  }

  await expect(primaryProvider).toHaveCount(1);
  await expect(page.locator("#cerebrasKey")).toHaveCount(1);
  await expect(page.locator("#groqKey")).toHaveCount(1);
  await expect(page.locator("#openaiKey")).toHaveCount(1);
  await expect(page.locator("#anthropicKey")).toHaveCount(1);
  await expect(page.locator("#googleKey")).toHaveCount(1);

  const keysStatus = await page.evaluate(async () => {
    const response = await fetch("/api/providers/keys/status");
    const payload = await response.json();
    return payload.keys || {};
  });

  expect(Object.prototype.hasOwnProperty.call(keysStatus, "openai_configured")).toBeTruthy();
  expect(Object.prototype.hasOwnProperty.call(keysStatus, "anthropic_configured")).toBeTruthy();
  expect(Object.prototype.hasOwnProperty.call(keysStatus, "google_configured")).toBeTruthy();
});

test("chat flow returns assistant message", async ({ page }) => {
  await page.goto("/");

  const input = page.locator("#chatInput");
  await input.fill("consigliami i top lavori oggi");
  await page.locator("#chatForm button[type='submit']").click();

  await expect(page.locator(".chat-item.assistant .bubble").last()).toBeVisible();
});
