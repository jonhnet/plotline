import {renderPreviewControls,studioNotice,stable,renderDocumentTabs,renderDocumentLibrary,chooseDocumentName,confirmDocumentClose,editHistory,renderStudioProgress,wireStudioTrays} from '/studio.js';
import {studioNavigation,openStudio,studioTitle} from '/studio-navigation.js';
import {renderInkSets,penTargets,sameMaterial,newInkId,colorName,revealInk} from '/inksets.js';
import {bindPaperUnits,paperSize,paperLength,onPaperUnitChange,readPaperField,writePaperField} from '/paper-units.js';
import {turnSheet} from '/pdf-paper.js';
import {previewNavigation} from '/preview-navigation.js';
import {prepareAuthoredPlot,studioRequest,submissionID} from '/authoring-print.js';
import {wireRecipeFiles} from '/recipe-files.js';

import {fitMaze,detailLevel,applyDetail} from './geometry.js';
const $=id=>document.getElementById(id),clone=structuredClone,request=studioRequest;
renderPreviewControls($('preview-tools'),{fitId:'fit-view',onFit:()=>view.reset(),onZoom:factor=>view.zoomBy(factor)});
const safe=fn=>(...args)=>Promise.resolve().then(()=>fn(...args)).catch(error=>studioNotice(error.message,{kind:'error'}));
let documents=[],active=null,capabilities,carousel={revision:0,slots:{}},timer=null,library=[];
const workspaceKey='plotline-maze-workspace';
const geometryKey=r=>stable([r.family,r.params]);
const signature=doc=>stable(doc.recipe),dirty=doc=>signature(doc)!==doc.savedSignature;
const nav=studioNavigation({studio:'mazes',open:openURL});
const view=previewNavigation($('preview'),{onChange:()=>paint(),onFit:()=>view.reset()});
const progressElements={bar:$('work-progress'),detail:$('work-detail'),time:$('progress-time')};
const families={square:'Square',hexagon:'Hexagonal',triangle:'Triangular'};
function progress(percent,text,running=false){
  if(!active)return;
  renderStudioProgress(progressElements,{percent,text,running,received:performance.now(),elapsed:running?(performance.now()-(active.started||performance.now()))/1000:null});
}
function remember(){
  const state={active:active?.key,documents:documents.map(d=>({key:d.key,recipe:d.recipe,saved:d.saved,savedSignature:d.savedSignature,savedRecipe:d.savedRecipe,sourceHash:d.sourceHash,sourceSignature:d.sourceSignature,receipt:d.receipt}))};
  try{const value=JSON.stringify(state);sessionStorage.setItem(workspaceKey,value);localStorage.setItem(workspaceKey+'-backup',value);}catch{studioNotice('Browser storage is full. Save your mazes to keep this work.',{kind:'error'});}
}
function makeDoc(recipe,extra={}){const doc={key:submissionID(),recipe:clone(recipe),...extra};doc.history=editHistory(doc.recipe);return doc;}
function loadedInk(slot,pen){return {id:newInkId(),label:pen.label||colorName(pen.color),color_srgb:pen.color,width_mm:pen.width_mm,source:{kind:'plotline-carousel',revision:carousel.revision,slot:Number(slot)}};}
function initialInks(){
  const inks=[];
  for(const [slot,pen] of Object.entries(carousel.slots))if(pen.state==='loaded'&&pen.width_mm>0&&pen.color&&pen.color.toLowerCase()!=='#ffffff'){inks.push(loadedInk(slot,pen));}
  return inks.length?inks:[{id:'ink-black',label:'Black',color_srgb:'#000000',width_mm:.3}];
}
function baseMedia(profile){let media=clone(profile);if(media.sheet_size_mm[0]<media.sheet_size_mm[1])media=turnSheet(media);return media;}
function newRecipe(family='square'){
  const profile=capabilities.media_profiles.find(p=>p.id==='hp7585-tabloid-landscape')||capabilities.media_profiles[0],inks=initialInks();
  const darkness=i=>[1,3,5].reduce((sum,j)=>sum+parseInt(i.color_srgb.slice(j,j+2),16),0);
  const black=inks.find(i=>/black/i.test(i.label))||inks.slice().sort((a,b)=>darkness(a)-darkness(b))[0],accent=inks.find(i=>i.id!==black.id)||black;
  return applyDetail({version:1,name:'Maze',family,detail:50,params:{columns:40,rows:27,seed:1,stretch:3,exits:[[0,.9],[1,.1]],mask:null},
    inks:[...new Map([black,accent].map(i=>[i.id,i])).values()],roles:[{label:'Walls',ink_id:black.id},{label:'Solution',ink_id:accent.id}],
    plot_solution:false,media:baseMedia(profile),paper_id:profile.id,orientation:0,margin_mode:'plotter',custom_margin_mm:19.05});
}
function prune(){const used=new Set(active.recipe.roles.map(r=>r.ink_id));active.recipe.inks=active.recipe.inks.filter(i=>used.has(i.id));}
function chrome(){
  renderDocumentTabs($('document-tabs'),{documents,active,name:d=>d.recipe.name,dirty,select,close:closeDoc,safe,activity:d=>d.preparing?{running:true,label:'Preparing plot'}:null});
  studioTitle(active?.recipe.name);$('undo').disabled=!active?.history.canUndo;$('redo').disabled=!active?.history.canRedo;
  $('revert').disabled=!active?.savedRecipe||!dirty(active);$('save').disabled=!!active?.saving;$('save-copy').disabled=!!active?.saving;
  buttons();
}
function buttons(){
  const d=active;if(!d)return;
  $('download-recipe').disabled=false;
  const ready=!!d.fitted?.paths.length&&d.fittedSignature===signature(d)&&!d.generating&&!d.fitted.blocked,exact=d.receipt?.signature===signature(d),prepared=exact&&d.receipt?.ready;
  $('prepare').disabled=!ready||d.preparing;$('prepare').textContent=d.preparing?'Preparing…':d.printError?'Retry preparation':'Prepare plot';
  $('open-plot').disabled=!prepared;$('download-pdf').disabled=!ready||d.preparing;$('download-svg').disabled=!ready;
  $('prepare').classList.toggle('primary',!prepared);$('open-plot').classList.toggle('primary',!!prepared);
  $('edit-pdf').disabled=!exact||!d.receipt?.document;
  $('plot-hint').textContent=prepared?'Ready to check paper and pens.':d.fitted?.blocked?'Pens close the passages; lower the detail or use finer pens.':'';
}
function mutate({controls=false}={}){
  prune();active.history.record(active.recipe);active.printError=null;remember();if(controls)renderControls();chrome();schedule();
}
function renderPaper(){
  const r=active.recipe;const choices=capabilities.media_profiles.slice();if(!choices.some(p=>p.id===r.paper_id))choices.unshift({...r.media,id:r.paper_id});
  $('paper').replaceChildren(...choices.map(p=>new Option(paperSize([...p.sheet_size_mm].sort((a,b)=>a-b)),p.id)));$('paper').value=r.paper_id;
  $('orientation').value=r.orientation;$('margins').value=r.margin_mode;$('custom-margins').hidden=r.margin_mode!=='custom';writePaperField($('margin'),r.custom_margin_mm);
}
function changePaper(){
  const r=clone(active.recipe),profile=capabilities.media_profiles.find(p=>p.id===$('paper').value);if(!profile)throw Error('Refresh to select this sheet.');
  r.paper_id=profile.id;r.orientation=Number($('orientation').value);r.margin_mode=$('margins').value;r.custom_margin_mm=readPaperField($('margin'));
  let media=baseMedia(profile);for(let i=0;i<r.orientation/90;i++)media=turnSheet(media);
  if(r.margin_mode==='custom'){
    const m=r.custom_margin_mm;if(!Number.isFinite(m)||m<0||2*m+4>=Math.min(...media.sheet_size_mm))throw Error('Margins leave no drawable area.');
    media={...media,id:'maze-custom-margins',label:'Custom margins',printable_rect_mm:[m,m,media.sheet_size_mm[0]-2*m,media.sheet_size_mm[1]-2*m]};
  }
  r.media=media;applyDetail(r);active.recipe=r;mutate({controls:true});
}
function renderControls(){
  const r=active.recipe,p=r.params;$('family').value=r.family;
  for(const key of ['seed','stretch'])$(key).value=p[key];
  $('detail').value=detailLevel(r);$('detail-value').textContent=detailLevel(r);
  $('detail-note').textContent=r.detail==null?'Saved grid preserved. Move Detail level to fit it to the page.':p.mask?'Fits the mask to the printable area; higher detail makes smaller cells.':'Fills the printable area; higher detail makes smaller cells.';
  $('stretch-value').textContent=p.stretch===0?'Unstretched':p.stretch+'×';
  for(let e=0;e<2;e++)for(let axis=0;axis<2;axis++)$('exit-'+e+'-'+axis).value=Math.round(p.exits[e][axis]*100);
  $('plot-solution').checked=r.plot_solution;
  $('mask-note').textContent=p.mask?p.mask.name+' · '+p.mask.width+' × '+p.mask.height+' mask. Dark pixels form the maze.':'Dark pixels form the maze; white and transparent pixels leave holes.';
  $('remove-mask').disabled=!p.mask;renderPaper();renderInks();
}
function renderInks(){
  const d=active,r=d.recipe,targets=penTargets(carousel,r.inks),roles=r.roles.map((role,i)=>({...r.inks.find(ink=>ink.id===role.ink_id),id:'role-'+i,label:role.label}));
  const mapping=Object.fromEntries(roles.map(role=>[role.id,targets.find(t=>sameMaterial(t.ink,role))?.id||0]));
  const assign=(ids,ink)=>{
    if(active!==d)return false;if((ink.color_srgb||ink.color)?.toLowerCase()==='#ffffff')throw Error('Choose a colored ink.');
    const proposed={label:ink.label||colorName(ink.color_srgb||ink.color),color_srgb:ink.color_srgb||ink.color,width_mm:ink.width_mm??null,...(ink.source?{source:clone(ink.source)}:{})};
    let target=r.inks.find(i=>sameMaterial(i,proposed));if(!target){target={...proposed,id:newInkId()};r.inks.push(target);}else if(proposed.source)target.source=proposed.source;
    for(const id of ids)r.roles[Number(id.slice(5))].ink_id=target.id;
    mutate();renderInks();queueMicrotask(()=>revealInk($('palette'),ids[0]));return true;
  };
  renderInkSets($('palette'),{inks:roles,carousel,targets,mapping,roleLabels:true,hideEmptyCustomTargets:true,allowOrder:false,title:'Maze roles',onAssign:(ids,t)=>assign(ids,t.slot?loadedInk(t.slot,t.ink):t.ink),onCustom:assign,customSeed:rs=>r.inks.find(i=>i.id===r.roles[Number(rs[0].id.slice(5))].ink_id),targetIssue:t=>t.slot&&t.ink.state!=='loaded'?'This slot is empty or unavailable.':(t.ink.color||t.ink.color_srgb)?.toLowerCase()==='#ffffff'?'Choose a colored ink.':''});
  $('ink-summary').replaceChildren(...r.inks.map(ink=>{const swatch=document.createElement('span');swatch.className='ink-chip';swatch.style.background=ink.color_srgb;swatch.title=ink.label+' · '+(ink.width_mm??'?')+' mm';return swatch;}));
  $('ink-warning').textContent=r.inks.some(i=>!i.width_mm)?'Choose a diameter for every maze ink before preparing a plot.':'';
  $('inventory-status').textContent=`Loaded carousel snapshot · revision ${carousel.revision}. Select Walls or Solution, then a pen.`;
}
function schedule(){
  clearTimeout(timer);if(!active)return;
  active.fittedSignature=null;buttons();$('preview').dataset.ready='false';
  timer=setTimeout(()=>safe(()=>generate(active))(),140);
}
async function generate(d){
  const key=geometryKey(d.recipe),run=(d.run||0)+1;d.run=run;d.generating=true;d.started=performance.now();
  if(d===active)progress(15,'Growing maze…',true);buttons();
  try{
    if(d.geometryKey!==key||!d.geometry){
      const geometry=await request('/api/mazes/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({family:d.recipe.family,params:d.recipe.params})});
      if(d.run!==run||geometryKey(d.recipe)!==key)return;
      d.geometry=geometry;d.geometryKey=key;
    }
    d.fitted=fitMaze(d.geometry,d.recipe);d.fittedSignature=signature(d);
    if(d===active)finish(d);
  }catch(e){if(d.run===run&&geometryKey(d.recipe)===key){d.fitted=null;d.fittedSignature=null;if(d===active){progress(0,e.message);paint();}}}
  finally{if(d.run===run)d.generating=false;if(d===active){buttons();paint();}}
}
function finish(d){
  const g=d.geometry,f=d.fitted;
  const text=f.blocked?'Pens close the passages. Lower the detail, use finer pens, or enlarge the paper.':`${g.cells.toLocaleString()} cells · ${g.solution_cells} on solution (${g.achieved_stretch.toFixed(1)}× shortest) · cell edge ${paperLength(f.cell_mm)} · ${(f.distance/1000).toFixed(1)} m of walls${d.recipe.plot_solution?' + solution':''}`;
  progress(100,text);$('maze-note').textContent=g.target_reached?'One connected maze, with exactly one route between the entrances.':'Stopped at a local maximum or the stretch limit; the achieved length is shown below. The maze still has one unique solution.';
}
function draw(canvas,recipe,fitted,{thumbnail=false,margin=false,bitmap=null,navigation={scale:1,x:0,y:0}}={}){
  const box=canvas.getBoundingClientRect(),w=thumbnail?canvas.width:box.width,h=thumbnail?canvas.height:box.height,ratio=thumbnail?1:devicePixelRatio||1;
  if(!thumbnail){canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);}
  const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.fillStyle=thumbnail?'#ffffff':'#e9ece4';ctx.fillRect(0,0,w,h);
  if(!recipe||(!fitted&&!bitmap))return;
  const [pw,ph]=recipe.media.sheet_size_mm,s=Math.min((w-(thumbnail?12:60))/pw,(h-(thumbnail?12:55))/ph)*navigation.scale;
  ctx.translate(w/2+navigation.x,h/2+navigation.y);ctx.scale(s,s);ctx.translate(-pw/2,-ph/2);
  ctx.fillStyle='#fffefa';ctx.fillRect(0,0,pw,ph);
  if(bitmap)ctx.drawImage(bitmap,0,0,pw,ph);
  // Match PDF role paint order, including intersections between different inks.
  else for(let role=0;role<recipe.roles.length;role++){
    const ink=recipe.inks.find(i=>i.id===recipe.roles[role].ink_id);ctx.strokeStyle=ink.color_srgb;ctx.lineWidth=ink.width_mm??.3;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();
    for(const p of fitted.paths)if(p.role===role){ctx.moveTo(...p.points[0]);for(let i=1;i<p.points.length;i++)ctx.lineTo(...p.points[i]);}
    ctx.stroke();
  }
  if(margin){
    // Inspection overlay: stay visible at fit/zoom and above progressive frames.
    // It never enters thumbnails, saved artwork, SVG or PDF exports.
    ctx.save();ctx.beginPath();ctx.rect(0,0,pw,ph);ctx.rect(...recipe.media.printable_rect_mm);
    ctx.fillStyle='#33443320';ctx.fill('evenodd');
    ctx.lineWidth=3/s;ctx.strokeStyle='#fffefa';ctx.strokeRect(...recipe.media.printable_rect_mm);
    ctx.lineWidth=1/s;ctx.strokeStyle='#526552';ctx.setLineDash([5/s,4/s]);ctx.strokeRect(...recipe.media.printable_rect_mm);ctx.restore();
  }
}
function paint(){draw($('preview'),active?.recipe,active?.fitted,{navigation:view.state,margin:$('show-margins').checked,bitmap:active?.previewBitmap});$('preview').dataset.ready=String(!!active?.fitted&&!active?.generating&&active.fittedSignature===signature(active));}
function thumbnail(doc){if(!doc.fitted||doc.fittedSignature!==signature(doc))return '';const c=document.createElement('canvas');c.width=400;c.height=280;draw(c,doc.recipe,doc.fitted,{thumbnail:true});return c.toDataURL('image/png');}
async function select(doc){active=doc;view.reset();renderControls();chrome();remember();schedule();}
async function newDocument(){const doc=makeDoc(newRecipe());documents.push(doc);await select(doc);}
async function closeDoc(doc){
  if(dirty(doc)){
    const choice=await confirmDocumentClose($('close-dialog'),$('close-message'),doc.recipe.name);
    if(choice==='cancel')return;if(choice==='save'&&!await saveDoc(doc))return;
  }
  if(doc.preparing){studioNotice('Preparation is still running. Keep this document open until the plot is ready.');return;}
  documents=documents.filter(d=>d!==doc);
  if(!documents.length)await newDocument();else await select(doc===active?documents.at(-1):active);remember();
}
async function saveDoc(doc=active,copy=false){
  let name=doc.recipe.name;
  if(!doc.saved||copy){
    if(copy){const result=await request('/api/mazes/documents');const names=new Set(result.documents.map(d=>d.name));let n=2;const base=name.replace(/ \(\d+\)$/,'');name=base+' (2)';while(names.has(name))name=base+` (${++n})`;}
    name=await chooseDocumentName({dialog:$('save-dialog'),form:$('save-form'),input:$('save-name'),heading:$('save-heading'),cancel:$('cancel-save')},name,copy);if(!name)return false;
  }
  const exactPreview=doc.fittedSignature===signature(doc);doc.recipe.name=name;doc.history.record(doc.recipe);if(exactPreview)doc.fittedSignature=signature(doc);const recipe=clone(doc.recipe);doc.saving=true;chrome();remember();
  try{
    const saved=await request('/api/mazes/documents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({recipe,thumbnail:thumbnail(doc),...(doc.saved&&!copy?{id:doc.saved.id,revision:doc.saved.revision}:{})})});
    doc.saved={id:saved.id,revision:saved.revision};doc.savedRecipe=recipe;doc.savedSignature=stable(recipe);remember();return true;
  }finally{doc.saving=false;chrome();}
}
async function openSaved(id){
  const existing=documents.find(d=>d.saved?.id===id);if(existing){await select(existing);return;}
  const row=await request('/api/mazes/documents/'+encodeURIComponent(id));const doc=makeDoc(row.recipe,{saved:{id:row.id,revision:row.revision},savedSignature:stable(row.recipe),savedRecipe:clone(row.recipe)});documents.push(doc);await select(doc);
}
async function openURL(url){
  const id=url.searchParams.get('document'),hash=url.searchParams.get('source_recipe'),snapshot=url.searchParams.has('snapshot'),substitutions=url.searchParams.get('pen_substitutions');
  if(id&&!snapshot&&!substitutions){await openSaved(id);return;}
  if(hash){
    const existing=!substitutions&&documents.find(d=>d.sourceHash===hash&&stable(d.recipe)===d.sourceSignature);if(existing){await select(existing);return;}
    const row=await request('/api/mazes/snapshots/'+encodeURIComponent(hash)),recipe=clone(row.recipe),changes=JSON.parse(substitutions||'{}');
    for(const ink of recipe.inks){const change=changes[ink.id];if(change){if(!/^#[0-9a-f]{6}$/i.test(change.color_srgb)||change.color_srgb.toLowerCase()==='#ffffff'||typeof change.width_mm!=='number'||!Number.isFinite(change.width_mm)||change.width_mm<.01||change.width_mm>5)throw Error('Invalid pen substitution');Object.assign(ink,{color_srgb:change.color_srgb,width_mm:change.width_mm,label:typeof change.label==='string'?change.label:ink.label});delete ink.source;}}
    const doc=makeDoc(recipe,{sourceHash:hash,sourceSignature:stable(recipe)});documents.push(doc);await select(doc);
    if(substitutions)studioNotice('Pen substitution applied to a separate source maze. Review it, then Prepare plot.');
    else if(snapshot)studioNotice('Opened the exact source maze as a separate document.');
  }
}
async function openLibrary(){library=(await request('/api/mazes/documents')).documents;renderLibrary();$('library').showModal();}
function renderLibrary(){renderDocumentLibrary($('documents'),{items:library.map(r=>({id:r.id,title:r.name,thumbnail_url:r.thumbnail,detail:new Date(r.updated_at*1000).toLocaleString()})),query:$('library-search').value,isOpen:r=>documents.some(d=>d.saved?.id===r.id),open:async r=>{await openSaved(r.id);$('library').close();},safe});}
wireRecipeFiles({dialog:$('library'),button:$('import-recipe'),input:$('recipe-file'),error:$('recipe-error'),download:$('download-recipe'),getRecipe:()=>active?.recipe,
  studio:'Maze',familyIds:Object.keys(families),
  validate:r=>{
    const p=r.params;
    if(r.roles.length!==2||typeof r.plot_solution!=='boolean')throw Error('A maze recipe needs Walls and Solution roles.');
    if(![p.columns,p.rows].every(n=>Number.isInteger(n)&&n>=2&&n<=160)||p.columns*p.rows>16000)throw Error('Use 2–160 rows/columns, with at most 16,000 cells.');
    if(!Number.isInteger(p.seed)||p.seed<0||p.seed>0xffffffff||!Number.isFinite(p.stretch)||p.stretch<0||p.stretch>12)throw Error('Invalid maze seed or stretch.');
    if(!Array.isArray(p.exits)||p.exits.length!==2||p.exits.some(e=>!Array.isArray(e)||e.length!==2||e.some(v=>!Number.isFinite(v)||v<0||v>1)))throw Error('Invalid maze entrance positions.');
    if(r.detail!=null&&(!Number.isInteger(r.detail)||r.detail<1||r.detail>100))throw Error('Detail level must be between 1 and 100.');
    if(p.mask){const m=p.mask;if(![m.width,m.height].every(n=>Number.isInteger(n)&&n>=1&&n<=512)||!Array.isArray(m.pixels)||m.pixels.length!==m.width*m.height||m.pixels.some(v=>v!==0&&v!==1)||typeof m.name!=='string'||m.name.length>240)throw Error('Invalid maze mask; use at most 512 × 512 binary pixels.');}
  },
  open:async recipe=>{const doc=makeDoc(recipe);documents.push(doc);await select(doc);},
});
async function exportDoc(doc){
  const sig=signature(doc);if(doc.export?.signature===sig)return doc.export.record;
  if(!doc.fitted||doc.generating||doc.fittedSignature!==signature(doc))throw Error('Wait for the maze preview.');
  if(doc.fitted.blocked)throw Error('Pens close the passages; lower the detail or use finer pens.');
  const recipe=clone(doc.recipe),paths=doc.fitted.paths;
  const record=await request('/api/mazes/exports',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({recipe,paths,...(doc.saved?{document_id:doc.saved.id}:{})})});
  doc.export={signature:sig,record};return record;
}
async function prepare(doc=active,resume=false){
  if(doc.preparing)return;doc.preparing=true;doc.printError=null;doc.started=performance.now();chrome();
  const sig=signature(doc);
  try{
    if(doc===active)progress(5,'Exporting drawing',true);
    let record;
    if(resume&&doc.receipt?.exportHash)record=await request('/api/mazes/snapshots/'+doc.receipt.exportHash);
    else record=await exportDoc(doc);
    if(!resume&&doc.receipt?.signature!==sig)doc.receipt={key:submissionID(),signature:sig,exportHash:record.hash};
    if(!doc.receipt)doc.receipt={key:submissionID(),signature:sig,exportHash:record.hash};
    remember();
    await prepareAuthoredPlot({submission:doc.receipt,exportRecord:record,onChange:()=>{remember();if(doc===active)buttons();},onProgress:(percent,text)=>{if(doc===active)progress(percent,text,percent<100);}});
  }catch(error){doc.printError=error.message;if(doc===active){progress(0,error.message);studioNotice(error.message,{kind:'error'});}}
  finally{doc.preparing=false;remember();chrome();}
}
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
async function downloadPDF(){const doc=active,record=await exportDoc(doc);const response=await fetch(record.pdf_url);if(!response.ok)throw Error('PDF download failed.');download(await response.blob(),doc.recipe.name+'.pdf');}
function downloadSVG(){
  const r=active.recipe,[w,h]=r.media.sheet_size_mm,xml=['<svg xmlns="http://www.w3.org/2000/svg" width="'+w+'mm" height="'+h+'mm" viewBox="0 0 '+w+' '+h+'">'];
  for(let role=0;role<r.roles.length;role++){const ink=r.inks.find(i=>i.id===r.roles[role].ink_id);xml.push(`<g fill="none" stroke="${ink.color_srgb}" stroke-width="${ink.width_mm??.3}" stroke-linecap="round" stroke-linejoin="round">`);for(const p of active.fitted.paths)if(p.role===role)xml.push('<path d="'+p.points.map(([x,y],i)=>(i?'L':'M')+x+' '+y).join(' ')+'"/>');xml.push('</g>');}
  xml.push('</svg>');download(new Blob([xml.join('\n')],{type:'image/svg+xml'}),r.name+'.svg');
}
function history(direction){view.cancel();const next=active.history[direction]();if(!next)return;active.recipe=next;active.printError=null;remember();renderControls();chrome();schedule();}

$('family').onchange=()=>{active.recipe.family=$('family').value;applyDetail(active.recipe);mutate({controls:true});};
$('detail').oninput=safe(()=>{active.recipe.detail=Number($('detail').value);applyDetail(active.recipe);mutate({controls:true});});
for(const key of ['seed','stretch'])$(key).oninput=()=>{
  const el=$(key);if(!el.checkValidity())return;active.recipe.params[key]=Number(el.value);
  if(key==='stretch')$('stretch-value').textContent=Number(el.value)===0?'Unstretched':el.value+'×';mutate();
};
$('new-seed').onclick=()=>{active.recipe.params.seed=crypto.getRandomValues(new Uint32Array(1))[0];mutate({controls:true});};
for(let e=0;e<2;e++)for(let axis=0;axis<2;axis++)$('exit-'+e+'-'+axis).oninput=()=>{active.recipe.params.exits[e][axis]=Number($('exit-'+e+'-'+axis).value)/100;mutate();};
$('plot-solution').onchange=()=>{active.recipe.plot_solution=$('plot-solution').checked;mutate();};
$('mask-upload').onchange=safe(async()=>{
  const file=$('mask-upload').files[0],d=active;if(!file)return;
  if(file.size>8000000)throw Error('Choose a PNG or JPEG smaller than 8 MB.');
  const bitmap=await createImageBitmap(file);if(bitmap.width*bitmap.height>40000000){bitmap.close();throw Error('Use a mask smaller than 40 megapixels.');}
  const scale=Math.min(1,256/Math.max(bitmap.width,bitmap.height)),c=document.createElement('canvas');c.width=Math.max(1,Math.round(bitmap.width*scale));c.height=Math.max(1,Math.round(bitmap.height*scale));
  const ctx=c.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,c.width,c.height);ctx.drawImage(bitmap,0,0,c.width,c.height);bitmap.close();
  const data=ctx.getImageData(0,0,c.width,c.height).data,pixels=[];for(let i=0;i<data.length;i+=4)pixels.push(.2126*data[i]+.7152*data[i+1]+.0722*data[i+2]<128?1:0);
  const form=new FormData();form.append('mask',file);const stored=await request('/api/mazes/masks',{method:'POST',body:form});
  if(active!==d)throw Error('Return to the original document to attach this mask.');
  d.recipe.params.mask={width:c.width,height:c.height,pixels,name:file.name.slice(0,100)};d.recipe.mask_source=stored.hash;applyDetail(d.recipe);mutate({controls:true});$('mask-upload').value='';
});
$('remove-mask').onclick=()=>{active.recipe.params.mask=null;delete active.recipe.mask_source;applyDetail(active.recipe);mutate({controls:true});};
$('new-document').onclick=safe(()=>newDocument());
$('save').onclick=safe(()=>saveDoc());$('save-copy').onclick=safe(()=>saveDoc(active,true));$('open-library').onclick=safe(openLibrary);$('close-library').onclick=()=>$('library').close();$('library-search').oninput=renderLibrary;
$('revert').onclick=()=>{active.recipe=clone(active.savedRecipe);mutate({controls:true});};
$('undo').onclick=()=>history('undo');$('redo').onclick=()=>history('redo');$('show-margins').onchange=paint;
for(const id of ['paper','orientation','margins','margin'])$(id).onchange=safe(changePaper);
$('edit-inks').onclick=()=>{$('ink-drawer').hidden=false;document.body.classList.add('ink-drawer-open');$('edit-inks').setAttribute('aria-expanded','true');};
$('close-inks').onclick=()=>{$('palette')._inkTray?.cancel();$('ink-drawer').hidden=true;document.body.classList.remove('ink-drawer-open');$('edit-inks').setAttribute('aria-expanded','false');};
$('refresh-carousel').onclick=safe(async()=>{carousel=await request('/api/carousel');renderInks();});
$('prepare').onclick=safe(()=>prepare());$('open-plot').onclick=safe(()=>openStudio('hpgl',active.receipt.jobs[0].url));
$('edit-pdf').onclick=safe(()=>openStudio('pdf',active.receipt.review_url||'/pdf?document='+active.receipt.document));
$('download-pdf').onclick=safe(downloadPDF);$('download-svg').onclick=safe(downloadSVG);
bindPaperUnits();onPaperUnitChange(()=>{if(active){renderPaper();if(active.fitted&&!active.generating&&!active.preparing)finish(active);}});wireStudioTrays();
new ResizeObserver(paint).observe($('preview').parentElement);
window.addEventListener('beforeunload',event=>{if(documents.some(d=>dirty(d))){event.preventDefault();event.returnValue='';}});
window.addEventListener('keydown',event=>{
  if(!(event.ctrlKey||event.metaKey)||event.altKey)return;
  if(event.key.toLowerCase()==='s'){event.preventDefault();safe(()=>saveDoc(active,event.shiftKey))();}
  if(event.key.toLowerCase()==='z'&&!['INPUT','TEXTAREA'].includes(event.target.tagName)){event.preventDefault();history(event.shiftKey?'redo':'undo');}
});
async function init(){
  const results=await Promise.allSettled([request('/api/v1/capabilities'),request('/api/carousel')]);
  if(results[0].status!=='fulfilled')throw results[0].reason;capabilities=results[0].value;
  if(results[1].status==='fulfilled')carousel=results[1].value;else studioNotice('Carousel unavailable. New mazes use an editable black ink.');
  let saved;try{saved=JSON.parse(sessionStorage.getItem(workspaceKey)||localStorage.getItem(workspaceKey+'-backup')||'null');}catch{}
  if(saved?.documents?.length){documents=saved.documents.filter(d=>families[d.recipe?.family]&&d.recipe.version===1).map(d=>makeDoc(d.recipe,d));if(documents.length)await select(documents.find(d=>d.key===saved.active)||documents[0]);}
  const url=new URL(location.href);if(url.searchParams.has('document')||url.searchParams.has('source_recipe'))await openURL(url);
  if(!documents.length)await newDocument();nav.ready();document.body.dataset.ready='true';
  for(const doc of documents)if(doc.receipt&&!doc.receipt.ready&&doc.receipt.exportHash)safe(()=>prepare(doc,true))();
}
safe(init)();
