const fs = require("fs");
const path = require("path");
const { test, expect } = require("@playwright/test");

const ROOT = path.resolve(__dirname, "..", "..");
const ORIGIN = "http://127.0.0.1:8123";

async function openSettings(page, options = {}) {
  const writes = [];
  const errors = [];
  const missing = [];
  const state = {
    connections: [
      { id: "codex", name: "Codex", kind: "codex", enabled: true, configured: true },
      { id: "claude", name: "Claude Code", kind: "claude", enabled: true, configured: true },
      { id: "endpoint-demo", name: "Hermes demo", kind: "openai_compatible", base_url: "http://127.0.0.1:8642/v1", model: "hermes-agent", enabled: true, configured: true, secret_configured: true },
      { id: "api:deepseek", name: "DeepSeek API", kind: "api", enabled: true, configured: true },
      { id: "api:openai", name: "OpenAI without key", kind: "api", enabled: true, configured: false },
    ],
    routing: Object.fromEntries(["chat", "scoring", "cv"].map((task) => [task, { primary: null, fallbacks: [] }])),
    proposals: [{ id: "proposal-1", kind: "preferences", status: "pending", summary: "Cambia sede preferita", payload: { base_cities: ["Torino", "Roma"], description: "<img src=x onerror=alert(1)>" } }],
  };
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (item) => { if (item.text().includes("missing translation")) missing.push(item.text()); });
  await page.addInitScript(() => { localStorage.setItem("language", "it"); localStorage.setItem("tutorialSeen", "1"); });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== ORIGIN) return route.abort();
    const pathname = url.pathname;
    if (!pathname.startsWith("/api/")) {
      const local = path.resolve(ROOT, pathname === "/" ? "web/index.html" : `.${decodeURIComponent(pathname)}`);
      if (!local.startsWith(`${ROOT}${path.sep}`) || !fs.existsSync(local)) return route.fulfill({ status: 404, body: "Not found" });
      const mime = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml" }[path.extname(local)] || "application/octet-stream";
      return route.fulfill({ contentType: mime, body: fs.readFileSync(local) });
    }
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    if (method !== "GET") writes.push({ path: pathname, method, body, headers: request.headers() });
    let payload = {};
    if (pathname === "/api/health") payload = { provider: { active_provider: options.runtimeOnly ? "none" : "google", active_model: "demo", available: !options.runtimeOnly, connections_configured: Boolean(options.runtimeOnly) }, keys: {}, preferences: {} };
    if (pathname === "/api/providers/keys/status") payload = { keys: { primary_provider: "google", google_configured: !options.runtimeOnly, connections_configured: Boolean(options.runtimeOnly) }, provider: { active_provider: options.runtimeOnly ? "none" : "google", available: !options.runtimeOnly, providers: {} } };
    if (pathname === "/api/setup/status") payload = { cv_loaded: true, provider_configured: true, active_cv: { id: 4, source_name: "Demo.pdf" }, search_goal: "Junior", applications_pending_count: 0 };
    if (pathname === "/api/profile/readiness") payload = { items: [], suggested_terms: ["Junior"], suggested_locations: ["Torino"] };
    if (pathname === "/api/profile") payload = { profile: { id: 4, source_name: "Demo.pdf", summary_json: { skills: [] } } };
    if (pathname === "/api/profiles") payload = { profiles: [], active_profile_id: 4 };
    if (pathname === "/api/profile/matching-facts") payload = { sources: {}, missing: [] };
    if (pathname === "/api/jobs") payload = { jobs: [], total: 0, counts: {} };
    if (pathname === "/api/chat/sessions") payload = { sessions: [{ id: "default", title: "" }] };
    if (pathname === "/api/reminders") payload = { reminders: [], stale: [], count: 0 };
    if (pathname === "/api/ai/connections") payload = { connections: state.connections, routing: state.routing };
    if (pathname === "/api/agent/ui/session") payload = { csrf_token: "ui-csrf-test" };
    if (pathname === "/api/ai/runtimes/codex/status") payload = { installed: true, version: "0.166.0", authenticated: true, plan: "pro", checked_at: "2026-11-03T12:00:00Z", quota: { primary: { usedPercent: 24, windowDurationMins: 300, resetsAt: 1793710800 } } };
    if (pathname === "/api/ai/runtimes/claude/status") payload = { installed: true, version: "2.1.261", authenticated: true, plan: "max", checked_at: "2026-11-03T12:00:00Z", quota: null };
    if (pathname.startsWith("/api/ai/connections/") && !pathname.endsWith("/test")) {
      const id = decodeURIComponent(pathname.split("/").at(-1));
      if (method === "PUT") {
        const connection = state.connections.find((item) => item.id === id);
        if (connection) Object.assign(connection, body, { secret_configured: body.api_key === "" ? false : Boolean(body.api_key || connection.secret_configured) });
        else state.connections.push({ id, ...body, configured: true, secret_configured: Boolean(body.api_key) });
        state.connections.forEach((item) => delete item.api_key);
      }
      if (method === "DELETE") state.connections = state.connections.filter((item) => item.id !== id);
      payload = { ok: true };
    }
    if (pathname.endsWith("/test")) payload = { ok: true };
    if (pathname === "/api/ai/routing" && method === "PUT") { state.routing = body; payload = { ok: true }; }
    if (pathname === "/api/agent/proposals") payload = { proposals: state.proposals };
    if (/\/proposals\/[^/]+\/(confirm|reject)$/.test(pathname)) { state.proposals = []; payload = { ok: true, status: pathname.endsWith("confirm") ? "confirmed" : "rejected" }; }
    if (pathname === "/api/agent/setup") payload = { endpoint: "/mcp/", token_file: "C:\\Users\\demo\\JobFinder\\agent-token", configs: { codex: '[mcp_servers.jobfinder]\ncommand = "JobFinderMCP.exe"', claude_desktop: { mcpServers: { jobfinder: { command: "JobFinderMCP.exe", args: ["--token-file", "C:\\Users\\demo\\JobFinder\\agent-token"] } } }, cowork: { mcpServers: { jobfinder: { command: "JobFinderMCP.exe" } } } } };
    return route.fulfill({ json: payload });
  });
  await page.goto("/");
  await expect(page.locator("#workflowActiveCv")).toContainText("Demo.pdf");
  await page.locator('[data-view="settings"]').first().click();
  await expect(page.locator("#agentConnectionsList")).toContainText("Codex");
  await expect(page.locator("#agentConnectionForm")).toBeHidden();
  await expect(page.locator('[data-connection-id="api:openai"]')).toHaveCount(0);
  return { state, writes, errors, missing };
}

test("runtime diagnosis shows only reported quota and never starts AI implicitly", async ({ page }) => {
  const { writes, errors } = await openSettings(page);
  expect(writes.filter((item) => item.path.endsWith("/test"))).toEqual([]);
  const codex = page.locator('[data-connection-id="codex"]');
  await codex.getByRole("button", { name: "Verifica runtime e accesso" }).click();
  await expect(codex).toContainText("Piano: pro");
  await expect(codex).toContainText("76% disponibile");
  const claude = page.locator('[data-connection-id="claude"]');
  await claude.getByRole("button", { name: "Verifica runtime e accesso" }).click();
  await expect(claude).toContainText("Piano: max");
  await expect(claude).toContainText("Quota residua dell'abbonamento: non disponibile");
  await codex.getByRole("button", { name: "Prova connessione" }).click();
  await expect(page.locator("#agentConnectionsMessage")).toHaveText("Prova della connessione riuscita.");
  expect(writes.filter((item) => item.path.endsWith("/test"))).toHaveLength(1);
  expect(writes.find((item) => item.path.endsWith("/test")).headers["x-jobfinder-csrf"]).toBe("ui-csrf-test");
  expect(errors).toEqual([]);
});

test("a subscription connection satisfies setup without any API key", async ({ page }) => {
  await openSettings(page, { runtimeOnly: true });
  await expect(page.locator("#noApiKeyBanner")).toHaveCount(0);
  await page.locator('[data-view="jobs"]').first().click();
  await expect(page.locator("#view-jobs")).toHaveClass(/is-active/);
});

test("editing an endpoint keeps a saved key unless removal is explicit", async ({ page }) => {
  const { writes } = await openSettings(page);
  await page.locator('[data-connection-id="endpoint-demo"]').getByRole("button", { name: "Modifica", exact: true }).click();
  await expect(page.getByLabel("Chiave API (facoltativa)", { exact: true })).toHaveValue("");
  await page.getByLabel("Nome", { exact: true }).fill("Hermes <script>demo</script>");
  await page.getByRole("button", { name: "Salva connessione", exact: true }).click();
  await expect(page.locator("#agentConnectionForm")).toBeHidden();
  await expect(page.locator('[data-connection-id="endpoint-demo"]')).toContainText("Hermes <script>demo</script>");
  const firstSave = writes.find((item) => item.path === "/api/ai/connections/endpoint-demo");
  expect(firstSave.body).not.toHaveProperty("api_key");
  expect(firstSave.headers["x-jobfinder-csrf"]).toBe("ui-csrf-test");
  await page.locator('[data-connection-id="endpoint-demo"]').getByRole("button", { name: "Modifica", exact: true }).click();
  await page.getByLabel("Rimuovi la chiave salvata").check();
  await page.getByRole("button", { name: "Salva connessione", exact: true }).click();
  await expect(page.locator("#agentConnectionsMessage")).toHaveText("Impostazioni salvate.");
  expect(writes.filter((item) => item.path === "/api/ai/connections/endpoint-demo").at(-1).body.api_key).toBe("");
  await expect(page.locator("#agentConnectionsList script")).toHaveCount(0);
});

test("task routing preserves explicit fallback order and rejects duplicates", async ({ page }) => {
  const { state, writes } = await openSettings(page);
  const chat = page.locator('#agentRouting fieldset[data-task="chat"]');
  await chat.getByLabel("Connessione principale per Chat di orientamento").selectOption("codex");
  await chat.getByRole("button", { name: "Aggiungi fallback" }).click();
  await chat.getByLabel("Fallback 1 per Chat di orientamento").selectOption("claude");
  await chat.getByRole("button", { name: "Aggiungi fallback" }).click();
  await chat.getByLabel("Fallback 2 per Chat di orientamento").selectOption("endpoint-demo");
  await page.getByRole("button", { name: "Salva motori delle attività" }).click();
  await expect(page.locator("#agentConnectionsMessage")).toHaveText("Impostazioni salvate.");
  expect(state.routing.chat).toEqual({ primary: "codex", fallbacks: ["claude", "endpoint-demo"] });
  expect(state.routing.scoring).toEqual({ primary: null, fallbacks: [] });
  await chat.getByLabel("Fallback 2 per Chat di orientamento").selectOption("codex");
  await page.getByRole("button", { name: "Salva motori delle attività" }).click();
  await expect(page.locator("#agentConnectionsMessage")).toContainText("una sola volta");
  expect(writes.filter((item) => item.path === "/api/ai/routing")).toHaveLength(1);
});

test("MCP setup and proposal confirmation require separate explicit UI gestures", async ({ page }) => {
  const { writes } = await openSettings(page);
  expect(writes.filter((item) => item.path === "/api/agent/setup")).toEqual([]);
  await page.getByText("Collega un assistente desktop o CLI tramite MCP", { exact: true }).click();
  await page.getByRole("button", { name: "Genera configurazione MCP" }).click();
  await expect(page.locator("#agentMcpConfig textarea")).toHaveCount(3);
  expect(writes.find((item) => item.path === "/api/agent/setup").headers["x-jobfinder-csrf"]).toBe("ui-csrf-test");
  await page.getByRole("button", { name: "Controlla proposte" }).click();
  await expect(page.locator("#agentProposals")).toContainText("Torino");
  await expect(page.locator("#agentProposals img")).toHaveCount(0);
  expect(writes.filter((item) => item.path.endsWith("/confirm"))).toEqual([]);
  await page.getByRole("button", { name: "Conferma queste modifiche" }).click();
  await expect(page.locator("#agentProposals")).toContainText("Nessuna modifica in attesa di conferma.");
  expect(writes.filter((item) => item.path.endsWith("/confirm"))).toHaveLength(1);
});

test("connection settings stay usable on mobile and in all five languages", async ({ page }) => {
  const { missing, errors } = await openSettings(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("#agentConnectionsCard")).toBeVisible();
  await page.locator('[data-agent-action="new"]').click();
  await page.getByLabel("Nome", { exact: true }).fill("Local service");
  await page.getByLabel("URL di base", { exact: true }).fill("http://127.0.0.1:8642/v1");
  await page.getByLabel("Abilita questa connessione").check();
  await page.getByRole("button", { name: "Salva connessione", exact: true }).click();
  await expect(page.locator("#agentConnectionsList")).toContainText("Local service");
  const labels = { en: "AI connections and assistants", es: "Conexiones de IA y asistentes", fr: "Connexions IA et assistants", de: "KI-Verbindungen und Assistenten", it: "Connessioni AI e assistenti" };
  for (const [lang, label] of Object.entries(labels)) {
    await page.locator("#langSelect").selectOption(lang);
    await expect(page.locator("#agentConnectionsCard h3")).toContainText(label);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  }
  expect(missing).toEqual([]);
  expect(errors).toEqual([]);
});
