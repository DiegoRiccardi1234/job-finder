const { test, expect } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const OUTPUT_DIR = path.join(process.cwd(), "screenshots", "readme");
const CV_DIR = path.join(process.cwd(), "Test-Mio-CV");

function ensureOutputDir() {
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }
}

async function uploadCv(page, filePath, label) {
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await expect(page.locator("#view-settings")).toHaveClass(/is-active/);

  await page.setInputFiles("#cvFile", filePath);
  await page.locator("#cvForm button[type='submit']").click();
  await expect(page.locator("#cvSummary")).toContainText("profile_id", { timeout: 120000 });

  await page.screenshot({
    path: path.join(OUTPUT_DIR, `settings-cv-${label}.png`),
    fullPage: true,
  });
}

async function addManualJobs(page) {
  const jobs = [
    {
      titolo: "Senior Backend Python Engineer",
      azienda: "Alpine Data Labs",
      sede: "Remote - Europe",
      link: "https://example.com/jobs/backend-python",
      descrizione: "Sviluppo API FastAPI, integrazione LLM provider multipli, ottimizzazione SQLite/Postgres e pipeline dati.",
    },
    {
      titolo: "AI Product Engineer",
      azienda: "NextWave Talent",
      sede: "Milan, Italy",
      link: "https://example.com/jobs/ai-product",
      descrizione: "Costruzione funzionalita AI user-facing, valutazione prompt, monitoraggio qualitativo output e miglioramento UX.",
    },
    {
      titolo: "Data & Automation Specialist",
      azienda: "Nordic Operations",
      sede: "Hybrid - Turin",
      link: "https://example.com/jobs/data-automation",
      descrizione: "Automazione processi, scraping/ingestion offerte, scoring priorita candidature e reporting operativo.",
    },
  ];

  for (const payload of jobs) {
    const result = await page.evaluate(async (jobPayload) => {
      const response = await fetch("/api/jobs/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(jobPayload),
      });
      const text = await response.text();
      return { ok: response.ok, text };
    }, payload);

    if (!result.ok) {
      throw new Error(`Creazione annuncio manuale fallita: ${result.text}`);
    }
  }
}

async function captureRecommendations(page, label) {
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  

  const recPayload = await page.evaluate(async () => {
    const response = await fetch("/api/recommendations?limit=5");
    return await response.json();
  });

  const jobs = recPayload.jobs || [];
  if (!jobs.length) {
    throw new Error("Nessuna raccomandazione disponibile dopo il setup del profilo.");
  }

  fs.writeFileSync(
    path.join(OUTPUT_DIR, `recommended-${label}.json`),
    JSON.stringify(jobs, null, 2),
    "utf-8",
  );

  await page.screenshot({
    path: path.join(OUTPUT_DIR, `dashboard-recommendations-${label}.png`),
    fullPage: true,
  });

  await page.locator("#recommendationsGrid button[data-rec-action='detail']").first().click();
  await expect(page.locator("#detailTitle")).not.toContainText("Seleziona un lavoro");

  // Mostra funzionalità Genera Cover Letter se presente
  if (await page.locator("#generateCoverLetterBtn").isVisible()) {
    await page.locator("#generateCoverLetterBtn").click();
    await page.waitForTimeout(2000); // attendi un po' per mostrare la UI "Generazione in corso..." o completata
  }

  await page.screenshot({
    path: path.join(OUTPUT_DIR, `discover-top-job-${label}.png`),
    fullPage: true,
  });

  return jobs;
}

test("showcase desktop con CV IT + EN per README", async ({ page }) => {
  test.setTimeout(240000);
  ensureOutputDir();

  const cvIt = path.join(CV_DIR, "CV_Diego_Riccardi_IT.pdf");
  const cvEn = path.join(CV_DIR, "CV_Diego_Riccardi_EN.pdf");

  if (!fs.existsSync(cvIt) || !fs.existsSync(cvEn)) {
    throw new Error("CV IT/EN non trovati nella cartella Test-Mio-CV.");
  }

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.getByText("Job Finder")).toBeVisible();

  await uploadCv(page, cvIt, "it");
  await addManualJobs(page);
  await captureRecommendations(page, "it");

  await uploadCv(page, cvEn, "en");
  await captureRecommendations(page, "en");
});
