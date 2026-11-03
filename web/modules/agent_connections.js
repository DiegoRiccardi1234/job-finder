import { api, escapeHtml, showToast } from "./helpers.js";
import { getCurrentLang, t } from "./i18n.js";

const TASKS = ["chat", "scoring", "cv"];
const $ = (id) => document.getElementById(id);
const tr = (key, values = {}) => t(`agentConnections.${key}`, values);
const h = escapeHtml;
let connections = [];
let routing = {};
let runtimeStatus = {};
let csrfToken = "";
let sessionPromise = null;
let loading = false;
let initialized = false;
let onChanged = () => {};

function message(value, error = false) {
  const el = $("agentConnectionsMessage");
  if (!el) return;
  el.textContent = value;
  el.classList.toggle("agent-message-error", error);
}

function connectionName(id) {
  return connections.find((item) => item.id === id)?.name || id;
}

function option(id, selected = "") {
  return `<option value="${h(id)}"${id === selected ? " selected" : ""}>${h(connectionName(id))}</option>`;
}

function options(selected = "", includeLegacy = false) {
  const first = `<option value="">${h(tr(includeLegacy ? "legacyRouting" : "none"))}</option>`;
  const selectable = connections.filter((item) => (item.enabled && (item.kind !== "api" || item.configured)) || item.id === selected);
  if (selected && !selectable.some((item) => item.id === selected)) {
    return first + option(selected, selected) + selectable.map((item) => option(item.id, selected)).join("");
  }
  return first + selectable.map((item) => option(item.id, selected)).join("");
}

function timestamp(value) {
  if (!value) return "";
  const date = new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(getCurrentLang());
}

function quotaText(status) {
  const quota = status?.quota;
  if (!quota) return tr("quotaUnavailable");
  const windows = Array.isArray(quota) ? quota : quota.windows || [quota.primary, quota.secondary].filter(Boolean);
  if (!windows.length) windows.push(quota);
  const parts = [];
  for (const window of windows) {
    const used = window.used_percent ?? window.used_percentage ?? window.usedPercent;
    const remaining = window.remaining_percent ?? (typeof used === "number" ? 100 - used : null);
    const reset = timestamp(window.resets_at || window.reset_at || window.resetsAt);
    if (typeof remaining === "number" && Number.isFinite(remaining)) {
      const name = window.name || window.label || window.window || "";
      parts.push(`${name ? `${name}: ` : ""}${tr("quotaRemaining", { percent: Math.max(0, Math.min(100, Math.round(remaining))) })}${reset ? ` · ${tr("resets", { time: reset })}` : ""}`);
    }
  }
  return parts.length ? parts.join(" · ") : tr("quotaUnavailable");
}

function runtimeInfo(connection) {
  const status = runtimeStatus[connection.kind] || connection.status;
  if (!status || typeof status !== "object") return "";
  let state = tr("loginRequired");
  if (status.installed === false) state = tr("notInstalled");
  else if (status.authenticated) state = tr("connected");
  const details = [state];
  if (status.plan) details.push(tr("plan", { name: status.plan }));
  if (status.version) details.push(tr("version", { version: status.version }));
  const checked = timestamp(status.checked_at);
  return `<p class="micro">${h(details.join(" · "))}</p><p class="micro">${h(quotaText(status))}</p>${checked ? `<p class="micro agent-checked">${h(tr("checkedAt", { time: checked }))}</p>` : ""}`;
}

function renderConnections() {
  const host = $("agentConnectionsList");
  if (!host) return;
  const referenced = new Set(Object.values(routing).flatMap((route) => [route.primary, ...(route.fallbacks || [])]));
  host.innerHTML = connections.filter((connection) => connection.kind !== "api" || connection.configured || referenced.has(connection.id)).map((connection) => {
    const native = ["codex", "claude"].includes(connection.kind);
    const readonly = connection.kind === "api";
    const source = native ? tr("subscription") : readonly ? tr("existingApi") : tr("compatibleEndpoint");
    return `<div class="agent-connection" data-connection-id="${h(connection.id)}">
      <div class="agent-connection-heading"><strong>${h(connection.name)}</strong><span class="agent-connection-state">${h(tr(connection.enabled && (!readonly || connection.configured) ? "enabled" : "disabled"))}</span></div>
      <p class="micro">${h(source)}${connection.model ? ` · ${h(connection.model)}` : ""}</p>
      ${connection.base_url ? `<p class="micro agent-endpoint">${h(connection.base_url)}</p>` : ""}
      ${native ? runtimeInfo(connection) : ""}
      <div class="agent-row-actions">
        ${!readonly ? `<button type="button" class="ghost-btn small" data-agent-action="edit" data-id="${h(connection.id)}">${h(tr("edit"))}</button>` : ""}
        ${native ? `<button type="button" class="ghost-btn small" data-agent-action="diagnose" data-id="${h(connection.id)}">${h(tr("diagnose"))}</button>` : ""}
        ${connection.enabled && !readonly ? `<button type="button" class="ghost-btn small" data-agent-action="test" data-id="${h(connection.id)}">${h(tr("test"))}</button>` : ""}
        ${connection.kind === "openai_compatible" ? `<button type="button" class="ghost-btn small" data-agent-action="delete" data-id="${h(connection.id)}">${h(tr("remove"))}</button>` : ""}
      </div>
    </div>`;
  }).join("") || `<p class="micro">${h(tr("empty"))}</p>`;
}

function renderRouting() {
  const host = $("agentRouting");
  if (!host) return;
  host.innerHTML = TASKS.map((task) => {
    const route = routing[task] || { primary: null, fallbacks: [] };
    return `<fieldset class="agent-route" data-task="${task}">
      <legend>${h(tr(`task.${task}`))}</legend>
      <label class="agent-field"><span class="micro">${h(tr("primary"))}</span><select data-agent-primary aria-label="${h(tr("primaryFor", { task: tr(`task.${task}`) }))}">${options(route.primary || "", true)}</select></label>
      <div class="agent-fallback-list">${(route.fallbacks || []).map((id, index) => fallbackMarkup(task, id, index)).join("")}</div>
      <button type="button" class="ghost-btn small" data-agent-action="add-fallback" data-task="${task}">${h(tr("addFallback"))}</button>
    </fieldset>`;
  }).join("");
}

function fallbackMarkup(task, id, index) {
  return `<div class="agent-fallback-row"><label class="agent-field"><span class="micro">${h(tr("fallbackPosition", { position: index + 1 }))}</span><select data-agent-fallback aria-label="${h(tr("fallbackFor", { task: tr(`task.${task}`), position: index + 1 }))}">${options(id)}</select></label><button type="button" class="ghost-btn small" data-agent-action="remove-fallback" aria-label="${h(tr("removeFallback"))}">${h(tr("remove"))}</button></div>`;
}

function collectRouting() {
  const value = {};
  for (const fieldset of document.querySelectorAll("#agentRouting fieldset[data-task]")) {
    const primary = fieldset.querySelector("[data-agent-primary]").value || null;
    const fallbacks = [...fieldset.querySelectorAll("[data-agent-fallback]")].map((el) => el.value).filter(Boolean);
    if ((!primary && fallbacks.length) || new Set([primary, ...fallbacks].filter(Boolean)).size !== [primary, ...fallbacks].filter(Boolean).length) {
      throw new Error(tr("routingDuplicate"));
    }
    value[fieldset.dataset.task] = { primary, fallbacks };
  }
  return value;
}

function editConnection(connection = null) {
  const form = $("agentConnectionForm");
  if (!form) return;
  form.reset();
  $("agentConnectionId").value = connection?.id || "";
  $("agentConnectionName").value = connection?.name || "";
  $("agentConnectionKind").value = connection?.kind || "openai_compatible";
  $("agentConnectionKind").disabled = true;
  $("agentConnectionUrl").value = connection?.base_url || "";
  $("agentConnectionModel").value = connection?.model || "";
  $("agentConnectionKey").disabled = false;
  $("agentConnectionEnabled").checked = Boolean(connection?.enabled);
  const compatible = !connection || connection.kind === "openai_compatible";
  $("agentEndpointFields").hidden = !compatible;
  $("agentConnectionUrl").required = compatible;
  $("agentConnectionKey").placeholder = tr(connection?.secret_configured ? "keyPreserved" : "keyOptional");
  form.hidden = false;
  $("agentConnectionEditorTitle").textContent = tr(connection ? "editConnection" : "newConnection");
  $("agentConnectionName").focus();
}

async function saveConnection(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const id = $("agentConnectionId").value || `endpoint-${crypto.randomUUID()}`;
  const kind = $("agentConnectionKind").value;
  const payload = {
    name: $("agentConnectionName").value.trim(), kind,
    model: $("agentConnectionModel").value.trim() || null,
    enabled: $("agentConnectionEnabled").checked,
  };
  if (kind === "openai_compatible") {
    payload.base_url = $("agentConnectionUrl").value.trim();
    const key = $("agentConnectionKey").value;
    if (key || $("agentConnectionClearKey").checked) payload.api_key = $("agentConnectionClearKey").checked ? "" : key;
  }
  await busy(form.querySelector('[type="submit"]'), async () => {
    await uiRequest(`/api/ai/connections/${encodeURIComponent(id)}`, "PUT", payload);
    form.reset();
    form.hidden = true;
    await loadAgentConnections();
    message(tr("saved"));
    onChanged();
  });
}

async function busy(button, action) {
  if (button) button.disabled = true;
  try { await action(); }
  catch (error) { message(`${tr("failed")}: ${error.message}`, true); }
  finally { if (button?.isConnected) button.disabled = false; }
}

async function uiSession() {
  if (csrfToken) return csrfToken;
  if (!sessionPromise) sessionPromise = (async () => {
    const result = await api("/api/agent/ui/session", { method: "POST", headers: { "Content-Type": "application/json", "X-JobFinder-UI": "1" }, body: "{}" });
    csrfToken = result.csrf_token;
    if (!csrfToken) throw new Error(tr("sessionUnavailable"));
    return csrfToken;
  })().finally(() => { sessionPromise = null; });
  return sessionPromise;
}

async function uiMutation(path) {
  return uiRequest(path, "POST", {});
}

async function uiRequest(path, method, payload) {
  const csrf = await uiSession();
  try {
    return await api(path, { method, headers: { "Content-Type": "application/json", "X-JobFinder-CSRF": csrf }, body: JSON.stringify(payload) });
  } catch (error) {
    // Expired UI sessions require another deliberate click; do not replay a
    // confirmation whose server-side outcome might be uncertain.
    csrfToken = "";
    throw error;
  }
}

export async function loadAgentProposals() {
  const host = $("agentProposals");
  if (!host) return;
  await uiSession();
  let result;
  try { result = await api("/api/agent/proposals"); }
  catch (error) { csrfToken = ""; throw error; }
  const items = (result.proposals || []).filter((item) => item.status === "pending");
  host.innerHTML = items.map((item) => `<article class="agent-proposal" data-proposal-id="${h(item.id)}">
    <h5>${h(item.summary || tr("proposal"))}</h5>
    <pre class="agent-proposal-payload">${h(JSON.stringify(item.payload || {}, null, 2))}</pre>
    <div class="agent-row-actions"><button type="button" class="secondary" data-agent-action="confirm-proposal" data-id="${h(item.id)}">${h(tr("confirmProposal"))}</button><button type="button" class="ghost-btn small" data-agent-action="reject-proposal" data-id="${h(item.id)}">${h(tr("reject"))}</button></div>
  </article>`).join("") || `<p class="micro">${h(tr("noProposals"))}</p>`;
}

async function generateSetup() {
  const result = await uiMutation("/api/agent/setup");
  const host = $("agentMcpConfig");
  host.hidden = false;
  host.innerHTML = Object.entries(result.configs || {}).map(([client, config]) => `<div class="agent-config-block"><label class="agent-field"><span>${h({ codex: "Codex / ChatGPT Desktop", claude_desktop: "Claude Desktop", cowork: "Claude Cowork" }[client] || client)}</span><textarea rows="7" readonly spellcheck="false" data-agent-config="${h(client)}">${h(typeof config === "string" ? config : JSON.stringify(config, null, 2))}</textarea></label><button type="button" class="ghost-btn small" data-agent-action="copy-config" data-client="${h(client)}">${h(tr("copy"))}</button></div>`).join("");
  if (!Object.keys(result.configs || {}).length) host.textContent = tr("setupUnavailable");
}

async function handleClick(event) {
  const button = event.target.closest("[data-agent-action]");
  if (!button || !$("agentConnectionsCard")?.contains(button)) return;
  const action = button.dataset.agentAction;
  const connection = connections.find((item) => item.id === button.dataset.id);
  if (action === "new") return editConnection();
  if (action === "edit" && connection) return editConnection(connection);
  if (action === "cancel-edit") { $("agentConnectionForm").reset(); $("agentConnectionForm").hidden = true; return; }
  if (action === "add-fallback") {
    const task = button.dataset.task;
    const list = button.closest("fieldset").querySelector(".agent-fallback-list");
    if (list.children.length >= connections.filter((item) => item.enabled).length) return;
    list.insertAdjacentHTML("beforeend", fallbackMarkup(task, "", list.children.length));
    list.lastElementChild.querySelector("select").focus();
    return;
  }
  if (action === "remove-fallback") {
    const row = button.closest(".agent-fallback-row");
    const fieldset = row.closest("fieldset");
    row.remove();
    fieldset.querySelector("[data-agent-action='add-fallback']").focus();
    return;
  }
  await busy(button, async () => {
    if (action === "refresh") await loadAgentConnections();
    if (action === "diagnose" && connection) {
      await uiSession();
      let result;
      try { result = await api(`/api/ai/runtimes/${encodeURIComponent(connection.kind)}/status`); }
      catch (error) { csrfToken = ""; throw error; }
      runtimeStatus[connection.kind] = result.status || result;
      renderConnections();
    }
    if (action === "test" && connection) {
      message(tr("testing"));
      const result = await uiMutation(`/api/ai/connections/${encodeURIComponent(connection.id)}/test`);
      if (result.ok === false || result.success === false) throw new Error(result.error || tr("failed"));
      message(tr("testPassed"));
    }
    if (action === "delete" && connection) {
      // Removing a connection never removes offers or their history. The
      // backend refuses removal while any routing rule still references it.
      await uiRequest(`/api/ai/connections/${encodeURIComponent(connection.id)}`, "DELETE", {});
      await loadAgentConnections();
      message(tr("removed"));
      onChanged();
    }
    if (action === "save-routing") {
      const value = collectRouting();
      await uiRequest("/api/ai/routing", "PUT", value);
      routing = value;
      message(tr("saved"));
      onChanged();
    }
    if (action === "setup") await generateSetup();
    if (action === "refresh-proposals") await loadAgentProposals();
    if (action === "confirm-proposal" || action === "reject-proposal") {
      await uiMutation(`/api/agent/proposals/${encodeURIComponent(button.dataset.id)}/${action === "confirm-proposal" ? "confirm" : "reject"}`);
      await loadAgentProposals();
      message(tr(action === "confirm-proposal" ? "proposalConfirmed" : "proposalRejected"));
      onChanged();
      document.dispatchEvent(new Event("agent-proposal-updated"));
    }
    if (action === "copy-config") {
      const textarea = [...$("agentMcpConfig").querySelectorAll("[data-agent-config]")].find((el) => el.dataset.agentConfig === button.dataset.client);
      if (!textarea) return;
      try { await navigator.clipboard.writeText(textarea.value); showToast(tr("copied"), "info"); }
      catch { textarea.focus(); textarea.select(); message(tr("copyManually")); }
    }
  });
}

export async function loadAgentConnections() {
  if (loading || !$("agentConnectionsCard")) return;
  loading = true;
  const saveRouting = $("agentConnectionsCard").querySelector('[data-agent-action="save-routing"]');
  saveRouting.disabled = true;
  try {
    const data = await api("/api/ai/connections");
    connections = data.connections || [];
    routing = data.routing || {};
    runtimeStatus = { ...runtimeStatus, ...(data.runtime_status || {}) };
    renderConnections();
    renderRouting();
    saveRouting.disabled = false;
    message("");
  } catch (error) { message(`${tr("failed")}: ${error.message}`, true); }
  finally { loading = false; }
}

export function initAgentConnections(deps = {}) {
  onChanged = deps.onChanged || (() => {});
  if (initialized || !$("agentConnectionsCard")) return;
  initialized = true;
  $("agentConnectionsCard").addEventListener("click", handleClick);
  $("agentConnectionForm").addEventListener("submit", saveConnection);
  $("agentConnectionClearKey").addEventListener("change", (event) => {
    $("agentConnectionKey").disabled = event.target.checked;
    if (event.target.checked) $("agentConnectionKey").value = "";
  });
}
