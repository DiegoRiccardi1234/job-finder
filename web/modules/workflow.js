// Operational context uses stored evidence only. Old scores and conversations
// have no profile fingerprint, so we never infer that they match a new CV.
import { api, setText, escapeHtml } from "./helpers.js";
import { t } from "./i18n.js";
import { fetchReadiness } from "./readiness.js";

function date(value) {
  if (!value) return t("workflow.never");
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString(document.documentElement.lang, { dateStyle: "short", timeStyle: "short" });
}

export async function refreshWorkflow() {
  try {
    const status = await api("/api/setup/status");
    setText("workflowLastScan", date(status.last_scan?.started_at));
    setText("workflowActiveCv", status.active_cv ? `${status.active_cv.source_name} · ${date(status.active_cv.created_at || status.active_cv.updated_at)}` : t("workflow.noCv"));
    setText("workflowGoal", status.search_goal || t("workflow.noGoal"));
    setText("workflowApplications", status.applications_pending_count ?? "—");
    setText("workflowNextAction", t(!status.cv_loaded ? "workflow.uploadCv" : !status.search_goal ? "workflow.setGoal" : "workflow.reviewSearch"));
    const scannedAt = status.last_scan?.started_at ? new Date(status.last_scan.started_at).getTime() : null;
    const cvAt = status.active_cv?.created_at ? new Date(status.active_cv.created_at).getTime() : null;
    const old = scannedAt !== null && Date.now() - scannedAt > 7 * 86400000;
    const previousCv = scannedAt !== null && cvAt !== null && scannedAt < cvAt;
    setText("workflowStale", t(previousCv ? "workflow.cvAfterScan" : "workflow.stale"));
    document.getElementById("workflowStale")?.classList.toggle("hidden", !old && !previousCv);
  } catch {
    setText("workflowLastScan", t("workflow.unavailable"));
  }
}

export async function updateSearchSource() {
  const report = await fetchReadiness();
  const source = report?.items?.find((item) => item.id === "search_terms")?.source;
  const labels = { manuale: "readiness.sourceManual", shortlist: "readiness.sourceShortlist", profile: "readiness.sourceProfile", last_scan: "readiness.sourceLastScan" };
  const label = labels[source] ? t(labels[source]) : t("workflow.suggestions");
  setText("searchTermsSource", `${t("workflow.termSource")}: ${label}. ${t("workflow.formWins")}`);
}

export function renderSearchReview(config) {
  const el = document.getElementById("searchLaunchReview");
  if (!el) return;
  const selections = [
    [t("settings.keywords"), config.terms?.join(", ")],
    [t("settings.locations"), config.location?.join(" · ")],
    [t("settings.country"), config.country],
    [t("scan.filters.workType"), [config.is_remote ? t("settings.remoteOnly") : "", ...(config.work_types || []).map((value) => t(`scan.filters.wt.${value}`))].filter(Boolean).join(", ")],
    [t("scan.filters.experience"), config.experience_levels?.map((value) => t(`scan.filters.exp.${value}`)).join(", ")],
    [t("scan.filters.jobType"), config.job_types?.map((value) => t(`scan.filters.jt.${value}`)).join(", ")],
    [t("workflow.portals"), config.sites?.join(", ")],
    [t("scan.minSalary"), config.min_salary ? String(config.min_salary) : ""],
  ];
  el.innerHTML = `<strong>${escapeHtml(t("workflow.searchReview"))}</strong><dl>${selections.filter(([, value]) => value).map(([name, value]) => `<div><dt>${escapeHtml(name)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl><p class="micro">${escapeHtml(t("workflow.formWins"))}</p>`;
}

export function markSearchEdited() {
  setText("searchTermsSource", t("workflow.currentForm"));
}
