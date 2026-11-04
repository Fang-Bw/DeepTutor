import { execFileSync } from "node:child_process";
import { writeFile } from "node:fs/promises";
import os from "node:os";
import { setTimeout as delay } from "node:timers/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import ExcelJS from "exceljs";
import { installFixture } from "./fixture";
import { startProbe, deliveryDone, finishProbe, distribution, type BrowserSample } from "./measure";
import { CHUNK_CHARS, CHUNK_INTERVAL_MS, FINAL_MARKER, HISTORY_PAIRS, SESSION_ID, scenarioContent, scenarioEvents, type ScenarioName } from "./scenarios";

const SAMPLES = Number(process.env.BENCHMARK_SAMPLES || 5);
if (!Number.isInteger(SAMPLES) || SAMPLES < 1 || SAMPLES > 30) {
  throw new Error("BENCHMARK_SAMPLES must be an integer from 1 to 30");
}

async function openChat(page: Page) {
  await page.goto(`/chat/${SESSION_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.locator('[data-chat-message-role="assistant"]')).toHaveCount(HISTORY_PAIRS);
  await expect(page.getByRole("textbox").last()).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

type Sample = BrowserSample & { warmup: boolean; unexpectedApiPaths: string[] };

async function writeReport(info: TestInfo, browserVersion: string, scenario: string, samples: Sample[], workload: object) {
  const measured = samples.filter((sample) => !sample.warmup);
  const path = info.outputPath("runtime-benchmark.json");
  let revision = "unknown";
  let workingTreeDirty: boolean | null = null;
  try { revision = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(); } catch { /* optional */ }
  try { workingTreeDirty = Boolean(execFileSync("git", ["status", "--porcelain"], { encoding: "utf8" }).trim()); } catch { /* optional */ }
  const report = {
    schemaVersion: 1, scenario, capturedAt: new Date().toISOString(), revision, workingTreeDirty,
    environment: {
      node: process.version, browser: browserVersion, platform: process.platform,
      browserChannel: info.project.use.channel || "bundled-chromium",
      trace: info.project.use.trace || "off",
      release: os.release(), arch: os.arch(), cpu: os.cpus()[0]?.model,
      logicalCpus: os.cpus().length, memoryBytes: os.totalmem(),
      viewport: info.project.use.viewport, frontend: info.project.use.baseURL,
      serverMode: process.env.BENCHMARK_SERVER_MODE || "unspecified",
      motion: "reduce", sampleCount: SAMPLES,
      cache: "fresh browser context per sample; frontend server warmed by first excluded sample",
      api: "synthetic fixtures; no backend, model, account data or billable calls",
    },
    workload,
    metricDefinitions: {
      elapsedMs: "Browser clock: before submission/click/keyboard activation to matching DOM + two animation frames. Includes input pacing and lazy loading; not pure React render time.",
      afterDeliveryMs: "Browser clock: all fixture events delivered or click/keyboard activation returned to matching DOM + two animation frames.",
      frameGapsMs: "Observed requestAnimationFrame intervals during the measured window, not a count of dropped frames.",
      longTasksMs: "PerformanceObserver longtask durations (>50ms), if supported.",
      inputToFrameMs: "Synthetic input event timestamp to next animation-frame callback; not field INP or hardware input latency.",
      mutationBatches: "MutationObserver callbacks, not React commit/render counts.",
      domElements: "Whole-page element count at completion, including shell and synthetic history.",
    },
    summary: {
      elapsedMs: distribution(measured.map((sample) => sample.elapsedMs)),
      afterDeliveryMs: distribution(measured.flatMap((sample) => sample.afterDeliveryMs === null ? [] : [sample.afterDeliveryMs])),
      frameGapMs: distribution(measured.flatMap((sample) => sample.frameGapsMs)),
      inputToFrameMs: distribution(measured.flatMap((sample) => sample.inputToFrameMs)),
      longTaskMs: distribution(measured.flatMap((sample) => sample.longTasksMs)),
      longTaskTotalMs: distribution(measured.map((sample) => sample.longTasksMs.reduce((sum, value) => sum + value, 0))),
      domElements: distribution(measured.map((sample) => sample.domElements)),
    },
    samples,
  };
  await writeFile(path, JSON.stringify(report, null, 2) + "\n");
  await info.attach("runtime-benchmark", { path, contentType: "application/json" });
  console.log(`${scenario}: elapsed median=${report.summary.elapsedMs.median?.toFixed(1)}ms p95=${report.summary.elapsedMs.p95?.toFixed(1)}ms; ${path}`);
}

for (const scenario of ["long-markdown", "math", "mermaid", "tool-events"] as ScenarioName[]) {
  test(`runtime ${scenario}`, async ({ browser }, info) => {
    const samples: Sample[] = [];
    const events = scenarioEvents(scenario);
    for (let i = 0; i <= SAMPLES; i += 1) {
      const context = await browser.newContext({ ...info.project.use });
      const page = await context.newPage();
      try {
        const fixture = await installFixture(page);
        await openChat(page);
        const composer = page.getByRole("textbox").last();
        await composer.fill(`Run synthetic ${scenario} benchmark`);
        await startProbe(page, { marker: FINAL_MARKER, math: scenario === "math" ? 180 : 0, diagrams: scenario === "mermaid" ? 8 : 0 });
        await composer.press("Enter");
        const socket = await Promise.race([
          fixture.submitted,
          delay(15_000, undefined, { ref: false }).then(() => { throw new Error("The real composer did not submit start_turn to the fixture"); }),
        ]);
        // Playwright's keyboard events exercise a real input while renderer
        // updates arrive. The draft is never submitted to a backend.
        const typing = composer.pressSequentially("draft", { delay: 15 });
        for (const event of events) {
          socket.send(JSON.stringify(event));
          await delay(CHUNK_INTERVAL_MS);
        }
        await deliveryDone(page);
        await typing;
        const sample = await finishProbe(page);
        await expect(page.locator('[data-chat-message-role="assistant"]').last()).toContainText(FINAL_MARKER);
        if (scenario === "tool-events") {
          // Outside the timing window: prove the real message renderer
          // consumed every synthetic tool call, not just the final text.
          await expect(page.locator('[data-chat-message-role="assistant"]').last()
            .getByText(/^(?:·\s*)?80 tool calls$/)).toBeVisible();
        }
        expect(sample.inputToFrameMs.length).toBeGreaterThan(0);
        samples.push({ ...sample, warmup: i === 0, unexpectedApiPaths: [...fixture.unknownPaths].sort() });
      } finally { await context.close(); }
    }
    await writeReport(info, browser.version(), scenario, samples, {
      historyPairs: HISTORY_PAIRS, characters: scenarioContent(scenario).length,
      events: events.length, chunkCharacters: CHUNK_CHARS,
      minimumEventIntervalMs: CHUNK_INTERVAL_MS,
      toolCalls: scenario === "tool-events" ? 80 : 0,
    });
  });
}

test("runtime spreadsheet paging", async ({ browser }, info) => {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Synthetic");
  for (let row = 1; row <= 1200; row += 1) {
    sheet.addRow(Array.from({ length: 80 }, (_, column) => `Cell ${row}:${column + 1}`));
  }
  const bytes = Buffer.from(await workbook.xlsx.writeBuffer());
  const samples: Sample[] = [];
  const paging: BrowserSample[] = [];
  for (let i = 0; i <= SAMPLES; i += 1) {
    const context = await browser.newContext({ ...info.project.use });
    const page = await context.newPage();
    try {
      const fixture = await installFixture(page, bytes);
      await openChat(page);
      await startProbe(page, { marker: "Cell 1:1" });
      await page.getByRole("button", { name: /Benchmark workbook/ }).click();
      await deliveryDone(page);
      const sample = await finishProbe(page);
      expect(sample.tableRows).toBe(50);
      expect(sample.tableCells).toBe(50 * 61);
      samples.push({ ...sample, warmup: i === 0, unexpectedApiPaths: [...fixture.unknownPaths].sort() });
      const nextPage = page.getByRole("button", { name: "Next page", exact: true });
      await nextPage.focus();
      await startProbe(page, { marker: "Cell 51:1" });
      // Native keyboard activation remains usable when Next's development
      // indicator overlaps the lower-right corner. Do not force a pointer
      // through an overlay or remove application/development UI.
      await nextPage.press("Enter");
      await deliveryDone(page);
      const next = await finishProbe(page);
      expect(next.tableRows).toBe(50);
      expect(next.tableCells).toBe(50 * 61);
      await expect(page.getByText("Cell 1:1", { exact: true })).toHaveCount(0);
      if (i > 0) paging.push(next);
    } finally { await context.close(); }
  }
  await writeReport(info, browser.version(), "spreadsheet paging", samples, {
    workbookRows: 1200, workbookColumns: 80, workbookBytes: bytes.length,
    expectedVisibleRows: 50, expectedDataColumns: 60, includesRowNumberColumn: true,
    nextPage: { activation: "Focused button + Enter key (focus occurs before timing)", elapsedMs: distribution(paging.map((sample) => sample.elapsedMs)), samples: paging },
  });
});
