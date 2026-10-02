const { test, expect } = require("@playwright/test");

// UI contracts only: every API call is mocked, so no scan, provider request,
// mailbox access or writes to a working archive are possible in these tests.
async function isolate(page, options = {}) {
  const writes = [];
  const job = { id: 71, titolo: "Junior automation specialist", azienda: "Demo company", sede: "Torino", status: "open", fonte: "manual", punteggio_ai: 7, analysis: {}, description: "Build API integrations with mentoring.", ...options.job };
  const profile = { id: 4, source_name: "Updated-demo.pdf", created_at: options.cvCreated || "2026-10-02T16:00:00", markdown: "Demo\nPython SQL", summary_json: { skills: ["Python", "SQL"], preferred_roles: ["Junior automation specialist"] } };
  const facts = { years_experience: 1, education_level: "triennale", education_levels: ["triennale"], degree_fields: ["informatica"], grade: 95, base_cities: ["Torino"], work_modes: ["onsite", "hybrid"], driving_licence: null, protected_category: null, sources: { work_rule: "cv" }, missing: [] };
  const status = { configured: true, state: "ok", enabled: false, last_run_ts: "1790950000", last_success_ts: "", body_mode: "never", ...options.mail };
  await page.addInitScript(() => { localStorage.setItem("language", "it"); localStorage.setItem("tutorialSeen", "1"); });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET") writes.push({ path, method: request.method(), body: request.headers()["content-type"]?.includes("application/json") ? request.postDataJSON() : null });
    let payload = {};
    if (path === "/api/health") payload = { provider: { active_provider: options.noProvider ? "none" : "google", active_model: "demo", available: !options.noProvider }, keys: {}, preferences: { onboarding_goal: "Junior integrations with mentoring", last_scan_terms: '["old role"]' } };
    if (path === "/api/providers/keys/status") payload = { keys: { primary_provider: "cerebras", cerebras_configured: false }, provider: { active_provider: "none", available: false, providers: { cerebras: { configured: false, available: false } } } };
    if (path === "/api/setup/status") payload = { cv_loaded: true, provider_configured: !options.noProvider, active_cv: profile, search_goal: "Junior integrations with mentoring", last_scan: { started_at: options.lastScan || "2026-08-26T12:00:00" }, applications_pending_count: 2 };
    if (path === "/api/profile/readiness") payload = { items: [{ id: "search_terms", status: "ok", source: "profile", group: "search" }], suggested_terms: ["Junior automation specialist"], suggested_locations: ["Torino"] };
    if (path === "/api/profile") payload = { profile };
    if (path === "/api/profiles") payload = { profiles: [profile], active_profile_id: 4 };
    if (path === "/api/profile/matching-facts") payload = facts;
    if (path === "/api/jobs") payload = { jobs: [job], total: 1, counts: {} };
    if (path === "/api/jobs/71") { if (request.method() === "DELETE") job.status = "archived"; payload = { job, actions: [] }; }
    if (path === "/api/jobs/71/restore") { job.status = "open"; payload = { ok: true, status: "open" }; }
    if (path === "/api/mail/status") payload = status;
    if (path === "/api/mail/review") payload = { items: options.review || [] };
    if (path === "/api/saved-searches") payload = { searches: options.searches || [] };
    if (path === "/api/reminders") payload = options.reminders || { reminders: [], stale: [], count: 0 };
    if (path === "/api/chat/sessions") payload = { sessions: [] };
    await route.fulfill({ json: payload });
  });
  await page.goto("/");
  await expect(page.locator("#keywordsContainer")).toContainText("Junior automation specialist");
  return { writes, job };
}

test("dashboard exposes active evidence and search terms are reviewable", async ({ page }) => {
  const missing = []; const errors = [];
  page.on("console", (message) => { if (message.text().includes("missing translation")) missing.push(message.text()); });
  page.on("pageerror", (error) => errors.push(String(error)));
  await isolate(page);
  await expect(page.locator("#workflowActiveCv")).toContainText("Updated-demo.pdf");
  await expect(page.locator("#workflowGoal")).toContainText("mentoring");
  await expect(page.locator("#workflowStale")).toBeVisible();
  await page.locator(".topnav [data-view='job-search']").click();
  await expect(page.locator("#searchLaunchReview")).toContainText("Junior automation specialist");
  await expect(page.locator("#searchLaunchReview")).toContainText("Torino");
  await page.locator("#restoreLastScanBtn").click();
  await expect(page.locator("#keywordsContainer")).toContainText("old role");
  await expect(page.locator("#keywordsContainer")).not.toContainText("Junior automation specialist");
  await page.locator("#useProfileTermsBtn").click();
  await expect(page.locator("#keywordsContainer")).toContainText("Junior automation specialist");
  await expect(page.locator("#keywordsContainer")).not.toContainText("old role");
  expect(missing).toEqual([]); expect(errors).toEqual([]);
});

test("CV confirmation submits only fields changed by the user", async ({ page }) => {
  const { writes } = await isolate(page);
  const showReview = () => page.evaluate(async () => {
    const source = await fetch("/web/app.js").then((response) => response.text());
    const url = source.match(/from ["']([^"']+cv_review\.js\?v=[^"']+)["']/)[1];
    await (await import(new URL(url, new URL("/web/app.js", location.href)))).showCvReview();
  });
  await showReview();
  const modal = page.locator("#cvReviewModal");
  await expect(modal).toBeVisible();
  await expect(modal).toHaveAttribute("aria-labelledby", "cvReviewTitle");
  await modal.locator("#cvrConfirmBtn").click();
  expect(writes.filter((write) => write.path === "/api/profile" && write.method === "PATCH")).toEqual([]);
  await showReview();
  await modal.locator("#cvrYears").fill("1.5");
  await modal.locator("#cvrConfirmBtn").click();
  expect(writes.filter((write) => write.path === "/api/profile" && write.method === "PATCH").map((write) => write.body)).toEqual([{ years_experience: 1.5 }]);
});

test("a CV uploaded after a recent scan warns about the previous profile", async ({ page }) => {
  await isolate(page, { lastScan: new Date(Date.now() - 3600000).toISOString(), cvCreated: new Date().toISOString() });
  await expect(page.locator("#workflowStale")).toBeVisible();
  await expect(page.locator("#workflowStale")).toContainText("CV attivo è successivo");
});

test("launched scan sends exactly the visible terms, pending text, locations and filters", async ({ page }) => {
  await isolate(page);
  let scan;
  await page.route("**/api/scan/stream?**", async (route) => {
    scan = new URL(route.request().url()).searchParams;
    await route.fulfill({ contentType: "text/event-stream", body: 'data: {"status":"complete","totale_nuovi":0,"totale_analizzati":0}\n\n' });
  });
  await page.locator(".topnav [data-view='job-search']").click();
  await page.locator("#keywordsInput").fill("Junior integration developer");
  await page.locator("#locationsInput").fill("Roma");
  await page.locator(".scan-advanced > summary").click();
  await page.locator("label.pill-toggle").filter({ has: page.locator("input[name='scanExperience'][value='entry']") }).click();
  await expect(page.locator("input[name='scanExperience'][value='entry']")).toBeChecked();
  await expect(page.locator("#searchLaunchReview")).toContainText("Junior integration developer");
  await expect(page.locator("#searchLaunchReview")).toContainText("Roma");
  await page.locator("#scanForm button[type='submit']").click();
  await expect.poll(() => scan?.get("search_terms")).toBe("Junior automation specialist, Junior integration developer");
  expect(scan.get("locations")).toBe("Torino|Roma");
  expect(scan.get("experience_levels")).toBe("entry");
  expect(scan.get("sites")).toBe("linkedin,indeed");
});

test("closed drawer is inert, open drawer traps focus and restores it on Escape", async ({ page }) => {
  await isolate(page);
  const drawer = page.locator("#jobDetailInline");
  await expect(drawer).toBeHidden();
  await expect(drawer).toHaveAttribute("inert", "");
  await page.locator(".topnav [data-view='jobs']").click();
  const trigger = page.locator("button[data-detail-id='71']");
  await trigger.click();
  await expect(drawer).toBeVisible();
  await expect(drawer).toHaveAttribute("aria-hidden", "false");
  await expect(page.locator("#closeDetailBtn")).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect.poll(() => page.evaluate(() => document.getElementById("jobDetailInline").contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("mobile menu stays hidden with provider banner and closes via Escape", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await isolate(page, { noProvider: true });
  const banner = page.locator("#noApiKeyBanner");
  await expect(banner).toBeVisible();
  expect(await banner.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  const links = await banner.locator("a").all();
  for (const link of links) {
    await expect(link).toBeVisible();
    const rect = await link.boundingBox();
    expect(rect.x).toBeGreaterThanOrEqual(0);
    expect(rect.x + rect.width).toBeLessThanOrEqual(391);
  }
  expect((await banner.locator(".no-key-banner-text").boundingBox()).width).toBeGreaterThan(200);
  await expect(page.locator("#topnav")).toBeHidden();
  await expect(page.locator("#navToggle")).toHaveAttribute("aria-expanded", "false");
  await page.locator("#navToggle").click();
  await expect(page.locator("#topnav")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator("#topnav")).toBeHidden();
  await expect(page.locator("#navToggle")).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("archive replaces destructive delete and restores previous state", async ({ page }) => {
  const { writes } = await isolate(page);
  await page.locator(".topnav [data-view='jobs']").click();
  await page.locator("[data-archive-id='71']").click();
  await expect(page.locator("[data-archive-id='71']")).toHaveAttribute("data-restore", "true");
  await page.locator("[data-archive-id='71']").click();
  await expect(page.locator("[data-archive-id='71']")).toHaveAttribute("data-restore", "false");
  expect(writes.some((write) => write.path === "/api/jobs/71/restore" && write.method === "POST")).toBe(true);
});

test("saved search loads for review without starting a scan", async ({ page }) => {
  const streams = [];
  page.on("request", (request) => { if (request.url().includes("/api/scan/stream")) streams.push(request.url()); });
  await isolate(page, { searches: [{ id: 2, name: "Demo Roma", config: { terms: ["Junior integration developer"], location: ["Roma"], sites: ["linkedin"], work_types: ["onsite"] } }] });
  await page.locator(".topnav [data-view='job-search']").click();
  await page.locator(".saved-search-run").click();
  await expect(page.locator("#searchLaunchReview")).toContainText("Roma");
  await expect(page.locator("#searchLaunchReview")).toContainText("Junior integration developer");
  expect(streams).toEqual([]);
});

test("rejection mail cannot create a new application and connection is distinct from successful read", async ({ page }) => {
  await isolate(page, { review: [{ review_id: 9, kind: "rejection", company: "Demo", role: "Junior automation", candidates: [{ job_id: 71, titolo: "Junior automation", azienda: "Demo" }] }] });
  await page.locator(".topnav [data-view='mail']").click();
  await expect(page.locator("[data-review-row='9']")).toContainText("Esito negativo da verificare");
  await expect(page.locator("[data-review-row='9'] input[value='create']")).toHaveCount(0);
  await expect(page.locator("#mailOperationalStatus")).toContainText("Mai eseguita");
  await expect(page.locator("#mailOperationalStatus")).toContainText("non dimostra");
});

test("unconfigured default provider is never queried and optional flag tooltips do not warn", async ({ page }) => {
  const models = []; const missing = [];
  page.on("request", (request) => { if (/\/api\/providers\/[^/]+\/models/.test(request.url())) models.push(request.url()); });
  page.on("console", (message) => { if (message.text().includes("missing translation")) missing.push(message.text()); });
  await isolate(page, { noProvider: true, job: { flags: ["non_valutato", "descrizione_breve"] } });
  await page.locator(".topnav [data-view='settings']").click();
  await expect(page.locator("#scoringModelSelect")).toBeDisabled();
  await expect(page.locator("#scoringModelSelect")).toContainText(/API key/i);
  expect(models).toEqual([]);
  expect(missing).toEqual([]);
});

test("dashboard previews eight reminders and can expand every stored reminder", async ({ page }) => {
  const stale = Array.from({ length: 40 }, (_, index) => ({ job_id: index + 100, titolo: `Demo application ${index}`, azienda: "Demo", status: "applied", since: "2026-08-01" }));
  await isolate(page, { reminders: { reminders: [{ job_id: 71, titolo: "Important follow-up", due_at: "2026-10-03" }], stale, count: 41 } });
  await expect(page.locator("#remindersList .reminder-item")).toHaveCount(8);
  await expect(page.locator("#remindersList .reminder-item").first()).toContainText("Important follow-up");
  await expect(page.locator("#remindersNavBadge")).toHaveText("1");
  await expect(page.locator("#remindersNavBadge")).toHaveAttribute("title", "Promemoria e scadenze");
  await page.locator("#showAllRemindersBtn").click();
  await expect(page.locator("#remindersList .reminder-item")).toHaveCount(41);
  await expect(page.locator("#showAllRemindersBtn")).toHaveAttribute("aria-expanded", "true");
  await page.locator("#showAllRemindersBtn").click();
  await expect(page.locator("#remindersList .reminder-item")).toHaveCount(8);
});

test("stale applications remain in the dashboard without raising the scheduled-reminder badge", async ({ page }) => {
  const stale = Array.from({ length: 183 }, (_, index) => ({ job_id: index + 100, titolo: `Demo application ${index}`, azienda: "Demo", status: "applied", since: "2026-08-01" }));
  await isolate(page, { reminders: { reminders: [], stale, count: 183 }, review: [{ review_id: 9, kind: "rejection", company: "Demo", candidates: [] }] });
  await expect(page.locator("#remindersList .reminder-item")).toHaveCount(8);
  await expect(page.locator("#showAllRemindersBtn")).toContainText("183");
  await expect(page.locator("#remindersNavBadge")).toBeHidden();
  await expect(page.locator("#mailNavBadge")).toHaveText("1");
  await expect(page.locator("#mailNavBadge")).toHaveClass(/nav-badge--review/);
  await expect(page.locator("#mailNavBadge")).toHaveAttribute("aria-label", "Messaggi da revisionare: 1");
});
