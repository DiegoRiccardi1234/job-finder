const { defineConfig } = require("@playwright/test");
const path = require("path");

const pythonExe = process.platform === "win32"
  ? `"${path.join(__dirname, ".venv", "Scripts", "python.exe")}"`
  : "python3";

module.exports = defineConfig({
  testDir: path.join("tests", "e2e"),
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:8000",
    headless: true,
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: `${pythonExe} -m uvicorn app.main:app --host 127.0.0.1 --port 8000`,
    url: "http://127.0.0.1:8000/api/health",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
