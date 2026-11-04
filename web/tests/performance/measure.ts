import type { Page } from "@playwright/test";

export interface BrowserSample {
  elapsedMs: number;
  deliveryMs: number | null;
  afterDeliveryMs: number | null;
  frameGapsMs: number[];
  longTasksMs: number[];
  longTasksSupported: boolean;
  inputToFrameMs: number[];
  mutationBatches: number;
  domElements: number;
  tableRows: number;
  tableCells: number;
  renderedMath: number;
  renderedDiagrams: number;
}

interface Probe {
  deliveryDone(): void;
  result: BrowserSample | null;
}

declare global {
  interface Window { __runtimeBenchmark?: Probe }
}

export async function startProbe(page: Page, options: {
  marker: string;
  math?: number;
  diagrams?: number;
}) {
  await page.evaluate(({ marker, math = 0, diagrams = 0 }) => {
    const started = performance.now();
    const frameGapsMs: number[] = [];
    const longTasksMs: number[] = [];
    const inputToFrameMs: number[] = [];
    let lastFrame: number | null = null;
    let delivered: number | null = null;
    let mutations = 0;
    let changed = true;
    let finishing = false;
    let frameId = 0;
    const longTasksSupported = PerformanceObserver.supportedEntryTypes.includes("longtask");
    const recordTasks = (entries: PerformanceEntry[]) => {
      for (const entry of entries) {
        if (entry.startTime >= started) longTasksMs.push(entry.duration);
      }
    };
    const taskObserver = longTasksSupported
      ? new PerformanceObserver((list) => recordTasks(list.getEntries())) : null;
    taskObserver?.observe({ type: "longtask" });
    const mutationObserver = new MutationObserver(() => { mutations += 1; changed = true; });
    mutationObserver.observe(document.body, { subtree: true, childList: true, characterData: true });
    const onInput = (event: Event) => {
      const start = event.timeStamp;
      requestAnimationFrame(() => inputToFrameMs.push(Math.max(0, performance.now() - start)));
    };
    document.addEventListener("input", onInput, true);
    const probe: Probe = {
      result: null,
      deliveryDone() { delivered = performance.now(); changed = true; },
    };
    window.__runtimeBenchmark = probe;

    const finish = () => {
      recordTasks(taskObserver?.takeRecords() ?? []);
      taskObserver?.disconnect();
      mutationObserver.disconnect();
      document.removeEventListener("input", onInput, true);
      cancelAnimationFrame(frameId);
      const ended = performance.now();
      probe.result = {
        elapsedMs: ended - started,
        deliveryMs: delivered === null ? null : delivered - started,
        afterDeliveryMs: delivered === null ? null : ended - delivered,
        frameGapsMs, longTasksMs, longTasksSupported, inputToFrameMs,
        mutationBatches: mutations,
        domElements: document.querySelectorAll("*").length,
        tableRows: document.querySelectorAll("tbody tr").length,
        tableCells: document.querySelectorAll("tbody td").length,
        renderedMath: document.querySelectorAll(".katex").length,
        renderedDiagrams: document.querySelectorAll('svg[id^="mermaid-"]').length,
      };
    };
    const tick = (now: number) => {
      if (lastFrame !== null) frameGapsMs.push(now - lastFrame);
      lastFrame = now;
      if (!finishing && delivered !== null && changed) {
        changed = false;
        if (document.body.textContent?.includes(marker)
            && document.querySelectorAll(".katex").length >= math
            && document.querySelectorAll('svg[id^="mermaid-"]').length >= diagrams) {
          finishing = true;
          // Wait past a render opportunity; this is DOM-ready + two rAFs,
          // not a claim about exact screen presentation timing.
          requestAnimationFrame(() => requestAnimationFrame(finish));
        }
      }
      frameId = requestAnimationFrame(tick);
    };
    frameId = requestAnimationFrame(tick);
  }, options);
}

export async function deliveryDone(page: Page) {
  await page.evaluate(() => window.__runtimeBenchmark!.deliveryDone());
}

export async function finishProbe(page: Page): Promise<BrowserSample> {
  await page.waitForFunction(() => Boolean(window.__runtimeBenchmark?.result), undefined, { timeout: 60_000 });
  return page.evaluate(() => window.__runtimeBenchmark!.result!);
}

export function distribution(values: number[]) {
  if (!values.length) return { count: 0, median: null, p95: null, max: null };
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return {
    count: sorted.length,
    median: sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2,
    p95: sorted[Math.ceil(sorted.length * 0.95) - 1],
    max: sorted[sorted.length - 1],
  };
}
