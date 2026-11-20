import { defineConfig, devices } from "@playwright/test";

// E2E runs the real app against fakes: tests/e2e_server.py swaps in the fake
// recorder helper, stubbed transcription and a throwaway output folder. It
// refuses to start without SCRIBEIQ_E2E=1. The front end is built first and
// served by that server, as in the real app.
const PORT = 8799;

export default defineConfig({
  testDir: "e2e",
  // One server holds one recording session, so specs run one at a time.
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1240, height: 860 } } }],
  webServer: {
    command: `npm run build && SCRIBEIQ_E2E=1 ../.venv/bin/python ../tests/e2e_server.py --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/session`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe",
  },
});
