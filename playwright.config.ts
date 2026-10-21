import {defineConfig, devices} from "@playwright/test";
export default defineConfig({
  testDir: "tests/frontend",
  timeout: 15000,
  use: {baseURL: "http://127.0.0.1:4173", screenshot: "only-on-failure"},
  projects: [{name: "desktop", use: {...devices["Desktop Chrome"]}}, {name: "mobile", use: {...devices["iPhone 13"], defaultBrowserType: "chromium"}}],
  webServer: {command: "node tests/frontend/server.mjs", url: "http://127.0.0.1:4173", reuseExistingServer: !process.env.CI},
});
