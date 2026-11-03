const path = require("path");
const { defineConfig } = require("@playwright/test");

// All documents, assets and API calls are intercepted by the spec. No app
// process, scheduler, provider or user database is opened by this suite.
module.exports = defineConfig({
  testDir: __dirname,
  testMatch: "agent-connections.spec.js",
  timeout: 30_000,
  retries: 0,
  fullyParallel: false,
  workers: 1,
  outputDir: path.join(__dirname, "..", "..", "test-results", "agent-connections"),
  use: { baseURL: "http://127.0.0.1:8123", headless: true, viewport: { width: 1440, height: 900 } },
});
