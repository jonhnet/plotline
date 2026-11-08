import {bindPaperUnits,onPaperUnitChange,paperSize,paperLength,readPaperField,writePaperField} from '/paper-units.js';
import {studioNavigation,openStudio,sourceLinks,mapSourceURL} from '/studio-navigation.js';
import {hpglWorkspace} from '/hpgl-workspace.js';
import {renderPreviewControls,stable,wireStudioTrays,studioNotice as notice} from '/studio.js';
import {previewNavigation} from '/preview-navigation.js';
import {renderInkSets,revealInk,equivalent,accepted,substitution,difference,modal,element,button,cap} from '/inksets.js';
import {
  matrix,
  project,
  rectangle,
  layout,
  fitError,
} from "./geometry.js";
import { plotControls } from "./plot-controls.js";
import { carouselControls, colorName, usable } from "./carousel.js";
const $ = (id) => document.getElementById(id);
renderPreviewControls($('preview-tools'),{fitId:'fit',onFit:fit,onZoom:factor=>navigation.zoomBy(factor)});
bindPaperUnits();
let jobs = [],
  pdfJobs = [],
  selected = null,
  setup = null,
  observation = null,
  view = null,
  base = null,
  generation = 0;
let worker = null,
  ready = false,
  busy = false,
  rendered = null,
  renderTimer = null,
  frameRequest = null,
  lastInput = 0,
  revision = 0;
let exporting = false, drawerPane = 'pens', plotAttempt = null;
const distance = (mm) =>
  mm >= 1000 ? `${(mm / 1000).toFixed(2)} m` : `${mm.toFixed(1)} mm`;
const number = (n) => n.toLocaleString();

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const text = await response.text();
    let error;
    try {
      error = JSON.parse(text).error;
    } catch {}
    const failure = new Error(error || `${response.status}: ${text.slice(0, 200)}`);
    failure.status = response.status;
    throw failure;
  }
  return response;
}
const writeOptions = (value) => ({
  headers: { "Content-Type": "application/json", "X-Plotline-Request": "1" },
  body: JSON.stringify(value),
});
const studioNav=studioNavigation({studio:'hpgl',notice,open:async url=>{const id=url.hash.slice(1);if(id)await workspace.open(await api('/api/jobs/'+encodeURIComponent(id)).then(r=>r.json()),url.searchParams.get('recipe')||undefined);}});
const authoringByConversion=new Map();
const plotting = plotControls({
  api, writeOptions, changed: plotState,
  isPlotDocument: ticket=>workspace.isPlotDocument(ticket),
  unavailable: versionIssue,
  current: () => ({ job: selected, setup }),
  observed: (value) => {
    observation = value;
    $("paper-mode").querySelector('[value="reported"]').disabled = false;
    renderDeviceReading(true);
    if (setup) fit();
  },
});
const carousel = carouselControls({
  api, writeOptions, current: () => ({ job: selected, setup }), notice,
  changed(saveMapping = false) {
    if (!setup) return;
    setup.carousel_revision = carousel.value.revision;
    renderPens();
    if (saveMapping) saveSetup();
  },
});
// Tiny cached PNGs instead of independently painting the full SVG in every
// library item. One background thumbnail job at a time bounds CPU and memory.
const thumbnails = new Map(),
  thumbQueue = [],
  thumbPending = new Set();
let thumbRunning = false;
function thumbnail(job, img) {
  if (thumbnails.has(job.id)) {
    img.src = thumbnails.get(job.id);
    return;
  }
  if (!thumbPending.has(job.id)) {
    thumbPending.add(job.id);
    thumbQueue.push(job);
  }
  nextThumbnail();
}
function nextThumbnail() {
  if (
    thumbRunning ||
    !$("library").open ||
    !thumbQueue.length ||
    !window.Worker ||
    !window.OffscreenCanvas
  )
    return;
  thumbRunning = true;
  const job = thumbQueue.shift(),
    w = new Worker("/render-worker.js", { type: "module" });
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    w.terminate();
    thumbPending.delete(job.id);
    thumbRunning = false;
    nextThumbnail();
  };
  w.onmessage = ({ data }) => {
    if (data.type === "thumbnail") {
      const url = URL.createObjectURL(data.blob);
      thumbnails.set(job.id, url);
      document
        .querySelectorAll(`img[data-job="${job.id}"]`)
        .forEach((img) => (img.src = url));
      finish();
    } else if (data.type === "error") finish();
  };
  w.onerror = finish;
  w.postMessage({
    type: "load",
    thumbnail: true,
    job,
    url: `/api/jobs/${job.id}/preview.svg`,
  });
}
const workspace=hpglWorkspace({api,writeOptions,notice,thumbnail,refreshLibrary:refresh,
  current:()=>({job:selected,setup,view:{facing:setup?.facing,travel:$('travel-toggle').checked,appearance:$('preview-ink-colors').value,appearanceChosen:workspace.active?.view?.appearanceChosen||false,navigation:navigation.state}}),
  activate:loadEntry,clear:clearDocument});
async function refresh(prefer) {
  [jobs,pdfJobs]=await Promise.all([
    api("/api/jobs?library=true").then(r=>r.json()),
    api("/api/v1/documents?summary=true").then(r=>r.json()).then(r=>r.documents),
  ]);
  workspace.setJobs(jobs);
  $("pdf-intake").replaceChildren();
  for (const doc of pdfJobs.filter(d=>d.draft?.status!=="ready" || d.draft.jobs.some(p=>p.status==="empty"))) {
    const link=document.createElement("a"),title=document.createElement("strong"),status=document.createElement("small");
    link.className="job-card";link.href=doc.review_url;title.textContent=doc.name;
    status.textContent=doc.draft?.error || doc.error || (["received","inspecting"].includes(doc.status) ? "Reading PDF…" : doc.draft?.status==="queued" ? "Preparing pen draft…" : doc.draft?.status==="ready" ? "Some pages have no drawable pen paths · review PDF" : "Choose pen interpretation in PDF Studio");
    link.append(title,status);$("pdf-intake").append(link);
  }
  $('pdf-intake-details').hidden=!$('pdf-intake').childElementCount;
  renderVersion();if(setup)updatePlacement();
  if(prefer){const job=jobs.find(j=>j.id===prefer);if(job)await workspace.open(job);}

}
function currentPdfPage(){return pdfJobs.find(d=>d.id===selected?.pdf?.document_id)?.draft?.jobs?.find(p=>p.page_index===selected.pdf.page_index);}
function versionIssue(){
  const page=currentPdfPage();
  return selected?.pdf && page && page.job_id!==selected.id ? 'PDF Studio prepared a newer version. Open latest version to plot it.' : '';
}
function renderVersion(){
  const issue=versionIssue();$('version-notice').hidden=!issue;$('version-message').textContent=issue;
  $('open-latest').hidden=!currentPdfPage()?.job_id;
}
$('open-latest').onclick=async()=>{
  const id=currentPdfPage()?.job_id;if(!id)return;
  try{await workspace.open(await api('/api/jobs/'+id).then(r=>r.json()));}catch(e){notice(e.message);}
};
let refreshingLibrary=false;
setInterval(async()=>{
  if(refreshingLibrary || document.hidden)return;
  refreshingLibrary=true;
  try{await refresh();}catch{}finally{refreshingLibrary=false;}
},3000);
function saveSetup(){workspace.capture();}
function flushSave(){workspace.capture();}
function area() {
  return layout(setup, observation);
}
function updatePlacement() {
  if (!selected || !setup) return;
  const a = area(),
    error = fitError(selected, setup, a) || carousel.issue(setup) || pdfInkIssue();
  const inks = selected.summary.pens.filter((p) => setup.pens.includes(p.id));
  const penIssue=carousel.issue(setup)||pdfInkIssue();
  $("pen-readiness").textContent=penIssue||"";
  $('drawer-warning').textContent=penIssue?'⚠':'';
  $('drawer-warning').title=penIssue||'';
  $('show-pens').title=penIssue||'Pens & mapping';
  $("down-distance").textContent = distance(
    inks.reduce((sum, p) => sum + p.distance_mm, 0),
  );
  $("path-count").textContent =
    `${number(inks.reduce((sum, p) => sum + p.paths, 0))} included paths`;
  $("pen-count").textContent =
    `${inks.length} of ${selected.summary.pens.length} inks`;
  const w =
      setup.rotation % 180
        ? selected.summary.height_mm
        : selected.summary.width_mm,
    h =
      setup.rotation % 180
        ? selected.summary.width_mm
        : selected.summary.height_mm;
  $("dimensions").textContent = paperSize([w,h]);
  $("inches").textContent = "Original scale";
  $("angle").textContent = `${setup.rotation}°`;
  $("placement-status").textContent =
    error ||
    `Fits · ${paperSize([a.window[2]-a.window[0],a.window[3]-a.window[1]])} drawing area · ${setup.registration ? "Fixed page placement" : "centered"}`;
  $("placement-status").classList.toggle("invalid", !!error);
  $("export-plan").disabled = !!error || exporting;
  plotting.update(error);
}
function pdfInkIssue() {
  if (selected?.calibration) {
    for (const ink of selected.summary.pens) {
      if (setup.pen_map[ink.id]!==ink.id || !setup.pens.includes(ink.id)) return "Calibration uses every selected pen in its original slot.";
      if (stable(carousel.value?.slots[ink.id])!==stable(selected.calibration.carousel.slots[ink.id])) return "Pens changed since this sheet was created. Create a new calibration sheet.";
    }
  }
  if (!selected?.pdf) return "";
  for (const ink of selected.summary.pens.filter(p=>setup.pens.includes(p.id))) {
    const pen=carousel.value?.slots[setup.pen_map[ink.id]];
    if (!equivalent(ink,pen) && !accepted(ink,setup.pen_map[ink.id],pen,setup.accepted_substitutions?.[ink.id]))
      return `Ink ${ink.id}: ${difference(ink,pen)}. Review this change in Pens.`;
  }
  return "";
}
const ns = "http://www.w3.org/2000/svg";
function node(tag, attrs, text) {
  const n = document.createElementNS(ns, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text) n.textContent = text;
  return n;
}
function scene() {
  if (!setup || !selected) return;
  const a = area(),
    svg = $("paper-scene");
  svg.replaceChildren();
  const paper = rectangle(a.paper || a.window, setup.facing, setup.rotation),
    windowRect = rectangle(a.window, setup.facing, setup.rotation);
  const [x, y, w, h] = paper;
  const rect = (r, attrs) =>
    node("rect", { x: r[0], y: r[1], width: r[2], height: r[3], ...attrs });
  if (setup.facing === "plotter") {
    svg.append(rect([x - 25, y - 25, w + 50, 18], { rx: 3, fill: "#334d50" }));
    svg.append(
      node("circle", { cx: x - 15, cy: y - 16, r: 6, fill: "#8fa6a2" }),
    );
    svg.append(
      node(
        "text",
        {
          x: x + w / 2,
          y: y - 13,
          "text-anchor": "middle",
          fill: "#eff4ef",
          "font-size": 5,
        },
        "HP 7585B · CARRIAGE",
      ),
    );
    svg.append(
      node(
        "text",
        {
          x: x + w / 2,
          y: y + h + 18,
          "text-anchor": "middle",
          fill: "#536c69",
          "font-size": 5,
        },
        "FRONT · YOU ARE HERE",
      ),
    );
  }
  svg.append(
    rect(paper, {
      id: "sheet-outline",
      fill: a.paper ? "#fffefb" : "#f9fbf7",
      stroke: "#b7c2b4",
      "stroke-width": 0.5,
    }),
  );
  svg.append(
    rect(windowRect, {
      id: "drawing-window",
      fill: "none",
      stroke: "#8a9d86",
      "stroke-dasharray": "2 2",
      "stroke-width": 0.5,
    }),
  );
  svg.append(
    node(
      "text",
      { x, y: y - 4, fill: "#536c69", "font-size": 4.5 },
      a.paper ? "Planned sheet" : "Saved drawing area · paper edges unknown",
    ),
  );
  svg.append(
    node(
      "text",
      { x, y: y + h + 9, fill: "#536c69", "font-size": 4.5 },
      setup.facing === "plotter"
        ? "+Y → carriage   ·   +X ↓ paper feed"
        : "Artwork upright · sheet turns around it",
    ),
  );
  const r = (setup.rotation * Math.PI) / 180,
    c = Math.round(Math.cos(r)),
    s = Math.round(Math.sin(r));
  const top = selected.summary.height_mm / 2 + 5;
  const start = project([-s * top, c * top], setup.facing, setup.rotation),
    end = project(
      [-s * (top + 8), c * (top + 8)],
      setup.facing,
      setup.rotation,
    );
  svg.append(
    node("line", {
      x1: start[0],
      y1: start[1],
      x2: end[0],
      y2: end[1],
      stroke: "#326e68",
      "stroke-width": 1,
    }),
  );
  const dx = end[0] - start[0],
    dy = end[1] - start[1];
  svg.append(
    node("path", {
      d: `M${end[0] - dx * 0.35 - dy * 0.2},${end[1] - dy * 0.35 + dx * 0.2} L${end} L${end[0] - dx * 0.35 + dy * 0.2},${end[1] - dy * 0.35 - dx * 0.2}`,
      fill: "none",
      stroke: "#326e68",
      "stroke-width": 1,
    }),
  );
  svg.append(
    node(
      "text",
      { x: end[0] + 4, y: end[1], fill: "#326e68", "font-size": 4.5 },
      "ARTWORK TOP",
    ),
  );
}
function spec() {
  return {
    view: [...view],
    width: $("viewport").clientWidth,
    height: $("viewport").clientHeight,
    dpr: devicePixelRatio,
    rotation: setup.rotation,
    facing: setup.facing,
    pens: setup.pens.filter((id) => usable(carousel.value?.slots[setup.pen_map[id]])),
    colors: Object.fromEntries(selected.summary.pens.map((p) => {
      const pen=carousel.value?.slots[setup.pen_map[p.id]];
      return [p.id, ($('preview-ink-colors').value==='measured'&&pen?.measured_color)||pen?.color];
    })),
    widths: Object.fromEntries(selected.summary.pens.map((p) =>
      [p.id, carousel.value?.slots[setup.pen_map[p.id]]?.width_mm ?? null])),
    travel: $("travel-toggle").checked,
    revision,
  };
}
function requestRender() {
  clearTimeout(renderTimer);
  if (!worker || !ready || !view || busy || !viewReady()) return;
  busy = true;
  $("render-status").textContent = "Refining preview…";
  worker.postMessage({ type: "render", spec: spec() });
}
$('preview-ink-colors').onchange=()=>{if(workspace.active)workspace.active.view.appearanceChosen=true;workspace.capture();revision++;requestRender();};
function viewReady() {
  return view?.every(Number.isFinite) && view[2]>0 && view[3]>0 &&
    $("viewport").clientWidth>0 && $("viewport").clientHeight>0;
}
function updateView() {
  if (!view || !setup) return;
  if (!frameRequest)
    frameRequest = requestAnimationFrame(() => {
      frameRequest = null;
      if (!view || !setup || !viewReady()) return;
      $("paper-scene").setAttribute("viewBox", view.join(" "));
      $("zoom").textContent = `${Math.round((base[2] / view[2]) * 100)}%`;
      if (rendered) {
        const now = new DOMMatrix(
          matrix(view, $("viewport").clientWidth, setup.rotation, setup.facing),
        );
        $("preview-canvas").style.transform = now
          .multiply(new DOMMatrix(rendered.m).inverse())
          .toString();
      }
    });
  lastInput = performance.now();
  clearTimeout(renderTimer);
  renderTimer = setTimeout(requestRender, 140);
}
function fit() {
  if (!selected || !setup || $("viewport").clientWidth<1 || $("viewport").clientHeight<1) return;
  const a = area(),
    paper = rectangle(a.paper || a.window, setup.facing, setup.rotation);
  let w = selected.summary.width_mm,
    h = selected.summary.height_mm;
  if (setup.rotation % 180) [w, h] = [h, w];
  const ink = rectangle(
    setup.registration ? selected.summary.bounds.map(n=>n/selected.units_per_mm) : [-w / 2, -h / 2, w / 2, h / 2],
    setup.facing,
    setup.rotation,
  );
  let x0 = Math.min(paper[0] - 28, ink[0] - 12),
    y0 = Math.min(paper[1] - 30, ink[1] - 12),
    x1 = Math.max(paper[0] + paper[2] + 28, ink[0] + ink[2] + 12),
    y1 = Math.max(paper[1] + paper[3] + 24, ink[1] + ink[3] + 12);
  const cx = (x0 + x1) / 2,
    cy = (y0 + y1) / 2,
    aspect = $("viewport").clientWidth / $("viewport").clientHeight;
  w = x1 - x0;
  h = y1 - y0;
  if (w / h > aspect) h = w / aspect;
  else w = h * aspect;
  base = [cx - w / 2, cy - h / 2, w, h];
  navigation.reset();
  scene();
  updatePlacement();
  updateView();
}
function updatePens() {
  carousel.render();
  revision++;
  updatePlacement();
  updateView();
}
function renderPens() {
  const job=selected,reviews=[];
  if(job.pdf)for(const ink of job.summary.pens.filter(i=>setup.pens.includes(i.id))){
    const slot=setup.pen_map[ink.id],pen=carousel.value?.slots[slot];
    if(usable(pen)&&!equivalent(ink,pen)&&!accepted(ink,slot,pen,setup.accepted_substitutions?.[ink.id])){
      const actions=[{label:'Use existing paths',run:()=>{if(selected.id!==job.id)return;(setup.accepted_substitutions||={})[ink.id]=substitution(ink,slot,pen);renderPens();saveSetup();}}];
      if(pen.width_mm!=null)actions.push({label:'Rebuild upstream',run:()=>rebuildForPen(ink,slot,pen)});
      reviews.push({id:ink.id,message:`Ink ${ink.id} → slot ${slot}: ${difference(ink,pen)}`,actions});
    }
  }
  renderInkSets($('pens'),{inks:job.summary.pens,carousel:carousel.value,mapping:setup.pen_map,title:'Document pens',reviews,reviewHost:$('pen-reviews'),toolbarHost:$('pen-arrangement'),
    included:setup.pens,
    isAccepted:(ink,slot,pen)=>!job.pdf||accepted(ink,slot,pen,setup.accepted_substitutions?.[ink.id]),onEditSlot:slot=>carousel.edit(slot),
    onAssign:(ids,target)=>{
      if(selected.id!==job.id)return false;
      const pen=carousel.value?.slots[target.slot];
      for(const id of ids){
        setup.pen_map[id]=target.slot;
        const ink=job.summary.pens.find(i=>i.id===id);
        // An explicit destination choice accepts this exact loaded material.
        // A later inventory change still invalidates the frozen acceptance.
        if(job.pdf&&usable(pen)&&!equivalent(ink,pen))(setup.accepted_substitutions||={})[id]=substitution(ink,target.slot,pen);
        else delete (setup.accepted_substitutions||{})[id];
      }
      renderPens();saveSetup();queueMicrotask(()=>revealInk($('pens'),ids[0]));return true;
    }});
  if(job.calibration)for(const button of $("pens").querySelectorAll(".pen-stack button,.ink-delta"))button.disabled=true;
  const omitted=job.summary.pens.length-setup.pens.length;
  $('choose-inks').textContent=omitted?`Choose inks… (${omitted} omitted)`:'Choose inks…';
  $('choose-inks').disabled=!!job.calibration;
  updatePens();
}
function chooseInks(){
  if(!selected||selected.calibration)return;
  const job=selected,{dialog,body}=modal('Inks to plot'),form=element('form'),checks=[];
  dialog.id='ink-inclusion-dialog';form.id='ink-inclusion-form';form.className='ink-inclusion-list';
  for(const ink of job.summary.pens){
    const label=element('label'),check=element('input');check.type='checkbox';check.checked=setup.pens.includes(ink.id);check.setAttribute('aria-label',`Include ink ${ink.id}`);
    label.append(check,cap(ink,null),element('span',ink.label||`Ink ${ink.id} · ${colorName(ink.color_srgb||ink.color)}`));form.append(label);checks.push([ink.id,check]);
  }
  const actions=element('div',null,'ink-dialog-actions'),apply=button('Apply',null,'ink-action ink-confirm');apply.type='submit';apply.setAttribute('form',form.id);
  actions.append(button('Cancel',()=>dialog.close()),apply);body.append(form);dialog.append(actions);
  form.onsubmit=event=>{event.preventDefault();if(selected===job){setup.pens=checks.filter(([,check])=>check.checked).map(([id])=>id);renderPens();saveSetup();}dialog.close();};
}
async function rebuildForPen(ink,slot,pen) {
  const job=selected,application=authoringByConversion.get(selected.pdf?.conversion_id)?.application,destination=application==='map-studio'?'map':application==='pattern-studio'?'patterns':application==='maze-studio'?'mazes':'pdf';openStudio(destination);
  try {
    const conversion=await api(`/api/v1/conversions/${job.pdf.conversion_id}`).then(r=>r.json()),intent=conversion.intent;
    const ids=intent.palette.inks.filter(i=>equivalent(i,ink)).map(i=>i.id);
    if(!ids.length)throw Error('The prepared ink was not found in its saved conversion. Open PDF Studio to review it.');
    const changes=Object.fromEntries(ids.map(id=>[id,{color_srgb:pen.color,width_mm:pen.width_mm,label:pen.label||colorName(pen.color),source:{kind:'plotline-carousel',revision:carousel.value.revision,slot}}]));
    let url;
    if(intent.authoring?.application==='map-studio') {
      url=new URL(mapSourceURL(intent.authoring,{snapshot:true}));
      for(const [source,id] of Object.entries(intent.ink_mapping||{}))if(changes[id])changes[source]=changes[id];
    }else if(['pattern-studio','maze-studio'].includes(intent.authoring?.application)){
      url=new URL(intent.authoring.application==='maze-studio'?'/mazes':'/patterns',location.origin);url.searchParams.set('source_recipe',intent.authoring.recipe_hash);url.searchParams.set('snapshot','1');
      for(const [source,id] of Object.entries(intent.ink_mapping||{}))if(changes[id])changes[source]=changes[id];
    }else url=new URL(`/pdf?document=${job.pdf.document_id}&conversion=${job.pdf.conversion_id}&page=${job.pdf.page_index}`,location.origin);
    if(!['http:','https:'].includes(url.protocol))throw Error('Invalid authoring URL');
    url.searchParams.set('pen_substitutions',JSON.stringify(changes));
    openStudio(destination,url.href);
  }catch(error){notice(error.message);}
}
let livePaperReading=false;
function renderDeviceReading(live=livePaperReading){
  livePaperReading=live;
  if(observation)$('device-reading').textContent=`${live?'':'Saved reading · '}${observation.identity} drawable area ${paperSize([observation.limits[2]-observation.limits[0],observation.limits[3]-observation.limits[1]].map(n=>n/observation.units_per_mm))} · measured ${new Date(observation.observed_at).toLocaleString()}`;
}
function paperLabels(){
  for(const option of $('paper-preset').options){
    if(option.value==='custom')continue;
    option.dataset.name??=option.textContent.trim().split(' · ')[0];
    option.textContent=option.dataset.name+' · '+paperSize(option.value.split(',').map(Number).sort((a,b)=>a-b));
  }
  $('margin-hint').textContent=`Normal-mode margins from the HP manual: ${paperLength(39)} at the back, ${paperLength(15)} elsewhere. Confirm the sheet and margins at calibration.`;
  renderDeviceReading();
}
onPaperUnitChange(()=>{paperLabels();if(setup){paperControls(false);updatePlacement();}});
paperLabels();
function paperControls(refreshFields=true) {
  $("paper-mode").value = setup.window;
  $('view-facing').setAttribute('aria-pressed',String(setup.facing==='plotter'));
  if(refreshFields){
    writePaperField($("paper-x"),setup.paper_x_mm);
    writePaperField($("paper-y"),setup.paper_y_mm);
    ["back", "front", "left", "right"].forEach(
      (n, i) => writePaperField($(`margin-${n}`),setup.margins_mm[i]),
    );
  }
  // A paper size has the same name with either edge along the feed direction.
  const orientations = [
    `${setup.paper_x_mm},${setup.paper_y_mm}`,
    `${setup.paper_y_mm},${setup.paper_x_mm}`,
  ];
  const preset = [...$("paper-preset").options].find((o) =>
    orientations.includes(o.value),
  );
  $("paper-preset").value = preset?.value ?? "custom";
  for (const id of [
    "paper-preset",
    "swap-feed-edge",
    "paper-x",
    "paper-y",
    "margin-back",
    "margin-front",
    "margin-left",
    "margin-right",
  ])
    $(id).disabled = setup.window !== "planned" || !!setup.registration;
  $("paper-mode").disabled=!!setup.registration;
  $("turn-sheet").disabled=!!setup.registration;
  $("paper-controls").hidden=!!setup.registration;
  $("paper-requirement").hidden=!setup.registration;
  if(setup.registration)$("paper-requirement").textContent=`Load the planned ${paperSize([setup.paper_x_mm,setup.paper_y_mm])} sheet, oriented as shown. Artwork size and placement are already set.`;
}
function changeSetup(refit = true) {
  revision++;
  paperControls();
  scene();
  updatePlacement();
  if (refit) fit();
  else updateView();
  saveSetup();
}
async function loadEntry(entry) {
  const job=entry.job;
  const request = ++generation;
  worker?.terminate();
  worker = null;
  ready = false;
  busy = false;
  rendered = null;
  clearTimeout(renderTimer);
  selected = job;
  setup = null;view = null;base = null;
  plotting.update('Opening preview…');
  renderVersion();
  studioNav.setSources(sourceLinks({pdf:job.pdf}));
  if(job.pdf){
    const id=job.pdf.conversion_id;
    try{if(!authoringByConversion.has(id))authoringByConversion.set(id,(await api(`/api/v1/conversions/${id}`).then(r=>r.json())).intent?.authoring);
      if(request!==generation)return;studioNav.setSources(sourceLinks({pdf:job.pdf,authoring:authoringByConversion.get(id)}));
    }catch{if(request===generation)notice('The source conversion is unavailable.');}
  }
  setup = null;
  view = null;
  base = null;
  revision++;
  const canvas = $("preview-canvas");
  canvas.width = 1;
  canvas.height = 1;
  canvas.style.transform = "none";
  delete canvas.dataset.ready;
  $("paper-scene").replaceChildren();
  $("preview-loading").hidden = false;
  $("render-status").textContent = "Opening preview…";

  notice("");
  $("empty").hidden = true;
  $('viewport').hidden=false;
  $('travel-toggle').checked=entry.view?.travel||false;
  $('preview-ink-colors').value=entry.view?.appearance||(job.calibration?'design':'measured');
  $("pdf-edit").hidden=!job.pdf;
  $("calibration-open").hidden=!job.calibration;
  if(job.calibration)$("calibration-open").href="/calibration#"+job.calibration.id;
  $('loaded-paper').open=!job.registration&&!job.pdf&&!job.calibration;
  if (job.pdf) $("pdf-edit").href=`/pdf?document=${job.pdf.document_id}&conversion=${job.pdf.conversion_id}&page=${job.pdf.page_index}`;
  $("job-subtitle").textContent =
    `${job.name} · ${(job.bytes / 1024).toFixed(0)} KB · Added ${new Date(job.created_at * 1000).toLocaleDateString()}`;
  $("download").href = `/api/jobs/${job.id}/source.hpgl`;
  $("download").download = job.name;
  $("down-distance").textContent = distance(job.summary.pen_down_mm);
  $("up-distance").textContent = distance(job.summary.pen_up_mm);
  $("path-count").textContent = `${number(job.summary.paths)} drawn paths`;
  $("pen-count").textContent = `${job.summary.pens.length} inks`;
  $("notes").replaceChildren();
  for (const text of job.summary.notes) {
    const li = document.createElement("li");
    li.textContent = text;
    $("notes").append(li);
  }
  $("file-info").replaceChildren();
  for (const [label, value] of [
    ["Source SHA-256", job.sha256],
    ["Coordinate scale", `${job.units_per_mm} units/mm`],
    ["Commands", number(job.summary.commands)],
    ["Y travel (source axes)", distance(job.summary.y_travel_mm)],
  ]) {
    const dt = document.createElement("dt"),
      dd = document.createElement("dd");
    dt.textContent = label;
    dd.textContent = value;
    $("file-info").append(dt, dd);
  }
  try {
    if (request !== generation) return;
    setup = structuredClone(entry.recipe);
    setup.facing=entry.view?.facing||'artwork';
    setup.carousel_revision = carousel.value.revision;
    paperControls();
    renderPens();
    fit();
    if(entry.view?.navigation)navigation.set(entry.view.navigation);
    if (!window.Worker || !window.OffscreenCanvas)
      throw new Error(
        "This preview needs a browser with OffscreenCanvas support.",
      );
    worker = new Worker("/render-worker.js", { type: "module" });
    worker.onmessage = ({ data }) => {
      if (request !== generation) {
        data.bitmap?.close();
        return;
      }
      if (data.type === "ready") {
        ready = true;
        requestRender();
      } else if (data.type === "frame") {
        busy = false;
        if (!viewReady()) {data.bitmap.close();return;}
        if (data.spec.revision === revision) {
          canvas.width = data.bitmap.width;
          canvas.height = data.bitmap.height;
          canvas.style.width = `${data.spec.width}px`;
          canvas.style.height = `${data.spec.height}px`;
          canvas
            .getContext("bitmaprenderer")
            .transferFromImageBitmap(data.bitmap);
          rendered = { m: data.m, spec: data.spec };
          const now = new DOMMatrix(
            matrix(
              view,
              $("viewport").clientWidth,
              setup.rotation,
              setup.facing,
            ),
          );
          canvas.style.transform = now
            .multiply(new DOMMatrix(data.m).inverse())
            .toString();
          $("preview-loading").hidden = true;
          $("render-status").textContent = "Ready";
          canvas.dataset.ready = "true";
          canvas.dataset.revision = revision;
          canvas.dataset.renderMs = data.ms.toFixed(1);
          canvas.dataset.pens = data.spec.pens.join(",");
          canvas.dataset.colors = JSON.stringify(data.spec.colors);
          canvas.dataset.travel = String(data.spec.travel);
          canvas.dataset.rotation = String(data.spec.rotation);
          canvas.dataset.facing = data.spec.facing;
          canvas.dataset.widths = JSON.stringify(data.spec.widths);
          window.dispatchEvent(new Event("plotline-rendered"));
        } else data.bitmap.close();
        const latest = spec();
        if (JSON.stringify(latest) !== JSON.stringify(data.spec)) {
          clearTimeout(renderTimer);
          renderTimer = setTimeout(
            requestRender,
            Math.max(0, 140 - (performance.now() - lastInput)),
          );
        }
      } else if (data.type === "error") {
        busy = false;
        $("preview-loading").hidden = true;
        notice(data.message);
        $("render-status").textContent = "Preview unavailable";
      }
    };
    worker.onerror = (e) => {
      if (request !== generation) return;
      busy = false;
      $("preview-loading").hidden = true;
      notice(e.message || "Preview worker failed");
    };
    worker.postMessage({
      type: "load",
      job,
      url: `/api/jobs/${job.id}/preview.svg`,
    });
  } catch (e) {
    if (request === generation) {
      $("preview-loading").hidden = true;
      notice(e.message);
    }
  }
}
function clearDocument(){
  generation++;worker?.terminate();worker=null;selected=null;setup=null;view=null;base=null;ready=false;busy=false;rendered=null;
  clearTimeout(renderTimer);$('preview-canvas').width=1;$('paper-scene').replaceChildren();$('pens')._inkTray?.dispose();$('pens').replaceChildren();$('choose-inks').disabled=true;
  $('empty').hidden=false;$('viewport').hidden=true;$('preview-loading').hidden=true;$('export-plan').disabled=true;
  $('download').removeAttribute('href');$('pdf-edit').hidden=true;$('calibration-open').hidden=true;renderVersion();
  studioNav.setSources([]);$('pen-readiness').textContent='Open a document to assign its pens.';$('drawer-warning').textContent='';plotting.update('');
}
function openDrawer(pane){
  drawerPane=pane;$('ink-drawer').hidden=!pane;
  $('pen-review-panel').hidden=pane!=='pens';
  $('pens-pane').hidden=pane!=='pens';$('plotter-pane').hidden=pane!=='plotter';
  $('ink-drawer').setAttribute('aria-label',pane==='plotter'?'Plotter & paper':'Pens & mapping');
  for(const name of ['pens','plotter'])$('show-'+name).setAttribute('aria-expanded',String(name===pane));
}
$('show-pens').onclick=()=>openDrawer(drawerPane==='pens'?null:'pens');
$('show-plotter').onclick=()=>openDrawer(drawerPane==='plotter'?null:'plotter');
$('choose-inks').onclick=chooseInks;
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&!document.querySelector('dialog[open],.tray[open]')&&!$('pens').classList.contains('ink-selecting'))openDrawer(null);});
wireStudioTrays();
function plotState({execution,submission,issue,previewIssue,message,failed,active,ownsActive,statusError}){
  const completed=execution&&['finished','reset_acknowledged'].includes(execution.state)&&submission?.job_id===selected?.id;
  const otherPlot=active&&!ownsActive;
  $('work-detail').textContent=ownsActive?message:otherPlot?(previewIssue||'Preview only · another document is plotting'):issue||((completed||failed)?message:setup?'Ready to plot':'Open an HPGL document');
  $('work-detail').title=$('work-detail').textContent;
  $('work-detail').classList.toggle('invalid',!active&&statusError);
  $('show-active-plot').hidden=!otherPlot;
  $('show-active-plot').title=otherPlot?`${submission?.name||'Current plot'} · ${message}`:'';
  workspace.setPlot(active&&submission?{...submission,state:execution?.state||submission.phase,message}:null).catch(e=>notice(e.message));
  if(ownsActive&&submission?.id!==plotAttempt){plotAttempt=submission?.id;openDrawer('plotter');}
}
$('show-active-plot').onclick=async()=>{try{await workspace.focusPlot();openDrawer('plotter');}catch(e){notice(e.message);}};
$("refresh").onclick = () => refresh().catch((e) => notice(e.message));
$("turn-sheet").onclick = () => {
  if (setup) {
    setup.rotation = (setup.rotation + 90) % 360;
    changeSetup();
  }
};
$("travel-toggle").onchange = () => {
  revision++;
  updateView();
};
$("paper-mode").onchange = () => {
  if (!setup) return;
  setup.window = $("paper-mode").value;
  changeSetup();
};
$('view-facing').onclick=()=>{if(!setup)return;setup.facing=setup.facing==='artwork'?'plotter':'artwork';revision++;paperControls();fit();workspace.capture();};
$("paper-preset").onchange = () => {
  if (!setup) return;
  if ($("paper-preset").value === "custom") {
    $("paper-details").open = true;
    return;
  }
  [setup.paper_x_mm, setup.paper_y_mm] = $("paper-preset")
    .value.split(",")
    .map(Number);
  changeSetup();
};
$("swap-feed-edge").onclick = () => {
  if (!setup) return;
  [setup.paper_x_mm, setup.paper_y_mm] = [setup.paper_y_mm, setup.paper_x_mm];
  changeSetup();
};
for (const id of [
  "paper-x",
  "paper-y",
  "margin-back",
  "margin-front",
  "margin-left",
  "margin-right",
])
  $(id).onchange = () => {
    if (!setup) return;
    if (!$(id).checkValidity() || !$(id).value) {
      $(id).reportValidity();
      return;
    }
    setup.paper_x_mm = readPaperField($("paper-x"));
    setup.paper_y_mm = readPaperField($("paper-y"));
    setup.margins_mm = ["back", "front", "left", "right"].map((n) =>
      readPaperField($(`margin-${n}`)),
    );
    changeSetup();
  };
$("export-plan").onclick = async () => {
  if (!setup || !selected) return;
  exporting = true;
  updatePlacement();
  $("export-plan").textContent = "Preparing…";
  notice("");
  const id = selected.id,
    name = selected.name.replace(/\.(hpgl|plt)$/i, ""),
    value = structuredClone(setup);
  try {
    const response = await api(`/api/jobs/${id}/prepared.hpgl`, {
      method: "POST",
      ...writeOptions(value),
    });
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}-prepared.hpgl`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  } catch (e) {
    notice(e.message);
  } finally {
    exporting = false;
    $("export-plan").textContent = "Prepared HPGL ↓";
    updatePlacement();
  }
};
const navigation=previewNavigation($("viewport"),{
  enabled:()=>Boolean(base&&setup),onFit:fit,
  onChange:({scale,x,y})=>{
    if(!base)return;
    const w=base[2]/scale,h=base[3]/scale;
    view=[base[0]+(base[2]-w)/2-x*w/$("viewport").clientWidth,
          base[1]+(base[3]-h)/2-y*h/$("viewport").clientHeight,w,h];
    updateView();
  }
});
new ResizeObserver(() => {
  if (setup) fit();
}).observe($("viewport"));
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    flushSave();
    navigation.cancel();
    clearTimeout(renderTimer);
  } else if (setup) {
    updateView();
  }
});
function openUpload() {
  $("upload-error").hidden = true;
  $("upload-dialog").showModal();
}
$('add-hpgl').onclick=()=>{$('library').close();openUpload();};
$("upload-open").onclick = openUpload;
$("empty-upload").onclick = openUpload;
$("upload-close").onclick = () => $("upload-dialog").close();
$("upload-form").onsubmit = async (event) => {
  event.preventDefault();
  const files = [...$("file-input").files];
  if (!files.length) return;
  $("upload-submit").disabled = true;
  $("upload-close").disabled = true;
  $("upload-error").hidden = true;
  let last = null;
  try {
    for (const [i, file] of files.entries()) {
      if (file.size > 64 * 1024 * 1024)
        throw new Error(`${file.name} exceeds 64 MiB.`);
      $("upload-progress").textContent = `Reading ${i + 1} of ${files.length}…`;
      const query = new URLSearchParams({
        name: file.name,
        units_per_mm: $("scale").value,
      });
      const response = await api(`/api/jobs?${query}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Plotline-Upload": "1",
        },
        body: file,
      });
      last = (await response.json()).id;
    }
    $("upload-dialog").close();
    $("file-input").value = "";
    await refresh(last);
  } catch (error) {
    $("upload-error").textContent = error.message;
    $("upload-error").hidden = false;
    if (last) await refresh(last).catch((e) => notice(e.message));
  } finally {
    $("upload-submit").disabled = false;
    $("upload-close").disabled = false;
    $("upload-progress").textContent = "";
  }
};
$("upload-dialog").addEventListener("cancel", (event) => {
  if ($("upload-submit").disabled) event.preventDefault();
});
try {
  const [status, device] = await Promise.all([
    api("/api/status").then((r) => r.json()),
    api("/api/device").then((r) => r.json()),
    carousel.refresh(),
  ]);
  observation = device.observation;
  if (observation) {
    $("paper-mode").querySelector('[value="reported"]').disabled = false;
    renderDeviceReading(false);
  }
  $("connection").textContent = `Dev service connected · v${status.version}`;
  await refresh();
  await workspace.start();studioNav.ready();
  plotting.poll();
} catch (e) {
  $("connection").textContent = "Service unavailable";
  notice(e.message);
}
