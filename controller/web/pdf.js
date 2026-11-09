import {bindPaperUnits,onPaperUnitChange,paperSize,paperLength,readPaperField,writePaperField} from '/paper-units.js';
import {studioNavigation,openStudio,sourceLinks} from '/studio-navigation.js';
import {usablePen,outputSlot,entrySources,resolveRecipePens,slotMaterial,unavailableSlots,recipeKey,screenPenChoices,rasterRoles,rasterRoleLabel,rasterAssignmentIssue} from '/pdf-pens.js';
import {renderPreviewControls,migrateStudioStorage,stable,renderDocumentTabs,renderDocumentLibrary,chooseDocumentName,confirmDocumentClose,editHistory,renderStudioProgress,wireStudioTrays,studioNotice as notice} from '/studio.js';
import {previewNavigation} from '/preview-navigation.js';
import {sheetAngle,platenWidth,fitsPlaten,loadSheet,turnSheet} from '/pdf-paper.js';
import {replaceSourceRecipe} from '/pdf-recipe.js';
import {revealInk,renderInkSets,nearest,equivalent,colorName,editLoadedPen,newInkId} from '/inksets.js';
for(const storage of [sessionStorage,localStorage])migrateStudioStorage(storage,'plotline-pdf-workspace');
let carousel=null,inkMapping={},stylePages=[],rasterInks={},rasterScreenInks=[];
const $ = (id) => document.getElementById(id);
renderPreviewControls($('preview-tools'),{onFit:()=>navigation.reset(),onZoom:factor=>navigation.zoomBy(factor)});
const clone = (value) => structuredClone(value);
let caps,
  doc,
  current,
  inks = [],
  poll,
  selectionToken = 0,
  posting = false,
  mediaSnapshot = null,
  paperOffsetsMm = [0, 0];
let updatingPlot = null;
bindPaperUnits();
const defaults = () => [
  { id: "black", label: "Black", color_srgb: "#000000", width_mm: 0.3 },
  { id: "red", label: "Red", color_srgb: "#ff0000", width_mm: 0.3 },
  { id: "green", label: "Green", color_srgb: "#008000", width_mm: 0.3 },
];
const defaultPolicies = {
  fill: { mode: "crosshatch", spacing_mm: 2, angle_deg: 45 },
  stroke_width_tolerance: 0.75,
  thin_detail_thickening: 0.75,
  stroke_width_mode: "pen",
  raster: "reject",
  gradients: "reject",
  transparency: "reject",
  blend_modes: "reject",
  soft_masks: "reject",
};
const number = (id) => $(id).hasAttribute('data-paper-length')?readPaperField($(id)):Number($(id).value);

function node(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "X-Plotline-Request": "1", ...options.headers },
  });
  const value = await response.json();
  if (!response.ok) throw Object.assign(Error(value.error || response.statusText),{status:response.status});
  return value;
}
function sourcePage() {
  return doc?.inspection?.pages.find((p) => p.page_index === number("page"));
}
const sizeText=paperSize;
function showOffsets(offsets = paperOffsetsMm) {
  paperOffsetsMm = [...offsets];
  ["x", "y"].forEach((axis, i) => writePaperField($(`offset-${axis}`),offsets[i]));
}
function mediaLabel(profile) {
  const name =
    profile.id === "pdf-page"
      ? "PDF page"
      : profile.calibration?.device || (profile.id?.startsWith('hp7585-')?'HP7585':profile.label?.replace(/(?:\d+(?:\.\d+)?\s*[×x]\s*)?\d+(?:\.\d+)?\s*(?:mm|in(?:ches)?)\b/g,'').trim()) || 'Sheet';
  return `${name} · ${sizeText(profile.sheet_size_mm)}`;
}
function paperLabels() {
  if (!doc?.inspection) return;
  for (const option of $("page").options) {
    const p = doc.inspection.pages.find(
      (p) => p.page_index === Number(option.value),
    );
    option.textContent = `${p.page_index + 1} · ${sizeText(p.source_size_mm)}`;
  }
  for (const option of $("media").options) {
    option.textContent =
      option.value === "source"
        ? `PDF page · ${sizeText(sourcePage().source_size_mm)}`
        : mediaLabel(
            option.value === "intent"
              ? mediaSnapshot
              : caps.media_profiles[Number(option.value)],
          );
  }
  paperSummary();
}
function paperSummary() {
  const source = sourcePage();
  if (!source) return;
  const target = media(),
    scale = number("scale");
  let size = [...source.source_size_mm];
  if (number("rotation") % 180) size.reverse();
  const placed = size.map((v) => v * scale);
  const sheet = target?.sheet_size_mm || source.source_size_mm;
  const printable = target?.printable_rect_mm.slice(2) || sheet;
  $('turn-sheet').disabled=!target;
  $('sheet-loading').replaceChildren();
  if(target)for(const angle of [0,90,180,270]){
    const width=platenWidth(target,angle),valid=fitsPlaten(width);
    const option=new Option(`${sizeText([width])} across · wide margin ${['left','top','right','bottom'][angle/90]}${valid?'':width<279.4?' · too narrow':' · too wide'}`,String(angle));
    option.disabled=!valid;$('sheet-loading').add(option);
  }
  $('sheet-loading').disabled=!target;
  if(target)$('sheet-loading').value=String(sheetAngle(target));
  const issue=paperLoadingIssue();
  $('sheet-loading-issue').textContent=issue;$('sheet-loading-issue').hidden=!issue;
  $("paper-summary").textContent =
    `Placed PDF page: ${sizeText(placed)}\nSheet: ${sizeText(sheet)} · printable: ${sizeText(printable)}`;
  $("source-size-note").textContent =
    `Scale 1× means the received ${sizeText(source.source_size_mm)} PDF. Fit computes an additional scale to the printable area.`;
  if(doc.inspection?.image_input){
    const input=doc.inspection.image_input;
    $('source-size-note').textContent=`Original image: ${input.pixels.join(' × ')} pixels. Scale 1× uses ${input.resolution_ppi.map(v=>Math.round(v)).join(' × ')} pixels/inch (${sizeText(source.source_size_mm)}). Fit places it inside the selected sheet’s printable area.`;
  }
  if(doc.inspection?.svg_input){
    $('source-size-note').textContent=`Original SVG page: ${sizeText(source.source_size_mm)}. Scale 1× preserves that page size. ${doc.inspection.svg_input.notes.join(' ')}`;
  }
  const requested = [
    ...new Map(
      (doc.captures || [])
        .filter((c) => c.requested_size_mm)
        .map((c) => [JSON.stringify(c.requested_size_mm), c.requested_size_mm]),
    ).values(),
  ];
  const differs = requested.some((size) =>
    size.some((v, i) => Math.abs(v - source.source_size_mm[i]) > 0.1),
  );
  $("capture-note").hidden = !differs;
  $("capture-note").textContent = differs
    ? `Matching desktop print ticket${requested.length > 1 ? "s" : ""}: ${requested.map(sizeText).join("; ")}. Received PDF: ${sizeText(source.source_size_mm)}. Scale below is relative to the received PDF; it does not include any scaling done before capture.`
    : "";
}
function paperLoadingIssue(){
  const target=doc&&sourcePage()?media():null;
  return target&&!fitsPlaten(platenWidth(target))?`HP7585 needs ${paperLength(279.4)} to ${paperLength(914.4)} across the platen. Choose a supported loading or a different sheet.`:'';
}
function readIntent() {
  const index = number("page"),
    source = sourcePage();
  const base = activeEntry?.recipe || doc.intent;
  const originalPage = base?.pages.find((p) => p.page_index === index);
  const selection = {
    page_index: index,
    source_size_mm: originalPage?.source_size_mm || source.source_size_mm,
    placement: {
      scale: number("scale"),
      rotation_deg: number("rotation"),
      offset_mm: [...paperOffsetsMm],
    },
  };
  const target = media();
  if (target) selection.target_media = target;
  const policies = {
    fill: { mode: $("fill").value },
    stroke_width_tolerance: number("tolerance"),
    soft_masks: "reject",
  };
  if($("stroke-mode").value==='pen')policies.stroke_width_mode='pen';
  if(number('thin-detail')>0)policies.thin_detail_thickening=number('thin-detail');
  if (["hatch", "crosshatch", "feathered"].includes(policies.fill.mode))
    Object.assign(policies.fill, {
      spacing_mm: number("spacing"),
      angle_deg: number("angle"),
    });
  if (policies.fill.mode === "feathered")
    policies.fill.feather_distance_mm = number("feather");
  for (const key of ["raster", "gradients", "transparency", "blend_modes"])
    policies[key] = $(key).value;
  if(policies.raster==='dither') policies.raster_settings={
    screening:$('raster-screening').checked,palette:$('raster-palette').value,
    fullness:number('raster-fullness')/100,edge_falloff_mm:number('raster-falloff'),
    black_treatment:$('raster-black').value,inks:clone(rasterInks),
    ...($('raster-palette').value==='selected'?{screen_inks:clone(rasterScreenInks)}:{})
  };
  return {
    schema: "plotline.print-intent/1",
    source_sha256: doc.source_sha256,
    title: base?.title || doc.name,
    pages: [selection],
    palette: {
      schema: "plotline.palette/1",
      id: "document-palette",
      label: "Document inks",
      inks: clone(inks),
    },
    bindings: base?.bindings || [],
    ...(Object.keys(inkMapping).length?{ink_mapping:clone(inkMapping)}:{}),
    ...(base?.authoring?{authoring:clone(base.authoring)}:{}),
    policies,
  };
}
function previewKey(intent, index) {
  const page = intent?.pages.find((p) => p.page_index === index);
  if (!page) return null;
  const media = page.target_media || {
    sheet_size_mm: page.source_size_mm,
    printable_rect_mm: [0, 0, ...page.source_size_mm],
    content_inset_mm: 0,
  };
  const policies = clone(intent.policies || defaultPolicies);
  policies.stroke_width_mode ||= "preserve";
  policies.thin_detail_thickening ||= 0;
  const fill = policies.fill;
  if (!["hatch", "crosshatch", "feathered"].includes(fill.mode)) {
    delete fill.spacing_mm;
    delete fill.angle_deg;
  }
  if (fill.mode !== "feathered") delete fill.feather_distance_mm;
  // Palette names and inventory provenance do not alter this preview. Ink IDs,
  // labels, widths, colors and bindings do. Compare only the selected page.
  const value = {
    source: intent.source_sha256,
    page: index,
    placement: page.placement,
    media: {
      sheet: media.sheet_size_mm,
      printable: media.printable_rect_mm,
      inset: media.content_inset_mm,
      calibration: media.calibration,
    },
    inks: intent.palette?.inks.map(({ source, ...ink }) => ink),
    bindings: intent.bindings || [],
    ink_mapping: intent.ink_mapping || {},
    policies,
  };
  function canonical(v) {
    if (typeof v === "number") return Number(v.toFixed(9));
    if (Array.isArray(v)) return v.map(canonical);
    if (v && typeof v === "object")
      return Object.fromEntries(
        Object.keys(v)
          .sort()
          .map((k) => [k, canonical(v[k])]),
      );
    return v;
  }
  return JSON.stringify(canonical(value));
}
function updateGeneration() {
  let same = false;
  if (doc?.status === "ready" && sourcePage()) {
    same =
      !!current &&
      previewKey(readIntent(), number("page")) ===
        previewKey(current.intent, number("page"));
  }
  const running = current && ["queued", "running"].includes(current.status);
  const noScreenPens=$('raster').value==='dither'&&$('raster-screening').checked&&$('raster-palette').value==='selected'&&!rasterScreenInks.length;
  const missingScreenPens=screenIssues().length>0;
  const assignmentIssue=rasterIssue();
  const paperIssue=paperLoadingIssue();
  const missingPens=penIssues().length>0||noScreenPens||missingScreenPens||!!assignmentIssue||!!paperIssue;
  const matched = same && (current.status === "ready" || running);
  const lossPage=current?.result?.pages?.find(p=>p.page_index===number('page'));
  const expired=matched&&lossPage?.preview_available===false;
  const oldPreview=matched&&current.status==='ready'&&(!lossPage?.artifacts?.detail_loss||(lossPage.detail_loss?.version||0)<2);
  const prepared=preparedPage(),job=prepared?.job_id?prepared:null;
  const needsPreparation=matched&&!running&&!prepared;
  $("convert-button").disabled = missingPens || posting || doc?.status !== "ready" || (matched&&!oldPreview&&!expired&&(!needsPreparation||!!updatingPlot));
  $("convert-button").title=paperIssue||assignmentIssue||'';
  $("convert-button").textContent =
    posting || (running && same) || (!!updatingPlot && same)
      ? "Generating pen preview…"
      : expired ? 'Rebuild preview' : oldPreview ? 'Update pen preview' : needsPreparation&&activeEntry?.preparationError ? 'Retry plot preparation' : "Preview pen strokes";
  $("update-plot").disabled=missingPens || posting || !!updatingPlot || !job;
  $("update-plot").textContent="Open plot ↗";
  $("update-plot").title=job?'Open these pen strokes in HPGL Studio':prepared?.status==='empty'?'This page has no drawable pen paths.':updatingPlot?'Finishing the pen preview and registering its plot.':'Preview the current pen settings first.';
  const previewReady=matched&&!running&&!oldPreview&&!expired&&!missingPens&&!!prepared;
  $("convert-button").classList.toggle("primary",doc?.status==="ready"&&!previewReady&&!missingPens);
  $("update-plot").classList.toggle("primary",!!job&&!$("update-plot").disabled);
  renderProgress();
  $("dirty").textContent = paperIssue || assignmentIssue || (missingScreenPens ? 'Remove unavailable screening inks or choose Use loaded pens.' : noScreenPens ? 'Choose at least one screening pen in Images & effects.' : posting
    ? "Submitting these settings…"
    : running && same
      ? "Generating a preview for these settings."
      : expired ? 'Preview expired. Rebuild a new revision from the saved PDF and settings; the original HPGL is retained.' : oldPreview ? 'Update this preview for focused loss highlighting.' : matched
        ? "Preview matches these settings."
        : current
          ? "Settings changed. Generate to update the preview."
          : "Preview the pen strokes to create a plot with these choices.");
}
function dirty(event) {
  if(event?.target?.id==='raster' && rasterDecisionFailure(current) && $('raster').value!=='reject')notice();
  captureRecipe(event);paperSummary();updateGeneration();documentState();
}

function sourceInks() {
  if(doc?.intent?.palette)return doc.intent.palette.inks;
  return (stylePages.find(p=>p.page_index===number('page'))?.source_styles||sourcePage()?.source_styles||[]).map(i=>({...i,label:`${colorName(i.color_srgb)} ${i.kind==='fill'?'fill':i.width_mm.toPrecision(5)+' mm'}`}));
}
function mappedInk(source) {return inks.find(i=>i.id===(inkMapping[source.id]||source.id))||nearest(source,inks);}
function initializeMapping(intent) {
  inkMapping=clone(intent?.ink_mapping||{});
  if(!doc?.intent?.palette)for(const page of stylePages)for(const ink of page.source_styles||[])if(!inkMapping[ink.id]&&inks.length)inkMapping[ink.id]=nearest(ink,inks).id;
}
function mappedSlots() {
  return Object.fromEntries(sourceInks().map(source=>[source.id,outputSlot(mappedInk(source),carousel)]));
}
function renderInks() {
  if(!doc)return;
  renderRasterPens();
  const documentId=doc.id,sources=sourceInks(),mapping=mappedSlots();
  renderInkSets($('inks'),{inks:sources,carousel,mapping,allowOrder:false,title:'Source document inks',acceptedLabel:'mapped',isAccepted:(source,slot,pen)=>slot>0&&usablePen(pen)&&equivalent(mappedInk(source),pen),
    onAssign:(ids,target)=>doc.id===documentId?assignPdfPens(ids,target.slot):false,
    onEditSlot:editPhysicalPen,
    targetIssue:t=>t.ink?.state!=='loaded'?'This slot is empty or unavailable.':t.ink.width_mm==null?'Record the measured diameter using Edit before converting.':t.ink.color?.toLowerCase()==='#ffffff'?'White clears paper; choose a colored pen.':'',
    onRefresh:()=>refreshCarousel().catch(e=>notice(e.message))
  });
  const summary=$('ink-summary'),colors=new Map();
  for(const ink of sources){const color=ink.color_srgb.toLowerCase();if(!colors.has(color))colors.set(color,ink);if(colors.size>8)break;}
  summary.replaceChildren();
  for(const [color,ink] of [...colors].slice(0,8)){
    const swatch=node('span','','ink-swatch');swatch.style.backgroundColor=color;swatch.title=ink.label||colorName(color);
    swatch.setAttribute('role','img');swatch.setAttribute('aria-label',`Document color: ${colorName(color)}`);summary.append(swatch);
  }
  if(colors.size>8){const more=node('span','…','ink-overflow');more.title='More document colors';more.setAttribute('aria-label',more.title);summary.append(more);}
  $('map-automatically').disabled=!sources.length||!availablePens().length;
  $('map-automatically').title=availablePens().length?'Choose the closest loaded color, then the closest measured diameter':'Record a loaded colored pen with a measured diameter first.';
  $('ink-note').textContent=penIssues().length?`Slot${penIssues().length===1?'':'s'} ${penIssues().join(', ')}: load a colored pen and record its diameter, or remap these inks.`:`${sources.length} source inks. Select an ink, then a destination pen. Stacks expand to individual inks or Select all.`;
}
function bindPdfOutput(ids,ink) {
  let target=inks.find(i=>equivalent(i,ink)&&i.source?.slot===ink.source?.slot);
  if(target)target.source=clone(ink.source);
  if(!target){target={...ink,id:newInkId('output')};inks.push(target);}
  for(const id of ids)inkMapping[id]=target.id;
  return target;
}
function assignPdfOutput(ids,ink,slot) {
  const target=bindPdfOutput(ids,ink);
  dirty();renderInks();queueMicrotask(()=>revealInk($('inks'),ids[0]));
  notice(`Assigned ${ids.length} source ink${ids.length===1?'':'s'} to ${slot?'slot '+slot+': ':''}${target.label} · ${target.width_mm} mm. Click “Preview pen strokes” to see the result.`);
  return true;
}
function availablePens(){return Object.entries(carousel?.slots||{}).filter(([,p])=>usablePen(p)).map(([slot,p])=>({...p,slot:Number(slot)}));}
function outputPen(pen,slot){return slotMaterial(pen,slot,carousel);}
function mapAutomatically(){
  const pens=availablePens(),sources=sourceInks();if(!pens.length)return;
  for(const source of sources){const pen=nearest(source,pens);bindPdfOutput([source.id],outputPen(pen,pen.slot));}
  dirty();renderInks();notice(`Mapped ${sources.length} source inks to loaded pens by closest color, then closest diameter. Preview pen strokes to see the result.`);
}
function assignPdfPens(ids,slot) {
  const pen=carousel.slots[slot];
  if(pen?.state!=='loaded'||pen.width_mm==null||pen.color?.toLowerCase()==='#ffffff')throw Error('Choose a loaded colored pen with a measured diameter.');
  return assignPdfOutput(ids,outputPen(pen,slot),slot);
}
async function editPhysicalPen(slot) {
  const previous=carousel,pen=previous.slots[slot];
  const updated=await editLoadedPen(pen,slot);
  if(!updated)return;
  const next=clone(previous);next.slots[slot]=updated;
  try{adoptCarousel(await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(next)}));}catch(e){notice(e.message);}
}
function penIssues(){return activeEntry?unavailableSlots(activeEntry.recipe,sourceInks(),carousel):[];}
function screenIssues(){return $('raster').value==='dither'&&$('raster-screening').checked&&$('raster-palette').value==='selected'?screenPenChoices(inks,rasterScreenInks,carousel).missing:[];}
function rasterRoleSettings(){return {screening:$('raster-screening').checked,palette:$('raster-palette').value,black_treatment:$('raster-black').value,inks:rasterInks};}
function rasterIssue(){return $('raster').value==='dither'?rasterAssignmentIssue(rasterRoleSettings(),inks):'';}
function adoptCarousel(next){
  if(carousel&&next.revision<=carousel.revision)return;
  stashEntry();const previous=carousel;carousel=next;
  for(const entry of workspaceDocs){
    const refresh=value=>resolveRecipePens(value,entrySources(entry),carousel,previous);
    entry.recipe=refresh(entry.recipe);entry.history.refresh(refresh);
  }
  if(activeEntry)applyRecipe();remember();documentState();
}
async function refreshCarousel(){adoptCarousel(await api('/api/carousel'));}
function refreshWhenVisible(){if(!document.hidden&&!document.querySelector('dialog[open]'))refreshCarousel().catch(()=>{});}
window.addEventListener('focus',refreshWhenVisible);
document.addEventListener('visibilitychange',refreshWhenVisible);
setInterval(refreshWhenVisible,4000);
const rasterDecisionMessage = 'Raster images is set to "Require a decision". Open Images & effects, choose "Draw with pens", and assign channel pens. You can also choose "Omit", "Reserve blank space", or "Box with an X".';
function rasterDecisionFailure(conversion) {
  return conversion?.status==='unsupported' && conversion.intent?.policies?.raster==='reject' && conversion.error?.includes('raster image');
}
function conversionError(conversion) {
  // Old conversion records remain immutable. Explain their terse legacy error
  // when reopened, while preserving any other unsupported features in the list.
  return (conversion.error||'Conversion needs attention').replace(/(^|; )raster image:\s*(?=;|$)/g,(_,prefix)=>prefix+'raster image: '+rasterDecisionMessage);
}
function rasterControls() {
  const count=sourcePage()?.image_paints||0;
  const needsDecision=count>0&&$('raster').value==='reject';
  $('raster-decision-hint').hidden=!needsDecision;
  $('raster-decision-hint').textContent=needsDecision?`This page contains ${count} raster image${count===1?'':'s'}. Choose Draw with pens and assign channel pens, or choose how to omit or replace the image${count===1?'':'s'}.`:'';
  $('raster-controls').hidden=$('raster').value!=='dither';
  const screening=$('raster-screening').checked;
  $('raster-palette').disabled=!screening;
  $('raster-fullness').disabled=!screening;
  $('raster-fullness-value').textContent=$('raster-fullness').value+'%';
  $('raster-falloff').disabled=!screening||number('raster-fullness')===100;
  const selected=$('raster-palette').value==='selected';
  const settings=rasterRoleSettings(),roles=rasterRoles(settings);
  for(const row of $('raster-inks').children){
    row.hidden=!roles.includes(row.dataset.role);
    row.firstChild.textContent=rasterRoleLabel(row.dataset.role,settings);
    row.querySelector('select').setAttribute('aria-label',rasterRoleLabel(row.dataset.role,settings));
    row.querySelector('select').setAttribute('aria-invalid',String(!row.hidden&&rasterRoles(settings,false).includes(row.dataset.role)&&!inks.some(ink=>ink.id===rasterInks[row.dataset.role])));
  }
  $('raster-assignment-hint').textContent=rasterIssue();$('raster-assignment-hint').hidden=!rasterIssue();
  $('raster-selected').hidden=!screening||!selected;
  $('raster-hint').textContent=selected?'Selected pens use scanned colors when available, otherwise their declared colors, and each pen’s diameter. Black treatment uses its separately chosen pen. Ink mixing remains an approximation.':'Assign pens to channel roles; their names can be anything. Cyan may use turquoise. For mixtures adjusted to scanned colors, choose Selected pens · optimized color.';
}
function renderScreenPens() {
  const container=$('raster-screen-pens');container.replaceChildren();
  const {loaded,missing}=screenPenChoices(inks,rasterScreenInks,carousel);
  for(const {ink,pen,slot,selectedIds} of loaded) {
    const label=node('label',undefined,'screen-pen');
    const box=node('input');box.type='checkbox';box.dataset.ink=ink?.id||'slot:'+slot;
    box.checked=selectedIds.length>0;
    box.disabled=!box.checked&&rasterScreenInks.length>=8;
    const swatch=node('span','','screen-swatch');swatch.style.backgroundColor=pen.measured_color||pen.color;
    swatch.title=pen.measured_color?`Scanned color ${pen.measured_color}`:`Declared color ${pen.color} · no scan applied`;
    label.append(box,swatch,node('span',`Slot ${slot}: ${pen.label||colorName(pen.color)} · ${pen.width_mm} mm`));
    box.onchange=event=>{
      event.stopPropagation();
      let id=ink?.id;
      if(!id){const added={id:newInkId('screen'),...outputPen(pen,slot)};inks.push(added);id=added.id;}
      else if(box.checked)Object.assign(ink,outputPen(pen,slot));
      rasterScreenInks=rasterScreenInks.filter(k=>!selectedIds.includes(k));
      if(box.checked)rasterScreenInks.push(id);
      dirty();renderRasterPens();
    };
    container.append(label);
  }
  const unavailable=$('raster-missing-pens');unavailable.replaceChildren();unavailable.hidden=!missing.length;
  if(missing.length)unavailable.append(node('p','Previously selected inks without a loaded match. Remove these selections or use loaded pens.','small'));
  for(const ink of missing){
    const label=node('label',undefined,'screen-pen'),box=node('input');box.type='checkbox';box.checked=true;box.dataset.ink=ink.id;
    label.append(box,node('span',`${ink.label} · ${ink.width_mm??'?'} mm · not loaded`));
    box.onchange=event=>{event.stopPropagation();rasterScreenInks=rasterScreenInks.filter(id=>id!==ink.id);dirty();renderRasterPens();};
    unavailable.append(label);
  }
  $('raster-screen-count').textContent=`${loaded.filter(c=>c.selectedIds.length).length} of ${loaded.length} loaded pens selected${missing.length?` · ${missing.length} unavailable selection${missing.length===1?'':'s'}`:''}`;
  $('raster-loaded-pens').disabled=!loaded.length;
}
$('raster-loaded-pens').onclick=()=>{
  rasterScreenInks=screenPenChoices(inks,rasterScreenInks,carousel).loaded.slice(0,8).map(choice=>{
    const {pen,slot}=choice;let ink=choice.ink;
    if(!ink){ink={id:newInkId('screen'),...outputPen(pen,slot)};inks.push(ink);}
    else Object.assign(ink,outputPen(pen,slot));
    return ink.id;
  });
  dirty();renderRasterPens();
};
function renderRasterPens() {
  const container=$('raster-inks');container.replaceChildren();
  for(const role of 'CMYK') {
    const label=node('label',rasterRoleLabel(role,rasterRoleSettings()));label.dataset.role=role;
    const select=node('select');select.id='raster-ink-'+role;
    select.setAttribute('aria-describedby','raster-assignment-hint');
    const blank=node('option','Choose a pen…');blank.value='';select.append(blank);
    for(const ink of inks){const option=node('option',`${ink.label} · ${ink.color_srgb} · ${ink.width_mm} mm`);option.value=ink.id;select.append(option);}
    for(const pen of availablePens()) {
      if(inks.some(ink=>outputSlot(ink,carousel)===pen.slot))continue;
      const option=node('option',`Slot ${pen.slot}: ${pen.label} · ${pen.color} · ${pen.width_mm} mm`);option.value='slot:'+pen.slot;select.append(option);
    }
    select.value=rasterInks[role]||'';
    select.onchange=()=>{
      let id=select.value;
      if(id.startsWith('slot:')){
        const slot=Number(id.slice(5));const ink={id:newInkId('raster-'+role.toLowerCase()),...outputPen(carousel.slots[slot],slot)};
        inks.push(ink);id=ink.id;
      }
      if(id)rasterInks[role]=id;else delete rasterInks[role];
      dirty();renderRasterPens();
    };
    label.append(select);container.append(label);
  }
  renderScreenPens();rasterControls();
}
function fillFields(policies) {
  const p = policies || defaultPolicies;
  $("stroke-mode").value=p.stroke_width_mode||"preserve";
  $("tolerance-label").hidden=$("stroke-mode").value==='pen';
  $("fill").value = p.fill.mode;
  writePaperField($("spacing"),p.fill.spacing_mm ?? 2);
  $("angle").value = p.fill.angle_deg ?? 45;
  writePaperField($("feather"),p.fill.feather_distance_mm ?? 12.7);
  $("tolerance").value = p.stroke_width_tolerance;
  $('thin-detail').value=p.thin_detail_thickening||0;
  for (const key of ["raster", "gradients", "transparency", "blend_modes"])
    $(key).value = p[key];
  $("feather-label").hidden = $("fill").value !== "feathered";
  const r=p.raster_settings||{};
  $('raster-screening').checked=r.screening??true;
  $('raster-palette').value=r.palette||'auto';
  $('raster-fullness').value=(r.fullness??1)*100;
  writePaperField($('raster-falloff'),r.edge_falloff_mm??6.35);
  $('raster-black').value=r.black_treatment||'lines';
  rasterInks=clone(r.inks||{});
  rasterScreenInks=clone(r.screen_inks||[]);
  renderRasterPens();
}
function pageSelection(intent = doc.intent) {
  const index = number("page");
  return intent?.pages.find((p) => p.page_index === index);
}
function setPlacement(selection) {
  const p = selection?.placement || {
    scale: 1,
    rotation_deg: 0,
    offset_mm: [0, 0],
  };
  $("scale").value = p.scale;
  $("rotation").value = p.rotation_deg;
  showOffsets(p.offset_mm);
  mediaSnapshot = selection?.target_media
    ? clone(selection.target_media)
    : null;
  $("media").replaceChildren(
    new Option("Source page size · uncalibrated", "source"),
  );
  for (let i = 0; i < caps.media_profiles.length; i++)
    $("media").add(new Option(caps.media_profiles[i].label, String(i)));
  if (selection?.target_media) {
    $("media").add(
      new Option(
        `${selection.target_media.label} · document snapshot`,
        "intent",
      ),
    );
    $("media").value = "intent";
  } else $("media").value = "source";
  paperLabels();
}
function media() {
  if ($("media").value === "source") return clone(sourcePage()?.default_media || null);
  if ($("media").value === "intent") return clone(mediaSnapshot);
  return clone(caps.media_profiles[Number($("media").value)]);
}
function chooseMedia(target){
  mediaSnapshot=target;
  if(![...$('media').options].some(o=>o.value==='intent'))$('media').add(new Option('', 'intent'));
  $('media').value='intent';paperLabels();dirty();
}
$('turn-sheet').onclick=()=>{const target=media();if(target)chooseMedia(turnSheet(target));};
$('sheet-loading').onchange=()=>{const target=media(),angle=number('sheet-loading');if(target&&fitsPlaten(platenWidth(target,angle)))chooseMedia(loadSheet(target,angle));};
// Working recipes belong to tabs; conversion results are immutable snapshots.
let workspaceDocs=[],activeEntry=null,libraryItems=[],opening=0,applying=false,lastEdit=null,restoring=true;
const documentName=entry=>entry.saved?.name||entry.source.name+(entry.sourceSnapshot?' · source version':'');
const documentDirty=entry=>recipeKey(entry.recipe)!==recipeKey(entry.saved?.intent||entry.baseline);
const studioNav=studioNavigation({studio:'pdf',notice,open:openNavigation});
const safely=fn=>(...args)=>Promise.resolve().then(()=>fn(...args)).catch(e=>notice(e.message));
function remember(){
  if(restoring)return;
  try{
    const state={version:1,active:activeEntry?.key,documents:workspaceDocs.map(e=>({key:e.key,documentId:e.source.id,saved:e.saved,recipe:e.recipe,baseline:e.baseline,page:e.page,currentId:e.current?.id,updatingPlot:e.updatingPlot,view:e.view,navigation:e.navigation,sourceConversion:e.sourceConversion,sourceSnapshot:e.sourceSnapshot,replacement:e.replacement}))};
    const value=JSON.stringify(state);sessionStorage.setItem('plotline-pdf-workspace',value);localStorage.setItem('plotline-pdf-workspace',value);
  }catch{notice('Browser storage is full or unavailable. Save your recipes before leaving.');}
}
function readRecipe(){
  const selected=readIntent(),pages=clone(activeEntry.recipe.pages||[]),index=pages.findIndex(p=>p.page_index===selected.pages[0].page_index);
  if(index<0)pages.push(selected.pages[0]);else pages[index]=selected.pages[0];
  return {...selected,pages:pages.sort((a,b)=>a.page_index-b.page_index)};
}
function captureRecipe(event){
  if(applying||!activeEntry||doc?.status!=='ready'||!sourcePage())return;
  const next=readRecipe(),key=event?.type==='input'?event.target.id:null,now=performance.now();
  activeEntry.history.record(next,{coalesce:!!key&&lastEdit?.key===key&&lastEdit.entry===activeEntry&&now-lastEdit.time<1000});
  if(stable(next)!==stable(activeEntry.recipe))lastEdit={key,entry:activeEntry,time:now};
  activeEntry.recipe=next;remember();
}
function syncAddress(){if(activeEntry)history.replaceState(null,'',`/pdf?document=${doc.id}${activeEntry.saved?'&recipe='+activeEntry.saved.id:''}${current?'&conversion='+current.id:''}`);else history.replaceState(null,'','/pdf');}
function stashEntry(){
  if(!activeEntry)return;
  captureRecipe();Object.assign(activeEntry,{current,updatingPlot,posting,stylePages,page:number('page'),view:$('view').value,navigation:navigation.state,scroll:document.querySelector('.controls').scrollTop});remember();
}
function documentState(){
  const entry=activeEntry,usable=entry?.source.status==='ready';
  renderDocumentTabs($('document-tabs'),{documents:workspaceDocs,active:entry,name:documentName,dirty:documentDirty,select:activateEntry,close:closeDocument,safe:safely});
  $('saved-state').textContent=entry?.saving?'Saving…':entry&&documentDirty(entry)?'Unsaved changes':entry?.saved?'Saved':'Not saved';
  $('save').textContent=entry?.saving?'Saving…':'Save';$('save').disabled=!usable||!!entry?.saving;$('save-copy').disabled=!usable||!!entry?.saving;
  $('revert').disabled=!entry?.saved||!documentDirty(entry)||!!entry.saving;
  $('replace-source').disabled=!usable;
  $('undo').disabled=!entry?.history.canUndo;$('redo').disabled=!entry?.history.canRedo;
  $('edit-inks').disabled=!usable;$('convert').querySelectorAll('fieldset').forEach(e=>e.disabled=!usable);
  studioNav.title(entry?documentName(entry)+(documentDirty(entry)?' *':''):'');
  const links=sourceLinks({authoring:entry?.recipe?.authoring});
  if(entry?.sourceConversion)links.push({studio:'pdf',label:'View source PDF version',url:`/pdf?document=${doc.id}&conversion=${entry.sourceConversion}&snapshot=1&page=${entry.page}`});
  studioNav.setSources(links);
}
function applyRecipe(){
  if(!activeEntry||doc.status!=='ready')return;
  applying=true;
  try{
    activeEntry.recipe=resolveRecipePens(activeEntry.recipe,entrySources(activeEntry),carousel);
    const intent=activeEntry.recipe;
    $('page').value=String(activeEntry.page??intent.pages[0].page_index);
    if(!sourcePage())$('page').selectedIndex=0;
    inks=clone(intent.palette.inks);initializeMapping(intent);fillFields(intent.policies);setPlacement(pageSelection(intent));renderInks();
  }finally{applying=false;}
  paperSummary();renderPreview();updateGeneration();documentState();
}
function activateEntry(entry){
  if(entry===activeEntry)return;
  stashEntry();clearTimeout(poll);selectionToken++;lastEdit=null;notice();
  activeEntry=entry;doc=entry?.source||null;current=entry?.current||null;posting=!!entry?.posting;updatingPlot=entry?.updatingPlot||null;
  $('empty').hidden=!!entry;$('review').hidden=!entry;
  if(!entry){syncAddress();$('preview-link').removeAttribute('href');$('preview').removeAttribute('src');$('preview').hidden=true;documentState();updateGeneration();renderProgress();remember();return;}
  stylePages=entry.stylePages||doc.inspection?.pages||[];
  $('page').replaceChildren(...(doc.inspection?.pages||[]).map(p=>new Option(String(p.page_index+1),p.page_index)));
  $('original').href=`/api/v1/documents/${doc.id}/${doc.original_format?'original':'source.pdf'}`;
  const sourceLabel=doc.original_format==='svg'?'Imported SVG':doc.original_format?'Original image':'Original PDF';
  $('original').textContent=doc.original_format==='svg'?'Original SVG ↓':`${sourceLabel} ↓`;
  $('view').querySelector('option[value="source"]').textContent=sourceLabel;
  $('document-detail').textContent=`${doc.name} · ${doc.inspection?.pages.length||'?'} pages · ${(doc.bytes/1024).toFixed(1)} KB`;
  $('view').value=entry.view||'pen';navigation.set(entry.navigation||{scale:1,x:0,y:0});
  applyRecipe();renderProgress();document.querySelector('.controls').scrollTop=entry.scroll||0;
  if(doc.status!=='ready'){renderPreview();updateGeneration();}
  syncAddress();
  if(doc.status==='error')notice(doc.error);else if(doc.intent_error)notice(doc.intent_error);
  else if(entry.replacementNote){notice(entry.replacementNote);delete entry.replacementNote;}
  if(['received','inspecting'].includes(doc.status))watchInspection(entry);else watchConversion();
  documentState();remember();
}
async function watchInspection(entry){
  const token=selectionToken;
  poll=setTimeout(async()=>{try{
    const loaded=await api(`/api/v1/documents/${entry.source.id}`);
    if(token!==selectionToken)return;
    if(loaded.status==='ready'){
      const next=await makeEntry(loaded,{key:entry.key,replacement:entry.replacement});Object.assign(entry,next);activeEntry=null;activateEntry(entry);await library();
    }else{entry.source=doc=loaded;renderProgress();if(loaded.status==='error')notice(loaded.error);else watchInspection(entry);}
  }catch(e){if(token===selectionToken){notice(e.message);watchInspection(entry);}}},1200);
}
async function makeEntry(source,snapshot={},conversionId=null,recipeId=null){
  const entry={sourceSnapshot:snapshot.sourceSnapshot,sourceConversion:snapshot.sourceConversion,key:snapshot.key||newInkId('document'),source,saved:snapshot.saved||null,stylePages:source.inspection?.pages||[],page:snapshot.page??0,current:null,posting:false,updatingPlot:snapshot.updatingPlot||null,view:snapshot.view||'pen',navigation:snapshot.navigation,replacement:snapshot.replacement};
  if(source.status!=='ready'){entry.recipe={};entry.baseline={};entry.history=editHistory({});return entry;}
  if(!source.intent?.palette&&entry.stylePages.some(p=>!p.source_styles))entry.stylePages=await api(`/api/v1/documents/${source.id}/source-styles`);
  if(snapshot.replacement){
    const result=replaceSourceRecipe(snapshot.replacement,source);
    entry.recipe=resolveRecipePens(result.intent,entrySources(entry),carousel);
    entry.baseline={};entry.history=editHistory(entry.recipe);entry.replacement=null;
    entry.replacementNote=result.note;
    return entry;
  }
  if(recipeId)entry.saved=await api(`/api/v1/documents/${source.id}/recipes/${recipeId}`);
  const requested=conversionId||snapshot.currentId;
  entry.current=source.conversions.find(c=>c.id===requested)||null;
  let intent=clone(snapshot.recipe||entry.saved?.intent||entry.current?.intent||source.conversions[0]?.intent||source.intent||{});
  let palette=intent.palette?.inks||caps.available_inks.filter(i=>i.width_mm!=null&&i.color_srgb.toLowerCase()!=='#ffffff');if(!palette.length)palette=defaults();
  if(!intent.policies){
    intent.policies=clone(defaultPolicies);
    if(source.original_format&&(source.original_format!=='svg'||source.inspection?.svg_input?.embedded_images))Object.assign(intent.policies,{raster:'dither',raster_settings:{screening:true,palette:'selected',screen_inks:palette.slice(0,8).map(i=>i.id),fullness:1,edge_falloff_mm:6.35,black_treatment:'off',inks:{}}});
  }
  if(intent.policies.stroke_width_mode==='preserve')delete intent.policies.stroke_width_mode;
  if(!intent.policies.thin_detail_thickening)delete intent.policies.thin_detail_thickening;
  intent={...intent,schema:'plotline.print-intent/1',source_sha256:source.source_sha256,title:intent.title||source.name,palette:{schema:'plotline.palette/1',id:'document-palette',label:'Document inks',inks:clone(palette)},bindings:intent.bindings||[],pages:entry.stylePages.map(page=>intent.pages?.find(p=>p.page_index===page.page_index)||{page_index:page.page_index,source_size_mm:page.source_size_mm,placement:{scale:1,rotation_deg:0,offset_mm:[0,0]},...(page.default_media?{target_media:page.default_media}:{})})};
  if(!source.intent?.palette){intent.ink_mapping=clone(intent.ink_mapping||{});for(const page of entry.stylePages)for(const ink of page.source_styles||[])if(!intent.ink_mapping[ink.id])intent.ink_mapping[ink.id]=nearest(ink,palette).id;}
  entry.recipe=resolveRecipePens(intent,entrySources(entry),carousel);entry.baseline=clone(snapshot.baseline||intent);entry.history=editHistory(entry.recipe);
  if(requested&&snapshot.page==null&&entry.current)entry.page=entry.current.intent.pages[0].page_index;
  if(!entry.current)entry.current=source.conversions.find(c=>previewKey(c.intent,entry.page)===previewKey(intent,entry.page))||null;
  if(entry.current&&entry.current.id===source.draft?.requested_conversion_id){
    if(['queued','running'].includes(source.draft.status))entry.updatingPlot={page:entry.page,key:previewKey(entry.current.intent,entry.page)};
    if(entry.current.status==='ready'&&source.draft.status==='error')entry.preparationError=source.draft.error;
  }
  return entry;
}
async function openDocument(id,conversionId=null,recipeId=null,exact=false){
  const existing=workspaceDocs.find(e=>e.source.id===id&&(recipeId?e.saved?.id===recipeId:conversionId?e.current?.id===conversionId:true)&&(!exact||!documentDirty(e)&&previewKey(e.recipe,e.page)===previewKey(e.current?.intent,e.page)));
  if(existing){activateEntry(existing);$('library').close();return;}
  const stamp=++opening,loaded=await api(`/api/v1/documents/${id}`);
  if(!conversionId&&!recipeId)recipeId=loaded.recipes?.[0]?.id;
  const entry=await makeEntry(loaded,{sourceSnapshot:exact},conversionId,recipeId);
  const duplicate=workspaceDocs.find(e=>e.source.id===id&&(recipeId?e.saved?.id===recipeId:!e.saved&&e.current?.id===entry.current?.id)&&(!exact||!documentDirty(e)&&previewKey(e.recipe,e.page)===previewKey(e.current?.intent,e.page)));
  if(!duplicate)workspaceDocs.push(entry);
  if(stamp===opening){activateEntry(duplicate||entry);$('library').close();}
  remember();
}
async function openNavigation(url){
  const query=url.searchParams;
  if(query.get('document'))await openDocument(query.get('document'),query.get('conversion'),query.get('recipe'),query.has('snapshot'));
  if(query.get('source_conversion')&&activeEntry){activeEntry.sourceConversion=query.get('source_conversion');notice('Opened the editable PDF recipe. View source PDF version is available under Edit source.');}
  if(query.has('pen_substitutions')&&doc){
    const changes=JSON.parse(query.get('pen_substitutions'));
    for(const ink of inks){const update=changes[ink.id];if(update && /^#[0-9a-f]{6}$/i.test(update.color_srgb)&&typeof update.width_mm==='number'&&update.width_mm>0&&update.width_mm<=5){Object.assign(ink,{color_srgb:update.color_srgb,width_mm:update.width_mm,label:typeof update.label==='string'?update.label:ink.label});delete ink.source;}}
    renderInks();dirty();notice('Pen substitutions applied to conversion settings. Preview pen strokes to rebuild the paths.');
  }
  if (query.has("page") && [...$("page").options].some(o=>o.value===query.get("page"))) {
    $("page").value=query.get("page");$("page").onchange();
  }
  documentState();remember();
}
async function closeDocument(entry){
  stashEntry();if(entry.saving)return;
  if(documentDirty(entry)){
    const choice=await confirmDocumentClose($('close-document-dialog'),$('close-document-message'),documentName(entry));
    if(choice==='cancel'||!choice)return;
    if(choice==='save'&&(!await saveRecipe(false,entry)||documentDirty(entry)))return;
  }
  const index=workspaceDocs.indexOf(entry);workspaceDocs.splice(index,1);
  if(entry===activeEntry)activateEntry(workspaceDocs[Math.min(index,workspaceDocs.length-1)]||null);
  remember();documentState();renderLibrary();
}
function unusedName(name){const names=new Set(libraryItems.map(i=>i.title.toLocaleLowerCase()));let result=name,n=2;while(names.has(result.toLocaleLowerCase()))result=name.slice(0,150)+` (${n++})`;return result;}
async function saveRecipe(copy=false,entry=activeEntry){
  stashEntry();if(!entry||entry.saving||entry.source.status!=='ready')return false;
  entry.saving=true;documentState();
  try{
    let name=entry.saved?.name;
    if(copy||!entry.saved){await library();name=await chooseDocumentName({dialog:$('save-dialog'),form:$('save-form'),input:$('save-name'),heading:$('save-heading'),cancel:$('cancel-save')},unusedName(documentName(entry)),copy);if(name===null)return false;}
    const intent=clone(entry.recipe),signature=stable(intent);
    const saved=await api(`/api/v1/documents/${entry.source.id}/recipes`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,intent,...(!copy&&entry.saved?{id:entry.saved.id,expected_revision:entry.saved.revision}:{})})});
    entry.saved=saved;lastEdit=null;
    if(stable(entry.recipe)===signature){entry.recipe=clone(saved.intent);if(entry===activeEntry)applyRecipe();}
    if(entry===activeEntry)history.replaceState(null,'',saved.review_url);
    remember();documentState();await library();return true;
  }catch(error){notice(error.message);return false;}finally{entry.saving=false;documentState();}
}
function restoreRecipe(value){activeEntry.recipe=clone(value);applyRecipe();remember();lastEdit=null;}
function undoRecipe(redo=false){if(!activeEntry)return;captureRecipe();const value=redo?activeEntry.history.redo():activeEntry.history.undo();if(value)restoreRecipe(value);}
function revertRecipe(){if(!activeEntry?.saved)return;captureRecipe();activeEntry.history.record(activeEntry.saved.intent);restoreRecipe(activeEntry.saved.intent);}
async function library(){
  const response=await api('/api/v1/documents?summary=true');
  libraryItems=response.documents.flatMap(item=>item.recipes?.length?item.recipes.map(recipe=>({...recipe,key:recipe.id,title:recipe.name,detail:item.name,documentId:item.id,recipeId:recipe.id})): [{key:item.id,title:item.name,documentId:item.id,detail:item.status==='ready'?'PDF · recipe not saved':item.status,thumbnail_url:item.status==='ready'?`/api/v1/documents/${item.id}/pages/0`:null}]);
  renderLibrary();
}
function renderLibrary(){renderDocumentLibrary($('documents'),{items:libraryItems,query:$('document-search').value,isOpen:item=>workspaceDocs.some(e=>e.source.id===item.documentId&&(!item.recipeId||e.saved?.id===item.recipeId)),open:item=>openDocument(item.documentId,null,item.recipeId),empty:'Add a PDF to begin.',safe:safely});}
async function openLibrary(){await library();$('library').showModal();$('document-search').focus();}
$('open-library').onclick=safely(openLibrary);$('empty-open').onclick=safely(openLibrary);$('close-library').onclick=()=>$('library').close();$('document-search').oninput=renderLibrary;
let uploadReplacement=null;
function showUpload(replace=false){
  stashEntry();
  uploadReplacement=replace?{intent:clone(activeEntry.recipe),sources:clone(entrySources(activeEntry))}:null;
  $('upload').reset();$('upload-heading').textContent=replace?'Replace source':'Add document';
  $('replacement-hint').hidden=!replace;
  $('replacement-hint').textContent=replace?`Use the current settings from “${documentName(activeEntry)}” in a new tab.`:'';
  $('intent-upload').hidden=replace;$('upload-submit').textContent=replace?'Open with these settings':'Add for review';
  $('library').close();$('upload-dialog').showModal();
}
$('new-document').onclick=$('add-pdf').onclick=()=>showUpload();
$('replace-source').onclick=()=>showUpload(true);$('cancel-upload').onclick=()=>$('upload-dialog').close();
$('save').onclick=safely(()=>saveRecipe());$('save-copy').onclick=safely(()=>saveRecipe(true));$('revert').onclick=revertRecipe;$('undo').onclick=()=>undoRecipe();$('redo').onclick=()=>undoRecipe(true);
setInterval(()=>{if($('library').open)library().catch(()=>{});},3000);
window.addEventListener('beforeunload',event=>{stashEntry();if(workspaceDocs.some(documentDirty)){event.preventDefault();event.returnValue='';}});
window.addEventListener('keydown',event=>{
  if(document.querySelector('dialog[open]'))return;
  const key=event.key.toLowerCase();
  if((event.ctrlKey||event.metaKey)&&key==='s'){event.preventDefault();safely(()=>saveRecipe(event.shiftKey))();}
  if((event.ctrlKey||event.metaKey)&&key==='o'){event.preventDefault();safely(openLibrary)();}
  if((event.ctrlKey||event.metaKey)&&['z','y'].includes(key)&&!event.target.closest('input,select,textarea,[contenteditable]')){event.preventDefault();undoRecipe(key==='y'||event.shiftKey);}
});
wireStudioTrays();
let drawerFocus=null,drawerScroll=0;
$('edit-inks').onclick=()=>{drawerFocus=document.activeElement;drawerScroll=window.scrollY;$('ink-drawer').hidden=false;document.body.classList.add('ink-drawer-open');$('edit-inks').setAttribute('aria-expanded','true');if(matchMedia('(max-width:640px)').matches)scrollTo(0,0);};
function closeInks(){if($('ink-drawer').hidden)return;$('inks')._inkTray?.cancel();$('ink-drawer').hidden=true;document.body.classList.remove('ink-drawer-open');$('edit-inks').setAttribute('aria-expanded','false');scrollTo(0,drawerScroll);drawerFocus?.focus({preventScroll:true});}
$('close-inks').onclick=closeInks;
document.addEventListener('click',event=>{if(event.target.closest('.ink-drawer-heading')&&$('inks').classList.contains('ink-selecting'))$('inks')._inkTray?.cancel();},true);
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&!document.querySelector('dialog[open]'))closeInks();});
new ResizeObserver(()=>{const scroll=$('inks').querySelector('.ink-scroll'),nav=$('inks').querySelector('.ink-scroll-nav');if(scroll&&nav)nav.hidden=scroll.scrollWidth<=scroll.clientWidth+1;}).observe($('inks'));

function metric(label, value) {
  const e = node("div");
  e.append(node("strong", value), node("span", label));
  return e;
}
function mileageTable(title, rows) {
  const wrap = node("div"),
    table = node("table"),
    head = node("tr");
  wrap.append(node("h2", title));
  ["Ink / feature", "Pen down", "Paths"].forEach((s) =>
    head.append(node("th", s)),
  );
  table.append(head);
  for (const [name, row] of rows) {
    const tr = node("tr");
    tr.append(
      node("td", name),
      node("td", `${(row.pen_down_mm / 1000).toFixed(2)} m`),
      node("td", String(row.paths)),
    );
    table.append(tr);
  }
  wrap.append(table);
  return wrap;
}
function renderPreview() {
  if (!doc) return;
  const index = number("page"),
    page = current?.result?.pages?.find((p) => p.page_index === index),
    ready = current?.status === "ready" && page && page.preview_available!==false;
  const mode = $("view").value;
  const url = !doc.inspection ? null : ready
    ? `/api/v1/conversions/${current.id}/pages/${index}/${mode}.svg`
    : `/api/v1/documents/${doc.id}/pages/${index}?v=2`;
  if(url){
    if ($("preview").getAttribute("src") !== url) $("preview").src = url;
    $("preview-link").href = url;$("preview-open").href = url;
  }else{
    $("preview").removeAttribute('src');$("preview-link").removeAttribute('href');$("preview-open").removeAttribute('href');
  }
  if(!ready)$("hpgl").removeAttribute('href');
  $("preview").hidden = !doc.inspection;
  renderLoss();
  renderProgress();
  $("caption").textContent = !doc.inspection ? '' : !ready
    ? `${doc.original_format==='svg'?'Imported SVG':doc.original_format?'Original image':'Original PDF'}, before pen conversion.`
    : {
        source:
          `${doc.original_format==='svg'?'Imported SVG':doc.original_format?'Original image':'Original PDF'} positioned on the selected sheet. Gray is outside the sheet; blue dashes mark the printable area.`,
        logical:
          "Policy-normalized vector paint; raster images show their pen interpretation. Use Original PDF to compare source image colors.",
        pen: "Decoded HPGL footprints at the selected ink diameters. Hatch gaps and width compromises are visible here.",
      }[mode];
  $("placement-note").textContent = ready
    ? (page.geometry.source_exceeds_sheet
        ? "The source page extends beyond this sheet. It has not been shrunk to fit. "
        : "") +
      "Pen output is clipped to the source page and the blue printable boundary."
    : "";
  $("metrics").replaceChildren();
  $("revision-detail").hidden = !ready;
  if (current?.status === "error" || current?.status === "unsupported")
    notice(conversionError(current));
  if (ready) {
    const m = page.metrics,
      stats = node("div", undefined, "metrics");
    stats.append(
      metric("pen down", `${(m.pen_down_mm / 1000).toFixed(2)} m`),
      metric("pen up", `${(m.pen_up_mm / 1000).toFixed(2)} m`),
      metric("paths", m.paths),
    );
    $("metrics").append(stats);
    $("metrics").append(
      mileageTable(
        "Pen mileage",
        Object.entries(page.by_ink).map(([id, row]) => [
          current.intent.palette.inks.find((i) => i.id === id)?.label || id,
          row,
        ]),
      ),
    );
    const features = Object.entries(page.by_feature).sort(
      (a, b) => b[1].pen_down_mm - a[1].pen_down_mm,
    );
    if (features.length > 1)
      $("metrics").append(
        mileageTable("Largest features", features.slice(0, 10)),
      );
    $("metrics").append(
      node(
        "p",
        `${page.visible_paints_without_paths} visible paints have no pen paths. Sparse fills intentionally leave unpainted area.`,
        "small quiet",
      ),
    );
    if(page.raster_images?.length) {
      const palettes=[...new Set(page.raster_images.filter(r=>r.screening!==false).map(r=>r.screen_palette==='selected'?`Selected pens · ${r.screen_inks.map(i=>i.label).join(', ')} (optimized color)`:r.screen_palette==='black'?'K':r.screen_palette.toUpperCase()))];
      $('metrics').append(node('p',`${palettes.length?'Image screening: '+palettes.join(' / '):'Image screening off; recovered black marks only'}. Colors use an illustrative ink-mixing model.`, 'small quiet'));
    }
    if (page.warnings.length)
      $("metrics").append(node("p", page.warnings.join(" · "), "small"));
    $("revision-meta").textContent =
      `Source ${current.source_sha256.slice(0, 16)} · settings ${current.settings_sha256.slice(0, 16)} · converter ${current.converter_sha256.slice(0, 16)}`;
    $("revision-settings").textContent = JSON.stringify(
      current.intent,
      null,
      2,
    );
    $("report").href =
      page.artifacts?.report || page.artifacts?.summary ||
      `/api/v1/conversions/${current.id}/pages/${index}/report.json`;
    $("report").textContent = page.artifacts?.summary && !page.artifacts?.report
      ? "Conversion summary ↗" : "Geometry and mileage report ↗";
    $("hpgl").href =
      `/api/v1/conversions/${current.id}/pages/${index}/plot.hpgl`;
  }
}
function renderLoss(){
  const page=current?.status==='ready'&&current.result?.pages?.find(p=>p.page_index===number('page'));
  const url=page?.preview_available!==false&&page?.detail_loss?.version>=2&&page?.artifacts?.detail_loss,control=$('show-loss'),overlay=$('loss-overlay');
  control.disabled=!url;
  const unavailable=current&&['queued','running'].includes(current.status)?'Available after the pen preview finishes.':page?'Update the pen preview first.':'Generate a pen preview first.';
  control.title=url?'Significant missing strokes and letter parts. Minor trimming and short texture fragments are ignored. Never plotted.':unavailable;
  control.closest('label').title=control.title;
  $('loss-count').textContent=url?(page.detail_loss?.affected_paints?`${page.detail_loss.affected_paints.toLocaleString()} affected marks`:'None detected'):unavailable;
  if(!url||!control.checked){overlay.hidden=true;delete overlay.dataset.url;return;}
  if(overlay.dataset.url!==url){
    overlay.hidden=true;overlay.dataset.url=url;delete overlay.dataset.loaded;
    const image=new Image();
    image.onload=()=>{if(overlay.dataset.url===url&&control.checked){overlay.style.maskImage=`url("${url}")`;overlay.dataset.loaded=url;overlay.hidden=false;}};
    image.onerror=()=>{if(overlay.dataset.url===url){overlay.hidden=true;$('loss-count').textContent='Overlay unavailable';}};
    image.src=url;
  }else overlay.hidden=overlay.dataset.loaded!==url;
}
function preparedPage(){
  if(!activeEntry||penIssues().length||!current||current.status!=='ready'||doc.draft?.status!=='ready')return null;
  if(doc.draft.requested_conversion_id!==current.id&&doc.draft.current_conversion_id!==current.id)return null;
  if(previewKey(readIntent(),number('page'))!==previewKey(current.intent,number('page')))return null;
  return doc.draft.jobs?.find(job=>job.page_index===number('page'))||null;
}
function preparedJob(){const page=preparedPage();return page?.job_id?page:null;}
let progressView=null,progressId=null,progressPercent=0;
function renderProgress(){
  const running=current&&['queued','running'].includes(current.status),p=current?.progress;
  const paperIssue=paperLoadingIssue(),blockedPaper=!!paperIssue&&doc?.status==='ready'&&!posting&&!running&&!updatingPlot;
  let text='Open a document to begin',percent=0,elapsed=null;
  if(doc){
    if(['received','inspecting'].includes(doc.status)){text=doc.original_format==='svg'?'Importing SVG…':'Reading document…';percent=null;}
    else if(doc.status==='error'){text='Unable to read document';}
    else if(posting){text='Submitting preview choices…';percent=null;}
    else if(running){
      const phases=['extracting','preparing','reading','clips','coverage','indexing','visibility','planning','routing','verification','artifacts','previews','complete'];
      if(current.intent?.policies?.raster==='dither')phases.splice(2,0,'raster');
      const at=Math.max(0,phases.indexOf(p?.phase)),fraction=p?.total>0?Math.min(1,p.completed/p.total):0;
      if(progressId!==current.id){progressId=current.id;progressPercent=0;}
      progressPercent=Math.max(progressPercent,Math.min(97,100*(at+fraction)/phases.length));percent=progressPercent;
      text='Drawing preview: '+(p?.label||(current.status==='queued'?'waiting for converter':'starting'));
      elapsed=current.elapsed_seconds||0;
    }else if(updatingPlot){text='Finishing preview: registering plot';percent=98;elapsed=current?.elapsed_seconds||0;}
    else if(blockedPaper){text=paperIssue;percent=0;}
    else if(current?.status==='error'||current?.status==='unsupported'){text=rasterDecisionFailure(current)?($('raster').value==='reject'?'Choose how to handle raster images':'Image choice updated; preview again'):'Conversion needs attention';}
    else if(activeEntry?.preparationError){text='Plot preparation needs attention';percent=98;elapsed=current?.elapsed_seconds;}
    else if(rasterIssue()){text=rasterIssue();percent=0;}
    else if(screenIssues().length){text='Choose loaded screening pens in Images & effects';percent=0;}
    else if(penIssues().length){text='Loaded pens need attention';percent=0;}
    else if(preparedPage()){text=preparedJob()?'Plot ready':'No drawable pen paths';percent=100;elapsed=current.elapsed_seconds;}
    else if(current?.status==='ready'){
      const matching=previewKey(readIntent(),number('page'))===previewKey(current.intent,number('page'));
      text=matching?'Preview complete':'Preview uses earlier choices';percent=100;elapsed=current.elapsed_seconds;
    }else{text=doc.original_format==='svg'?'Imported SVG':doc.original_format?'Original image':'Original PDF';percent=100;}
  }
  progressView={text,percent,elapsed,running:!!running||!!updatingPlot,received:performance.now()};
  $('work-status').dataset.conversion=current?.id||'';
  $('work-status').dataset.phase=updatingPlot?'preparing':activeEntry?.preparationError?'error':current?.status||doc?.status||'empty';
  $('review-paper').hidden=!blockedPaper;
  $('work-detail').classList.toggle('paper-issue',blockedPaper);
  renderStudioProgress({bar:$('work-progress'),detail:$('work-detail'),time:$('progress-time')},progressView);
}
$('review-paper').onclick=()=>{
  closeInks();
  const field=[...$('sheet-loading').options].some(o=>!o.disabled)?$('sheet-loading'):$('media');
  field.scrollIntoView({block:'center'});field.focus({preventScroll:true});
};
setInterval(()=>{if(progressView)renderStudioProgress({bar:$('work-progress'),detail:$('work-detail'),time:$('progress-time')},progressView);},500);
function watchConversion(recovering=false){
  clearTimeout(poll);
  if(!current||(!['queued','running'].includes(current.status)&&!updatingPlot))return;
  const id=current.id,entry=activeEntry,token=selectionToken;
  poll=setTimeout(async()=>{try{
    const next=await api(`/api/v1/conversions/${id}`);
    if(token!==selectionToken||activeEntry!==entry||current?.id!==id)return;
    if(recovering)notice();current=entry.current=next;
    const i=doc.conversions.findIndex(c=>c.id===id);if(i>=0)doc.conversions[i]=next;else doc.conversions.unshift(next);
    if(updatingPlot&&!['queued','running'].includes(next.status)){
      const loaded=await api(`/api/v1/documents/${doc.id}`);if(token!==selectionToken)return;doc.draft=loaded.draft;
      if(next.status!=='ready'){notice(next.error||'Could not create the pen preview');updatingPlot=null;}
      else if(loaded.draft.requested_conversion_id!==id){entry.preparationError='Another preparation superseded this one. Preview again to use these settings.';notice(entry.preparationError);updatingPlot=null;}
      else if(loaded.draft.status==='error'){entry.preparationError=loaded.draft.error||'Could not register the plot';notice(entry.preparationError);updatingPlot=null;}
      else if(loaded.draft.status==='ready'){
        const job=loaded.draft.jobs.find(p=>p.page_index===updatingPlot.page);
        if(job?.status==='empty')notice('This page has no drawable pen paths with these settings.');updatingPlot=null;
      }
      entry.updatingPlot=updatingPlot;
    }
    renderPreview();updateGeneration();remember();watchConversion();
  }catch(e){if(token===selectionToken&&current?.id===id){notice(e.message);watchConversion(true);}}},1200);
}
async function convertRecipe(){
  if(!$('convert').reportValidity()||!activeEntry||activeEntry.posting||doc.status!=='ready')return;
  captureRecipe();const entry=activeEntry,index=number('page'),snapshot=readIntent(),sources=sourceInks(),previous=carousel;
  entry.posting=posting=true;notice();
  entry.preparationError=null;entry.updatingPlot=updatingPlot={page:index,key:previewKey(snapshot,index)};
  updateGeneration();
  try{
    // Freeze recipe edits at the click, then resolve its slot dependencies.
    // Changes made while the inventory request is in flight stay in the editor.
    await refreshCarousel();
    const intent=resolveRecipePens(snapshot,sources,carousel,previous);
    if(unavailableSlots(intent,sources,carousel).length)throw Error('Load the assigned pens with known diameters, or change their mapping before converting.');
    const raster=intent.policies?.raster_settings;
    if(intent.policies?.raster==='dither'){const issue=rasterAssignmentIssue(raster,intent.palette.inks);if(issue)throw Error(issue);}
    if(intent.policies?.raster==='dither'&&raster?.screening&&raster.palette==='selected'&&screenPenChoices(intent.palette.inks,raster.screen_inks,carousel).missing.length)throw Error('Remove unavailable screening inks or choose Use loaded pens before converting.');
    entry.updatingPlot.key=previewKey(intent,index);
    if(!intent.palette.inks.length)throw Error('Choose at least one output pen.');
    const page=entry.current?.result?.pages?.find(p=>p.page_index===index);
    // Retained previews and failed registration retries reuse their exact HPGL.
    // Rebuild requests must still regenerate expired/outdated diagnostic views.
    if(entry.current?.status==='ready'&&page?.preview_available!==false&&page?.artifacts?.detail_loss&&page.detail_loss?.version>=2&&previewKey(intent,index)===previewKey(entry.current.intent,index)){
      entry.source.draft=await api(`/api/v1/conversions/${entry.current.id}/publish`,{method:'POST'});entry.updatingPlot=null;
    }else{
      const revision=await api(`/api/v1/documents/${entry.source.id}/draft`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(intent)});
      entry.current=revision;entry.source.conversions.unshift(revision);entry.view='pen';
    }
    if(activeEntry===entry){current=entry.current;updatingPlot=entry.updatingPlot;$('view').value='pen';syncAddress();renderPreview();watchConversion();}
  }catch(e){entry.updatingPlot=null;entry.preparationError=e.message;if(activeEntry===entry)notice(e.message);}
  finally{entry.posting=false;if(activeEntry===entry){posting=false;updatingPlot=entry.updatingPlot;updateGeneration();}remember();}
}
$('update-plot').onclick=safely(()=>{const job=preparedJob();if(job&&!$('update-plot').disabled)openStudio('hpgl',job.url);});
$('convert').onsubmit=event=>{event.preventDefault();if(!$('convert-button').disabled)safely(()=>convertRecipe())();};
$('upload').onsubmit=async event=>{
  event.preventDefault();const button=event.submitter,replacement=uploadReplacement;button.disabled=true;notice('Saving original document…');
  try{
    const form=new FormData();form.append('document',$('pdf-file').files[0]);if(!replacement&&$('intent-file').files[0])form.append('intent',$('intent-file').files[0]);
    const result=await api('/api/v1/documents',{method:'POST',body:form});$('upload-dialog').close();
    if(replacement){
      const loaded=await api(`/api/v1/documents/${result.id}`),entry=await makeEntry(loaded,{replacement});
      workspaceDocs.push(entry);activateEntry(entry);remember();
    }else await openDocument(result.id);
    await library();
  }catch(e){notice(e.message);}finally{button.disabled=false;}
};

$("page").onchange = event => {
  if(!activeEntry)return;
  const next=number('page');$('page').value=String(activeEntry.page);captureRecipe();activeEntry.page=next;applyRecipe();remember();event?.stopPropagation();
};
$('view').onchange=()=>{if(activeEntry)activeEntry.view=$('view').value;renderPreview();remember();};
const previewCanvas=document.querySelector('.canvas');
const navigation=previewNavigation(previewCanvas,{
  enabled:()=>Boolean(doc?.inspection),
  onChange:({scale,x,y})=>{
    $("preview-link").style.transform=`translate(-50%,-50%) translate(${x}px,${y}px) scale(${scale})`;
    $("preview-zoom").textContent=`${Math.round(scale*100)}%`;
    if(activeEntry)activeEntry.navigation={scale,x,y};
  }
});
function fitPreview() {
  const image=$("preview");
  if(!image.naturalWidth||!image.naturalHeight)return;
  const width=Math.min(previewCanvas.clientWidth-2,(previewCanvas.clientHeight-2)*image.naturalWidth/image.naturalHeight);
  $("preview-link").style.width=Math.max(1,width)+"px";
}
$("preview").onload=()=>{fitPreview();renderLoss();};
$('show-loss').onchange=renderLoss;
new ResizeObserver(fitPreview).observe(previewCanvas);
$("convert").oninput = event => {if(event.target.id==='sheet-loading')return;if(event.target.id.startsWith('raster'))rasterControls();dirty(event);};
$("convert").onchange = event => {if(event.target.id.startsWith('raster'))rasterControls();dirty(event);};
["x", "y"].forEach((axis, i) => {
  $(`offset-${axis}`).oninput = () => {
    paperOffsetsMm[i] =
      number(`offset-${axis}`);
    dirty();
  };
});
onPaperUnitChange(() => {
  paperLabels();
  updateGeneration();
});
$("fill").onchange = () => {
  $("feather-label").hidden = $("fill").value !== "feathered";
  dirty();
};
$('map-automatically').onclick=mapAutomatically;
$('stroke-mode').onchange=()=>{$('tolerance-label').hidden=$('stroke-mode').value==='pen';renderInks();dirty();};
$("fit").onclick = () => {
  const p = doc.inspection.pages.find((p) => p.page_index === number("page")),
    target = media();
  const rect = target?.printable_rect_mm || [0, 0, ...p.source_size_mm];
  const inset = target?.content_inset_mm || 0;
  let [w, h] = p.source_size_mm;
  if (number("rotation") % 180) [w, h] = [h, w];
  const scale = Math.min((rect[2] - 2 * inset) / w, (rect[3] - 2 * inset) / h);
  $("scale").value = scale;
  showOffsets([
    rect[0] + (rect[2] - w * scale) / 2,
    rect[1] + (rect[3] - h * scale) / 2,
  ]);
  dirty();
};
try {
  [caps,carousel]=await Promise.all([api("/api/v1/capabilities"),api("/api/carousel")]);
  await library();
  const requestedURL=new URL(location.href);
  const stored=JSON.parse(sessionStorage.getItem('plotline-pdf-workspace')||localStorage.getItem('plotline-pdf-workspace')||'null');
  const restored=[];
  if(stored?.version===1)for(const snapshot of stored.documents||[]){
    try{const loaded=await api(`/api/v1/documents/${snapshot.documentId}`);restored.push(await makeEntry(loaded,snapshot));}
    catch(error){notice('Could not restore an open PDF: '+error.message);throw error;}
  }
  workspaceDocs=restored;restoring=false;
  const selected=workspaceDocs.find(e=>e.key===stored?.active)||workspaceDocs[0];if(selected)activateEntry(selected);
  await openNavigation(requestedURL);studioNav.ready();
} catch (error) {
  notice(error.message);
}

// A sweep may expire the cache after this tab loaded its revision metadata.
$("preview").onerror = async () => {
  const id = current?.id;
  if (!id || !$("preview").getAttribute("src")?.startsWith('/api/v1/conversions/')) return;
  try {
    const response = await fetch(`/api/v1/conversions/${id}`);
    if (!response.ok || current?.id !== id) return;
    const row = await response.json();
    if (row.result?.pages?.find(p=>p.page_index===number('page'))?.preview_available===false) {
      current=row; renderPreview(); updateGeneration();
    }
  } catch (_) { /* Keep the existing error display if the service is offline. */ }
};
