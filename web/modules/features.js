// Optional, toggleable features: interview-prep / resume-tailoring generation,
// skill-gap panel, and the scheduled auto-scan controls + highlights banner.
// Pure helpers are exported for app.js (loadHealth / showJobDetail / bootstrap);
// initFeatures() wires the DOM event listeners (loadJobs injected to avoid a
// circular import).
import { api, escapeHtml, setText, showToast } from "./helpers.js";
import { t } from "./i18n.js";
import { appState } from "./state.js";

export function readFeatureFlags(prefs) {
  const off = (v) => v === "0" || v === "false" || v === "off";
  return {
    interview_prep: !off(prefs.feature_interview_prep),
    resume_tailoring: !off(prefs.feature_resume_tailoring),
    skill_gap: !off(prefs.feature_skill_gap),
  };
}

export async function loadSkillGap() {
  const section = document.getElementById("skillGapSection");
  if (!section) return;
  if (appState.featureFlags.skill_gap === false) {
    section.style.display = "none";
    return;
  }
  try {
    const data = await api("/api/skill-gap");
    renderSkillGap(data);
    section.style.display = "block";
  } catch (error) {
    section.style.display = "none";
  }
}

function renderSkillGap(data) {
  const bars = document.getElementById("skillGapBars");
  const empty = document.getElementById("skillGapEmpty");
  if (!bars) return;
  const gaps = (data && data.gaps) || [];
  bars.innerHTML = "";
  if (!gaps.length) {
    if (empty) empty.style.display = "block";
    return;
  }
  if (empty) empty.style.display = "none";
  const max = (data && data.max_count) || 1;
  for (const g of gaps) {
    const pct = Math.max(8, Math.round((g.count / max) * 100));
    const row = document.createElement("div");
    row.className = "skill-gap-row";
    row.innerHTML =
      `<span class="skill-gap-label">${escapeHtml(g.skill)}</span>` +
      `<span class="skill-gap-track"><span class="skill-gap-fill" style="width:${pct}%"></span></span>` +
      `<span class="skill-gap-count">${g.count}</span>`;
    bars.appendChild(row);
  }
}

export function syncFeatureToggles() {
  document.querySelectorAll("#featureToggleList input[data-feature]").forEach((cb) => {
    const key = cb.dataset.feature;
    if (key in appState.featureFlags) cb.checked = appState.featureFlags[key] !== false;
  });
}

export function setupGenerationButton({ btnId, boxId, outId, enabled, saved }) {
  const btn = document.getElementById(btnId);
  const box = document.getElementById(boxId);
  const out = document.getElementById(outId);
  if (!btn || !box || !out) return;
  btn.style.display = enabled ? "inline-block" : "none";
  if (saved) {
    out.textContent = saved;
    box.style.display = "block";
  } else {
    out.textContent = "";
    box.style.display = "none";
  }
}

async function runGeneration({ btn, box, out, endpoint, field }) {
  if (!appState.selectedJobId) return;
  box.style.display = "block";
  out.textContent = t("toast.generating");
  btn.disabled = true;
  const originalLabel = btn.innerHTML;
  btn.innerHTML = `<span class="spinner-inline"></span> ${t("toast.generating")}`;
  try {
    const payload = await api(`/api/jobs/${appState.selectedJobId}/${endpoint}`, { method: "POST" });
    out.textContent = payload[field] || t("toast.noResult");
  } catch (error) {
    out.textContent = `${t("toast.genError")}: ${error.message}`;
    showToast(`${t("toast.genError")}: ${error.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalLabel;
  }
}

export async function loadSchedulerStatus() {
  try {
    const st = await api("/api/scheduler/status");
    const en = document.getElementById("autoscanEnabled");
    const iv = document.getElementById("autoscanInterval");
    const th = document.getElementById("autoscanThreshold");
    if (en) en.checked = !!st.enabled;
    if (iv && st.interval_hours) iv.value = st.interval_hours;
    if (th && typeof st.threshold === "number") th.value = st.threshold;
    renderAutoscanBanner(st.pending);
  } catch (error) {
    /* scheduler is optional; ignore when unavailable */
  }
}

function renderAutoscanBanner(pending) {
  const banner = document.getElementById("autoscanBanner");
  if (!banner) return;
  if (pending && pending.count > 0) {
    setText(
      "autoscanBannerText",
      t("autoscan.bannerText", { count: pending.count, threshold: pending.threshold }),
    );
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

async function saveSchedulerConfig(patch) {
  try {
    await api("/api/scheduler/config", { method: "POST", body: JSON.stringify(patch) });
  } catch (error) {
    showToast(`${t("toast.actionError")}: ${error.message}`, "error");
  }
}

export function initFeatures({ loadJobs }) {
  const ipBtn = document.getElementById("generateInterviewPrepBtn");
  if (ipBtn) {
    ipBtn.addEventListener("click", () =>
      runGeneration({
        btn: ipBtn,
        box: document.getElementById("interviewPrepBox"),
        out: document.getElementById("interviewPrepOutput"),
        endpoint: "interview-prep",
        field: "interview_prep",
      }),
    );
  }

  const trBtn = document.getElementById("generateTailoredResumeBtn");
  if (trBtn) {
    trBtn.addEventListener("click", () =>
      runGeneration({
        btn: trBtn,
        box: document.getElementById("tailoredResumeBox"),
        out: document.getElementById("tailoredResumeOutput"),
        endpoint: "tailored-resume",
        field: "tailored_resume",
      }),
    );
  }

  const copyTrBtn = document.getElementById("copyTailoredResumeBtn");
  if (copyTrBtn) {
    copyTrBtn.addEventListener("click", async () => {
      const text = document.getElementById("tailoredResumeOutput").textContent || "";
      try {
        await navigator.clipboard.writeText(text);
        setText("trCopyStatus", t("offcanvas.copied") || "Copied");
        setTimeout(() => setText("trCopyStatus", ""), 1500);
      } catch (error) {
        showToast(String(error), "error");
      }
    });
  }

  const featureToggleList = document.getElementById("featureToggleList");
  if (featureToggleList) {
    featureToggleList.addEventListener("change", async (event) => {
      const cb = event.target.closest("input[data-feature]");
      if (!cb) return;
      const key = cb.dataset.feature;
      appState.featureFlags[key] = cb.checked;
      try {
        await api("/api/preferences", {
          method: "POST",
          body: JSON.stringify({ key: `feature_${key}`, value: cb.checked ? "1" : "0" }),
        });
        showToast(t("settings.features.saved") || "Saved", "info");
        if (key === "skill_gap") loadSkillGap();
      } catch (error) {
        cb.checked = !cb.checked;
        appState.featureFlags[key] = cb.checked;
        showToast(`${t("toast.actionError")}: ${error.message}`, "error");
      }
    });
  }

  const refreshSkillGapBtn = document.getElementById("refreshSkillGapBtn");
  if (refreshSkillGapBtn) {
    refreshSkillGapBtn.addEventListener("click", loadSkillGap);
  }

  const autoscanEnabledEl = document.getElementById("autoscanEnabled");
  if (autoscanEnabledEl) {
    autoscanEnabledEl.addEventListener("change", () =>
      saveSchedulerConfig({ enabled: autoscanEnabledEl.checked }),
    );
  }
  const autoscanIntervalEl = document.getElementById("autoscanInterval");
  if (autoscanIntervalEl) {
    autoscanIntervalEl.addEventListener("change", () =>
      saveSchedulerConfig({ interval_hours: parseInt(autoscanIntervalEl.value, 10) || 12 }),
    );
  }
  const autoscanThresholdEl = document.getElementById("autoscanThreshold");
  if (autoscanThresholdEl) {
    autoscanThresholdEl.addEventListener("change", () =>
      saveSchedulerConfig({ threshold: parseInt(autoscanThresholdEl.value, 10) || 0 }),
    );
  }
  const autoscanRunNowEl = document.getElementById("autoscanRunNow");
  if (autoscanRunNowEl) {
    autoscanRunNowEl.addEventListener("click", async () => {
      try {
        await api("/api/scheduler/run-now", { method: "POST" });
        showToast(t("settings.features.autoscanStarted") || "Auto-scan started", "info");
      } catch (error) {
        showToast(`${t("toast.actionError")}: ${error.message}`, "error");
      }
    });
  }
  const autoscanBannerDismissEl = document.getElementById("autoscanBannerDismiss");
  if (autoscanBannerDismissEl) {
    autoscanBannerDismissEl.addEventListener("click", async () => {
      document.getElementById("autoscanBanner").classList.add("hidden");
      try {
        await api("/api/scheduler/dismiss", { method: "POST" });
      } catch (error) {
        /* best-effort dismiss */
      }
    });
  }
  const autoscanBannerViewEl = document.getElementById("autoscanBannerView");
  if (autoscanBannerViewEl) {
    autoscanBannerViewEl.addEventListener("click", () => {
      const onlyNew = document.getElementById("onlyNew");
      if (onlyNew) {
        onlyNew.checked = true;
        loadJobs();
      }
      const jobsSection = document.querySelector(".jobs-section");
      if (jobsSection) jobsSection.scrollIntoView({ behavior: "smooth" });
    });
  }
}
