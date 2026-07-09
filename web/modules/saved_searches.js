// Saved searches (F7): persist the Job Search filter state as named presets and
// re-run one with a click. The form snapshot/restore lives in app.js (which owns
// the tag inputs); this module handles the list UI, the API and the save button.
import { api, escapeHtml, showToast } from "./helpers.js";
import { t } from "./i18n.js";

let _readConfig = null;
let _applyConfig = null;
let _submitScan = null;

function _summarize(cfg) {
  cfg = cfg || {};
  const parts = [];
  if (Array.isArray(cfg.terms) && cfg.terms.length) parts.push(cfg.terms.join(", "));
  if (Array.isArray(cfg.location) && cfg.location.length) parts.push(cfg.location.join(", "));
  if (cfg.is_remote) parts.push(t("jobs.remote"));
  return parts.join(" · ") || "—";
}

export async function loadSavedSearches() {
  const list = document.getElementById("savedSearchList");
  const empty = document.getElementById("savedSearchEmpty");
  if (!list) return;
  let data;
  try {
    data = await api("/api/saved-searches");
  } catch {
    return;
  }
  const searches = data.searches || [];
  list.innerHTML = "";
  if (!searches.length) {
    if (empty) empty.style.display = "block";
    return;
  }
  if (empty) empty.style.display = "none";
  for (const s of searches) {
    const item = document.createElement("div");
    item.className = "saved-search-item";
    item.innerHTML =
      `<button type="button" class="saved-search-run" data-id="${s.id}">` +
      `<span class="material-symbols-outlined">play_arrow</span>` +
      `<span class="saved-search-main"><strong>${escapeHtml(s.name)}</strong>` +
      `<span class="saved-search-sub">${escapeHtml(_summarize(s.config))}</span></span></button>` +
      `<button type="button" class="saved-search-del ghost-btn small" data-del="${s.id}" aria-label="Delete"><span class="material-symbols-outlined">delete</span></button>`;
    list.appendChild(item);
  }
  list.querySelectorAll("[data-id]").forEach((el) => {
    el.addEventListener("click", () => {
      const s = searches.find((x) => String(x.id) === el.dataset.id);
      if (!s) return;
      if (typeof _applyConfig === "function") _applyConfig(s.config);
      showToast(t("savedSearch.loaded", { name: s.name }), "info");
      if (typeof _submitScan === "function") _submitScan();
    });
  });
  list.querySelectorAll("[data-del]").forEach((el) => {
    el.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      try {
        await api(`/api/saved-searches/${el.dataset.del}`, { method: "DELETE" });
        loadSavedSearches();
      } catch (err) {
        showToast(`${t("toast.actionError")}: ${err.message}`, "error");
      }
    });
  });
}

function _suggestName(cfg) {
  if (cfg && Array.isArray(cfg.terms) && cfg.terms.length) {
    return cfg.terms.join(", ").slice(0, 40);
  }
  return "";
}

export function initSavedSearches({ readConfig, applyConfig, submitScan } = {}) {
  _readConfig = readConfig || null;
  _applyConfig = applyConfig || null;
  _submitScan = submitScan || null;
  const saveBtn = document.getElementById("saveSearchBtn");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      if (typeof _readConfig !== "function") return;
      const cfg = _readConfig();
      const name = (window.prompt(t("savedSearch.namePrompt"), _suggestName(cfg)) || "").trim();
      if (!name) return;
      try {
        await api("/api/saved-searches", {
          method: "POST",
          body: JSON.stringify({ name, config: cfg }),
        });
        showToast(t("savedSearch.saved"), "info");
        loadSavedSearches();
      } catch (err) {
        showToast(`${t("toast.actionError")}: ${err.message}`, "error");
      }
    });
  }
}
