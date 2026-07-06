// Dashboard analytics charts (Chart.js). Self-contained: owns its three chart
// instances and re-renders them from /api/analytics. Each card shows a
// "no data yet" note instead of an empty/broken canvas when its series is empty.
import { api } from "./helpers.js";
import { t } from "./i18n.js";

let statusChart = null;
let scoreChart = null;
let topCompaniesChart = null;

function _card(canvas) {
  return canvas.closest(".analytics-card") || canvas.parentElement;
}

function _emptyCard(canvas) {
  if (!canvas) return;
  canvas.style.display = "none";
  const card = _card(canvas);
  if (!card) return;
  let note = card.querySelector(".analytics-empty");
  if (!note) {
    note = document.createElement("p");
    note.className = "analytics-empty";
    card.appendChild(note);
  }
  note.textContent = t("analytics.noData") || "No data yet";
}

function _showCanvas(canvas) {
  if (!canvas) return;
  canvas.style.display = "";
  const card = _card(canvas);
  const note = card && card.querySelector(".analytics-empty");
  if (note) note.remove();
}

function _hasData(obj) {
  const vals = Object.values(obj || {});
  return vals.length > 0 && vals.some((v) => Number(v) > 0);
}

export async function loadAnalytics() {
  try {
    const data = await api("/api/analytics");

    const statusCtx = document.getElementById("statusChart");
    if (statusCtx) {
      if (statusChart) statusChart.destroy();
      if (_hasData(data.jobs_by_status)) {
        _showCanvas(statusCtx);
        statusChart = new Chart(statusCtx, {
          type: "doughnut",
          data: {
            labels: Object.keys(data.jobs_by_status).map((k) => t(`jobs.status.${k}`) || k),
            datasets: [
              {
                data: Object.values(data.jobs_by_status),
                backgroundColor: ["#198754", "#dc3545", "#ffc107", "#0d6efd", "#6c757d"],
              },
            ],
          },
          options: { responsive: true },
        });
      } else {
        _emptyCard(statusCtx);
      }
    }

    const scoreCtx = document.getElementById("scoreChart");
    if (scoreCtx) {
      if (scoreChart) scoreChart.destroy();
      if (_hasData(data.score_distribution)) {
        _showCanvas(scoreCtx);
        scoreChart = new Chart(scoreCtx, {
          type: "bar",
          data: {
            labels: Object.keys(data.score_distribution),
            datasets: [
              {
                label: t("analytics.matchScore") || "Match Score",
                data: Object.values(data.score_distribution),
                backgroundColor: "#0d6efd",
              },
            ],
          },
          options: { responsive: true, scales: { y: { beginAtZero: true } } },
        });
      } else {
        _emptyCard(scoreCtx);
      }
    }

    const companiesCtx = document.getElementById("topCompaniesChart");
    if (companiesCtx) {
      if (topCompaniesChart) topCompaniesChart.destroy();
      if (Array.isArray(data.top_companies) && data.top_companies.length) {
        _showCanvas(companiesCtx);
        topCompaniesChart = new Chart(companiesCtx, {
          type: "bar",
          data: {
            labels: data.top_companies.map((c) => c.company),
            datasets: [
              {
                label: t("analytics.topCompanies") || "Top Companies",
                data: data.top_companies.map((c) => c.count),
                backgroundColor: "#635bff",
              },
            ],
          },
          options: {
            indexAxis: "y",
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { x: { beginAtZero: true } },
          },
        });
      } else {
        _emptyCard(companiesCtx);
      }
    }
  } catch (e) {
    console.error("Failed to load analytics", e);
  }
}
