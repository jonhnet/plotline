import {familyGroups} from './groups.js';
import {createFamilyGallery} from './gallery.js';
import {families,familyFor,defaults,fitPaths,pageTransform,usesInkSequence,geometryBytes,limits,loadLimits,usesPhysicalWidths,refinementKey,physicalOptions} from './generators.js';
import {isComplex,complexWindow,complexPoint,navigateComplex,complexFrame,juliaShapes,newtonParams} from './complex.js';
import {renderPreviewControls,studioNotice,stable,renderDocumentTabs,renderDocumentLibrary,chooseDocumentName,confirmDocumentClose,editHistory,renderStudioProgress,wireStudioTrays} from '/studio.js';
import {studioNavigation,openStudio,studioTitle} from '/studio-navigation.js';
import {renderInkSets,penTargets,sameMaterial,newInkId,colorName,revealInk,renderInkSequence} from '/inksets.js';
import {bindPaperUnits,paperSize,paperLength,onPaperUnitChange,readPaperField,writePaperField} from '/paper-units.js';
import {turnSheet} from '/pdf-paper.js';
import {previewNavigation} from '/preview-navigation.js';
import {prepareAuthoredPlot,studioRequest,submissionID} from '/authoring-print.js';
import {wireRecipeFiles} from '/recipe-files.js';
import {validatePatternParameters} from './generators.js';

const $=id=>document.getElementById(id),clone=structuredClone,request=studioRequest;
renderPreviewControls($('preview-tools'),{fitId:'fit-view',onFit:()=>view.reset(),onZoom:factor=>view.zoomBy(factor)});
const safe=fn=>(...args)=>Promise.resolve().then(()=>fn(...args)).catch(error=>studioNotice(error.message,{kind:'error'}));
let documents=[],active=null,capabilities,carousel={revision:0,slots:{}},worker=null,timer=null,library=[];
const cache=new Map(),workspaceKey='plotline-pattern-workspace';
const inkFootprintKey=r=>usesPhysicalWidths(r.family)?stable([r.media,r.rotation,r.size,r.roles.map(role=>r.inks.find(i=>i.id===role.ink_id)?.width_mm)]):null;
const geometryKey=recipe=>stable([recipe.family,recipe.params,usesPhysicalWidths(recipe.family)?recipe.roles.map(r=>recipe.inks.find(i=>i.id===r.ink_id)?.width_mm):null,recipe.ink_sequence?.length,recipe.sampling_limit,(isComplex(recipe.family)||usesPhysicalWidths(recipe.family))?[recipe.media,recipe.inks.map(i=>i.width_mm),recipe.rotation,recipe.size]:null]);
const signature=doc=>stable(doc.recipe),dirty=doc=>signature(doc)!==doc.savedSignature;
const nav=studioNavigation({studio:'patterns',open:openURL});
let planeMode='inspect';
const view=previewNavigation($('preview'),{onChange:()=>paint(),onFit:()=>view.reset(),edit:{
  enabled:()=>isComplex(active?.recipe.family)&&planeMode!=='inspect',
  start:beginPlaneGesture,rebase:t=>{t.base=clone(t.doc.recipe.params);},move:movePlaneGesture,wheel:wheelPlaneGesture,end:endPlaneGesture,
}});
const progressElements={bar:$('work-progress'),detail:$('work-detail'),time:$('progress-time')};
function progress(percent,text,running=false){
  if(!active)return;
  renderStudioProgress(progressElements,{percent,text,running,received:performance.now(),elapsed:running?(performance.now()-(active.started||performance.now()))/1000:null});
}
function remember(){
  const state={active:active?.key,documents:documents.map(d=>({key:d.key,recipe:d.recipe,saved:d.saved,savedSignature:d.savedSignature,savedRecipe:d.savedRecipe,sourceHash:d.sourceHash,sourceSignature:d.sourceSignature,receipt:d.receipt}))};
  try{const value=JSON.stringify(state);sessionStorage.setItem(workspaceKey,value);localStorage.setItem(workspaceKey+'-backup',value);}catch{studioNotice('Browser storage is full. Save your patterns to keep this work.',{kind:'error'});}
}
function makeDoc(recipe,extra={}){
  const doc={key:submissionID(),...extra,recipe:clone(recipe)};
  if(doc.recipe.family==='newton')doc.recipe.params=newtonParams(doc.recipe.params);
  if(doc.recipe.family==='carpet'&&doc.recipe.params.round!==undefined){
    const exactSource=doc.sourceSignature===stable(doc.recipe);
    delete doc.recipe.params.round;
    if(exactSource)doc.sourceSignature=stable(doc.recipe);
  }
  doc.history=editHistory(doc.recipe);return doc;
}
function loadedInk(slot,pen){return {id:newInkId(),label:pen.label||colorName(pen.color),color_srgb:pen.color,width_mm:pen.width_mm,source:{kind:'plotline-carousel',revision:carousel.revision,slot:Number(slot)}};}
function initialInks(){
  const inks=[];
  for(const [slot,pen] of Object.entries(carousel.slots))if(pen.state==='loaded'&&pen.width_mm>0&&pen.color&&pen.color.toLowerCase()!=='#ffffff'){inks.push(loadedInk(slot,pen));}
  return inks.length?inks:[{id:'ink-black',label:'Black',color_srgb:'#000000',width_mm:.3}];
}
function baseMedia(profile){let media=clone(profile);if(media.sheet_size_mm[0]<media.sheet_size_mm[1])media=turnSheet(media);return media;}
function newRecipe(family){
  const profile=capabilities.media_profiles.find(p=>p.id==='hp7585-tabloid-landscape')||capabilities.media_profiles[0],inks=initialInks();
  const roles=usesInkSequence(family)?inks.map((ink,i)=>({label:`Sequence ink ${i+1}`,ink_id:ink.id})):roleNames(family,defaults(family)).map((label,i)=>({label,ink_id:inks[i%inks.length].id}));
  return {version:1,name:familyFor(family).name,family,params:defaults(family),inks:inks.filter(i=>roles.some(r=>r.ink_id===i.id)),roles,...(usesInkSequence(family)?{ink_sequence:roles.map(r=>r.ink_id)}:{}),media:baseMedia(profile),paper_id:profile.id,orientation:0,margin_mode:'plotter',custom_margin_mm:15,rotation:0,size:.96};
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
  const ready=!!d.fitted?.paths.length&&d.geometryKey===geometryKey(d.recipe)&&!d.generating,known=d.recipe.inks.every(i=>i.width_mm>0),exact=d.receipt?.signature===signature(d),prepared=exact&&d.receipt?.ready;
  $('prepare').disabled=!ready||!known||d.preparing;$('prepare').textContent=d.preparing?'Preparing…':d.printError?'Retry preparation':'Prepare plot';
  $('open-plot').disabled=!prepared;$('download-pdf').disabled=!ready||!known||d.preparing;$('download-svg').disabled=!ready;
  $('prepare').classList.toggle('primary',!prepared);$('open-plot').classList.toggle('primary',!!prepared);
  $('edit-pdf').disabled=!exact||!d.receipt?.document;
  $('plot-hint').textContent=!known?'Assign ink diameters under Pattern inks.':prepared?'Ready to check paper and pens.':'';
  $('pick-julia').disabled=!d.fitted||d.generating;
  $('cancel-preview').hidden=!d.generating;
  $('refine-preview').hidden=d.generating||(!d.recipe.sampling_limit&&!d.cancelled);$('refine-preview').textContent=d.recipe.sampling_limit?'Refine detail':'Resume rendering';
}
function mutate({controls=false}={}){
  if(active.recipe.family==='carpet')delete active.recipe.params.round;
  if(active.recipe.family==='newton')active.recipe.params=newtonParams(active.recipe.params);
  if(active.recipe.family==='newton'&&active.recipe.roles.length!==active.recipe.params.roots)resizeRootRoles(active.recipe);
  prune();active.history.record(active.recipe);active.printError=null;
  remember();if(controls)renderControls();chrome();schedule();
}
function chooseFamily(id){
  view.cancel();planeMode='inspect';
  const r=active.recipe,old=familyFor(r.family),f=familyFor(id);if(r.name===old.name)r.name=f.name;
  r.family=id;r.params=defaults(id);delete r.ink_sequence;delete r.sampling_limit;
  if(usesInkSequence(id)){r.ink_sequence=r.inks.map(i=>i.id);r.roles=r.inks.map((ink,i)=>({label:`Sequence ink ${i+1}`,ink_id:ink.id}));}
  else r.roles=roleNames(id,r.params).map((label,i)=>({label,ink_id:r.inks[i%r.inks.length].id}));mutate({controls:true});
}
function knob(host,k,value,change){
  if(k.choices){
    const label=document.createElement('label'),select=document.createElement('select');label.textContent=k.label;select.id='knob-'+k.key;select.setAttribute('aria-label',k.label);
    select.replaceChildren(...k.choices.map((name,i)=>new Option(name,String(k.min+i*k.step))));select.value=String(value);select.onchange=()=>change(Number(select.value));label.append(select);host.append(label);return;
  }
  const row=document.createElement('div');row.className='knob';
  const heading=document.createElement('label');heading.className='knob-heading';heading.htmlFor='knob-'+k.key;heading.textContent=k.label;
  const display=value=>k.key==='cutoff'?Number((value*100).toFixed(2))+'%':String(value);
  const output=document.createElement('output');output.textContent=display(value);heading.append(output);
  const controls=document.createElement('div');controls.className='knob-input';
  const input=document.createElement('input');Object.assign(input,{type:'range',id:'knob-'+k.key,min:k.min,max:k.max,step:k.step,value});input.setAttribute('aria-label',k.label);
  const minus=document.createElement('button'),plus=document.createElement('button');minus.textContent='−';plus.textContent='+';minus.setAttribute('aria-label','Decrease '+k.label);plus.setAttribute('aria-label','Increase '+k.label);
  const update=()=>{const value=Number(Number(input.value).toFixed(6));output.textContent=display(value);minus.disabled=value<=k.min;plus.disabled=value>=k.max;change(value);};
  input.oninput=update;minus.onclick=()=>{input.stepDown();update();};plus.onclick=()=>{input.stepUp();update();};minus.disabled=value<=k.min;plus.disabled=value>=k.max;
  controls.append(minus,input,plus);row.append(heading,controls);host.append(row);
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
    media={...media,id:'pattern-custom-margins',label:'Custom margins',printable_rect_mm:[m,m,media.sheet_size_mm[0]-2*m,media.sheet_size_mm[1]-2*m]};
  }
  r.media=media;active.recipe=r;mutate({controls:true});
}
function renderControls(){
  const r=active.recipe;$('family').value=r.family;$('knobs').replaceChildren();
  for(const k of familyFor(r.family).knobs.filter(k=>r.family!=='julia'||(k.key!=='detail'&&(k.key!=='shape'||!r.params.c))))knob($('knobs'),k,r.params[k.key],value=>{r.params[k.key]=value;delete r.sampling_limit;if(k.key==='location')delete r.params.viewport;if(k.key==='shape')delete r.params.c;if(k.key==='roots')resizeRootRoles(r);mutate();if(k.key==='roots')renderInks();if(['location','shape'].includes(k.key))renderPlaneControls();});
  $('composition-knobs').replaceChildren();
  knob($('composition-knobs'),{key:'rotation',label:'Rotation',min:-180,max:180,step:15},r.rotation,value=>{r.rotation=value;mutate();});
  knob($('composition-knobs'),{key:'size',label:'Size',min:.32,max:1,step:.04},r.size,value=>{r.size=value;mutate();});
  const notes={flowers:'Layered petal whorls with curved stems and leaf pairs. Inks cycle around the bloom.',schottky:'Paired Möbius transformations nest circles into fractal clusters. Generations and smallest-circle size both limit detail.',pinwheel:'Five smaller triangles replace each tile. Shared and partially shared edges are drawn once; motifs distinguish the two handednesses.',hatched_solids:'Surface hatches shade visible faces. Hatch ends account for both hatch and edge pen widths; copies stay separate.',lsystem:'Four prepared plant grammars. Generations grow branches; reach shortens side branches, and leaf tips are optional.',starweave:'Stars join across a polygon lattice. Strands alternate over and under; crossing clearance follows the actual pen widths.',guilloche:'Closed waves form rosettes or a rounded border ribbon. Twist shifts the wave phase between lines.',wave_contours:'Contours of two standing waves. Mode balance changes their interference; one band follows the nodal lines.',reaction:'A seeded reaction–diffusion simulation. Growth time changes its stage; inks follow concentration bands.',inversion:'Circle inversions repeat until the generation or smallest-circle limit. Inks follow sectors and generations.',ammann:'Squares and diamonds form an octagonal tiling. Shared edges are drawn once; nested motifs stay inside each tile.',colonization:'Branches grow toward nearby targets within the selected crown. Variation repeats exactly; density changes the target distribution.',architecture:'Stacked towers with hidden edges removed. Inks distinguish X, Y and vertical edges.',peano:'One continuous ternary walk. Inks share endpoints along the curve.',nested_polygons:'Each polygon sits inside the previous one. Edge step controls its twist and shrinkage.',pythagoras:'Square outlines branch around right triangles. Colors follow generations; overlapping squares remain visible.',lightning:'Seeded jagged paths with attached forks. Variation repeats exactly.',levy:'Two folded edges replace each segment. Color changes share endpoints.',minkowski:'Eight right-angled steps replace each edge. Island closes the curve around a square.',carpet:'Outlined openings of the carpet. Inks distinguish the eight recursive regions.',clifford:'Contour lines of smoothed point density. Softness blends nearby details; colors follow density levels.',dejong:'Contour lines of smoothed point density. Softness blends nearby details; colors follow density levels.',gosper:'One continuous curve; ink changes share endpoints. Rounding softens the corners.',dla:'Diffusing particles stick to the growing branches. Colors follow direction from the seed; Variation repeats exactly.',tetrahedron:'Four smaller tetrahedra at each level. Visible edges removes solid occlusion; wireframe shows every edge.',woven:'Crossing gaps follow the selected pen widths. Sequence inks color the ribbon strands.',barnsley:'Recursive stems stop at the generation limit or the smallest twig size.',crystal:'Seeded branches repeat symmetrically; colors follow arm and branch generation.',menger:'Coarse preview first; hidden edges refine in the background.',newton:'Depth limits the smaller basins. Spacing controls the outlines; crowded contours thinner than the selected pens are skipped.',apollonian:'Stops at either the generation limit or the minimum circle size, relative to the outer circle.',julia:'Sampling follows paper size and the finest selected pen.'};
  const note=notes[r.family]||(isComplex(r.family)?'Sampling follows paper size and the finest selected pen.':'');$('family-note').hidden=!note;$('family-note').textContent=note;
  renderPaper();renderInks();renderPlaneControls();
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
  if(usesInkSequence(r.family)){
    const selected=(r.ink_sequence||r.roles.map(role=>role.ink_id)).map(id=>r.inks.find(i=>i.id===id));
    const choices=Object.entries(carousel.slots).filter(([,p])=>p.state==='loaded'&&p.width_mm>0&&p.color&&p.color.toLowerCase()!=='#ffffff').map(([slot,p])=>{const ink=loadedInk(slot,p),existing=r.inks.find(i=>sameMaterial(i,ink));return {label:'Slot '+slot,ink:{...ink,id:existing?.id||ink.id}};});
    renderInkSequence($('palette'),{selected,choices,onChange:safe(inks=>{
      if(!inks.length)return;if(inks.length>64)throw Error('Choose at most 64 document inks.');
      const seen=new Set();r.inks=inks.map(ink=>{const id=ink.id&&!seen.has(ink.id)?ink.id:newInkId();seen.add(id);return {...ink,id};});r.ink_sequence=r.inks.map(i=>i.id);r.roles=r.inks.map((ink,i)=>({label:`Sequence ink ${i+1}`,ink_id:ink.id}));mutate();renderInks();
    })});
  }else renderInkSets($('palette'),{inks:roles,carousel,targets,mapping,roleLabels:true,hideEmptyCustomTargets:true,allowOrder:false,title:'Pattern roles',onAssign:(ids,t)=>assign(ids,t.slot?loadedInk(t.slot,t.ink):t.ink),onCustom:assign,customSeed:rs=>r.inks.find(i=>i.id===r.roles[Number(rs[0].id.slice(5))].ink_id),targetIssue:t=>t.slot&&t.ink.state!=='loaded'?'This slot is empty or unavailable.':(t.ink.color||t.ink.color_srgb)?.toLowerCase()==='#ffffff'?'Choose a colored ink.':''});
  $('ink-summary').replaceChildren(...r.inks.map(ink=>{const swatch=document.createElement('span');swatch.className='ink-chip';swatch.style.background=ink.color_srgb;swatch.title=ink.label+' · '+(ink.width_mm??'?')+' mm';return swatch;}));
  $('ink-warning').textContent=r.inks.some(i=>!i.width_mm)?'Choose a diameter for every pattern ink before preparing a plot.':'';
  $('inventory-status').textContent=usesInkSequence(r.family)?'Click pens to include or remove them. The numbered sequence repeats through layers, generations or curve sections.':`Loaded carousel snapshot · revision ${carousel.revision}. Select a role, then a pen to copy its design color and diameter.`;
}
function releaseFrame(d){d?.previewBitmap?.close();if(d)d.previewBitmap=null;}
function cacheGeometry(key,geometry){
  cache.delete(key);cache.set(key,geometry);
  let bytes=[...cache.values()].reduce((sum,g)=>sum+geometryBytes(g.paths.length,g.points),0);
  while(cache.size>1&&(cache.size>12||bytes>limits.working_memory_bytes/2)){
    const oldest=cache.keys().next().value,g=cache.get(oldest);bytes-=geometryBytes(g.paths.length,g.points);cache.delete(oldest);
  }
}
function bestCheckpoint(d,candidate){
  const previous=d.checkpoint;if(!previous)return candidate;
  const shape=p=>isComplex(d.recipe.family)?p:Object.fromEntries(Object.entries(p).filter(([key])=>key!==refinementKey(d.recipe.family)));
  const quality=c=>c.samplingResolution||c.params[refinementKey(d.recipe.family)]||0;
  // Rebuilding a coarse guide must not displace a finer completed result of
  // this same drawing. A changed camera/shape starts its own checkpoints.
  return stable(shape(previous.params))===stable(shape(candidate.params))&&quality(previous)>=quality(candidate)?previous:candidate;
}
function cancelPreview(message){
  const d=active,kept=d.checkpoint;stopWorker();releaseFrame(d);
  if(!kept){d.cancelled=true;progress(0,message||'Cancelled before a complete preview. Resume rendering to continue.');buttons();return;}
  d.recipe.params=clone(kept.params);
  if(d.recipe.family==='newton'&&d.recipe.roles.length!==d.recipe.params.roots){resizeRootRoles(d.recipe);prune();}
  if(kept.samplingResolution)d.recipe.sampling_limit=kept.samplingResolution;
  d.history.record(d.recipe);d.geometry=kept.geometry;d.geometryKey=geometryKey(d.recipe);d.checkpoint=null;d.cancelled=false;
  cacheGeometry(d.geometryKey,d.geometry);remember();renderControls();chrome();finish(d);
  progress(100,message||`Kept ${isComplex(d.recipe.family)?'completed refinement':d.recipe.params.depth!==undefined?'detail '+d.recipe.params.depth:'completed pattern'} · ${d.fitted.paths.length.toLocaleString()} paths`);
}
function stopWorker(){clearTimeout(timer);worker?.terminate();worker=null;if(active)active.generating=false;}
function schedule(){
  const d=active,key=geometryKey(d.recipe);
  if(d.generating&&d.requestKey===key){worker?.postMessage({type:'appearance',recipe:d.recipe});return;}
  clearTimeout(timer);
  if(cache.has(key)){stopWorker();d.geometry=cache.get(key);d.geometryKey=key;finish(d);return;}
  stopWorker();releaseFrame(d);d.checkpoint=d.completed?.family===d.recipe.family&&d.completed.inkCount===d.recipe.ink_sequence?.length&&d.completed.inkFootprintKey===inkFootprintKey(d.recipe)?d.completed:null;d.cancelled=false;d.generating=true;d.requestKey=key;d.geometryKey=null;d.fitted=null;d.started=performance.now();$('preview').dataset.ready='false';buttons();progress(0,'Generating '+familyFor(d.recipe.family).name,true);
  timer=setTimeout(()=>{
    worker=new Worker('/patterns/worker.js',{type:'module'});const current=worker;
    current.onmessage=({data})=>{
      if(current!==worker||d!==active||key!==geometryKey(d.recipe)){data.frame?.close();return;}
      if(data.progress){const p=data.progress;progress(p.fraction*95,p.label+(p.paths?` · ${p.paths.toLocaleString()} paths`:''),true);return;}
      if(data.frame){releaseFrame(d);d.previewBitmap=data.frame;paint();return;}
      if(data.checkpoint){d.checkpoint=bestCheckpoint(d,data.checkpoint);releaseFrame(d);d.fitted=fitPaths(d.checkpoint.geometry,d.recipe);paint();return;}
      d.generating=false;current.terminate();worker=null;
      if(data.error){cancelPreview(data.error);studioNotice(data.error,{kind:'error'});return;}
      releaseFrame(d);d.checkpoint=null;cacheGeometry(key,data.result);
      d.geometry=data.result;d.geometryKey=key;finish(d);
    };
    current.onerror=event=>{if(worker!==current)return;stopWorker();progress(0,'Pattern renderer could not start. Reload to retry.');buttons();studioNotice(event.message||'Pattern renderer could not start. Reload to retry.',{kind:'error'});};
    current.postMessage({family:d.recipe.family,params:d.recipe.params,recipe:d.recipe,options:{inkCount:d.recipe.ink_sequence?.length}});
  },100);
}
function finish(d){
  releaseFrame(d);d.cancelled=false;d.checkpoint=null;
  d.completed={geometry:d.geometry,params:clone(d.recipe.params),family:d.recipe.family,inkCount:d.recipe.ink_sequence?.length,inkFootprintKey:inkFootprintKey(d.recipe),samplingResolution:isComplex(d.recipe.family)?(d.geometry.sampling?.resolution||d.recipe.sampling_limit):undefined};
  try{d.fitted=fitPaths(d.geometry,d.recipe);}catch(error){d.fitted=null;progress(0,error.message);buttons();return;}
  paint();buttons();if(d.geometry.sampling){const q=d.geometry.sampling;$('family-note').textContent=`Sampling cells: ${paperLength(q.cell_mm,{precision:4})} · half-pen target: ${paperLength(q.target_mm,{precision:4})}${q.limited?' · sampling limit reached':''}.${d.geometry.ink_stats?.omitted_contours?' '+d.geometry.ink_stats.omitted_contours.toLocaleString()+' tiny or crowded contours skipped.':''}`;}renderPlaneControls();if(!d.preparing)progress(100,d.fitted.paths.length?`${d.fitted.paths.length.toLocaleString()} paths · ${paperLength(d.fitted.length_mm,{precision:0})} of strokes`:'No contours in this window. Pan, zoom out, or reset view.');
}
function roleNames(family,params){return family==='newton'?familyFor(family).roles.slice(0,params.roots):familyFor(family).roles;}
function resizeRootRoles(r){
  const available=initialInks();r.roles=roleNames(r.family,r.params).map((label,i)=>{
    if(r.roles[i])return {...r.roles[i],label};
    const candidate=available[i%available.length],ink=r.inks.find(ink=>sameMaterial(ink,candidate))||candidate;
    if(!r.inks.some(i=>i.id===ink.id))r.inks.push(ink);return {label,ink_id:ink.id};
  });
}
function renderPlaneControls(){
  const r=active.recipe,complex=isComplex(r.family);if(!complex)planeMode='inspect';
  $('complex-tools').hidden=!complex;$('complex-context').hidden=!complex;
  $('pick-julia').hidden=r.family!=='mandelbrot';
  $('explore-plane').setAttribute('aria-pressed',String(planeMode==='explore'));
  $('pick-julia').setAttribute('aria-pressed',String(planeMode==='pick'));
  $('preview').dataset.planeMode=planeMode;
  $('plane-instruction').textContent=planeMode==='pick'?'Click the drawing to open its Julia set in a new tab. Escape cancels.':planeMode==='explore'?'Drag to move the drawing window; scroll or pinch to zoom. Escape cancels a gesture.':'Explore changes the drawing window. Preview zoom only inspects the page.';
  if(complex){const v=complexWindow(r.family,r.params);$('plane-coordinates').textContent=`Center ${complexLabel([v.real,v.imag])} · width ${Number(v.span.toPrecision(6))}`;}
  $('julia-parameter').hidden=r.family!=='julia';$('julia-preset').hidden=!r.params.c;
  if(r.family==='julia'){
    const c=r.params.c||juliaShapes[r.params.shape];$('julia-real').value=c[0];$('julia-imag').value=c[1];
  }
}
function complexLabel([real,imag]){return `${Number(real.toPrecision(8))} ${imag<0?'−':'+'} ${Number(Math.abs(imag).toPrecision(8))}i`;}
// Invert the same paper placement and inspection transform used to paint.
function planeCoordinateReader(doc){
  const canvas=$('preview'),box=canvas.getBoundingClientRect(),navigation=view.state,r=doc.recipe,[pw,ph]=r.media.sheet_size_mm;
  const s=Math.min((box.width-60)/pw,(box.height-55)/ph)*navigation.scale;
  const t=pageTransform(doc.geometry||{paths:[],frame:complexFrame},r);
  return ([cx,cy])=>{
    const x=((cx-box.left-box.width/2-navigation.x)/s+pw/2-t.ox)/t.scale;
    const y=((cy-box.top-box.height/2-navigation.y)/s+ph/2-t.oy)/t.scale;
    return [x*t.cos+y*t.sin,-x*t.sin+y*t.cos];
  };
}
function beginPlaneGesture(kind,event){
  const t={doc:active,original:clone(active.recipe),base:clone(active.recipe.params),read:planeCoordinateReader(active),mode:planeMode,start:[event.clientX,event.clientY],moved:false};
  if(kind==='wheel'&&t.mode==='pick')t.mode='inspect';return t;
}
function changeWindow(t,viewport){
  if(t.doc!==active)return;
  if(Math.abs(viewport.real)>100||Math.abs(viewport.imag)>100)return;
  active.recipe.params.viewport=viewport;delete active.recipe.sampling_limit;active.printError=null;
  renderPlaneControls();schedule();chrome();
}
function movePlaneGesture(t,{dx,dy,factor,anchor}){
  t.moved ||= Math.hypot(dx,dy)>4||Math.abs(factor-1)>.01;
  if(t.mode!=='explore'||!t.moved)return;
  const a=t.read(anchor),b=t.read([anchor[0]+dx,anchor[1]+dy]);
  changeWindow(t,navigateComplex(t.doc.recipe.family,t.base,{anchor:a,delta:b.map((v,i)=>v-a[i]),factor}));
}
function wheelPlaneGesture(t,factor,point){
  if(t.mode==='inspect'){view.zoomBy(factor,point);return;}
  if(t.mode==='explore'){t.moved=true;changeWindow(t,navigateComplex(t.doc.recipe.family,t.doc.recipe.params,{anchor:t.read(point),factor}));}
}
function endPlaneGesture(t,cancel){
  if(t.doc!==active)return;
  if(t.mode==='pick'){
    if(!cancel&&!t.moved){const point=t.read(t.start);if(point.every(v=>Math.abs(v)<=1))safe(()=>openJuliaAt(complexPoint('mandelbrot',t.original.params,point)))();}
    return;
  }
  if(t.mode!=='explore'||!t.moved)return;
  if(cancel){active.recipe=t.original;renderControls();chrome();schedule();}
  else mutate();
}
async function openJuliaAt(c){
  const source=active.recipe,recipe=clone(source);
  recipe.family='julia';recipe.name=`Julia · ${complexLabel(c)}`;recipe.params={...defaults('julia'),c};delete recipe.sampling_limit;
  // Retain the source's inks, paper and composition; a new document owns c.
  const doc=makeDoc(recipe);documents.push(doc);await select(doc);
}
function togglePlane(mode){view.cancel();planeMode=planeMode===mode?'inspect':mode;renderPlaneControls();$('preview').focus();}

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
function paint(){draw($('preview'),active?.recipe,active?.fitted,{navigation:view.state,margin:$('show-margins').checked,bitmap:active?.previewBitmap});$('preview').dataset.ready=String(!!active?.fitted&&!active?.generating&&active.geometryKey===geometryKey(active.recipe));}
function thumbnail(doc){if(!doc.fitted||doc.geometryKey!==geometryKey(doc.recipe))return '';const c=document.createElement('canvas');c.width=400;c.height=280;draw(c,doc.recipe,doc.fitted,{thumbnail:true});return c.toDataURL('image/png');}
async function select(doc){view.cancel();planeMode='inspect';stopWorker();if(active&&active!==doc){releaseFrame(active);active.geometry=null;active.fitted=null;active.checkpoint=null;active.completed=null;active.geometryKey=null;}active=doc;view.reset();renderControls();chrome();remember();schedule();}
async function newDocument(family='koch'){const doc=makeDoc(newRecipe(family));documents.push(doc);await select(doc);}
async function closeDoc(doc){
  if(dirty(doc)){
    const choice=await confirmDocumentClose($('close-dialog'),$('close-message'),doc.recipe.name);
    if(choice==='cancel')return;if(choice==='save'&&!await saveDoc(doc))return;
  }
  if(doc.preparing){studioNotice('Preparation is still running. Keep this document open until the plot is ready.');return;}
  if(doc===active)stopWorker();documents=documents.filter(d=>d!==doc);
  if(!documents.length)await newDocument();else await select(doc===active?documents.at(-1):active);remember();
}
async function saveDoc(doc=active,copy=false){
  let name=doc.recipe.name;
  if(!doc.saved||copy){
    if(copy){const result=await request('/api/patterns/documents');const names=new Set(result.documents.map(d=>d.name));let n=2;const base=name.replace(/ \(\d+\)$/,'');name=base+' (2)';while(names.has(name))name=base+` (${++n})`;}
    name=await chooseDocumentName({dialog:$('save-dialog'),form:$('save-form'),input:$('save-name'),heading:$('save-heading'),cancel:$('cancel-save')},name,copy);if(!name)return false;
  }
  doc.recipe.name=name;doc.history.record(doc.recipe);const recipe=clone(doc.recipe);doc.saving=true;chrome();remember();
  try{
    const saved=await request('/api/patterns/documents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({recipe,thumbnail:thumbnail(doc),...(doc.saved&&!copy?{id:doc.saved.id,revision:doc.saved.revision}:{})})});
    doc.saved={id:saved.id,revision:saved.revision};doc.savedRecipe=recipe;doc.savedSignature=stable(recipe);remember();return true;
  }finally{doc.saving=false;chrome();}
}
async function openSaved(id){
  const existing=documents.find(d=>d.saved?.id===id);if(existing){await select(existing);return;}
  const row=await request('/api/patterns/documents/'+encodeURIComponent(id));const doc=makeDoc(row.recipe,{saved:{id:row.id,revision:row.revision},savedSignature:stable(row.recipe),savedRecipe:clone(row.recipe)});documents.push(doc);await select(doc);
}
async function openURL(url){
  const id=url.searchParams.get('document'),hash=url.searchParams.get('source_recipe'),snapshot=url.searchParams.has('snapshot'),substitutions=url.searchParams.get('pen_substitutions');
  if(id&&!snapshot&&!substitutions){await openSaved(id);return;}
  if(hash){
    const existing=!substitutions&&documents.find(d=>d.sourceHash===hash&&stable(d.recipe)===d.sourceSignature);if(existing){await select(existing);return;}
    const row=await request('/api/patterns/snapshots/'+encodeURIComponent(hash)),recipe=clone(row.recipe),changes=JSON.parse(substitutions||'{}');
    for(const ink of recipe.inks){const change=changes[ink.id];if(change){if(!/^#[0-9a-f]{6}$/i.test(change.color_srgb)||change.color_srgb.toLowerCase()==='#ffffff'||typeof change.width_mm!=='number'||!Number.isFinite(change.width_mm)||change.width_mm<.01||change.width_mm>5)throw Error('Invalid pen substitution');Object.assign(ink,{color_srgb:change.color_srgb,width_mm:change.width_mm,label:typeof change.label==='string'?change.label:ink.label});delete ink.source;}}
    const doc=makeDoc(recipe,{sourceHash:hash,sourceSignature:stable(recipe)});documents.push(doc);await select(doc);
    if(substitutions)studioNotice('Pen substitution applied to a separate source pattern. Review it, then Prepare plot.');
    else if(snapshot)studioNotice(recipe.family==='carpet'&&recipe.params.round!==undefined?'Opened source pattern with square carpet corners. The earlier exported plot is unchanged.':'Opened the exact source pattern as a separate document.');
  }
}
async function openLibrary(){library=(await request('/api/patterns/documents')).documents;renderLibrary();$('library').showModal();}
function renderLibrary(){renderDocumentLibrary($('documents'),{items:library.map(r=>({id:r.id,title:r.name,thumbnail_url:r.thumbnail,detail:new Date(r.updated_at*1000).toLocaleString()})),query:$('library-search').value,isOpen:r=>documents.some(d=>d.saved?.id===r.id),open:async r=>{await openSaved(r.id);$('library').close();},safe});}
wireRecipeFiles({dialog:$('library'),button:$('import-recipe'),input:$('recipe-file'),error:$('recipe-error'),download:$('download-recipe'),getRecipe:()=>active?.recipe,
  studio:'Pattern',familyIds:families.map(f=>f.id),
  validate:r=>{
    const params=validatePatternParameters(r.family,r.params);
    if(!Number.isFinite(r.rotation)||r.rotation < -180||r.rotation>180||!Number.isFinite(r.size)||r.size<=0||r.size>1)throw Error('Invalid pattern rotation or size.');
    if(r.sampling_limit!==undefined&&(!Number.isInteger(r.sampling_limit)||r.sampling_limit<128||r.sampling_limit>3072))throw Error('Invalid saved sampling resolution.');
    if(r.ink_sequence!==undefined){
      if(!usesInkSequence(r.family)||!Array.isArray(r.ink_sequence)||r.ink_sequence.length!==r.roles.length||r.ink_sequence.some((id,i)=>id!==r.roles[i].ink_id))throw Error('The ink sequence must match the drawing roles.');
    }else if(r.roles.length!==roleNames(r.family,params).length)throw Error('The recipe is missing drawing roles for this pattern.');
  },
  open:async recipe=>{const doc=makeDoc(recipe);documents.push(doc);await select(doc);},
});
const familyGallery=createFamilyGallery({dialog:$('gallery'),tabs:$('family-groups'),panel:$('family-gallery'),families,onChoose:safe(newDocument)});
function gallery(){familyGallery.open(active?.recipe.family);}
async function exportDoc(doc){
  const sig=signature(doc);if(doc.export?.signature===sig)return doc.export.record;
  if(!doc.fitted||doc.generating||doc.geometryKey!==geometryKey(doc.recipe))throw Error('Wait for the pattern preview.');
  const recipe=clone(doc.recipe),paths=doc.fitted.paths;
  const record=await request('/api/patterns/exports',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({recipe,paths,...(doc.saved?{document_id:doc.saved.id}:{})})});
  doc.export={signature:sig,record};return record;
}
async function prepare(doc=active,resume=false){
  if(doc.preparing)return;doc.preparing=true;doc.printError=null;doc.started=performance.now();chrome();
  const sig=signature(doc);
  try{
    if(doc===active)progress(5,'Exporting drawing',true);
    let record;
    if(resume&&doc.receipt?.exportHash)record=await request('/api/patterns/snapshots/'+doc.receipt.exportHash);
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

$('explore-plane').onclick=()=>togglePlane('explore');$('pick-julia').onclick=()=>togglePlane('pick');
$('reset-plane').onclick=()=>{view.cancel();delete active.recipe.params.viewport;delete active.recipe.sampling_limit;mutate({controls:true});};
$('julia-preset').onclick=()=>{delete active.recipe.params.c;delete active.recipe.sampling_limit;mutate({controls:true});};
for(const id of ['julia-real','julia-imag'])$(id).onchange=safe(()=>{const c=[Number($('julia-real').value),Number($('julia-imag').value)];if(c.some(v=>!Number.isFinite(v)||Math.abs(v)>100))throw Error('Julia parameters must be between −100 and 100.');active.recipe.params.c=c;delete active.recipe.sampling_limit;mutate({controls:true});});
$('preview').addEventListener('keydown',event=>{if(event.key==='Escape'){planeMode='inspect';renderPlaneControls();}});

$('family').replaceChildren(...familyGroups.map(g=>{const group=document.createElement('optgroup');group.label=g.name;group.append(...g.families.map(id=>new Option(familyFor(id).name,id)));return group;}));$('family').onchange=()=>chooseFamily($('family').value);
$('new-document').onclick=safe(gallery);$('browse').onclick=safe(gallery);$('close-gallery').onclick=()=>$('gallery').close();
$('reset-preset').onclick=()=>{active.recipe.params=defaults(active.recipe.family);delete active.recipe.sampling_limit;mutate({controls:true});};
$('save').onclick=safe(()=>saveDoc());$('save-copy').onclick=safe(()=>saveDoc(active,true));$('open-library').onclick=safe(openLibrary);$('close-library').onclick=()=>$('library').close();$('library-search').oninput=renderLibrary;
$('revert').onclick=()=>{active.recipe=clone(active.savedRecipe);mutate({controls:true});};
$('undo').onclick=()=>history('undo');$('redo').onclick=()=>history('redo');$('show-margins').onchange=paint;
$('cancel-preview').onclick=()=>cancelPreview();
$('refine-preview').onclick=()=>{delete active.recipe.sampling_limit;mutate({controls:true});};
for(const id of ['paper','orientation','margins','margin'])$(id).onchange=safe(changePaper);
$('edit-inks').onclick=()=>{$('ink-drawer').hidden=false;document.body.classList.add('ink-drawer-open');$('edit-inks').setAttribute('aria-expanded','true');};
$('close-inks').onclick=()=>{$('palette')._inkTray?.cancel();$('ink-drawer').hidden=true;document.body.classList.remove('ink-drawer-open');$('edit-inks').setAttribute('aria-expanded','false');};
$('refresh-carousel').onclick=safe(async()=>{carousel=await request('/api/carousel');renderInks();});
$('prepare').onclick=safe(()=>prepare());$('open-plot').onclick=safe(()=>openStudio('hpgl',active.receipt.jobs[0].url));
$('edit-pdf').onclick=safe(()=>openStudio('pdf',active.receipt.review_url||'/pdf?document='+active.receipt.document));
$('download-pdf').onclick=safe(downloadPDF);$('download-svg').onclick=safe(downloadSVG);
bindPaperUnits();onPaperUnitChange(()=>{if(active){renderPaper();if(active.fitted&&!active.generating&&!active.preparing&&active.geometryKey===geometryKey(active.recipe))finish(active);}});wireStudioTrays();
new ResizeObserver(paint).observe($('preview').parentElement);
window.addEventListener('beforeunload',event=>{if(documents.some(d=>dirty(d))){event.preventDefault();event.returnValue='';}});
window.addEventListener('keydown',event=>{
  if(!(event.ctrlKey||event.metaKey)||event.altKey)return;
  if(event.key.toLowerCase()==='s'){event.preventDefault();safe(()=>saveDoc(active,event.shiftKey))();}
  if(event.key.toLowerCase()==='z'&&!['INPUT','TEXTAREA'].includes(event.target.tagName)){event.preventDefault();history(event.shiftKey?'redo':'undo');}
});
async function init(){
  await loadLimits();
  const results=await Promise.allSettled([request('/api/v1/capabilities'),request('/api/carousel')]);
  if(results[0].status!=='fulfilled')throw results[0].reason;capabilities=results[0].value;
  if(results[1].status==='fulfilled')carousel=results[1].value;else studioNotice('Carousel unavailable. New patterns use an editable black ink.');
  let saved;try{saved=JSON.parse(sessionStorage.getItem(workspaceKey)||localStorage.getItem(workspaceKey+'-backup')||'null');}catch{}
  if(saved?.documents?.length){
    documents=saved.documents.filter(d=>familyFor(d.recipe?.family)&&d.recipe.version===1).map(d=>makeDoc(d.recipe,d));
    if(documents.length)await select(documents.find(d=>d.key===saved.active)||documents[0]);
  }
  const url=new URL(location.href);
  if(url.searchParams.has('document')||url.searchParams.has('source_recipe'))await openURL(url);
  if(!documents.length)await newDocument();
  nav.ready();document.body.dataset.ready='true';
  for(const doc of documents)if(doc.receipt&&!doc.receipt.ready&&doc.receipt.exportHash)safe(()=>prepare(doc,true))();
}
safe(init)();
