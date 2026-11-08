// Non-mutating browser measurement against a running preview service.
import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
const url = process.env.PLOTLINE_URL || "http://127.0.0.1:9191";
const jobs = await (await fetch(`${url}/api/jobs`)).json();
const job = jobs.reduce((a, b) =>
  a.summary.points > b.summary.points ? a : b,
);
const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox"],
});
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1080 },
  });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", {
    rate: Number(process.env.CPU_RATE || 4),
  });
  await page.goto(`${url}/#${job.id}`);
  await page
    .locator("#preview-loading")
    .waitFor({ state: "hidden", timeout: 120000 });
  await page.locator("#viewport").scrollIntoViewIfNeeded();
  await page.waitForTimeout(1000);
  const result = await page.evaluate(async () => {
    const viewport = document.getElementById("viewport");
    const rect = viewport.getBoundingClientRect();
    const frames = [],
      handlers = [];
    let previous = performance.now(),
      running = true;
    function frame(now) {
      frames.push(now - previous);
      previous = now;
      if (running) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
    const started = performance.now();
    for (let i = 0; i < 60; i++) {
      const t = performance.now();
      viewport.dispatchEvent(
        new WheelEvent("wheel", {
          deltaY: i < 30 ? -6 : 6,
          clientX: rect.x + rect.width / 2,
          clientY: rect.y + rect.height / 2,
          bubbles: true,
          cancelable: true,
        }),
      );
      handlers.push(performance.now() - t);
      await new Promise((r) => setTimeout(r, 16));
    }
    const inputMs = performance.now() - started;
    await new Promise((r) => setTimeout(r, 300));
    running = false;
    const sorted = frames.slice(2).sort((a, b) => a - b);
    return {
      inputMs,
      events: handlers.length,
      handlerMaxMs: Math.max(...handlers),
      frameP95Ms: sorted[Math.floor(sorted.length * 0.95)],
      frameMaxMs: Math.max(...frames.slice(2)),
      frames: frames.length,
    };
  });
  result.cpuRate = Number(process.env.CPU_RATE || 4);
  result.job = job.name;
  result.points = job.summary.points;
  console.log(JSON.stringify(result, null, 2));
  if (process.env.REPORT) {
    await mkdir("build/controller", { recursive: true });
    await writeFile(process.env.REPORT, JSON.stringify(result, null, 2) + "\n");
  }
} finally {
  await browser.close();
}
