// Screenshot capture against a pre-seeded demo database.
//
// Prereq: run `python scripts/seed_demo.py --db data/demo.db --force` first,
// then launch the webapp with `SEARCHER_DB_PATH=data/demo.db`.
//
// Produces the 4 README screenshots:
// dashboard, job-search wizard, chat view, scan progress.

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

  // 2. Job Search (flat layout): profile-derived role chips + scan form.
  await page.locator(".topnav .nav-link[data-view='job-search']").click();
  await page.waitForTimeout(700);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "job-search-en.png");

  // 3. Chat view — inject a conversation so bubbles are visible
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await page.evaluate(() => {
    const box = document.getElementById("chatBox");
    if (!box) return;
    box.innerHTML = "";
    const mk = (role, html, extra = "") => {
      const div = document.createElement("div");
      div.className = `chat-item ${role}`;
      div.innerHTML = `<div class="role">${role === "user" ? "You" : "AI Coach"}</div><div class="bubble">${html}</div>${extra}`;
      box.appendChild(div);
    };
    mk("user", "I'm a Python developer with 3 years of experience. What roles should I target?");
    mk("assistant", "Based on your CV I see strong <b>backend</b> and <b>data engineering</b> skills. I'd target these roles first:", `
      <div class="role-pill-row">
        <button class="role-pill">Backend Engineer (Python)</button>
        <button class="role-pill">Data Engineer</button>
        <button class="role-pill">ML Engineer</button>
        <button class="role-pill">Platform Engineer</button>
      </div>`);
    mk("user", "Focus on Milan, remote ok. Find jobs matching my profile.");
    mk("assistant", "Great — I'll search for <b>Backend Engineer</b> and <b>Data Engineer</b> in <b>Milan</b> plus remote. Launching scan now... you'll see live progress in the overlay.");
    box.scrollTop = box.scrollHeight;
  });
  await page.waitForTimeout(400);
  await shot(page, "chat-view-en.png");

  // 4. Scan progress feed — show the overlay + feed mid-analysis
  await page.evaluate(() => {
    const overlay = document.getElementById("scanOverlay");
    const fill = document.getElementById("scanProgressFill");
    const text = document.getElementById("scanProgressText");
    const feed = document.getElementById("scanFeed");
    if (!overlay || !feed) return;
    overlay.style.display = "flex";
    fill.style.width = "62%";
    text.textContent = "Analyzed: Senior Data Engineer @ Satispay — Score 8/10";
    const rows = [
      { icon: "travel_explore", text: "Searching for: <b>Backend Engineer, Data Engineer</b>", chip: null },
      { icon: "manage_search", text: "Found 24 ads on <b>LinkedIn</b>", chip: null },
      { icon: "manage_search", text: "Found 18 ads on <b>Indeed</b>", chip: null },
      { icon: "check_circle", text: "<b>Backend Python Engineer</b> @ Nexi", chip: { label: "7/10", cls: "score-high" } },
      { icon: "check_circle", text: "<b>Platform Engineer</b> @ Bending Spoons", chip: { label: "6/10", cls: "score-mid" } },
      { icon: "check_circle", text: "<b>Senior Data Engineer</b> @ Satispay", chip: { label: "8/10", cls: "score-high" } },
      { icon: "check_circle", text: "<b>Junior Developer</b> @ StartupX", chip: { label: "3/10", cls: "score-low" } },
      { icon: "check_circle", text: "<b>ML Engineer</b> @ Prima", chip: { label: "7/10", cls: "score-high" } },
    ];
    feed.innerHTML = "";
    rows.forEach((r) => {
      const li = document.createElement("li");
      const chipHtml = r.chip ? `<span class="feed-chip ${r.chip.cls}">${r.chip.label}</span>` : "";
      li.innerHTML = `<span class="material-symbols-outlined feed-icon">${r.icon}</span><span class="feed-text">${r.text}</span>${chipHtml}`;
      feed.appendChild(li);
    });
    feed.scrollTop = feed.scrollHeight;
  });
  await page.waitForTimeout(400);
  await shot(page, "scan-progress-en.png");
  await page.evaluate(() => {
    const overlay = document.getElementById("scanOverlay");
    if (overlay) overlay.style.display = "none";
  });
});
