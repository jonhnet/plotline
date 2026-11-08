// Isolated spool and fresh server. No test contacts stain or plots anything.
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { mkdtemp, rm, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import assert from "node:assert/strict";
import { assignSlot } from "../web/geometry.js";
import {matchPens} from '../../shared/web/inksets.js';
function documentCarousel(pens,revision){const slots=Object.fromEntries(Array.from({length:8},(_,i)=>[i+1,{label:'',state:'empty',color:null,width_mm:null,speed_cm_s:5,force:4}]));for(const p of pens)slots[p.id]={label:p.color==='#000000'?'black':p.color,state:'loaded',color:p.color,width_mm:p.width_mm??null,speed_cm_s:p.speed_cm_s??5,force:p.force??4};return {revision,slots};}
// Occupied slots can always be reassigned, including a completely full carousel.
const slots = Object.fromEntries([1, 2, 3, 4, 5, 6, 7, 8].map((n) => [n, n]));
for (let ink = 1; ink <= 8; ink++)
  for (let destination = 1; destination <= 8; destination++) {
    const moved = assignSlot(slots, ink, destination);
    assert.equal(moved[ink], destination);
    for(const id of Object.keys(slots))if(Number(id)!==ink)assert.equal(moved[id],slots[id]);
    assert.equal(slots[ink], ink, "Input map stays unchanged");
  }
assert.deepEqual(assignSlot({ 1: 1, 2: 1, 3: 2 }, 3, 1), { 1: 1, 2: 1, 3: 1 });

// Requested black/red/green maps to the existing physical pens, including a
// wider green. Width breaks identical-color ties; unavailable pens are ignored.
const requests = [{id:1,color:"#000000",width_mm:.3},{id:2,color:"#ff0000",width_mm:.3},{id:3,color:"#008000",width_mm:.3}];
const physical = documentCarousel([
  {id:2,color:"#ff0000",width_mm:.3},{id:3,color:"#008000",width_mm:.7},
  {id:5,color:"#ff1493",width_mm:.3},{id:6,color:"#000000",width_mm:.3},
], 0);
assert.deepEqual(matchPens(requests, physical, {1:1,2:2,3:3}), {1:6,2:2,3:3});
physical.slots[8] = {...physical.slots[3], width_mm:.3};
assert.equal(matchPens(requests, physical, {1:1,2:2,3:3})[3], 8);
physical.slots[8].state = "unavailable";
assert.equal(matchPens(requests, physical, {1:1,2:2,3:3})[3], 3);
assert.deepEqual(matchPens(requests, documentCarousel([],0)),{1:0,2:0,3:0});

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const temp = await mkdtemp(`${tmpdir()}/plotline-browser-`);
await writeFile(
  `${temp}/device-observation.json`,
  JSON.stringify({
    identity: "7585B",
    observed_at: "2026-10-29T18:26:54Z",
    units_per_mm: 40,
    limits: [-7540, -5089, 7540, 5089],
  }),
);
const reserve = createServer();
await new Promise((r) => reserve.listen(0, "127.0.0.1", r));
const port = reserve.address().port;
await new Promise((r) => reserve.close(r));
const url = `http://127.0.0.1:${port}`;
const server = spawn(
  process.env.PLOTLINE_BINARY || resolve(root, "controller/target/release/plotline-studios"),
  ["--state", temp, "--listen", `127.0.0.1:${port}`],
  { stdio: ["ignore", "ignore", "pipe"] },
);
let logs = "",
  browser;
server.stderr.on("data", (data) => (logs += data));
const headers = {
  "Content-Type": "application/json",
  "X-Plotline-Request": "1",
};
try {
  for (let i = 0; ; i++) {
    try {
      if ((await fetch(`${url}/api/status`)).ok) break;
    } catch {}
    if (i > 100) throw new Error(logs);
    await new Promise((r) => setTimeout(r, 50));
  }
  browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1120 },
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
  const ready = () =>
    page
      .locator('#preview-canvas[data-ready="true"]')
      .waitFor({ timeout: 120000 });
  const drawn = async (attribute, value) =>
    page.waitForFunction(
      ([key, value]) =>
        document.getElementById("preview-canvas").dataset[key] === value,
      [attribute, value],
    );
  const showPane=async name=>{if(await page.locator('#show-'+name).getAttribute('aria-expanded')!=='true')await page.locator('#show-'+name).click();};
  const saved=async()=>{
    if(await page.locator('#save-state').textContent()==='Saved')return;
    await page.locator('#save').click();
    if(await page.locator('#save-dialog').isVisible())await page.locator('#save-form button[type=submit]').click();
    await page.waitForFunction(()=>document.getElementById('save-state').textContent==='Saved');
  };
  const getSetup=async id=>{
    const rows=await (await fetch(`${url}/api/jobs/${id}/recipes`)).json();
    const selected=await page.evaluate(()=>{const s=JSON.parse(sessionStorage.getItem('plotline-hpgl-workspace'));return s?.documents.find(d=>d.key===s.active)?.saved?.id;});
    const recipe=rows.find(r=>r.id===selected)||rows[0];
    return {...(recipe?.setup||await (await fetch(`${url}/api/jobs/${id}/setup`)).json()),carousel_revision:(await getCarousel()).revision};
  };
  const openJob=async name=>{
    const tab=page.getByRole('tab',{name,exact:true});
    if(await tab.count())await tab.click();else{
      await page.locator('#open-library').click();
      await page.locator('#jobs .document-card').filter({hasText:name}).first().getByRole('button',{name:/^(Open|Switch to document)$/}).click();
    }
    await page.waitForFunction(name=>document.title.includes(name),name);
  };
  const exportPlan = async (id, value) =>
    await fetch(`${url}/api/jobs/${id}/prepared.hpgl`, {
      method: "POST",
      headers,
      body: JSON.stringify(value),
    });
  const getCarousel = async () => (await fetch(`${url}/api/carousel`)).json();
  const setCarousel = async (value) => {
    const r = await fetch(`${url}/api/carousel`, {method:"PUT", headers, body:JSON.stringify(value)});
    assert.equal(r.status, 200); return r.json();
  };
  const loadedButton=slot=>page.locator(`#pens .ink-loaded button[aria-label^="Loaded pen — slot ${slot}:"]`).first();
  const mapInk=async(ink,slot)=>{await showPane('pens');
    await showPane('pens');await page.getByLabel('Pen arrangement', {exact:true}).selectOption('document');await page.locator(`#pens [data-ink-id="${ink}"]`).click();
    await page.locator(`#pens .ink-loaded .pen-cap[data-slot="${slot}"]`).first().click();
    // Imported HPGL can use substitutions directly, without opening a dialog.
    await page.locator('.ink-scrim').waitFor({state:'hidden'});
  };
  const editSlot = async (slot, fields) => {
    await showPane('pens');await page.getByRole('button',{name:`Edit loaded pen — slot ${slot}`,exact:true}).first().click();
    for(const [key,value] of Object.entries(fields)){
      const name={name:'label',width:'width_mm',speed:'speed_cm_s',force:'force'}[key];
      if(key==='state')await page.locator('.ink-dialog select').selectOption(value);
      else await page.locator(`.ink-dialog input[name="${name}"]`).fill(value);
    }
    await page.getByRole('button',{name:'OK',exact:true}).click();await page.locator('.ink-dialog').waitFor({state:'hidden'});
  };
  await page.goto(url+"/hpgl");
  await page.locator("#empty:not([hidden])").waitFor();
  const source = Buffer.from(
    'IN;PA;CO"pen id=1 r=0 g=0 b=0 width_mm=0.3";CO"pen id=2 r=210 g=30 b=30 width_mm=0.7";SP1;VS9;FS2;PU0,0;PD4000,0,4000,3000,0,3000,0,0;PU;SP2;VS4;FS5;PU500,500;PD3500,2500;PU;SP3;PU500,2500;PD3500,500;PU;SP0;\r\n',
  );
  await page.locator("#upload-open").click();
  await page.locator("#file-input").setInputFiles({
    name: "Three pens.hpgl",
    mimeType: "application/octet-stream",
    buffer: source,
  });
  await page.locator("#upload-submit").click();
  await ready();
  const id = page.url().split("#")[1];
  assert.equal(await page.locator("#pens .ink-loaded .pen-cap").count(), 8);
  assert.equal(await page.locator("#export-plan").isDisabled(), true);
  await setCarousel(documentCarousel((await (await fetch(`${url}/api/jobs/${id}`)).json()).summary.pens,(await getCarousel()).revision));
  await showPane('pens');await page.locator('#carousel-refresh').click();
  await drawn("widths", JSON.stringify({1:0.3,2:0.7,3:null}));
  await saved();
  assert.equal(await page.locator("#dimensions").isVisible(), false);
  assert.equal(
    await page
      .getByRole("button", { name: "Facing plotter", exact: true })
      .isVisible(),
    true,
  );
  for (const label of [
    "ANSI C · 17 × 22 in",
    "ANSI D · 22 × 34 in",
    "ANSI E · 34 × 44 in",
  ])
    assert.ok(
      (await page.locator("#paper-preset").textContent()).includes(label),
    );
  const initial = await getCarousel();
  assert.equal(initial.slots[1].width_mm, .3);
  assert.equal(initial.slots[2].width_mm, .7);
  assert.equal(initial.slots[3].width_mm, null);
  assert.equal(initial.slots[1].speed_cm_s, 9);
  assert.equal(initial.slots[1].force, 2);
  assert.equal(
    await page.locator("#dimensions").textContent(),
    "3.94 × 2.95 in",
  );
  assert.deepEqual(
    Buffer.from(
      await (await fetch(`${url}/api/jobs/${id}/source.hpgl`)).arrayBuffer(),
    ),
    source,
  );
  // Check actual raster output, not merely a "ready" flag: all three colors.
  const colors = await page.evaluate(() => {
    const src = document.getElementById("preview-canvas"),
      c = document.createElement("canvas");
    c.width = src.width;
    c.height = src.height;
    const ctx = c.getContext("2d");
    ctx.drawImage(src, 0, 0);
    const pixels = ctx.getImageData(0, 0, c.width, c.height).data;
    const counts = { black: 0, red: 0, green: 0 };
    for (let i = 0; i < pixels.length; i += 4)
      if (pixels[i + 3] > 50) {
        const [r, g, b] = pixels.slice(i, i + 3);
        if (Math.abs(r - g) < 20 && r < 80) counts.black++;
        else if (r > g * 1.3) counts.red++;
        else if (g > r * 1.3) counts.green++;
      }
    return counts;
  });
  for (const [color, count] of Object.entries(colors))
    assert.ok(count > 30, `${color} pixels: ${count}`);
  assert.ok(
    (await page.locator("#viewport svg *").count()) < 25,
    "No vector map DOM",
  );
  const blackCoverage = () =>
    page.evaluate(() => {
      const src = document.getElementById("preview-canvas"),
        c = document.createElement("canvas");
      c.width = src.width;
      c.height = src.height;
      const ctx = c.getContext("2d");
      ctx.drawImage(src, 0, 0);
      const a = ctx.getImageData(0, 0, c.width, c.height).data;
      let total = 0;
      for (let i = 0; i < a.length; i += 4)
        if (a[i] < 80 && Math.abs(a[i] - a[i + 1]) < 20)
          total += a[i + 3] / 255;
      return total;
    });
  const thin = await blackCoverage();
  await editSlot(1, {width:"0.7"});
  await drawn("widths", JSON.stringify({ 1: 0.7, 2: 0.7, 3: null }));
  const thick = await blackCoverage();
  assert.ok(
    thick / thin > 1.9 && thick / thin < 2.8,
    `Physical width changes raster coverage: ${thick / thin}`,
  );
  await editSlot(1, {width:"0.3"});
  await drawn("widths", JSON.stringify({ 1: 0.3, 2: 0.7, 3: null }));
  await showPane("plotter");await page.locator("#turn-sheet").click();
  assert.equal(
    await page.locator("#dimensions").textContent(),
    "2.95 × 3.94 in",
  );
  await page.locator("#viewport").press("+");
  await page.waitForFunction(
    () => document.getElementById("zoom").textContent === "125%",
  );
  await showPane("pens");await page.locator("#choose-inks").click();await page.getByLabel("Include ink 2", { exact: true }).uncheck();await page.locator("#ink-inclusion-dialog").getByRole("button",{name:"Apply",exact:true}).click();
  await drawn("pens", "1,3");
  const movedPens = await getCarousel();
  movedPens.slots[6] = {...movedPens.slots[1]};
  movedPens.slots[5] = {...movedPens.slots[2]};
  movedPens.slots[4] = {...movedPens.slots[3]};
  await setCarousel(movedPens);
  await showPane("pens");await page.locator("#carousel-refresh").click();
  await page.waitForFunction(() => document.querySelector('#pens .ink-loaded button[aria-label^="Loaded pen — slot 6:"]')?.title.includes("black"));
  for (const [ink, slot] of [
    [1, 6],
    [2, 5],
    [3, 4],
  ])
    await mapInk(ink,slot);
  await editSlot(6, {width:"0.7",speed:"12",force:"6"});
  await editSlot(4, {width:"0.3",speed:"8",force:"2"});
  await drawn("widths", JSON.stringify({ 1: 0.7, 2: 0.7, 3: 0.3 }));
  await page.locator("#travel-toggle").check();
  await drawn("travel", "true");
  await saved();
  let setup = await getSetup(id);
  assert.equal(setup.rotation, 90);
  assert.deepEqual(setup.pens, [1, 3]);
  const exported = await (await exportPlan(id, setup)).text();
  assert.match(exported, /SP6;\nVS12;\nFS6;/);
  assert.match(exported, /SP4;\nVS8;\nFS2;/);
  assert.match(exported, /CO"pen id=6 r=0 g=0 b=0 width_mm=0.7";/);
  assert.equal(
    await (await exportPlan(id, setup)).text(),
    exported,
    "Repeated export is byte-identical; rotation does not accumulate",
  );
  const importedResponse = await fetch(
    `${url}/api/jobs?name=Roundtrip.hpgl&units_per_mm=40`,
    { method: "POST", headers: { "X-Plotline-Upload": "1" }, body: exported },
  );
  assert.equal(importedResponse.status, 201);
  const imported = await importedResponse.json();
  const roundtripInk = imported.summary.pens.find((p) => p.id === 6);
  assert.equal(roundtripInk.width_mm, 0.7);
  assert.equal(roundtripInk.speed_cm_s, 12);
  assert.equal(roundtripInk.force, 6);
  assert.equal(roundtripInk.color, "#000000");
  assert.match(exported, /SP4;/);
  assert.doesNotMatch(exported, /SP[1235];/);
  assert.match(
    exported,
    /PU1500,-2000;\nPD1500,2000;/,
    "Physical quarter-turn, full-source centering",
  );
  const dl = page.waitForEvent("download");
  await page.locator("#export-tools > summary").click();await page.locator("#export-plan").click();await page.locator("#export-tools > summary").click();
  assert.equal(await readFile(await (await dl).path(), "utf8"), exported);
  // Viewpoint is independent of physical rotation.
  await page.locator("#view-facing").click();
  await saved();
  setup = await getSetup(id);
  assert.equal(await (await exportPlan(id, setup)).text(), exported);
  assert.match(
    await page.locator("#paper-scene").textContent(),
    /FRONT · YOU ARE HERE/,
  );
  await mapInk(3,6);await saved();
  assert.equal(await page.locator('#export-plan').isDisabled(),false);
  const merged=await getSetup(id);assert.equal(merged.pen_map[1],6);assert.equal(merged.pen_map[3],6);
  assert.equal((await exportPlan(id,merged)).status,200);
  const invalid=structuredClone(merged);invalid.pen_map['1']=0;
  assert.equal((await exportPlan(id,invalid)).status,422);
  await mapInk(3,4);await saved();
  await page.reload();
  await ready();
  assert.equal(await page.locator("#angle").textContent(), "90°");
  assert.equal(
    await page.locator("#view-facing").getAttribute("aria-pressed"),
    "true",
  );
  assert.equal(
    (await getSetup(id)).pen_map[1],
    6,
  );
  await drawn("pens", "1,3");
  const reloaded = await getCarousel();
  assert.equal(reloaded.slots[6].width_mm, .7);
  assert.equal(reloaded.slots[6].speed_cm_s, 12);
  assert.equal(reloaded.slots[6].force, 6);
  await showPane('pens');await page.getByRole('button',{name:'Edit loaded pen — slot 6',exact:true}).first().click();
  await page.locator('.ink-dialog input[name="speed_cm_s"]').fill("61");
  await page.getByRole("button",{name:"OK",exact:true}).click();
  assert.equal(await page.locator(".ink-dialog").isVisible(),true);
  assert.equal((await getCarousel()).slots[6].speed_cm_s,12);
  await page.locator(".ink-dialog").getByRole("button",{name:"Cancel",exact:true}).click();
  await page.locator("#viewport").scrollIntoViewIfNeeded();
  const before = await page.locator("#paper-scene").getAttribute("viewBox");
  const box = await page.locator("#viewport").boundingBox();
  await page.mouse.move(box.x + 100, box.y + 100);
  await page.mouse.down();
  await page.mouse.move(box.x + 150, box.y + 150);
  await page.mouse.up();
  await page.waitForFunction(
    (before) =>
      document.getElementById("paper-scene").getAttribute("viewBox") !== before,
    before,
  );
  await showPane("plotter");await page.locator("#paper-mode").selectOption("reported");
  assert.match(
    await page.locator("#placement-status").textContent(),
    /14.84 × 10.02 in/,
  );
  assert.match(
    await page.locator("#paper-scene").textContent(),
    /paper edges unknown/,
  );
  assert.equal(await page.locator("#paper-preset").isDisabled(), true);
  assert.equal(await page.locator("#turn-sheet").isDisabled(), false);
  assert.equal(await page.locator("#swap-feed-edge").isDisabled(), true);
  await showPane("plotter");await page.locator("#paper-mode").selectOption("planned");
  await page.locator("#paper-details summary").click();
  await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator("#paper-unit").selectOption("mm");
  await page.locator("#paper-x").fill("100");
  await page.locator("#paper-x").press("Tab");
  assert.equal(await page.locator("#export-plan").isDisabled(), true);
  assert.match(
    await page.locator("#placement-status").textContent(),
    /too large/,
  );
  for (const [value, x, y] of [
    ["558.8,431.8", 558.8, 431.8],
    ["863.6,558.8", 863.6, 558.8],
    ["1117.6,863.6", 1117.6, 863.6],
  ]) {
    await showPane("plotter");await page.locator("#paper-preset").selectOption(value);
    await saved();
    const planned = await getSetup(id);
    assert.equal(planned.paper_x_mm, x);
    assert.equal(planned.paper_y_mm, y);
  }
  await showPane("plotter");await page.locator("#paper-preset").selectOption("431.8,279.4");
  await saved();
  // Layout writes and export require same-origin custom headers too.
  for (const suffix of ["setup", "prepared.hpgl"]) {
    const res = await fetch(`${url}/api/jobs/${id}/${suffix}`, {
      method: suffix === "setup" ? "PUT" : "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Plotline-Request": "1",
        Origin: "https://unrelated.example",
      },
      body: JSON.stringify(setup),
    });
    assert.equal(res.status, 403);
  }
  assert.deepEqual(
    Buffer.from(
      await (await fetch(`${url}/api/jobs/${id}/source.hpgl`)).arrayBuffer(),
    ),
    source,
  );
  // Letter-sized asymmetric L: two orientations fit, with the 39 mm margin
  // above or below upright artwork. Check the actual SVG, raster and export.
  const letterSource = "IN;PA;SP1;PU0,0;PD7428,0,7428,8936;PU;SP0;";
  const letterResponse = await fetch(
    `${url}/api/jobs?name=Letter%20orientations.hpgl&units_per_mm=40`,
    {
      method: "POST",
      headers: { "X-Plotline-Upload": "1" },
      body: letterSource,
    },
  );
  assert.equal(letterResponse.status, 201);
  const letter = await letterResponse.json();
  await openJob("Letter orientations");
  await ready();
  await page.locator("#travel-toggle").uncheck();
  await showPane("plotter");await page.locator("#paper-preset").selectOption("279.4,215.9");
  await page.locator("#paper-details").evaluate((el) => (el.open = true));
  for (const x of [215.9, 279.4]) {
    await page.locator("#swap-feed-edge").click();
    await saved();
    assert.equal((await getSetup(letter.id)).paper_x_mm, x);
    assert.equal(await page.locator("#paper-preset").inputValue(), "279.4,215.9");
  }
  await page.locator("#paper-details").evaluate((el) => (el.open = false));
  let firstLetterExport;
  for (const angle of [0, 90, 180, 270, 0]) {
    await drawn("rotation", String(angle));
    await saved();
    const planned = await getSetup(letter.id);
    assert.equal(planned.rotation, angle);
    assert.equal(planned.paper_x_mm, 279.4);
    assert.equal(planned.paper_y_mm, 215.9);
    assert.deepEqual(planned.margins_mm, [39, 15, 15, 15]);
    assert.equal(await page.locator("#paper-preset").inputValue(), "279.4,215.9");
    const margins = await page.evaluate(() => {
      const p = document.getElementById("sheet-outline").getBBox(),
        w = document.getElementById("drawing-window").getBBox();
      return [w.x - p.x, w.y - p.y,
        p.x + p.width - w.x - w.width, p.y + p.height - w.y - w.height];
    });
    for (let side = 0; side < 4; side++)
      assert.ok(Math.abs(margins[side] - (side === angle / 90 ? 39 : 15)) < 0.001,
        `Margin ${side} at ${angle}°: ${margins}`);
    const upright = await page.evaluate(() => {
      const arrow = document.querySelector("#paper-scene line"),
        src = document.getElementById("preview-canvas"),
        c = document.createElement("canvas");
      c.width = src.width; c.height = src.height;
      const ctx = c.getContext("2d"); ctx.drawImage(src, 0, 0);
      const pixels = ctx.getImageData(0, 0, c.width, c.height).data;
      let x0 = c.width, y0 = c.height, x1 = -1, y1 = -1;
      for (let y = 0; y < c.height; y++)
        for (let x = 0; x < c.width; x++)
          if (pixels[(y * c.width + x) * 4 + 3] > 30) {
            x0 = Math.min(x0, x); y0 = Math.min(y0, y);
            x1 = Math.max(x1, x); y1 = Math.max(y1, y);
          }
      // The L's vertical stroke must remain on the right, not upside-down.
      const midY = Math.round((y0 + y1) / 2);
      let left = 0, right = 0;
      for (let x = x0; x <= x1; x++) {
        const alpha = pixels[(midY * c.width + x) * 4 + 3];
        if (x < (x0 + x1) / 2) left += alpha; else right += alpha;
      }
      return { aspect: (x1 - x0) / (y1 - y0), left, right,
        arrowUp: arrow.x1.baseVal.value === arrow.x2.baseVal.value
          && arrow.y2.baseVal.value < arrow.y1.baseVal.value };
    });
    assert.ok(Math.abs(upright.aspect - 7428 / 8936) < 0.02);
    assert.ok(upright.arrowUp && upright.left === 0 && upright.right > 0);
    const fits = angle === 90 || angle === 270;
    assert.equal(await page.locator("#export-plan").isDisabled(), !fits);
    const result = await exportPlan(letter.id, planned);
    assert.equal(result.status, fits ? 200 : 422);
    if (fits) {
      const commands = await result.text();
      assert.match(commands, angle === 90 ? /PU4468,-3714;/ : /PU-4468,3714;/);
      if (angle === 90) firstLetterExport = commands;
      else assert.notEqual(commands, firstLetterExport);
      // Looking from the machine never alters the prepared coordinates.
      assert.equal(await (await exportPlan(letter.id, { ...planned, facing: "plotter" })).text(), commands);
    }
    if (angle === 270) {
      await page.reload(); await ready(); await drawn("rotation", "270");
      assert.equal(await page.locator("#angle").textContent(), "270°");
    }
    if (angle === 0 && firstLetterExport) break;
    await showPane("plotter");await page.locator("#turn-sheet").click();
  }
  assert.equal(await (await fetch(`${url}/api/jobs/${letter.id}/source.hpgl`)).text(), letterSource);
  await page.locator("#upload-open").click();
  await page.locator("#file-input").setInputFiles({
    name: "unsupported.hpgl",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("IN;SC0,1,0,1;"),
  });
  await page.locator("#upload-submit").click();
  await page.locator("#upload-error:not([hidden])").waitFor();
  assert.match(
    await page.locator("#upload-error").textContent(),
    /Unsupported HPGL command SC/,
  );
  await page.locator("#upload-close").click();
  // Large deterministic synthetic input, always available in a fresh checkout.
  const rows = ["IN;PA;SP1;"];
  for (let row = 0; row < 1600; row++) {
    const y = row * 5,
      points = [];
    for (let i = 1; i < 1250; i++) points.push(`${i * 9},${y + (i % 2)}`);
    rows.push(`PU0,${y};PD${points.join(",")};PU;`);
  }
  const res = await fetch(
    `${url}/api/jobs?name=Two%20million%20points.hpgl&units_per_mm=40`,
    {
      method: "POST",
      headers: { "X-Plotline-Upload": "1" },
      body: rows.join("\n"),
    },
  );
  assert.equal(res.status, 201);
  const large = await res.json();
  assert.equal(large.summary.points, 2000000);
  await openJob("Two million points");
  // Switching jobs while the old worker parses must not resurrect its result.
  await openJob("Three pens");
  await ready();
  await drawn("pens", "1,3");
  assert.equal(await page.locator('[role=tab][aria-selected=true]').textContent(), "Three pens");
  await openJob("Two million points");
  await ready();
  await page.evaluate(() => {
    const v = document.getElementById("viewport"),
      r = v.getBoundingClientRect();
    for (let i = 0; i < 200; i++)
      v.dispatchEvent(
        new WheelEvent("wheel", {
          deltaY: i < 100 ? -2 : 2,
          clientX: r.x + r.width / 2,
          clientY: r.y + r.height / 2,
          cancelable: true,
        }),
      );
  });
  await page.waitForFunction(
    () => document.getElementById("zoom").textContent === "100%",
  );
  await page.locator("#travel-toggle").check();
  await showPane("plotter");await page.locator("#turn-sheet").click();
  await showPane("plotter");await page.locator("#turn-sheet").click();
  await page.locator("#travel-toggle").uncheck();
  await drawn("travel", "false");
  await drawn("rotation", "180");
  await saved();
  await page.waitForTimeout(600);
  assert.equal(await page.locator("#angle").textContent(), "180°");
  assert.equal(await page.locator("#zoom").textContent(), "100%");
  // Screenshot a real map when available, retaining fresh-checkout independence.
  let map;
  try {
    map = await readFile(resolve(root, "build/ravenna-letter/plot.hpgl"));
  } catch {}
  if (map) {
    const res = await fetch(
      `${url}/api/jobs?name=Ravenna%20letter.hpgl&units_per_mm=40`,
      { method: "POST", headers: { "X-Plotline-Upload": "1" }, body: map },
    );
    assert.equal(res.status, 201);
    await openJob("Ravenna letter");
    await ready();
    await showPane("plotter");await page.locator("#paper-preset").selectOption("431.8,279.4");
    await page.locator("#view-facing").click();
    await showPane("plotter");await page.locator("#turn-sheet").click();
    await page.locator("#paper-details").evaluate((el) => (el.open = false));
    await saved();
    await page.waitForTimeout(600);
  }
  await mkdir(resolve(root, "build/controller"), { recursive: true });
  await page.screenshot({
    path: resolve(root, "build/controller/desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500);
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
    true,
  );
  await mapInk(1,2);
  await page.locator("#choose-inks").click();await page.getByLabel("Include ink 1", { exact: true }).uncheck();await page.locator("#ink-inclusion-dialog").getByRole("button",{name:"Apply",exact:true}).click();
  await page.locator("#choose-inks").click();await page.getByLabel("Include ink 1", { exact: true }).check();await page.locator("#ink-inclusion-dialog").getByRole("button",{name:"Apply",exact:true}).click();
  await drawn("pens", map ? "1,2,3" : "1");
  await page.screenshot({
    path: resolve(root, "build/controller/phone.png"),
    fullPage: true,
  });
  // Exercise the complete physical-inventory workflow from Jon's example.
  const requestedSource = 'IN;CO"pen id=1 r=0 g=0 b=0 width_mm=0.3";CO"pen id=2 r=255 g=0 b=0 width_mm=0.3";CO"pen id=3 r=0 g=128 b=0 width_mm=0.3";SP1;PU0,0;PD4000,0;PU;SP2;PU0,1000;PD4000,1000;PU;SP3;PU0,2000;PD4000,2000;PU;SP0;';
  const requestJob = await (await fetch(`${url}/api/jobs?name=Carousel%20proof.hpgl&units_per_mm=40`, {
    method:"POST",headers:{"X-Plotline-Upload":"1"},body:requestedSource,
  })).json();
  const configured = documentCarousel([
    {id:2,color:"#ff0000",width_mm:.3},{id:3,color:"#008000",width_mm:.7},
    {id:5,color:"#ff1493",width_mm:.3},{id:6,color:"#000000",width_mm:.3},
  ], (await getCarousel()).revision);
  const expectedInventory = await setCarousel(configured);
  await showPane("pens");await page.locator("#carousel-refresh").click();
  await openJob("Carousel proof"); await ready();
  assert.deepEqual(await getCarousel(), expectedInventory, "Opening a document does not replace the physical pens");
  await page.locator("#match-loaded-pens").click(); await saved();
  assert.deepEqual((await getSetup(requestJob.id)).pen_map, {1:6,2:2,3:3});
  await drawn("widths", JSON.stringify({1:.3,2:.3,3:.7}));
  // Clicking slot 6 and typing purple changes the mapped preview, never source metadata.
  await editSlot(6, {name:"Purple",width:"0.7",speed:"12",force:"6"});
  await drawn("colors", JSON.stringify({1:"#800080",2:"#ff0000",3:"#008000"}));
  await drawn("widths", JSON.stringify({1:.7,2:.3,3:.7}));
  const purplePixels = await page.evaluate(() => {
    const src = document.getElementById("preview-canvas"), c = document.createElement("canvas");
    c.width=src.width; c.height=src.height; const ctx=c.getContext("2d"); ctx.drawImage(src,0,0);
    const a=ctx.getImageData(0,0,c.width,c.height).data; let count=0;
    for(let i=0;i<a.length;i+=4) if(a[i+3]>40 && a[i]>90 && a[i+1]<20 && a[i+2]>90) count++;
    return count;
  });
  assert.ok(purplePixels > 30, `Mapped purple pixels: ${purplePixels}`);
  const actual = await getCarousel();
  assert.equal(actual.slots[6].label, "Purple");
  const configuredSetup = {...await getSetup(requestJob.id),carousel_revision:actual.revision};
  const mappedOutput = await (await exportPlan(requestJob.id, configuredSetup)).text();
  assert.match(mappedOutput,/CO"pen id=6 r=128 g=0 b=128 width_mm=0.7"/);
  assert.match(mappedOutput,/SP6;\nVS12;\nFS6;/);
  assert.match(mappedOutput,/CO"pen id=3 r=0 g=128 b=0 width_mm=0.7"/);
  assert.equal(await (await fetch(`${url}/api/jobs/${requestJob.id}/source.hpgl`)).text(), requestedSource);
  assert.equal((await (await fetch(`${url}/api/jobs/${requestJob.id}`)).json()).summary.pens[0].width_mm,.3);
  // Concurrent editors cannot overwrite the winner, and stale preview exports fail.
  const stale = structuredClone(actual);
  const newer = structuredClone(actual); newer.slots[6].label = "Purple pen";
  await setCarousel(newer);
  assert.equal((await fetch(`${url}/api/carousel`,{method:"PUT",headers,body:JSON.stringify(stale)})).status,409);
  assert.equal((await exportPlan(requestJob.id,configuredSetup)).status,422);
  assert.equal((await fetch(`${url}/api/carousel`,{method:"PUT",headers:{...headers,Origin:"https://unrelated.example"},body:JSON.stringify(newer)})).status,403);
  await page.reload(); await ready();
  assert.match(await loadedButton(6).getAttribute("title"),/Purple pen/);
  await drawn("colors",JSON.stringify({1:"#800080",2:"#ff0000",3:"#008000"}));
  await page.setViewportSize({width:1440,height:1120});
  await page.screenshot({path:resolve(root,"build/controller/carousel-desktop.png"),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
  await page.screenshot({path:resolve(root,"build/controller/carousel-phone.png"),fullPage:true});
  assert.equal(await page.getByRole('button',{name:'Use document pens',exact:true}).count(),0);
  assert.deepEqual(errors, []);
  console.log(
    "Browser checks passed: editable eight-slot inventory, best matching, mapped raster colors/widths and export, stale edits, no inventory-overwrite shortcut; four sheet rotations with upright raster artwork, asymmetric margins and matching HPGL; physical pen widths and metadata round-trip; physical-slot speed/force; many-to-one slot assignment including full carousel; non-cumulative rotation; saved layouts; C/D/E sizes; folded statistics; source preservation; fit/CSRF rejection; two-million-point preview; desktop/phone.",
  );
} finally {
  await browser?.close();
  server.kill("SIGTERM");
  await new Promise((r) =>
    server.exitCode !== null ? r() : server.once("exit", r),
  );
  await rm(temp, { recursive: true, force: true });
}
