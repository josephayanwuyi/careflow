import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests in a real browser. Requirements:
 *  - the CareFlow API running at CAREFLOW_API_URL (default http://127.0.0.1:8000) with seed data;
 *  - CAREFLOW_E2E_PASSWORD set to the seed accounts' password.
 * The Vite dev server is started automatically (and proxies /api to the backend).
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://localhost:5173",
    ...devices["Pixel 7"],
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
