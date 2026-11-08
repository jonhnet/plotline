// Real PDF worker + HTTP boundary + browser; isolated state, no hardware configuration.
import { chromium } from "playwright";
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, rm, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import assert from "node:assert/strict";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const project = process.env.PLOTLINE_TEST_PROJECT || root;
const state = await mkdtemp(`${tmpdir()}/plotline-pdf-`);
const reserve = createServer();
await new Promise((r) => reserve.listen(0, "127.0.0.1", r));
const port = reserve.address().port;
await new Promise((r) => reserve.close(r));
const url = `http://127.0.0.1:${port}`,
  headers = { "X-Plotline-Request": "1" };
const server = spawn(
  process.env.PLOTLINE_TEST_BINARY || resolve(root, "controller/target/release/plotline-studios"),
  ["--state", state, "--listen", `127.0.0.1:${port}`],
  { env: {...process.env,PLOTLINE_PDF_PROJECT:project}, stdio: ["ignore", "ignore", "pipe"] },
);
let logs = "",
  browser;
server.stderr.on("data", (data) => (logs += data));
async function api(path, options = {}) {
  const response = await fetch(url + path, {
    ...options,
    headers: { ...headers, ...options.headers },
  });
  const value = await response.json();
  assert(response.ok, JSON.stringify(value));
  return value;
}
async function done(path) {
  for (let n = 0; n < 200; n++) {
    const result = await api(path);
    if (
      !["received", "inspecting", "queued", "running"].includes(result.status)
    )
      return result;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw Error("Worker timed out");
}
try {
  for (let n = 0; ; n++) {
    try {
      if ((await fetch(url + "/api/status")).ok) break;
    } catch {}
    if (n > 100) throw Error(logs);
    await new Promise((r) => setTimeout(r, 50));
  }
  const caps = await api("/api/v1/capabilities");
  assert(caps.implemented.pdf_upload);
  assert(caps.implemented.per_ink_widths);
  const original = await readFile(
    resolve(project, "build/pdf-boundary-v1/boundary.pdf"),
  );
  const intent = JSON.parse(
    await readFile(
      resolve(project, "build/pdf-boundary-v1/boundary.print-intent.json"),
    ),
  );
  const form = new FormData();
  form.append("document", new Blob([original]), "Boundary.pdf");
  form.append("intent", new Blob([JSON.stringify(intent)]), "intent.json");
  const upload = await api("/api/v1/documents", { method: "POST", body: form });
  assert.equal(upload.status, "received");
  assert.deepEqual(
    Buffer.from(
      await (
        await fetch(url + `/api/v1/documents/${upload.id}/source.pdf`)
      ).arrayBuffer(),
    ),
    original,
    "Original bytes available immediately after durable acknowledgement",
  );
  const doc = await done(upload.status_url);
  assert.equal(doc.status, "ready", doc.error);
  assert.equal(doc.inspection.pages.length, 2);
  const created = await api(`/api/v1/documents/${doc.id}/conversions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(intent),
  });
  const conversion = await done(created.status_url);
  assert.equal(conversion.status, "ready", conversion.error);
  await api(`/api/v1/conversions/${conversion.id}/publish`, {method:"POST"});
  assert.equal(conversion.result.pages.length, 2);
  assert(conversion.result.pages[1].geometry.source_exceeds_sheet);
  assert(conversion.result.pages[0].by_ink["broad-black"]);
  assert.equal(conversion.result.artifact_mode, "preview");
  assert(conversion.result.pages[0].artifacts.summary.endsWith("/summary.json"));
  assert(!conversion.result.pages[0].artifacts.report);
  const summary = await api(conversion.result.pages[0].artifacts.summary);
  assert.equal(summary.schema, "plotline.conversion-summary/1");
  assert.deepEqual(summary.by_ink, conversion.result.pages[0].by_ink);
  assert.equal((await fetch(url + `/api/v1/conversions/${conversion.id}/pages/0/report.json`)).status, 404);
  for (const file of Object.values(conversion.result.pages[0].artifacts))
    assert((await fetch(url + file)).ok, file);
  const bad = { ...intent, source_sha256: "0".repeat(64) };
  assert.equal(
    (
      await fetch(url + `/api/v1/documents/${doc.id}/conversions`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify(bad),
      })
    ).status,
    422,
  );
  const malformed = new FormData();
  malformed.append("document", new Blob(["not a PDF"]), "broken.pdf");
  const broken = await api("/api/v1/documents", {
    method: "POST",
    body: malformed,
  });
  assert.equal((await done(broken.status_url)).status, "error");
  assert.equal(
    await (
      await fetch(url + `/api/v1/documents/${broken.id}/source.pdf`)
    ).text(),
    "not a PDF",
  );
  // Buffer the body: Node's streaming multipart sender can report EPIPE after
  // the server deliberately rejects headers without consuming an upload.
  const unauthorized =
    '--test\r\nContent-Disposition: form-data; name="document"; filename="small.pdf"\r\n\r\nsmall\r\n--test--\r\n';
  assert.equal(
    (
      await fetch(url + "/api/v1/documents", {
        method: "POST",
        headers: { "Content-Type": "multipart/form-data; boundary=test" },
        body: unauthorized,
      })
    ).status,
    403,
  );
  browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage({
    viewport: { width: 1600, height: 1100 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (
      m.type() === "error" &&
      /Content Security Policy|Refused to/.test(m.text())
    )
      errors.push(m.text());
  });
  // Simulate a slow phase plus one transient poll failure. Real conversions
  // above test the worker; this deterministic sequence tests the browser state.
  const pending = {...conversion, status:"running", elapsed_seconds:72, result:{pages:null},
    progress:{version:1,label:"Resolving paint visibility",phase:"visibility",
      page_number:1,page_count:2,completed:40,total:100,unit:"paints"}};
  const documentRoute = `${url}/api/v1/documents/${doc.id}`;
  const conversionRoute = url + conversion.status_url;
  await page.route(documentRoute, async route => {
    const response = await route.fetch();
    const value = await response.json();
    value.conversions = value.conversions.map(c => c.id === conversion.id ? pending : c);
    await route.fulfill({json:value});
  });
  let polls = 0;
  await page.route(conversionRoute, async route => {
    polls++;
    if (polls === 1) await route.fulfill({status:503,json:{error:"Temporary poll failure"}});
    else if (polls <= 3) await route.fulfill({json:pending});
    else await route.fulfill({json:conversion});
  });
  await page.goto(url + conversion.review_url);
  await page.waitForFunction(() => document.getElementById("work-detail").textContent.includes("Resolving paint visibility"));
  assert.match(await page.locator("#progress-time").textContent(), /1m 1[2-5]s elapsed/);
  assert.equal(Math.round(Number(await page.locator("#work-progress").getAttribute("value"))), 49);
  await page.locator("#preview").evaluate(img => {
    window.previewSrcChanges = 0;
    new MutationObserver(changes => window.previewSrcChanges += changes.filter(c => c.attributeName === "src").length)
      .observe(img,{attributes:true});
  });
  await page.waitForFunction(() => document.getElementById("work-status").dataset.phase === "ready");
  assert.equal(await page.locator("#report").getAttribute("href"), conversion.result.pages[0].artifacts.summary);
  assert.equal(await page.locator("#report").textContent(), "Conversion summary ↗");
  assert(polls >= 4, "Polling recovers after a transient failure");
  assert.equal(await page.evaluate(() => window.previewSrcChanges), 1, "Only the final artifact changes the image, not each progress poll");
  assert.equal(await page.locator("#work-progress").getAttribute("value"), "100");
  await page.unroute(documentRoute);
  await page.unroute(conversionRoute);

  await page.waitForFunction(() =>
    document.getElementById("metrics").textContent.includes("Pen mileage"),
  );
  await page.locator("#preview").evaluate((img) => img.decode());
  assert.equal(await page.locator('#inks select[aria-label="Pen arrangement"]').count(),0);
  assert.equal(await page.locator('#inks .ink-pair[data-ink-ids]').evaluateAll(rows=>rows.reduce((n,r)=>n+JSON.parse(r.dataset.inkIds).length,0)),4);
  const generate = page.locator("#convert-button");
  assert(
    await generate.isDisabled(),
    "A saved preview starts with matching settings",
  );
  assert.match(
    await page.locator("#page option:checked").textContent(),
    /17 × 11 in/,
  );
  await page.locator("#rotation").selectOption("90");
  assert(await generate.isEnabled());
  await page.locator("#rotation").selectOption("0");
  assert(await generate.isDisabled(), "Undoing a setting restores the match");
  await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator("#paper-unit").selectOption("mm");
  assert(
    await generate.isDisabled(),
    "Display units do not alter physical settings",
  );
  await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator("#paper-unit").selectOption("in");
  await page.locator("#offset-x").fill("1.2");
  assert(await generate.isEnabled());
  await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator("#paper-unit").selectOption("mm");
  assert.equal(Number(await page.locator("#offset-x").inputValue()), 30.48);
  await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator("#paper-unit").selectOption("in");
  assert.equal(Number(await page.locator("#offset-x").inputValue()), 1.2);
  await page.locator("#offset-x").fill("0");
  assert(await generate.isDisabled());
  await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator("#paper-unit").selectOption("mm");
  await page.locator("#spacing").fill("3");
  assert(await generate.isEnabled());
  await page.locator("#spacing").fill("2");
  assert(await generate.isDisabled());
  const originalCanvas = await page.locator(".canvas").boundingBox();
  const originalButton = await generate.boundingBox();
  await page
    .locator(".controls")
    .evaluate((e) => (e.scrollTop = e.scrollHeight));
  assert(
    (await page.locator(".controls").evaluate((e) => e.scrollTop)) > 0,
  );
  assert.deepEqual(
    await page.locator(".canvas").boundingBox(),
    originalCanvas,
    "Scrolling settings leaves the paper stationary",
  );
  assert.deepEqual(
    await generate.boundingBox(),
    originalButton,
    "Generate stays outside the scrolling settings",
  );
  assert(
    originalButton.y + originalButton.height <= 1100,
    "Generate is always visible",
  );
  await page.locator(".controls").evaluate((e) => (e.scrollTop = 0));

  await page.locator("#view").selectOption("source");
  assert(await generate.isDisabled(), "Preview view is display-only");
  await page.locator("#preview").evaluate((img) => img.decode());
  await page.locator("#page").selectOption("1");
  assert(
    await generate.isDisabled(),
    "Each page compares against its own saved settings",
  );
  await page.waitForFunction(() =>
    document
      .getElementById("placement-note")
      .textContent.includes("extends beyond"),
  );
  await page.locator("#view").selectOption("pen");
  await page.locator("#preview").evaluate((img) => img.decode());
  await page.screenshot({
    path: resolve(root, "build/pdf-studio-browser.png"),
    fullPage: true,
  });
  await page.locator("#page").selectOption("0");
  await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator("#paper-unit").selectOption("in");
  await page.locator("#offset-x").fill("1.2");
  await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator("#paper-unit").selectOption("mm");
  await page.locator("#fill").selectOption("feathered");
  await page.locator("#feather").fill("8");
  await page.route("**/api/v1/documents/*/draft", async (route) => {
    const response = await route.fetch();
    await new Promise((r) => setTimeout(r, 500));
    await route.fulfill({ response });
  });
  await page.locator("#convert-button").click();
  await page.locator("#spacing").fill("3");
  await page.waitForFunction(
    (old) =>
      document.getElementById("work-status").dataset.phase === "ready" &&
      document.getElementById("work-status").dataset.conversion !== old,
    conversion.id,
    { timeout: 120000 },
  );
  assert.equal(
    (await api(conversion.status_url)).settings_sha256,
    conversion.settings_sha256,
    "Previous conversion remains immutable",
  );
  assert.equal(
    await page.locator("#spacing").inputValue(),
    "3",
    "Edits made during generation survive",
  );
  assert(
    await generate.isEnabled(),
    "The completed preview does not match newer edits",
  );
  const currentId = new URL(page.url()).searchParams.get("conversion");
  const saved = await api(`/api/v1/conversions/${currentId}`);
  assert(
    Math.abs(saved.intent.pages[0].placement.offset_mm[0] - 30.48) < 1e-9,
    "Inch offsets are serialized in millimeters",
  );
  assert.equal(
    saved.intent.policies.fill.spacing_mm,
    2,
    "The submitted settings are frozen",
  );
  await page.locator("#spacing").fill("2");
  assert(
    await generate.isDisabled(),
    "Reverting to the completed revision disables Generate",
  );
  await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator("#paper-unit").selectOption("mm");
  await page.reload();
  await page.waitForFunction(() =>
    document.getElementById("metrics").textContent.includes("Pen mileage"),
  );
  assert.equal(
    await page.locator("#paper-unit").inputValue(),
    "mm",
    "Paper-unit preference survives reload",
  );
  assert(await generate.isDisabled(), "Reload restores matching controls");
  await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator("#paper-unit").selectOption("in");

  assert.equal(await page.locator("#inks input").count(), 0, "Source inks are immutable");
  await page.setViewportSize({ width: 700, height: 900 });
  await page.screenshot({
    path: resolve(root, "build/pdf-studio-mobile.png"),
    fullPage: true,
  });
  const mobileButton = await generate.boundingBox();
  assert(
    mobileButton.y + mobileButton.height <= 900,
    "Generate remains visible in the compact layout",
  );
  const mobileCanvas = await page.locator(".canvas").boundingBox();
  await page.locator(".controls").evaluate((e) => (e.scrollTop = 0));
  assert.deepEqual(await page.locator(".canvas").boundingBox(), mobileCanvas);
  const conversionBefore=await page.locator('#convert-button').isDisabled();
  const placementBefore=await page.evaluate(()=>['scale','rotation','offset-x','offset-y'].map(id=>document.getElementById(id).value));
  let gestureRequests=0;
  const countGestureRequest=request=>{if(request.method()==='POST')gestureRequests++;};
  page.on('request',countGestureRequest);
  const canvas=await page.locator('.canvas').boundingBox(),anchor={clientX:canvas.x+canvas.width*.4,clientY:canvas.y+canvas.height*.4};
  const imageBefore=await page.locator('#preview').boundingBox();
  await page.locator('.canvas').dispatchEvent('wheel',{...anchor,deltaY:-220});
  const imageAfter=await page.locator('#preview').boundingBox();
  assert(imageAfter.width>imageBefore.width);
  const fraction=(anchor.clientX-imageBefore.x)/imageBefore.width;
  assert(Math.abs(imageAfter.x+fraction*imageAfter.width-anchor.clientX)<2,'Zoom stays anchored at the pointer');
  await page.mouse.move(anchor.clientX,anchor.clientY);await page.mouse.down();
  await page.mouse.move(anchor.clientX+30,anchor.clientY+20,{steps:4});await page.mouse.up();
  const dragged=await page.locator('#preview').boundingBox();assert(Math.abs(dragged.x-imageAfter.x-30)<2);
  await page.locator('.canvas').dispatchEvent('wheel',{...anchor,deltaY:-30,ctrlKey:true});
  assert.deepEqual(await page.evaluate(()=>['scale','rotation','offset-x','offset-y'].map(id=>document.getElementById(id).value)),placementBefore);
  assert.equal(await page.locator('#convert-button').isDisabled(),conversionBefore);
  assert.equal(gestureRequests,0,'Inspecting cannot submit a conversion or edit placement');
  await page.locator('#preview-fit').click();assert.equal(await page.locator('#preview-zoom').textContent(),'100%');
  page.off('request',countGestureRequest);
  assert.deepEqual(errors, []);
  // Expire every preview, then exercise the operator's recovery flow. Sources,
  // revision identities and exact HPGL must survive collection unchanged.
  const selected = new URL(page.url()).searchParams.get('conversion');
  assert(selected);
  const hpglUrl = `/api/v1/conversions/${selected}/pages/0/plot.hpgl`;
  const hpglBefore = Buffer.from(await (await fetch(url+hpglUrl)).arrayBuffer());
  await writeFile(`${state}/storage-policy.json`, JSON.stringify({preview_max_bytes:0}));
  const sweep = spawnSync('python3',[`${root}/controller/scripts/retention.py`,'--state',state,'--apply'],{encoding:'utf8'});
  assert.equal(sweep.status,0,sweep.stdout+sweep.stderr);
  assert.equal(JSON.parse(sweep.stdout).preview_retained_bytes,0);
  assert.deepEqual(Buffer.from(await (await fetch(url+hpglUrl)).arrayBuffer()),hpglBefore);
  assert.deepEqual(Buffer.from(await (await fetch(url+`/api/v1/documents/${doc.id}/source.pdf`)).arrayBuffer()),original);
  const published = await api(`/api/v1/conversions/${selected}/publish`, {method:'POST'});
  assert(published.jobs.some(j=>j.job_id),'An expired preview can still publish its retained exact HPGL');
  await page.reload();
  await page.getByRole('button',{name:'Rebuild preview',exact:true}).waitFor();
  await page.waitForFunction(()=>document.getElementById('preview').naturalWidth>0);
  assert((await page.locator('#caption').textContent()).includes('Original PDF'));
  await writeFile(`${state}/storage-policy.json`,'{}');
  await generate.click();
  await page.waitForFunction(()=>document.getElementById('convert-button').disabled&&document.getElementById('dirty').textContent==='Preview matches these settings.');
  const rebuilt = new URL(page.url()).searchParams.get('conversion');
  assert.notEqual(rebuilt,selected,'Rebuilding preserves the historical revision');
  assert.deepEqual(Buffer.from(await (await fetch(url+hpglUrl)).arrayBuffer()),hpglBefore);
  console.log(
    "PASS PDF storage, revisions, placement, controls, compressed HPGL, expired previews and rebuild from retained inputs",
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
  await new Promise((r) => server.once("exit", r));
  await rm(state, { recursive: true, force: true });
}
