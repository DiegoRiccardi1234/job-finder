// Dashboard analytics charts (Chart.js). Self-contained: owns its three chart
// instances and re-renders them from /api/analytics.
import { api } from "./helpers.js";
import { t } from "./i18n.js";

let statusChart = null;
let scoreChart = null;
let topCompaniesChart = null;

export async function loadAnalytics() {
  try {
    const data = await api("/api/analytics");

    const statusCtx = document.getElementById("statusChart");
    if (statusCtx && data.jobs_by_status) {
      if (statusChart) statusChart.destroy();
      statusChart = new Chart(statusCtx, {
        type: "doughnut",
        data: {
          labels: Object.keys(data.jobs_by_status),
          datasets: [
            {
              data: Object.values(data.jobs_by_status),
              backgroundColor: ["#198754", "#dc3545", "#ffc107", "#0d6efd", "#6c757d"],
            },
          ],
        },
        options: { responsive: true },
      });
    }

    const scoreCtx = document.getElementById("scoreChart");
    if (scoreCtx && data.score_distribution) {
      if (scoreChart) scoreChart.destroy();
      scoreChart = new Chart(scoreCtx, {
        type: "bar",
        data: {
          labels: Object.keys(data.score_distribution),
          datasets: [
            {
              label: "Match Score",
              data: Object.values(data.score_distribution),
              backgroundColor: "#0d6efd",
            },
          ],
        },
        options: { responsive: true, scales: { y: { beginAtZero: true } } },
      });
    }

    const companiesCtx = document.getElementById("topCompaniesChart");
    if (companiesCtx && Array.isArray(data.top_companies)) {
      if (topCompaniesChart) topCompaniesChart.destroy();
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
    }
  } catch (e) {
    console.error("Failed to load analytics", e);
  }
}
