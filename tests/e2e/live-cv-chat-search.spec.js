const { test, expect } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const CV_PATH = path.join(process.cwd(), "Test-Mio-CV", "CV_Diego_Riccardi_IT.pdf");

// This is an end-to-end live-LLM + live-scan scenario. Skip by default to keep
// the default test run deterministic and fast. Enable with RUN_LIVE_LLM=1.
test.skip(
  process.env.RUN_LIVE_LLM !== "1",
  "live flow skipped — set RUN_LIVE_LLM=1 to enable"
);

async function postJson(page, url, payload) {
  return page.evaluate(async ({ endpoint, body }) => {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await response.text();

    let data = null;
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

async function getJson(page, url) {
  return page.evaluate(async (endpoint) => {
    const response = await fetch(endpoint);
    const text = await response.text();

    let data = null;
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
  }, url);
}

async function uploadCvViaUi(page, cvFilePath) {
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await expect(page.locator("#view-settings")).toHaveClass(/is-active/);

  await page.setInputFiles("#cvFile", cvFilePath);
  await page.locator("#cvForm button[type='submit']").click();
  await expect(page.locator("#cvSummary")).toContainText("profile_id", { timeout: 120000 });
}

function firstWords(value, max = 2) {
  return String(value || "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, max)
    .join(" ")
    .toLowerCase();
}

test("live flow: CV -> chat search guidance -> scan -> final recommendations", async ({ page }) => {
  test.setTimeout(10 * 60 * 1000);

  if (!fs.existsSync(CV_PATH)) {
    throw new Error("CV IT non trovato in Test-Mio-CV.");
  }

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.getByText("Job Finder")).toBeVisible();

  await uploadCvViaUi(page, CV_PATH);

  // Imposta una preferenza esplicita per rendere il consiglio finale davvero personalizzato.
  const prefRes = await postJson(page, "/api/chat", {
    session_id: "default",
    message: "Cerco full remote con RAL minima 35000, preferibilmente ruoli data o QA.",
  });
  expect(prefRes.ok).toBeTruthy();

  const guidanceRes = await postJson(page, "/api/chat", {
    session_id: "default",
    message: "In base al mio CV, dimmi quali lavori cercare e prepara la ricerca con parole chiave e location.",
  });

  expect(guidanceRes.ok).toBeTruthy();
  expect(String(guidanceRes.data.answer || "").trim().length).toBeGreaterThan(20);

  const action = guidanceRes.data.action || null;
  expect(action).toBeTruthy();
  expect(action.type).toBe("FILL_SCAN_FORM");

  const suggestedKeywords = Array.isArray(action.keywords) ? action.keywords.filter(Boolean) : [];
  const suggestedLocations = Array.isArray(action.locations) ? action.locations.filter(Boolean) : [];

  expect(suggestedKeywords.length).toBeGreaterThan(0);
  expect(suggestedLocations.length).toBeGreaterThan(0);

  const scanTerms = suggestedKeywords.slice(0, 2);
  const scanLocation = suggestedLocations[0];

  const scanRes = await postJson(page, "/api/scan", {
    search_terms: scanTerms,
    location: scanLocation,
    is_remote: true,
    sites: ["linkedin", "indeed"],
  });

  expect(scanRes.ok).toBeTruthy();
  expect(scanRes.data.status).toBe("complete");

  const jobsRes = await getJson(page, "/api/jobs?status=open&limit=50");
  expect(jobsRes.ok).toBeTruthy();

  const openJobs = Array.isArray(jobsRes.data.jobs) ? jobsRes.data.jobs : [];
  if (!openJobs.length) {
    throw new Error("La ricerca è terminata ma non ha prodotto lavori aperti.");
  }

  const recRes = await getJson(page, "/api/recommendations?limit=5");
  expect(recRes.ok).toBeTruthy();

  const recJobs = Array.isArray(recRes.data.jobs) ? recRes.data.jobs : [];
  if (!recJobs.length) {
    throw new Error("Nessuna raccomandazione disponibile dopo la ricerca.");
  }

  const topScore = Number(recJobs[0]?.punteggio_ai ?? 0);
  expect(topScore).toBeGreaterThan(0);

  const finalAdviceRes = await postJson(page, "/api/chat", {
    session_id: "default",
    message: "Ora consigliami in ordine di priorità a quali offerte candidarmi, con motivazione basata su CV e preferenze.",
  });

  expect(finalAdviceRes.ok).toBeTruthy();
  const finalAnswer = String(finalAdviceRes.data.answer || "");
  expect(finalAnswer.trim().length).toBeGreaterThan(30);

  const topJob = recJobs[0] || {};
  const topTitleHint = firstWords(topJob.titolo);
  const topCompanyHint = firstWords(topJob.azienda, 1);
  const normalizedAnswer = finalAnswer.toLowerCase();

  const referencesTopJob =
    (topTitleHint && normalizedAnswer.includes(topTitleHint)) ||
    (topCompanyHint && normalizedAnswer.includes(topCompanyHint));
  const hasRankedList = /(^|\n)\s*1\./.test(finalAnswer);

  expect(Boolean(referencesTopJob || hasRankedList)).toBeTruthy();

  await page.screenshot({
    path: path.join("test-results", "live-cv-chat-search-final.png"),
    fullPage: true,
  });

  const summary = {
    guidanceState: guidanceRes.data.chat_state,
    guidanceHasAction: Boolean(action),
    usedSearchTerms: scanTerms,
    usedLocation: scanLocation,
    scanComplete: scanRes.data,
    openJobsCount: openJobs.length,
    recommendedCount: recJobs.length,
    topRecommendation: {
      titolo: topJob.titolo || null,
      azienda: topJob.azienda || null,
      punteggio_ai: topJob.punteggio_ai || null,
      consiglio: topJob.consiglio || null,
    },
    finalAdvicePreview: finalAnswer.slice(0, 500),
  };

  console.log(`LIVE_FLOW_SUMMARY: ${JSON.stringify(summary)}`);

  await test.info().attach("live-cv-chat-search-summary", {
    contentType: "application/json",
    body: Buffer.from(JSON.stringify(summary, null, 2)),
  });
});
