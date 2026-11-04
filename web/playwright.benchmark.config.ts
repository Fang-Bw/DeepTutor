import { defineConfig, devices } from "@playwright/test";

const channel = process.env.BENCHMARK_BROWSER_CHANNEL?.trim() || undefined;
if (channel !== undefined && channel !== "chrome" && channel !== "msedge") {
  throw new Error("BENCHMARK_BROWSER_CHANNEL must be chrome or msedge (or unset for bundled Chromium)");
}

// Run against a separately started frontend. All API/WS traffic is replaced
// by fixtures in the browser; a backend and model credentials are unnecessary.
export default defineConfig({
  testDir: "./tests/performance",
  testMatch: "**/*.bench.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 240_000,
  expect: { timeout: 30_000 },
  outputDir: "test-results/runtime-benchmark",
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    channel,
    viewport: { width: 1440, height: 900 },
    baseURL: process.env.WEB_BASE_URL || "http://127.0.0.1:3000",
    locale: "en-US",
    reducedMotion: "reduce",
    actionTimeout: 30_000,
    serviceWorkers: "block",
    // Recording snapshots can perturb runtime measurements even when a
    // successful trace is later discarded. Opt in only while debugging.
    trace: process.env.BENCHMARK_TRACE === "1" ? "retain-on-failure" : "off",
  },
});
