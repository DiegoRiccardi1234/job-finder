import { api, escapeHtml, setText, truncate, showToast, renderCoachMarkdown } from "./modules/helpers.js";
import { initTheme } from "./modules/theme.js";
import { loadShortlist as _loadShortlistApi, addToShortlist as _addToShortlistApi } from "./modules/shortlist.js";
import { initI18n, t, loadLanguage, getCurrentLang, onLanguageChange } from "./modules/i18n.js";
import { loadProfile as loadProfileView, bindProfileEvents, addRolesToProfile } from "./modules/profile.js";
import { appState } from "./modules/state.js";
import { loadAnalytics } from "./modules/analytics.js";
import {
  readFeatureFlags,
  syncFeatureToggles,
  setupGenerationButton,
  loadSkillGap,
  loadSchedulerStatus,
  initFeatures,
} from "./modules/features.js";
import {
  checkForUpdate,
  populateSystemInfo,
  wireSystemSettings,
  wirePostScanModal,
  showPostScanModal,
} from "./modules/update.js";
import {
  renderProviderCards,
  normalizeKeyStatus,
  setPrimaryProviderValue,
  updateProvidersMetadata,
  populateModelOptions,
  onSaveProviderKey,
  onSetPrimaryProvider,
  fetchAndRenderProviderModels,
  populateChatModelSelector,
  maybeOfferPersistChatOverride,
  setProviderDeps,
} from "./modules/providers.js";

initTheme();

// Inject core refresh callbacks the provider module needs after a key save
// (avoids a circular import). loadHealth/loadKeysStatus/refreshOnboardingPlaceholder
// are hoisted function declarations defined below.
setProviderDeps({ loadHealth, loadKeysStatus, refreshOnboardingPlaceholder });

onLanguageChange(() => {
  if (typeof loadChatPrompts === "function") {
    loadChatPrompts().catch(() => {});
  }
});

// Language selector
const langSelect = document.getElementById('langSelect');
if (langSelect) {
  langSelect.value = getCurrentLang();
  langSelect.addEventListener('change', async () => {
    await loadLanguage(langSelect.value);
    showToast(t("toast.languageChanged") || "Language updated", "info");
  });
}


function activateView(viewName) {
  // v1.3.0: navigation is no longer gated by provider configuration. The
  // warning banner + onboarding placeholder guide the user instead.
  document.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("is-active", section.id === `view-${viewName}`);
  });

  document.querySelectorAll(".nav-link").forEach((btn) => {
    const target = btn.dataset.view;
    btn.classList.toggle("is-active", target === viewName);
    btn.classList.remove("tab-locked");
  });

  document.querySelectorAll(".rail-link").forEach((btn) => {
    const target = btn.dataset.view;
    btn.classList.toggle("is-active", target === viewName);
    btn.classList.remove("tab-locked");
  });

  // v1.3.2: hide the chat coach sidebar on the Info view so reading docs
  // is not crowded by the chat panel. Other views keep it for quick access.
  const rail = document.querySelector(".right-rail");
  if (rail) rail.classList.toggle("hidden", viewName === "info");

  // Mobile chrome: navigating closes any open menu/drawer and the chat FAB
  // is suppressed on the Info view (where the rail is hidden).
  rail?.classList.remove("drawer-open");
  document.getElementById("topnav")?.classList.remove("open");
  document.getElementById("navToggle")?.setAttribute("aria-expanded", "false");
  const overlay = document.getElementById("mobileOverlay");
  if (overlay) { overlay.classList.remove("active"); overlay.hidden = true; }
  const fab = document.getElementById("chatFab");
  if (fab) fab.classList.toggle("hidden", viewName === "info");
}

function roleLabel(role) {
  if (role === "assistant") return "Coach";
  if (role === "user") return "You";
  return "System";
}

async function addRolesToShortlist(keywords, label) {
  const kws = (keywords || []).filter(Boolean).map(String);
  if (!kws.length) return;
  await _addToShortlistApi(kws);
  if (window.getKeywords && typeof window.getKeywords.addMultiple === "function") {
    window.getKeywords.addMultiple(kws);
  }
  const msg = t("coach.savedToShortlist") || "Role added to your search";
  showToast(`${msg}${label ? ": " + label : ""}`, "info");
}

function appendChat(role, content, extras) {
  const box = document.getElementById("chatBox");
  if (!box) return;

  const item = document.createElement("div");
  item.className = `chat-item ${role}`;

  const roleDiv = document.createElement("div");
  roleDiv.className = "role";
  roleDiv.textContent = roleLabel(role);

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (role === "assistant") {
    bubble.innerHTML = renderCoachMarkdown(content);
  } else {
    bubble.innerHTML = escapeHtml(content).replaceAll("\n", "<br>");
  }

  item.appendChild(roleDiv);
  item.appendChild(bubble);

  const roles = extras && Array.isArray(extras.suggested_roles) ? extras.suggested_roles : [];
  if (role === "assistant" && roles.length) {
    const pillRow = document.createElement("div");
    pillRow.className = "role-pill-row";
    for (const r of roles) {
      if (!r || !r.label) continue;
      const pill = document.createElement("button");
      pill.type = "button";
      pill.className = "role-pill";
      pill.textContent = r.label;
      const kws = Array.isArray(r.keywords) && r.keywords.length ? r.keywords : [r.label];
      pill.addEventListener("click", async () => {
        const profileAdded = await addRolesToProfile([r.label]);
        const kwAdded =
          window.getKeywords && typeof window.getKeywords.addMultiple === "function"
            ? window.getKeywords.addMultiple(kws)
            : false;
        try {
          await _addToShortlistApi(kws);
        } catch (err) {
          /* shortlist API best-effort; chip still added locally */
        }
        pill.classList.add("is-added");
        if (profileAdded || kwAdded) {
          showToast(t("coach.savedToShortlist") || "Added to your search", "info");
        }
      });
      pillRow.appendChild(pill);
    }
    if (pillRow.childElementCount) item.appendChild(pillRow);
  }

  box.appendChild(item);
  box.scrollTop = box.scrollHeight;
}

// Tracks whether at least one provider key is saved. Updated by loadHealth().
// While ``false``, the banner stays visible and non-dashboard tabs are gated.
let _setupReady = true;

function ensureNoKeyBanner(show, message) {
  _setupReady = !show;
  let banner = document.getElementById("noApiKeyBanner");
  if (!show) {
    if (banner) banner.remove();
    return;
  }
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "noApiKeyBanner";
    banner.className = "no-key-banner";
    document.body.insertBefore(banner, document.body.firstChild);
  }
  // Non-dismissable: removed the close button. The banner clears itself once
  // ``loadHealth()`` sees a configured provider on the next render.
  banner.innerHTML = `
    <span class="material-symbols-outlined">warning</span>
    <span class="no-key-banner-text">${escapeHtml(message)}</span>
    <span class="no-key-banner-hint">${t("banner.signupHint")}</span>
    <a href="https://cloud.cerebras.ai/?utm_source=jobfinder" target="_blank" rel="noopener noreferrer" class="no-key-banner-link no-key-banner-link--primary">${t("banner.signupCerebras")}</a>
    <a href="https://console.groq.com/keys" target="_blank" rel="noopener noreferrer" class="no-key-banner-link">${t("banner.signupGroq")}</a>
    <a href="#" id="noApiKeyBannerLink" class="no-key-banner-link">${t("banner.openSettings")}</a>
  `;
  banner.querySelector("#noApiKeyBannerLink").addEventListener("click", (e) => {
    e.preventDefault();
    activateView("settings");
    const keys = document.getElementById("providerCards");
    if (keys && keys.scrollIntoView) keys.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

async function loadHealth() {
  const health = await api("/api/health");
  setText("providerBadge", `Provider: ${health.provider.active_provider}`);
  setText("modelBadge", `Model: ${health.provider.active_model}`);

  const active = String(health.provider.active_provider || "").toLowerCase();
  const missing = !active || active === "none" || active === "fallback" || health.provider.available === false;
  ensureNoKeyBanner(missing, t("banner.noKey"));

  const prefs = health.preferences || {};
  appState.featureFlags = readFeatureFlags(prefs);
  syncFeatureToggles();
  const linkedinInput = document.getElementById("linkedinUrl");
  if (linkedinInput && prefs.linkedin_url) {
    linkedinInput.value = prefs.linkedin_url;
  }

  const keys = health.keys || {};
  const status = normalizeKeyStatus(keys, health.provider || {});
  setPrimaryProviderValue(status.primary_provider);
  updateProvidersMetadata(health.provider || {}, keys.preferred_model || "");
  renderProviderCards(keys, health.provider || {});
  setText("keysStatus", JSON.stringify(status, null, 2));
}

function setKeysSectionMode(_configured, _forceExpanded = false) {
  /* no-op: provider cards manage their own state */
}

async function loadKeysStatus() {
  const payload = await api("/api/providers/keys/status");
  const keys = payload.keys || {};
  const provider = payload.provider || {};
  const status = normalizeKeyStatus(keys, provider);
  setPrimaryProviderValue(status.primary_provider);
  updateProvidersMetadata(provider, keys.preferred_model || "");
  renderProviderCards(keys, provider);
  setText("keysStatus", JSON.stringify(status, null, 2));
}

async function saveKeys() {
  /* legacy entry point — replaced by per-card onSaveProviderKey */
}

async function loadProfiles() {
  const payload = await api("/api/profiles");
  const select = document.getElementById("profileSelect");
  select.innerHTML = "";

  const active = String(payload.active_profile_id || "");
  for (const profile of payload.profiles || []) {
    const option = document.createElement("option");
    option.value = String(profile.id);
    option.textContent = `${profile.id} - ${profile.source_name}`;
    if (String(profile.id) === active) option.selected = true;
    select.appendChild(option);
  }

  if (!select.value && select.options.length > 0) {
    select.value = select.options[0].value;
  }
}

async function activateProfile(profileId) {
  if (!profileId) return;
  await api(`/api/profiles/${profileId}/activate`, { method: "POST" });
  showToast(t("toast.profileActive", { id: profileId }), "info");
}

let _matchRadarChart = null;

function renderMatchRadar(axes) {
  const canvas = document.getElementById("detailMatchRadar");
  if (!canvas || typeof window.Chart === "undefined") return;
  const data = axes && typeof axes === "object" ? axes : {};
  const labels = [
    t("offcanvas.axisSkills"),
    t("offcanvas.axisSeniority"),
    t("offcanvas.axisRemote"),
    t("offcanvas.axisSalary"),
    t("offcanvas.axisContract"),
  ];
  const values = [
    Number(data.skills_match) || 0,
    Number(data.seniority_match) || 0,
    Number(data.remote_match) || 0,
    Number(data.salary_match) || 0,
    Number(data.contract_match) || 0,
  ];
  if (_matchRadarChart) {
    _matchRadarChart.destroy();
    _matchRadarChart = null;
  }
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  const gridColor = isDark ? "rgba(255,255,255,0.12)" : "rgba(0,0,0,0.08)";
  const textColor = isDark ? "#cbd5e1" : "#334155";
  _matchRadarChart = new window.Chart(canvas, {
    type: "radar",
    data: {
      labels,
      datasets: [
        {
          label: t("offcanvas.matchScore"),
          data: values,
          backgroundColor: "rgba(99,91,255,0.25)",
          borderColor: "#635bff",
          pointBackgroundColor: "#635bff",
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        r: {
          min: 0,
          max: 10,
          ticks: { stepSize: 2, color: textColor, backdropColor: "transparent" },
          angleLines: { color: gridColor },
          grid: { color: gridColor },
          pointLabels: { color: textColor, font: { size: 11 } },
        },
      },
    },
  });
}

async function showJobDetail(jobId) {
  const payload = await api(`/api/jobs/${jobId}`);
  const job = payload.job || {};
  const analysis = job.analysis || {};
  appState.selectedJobId = job.id || null;

  setText("detailStatus", `Stato: ${job.status || "open"}`);
  setText("detailTitle", job.titolo || "Title unavailable");
  setText("detailCompany", job.azienda || "Company unavailable");
  // Build the meta line from distinct parts: legacy data sometimes stored the
  // city in ``modalita`` too, which produced "Torino | Score | Torino".
  const sede = (job.sede || "").trim();
  const modalita = (job.modalita || "").trim();
  const metaParts = [sede || "Sede N/D", `Score ${job.punteggio_ai || 0}/10`];
  if (modalita && modalita.toLowerCase() !== sede.toLowerCase()) {
    metaParts.push(modalita);
  }
  setText("detailMeta", metaParts.join(" | "));

  const detailLinkBtn = document.getElementById("detailLinkBtn");
  if (detailLinkBtn) {
    if (job.link) {
      detailLinkBtn.href = job.link;
      detailLinkBtn.style.display = "flex";
    } else {
      detailLinkBtn.style.display = "none";
    }
  }

  const genBtn = document.getElementById("generateCoverLetterBtn");
  const covBox = document.getElementById("coverLetterBox");
  if (genBtn && covBox) {
    genBtn.style.display = "inline-block";
    covBox.style.display = "none";
    document.getElementById("coverLetterOutput").textContent = "";
  }

  // Optional generation features: show the button only when enabled, and
  // re-render any previously generated artifact stored on the job.
  setupGenerationButton({
    btnId: "generateInterviewPrepBtn",
    boxId: "interviewPrepBox",
    outId: "interviewPrepOutput",
    enabled: appState.featureFlags.interview_prep !== false,
    saved: analysis.interview_prep,
  });
  setupGenerationButton({
    btnId: "generateTailoredResumeBtn",
    boxId: "tailoredResumeBox",
    outId: "tailoredResumeOutput",
    enabled: appState.featureFlags.resume_tailoring !== false,
    saved: analysis.tailored_resume,
  });

  const recruiter = payload.recruiter || null;

  const renderList = (items, max = 5) => {
    if (!Array.isArray(items) || !items.length) return "";
    return `<ul class="bullet-list">${items.slice(0, max).map((x) => `<li>${escapeHtml(String(x))}</li>`).join("")}</ul>`;
  };

  const requisitiBlock = analysis && analysis.requisiti ? `
        <div class="info-card mt-16">
          <h4>${t("offcanvas.requirements") || "Requisiti chiave"}</h4>
          ${renderList(analysis.requisiti)}
        </div>` : "";
  const responsabilitaBlock = analysis && analysis.responsabilita ? `
        <div class="info-card mt-8">
          <h4>${t("offcanvas.responsibilities") || "Responsabilità"}</h4>
          ${renderList(analysis.responsabilita)}
        </div>` : "";
  const benefitBlock = analysis && Array.isArray(analysis.benefit) && analysis.benefit.length ? `
        <div class="info-card mt-8">
          <h4>${t("offcanvas.benefits") || "Benefit"}</h4>
          ${renderList(analysis.benefit)}
        </div>` : "";

  let skillsMatchBlock = "";
  if (analysis && analysis.skills_match) {
    const hai = Array.isArray(analysis.skills_match.hai) ? analysis.skills_match.hai : [];
    const mancano = Array.isArray(analysis.skills_match.mancano) ? analysis.skills_match.mancano : [];
    skillsMatchBlock = `
        <div class="info-card mt-8">
          <h4>${t("offcanvas.skillsMatch") || "Skills match"}</h4>
          <div class="skills-match">
            <div><strong class="pro-label">✅ ${t("offcanvas.skillsHave") || "Hai"}:</strong> ${hai.length ? hai.map((s) => `<span class="chip-mini">${escapeHtml(s)}</span>`).join("") : "—"}</div>
            <div class="mt-8"><strong class="con-label">❌ ${t("offcanvas.skillsMissing") || "Mancano"}:</strong> ${mancano.length ? mancano.map((s) => `<span class="chip-mini missing">${escapeHtml(s)}</span>`).join("") : "—"}</div>
          </div>
        </div>`;
  }

  let recruiterBlock = "";
  if (recruiter && (recruiter.name || recruiter.headline)) {
    recruiterBlock = `
        <div class="info-card mt-8 recruiter-card">
          <h4>${t("offcanvas.postedBy") || "Pubblicato da"}</h4>
          <div class="recruiter-name">${escapeHtml(recruiter.name || "—")}</div>
          ${recruiter.title ? `<div class="text-sm text-dim">${escapeHtml(recruiter.title)}</div>` : ""}
          ${recruiter.headline ? `<div class="text-sm">${escapeHtml(recruiter.headline)}</div>` : ""}
          ${recruiter.profile_url ? `<a class="ghost-btn small mt-8" target="_blank" rel="noopener" href="${escapeHtml(recruiter.profile_url)}">${t("offcanvas.viewProfile") || "View profile"}</a>` : ""}
        </div>`;
  }

  const pinBtnRow = `
        <div class="mt-16 detail-action-row">
          <button type="button" class="ghost-btn" id="detailPinBtn"><span class="material-symbols-outlined">push_pin</span> ${t("offcanvas.pinToChat") || "Pin to chat"}</button>
        </div>`;

  const container = document.getElementById("jobDetailContainer");
  if (container) {
    const score = job.punteggio_ai || 0;
    let ralSpan = "";
    if (analysis && analysis.ral_stimata && analysis.ral_stimata !== "Non stimabile") {
      ralSpan = `<div class="info-tag"><strong>RAL:</strong> ${escapeHtml(analysis.ral_stimata)}</div>`;
    }

    container.innerHTML = `
      <div class="modern-detail">
        <div class="modern-detail-grid">
          <div class="info-card highlight" style="display:flex; flex-direction:column; justify-content:center; align-items:center;">
            <h4>${t("offcanvas.matchScore")}</h4>
            <div class="score-xl">${score}/10</div>
            <div class="text-sm mt-8 text-center">${escapeHtml((analysis ? analysis.consiglio : null) || job.consiglio || "")}</div>
          </div>
          <div class="info-card">
            <h4>${t("offcanvas.positionDetails")}</h4>
            ${ralSpan}
            <div class="info-tag"><strong>${t("offcanvas.contract")}:</strong> ${escapeHtml((analysis ? analysis.contratto : null) || "N/A")}</div>
            <div class="info-tag"><strong>${t("offcanvas.remoteWork")}:</strong> ${escapeHtml((analysis ? analysis.smart_working : null) || job.modalita || "N/A")}</div>
            <div class="info-tag"><strong>${t("offcanvas.experience")}:</strong> ${escapeHtml((analysis ? analysis.anni_esperienza_richiesti : null) || "N/A")}</div>
            <div class="info-tag"><strong>${t("offcanvas.codingSkills")}:</strong> ${escapeHtml((analysis ? analysis.programmazione_richiesta : null) || "N/A")}</div>
            <div class="info-tag"><strong>${t("offcanvas.graduateFriendly")}:</strong> ${escapeHtml((analysis ? analysis.adatta_neolaureati : null) || "N/A")}</div>
          </div>
        </div>
        <div class="mt-16">
          <h4>${t("offcanvas.prosAndCons")}</h4>
          <ul class="pros-cons">
            <li class="pro">✅ ${escapeHtml((analysis ? analysis.punti_forza_per_diego : null) || "N/A")}</li>
            <li class="con">❌ ${escapeHtml((analysis ? analysis.punti_deboli_per_diego : null) || "N/A")}</li>
          </ul>
        </div>
        <div class="info-card mt-8">
            <p class="text-sm">💡 <strong>${t("offcanvas.aiVerdict")}:</strong> ${escapeHtml((analysis ? analysis.riassunto : null) || "")}</p>
        </div>
        <div class="mt-16 info-card">
          <h4>${t("offcanvas.breakdown")}</h4>
          <canvas id="detailMatchRadar" height="220"></canvas>
        </div>
        ${requisitiBlock}
        ${responsabilitaBlock}
        ${benefitBlock}
        ${skillsMatchBlock}
        ${recruiterBlock}
        ${pinBtnRow}
        <div class="mt-16">
          <h4>${t("offcanvas.listingMeta")}</h4>
          <p class="text-sm text-dim">${t("offcanvas.search")}: ${escapeHtml(job.ricerca_usata)} | ${t("jobs.source")}: ${escapeHtml(job.fonte || "App")} | ${t("offcanvas.found")}: ${escapeHtml(job.first_seen_at || "")} | ${t("offcanvas.companyRep")}: ${escapeHtml((analysis ? analysis.reputazione_azienda : null) || "N/A")}</p>
        </div>
      </div>
    `;
    renderMatchRadar(analysis && analysis.match_axes);
    const pinBtn = document.getElementById("detailPinBtn");
    if (pinBtn) {
      pinBtn.addEventListener("click", () => {
        if (appState.selectedJobId && typeof pinJobToActiveSession === "function") {
          pinJobToActiveSession(appState.selectedJobId);
        }
      });
    }
  }
  
  const inlineDetail = document.getElementById('jobDetailInline');
  if (inlineDetail) {
    inlineDetail.style.display = 'block';
    inlineDetail.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

}

async function performJobAction(jobId, action) {
  try {
    await api(`/api/jobs/${jobId}/action`, {
      method: "POST",
      body: JSON.stringify({ action, notes: "" }),
    });
    await Promise.all([loadJobs(), loadRecommendations()]);
    const specific = t(`toast.job.${action}`);
    showToast(specific && specific !== `toast.job.${action}` ? specific : (t("toast.jobUpdated") || "Job updated"), "info");
  } catch (err) {
    showToast(`${t("toast.jobActionFailed") || "Job action failed"}: ${err.message}`, "error");
  }
}

async function toggleFavorite(jobId, isFavorite) {
  try {
    await api(`/api/jobs/${jobId}/favorite`, {
      method: "POST",
      body: JSON.stringify({ is_favorite: isFavorite }),
    });
    await Promise.all([loadJobs(), loadRecommendations()]);
    const key = isFavorite ? "toast.favoriteAdded" : "toast.favoriteRemoved";
    const fallback = isFavorite ? "Added to favorites" : "Removed from favorites";
    showToast(t(key) || fallback, "info");
  } catch (err) {
    showToast(`${t("toast.jobActionFailed") || "Job action failed"}: ${err.message}`, "error");
  }
}

function recommendationCardHtml(job) {
  const score = Number(job.punteggio_ai || 0);
  const consiglio = escapeHtml(job.consiglio || "Evaluate match");
  const title = escapeHtml(job.titolo || t("jobs.titleUnavailable"));
  const company = escapeHtml(job.azienda || t("jobs.companyUnavailable"));
  const newTag = job.is_new ? `<span class="pill-new">${t("jobs.newBadge")}</span>` : "";
  const favoriteText = job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite");
  const nextFavorite = job.is_favorite ? "0" : "1";
  const linkHtml = job.link ? `<div style="margin-top: 4px"><a href="${job.link}" target="_blank" rel="noopener">🔗 ${t("jobs.linkToOffer")}</a></div>` : "";

  return `
    <article class="rec-card" data-rec-id="${job.id}">
      <div class="rec-head">
        <div class="rec-title">${title}</div>
        <span class="rec-score">${score}/10</span>
      </div>
      <div class="rec-company">${company} ${newTag}</div>
      <div>${consiglio}</div>
      ${linkHtml}
      <div class="rec-actions">
        <button class="secondary" data-rec-action="detail" data-id="${job.id}">${t("jobs.details")}</button>
        <button class="apply-btn" data-rec-action="applied" data-id="${job.id}">${t("jobs.apply")}</button>
        <button class="danger" data-rec-action="rejected" data-id="${job.id}">${t("jobs.skip")}</button>
        <button class="secondary" data-rec-favorite="${nextFavorite}" data-id="${job.id}">${favoriteText}</button>
      </div>
    </article>
  `;
}

async function loadRecommendations() {
  const container = document.getElementById("recommendationsGrid");
  if (!container) return;

  container.innerHTML = "";
  try {
    const payload = await api("/api/recommendations?limit=5");
    const jobs = payload.jobs || [];

    if (!jobs.length) {
      container.innerHTML = `<article class="rec-card"><div class="rec-title">${t("recommendations.noRecs")}</div><div>${t("recommendations.noRecsSub")}</div></article>`;
      return;
    }

    container.innerHTML = jobs.map((job) => recommendationCardHtml(job)).join("");

    container.querySelectorAll("button[data-rec-action]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.id;
        const action = btn.dataset.recAction;
        try {
          if (action === "detail") {
            await showJobDetail(id);
            return;
          }
          await performJobAction(id, action);
        } catch (error) {
          showToast(`${t("toast.quickActionError")}: ${error.message}`, "info");
        }
      });
    });

    container.querySelectorAll("button[data-rec-favorite]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.id;
        const fav = btn.dataset.recFavorite === "1";
        try {
          await toggleFavorite(id, fav);
        } catch (error) {
          showToast(`${t("toast.favoriteError")}: ${error.message}`, "info");
        }
      });
    });
  } catch (error) {
    container.innerHTML = `<article class="rec-card"><div class="rec-title">${t("recommendations.loadError")}</div><div>${escapeHtml(error.message)}</div></article>`;
  }
}

async function loadChatPrompts() {
  const wrap = document.getElementById("chatQuickPrompts");
  if (!wrap) return;

  wrap.innerHTML = "";
  try {
    const payload = await api(`/api/chat/prompts?lang=${encodeURIComponent(getCurrentLang() || "en")}`);
    // Cap to 2 — even if the backend ever returns more, the UI stays tidy.
    const prompts = (payload.prompts || []).slice(0, 2);
    for (const prompt of prompts) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip";
      btn.textContent = prompt;
      btn.addEventListener("click", async () => {
        await sendChatMessage(prompt);
      });
      wrap.appendChild(btn);
    }
  } catch (error) {
    showToast(`${t("toast.quickPromptsUnavail")}: ${error.message}`, "info");
  }
}

async function sendChatMessage(message) {
  const text = String(message || "").trim();
  if (!text) return;

  appendChat("user", text);

  const chatBox = document.getElementById("chatBox");
  let pendingEl = null;
  if (chatBox) {
    pendingEl = document.createElement("div");
    pendingEl.className = "chat-item assistant pending";
    pendingEl.innerHTML = '<div class="role">AI</div><div class="bubble"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></div>';
    chatBox.appendChild(pendingEl);
    chatBox.scrollTop = chatBox.scrollHeight;
  }

  try {
    const providerSelector = document.getElementById("chatModelSelector");
    const modelSelector = document.getElementById("chatModelSelectorModel");
    const providerVal = providerSelector && providerSelector.value ? providerSelector.value : null;
    const modelVal = modelSelector && modelSelector.value ? modelSelector.value : null;

    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message: text, session_id: (window.ChatSessions?.active || ChatSessions.active || "default"), provider: providerVal, model: modelVal }),
    });

    maybeOfferPersistChatOverride(providerVal, modelVal);
    if (pendingEl && pendingEl.parentNode) pendingEl.parentNode.removeChild(pendingEl);
    appendChat("assistant", result.answer || "No response available.", { suggested_roles: result.suggested_roles });
    if (typeof refreshChatSessions === "function") {
      refreshChatSessions().then(renderChatSessionDropdown).catch(() => {});
    }

    if (result.action && result.action.type === "FILL_SCAN_FORM") {
      // populate tags
      if (!getKeywords?.addMultiple || !getLocations?.addMultiple) {
          console.warn("Tag setups not ready");
      } else {
         const kwTags = getKeywords.addMultiple(result.action.keywords || []);
         const locTags = getLocations.addMultiple(result.action.locations || []);
         if (kwTags || locTags) {
           showToast(t("toast.formFilled"), "info");
           activateView("settings");
         }
      }
    }
  } catch (error) {
    if (pendingEl && pendingEl.parentNode) pendingEl.parentNode.removeChild(pendingEl);
    const isNoProvider = error && (error.status === 412 || /412|no_provider_configured|noProvider/i.test(error.message || ""));
    if (isNoProvider) {
      appendChat("assistant", t("errors.noProviderToast") || "Configure an AI provider key first to use the chat.");
      try {
        activateView("settings");
        const cards = document.getElementById("providerCards");
        if (cards && cards.scrollIntoView) cards.scrollIntoView({ behavior: "smooth", block: "center" });
      } catch (_) {}
    } else {
      appendChat("assistant", `${t("toast.chatError")}: ${error.message}`);
    }
  }
}

async function loadJobs() {
  const onlyNew = document.getElementById("onlyNew").checked;
  const onlyFavorites = document.getElementById("onlyFavorites").checked;
  const searchText = document.getElementById("searchText").value.trim();
  const status = document.getElementById("statusFilter").value;
  const minScoreRaw = document.getElementById("minScore").value.trim();
  const maxAgeRaw = document.getElementById("maxAgeDays").value.trim();

  const query = new URLSearchParams({
    only_new: onlyNew ? "true" : "false",
    only_favorites: onlyFavorites ? "true" : "false",
    limit: "250",
  });
  if (searchText) query.set("search_text", searchText);
  if (status) query.set("status", status);
  if (minScoreRaw) query.set("min_score", minScoreRaw);
  if (maxAgeRaw) query.set("max_age_days", maxAgeRaw);

  const { jobs } = await api(`/api/jobs?${query.toString()}`);
  const body = document.getElementById("jobsTableBody");
  body.innerHTML = "";

  for (const job of jobs) {
    const newBadge = job.is_new ? `<span class="pill-new">${t("jobs.newBadge")}</span>` : "";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${job.punteggio_ai || 0}/10 ${newBadge}</td>
      <td>${truncate(job.titolo || "")}</td>
      <td>${truncate(job.azienda || "")}</td>
      <td>${truncate(job.sede || "")}</td>
      <td>${truncate(job.fonte || "")}</td>
      <td>${truncate(job.consiglio || "")}</td>
      <td>
        <button data-detail-id="${job.id}" class="secondary">${t("jobs.details")}</button>
        ${job.link ? `<a href="${job.link}" target="_blank" rel="noopener" style="margin-left: 8px;">🔗</a>` : ""}
      </td>
      <td>
        <div class="mini">
          <button class="apply-btn" data-action="applied" data-id="${job.id}">${t("jobs.apply")}</button>
          <button data-action="rejected" data-id="${job.id}" class="danger">${t("jobs.skip")}</button>
          <button data-action="reopened" data-id="${job.id}" class="secondary icon-btn" title="${t("jobs.reopen")}" aria-label="${t("jobs.reopen")}"><span class="material-symbols-outlined">restart_alt</span></button>
          <button data-favorite="${job.is_favorite ? "0" : "1"}" data-id="${job.id}" class="secondary icon-btn${job.is_favorite ? " is-active" : ""}" title="${job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite")}" aria-label="${job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite")}"><span class="material-symbols-outlined">${job.is_favorite ? "favorite" : "favorite_border"}</span></button>
          <button data-delete-id="${job.id}" class="danger icon-btn" title="${t("jobs.delete")}" aria-label="${t("jobs.delete")}"><span class="material-symbols-outlined">delete</span></button>
        </div>
      </td>
    `;
    body.appendChild(tr);
  }

  body.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const action = btn.dataset.action;
      try {
        await performJobAction(id, action);
      } catch (error) {
        showToast(`${t("toast.actionError")}: ${error.message}`, "info");
      }
    });
  });

  body.querySelectorAll("button[data-favorite]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const fav = btn.dataset.favorite === "1";
      try {
        await toggleFavorite(id, fav);
      } catch (error) {
        showToast(`${t("toast.favoriteError")}: ${error.message}`, "info");
      }
    });
  });

  body.querySelectorAll("button[data-detail-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.detailId;
      try {
        await showJobDetail(id);
      } catch (error) {
        showToast(`${t("toast.detailError")}: ${error.message}`, "info");
      }
    });
  });

  body.querySelectorAll("button[data-delete-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.deleteId;
      if (!confirm(t("jobs.deleteConfirm"))) return;
      try {
        await api(`/api/jobs/${id}`, { method: "DELETE" });
        showToast(t("jobs.deleted"), "info");
        await loadJobs();
      } catch (error) {
        showToast(`${t("toast.deleteError")}: ${error.message}`, "info");
      }
    });
  });

  renderKanban(jobs);
}

function normalizeJobStatus(status) {
  const normalized = String(status || "open").trim().toLowerCase();
  if (normalized === "interview") return "interviewing";
  return normalized;
}

function renderKanban(jobs) {
  const kanbanView = document.getElementById("kanbanView");
  if (!kanbanView) return;

  const columns = {
    open: kanbanView.querySelector('.kanban-col[data-status="open"] .cards-container'),
    applied: kanbanView.querySelector('.kanban-col[data-status="applied"] .cards-container'),
    interviewing: kanbanView.querySelector('.kanban-col[data-status="interviewing"] .cards-container'),
    rejected: kanbanView.querySelector('.kanban-col[data-status="rejected"] .cards-container'),
  };

  Object.values(columns).forEach((container) => {
    if (container) container.innerHTML = "";
  });

  const counts = { open: 0, applied: 0, interviewing: 0, rejected: 0 };
  for (const job of jobs || []) {
    const status = normalizeJobStatus(job.status);
    if (!(status in columns) || !columns[status]) continue;

    counts[status] += 1;
    const card = document.createElement("article");
    card.className = "kanban-card";
    card.innerHTML = `
      <strong>${escapeHtml(job.titolo || t("jobs.titleUnavailable"))}</strong>
      <div class="micro">${escapeHtml(job.azienda || t("jobs.companyUnavailable"))}</div>
      <div class="micro">Score: ${job.punteggio_ai || 0}/10</div>
      <div class="mini" style="margin-top:8px;">
        <button class="secondary" data-k-detail-id="${job.id}">${t("jobs.details")}</button>
        <button class="apply-btn" data-k-action="${status === "open" ? "applied" : "reopened"}" data-id="${job.id}">
          ${status === "open" ? t("jobs.apply") : t("jobs.reopen")}
        </button>
      </div>
    `;
    columns[status].appendChild(card);
  }

  setText("k-count-open", String(counts.open));
  setText("k-count-applied", String(counts.applied));
  setText("k-count-interviewing", String(counts.interviewing));
  setText("k-count-rejected", String(counts.rejected));

  kanbanView.querySelectorAll("button[data-k-detail-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await showJobDetail(btn.dataset.kDetailId);
      } catch (error) {
        showToast(`${t("toast.detailError")}: ${error.message}`, "info");
      }
    });
  });

  kanbanView.querySelectorAll("button[data-k-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await performJobAction(btn.dataset.id, btn.dataset.kAction);
      } catch (error) {
        showToast(`${t("toast.actionError")}: ${error.message}`, "info");
      }
    });
  });
}

async function loadChatHistory() {
  const { messages } = await api("/api/chat/history?session_id=default&limit=20");
  const box = document.getElementById("chatBox");
  box.innerHTML = "";
  for (const msg of messages) {
    appendChat(msg.role, msg.content);
  }
}

document.getElementById("linkedinForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = document.getElementById("linkedinUrl").value.trim();
  try {
    await api("/api/preferences", {
      method: "POST",
      body: JSON.stringify({ key: "linkedin_url", value: url }),
    });
    const status = document.getElementById("linkedinStatus");
    status.textContent = t("toast.linkedinSaved");
    status.classList.remove("hidden");
    setTimeout(() => status.classList.add("hidden"), 3000);
    showToast(t("toast.linkedinSaved"), "info");
  } catch (error) {
    showToast(`${t("toast.linkedinError")}: ${error.message}`, "info");
  }
});

document.getElementById("cvForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const fileInput = document.getElementById("cvFile");
  if (!fileInput.files.length) return;

  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  const submitBtn = event.currentTarget.querySelector('button[type="submit"]');
  const originalLabel = submitBtn ? submitBtn.innerHTML : "";
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<span class="spinner-inline"></span> ${t("toast.cvAnalyzing") || "Analyzing CV with AI..."}`;
  }
  showToast(t("toast.cvAnalyzing") || "Analyzing CV with AI...", "info");

  try {
    const response = await fetch("/api/upload-cv", {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      setText("cvSummary", `${t("toast.uploadError")}: ${await response.text()}`);
      showToast(t("toast.uploadError") || "Upload failed", "error");
      return;
    }

    const payload = await response.json();
    setText("cvSummary", JSON.stringify(payload, null, 2));
    await loadProfiles();
    await loadProfileView();
    await loadRecommendations();
    if (typeof refreshOnboardingPlaceholder === "function") {
      refreshOnboardingPlaceholder().catch(() => {});
    }

    if (payload.summary_method === "llm") {
      const msg = payload.retries
        ? (t("toast.cvLlmRetried") || "AI summary ready (retried {n}×)").replace("{n}", payload.retries)
        : (t("toast.cvLlmOk") || "AI summary ready");
      showToast(msg, "info");
    } else {
      showToast(
        t("toast.cvHeuristic") || "AI was busy — used a quick fallback. Re-upload later for full analysis.",
        "info",
      );
    }
  } catch (err) {
    showToast(`${t("toast.uploadError") || "Upload failed"}: ${err.message}`, "error");
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalLabel;
    }
  }
});

(() => {
  const dz = document.getElementById("cvDropzone");
  const fileInput = document.getElementById("cvFile");
  if (!dz || !fileInput) return;
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.add("is-dragover");
    }),
  );
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.remove("is-dragover");
    }),
  );
  dz.addEventListener("drop", (e) => {
    if (e.dataTransfer?.files?.length) {
      fileInput.files = e.dataTransfer.files;
      fileInput.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });
  fileInput.addEventListener("change", () => {
    const name = fileInput.files?.[0]?.name;
    const text = dz.querySelector(".cv-dropzone-text");
    if (name && text) {
      text.textContent = name;
      dz.classList.add("has-file");
    }
  });
})();

{
  const providerCardsEl = document.getElementById("providerCards");
  if (providerCardsEl) {
    providerCardsEl.addEventListener("click", async (event) => {
      const target = event.target.closest("button");
      if (!target) return;
      const card = target.closest(".provider-card");
      if (!card) return;
      const name = card.dataset.provider;

      if (target.classList.contains("provider-toggle-visibility")) {
        const input = card.querySelector(".provider-key-input");
        if (input) input.type = input.type === "password" ? "text" : "password";
        return;
      }
      if (target.classList.contains("provider-save-btn")) {
        const input = card.querySelector(".provider-key-input");
        const value = input ? input.value.trim() : "";
        if (!value) {
          showToast(t("toast.enterKeyOrProvider"), "info");
          return;
        }
        await onSaveProviderKey(name, value);
        return;
      }
      if (target.classList.contains("provider-refresh-btn")) {
        await fetchAndRenderProviderModels(name, true);
        return;
      }
    });

    providerCardsEl.addEventListener("change", async (event) => {
      const card = event.target.closest(".provider-card");
      if (!card) return;
      const name = card.dataset.provider;
      if (event.target.matches('input[name="primaryProviderRadio"]')) {
        if (event.target.checked) {
          const select = card.querySelector(".provider-model-select");
          const modelOverride = select ? select.value : "";
          await onSetPrimaryProvider(name, modelOverride);
        }
        return;
      }
      if (event.target.classList.contains("provider-model-select")) {
        const radio = card.querySelector('input[name="primaryProviderRadio"]');
        if (radio && radio.checked) {
          await onSetPrimaryProvider(name, event.target.value);
        }
      }
    });

    providerCardsEl.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      if (!event.target.classList.contains("provider-key-input")) return;
      event.preventDefault();
      const card = event.target.closest(".provider-card");
      const saveBtn = card?.querySelector(".provider-save-btn");
      if (saveBtn) saveBtn.click();
    });
  }
}

document.getElementById("scanForm").addEventListener("submit", async (event) => {
  event.preventDefault();

  if (typeof ensureProviderConfigured === "function") {
    const ok = await ensureProviderConfigured();
    if (!ok) return;
  }

  // Add any pending typed text that wasn't entered
  const kwInputRaw = document.getElementById("keywordsInput").value.trim();
  if (kwInputRaw) {
    getKeywords.addMultiple([kwInputRaw]);
    document.getElementById("keywordsInput").value = '';
  }
  const locInputRaw = document.getElementById("locationsInput").value.trim();
  if (locInputRaw) {
    getLocations.addMultiple([locInputRaw]);
    document.getElementById("locationsInput").value = '';
  }

  const termsText = getKeywords.getTags().join(", ");
  const siteCheckboxes = document.querySelectorAll('input[name="scanSites"]:checked');
  const selectedSites = Array.from(siteCheckboxes).map(cb => cb.value);
  const isRemote = document.getElementById("remoteToggle")?.checked || false;
  const location = getLocations.getTags().join(", ");

  const expLevels = Array.from(document.querySelectorAll('input[name="scanExperience"]:checked')).map(cb => cb.value);
  const jobTypes = Array.from(document.querySelectorAll('input[name="scanJobType"]:checked')).map(cb => cb.value);
  const workTypes = Array.from(document.querySelectorAll('input[name="scanWorkType"]:checked')).map(cb => cb.value);

  const params = new URLSearchParams();
  if (termsText) params.set("search_terms", termsText);
  if (location) params.set("location", location);
  params.set("is_remote", isRemote);
  params.set("sites", (selectedSites.length > 0 ? selectedSites : ["linkedin", "indeed"]).join(","));
  if (expLevels.length) params.set("experience_levels", expLevels.join(","));
  if (jobTypes.length) params.set("job_types", jobTypes.join(","));
  if (workTypes.length) params.set("work_types", workTypes.join(","));

  // Show scan overlay
  const overlay = document.getElementById("scanOverlay");
  const progressText = document.getElementById("scanProgressText");
  const progressFill = document.getElementById("scanProgressFill");
  const feedEl = document.getElementById("scanFeed");
  overlay.style.display = "flex";
  overlay.classList.remove("minimized");
  progressFill.style.width = "5%";
  progressText.textContent = t("scan.connecting");
  if (feedEl) feedEl.innerHTML = "";

  const appendFeed = (iconName, textHtml, chip) => {
    if (!feedEl) return;
    const li = document.createElement("li");
    const chipHtml = chip ? `<span class="feed-chip ${chip.cls || ""}">${chip.label}</span>` : "";
    li.innerHTML = `<span class="material-symbols-outlined feed-icon">${iconName}</span><span class="feed-text">${textHtml}</span>${chipHtml}`;
    feedEl.appendChild(li);
    while (feedEl.children.length > 50) feedEl.removeChild(feedEl.firstChild);
    feedEl.scrollTop = feedEl.scrollHeight;
  };
  const escHtml = (s) => String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let analysisCount = 0;
  let totalFound = 0;
  const lastTopJobs = [];

  const fmtEta = (ms) => {
    if (!ms || ms < 0) return "";
    const sec = Math.round(ms / 1000);
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m ${String(s).padStart(2, "0")}s`;
  };

  const setProgress = (pct, label) => {
    progressFill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
    if (label) progressText.textContent = label;
  };

  const evtSource = new EventSource(`/api/scan/stream?${params.toString()}`);
  evtSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.status === "started") {
        const terms = (data.terms || []).join(", ");
        setProgress(2, `${t("scan.searching")}: ${terms}`);
        appendFeed("travel_explore", t("scan.feedStarted", { terms: escHtml(terms) }));
      } else if (data.status === "progress") {
        const pct = Number(data.percent || 0);
        const eta = fmtEta(data.eta_ms);
        const stepKey = data.step === "analyzing" ? "scan.progress.analyzing" : "scan.progress.scraping";
        const stepLabel = t(stepKey) || (data.step === "analyzing" ? "Analyzing" : "Scraping");
        const line = `${stepLabel} ${data.current || 0}/${data.total || "?"}` + (eta ? ` · ${t("scan.progress.eta") || "ETA"} ${eta}` : "");
        setProgress(pct, line);
      } else if (data.status === "scraped") {
        totalFound += data.found || 0;
        appendFeed("manage_search", t("scan.feedScraped", { found: data.found || 0, portal: escHtml(data.portal || data.site || "") }));
      } else if (data.status === "analyzed") {
        analysisCount++;
        const j = data.job || {};
        const score = Number(j.score || 0);
        lastTopJobs.push({ titolo: j.titolo, azienda: j.azienda, score });
        lastTopJobs.sort((a, b) => b.score - a.score);
        if (lastTopJobs.length > 5) lastTopJobs.length = 5;
        const cls = score >= 7 ? "score-high" : score >= 4 ? "score-mid" : "score-low";
        const pct = Number(data.percent || Math.min(30 + analysisCount * 3, 95));
        const eta = fmtEta(data.eta_ms);
        const line = t("scan.analyzed", { title: j.titolo || "?", company: j.azienda || "?", score });
        setProgress(pct, eta ? `${line} · ${t("scan.progress.eta") || "ETA"} ${eta}` : line);
        appendFeed("check_circle", t("scan.feedAnalyzed", { title: escHtml(j.titolo || "?"), company: escHtml(j.azienda || "?") }), { label: `${score}/10`, cls });
      } else if (data.status === "complete") {
        setProgress(100, t("scan.complete", { newJobs: data.totale_nuovi || 0, analyzed: data.totale_analizzati || 0 }));
        appendFeed("task_alt", t("scan.complete", { newJobs: data.totale_nuovi || 0, analyzed: data.totale_analizzati || 0 }));
        evtSource.close();
        // Close the scan overlay first so the post-scan modal isn't covered
        // by it. Stash the data on closure since the SSE event won't repeat.
        const completeData = data;
        const completeTops = lastTopJobs.slice();
        setTimeout(() => {
          overlay.style.display = "none";
          overlay.classList.remove("minimized");
          try {
            showPostScanModal(completeData, completeTops);
          } catch (err) {
            console.error("post-scan modal failed:", err);
            showToast("Scan complete (post-scan summary failed to render)", "info");
          }
        }, 600);
        Promise.all([loadJobs(), loadRecommendations()]);
      } else if (data.error) {
        progressText.textContent = `${t("scan.error")}: ${data.error}`;
        appendFeed("error", `${t("scan.error")}: ${escHtml(data.error)}`);
        showToast(`${t("scan.error")}: ${data.error}`, "error");
        evtSource.close();
        setTimeout(() => { overlay.style.display = "none"; overlay.classList.remove("minimized"); }, 3000);
      }
    } catch (_) {}
  };
  evtSource.onerror = () => {
    evtSource.close();
    progressText.textContent = t("scan.connectionLost");
    showToast(t("scan.connectionLost") || "Scan connection lost", "error");
    setTimeout(() => { overlay.style.display = "none"; }, 2000);
    Promise.all([loadJobs(), loadRecommendations()]);
  };

  document.getElementById("cancelScanBtn").onclick = () => {
    evtSource.close();
    overlay.style.display = "none";
    overlay.classList.remove("minimized");
    Promise.all([loadJobs(), loadRecommendations()]);
  };

  const minimizeBtn = document.getElementById("minimizeScanBtn");
  if (minimizeBtn) {
    minimizeBtn.onclick = () => overlay.classList.toggle("minimized");
  }
});

const _chatForm = document.getElementById("chatForm");
if (_chatForm) _chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  input.style.height = "auto";
  await sendChatMessage(message);
});

const _chatInputEl = document.getElementById("chatInput");
if (_chatInputEl) {
  // Enter sends the message; Shift+Enter inserts a newline.
  _chatInputEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (_chatForm) _chatForm.requestSubmit();
    }
  });
  // Auto-grow the textarea up to the CSS max-height as the user types.
  _chatInputEl.addEventListener("input", () => {
    _chatInputEl.style.height = "auto";
    _chatInputEl.style.height = `${Math.min(_chatInputEl.scrollHeight, 140)}px`;
  });
}

const _quickRecommendBtn = document.getElementById("quickRecommendBtn");
if (_quickRecommendBtn) _quickRecommendBtn.addEventListener("click", async () => {
  await sendChatMessage("Recommend the top 5 jobs I should apply for today, in priority order.");
});

const _refreshRecommendationsBtn = document.getElementById("refreshRecommendationsBtn");
if (_refreshRecommendationsBtn) _refreshRecommendationsBtn.addEventListener("click", async () => {
  const btn = _refreshRecommendationsBtn;
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner-inline"></span> ${t("toast.recsRefreshing") || "Refreshing..."}`;
  try {
    await loadRecommendations();
  } catch (err) {
    showToast(`${t("toast.recsFailed") || "Refresh failed"}: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
  }
});

const _focusOpenBtn = document.getElementById("focusOpenBtn");
if (_focusOpenBtn) _focusOpenBtn.addEventListener("click", async () => {
  const status = document.getElementById("statusFilter");
  status.value = "open";
  activateView("dashboard");
  await loadJobs();
  requestAnimationFrame(() => {
    document.querySelector(".jobs-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

document.querySelectorAll("[data-view]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.view || "dashboard";
    activateView(view);
    if (view === "profile") {
      loadProfileView().catch(() => {});
    }
  });
});

bindProfileEvents();

const _primaryProviderEl = document.getElementById("primaryProvider");
if (_primaryProviderEl) {
  _primaryProviderEl.addEventListener("change", () => {
    populateModelOptions(_primaryProviderEl.value, "");
  });
}

const _chatProviderEl = document.getElementById("chatModelSelector");
if (_chatProviderEl) {
  _chatProviderEl.addEventListener("change", () => {
    populateChatModelSelector(_chatProviderEl.value);
  });
}

// ─── Job Search ──────────────────────────────────────────
function _refreshChipState() {
  const chipsEl = document.getElementById("wizardRoleSuggestions");
  if (!chipsEl) return;
  const tags = (typeof getKeywords !== "undefined" ? getKeywords.getTags() : []).map((t) => t.toLowerCase());
  chipsEl.querySelectorAll(".chip-suggestion").forEach((chip) => {
    const role = (chip.textContent || "").toLowerCase();
    chip.classList.toggle("is-added", tags.includes(role));
  });
}

function updateWizardReview() {
  _refreshChipState();
}

async function loadWizardProfile() {
  const summaryEl = document.getElementById("wizardProfileSummary");
  const chipsEl = document.getElementById("wizardRoleSuggestions");
  if (!summaryEl || !chipsEl) return;
  chipsEl.innerHTML = "";
  try {
    const data = await api("/api/profile");
    const profile = data.profile;
    if (!profile) {
      summaryEl.innerHTML = `<em>${t("jobSearch.noProfile")}</em>`;
      return;
    }
    const summary = profile.summary_json || {};
    const skills = Array.isArray(summary.skills) ? summary.skills.slice(0, 12) : [];
    const roles = Array.isArray(summary.preferred_roles) ? summary.preferred_roles : [];
    const skillList = skills.length ? skills.map((s) => `<span class="search-tag">${escapeHtml(s)}</span>`).join("") : `<em>—</em>`;
    summaryEl.innerHTML = `
      <div class="search-summary-row">
        <span class="search-summary-label">${t("jobSearch.detectedSkills")}</span>
        <div class="search-summary-tags">${skillList}</div>
      </div>
    `;
    if (!roles.length) {
      chipsEl.innerHTML = `<em class="micro">${t("jobSearch.noRoles") || "Add roles to your Profile to get suggestions here."}</em>`;
    }
    for (const role of roles) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip-suggestion";
      chip.textContent = role;
      chip.addEventListener("click", () => {
        if (typeof getKeywords !== "undefined") {
          getKeywords.addMultiple([role]);
          updateWizardReview();
        }
      });
      chipsEl.appendChild(chip);
    }
    _refreshChipState();
  } catch (err) {
    summaryEl.innerHTML = `<em>${t("jobSearch.noProfile")}</em>`;
  }
}

document.querySelectorAll('[data-view="job-search"]').forEach((btn) => {
  btn.addEventListener("click", () => {
    loadWizardProfile();
    updateWizardReview();
  });
});

document.querySelectorAll('input[name="scanSites"], #remoteToggle').forEach((el) => {
  el.addEventListener("change", updateWizardReview);
});

["keywordsContainer", "locationsContainer"].forEach((id) => {
  const el = document.getElementById(id);
  if (!el) return;
  new MutationObserver(updateWizardReview).observe(el, { childList: true, subtree: true });
});

document.getElementById("railRecommendBtn").addEventListener("click", async () => {
  await sendChatMessage("Recommend the strongest jobs I should apply for right now, in priority order.");
});

document.getElementById("detailApplyNowBtn").addEventListener("click", async () => {
  if (!appState.selectedJobId) {
    showToast(t("toast.openJobFirst"), "info");
    return;
  }
  try {
    await performJobAction(appState.selectedJobId, "applied");
    showToast(t("toast.appMarked"), "info");
  } catch (error) {
    showToast(`${t("toast.actionError")}: ${error.message}`, "info");
  }
});

const genCovBtn = document.getElementById("generateCoverLetterBtn");
if (genCovBtn) {
  genCovBtn.addEventListener("click", async () => {
    if (!appState.selectedJobId) return;
    const outBox = document.getElementById("coverLetterBox");
    const outTxt = document.getElementById("coverLetterOutput");

    outBox.style.display = "block";
    outTxt.textContent = t("toast.generating");
    genCovBtn.disabled = true;
    const originalLabel = genCovBtn.innerHTML;
    genCovBtn.innerHTML = `<span class="spinner-inline"></span> ${t("toast.coverLetterGenerating") || "Generating..."}`;
    showToast(t("toast.coverLetterGenerating") || "Generating cover letter...", "info");

    try {
      const payload = await api(`/api/jobs/${appState.selectedJobId}/cover-letter`, { method: "POST" });
      outTxt.textContent = payload.cover_letter || t("toast.noResult");
      showToast(t("toast.coverLetterReady") || "Cover letter ready", "info");
    } catch (error) {
      outTxt.textContent = `${t("toast.genError")}: ${error.message}`;
      showToast(`${t("toast.coverLetterFailed") || "Cover letter failed"}: ${error.message}`, "error");
    } finally {
      genCovBtn.disabled = false;
      genCovBtn.innerHTML = originalLabel;
    }
  });
}

initFeatures({ loadJobs });

document.getElementById("refreshJobsBtn").addEventListener("click", loadJobs);
document.getElementById("onlyNew").addEventListener("change", loadJobs);
document.getElementById("onlyFavorites").addEventListener("change", loadJobs);
document.getElementById("searchText").addEventListener("change", loadJobs);
document.getElementById("minScore").addEventListener("change", loadJobs);
document.getElementById("maxAgeDays").addEventListener("change", loadJobs);
document.getElementById("statusFilter").addEventListener("change", loadJobs);
document.getElementById("profileSelect").addEventListener("change", async (event) => {
  await activateProfile(event.target.value);
});

document.getElementById("exportCsvBtn").addEventListener("click", async () => {
  const result = await api("/api/export/csv", { method: "POST" });
  showToast(t("toast.csvExported", { file: result.file }), "info");
});

document.getElementById("deleteAllJobsBtn").addEventListener("click", async () => {
  if (!confirm(t("jobs.deleteAllConfirm"))) return;
  try {
    const res = await api("/api/jobs", { method: "DELETE" });
    showToast(t("jobs.deletedAll", { count: res.deleted }), "info");
    await Promise.all([loadJobs(), loadRecommendations()]);
  } catch (error) {
    showToast(`${t("toast.deleteError")}: ${error.message}`, "info");
  }
});

async function bootstrap() {
  await initI18n();
  activateView("dashboard");
  await loadHealth();
  await loadKeysStatus();
  await loadProfiles();
  await Promise.all([loadJobs(), loadRecommendations()]);
  await loadAnalytics();
  await loadSkillGap();
  await loadSchedulerStatus();
  await loadChatPrompts();
  // i18n is ready here, so the session dropdown / empty-state get localised
  // labels (no boot race). Sessions first: history loads the active one.
  await initChatSessions().catch((e) => console.error("initChatSessions failed:", e));
  await loadChatHistory();
  // Shows only if the conversation is empty, with localised suggestion labels.
  renderChatEmptyState();
}

bootstrap().catch((error) => {
  console.error(error);
  showToast(`${t("toast.initError")}: ${error.message}`, "info");
});


const closeDetailBtn = document.getElementById('closeDetailBtn');
if (closeDetailBtn) {
    closeDetailBtn.addEventListener('click', () => {
        const inlineDetail = document.getElementById('jobDetailInline');
        if (inlineDetail) inlineDetail.style.display = 'none';
    });
}


document.getElementById("viewTableBtn")?.addEventListener("click", e => {
    document.getElementById("tableView").classList.add("is-active");
    document.getElementById("kanbanView").classList.remove("is-active");
  e.currentTarget.classList.add("is-active");
    document.getElementById("viewKanbanBtn").classList.remove("is-active");
});

document.getElementById("viewKanbanBtn")?.addEventListener("click", e => {
    document.getElementById("kanbanView").classList.add("is-active");
    document.getElementById("tableView").classList.remove("is-active");
  e.currentTarget.classList.add("is-active");
    document.getElementById("viewTableBtn").classList.remove("is-active");
  loadJobs();
});

// Tag Input UI Logic
function setupTagInput(containerId, inputId) {
    const container = document.getElementById(containerId);
    const input = document.getElementById(inputId);
    const tags = [];

    if (!container || !input) return { getTags: () => [], addMultiple: () => false };

    function renderTags() {
        container.querySelectorAll('.tag').forEach(el => el.remove());
        tags.forEach((tagText, index) => {
            const tagEl = document.createElement('span');
            tagEl.className = 'tag';
            tagEl.textContent = tagText;
            
            const removeBtn = document.createElement('span');
            removeBtn.className = 'remove-tag material-symbols-outlined';
            removeBtn.textContent = 'close';
            removeBtn.onclick = () => {
                tags.splice(index, 1);
                renderTags();
            };
            
            tagEl.appendChild(removeBtn);
            container.insertBefore(tagEl, input);
        });
    }

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            const val = input.value.trim();
            if (val && !tags.includes(val)) {
                tags.push(val);
                input.value = '';
                renderTags();
            }
        }
    });

    return {
        getTags: () => tags,
        addMultiple: (newTags) => {
            if (!Array.isArray(newTags)) return false;
            let added = false;
            for(const nt of newTags) {
                const val = (nt||'').trim();
                if(val && !tags.includes(val)) {
                    tags.push(val);
                    added = true;
                }
            }
            if(added) renderTags();
            return added;
        }
    };
}

const getKeywords = setupTagInput('keywordsContainer', 'keywordsInput');
const getLocations = setupTagInput('locationsContainer', 'locationsInput');
window.getKeywords = getKeywords;
window.getLocations = getLocations;

async function loadRoleShortlist() {
  const roles = await _loadShortlistApi();
  if (roles.length && getKeywords && typeof getKeywords.addMultiple === "function") {
    getKeywords.addMultiple(roles);
  }
}
loadRoleShortlist();

// ─── Update Banner ──────────────────────────────────────────────

// ─── Chat empty state + first-time tutorial ─────────────────────
const CHAT_SUGGESTION_KEYS = [
  'chat.suggestions.roles',
  'chat.suggestions.top5',
];

function renderChatEmptyState() {
  const box = document.getElementById('chatBox');
  if (!box) return;
  if (box.querySelector('.chat-item') || box.querySelector('.chat-empty')) return;
  const empty = document.createElement('div');
  empty.className = 'chat-empty';
  const lead = document.createElement('div');
  lead.className = 'lead';
  lead.textContent = t('chat.suggestions.label');
  empty.appendChild(lead);
  for (const key of CHAT_SUGGESTION_KEYS) {
    const label = t(key);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = label;
    btn.addEventListener('click', () => sendChatMessage(label));
    empty.appendChild(btn);
  }
  box.appendChild(empty);
}

function clearChatEmptyState() {
  const box = document.getElementById('chatBox');
  const empty = box && box.querySelector('.chat-empty');
  if (empty) empty.remove();
}

const _origAppendChat = typeof appendChat === 'function' ? appendChat : null;
if (_origAppendChat) {
  window.appendChat = function (role, content) {
    clearChatEmptyState();
    return _origAppendChat(role, content);
  };
}

async function showFirstTimeTutorial() {
  if (localStorage.getItem('tutorialSeen')) return;

  // Skip wizard if user is already fully set up.
  let initialStatus = { provider_configured: false, cv_loaded: false };
  try {
    initialStatus = await fetch('/api/setup/status').then((r) => r.json());
  } catch { /* offline ok */ }
  if (initialStatus.provider_configured && initialStatus.cv_loaded) {
    localStorage.setItem('tutorialSeen', '1');
    return;
  }

  const overlay = document.createElement('div');
  overlay.className = 'tutorial-overlay';
  overlay.id = 'tutorialOverlay';
  document.body.appendChild(overlay);

  let currentStep = 0;
  let pollHandle = null;
  let lastStatus = initialStatus;

  const close = () => {
    if (pollHandle) clearInterval(pollHandle);
    overlay.remove();
    localStorage.setItem('tutorialSeen', '1');
  };

  const STEPS = [
    {
      key: 'step1',
      titleKey: 'tutorial.step1Title',
      bodyKey: 'tutorial.step1Body',
      ctaKey: 'tutorial.openSettings',
      ctaTarget: 'settings',
      ctaScroll: 'providerCards',
      isDone: (s) => !!s.provider_configured,
    },
    {
      key: 'step2',
      titleKey: 'tutorial.step2Title',
      bodyKey: 'tutorial.step2Body',
      ctaKey: 'tutorial.openProfile',
      ctaTarget: 'profile',
      ctaScroll: 'cvFile',
      isDone: (s) => !!s.cv_loaded,
    },
    {
      key: 'step3',
      titleKey: 'tutorial.step3Title',
      bodyKey: 'tutorial.step3Body',
      ctaKey: 'tutorial.openSearch',
      ctaTarget: 'job-search',
      ctaScroll: null,
      isDone: () => true, // last step always free
    },
  ];

  const render = () => {
    const step = STEPS[currentStep];
    const stepLabel = (t('tutorial.stepLabel') || 'Step {n} of 3').replace('{n}', currentStep + 1);
    const stepperHtml = STEPS.map((_, i) => {
      let cls = 'wizard-dot';
      if (i < currentStep) cls += ' done';
      else if (i === currentStep) cls += ' active';
      return `<span class="${cls}">${i + 1}</span>`;
    }).join('<span class="wizard-line"></span>');
    const isDone = step.isDone(lastStatus);
    const isLast = currentStep === STEPS.length - 1;
    const nextLabel = isLast ? (t('tutorial.finish') || 'Finish') : (t('tutorial.next') || 'Next');
    const nextDisabled = !isDone ? 'disabled' : '';
    overlay.innerHTML = `
      <div class="tutorial-card wizard-card">
        <div class="wizard-stepper">${stepperHtml}</div>
        <p class="wizard-step-label">${stepLabel}</p>
        <h3>${escapeHtml(t(step.titleKey) || step.key)}</h3>
        <p>${escapeHtml(t(step.bodyKey) || '')}</p>
        <div class="tutorial-actions wizard-actions">
          <button type="button" class="ghost-btn" id="wizSkip">${t('tutorial.skip') || 'Skip'}</button>
          <div class="wizard-actions-right">
            ${currentStep > 0 ? `<button type="button" class="ghost-btn" id="wizBack">${t('tutorial.back') || 'Back'}</button>` : ''}
            <button type="button" class="secondary" id="wizCta">${escapeHtml(t(step.ctaKey) || step.ctaTarget)}</button>
            <button type="button" class="action-main" id="wizNext" ${nextDisabled}>${nextLabel}</button>
          </div>
        </div>
      </div>
    `;
    overlay.querySelector('#wizSkip').addEventListener('click', close);
    const back = overlay.querySelector('#wizBack');
    if (back) back.addEventListener('click', () => { currentStep = Math.max(0, currentStep - 1); render(); });
    overlay.querySelector('#wizCta').addEventListener('click', () => {
      try {
        activateView(step.ctaTarget);
        if (step.ctaScroll) {
          const el = document.getElementById(step.ctaScroll);
          if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      } catch (_) {}
    });
    overlay.querySelector('#wizNext').addEventListener('click', () => {
      if (!STEPS[currentStep].isDone(lastStatus)) return;
      if (isLast) { close(); return; }
      currentStep = Math.min(STEPS.length - 1, currentStep + 1);
      render();
    });
  };

  render();

  // Poll setup_status while overlay is open so the Next button enables
  // the moment the user completes the current step in another tab.
  pollHandle = setInterval(async () => {
    try {
      const s = await fetch('/api/setup/status').then((r) => r.json());
      const prevDone = STEPS[currentStep].isDone(lastStatus);
      lastStatus = s;
      const nowDone = STEPS[currentStep].isDone(lastStatus);
      if (prevDone !== nowDone) render();
    } catch { /* ignore */ }
  }, 1500);
}


window.addEventListener('load', () => {
  checkForUpdate().then(populateSystemInfo).catch(() => { /* offline ok */ });
  wireSystemSettings();
  // Defer tutorial to let dashboard render first.
  setTimeout(showFirstTimeTutorial, 800);
  wirePostScanModal();
  wireInfoTab();
  wireOnboardingPlaceholder();
  refreshOnboardingPlaceholder().catch(() => {});
  refreshPinnedStrip().catch(() => {});
  wireMobileChrome();
});

// Hamburger nav + off-canvas Career Coach drawer (mobile only). The CSS hides
// the toggle/FAB/overlay on wide viewports, so these handlers are inert there.
function wireMobileChrome() {
  const navToggle = document.getElementById("navToggle");
  const topnav = document.getElementById("topnav");
  const overlay = document.getElementById("mobileOverlay");
  const fab = document.getElementById("chatFab");
  const rail = document.querySelector(".right-rail");

  const showOverlay = () => {
    if (!overlay) return;
    overlay.hidden = false;
    overlay.classList.add("active");
  };
  const closeAll = () => {
    topnav?.classList.remove("open");
    navToggle?.setAttribute("aria-expanded", "false");
    rail?.classList.remove("drawer-open");
    if (overlay) { overlay.classList.remove("active"); overlay.hidden = true; }
    fab?.classList.remove("hidden");
  };

  navToggle?.addEventListener("click", () => {
    const open = topnav?.classList.toggle("open");
    navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) { rail?.classList.remove("drawer-open"); showOverlay(); fab?.classList.remove("hidden"); }
    else if (overlay) { overlay.classList.remove("active"); overlay.hidden = true; }
  });

  fab?.addEventListener("click", () => {
    rail?.classList.add("drawer-open");
    topnav?.classList.remove("open");
    fab.classList.add("hidden");
    showOverlay();
  });

  overlay?.addEventListener("click", closeAll);
}


/* =============================================================== */
/* v1.3.0: Multi-chat sessions                                       */
/* =============================================================== */

const ChatSessions = {
  active: localStorage.getItem("activeChatSession") || "default",
  list: [],
};

async function initChatSessions() {
  await refreshChatSessions();
  renderChatSessionDropdown();
  wireChatSessionUI();
}

async function refreshChatSessions() {
  let sessions = [];
  try {
    const res = await fetch("/api/chat/sessions");
    if (res.ok) {
      const payload = await res.json();
      sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
    } else {
      console.warn("chat sessions fetch returned", res.status);
    }
  } catch (err) {
    console.warn("chat sessions fetch failed", err);
  }
  // Always guarantee a usable list — the dropdown must never be empty.
  if (!sessions.length) {
    sessions = [{ id: "default", title: "", created_at: "", updated_at: "" }];
  }
  ChatSessions.list = sessions;
  if (!ChatSessions.list.find((s) => s.id === ChatSessions.active)) {
    ChatSessions.active = ChatSessions.list[0].id;
    localStorage.setItem("activeChatSession", ChatSessions.active);
  }
}

function renderChatSessionDropdown() {
  const sel = document.getElementById("chatSessionSelect");
  if (!sel) return;
  sel.innerHTML = ChatSessions.list.map((s) => {
    const label = (s.title || "").trim() || (s.id === "default" ? (t("chat.defaultSession") || "Default") : s.id);
    return `<option value="${escapeHtml(s.id)}" ${s.id === ChatSessions.active ? "selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
}

function wireChatSessionUI() {
  const sel = document.getElementById("chatSessionSelect");
  const newBtn = document.getElementById("chatSessionNew");
  const delBtn = document.getElementById("chatSessionDelete");
  if (sel) {
    sel.addEventListener("change", async () => {
      ChatSessions.active = sel.value;
      localStorage.setItem("activeChatSession", ChatSessions.active);
      await reloadChatHistoryForActive();
      await refreshPinnedStrip();
    });
  }
  if (newBtn) {
    newBtn.addEventListener("click", async () => {
      try {
        const res = await fetch("/api/chat/sessions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: "" }),
        }).then((r) => r.json());
        const s = res.session;
        if (s) {
          ChatSessions.active = s.id;
          localStorage.setItem("activeChatSession", ChatSessions.active);
          await refreshChatSessions();
          renderChatSessionDropdown();
          const box = document.getElementById("chatBox");
          if (box) box.innerHTML = "";
          await refreshPinnedStrip();
          showToast(t("toast.chatCreated") || "New chat created", "info");
        }
      } catch (err) {
        showToast(`${t("toast.chatCreateFailed") || "Could not create chat"}: ${err.message}`, "error");
      }
    });
  }
  if (delBtn) {
    delBtn.addEventListener("click", async () => {
      if (!ChatSessions.active) return;
      if (!confirm(t("chat.confirmDeleteSession") || "Delete this chat?")) return;
      try {
        await fetch(`/api/chat/sessions/${encodeURIComponent(ChatSessions.active)}`, { method: "DELETE" });
        ChatSessions.active = "default";
        localStorage.setItem("activeChatSession", "default");
        await refreshChatSessions();
        renderChatSessionDropdown();
        await reloadChatHistoryForActive();
        await refreshPinnedStrip();
        showToast(t("toast.chatDeleted") || "Chat deleted", "info");
      } catch (err) {
        showToast(`${t("toast.chatDeleteFailed") || "Could not delete chat"}: ${err.message}`, "error");
      }
    });
  }
}

async function reloadChatHistoryForActive() {
  const box = document.getElementById("chatBox");
  if (!box) return;
  box.innerHTML = "";
  try {
    const res = await fetch(`/api/chat/history?session_id=${encodeURIComponent(ChatSessions.active)}&limit=30`).then((r) => r.json());
    (res.messages || []).forEach((m) => appendChat(m.role, m.content));
  } catch (_) {}
}

/* =============================================================== */
/* v1.3.0: Pin jobs into chat                                        */
/* =============================================================== */

async function refreshPinnedStrip() {
  const strip = document.getElementById("chatPinnedStrip");
  if (!strip) return;
  try {
    const res = await fetch(`/api/chat/sessions/${encodeURIComponent(ChatSessions.active)}/pinned`).then((r) => r.json());
    const jobs = res.jobs || [];
    if (!jobs.length) {
      strip.innerHTML = "";
      strip.classList.add("hidden");
      return;
    }
    strip.classList.remove("hidden");
    strip.innerHTML = jobs.map((j) => `
      <span class="pinned-pill" data-job-id="${j.id}">
        <span class="material-symbols-outlined">push_pin</span>
        <span class="pinned-text">${escapeHtml(j.titolo || "?")} · ${escapeHtml(j.azienda || "?")}</span>
        <button type="button" class="pinned-remove" data-unpin="${j.id}" title="${t("chat.unpin") || "Unpin"}">×</button>
      </span>
    `).join("");
    strip.querySelectorAll("[data-unpin]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const jid = btn.getAttribute("data-unpin");
        await fetch(`/api/chat/sessions/${encodeURIComponent(ChatSessions.active)}/pin/${jid}`, { method: "DELETE" });
        await refreshPinnedStrip();
      });
    });
  } catch (_) {
    strip.innerHTML = "";
    strip.classList.add("hidden");
  }
}

async function pinJobToActiveSession(jobId) {
  try {
    await fetch(`/api/chat/sessions/${encodeURIComponent(ChatSessions.active)}/pin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: Number(jobId) }),
    });
    await refreshPinnedStrip();
    showToast(t("chat.pinned") || "Pinned to chat", "info");
  } catch (err) {
    showToast(`${t("chat.pinFailed") || "Pin failed"}: ${err.message}`, "error");
  }
}
window.pinJobToActiveSession = pinJobToActiveSession;

/* =============================================================== */
/* v1.3.0: Info tab wiring                                          */
/* =============================================================== */

function wireInfoTab() {
  document.querySelectorAll('[data-view="info"]').forEach((btn) => {
    btn.addEventListener("click", () => {
      try { activateView("info"); } catch (_) {}
    });
  });
}

/* =============================================================== */
/* v1.3.0: Onboarding placeholder                                   */
/* =============================================================== */

async function refreshOnboardingPlaceholder() {
  const ph = document.getElementById("onboardingPlaceholder");
  if (!ph) return;
  let status;
  try {
    status = await fetch("/api/setup/status").then((r) => r.json());
  } catch (_) {
    return;
  }
  const providerOk = !!status.provider_configured;
  const cvOk = !!status.cv_loaded;
  if (providerOk && cvOk) {
    ph.classList.add("hidden");
    return;
  }
  ph.classList.remove("hidden");
  const s1 = document.getElementById("onbStep1");
  const s2 = document.getElementById("onbStep2");
  if (s1) s1.classList.toggle("done", providerOk);
  if (s2) s2.classList.toggle("done", cvOk);
}

async function ensureProviderConfigured() {
  try {
    const status = await fetch("/api/setup/status").then((r) => r.json());
    if (status.provider_configured) return true;
  } catch (_) {
    return true; // network glitch — let backend reject if needed
  }
  showToast(t("errors.noProviderToast") || "Configure an AI provider key first", "error");
  try {
    activateView("settings");
    const cards = document.getElementById("providerCards");
    if (cards && cards.scrollIntoView) cards.scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (_) {}
  return false;
}
window.ensureProviderConfigured = ensureProviderConfigured;

function wireOnboardingPlaceholder() {
  document.querySelectorAll("#onboardingPlaceholder [data-onb-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.getAttribute("data-onb-action");
      try {
        if (target === "settings") {
          activateView("settings");
          const cards = document.getElementById("providerCards");
          if (cards && cards.scrollIntoView) cards.scrollIntoView({ behavior: "smooth", block: "center" });
        } else if (target === "profile") {
          activateView("profile");
          const cv = document.getElementById("cvFile");
          if (cv && cv.scrollIntoView) cv.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      } catch (_) {}
    });
  });
}
