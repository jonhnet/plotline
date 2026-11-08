import { createRequire } from "node:module";
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const require = createRequire(
  process.env.PLOTLINE_PLAYWRIGHT_PACKAGE ||
    new URL("../controller/package.json", import.meta.url),
);
const { chromium } = require("playwright");
const base = process.env.RASTER_LAB_URL || "http://127.0.0.1:9194";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [],
  external = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("request", (req) => {
  if (new URL(req.url()).origin !== new URL(base).origin)
    external.push(req.url());
});
try {
  const portfolio = await (
    await page.request.get(base + "/artifacts/portfolio.json")
  ).json();
  const columns = portfolio.experiments.length;
  const active = portfolio.experiments.filter(
    (e) => e.study === "strokes" && e.round === 5,
  ).length;
  await page.goto(base);
  await page.waitForSelector("tbody tr");
  assert.equal(await page.locator("#matrix tbody tr").count(), 8);
  assert.equal(await page.locator("#matrix thead th").count(), active + 1);
  assert.equal(await page.locator(".image-button").count(), 8 * (active + 1));
  for (const row of await page.locator("#matrix tbody tr").all())
    await row.scrollIntoViewIfNeeded();
  await page.locator("#matrix").evaluate((e) => (e.scrollTop = 0));
  await page
    .getByRole("button", {
      name: "Compare My Love: Selected recovery",
      exact: true,
    })
    .click();
  await page.waitForFunction(() =>
    document.querySelector("#right-info").textContent.includes("Vector SVG"),
  );
  assert.equal(await page.locator("#compare").evaluate((e) => e.open), true);
  await page.selectOption("#zoom", "4");
  const box = await page.locator("#left-canvas").boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    box.x + box.width / 2 + 65,
    box.y + box.height / 2 + 25,
  );
  await page.mouse.up();
  if (process.env.RASTER_LAB_TEST_WRITES === "1") {
    assert.notEqual(
      new URL(base).port,
      "9194",
      "Feedback smoke tests must use a disposable state/server",
    );
    const marker = `Browser test ${Date.now()}: ooh, best in the row! <b>literal</b>`;
    await page.fill("#comment-text", marker);
    await page
      .getByRole("button", { name: "Save comment", exact: true })
      .click();
    await page.waitForFunction(
      () =>
        document.querySelector("#comment-status").textContent ===
        "Saved on the server.",
    );
    assert.equal(
      await page.locator(".comment-body").filter({ hasText: marker }).count(),
      1,
    );
    assert.equal(await page.locator(".comment-body b").count(), 0);
    const written = (
      await (await page.request.get(base + "/api/comments")).json()
    ).comments.find((n) => n.text === marker);
    assert.equal(written.sample.experiment_id, "review-ink");
    assert.equal(written.view.zoom, 4);
    await page.getByRole("button", { name: "Close comparison" }).click();
    await page.reload();
    await page
      .getByRole("button", {
        name: "Comment on My Love: Selected recovery",
        exact: true,
      })
      .click();
    await page.waitForSelector(`[data-note-id="${written.id}"]`);
    const article = page.locator(`[data-note-id="${written.id}"]`);
    await article.getByRole("button", { name: "Restore 4× view" }).click();
    assert.equal(await page.locator("#zoom").inputValue(), "4");
    await article.getByRole("button", { name: "Edit", exact: true }).click();
    await page.fill("#comment-text", marker + " Edited.");
    await page.getByRole("button", { name: "Save edit", exact: true }).click();
    await page.waitForFunction(
      () =>
        document.querySelector("#comment-status").textContent ===
        "Saved on the server.",
    );
    const saved = (
      await (await page.request.get(base + "/api/comments")).json()
    ).comments.find((n) => n.id === written.id);
    assert.equal(saved.version, 2);
    assert.equal(saved.history[0].text, marker);
    assert.equal(
      (
        await page.request.get(
          base + `/comment-samples/${saved.sample.result_key}/preview.png`,
        )
      ).status(),
      200,
    );
    await page.fill("#comment-text", "Unsaved draft belongs to recovered ink");
    await page.selectOption("#right-select", "edges-fine");
    await page.waitForFunction(
      () =>
        document.querySelector("#comment-target").textContent === "Fine edges",
    );
    assert.equal(await page.locator("#comment-text").inputValue(), "");
    assert.equal(
      await page.locator(".comment-body").filter({ hasText: marker }).count(),
      0,
    );
    await page.selectOption("#right-select", "review-ink");
    await page.waitForFunction(
      () =>
        document.querySelector("#comment-text").value ===
        "Unsaved draft belongs to recovered ink",
    );
    await page.fill("#comment-text", "");
  }
  await page.selectOption("#left-select", "recovery-open");
  await page.selectOption("#right-select", "tone-50");
  await page.waitForFunction(
    () =>
      document.querySelector("#comment-target").textContent ===
      "Interior 50% · 0.25″",
  );
  await page.locator("#blink").check();
  await page.waitForTimeout(700);
  await page.locator("#blink").uncheck();
  const params = await page
    .locator("#parameters")
    .evaluate((e) => JSON.parse(e.textContent));
  assert.equal(params.right.palette, "cmy");
  assert.equal(params.right.interior_ink, 0.5);
  const focusLink = await page
    .getByRole("link", { name: "Ink emphasis map (white = more ink)" })
    .getAttribute("href");
  assert.equal((await page.request.get(base + focusLink)).status(), 200);
  await mkdir("build/raster-lab-browser", { recursive: true });
  await page.screenshot({ path: "build/raster-lab-browser/compare.png" });
  await page.getByRole("button", { name: "Close comparison" }).click();
  assert.equal(await page.locator("#archive").evaluate((e) => e.open), false);
  assert.equal(await page.locator("#archive-matrix .image-button").count(), 0);
  await page.selectOption("#family", "screens");
  assert.equal(await page.locator("#tone-strip").isVisible(), true);
  const strip = await page.locator("#tone-strip a").getAttribute("href");
  assert.equal((await page.request.get(base + strip)).status(), 200);
  assert.ok((await page.locator("#matrix").textContent()).includes("Screen paths"));
  assert.equal(await page.locator("#matrix thead th").count(), 5);
  assert.equal(await page.locator("#matrix tbody tr").count(), 6);
  assert.equal(
    await page.locator("#matrix [data-challenge=the-visitor]").count(),
    0,
  );
  assert.equal(
    await page.locator("#matrix [data-challenge=principals-office]").count(),
    0,
  );
  assert.equal(
    await page
      .locator("#matrix .source-meta")
      .filter({ hasText: "Monochrome" })
      .count(),
    1,
  );
  await page
    .getByRole("button", {
      name: "Compare Game Night Sketch: Full · reference",
      exact: true,
    })
    .click();
  await page.waitForFunction(() =>
    document.querySelector("#right-info").textContent.includes("K only:"),
  );
  const resolved = await page
    .locator("#parameters")
    .evaluate((e) => JSON.parse(e.textContent));
  assert.equal(resolved.right.palette, "black");
  const mono = portfolio.cells["game-night"]["highlight-reference"];
  const paths = await (
    await page.request.get(base + `/artifacts/${mono.artifact}/paths.json`)
  ).json();
  assert.deepEqual(Object.keys(paths.paths), ["K"]);
  await page.getByRole("button", { name: "Close comparison" }).click();
  await page.reload();
  await page.waitForSelector("#matrix tbody tr");
  assert.equal(await page.locator("#family").inputValue(), "screens");
  await page.fill("#search", "alethyia");
  assert.equal(await page.locator("#matrix tbody tr").count(), 2);
  await page.fill("#search", "");
  await page.selectOption("#family", "strokes");
  await page.getByRole("button", { name: "Choose columns" }).click();
  await page.locator("#columns input[data-experiment=review-ink]").uncheck();
  assert.equal(await page.locator("#matrix thead th").count(), active);
  await page.locator("#columns input[data-experiment=review-ink]").check();
  await page.getByRole("button", { name: "Choose columns" }).click();
  await page
    .getByRole("button", {
      name: "Compare Happy Shreksgiving!: Selected recovery",
      exact: true,
    })
    .click();
  await page.waitForFunction(() =>
    document.querySelector("#right-info").textContent.includes("0.15 mm pen"),
  );
  await page.getByRole("button", { name: "Close comparison" }).click();
  await page.selectOption("#family", "screens");
  await page.getByRole("button", { name: "Compare Happy Shreksgiving!: Highlight diffusion · 0.8 mm", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("#right-info").textContent.includes("K 0.15 mm"));
  assert.ok((await page.locator("#right-info").textContent()).includes("C 0.30 mm"));
  const mixed = portfolio.cells["happy-shreksgiving"]["highlight-short"];
  const mixedSVG = await (await page.request.get(base + `/artifacts/${mixed.artifact}/paths.svg`)).text();
  assert.match(mixedSVG, /id="ink-K"[^>]*stroke-width="0.15"/);
  assert.ok((await page.locator("#right-info").textContent()).includes("full screen = 100"));
  await page.getByRole("button", { name: "Close comparison" }).click();
  await page.selectOption("#family", "lettering");
  assert.equal(await page.locator("#lettering-stages").isVisible(), true);
  const stages = await page.locator("#lettering-stages a").getAttribute("href");
  assert.equal((await page.request.get(base + stages)).status(), 200);
  assert.equal(await page.locator("#matrix tbody tr").count(), 1);
  assert.equal(await page.locator("#matrix thead th").count(), 7);
  await page.getByRole("button", { name: "Compare The Visitor: Smooth · bounded", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("#right-info").textContent.includes("Skeleton before fitting"));
  assert.ok((await page.locator("#right-info").textContent()).includes("0.075 mm pen"));
  assert.ok((await page.locator("#right-info").textContent()).includes("450 dpi preview"));
  assert.ok((await page.locator("#right-info").textContent()).includes("1656 × 2142"));
  assert.equal(await page.locator("#zoom").inputValue(), "2");
  for (const name of ["Threshold mask", "Skeleton before fitting"]) {
    const link = await page.getByRole("link", { name, exact: true }).getAttribute("href");
    assert.equal((await page.request.get(base + link)).status(), 200);
  }
  await page.getByRole("button", { name: "Reset view", exact: true }).click();
  assert.equal(await page.locator("#zoom").inputValue(), "1");
  await page.getByRole("button", { name: "Upper-right panel", exact: true }).click();
  assert.equal(await page.locator("#zoom").inputValue(), "2");
  if (process.env.RASTER_LAB_TEST_WRITES === "1") {
    const marker = `Crop test ${Date.now()}`;
    await page.fill("#comment-text", marker);
    await page.getByRole("button", { name: "Save comment", exact: true }).click();
    await page.waitForFunction(() => document.querySelector("#comment-status").textContent === "Saved on the server.");
    const note = (await (await page.request.get(base + "/api/comments")).json()).comments.find(n => n.text === marker);
    assert.deepEqual(note.view, { zoom: 2, x: .75, y: .25 });
  }
  await page.selectOption("#right-select", "thin-fill");
  await page.waitForFunction(() => document.querySelector("#right-info").textContent.includes("no skeleton"));
  assert.equal(await page.locator("#right-info").getByRole("link", { name: "Accepted skeleton", exact: true }).count(), 0);
  const fillMask = await page.locator("#right-info").getByRole("link", { name: "Threshold mask", exact: true }).getAttribute("href");
  assert.equal((await page.request.get(base + fillMask)).status(), 200);
  await page.selectOption("#right-select", "fill-joined");
  await page.waitForFunction(() => document.querySelector("#right-info").textContent.includes("Joining:"));
  assert.ok((await page.locator("#right-info").textContent()).includes("133,771 → 11,510 runs"));
  assert.equal(await page.locator("#right-info").getByRole("link", { name: "Accepted skeleton", exact: true }).count(), 0);
  await page.selectOption("#right-select", "fill-paper-quarter");
  await page.waitForFunction(() => document.querySelector("#right-info").textContent.includes("Paper: 11.0 × 17.0″"));
  const paperInfo = await page.locator("#right-info").textContent();
  assert.ok(paperInfo.includes("0.25 mm pen"));
  assert.ok(paperInfo.includes("art: 9.5 × 12.3″"));
  assert.ok(paperInfo.includes("minimum margins 0.75″"));
  assert.ok(paperInfo.includes("top/bottom 2.36″"));
  for (const name of ["Paper preview", "Paper SVG (actual size)"]) {
    const link = await page.locator("#right-info").getByRole("link", {name, exact:true}).getAttribute("href");
    assert.equal((await page.request.get(base + link)).status(), 200);
  }
  await page.selectOption("#left-select", "fill-joined");
  await page.waitForFunction(() => document.querySelector("#left-info").textContent.includes("0.075 mm pen"));
  assert.ok((await page.locator("#right-info").textContent()).includes("0.25 mm pen"));
  await page.getByRole("button", { name: "Close comparison" }).click();
  await page.selectOption("#family", "combined");
  assert.equal(await page.locator("#matrix tbody tr").count(), 8);
  assert.equal(await page.locator("#matrix thead th").count(), 5);
  await page.getByRole("button", {name:"Compare My Love: Joined ink · neutral mask",exact:true}).click();
  await page.waitForFunction(() => document.querySelector("#right-info").textContent.includes("Turquoise #009f7c"));
  const combinedInfo = await page.locator("#right-info").textContent();
  assert.ok(combinedInfo.includes("0.25 mm test widths"));
  assert.ok(combinedInfo.includes("M/Y colors provisional"));
  const maskLink = await page.locator("#right-info").getByRole("link", {name:"Recovered ink mask",exact:true}).getAttribute("href");
  assert.equal((await page.request.get(base+maskLink)).status(),200);
  await page.selectOption("#left-select", "combined-luminance");
  await page.waitForFunction(() => document.querySelector("#parameters").textContent.includes('"black_policy": "luminance"'));
  const materialRecipe = JSON.parse(await page.locator("#parameters").textContent()).right;
  assert.equal(materialRecipe.ink_colors.C,"#009f7c");
  assert.equal(materialRecipe.pen_mm,.25);
  await page.getByRole("button", {name:"Close comparison"}).click();
  await page.selectOption("#family", "strokes");
  await page.locator("#archive summary").click();
  await page.waitForSelector("#archive-matrix tbody tr");
  assert.equal(await page.locator("#archive-matrix tbody tr").count(), 8);
  assert.equal(await page.locator("#archive-matrix thead th").count(), 53);
  await page
    .locator("#archive-matrix")
    .getByRole("button", {
      name: "Compare My Love: Edge CMY + ink · 0.25″",
      exact: true,
    })
    .click();
  await page.waitForFunction(
    () =>
      document.querySelector("#comment-target").textContent ===
      "Edge CMY + ink · 0.25″",
  );
  await page.getByRole("button", { name: "Close comparison" }).click();
  await page.locator("#archive summary").click();
  await page.waitForFunction(
    () => !document.querySelector("#archive-matrix .image-button"),
  );
  await page.screenshot({ path: "build/raster-lab-browser/grid.png" });
  const matrix = await page.locator("#matrix").evaluate((e) => ({
    client: e.clientWidth,
    scroll: e.scrollWidth,
    body: document.body.scrollWidth,
    window: innerWidth,
  }));
  assert.ok(matrix.scroll >= matrix.client);
  assert.ok(matrix.body <= matrix.window);
  for (const path of [
    "/source-urls.txt",
    "/sources.json",
    "/originals/..%2Fsources.json",
  ])
    assert.equal((await page.request.get(base + path)).status(), 404);
  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
  console.log(
    `PASS: 8 rows, 4 focused studies, 52 archived columns, ${columns} total columns, K-only monochrome, turquoise/test-width provenance, ink index, comparison, comments, local-only requests${process.env.RASTER_LAB_TEST_WRITES === "1" ? ", persistent comments/editing/drafts" : ""}`,
  );
} finally {
  await browser.close();
}
