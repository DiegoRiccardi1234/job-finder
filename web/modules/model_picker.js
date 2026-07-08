// Unified provider/model picker popover for the coach panel.
//
// It is purely presentational: it MIRRORS the two hidden native selects
// (#chatModelSelector = provider, #chatModelSelectorModel = model) that
// providers.js keeps populated (with ⭐ recommended and Free/Paid groups) and
// app.js reads on send. Selecting a row writes back into those selects, so the
// send path and the "save as default" toast keep working unchanged.
import { escapeHtml } from "./helpers.js";
import { t } from "./i18n.js";
import { populateChatModelSelector } from "./providers.js";

let _open = false;

function _els() {
  return {
    btn: document.getElementById("modelPickerBtn"),
    pop: document.getElementById("modelPickerPop"),
    provCol: document.getElementById("mpProviders"),
    modelCol: document.getElementById("mpModels"),
    provSel: document.getElementById("chatModelSelector"),
    modelSel: document.getElementById("chatModelSelectorModel"),
  };
}

function _labelFor() {
  const { provSel, modelSel } = _els();
  if (!provSel || !provSel.value) return t("coach.autoApi") || "Auto";
  const opt = provSel.selectedOptions[0];
  const provLabel = (opt && (opt.dataset.label || opt.textContent)) || provSel.value;
  const model = modelSel && modelSel.value ? modelSel.value : "";
  return model ? `${provLabel} · ${model}` : provLabel;
}

function _updateLabel() {
  const { btn } = _els();
  const lbl = btn && btn.querySelector(".model-picker-label");
  if (lbl) lbl.textContent = _labelFor();
}

function _renderProviders() {
  const { provCol, provSel } = _els();
  if (!provCol || !provSel) return;
  provCol.innerHTML = "";
  Array.from(provSel.options).forEach((opt) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className =
      "mp-row" + (opt.value === provSel.value ? " is-active" : "") + (opt.disabled ? " is-muted" : "");
    row.textContent = opt.textContent;
    if (opt.disabled) row.disabled = true;
    else row.addEventListener("click", () => _selectProvider(opt.value));
    provCol.appendChild(row);
  });
}

function _renderModels() {
  const { modelCol, modelSel } = _els();
  if (!modelCol || !modelSel) return;
  modelCol.innerHTML = "";
  Array.from(modelSel.options).forEach((opt) => {
    // Disabled + empty value = a "── Free/Paid ──" group separator → header.
    if (opt.disabled && !opt.value) {
      const head = document.createElement("div");
      head.className = "mp-group";
      head.textContent = opt.textContent.replace(/[─-]{2,}/g, "").trim();
      modelCol.appendChild(head);
      return;
    }
    const row = document.createElement("button");
    row.type = "button";
    row.className = "mp-row" + (opt.value === modelSel.value ? " is-active" : "");
    const isFree = /:free\b/.test(opt.value);
    const badge = opt.value
      ? `<span class="model-badge ${isFree ? "free" : "paid"}">${
          isFree ? t("coach.badgeFree") || "free" : t("coach.badgePaid") || "paid"
        }</span>`
      : "";
    row.innerHTML = `<span class="mp-model-name">${escapeHtml(opt.textContent)}</span>${badge}`;
    row.addEventListener("click", () => _selectModel(opt.value));
    modelCol.appendChild(row);
  });
}

async function _selectProvider(value) {
  const { provSel, modelSel } = _els();
  if (!provSel) return;
  provSel.value = value;
  // Repopulate the hidden model select for the new provider (same fn the native
  // change handler uses), then reset the model to Auto and re-render.
  await populateChatModelSelector(value);
  if (modelSel) modelSel.value = "";
  _renderProviders();
  _renderModels();
  _updateLabel();
}

function _selectModel(value) {
  const { modelSel } = _els();
  if (!modelSel) return;
  modelSel.value = value;
  _updateLabel();
  _close();
}

function _onDocClick(e) {
  const { pop, btn } = _els();
  if (!pop || !btn) return;
  if (!pop.contains(e.target) && !btn.contains(e.target)) _close();
}

function _onKey(e) {
  if (e.key === "Escape") _close();
}

function _openPop() {
  const { pop, btn } = _els();
  if (!pop || !btn) return;
  _renderProviders();
  _renderModels();
  pop.classList.remove("hidden");
  btn.setAttribute("aria-expanded", "true");
  _open = true;
  document.addEventListener("click", _onDocClick, true);
  document.addEventListener("keydown", _onKey);
}

function _close() {
  const { pop, btn } = _els();
  if (pop) pop.classList.add("hidden");
  if (btn) btn.setAttribute("aria-expanded", "false");
  _open = false;
  document.removeEventListener("click", _onDocClick, true);
  document.removeEventListener("keydown", _onKey);
}

export function initModelPicker() {
  const { btn } = _els();
  if (!btn) return;
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (_open) _close();
    else _openPop();
  });
  _updateLabel();
}

export function refreshModelPickerLabel() {
  _updateLabel();
}
