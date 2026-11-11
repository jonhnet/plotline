import {bindPaperUnits,onPaperUnitChange,paperSize,readPaperField,writePaperField} from '/paper-units.js';
import {studioNavigation,openStudio,studioURL,mapSourceURL} from '/studio-navigation.js';
import {renderPreviewControls,renderDocumentTabs,renderDocumentLibrary,chooseDocumentName,confirmDocumentClose,renderStudioProgress,studioNotice} from '/studio.js';
import {previewNavigation} from '/preview-navigation.js';
import {renderInkSets,penTargets,sameMaterial,newInkId,revealInk,colorName,colorMatches} from '/inksets.js';
import * as paper from './paper.js';
const mapPrefix=location.pathname.startsWith('/map')?'/map':'';
function mapURL(value){if(!value)return value;const u=new URL(value,location.origin);if(u.origin===location.origin&&/^\/(api|artifacts)\//.test(u.pathname))return mapPrefix+u.pathname+u.search+u.hash;return value;}
const $ = id => document.getElementById(id);
renderPreviewControls($('preview-tools'),{undoId:'undo-map',redoId:'redo-map',onFit:()=>navigation.reset(),onZoom:factor=>navigation.zoomBy(factor)});
bindPaperUnits();
const clone = value => JSON.parse(JSON.stringify(value));
const uid = () => globalThis.crypto?.randomUUID?.() || `map-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const session = uid();
const roleNames={labels:'Labels',roads:'Roads',bridges:'Bridges',buildings:'Buildings',parks:'Parks',water:'Water',terrain:'Contours',silhouettes:'Silhouettes',hillshade:'Hillshade'};
let catalog, recipe, saved = null, capabilities = null, revision = 0, epoch = 0;
let timer, active = null, full = null, scene = null, sketchFrame = 0;
let printEpoch=0,printTask=null,printPointer=null,refining=false,printRestoration=null;
let workspaceDocs=[],activeDoc=null,previewSnapshot=null,libraryItems=[],libraryEpoch=0;
let paperRefreshing=false;
let updatingMapInks=false;
const printActive=new Set(['rendering','handoff','queued','uploading','inspecting','submitting','converting','preparing']);
const stable=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
async function api(path, body, controller=new AbortController()) {
  const timeout=setTimeout(()=>controller.abort(),30000);
  try{
    const response=await fetch(mapURL(path),{signal:controller.signal,...(body===undefined?{}:{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)
    })});
    const result=await response.json();
    if(!response.ok)throw Object.assign(new Error(result.error||`Request failed (${response.status})`),{status:response.status});
    return result;
  }catch(error){if(error.name==='AbortError')throw new Error('Request timed out or was cancelled. Retry to reconnect.');throw error;}
  finally{clearTimeout(timeout);}
}
function notice(message='',expires=0) { return studioNotice(message,{timeout:expires}); }
// Preview and durable plot preparation share one progress surface. Background
// preview restoration must never replace an active or completed plot receipt.
let statusView=null,printStatusView=null,lastQuality='draft',sceneController=null,previewStartedAt=null;
let overallPercent=0;
function duration(seconds){const s=Math.max(0,Math.floor(seconds));return s<60?`${s}s`:`${Math.floor(s/60)}m ${s%60}s`;}
function stage(state){$('desk').dataset.previewState=state;}
function renderProgress(){
  const isPlot=Boolean(printStatusView&&(printTask||printConnectionLost));
  const view=isPlot?printStatusView:statusView;
  if(!view)return;
  renderStudioProgress({bar:$('work-progress'),detail:$('work-detail'),time:$('progress-time')},view);
  $('work-status').dataset.owner=isPlot?'plot':'preview';
  $('work-status').dataset.phase=view.phase||'';
  if($('work-count').textContent!==(view.count||''))$('work-count').textContent=view.count||'';
  $('retry-preview').hidden=Boolean(printTask)||!view.retry;
}
function showProgress(job,imageReady=false){
  const p=job.progress||{},running=!imageReady&&['queued','running','ready'].includes(job.status);
  const prefix=job.quality==='proof'?'Creating PDF':'Drawing preview';
  overallPercent=imageReady?100:Math.max(overallPercent,Math.min(99,p.overall_percent??(p.stage==='image'||job.status==='ready'?99:0)));
  statusView={running,received:performance.now(),percent:overallPercent,phase:imageReady?'complete':p.stage||'starting',
    elapsed:Math.max(job.elapsed_seconds||0,previewStartedAt===null?0:(performance.now()-previewStartedAt)/1000),
    text:imageReady?(job.quality==='proof'?'PDF complete':'Preview complete'):`${prefix}: ${p.detail||'starting'}`,
    count:p.total>0&&Number.isFinite(p.completed)?`${p.completed.toLocaleString()} / ${p.total.toLocaleString()} ${p.unit||''}`:''};
  stage(imageReady?'ready':'working');$('desk').dataset.previewQuality=job.quality||lastQuality;
  renderProgress();
}
function idleProgress(label,detail='',retry=false){
  const complete=['Preview complete','PDF complete'].includes(label);
  statusView={running:false,received:performance.now(),percent:complete?100:retry?(statusView?.percent||0):0,
    elapsed:complete?null:retry&&statusView?statusView.elapsed+(statusView.running?(performance.now()-statusView.received)/1000:0):null,
    text:detail?`${label}: ${detail}`:label,retry,phase:complete?'complete':retry?'error':'waiting'};
  stage(complete?'ready':retry?'error':'working');renderProgress();
}
setInterval(renderProgress,500);
window.addEventListener('pagehide',()=>{
  if((active&&['queued','running'].includes(active.status))||active?.pending){
    fetch(mapURL('/api/cancel'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session,revision:++revision}),keepalive:true}).catch(()=>{});
  }
});

function safely(fn) { return (...args) => Promise.resolve().then(()=>fn(...args)).catch(error=>notice(error.message)); }
function stashDocument(){
  if(activeDoc)Object.assign(activeDoc,{recipe,saved,scene,full,preview:previewSnapshot,printPointer,
    controlsScroll:document.querySelector('.controls').scrollTop,styleRegion:$('style-region').value,viewMode:$('view-mode').value});
}
function remember(){
  stashDocument();
  try{
    const state=JSON.stringify({version:1,active:activeDoc?.key,documents:workspaceDocs.map(d=>({key:d.key,recipe:d.recipe,saved:d.saved,baseline:d.baseline,needsSave:d.needsSave,preview:d.preview,printPointer:d.printPointer,sourceRecipe:d.sourceRecipe,sourceSnapshot:d.sourceSnapshot}))});
    sessionStorage.setItem('plotline-map-workspace',state);localStorage.setItem('plotline-map-workspace',state);
    localStorage.setItem('plotline-map-draft',JSON.stringify({recipe,saved}));
  }catch{notice('Browser storage is full or unavailable. Save your documents before leaving this page.');}
}
const documentName=doc=>doc.saved?.name||(doc.recipe.title||'Untitled map')+(doc.sourceSnapshot?' · source version':'');
const documentDirty=doc=>doc.saved?stable(doc.recipe)!==doc.saved.signature:Boolean(doc.needsSave||stable(doc.recipe)!==doc.baseline);
function documentState(){
  if(!activeDoc)return;
  stashDocument();
  $('saved-state').textContent=activeDoc.saving?'Saving…':documentDirty(activeDoc)?'Unsaved changes':saved?'Saved':'Not saved';
  $('save').textContent=activeDoc.saving?'Saving…':'Save';
  $('save').disabled=Boolean(activeDoc.saving);$('save-copy').disabled=Boolean(activeDoc.saving);
  $('revert').disabled=Boolean(activeDoc.saving)||!saved||!documentDirty(activeDoc);
  studioNav.title(documentName(activeDoc)+(documentDirty(activeDoc)?' *':''));
  studioNav.setSources(activeDoc.sourceRecipe?[{studio:'map',label:'View source map version',url:mapSourceURL({recipe_hash:activeDoc.sourceRecipe},{snapshot:true})}]:[]);
  renderDocumentTabs($('document-tabs'),{documents:workspaceDocs,active:activeDoc,name:documentName,dirty:documentDirty,select:switchDocument,close:closeDocument,safe:safely});
}
function makeDocument(value,snapshot=null,needsSave=false){
  return {key:uid(),recipe:clone(value),saved:snapshot,baseline:stable(value),needsSave,preview:null,printPointer:null};
}
function switchDocument(doc){
  if(doc===activeDoc)return;
  navigation.cancel();stashDocument();pauseDraft();printEpoch++;printTask=null;printRestoration=null;printConnectionLost=false;printPercent=0;printStatusView=null;
  activeDoc=doc;recipe=doc.recipe;saved=doc.saved;scene=doc.scene||null;full=doc.full||null;previewSnapshot=doc.preview||null;printPointer=doc.printPointer||null;
  $('style-region').value=doc.styleRegion||'inside';$('view-mode').value=doc.viewMode||'ground';
  document.querySelector('.controls').scrollTop=doc.controlsScroll||0;
  history.replaceState(null,'',location.pathname+(saved?'#recipe='+saved.id:''));
  activate();remember();printRestoration=restorePrint();
}
function addDocument(value,snapshot=null,needsSave=false){const doc=makeDocument(value,snapshot,needsSave);workspaceDocs.push(doc);switchDocument(doc);return doc;}
async function closeDocument(doc){
  stashDocument();if(doc.saving)return;
  if(documentDirty(doc)){
    const choice=await confirmDocumentClose($('close-document-dialog'),$('close-document-message'),documentName(doc));
    if(choice==='cancel'||!choice)return;
    if(choice==='save'&&(!await saveRecipe(false,doc)||documentDirty(doc)))return;
  }
  const index=workspaceDocs.indexOf(doc);workspaceDocs.splice(index,1);
  if(doc===activeDoc){
    if(!workspaceDocs.length)workspaceDocs.push(makeDocument(newMapRecipe()));
    switchDocument(workspaceDocs[Math.min(index,workspaceDocs.length-1)]);
  }else{remember();documentState();}
}
window.addEventListener('beforeunload',event=>{if(document.activeElement?.matches('input,select'))document.activeElement.blur();stashDocument();if(workspaceDocs.some(d=>documentDirty(d)||d.saving)){event.preventDefault();event.returnValue='';}});
onPaperUnitChange(()=>{if(recipe&&catalog)fillMedia({refreshFields:false});});
function currentStyle(){return $('style-region').value==='outside'&&recipe.boundary?recipe.outside:recipe;}
const RAD=Math.PI/180,R=6378137;
const world=([lon,lat])=>[R*lon*RAD,R*Math.log(Math.tan(Math.PI/4+Math.max(-85,Math.min(85,lat))*RAD/2))];
const geo=([x,y])=>[((x/R/RAD+180)%360+360)%360-180,Math.max(-84,Math.min(84,(2*Math.atan(Math.exp(y/R))-Math.PI/2)/RAD))];
function fillMedia({refreshFields=true}={}) {
  const current=paper.paperFor(recipe.media,catalog.paper_sizes),groups=new Map();
  $('paper-size').replaceChildren();
  for(const p of catalog.paper_sizes){
    if(!groups.has(p.group)){const group=document.createElement('optgroup');group.label=p.group;groups.set(p.group,group);$('paper-size').append(group);}
    groups.get(p.group).append(new Option(p.label.split(' · ')[0]+' · '+paperSize(p.size_mm),p.id));
  }
  if(current.id==='saved-size')$('paper-size').append(new Option(current.label,current.id));
  $('paper-size').value=current.id;
  $('paper-orientation').value=paper.orientation(recipe.media);
  const mode=paper.marginMode(recipe.media),plotter=paper.plotterProfile(recipe.media.sheet_size_mm,catalog,capabilities,paper.orientation(recipe.media));
  $('paper-margins').value=mode;$('paper-margins').querySelector('[value="plotter"]').disabled=!plotter;
  $('custom-margins').hidden=mode!=='custom';
  const edges=paper.margins(recipe.media);
  ['top','right','bottom','left'].forEach((edge,i)=>{
    if(refreshFields)writePaperField($('margin-'+edge),edges[i]);$('margin-'+edge).onchange=safely(applyPaper);
  });
  for(const id of ['paper-size','paper-orientation','paper-margins'])$(id).onchange=safely(applyPaper);
  $('refresh-paper').hidden=mode!=='plotter';$('refresh-paper').disabled=paperRefreshing||!plotter;
  $('refresh-paper').textContent=paperRefreshing?'Reading plotter margins…':'Refresh margins from plotter';
  $('refresh-paper').onclick=safely(refreshPaper);
  $('media-note').hidden=!paper.outsidePlotter(recipe.media,plotter);
  $('media-note').textContent=$('media-note').hidden?'':'Custom margins extend beyond the plotter’s printable area.';
}
function applyPaper(){
  try{
    const selected=catalog.paper_sizes.find(p=>p.id===$('paper-size').value)||paper.paperFor(recipe.media,catalog.paper_sizes);
    const choice=$('paper-orientation').value;
    const size=choice.startsWith('portrait')?[...selected.size_mm]:[...selected.size_mm].reverse();
    const plotter=paper.plotterProfile(size,catalog,capabilities,choice);
    let media;
    if($('paper-margins').value==='plotter'){
      if(!plotter)throw Error('No plotter margins are available for this paper size.');
      media=clone(plotter);
    }else{
      const edges=['top','right','bottom','left'].map(edge=>readPaperField($('margin-'+edge)));
      media=paper.customProfile(size,edges,recipe.media.content_inset_mm,plotter,choice);
    }
    if(stable(media)!==stable(recipe.media)){recipe.media=media;mutated();sync();}
    else fillMedia();
    notice();
  }catch(error){fillMedia();throw error;}
}
async function refreshPaper(){
  const doc=activeDoc,snapshot=stable(recipe.media);
  paperRefreshing=true;fillMedia();
  try{
    await refreshInventory();
    if(capabilities.offline)throw Error('Plotline Studios is unavailable. The saved margins are unchanged.');
    if(doc===activeDoc&&snapshot===stable(recipe.media)&&paper.marginMode(recipe.media)==='plotter')applyPaper();
  }finally{paperRefreshing=false;fillMedia();}
}
function sync() {
  documentState();
  $('title').value=recipe.title;fillMedia();
  for(const key of Object.keys(recipe.furniture))$('furniture-'+key).checked=recipe.furniture[key];
  $('scale').value=+recipe.framing.scale_denominator.toFixed(2);
  writePaperField($('offset-x'),recipe.framing.offset_mm[0]);writePaperField($('offset-y'),recipe.framing.offset_mm[1]);
  $('longitude').value=+recipe.center[0].toFixed(6);$('latitude').value=+recipe.center[1].toFixed(6);
  $('extent').value=recipe.extent;$('bounds-controls').hidden=recipe.extent!=='bounds';
  ['west','south','east','north'].forEach((id,i)=>$(id).value=+recipe.bounds[i].toFixed(6));
  [['azimuth','azimuth_deg','°'],['elevation','elevation_deg','°'],['exaggeration','exaggeration','×']].forEach(([id,key,unit])=>{
    $(id).value=recipe.camera[key];$(id+'-value').textContent=recipe.camera[key]+unit;
  });
  $('relief').checked=recipe.camera.relief;$('relief-controls').hidden=!recipe.camera.relief;
  $('style-region-field').hidden=!recipe.boundary;
  $('ink-region-field').hidden=!recipe.boundary;
  if(!recipe.boundary)$('style-region').value='inside';
  $('ink-region').value=$('style-region').value;
  $('boundary-status').textContent=recipe.boundary?recipe.boundary.name+' · snapshot saved in recipe':'No boundary selected.';
  $('boundary-clear').disabled=!recipe.boundary;
  $('role-region').textContent=recipe.boundary?($('style-region').value==='outside'?'· outside':'· inside'):'';
  const f=currentStyle().features;
  ['roads','shore','labels','buildings'].forEach(id=>$(id).value=f[id]);
  ['bridges','parks','contours','hillshade'].forEach(id=>$(id).checked=f[id]);
  $('park-labels').checked=f.park_labels;$('peak-labels').checked=f.peak_labels;$('terrain-edges').checked=f.terrain_edges;$('terrain-edges').disabled=!recipe.camera.relief;
  $('hillshade-controls').hidden=!f.hillshade;$('hillshade-spacing').value=f.hillshade_spacing_mm;
  for(const [id,key] of [['sun-azimuth','azimuth_deg'],['sun-elevation','elevation_deg']]){
    $(id).value=recipe.lighting[key];$(id+'-value').textContent=recipe.lighting[key]+'°';
  }
  $('water-treatment').value=f.water_treatment;$('water-spacing').value=f.water_spacing_mm;writePaperField($('building-min'),f.building_min_mm2);
  $('water-spacing-field').hidden=!['hatch','concentric'].includes(f.water_treatment);
  $('park-spacing-field').hidden=!f.parks||!['hatch','concentric'].includes(f.park_treatment);
  $('park-treatment').value=f.park_treatment;$('hatch-spacing').value=f.hatch_spacing_mm;$('contour-interval').value=f.contour_interval_m;
  renderPalette();guides();resize();interactionHint();
}
function mutated({delay=750,recordHistory=true,prune=true}={}) {
  if(prune)prunePalette();
  if(activeDoc){
    if(recordHistory&&activeDoc.historyRecipe&&stable(activeDoc.historyRecipe)!==stable(recipe)){
      (activeDoc.undo||=[]).push(clone(activeDoc.historyRecipe));activeDoc.undo=activeDoc.undo.slice(-50);activeDoc.redo=[];
    }
    activeDoc.historyRecipe=clone(recipe);updateUndo();
  }
  printEpoch++;printTask=null;printPointer=null;printRestoration=null;printPercent=0;printConnectionLost=false;printStatusView=null;
  epoch++;clearTimeout(timer);full=null;sceneController?.abort();sceneController=null;
  idleProgress('Preview pending','Waiting for edits to settle');
  refining=false;updateSend();
  $('metrics').textContent='';$('report').replaceChildren();
  if(active){api('/api/cancel',{session,revision:++revision}).catch(()=>{});active=null;}
  $('preview').hidden=true;$('sketch').hidden=false;
  $('preview-fidelity').hidden=!scene;
  $('preview-fidelity').textContent='Sketch from cached geometry · updating map data';
  notice();remember();guides();queueSketch();
  documentState();
  timer=setTimeout(()=>requestPreview('draft'),delay);
}
function loadPreviewImage(url) {
  return new Promise((resolve,reject)=>{
    const image=new Image();
    const finish=error=>{clearTimeout(timeout);image.onload=image.onerror=null;error?reject(error):resolve(image);};
    const timeout=setTimeout(()=>finish(new Error('Preview image timed out. Retry to load it again.')),30000);
    image.onload=()=>finish();
    image.onerror=()=>finish(new Error('Could not load the preview image. Retry to load it again.'));
    image.src=mapURL(url);
  });
}
function currentPreview(preview=previewSnapshot) {
  return preview?.signature===stable(recipe)&&preview.composerVersion===catalog.composer_version&&preview.quality!=='thumbnail';
}
async function requestPreview(quality) {
  // A restored receipt must report its actual phase before it can suppress a
  // draft. An unfinished browser pointer can refer to a failed/completed job.
  const waitingEpoch=epoch;
  if(printRestoration)await printRestoration;
  if(waitingEpoch!==epoch||printActive.has(printTask?.phase))return;
  clearTimeout(timer);sceneController?.abort();lastQuality=quality;previewStartedAt=performance.now();
  notice();
  overallPercent=0;
  const thisEpoch=++epoch,signature=JSON.stringify(recipe),seq=++revision;
  active={pending:true};refining=quality==='proof';updateSend();
  showProgress({quality,status:'running',progress:{stage:'starting',detail:'Requesting preview worker'}});
  let sceneVersion=0,imageStarted=false;
  function requestScene(job){
    const version=job.scene_revision||1;
    if(version<=sceneVersion||!job.scene_available)return;
    sceneVersion=version;sceneController?.abort();sceneController=new AbortController();
    // A large sketch download must not suspend status polling. The resolved
    // image can arrive first; an obsolete sketch must never replace it.
    api('/api/scenes/'+job.id+(version>1?'?revision='+version:''),undefined,sceneController).then(next=>{
      if(thisEpoch!==epoch||version!==sceneVersion)return;
      scene=next;prepareTerrainSketch();
      if(!imageStarted && $('preview').hidden){
        $('sketch').hidden=false;queueSketch();$('preview-fidelity').hidden=false;
        const pending=['fills','labels'];
        const features=[recipe.features,...(recipe.boundary?[recipe.outside.features]:[])];
        if(features.some(f=>f.hillshade))pending.push('hillshade');
        if(recipe.camera.relief&&features.some(f=>f.terrain_edges))pending.push('silhouettes');
        if(recipe.boundary)pending.unshift('boundary styles');
        if(recipe.camera.relief)pending.unshift('terrain occlusion');
        $('preview-fidelity').textContent=(version>1?'Refined terrain sketch':'Sketch')+' · '+pending.join(', ')+' pending';
      }
    }).catch(()=>{}); // The resolved preview remains available if sketch transfer fails.
  }
  try {
    let job=await api('/api/preview',{recipe:clone(recipe),quality,session,revision:seq});
    if(thisEpoch!==epoch)return;
    active=job;showProgress(job);requestScene(job);
    while(['queued','running'].includes(job.status)) {
      await new Promise(resolve=>setTimeout(resolve,650));
      if(thisEpoch!==epoch)return;
      job=await api('/api/jobs/'+job.id);
      if(thisEpoch!==epoch)return;
      active=job;showProgress(job);requestScene(job);
    }
    if(thisEpoch!==epoch||signature!==JSON.stringify(recipe))return;
    if(job.status==='error')throw new Error(job.error);
    if(job.status==='cancelled'){idleProgress('Preview cancelled','Retry to continue',true);return;}
    $('fetch-note').textContent=`OSM zoom ${job.result.source.zoom} · ${job.result.source.tile_count} tiles · extent ${job.result.fetched_bounds.map(n=>n.toFixed(4)).join(', ')}`;
    imageStarted=true;
    showProgress({...job,status:'running',progress:{stage:'image',detail:'Loading the completed preview image'}});
    const image=await loadPreviewImage(job.artifacts+'preview.png');
    if(thisEpoch!==epoch)return;
    $('preview').src=image.src;$('preview').hidden=false;$('sketch').hidden=true;
    previewSnapshot={url:image.src,signature:stable(recipe),composerVersion:catalog.composer_version,quality,unmeasuredInks:job.result.unmeasured_inks||[]};remember();attachThumbnail(activeDoc,image);
    $('preview-fidelity').hidden=true;
    showProgress(job,true);
    const count=Object.entries(job.result.feature_counts).filter(([k])=>!k.startsWith('omitted/')).reduce((n,[,v])=>n+v,0);
    $('metrics').textContent=`${count.toLocaleString()} feature groups · ${job.result.label_count} labels`;
    $('report').replaceChildren(...job.result.warnings.map(message=>{const p=document.createElement('p');p.textContent=message;return p;}));
    if(job.result.build_cache){
      const cache=job.result.build_cache,p=document.createElement('p');
      p.textContent=`Reused ${cache.reused} drawing/geometry stages · rebuilt ${cache.built}.`;
      $('report').prepend(p);
    }
    if(job.result.omitted_labels.length){const p=document.createElement('p');p.textContent='Labels without a clear placement: '+job.result.omitted_labels.join(', ');$('report').append(p);}
    if(quality==='proof'){
      full={job,signature};
      remember();
      updateSend();
    }
  }catch(error){
    if(thisEpoch===epoch){sceneController?.abort();notice(error.message);idleProgress('Preview failed',error.message,true);}
  }finally{
    if(thisEpoch===epoch){refining=false;updateSend();}
  }
}
function queueSketch(){if(sketchFrame)return;sketchFrame=requestAnimationFrame(()=>{sketchFrame=0;drawSketch();});}
function pageLayout(){
  const [x,y,w,h]=recipe.media.printable_rect_mm,i=recipe.media.content_inset_mm;
  const content=[x+i,y+i,x+w-i,y+h-i];
  return {content,frame:[...content]};
}
function guides(){
  const [w,h]=recipe.media.sheet_size_mm;$('guides').setAttribute('viewBox',`0 0 ${w} ${h}`);
  const [x,y,rw,rh]=recipe.media.printable_rect_mm;
  for(const [k,v] of Object.entries({x,y,width:rw,height:rh}))$('printable-guide').setAttribute(k,v);
  $('margin-guide').setAttribute('d',`M0 0H${w}V${h}H0Z M${x} ${y}V${y+rh}H${x+rw}V${y}Z`);
  // SVG elements do not reflect the HTML `hidden` property into an attribute.
  $('guides').toggleAttribute('hidden',!$('show-guides').checked);
}
function resize(){
  if(!recipe)return;
  const desk=$('desk'),[w,h]=recipe.media.sheet_size_mm;
  const style=getComputedStyle(desk);
  const availableW=desk.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight);
  const availableH=Math.max(1,desk.clientHeight-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom));
  const factor=Math.min(availableW/w,availableH/h);
  $('paper').style.width=(w*factor)+'px';$('paper').style.height=(h*factor)+'px';
}
function matrices(){
  const a=recipe.camera.azimuth_deg*RAD,e=(recipe.camera.relief?recipe.camera.elevation_deg:90)*RAD;
  return [[Math.cos(a),-Math.sin(a),0],[-Math.sin(e)*Math.sin(a),-Math.sin(e)*Math.cos(a),-Math.cos(e)]];
}
function terrainHeight(x,y){
  if(!scene?.terrain || !recipe.camera.relief)return 0;
  if(scene.terrain_mesh?.lookup){
    const mesh=scene.terrain_mesh,[x0,y0,x1,y1]=mesh.bounds;
    if(Math.abs(x)<1e-8&&Math.abs(y)<1e-8)return mesh.center_height_m*recipe.camera.exaggeration/Math.cos(recipe.center[1]*RAD);
    const qx=(x-x0)/(x1-x0)*32768,qy=(y-y0)/(y1-y0)*32768;
    for(let size=4096;size>=2;size/=2){
      const cell=mesh.lookup.get(`${Math.floor(qx/size)*size},${Math.floor(qy/size)*size},${size}`);
      if(!cell)continue;
      for(let i=cell[3];i<cell[3]+cell[4];i++){
        const [a,b,c]=mesh.indices[i].map(index=>mesh.vertices[index]);
        const dx=x-a[0],dy=y-a[1],bx=b[0]-a[0],by=b[1]-a[1],cx=c[0]-a[0],cy=c[1]-a[1],det=bx*cy-by*cx;
        const u=(dx*cy-dy*cx)/det,v=(bx*dy-by*dx)/det;
        if(u>=-1e-8&&v>=-1e-8&&u+v<=1+1e-8)return (a[2]+u*(b[2]-a[2])+v*(c[2]-a[2]))*recipe.camera.exaggeration/Math.cos(recipe.center[1]*RAD);
      }
      break;
    }
  }
  const {xs,ys,z}=scene.terrain;
  const bracket=(a,v)=>{let l=0,r=a.length-1;while(r-l>1){const m=(l+r)>>1;if(a[m]>v)r=m;else l=m;}return Math.min(l,a.length-2);};
  const i=bracket(xs,x),j=bracket(ys,y),u=Math.max(0,Math.min(1,(x-xs[i])/(xs[i+1]-xs[i]))),v=Math.max(0,Math.min(1,(y-ys[j])/(ys[j+1]-ys[j])));
  return ((1-u)*(1-v)*z[j][i]+u*(1-v)*z[j][i+1]+(1-u)*v*z[j+1][i]+u*v*z[j+1][i+1])*recipe.camera.exaggeration/Math.cos(recipe.center[1]*RAD);
}
function prepareTerrainSketch(){
  const mesh=scene?.terrain_mesh;
  if(mesh?.cells?.length)mesh.lookup=new Map(mesh.cells.map(cell=>[cell.slice(0,3).join(','),cell]));
}
function drawSketch(){
  if(!recipe)return;
  const canvas=$('sketch'),[pw,ph]=recipe.media.sheet_size_mm,pixels=1500/pw;
  canvas.width=1500;canvas.height=Math.round(ph*pixels);
  const ctx=canvas.getContext('2d');ctx.scale(pixels,pixels);ctx.fillStyle='#fff';ctx.fillRect(0,0,pw,ph);
  if(!scene)return;
  const {frame,content}=pageLayout(),center=world(recipe.center),[cx,cy]=center.map((v,i)=>v-scene.origin[i]);
  const scale=1000*Math.cos(recipe.center[1]*RAD)/recipe.framing.scale_denominator,m=matrices(),[ox,oy]=recipe.framing.offset_mm;
  const z0=terrainHeight(cx,cy),pc=[(frame[0]+frame[2])/2+ox,(frame[1]+frame[3])/2+oy];
  const project=([x,y,deck])=>{const height=recipe.camera.relief&&Number.isFinite(deck)?deck*recipe.camera.exaggeration/Math.cos(recipe.center[1]*RAD):terrainHeight(x,y),z=height-z0;return [pc[0]+((x-cx)*m[0][0]+(y-cy)*m[0][1]+z*m[0][2])*scale,pc[1]+((x-cx)*m[1][0]+(y-cy)*m[1][1]+z*m[1][2])*scale];};
  ctx.save();ctx.beginPath();ctx.rect(frame[0],frame[1],frame[2]-frame[0],frame[3]-frame[1]);ctx.clip();
  const inks=Object.fromEntries(recipe.palette.inks.map(i=>[i.id,i.color_srgb])),f=recipe.features;
  // Immediate sketch is deliberately geometry-only; accurate boundary styles,
  // white paint, labels and hidden lines arrive from this same job's PDF.
  for(const line of scene.lines){
    if(line.kind==='parks'&&!f.parks || line.kind==='buildings'&&(f.buildings==='none'||line.area_m2*scale*scale<f.building_min_mm2) || line.kind==='water'&&f.shore==='none')continue;
    if(line.kind==='roads'&&(f.roads==='none'||f.roads==='major'&&!['motorway','trunk','primary','secondary','tertiary'].includes(line.cls)))continue;
    const role=line.kind==='roads'&&line.bridge&&f.bridges?'bridges':line.kind;
    ctx.strokeStyle=inks[recipe.ink_roles[role]]||'#222';ctx.lineWidth=line.kind==='roads'?.32:.18;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();
    line.points.forEach((p,i)=>{const a=project(p);if(i)ctx.lineTo(...a);else ctx.moveTo(...a);});ctx.stroke();
  }
  ctx.restore();drawFurnitureSketch(ctx,content,inks[recipe.ink_roles.labels]);
}
function drawFurnitureSketch(ctx,[x0,y0,x1,y1],ink){
  const f=recipe.furniture;ctx.save();ctx.beginPath();ctx.rect(x0,y0,x1-x0,y1-y0);ctx.clip();
  const contrast=catalog.compass.contrast_margin_mm;
  ctx.lineJoin='round';ctx.lineCap='round';
  if(f.title){
    const available=x1-x0-8-(f.compass?43:0);let size=18/(72/25.4);
    ctx.font=`${size}px serif`;size*=Math.min(1,available/ctx.measureText(recipe.title).width);ctx.font=`${size}px serif`;
    const x=x0+4,y=y0+4+size;ctx.strokeStyle='white';ctx.lineWidth=2*contrast;ctx.strokeText(recipe.title,x,y);ctx.fillStyle=ink;ctx.fillText(recipe.title,x,y);
  }
  if(f.compass){
    const art=catalog.compass,m=matrices(),length=Math.hypot(m[0][1],m[1][1]),north=[m[0][1]/length,m[1][1]/length];
    const center=[x1-art.edge_inset,y0+art.edge_inset],anchor=center.map((v,i)=>v+north[i]*art.north_label_radius);
    const rotate=([x,y])=>[center[0]-north[1]*x-north[0]*y,center[1]+north[0]*x-north[1]*y];
    const path=(points,transform)=>{ctx.beginPath();points.forEach((p,i)=>{const q=transform(p);if(i)ctx.lineTo(...q);else ctx.moveTo(...q);});};
    ctx.fillStyle=ctx.strokeStyle='white';ctx.lineWidth=2*contrast;
    ctx.beginPath();ctx.arc(...center,art.ring_radius,0,Math.PI*2);ctx.fill();ctx.stroke();
    for(const spoke of art.spokes){path(spoke.outline,rotate);ctx.fill();ctx.stroke();}
    for(const line of art.letter){path(line,p=>p.map((v,i)=>v+anchor[i]));ctx.stroke();}
    ctx.strokeStyle=ink;ctx.lineWidth=.45/(72/25.4);ctx.beginPath();ctx.arc(...center,art.ring_radius,0,Math.PI*2);ctx.stroke();
    for(const spoke of art.spokes){path(spoke.outline,rotate);ctx.fillStyle='white';ctx.fill();ctx.stroke();path(spoke.shade,rotate);ctx.fillStyle=ink;ctx.fill();}
    ctx.lineWidth=.32;for(const line of art.letter){path(line,p=>p.map((v,i)=>v+anchor[i]));ctx.stroke();}
  }
  ctx.restore();
}
let editMode=false,shiftHeld=false,activeEdit=null;
function interactionHint(){
  const editing=editMode||shiftHeld||Boolean(activeEdit);
  $('edit-map').setAttribute('aria-pressed',String(editMode));
  $('desk').classList.toggle('editing-map',editing);
  $('edit-state').hidden=!editing;
  $('edit-state').textContent='Editing map';
}
function updateUndo(){
  $('undo-map').disabled=!activeDoc?.undo?.length;
  $('redo-map').disabled=!activeDoc?.redo?.length;
}
function undoMap(redo=false){
  navigation.finish();
  if(activeEdit){navigation.cancel();return;}
  const from=activeDoc?.[redo?'redo':'undo'];if(!from?.length)return;
  (activeDoc[redo?'undo':'redo']||=[]).push(clone(recipe));recipe=from.pop();
  mutated({delay:0,recordHistory:false,prune:false});sync();
}
function revertRecipe(){
  navigation.finish();
  if(!activeDoc?.saved||activeDoc.saving||!documentDirty(activeDoc))return;
  navigation.cancel();
  // The saved signature contains the exact recipe for this tab's last save,
  // including after a reload. Revert never writes a new saved revision.
  recipe=JSON.parse(activeDoc.saved.signature);
  editMode=false;shiftHeld=false;navigation.reset();
  mutated({delay:0,prune:false});sync();
  notice('Reverted to the last saved recipe. Undo restores your edits.',5000);
}
function inverseDelta(dx,dy,r=recipe){
  const a=r.camera.azimuth_deg*RAD,e=(r.camera.relief?r.camera.elevation_deg:90)*RAD;
  const cos=Math.cos(a),sin=Math.sin(a),se=Math.sin(e),scale=r.framing.scale_denominator/(1000*Math.cos(r.center[1]*RAD));
  return [(cos*dx-sin*dy/se)*scale,(-sin*dx-cos*dy/se)*scale];
}
function mapZoom(factor,anchor=null){
  const rect=$('paper').getBoundingClientRect(),mm=recipe.media.sheet_size_mm[0]/rect.width,{frame}=pageLayout();
  const p=anchor?[(anchor[0]-rect.left)*mm-(frame[0]+frame[2])/2-recipe.framing.offset_mm[0],(anchor[1]-rect.top)*mm-(frame[1]+frame[3])/2-recipe.framing.offset_mm[1]]:[0,0];
  const before=inverseDelta(...p),center=world(recipe.center);
  recipe.framing.scale_denominator=Math.max(500,Math.min(100000000,recipe.framing.scale_denominator/factor));
  const after=inverseDelta(...p);recipe.center=geo(center.map((v,i)=>v+before[i]-after[i]));
}
function editFeedback(tx){
  for(const id of ['preview','sketch']){$(id).style.transformOrigin='0 0';$(id).style.transform=tx.visual.toString();}
  $('scale').value=+recipe.framing.scale_denominator.toFixed(2);
  $('longitude').value=+recipe.center[0].toFixed(6);$('latitude').value=+recipe.center[1].toFixed(6);
  writePaperField($('offset-x'),recipe.framing.offset_mm[0]);writePaperField($('offset-y'),recipe.framing.offset_mm[1]);
  $('edit-state').textContent='Editing map · provisional view';
}
function localPaper(anchor){
  const rect=$('paper').getBoundingClientRect();
  return [(anchor[0]-rect.left)/navigation.state.scale,(anchor[1]-rect.top)/navigation.state.scale];
}
function scaleVisual(matrix,factor,anchor){const p=localPaper(anchor);return new DOMMatrix().translate(...p).scale(factor).translate(-p[0],-p[1]).multiply(matrix);}
const navigation=previewNavigation($('desk'),{
  enabled:()=>Boolean(recipe),
  origin:()=>{const d=$('desk').getBoundingClientRect(),p=$('paper').getBoundingClientRect(),v=navigation.state;return [p.left+p.width/2-v.x-d.left,p.top+p.height/2-v.y-d.top];},
  onChange:({scale,x,y})=>{
    $('paper').style.transform=`translate(${x}px,${y}px) scale(${scale})`;
    $('desk').dataset.previewZoom=String(Math.round(scale*100));
  },
  edit:{
    enabled:event=>editMode||(event.shiftKey&&!event.ctrlKey&&!event.metaKey),
    start:()=>{
      const tx={before:clone(recipe),base:clone(recipe),visual:new DOMMatrix(),baseVisual:new DOMMatrix(),mode:$('view-mode').value};
      pauseDraft();activeEdit=tx;interactionHint();return tx;
    },
    rebase:tx=>{tx.base=clone(recipe);tx.baseVisual=new DOMMatrix(tx.visual);},
    move:(tx,{dx,dy,factor,anchor})=>{
      recipe=clone(tx.base);
      const magnification=navigation.state.scale,x=dx/magnification,y=dy/magnification;
      if(tx.mode==='orbit'){
        recipe.camera.azimuth_deg=Math.round((tx.base.camera.azimuth_deg+dx*.4+360)%360);
        if(recipe.camera.relief)recipe.camera.elevation_deg=Math.round(Math.max(10,Math.min(85,tx.base.camera.elevation_deg+dy*.25)));
        const cx=$('paper').offsetWidth/2,cy=$('paper').offsetHeight/2;
        const pitch=Math.sin(recipe.camera.elevation_deg*RAD)/Math.sin(tx.base.camera.elevation_deg*RAD);
        tx.visual=new DOMMatrix().translate(cx,cy).rotate(-dx*.4).scale(1,recipe.camera.relief?pitch:1).translate(-cx,-cy).multiply(tx.baseVisual);
      }else{
        const mm=recipe.media.sheet_size_mm[0]/$('paper').getBoundingClientRect().width;
        if(tx.mode==='frame')recipe.framing.offset_mm=tx.base.framing.offset_mm.map((v,i)=>Math.max(-1000,Math.min(1000,v+(i?dy:dx)*mm)));
        else{const delta=inverseDelta(-dx*mm,-dy*mm,tx.base);recipe.center=geo(world(tx.base.center).map((v,i)=>v+delta[i]));}
        tx.visual=new DOMMatrix().translate(x,y).multiply(tx.baseVisual);
      }
      if(factor!==1){const before=recipe.framing.scale_denominator;mapZoom(factor,anchor);tx.visual=scaleVisual(tx.visual,before/recipe.framing.scale_denominator,anchor);}
      editFeedback(tx);
    },
    wheel:(tx,factor,anchor)=>{const before=recipe.framing.scale_denominator;mapZoom(factor,anchor);tx.visual=scaleVisual(tx.visual,before/recipe.framing.scale_denominator,anchor);editFeedback(tx);},
    end:(tx,cancel)=>{
      activeEdit=null;
      for(const id of ['preview','sketch'])$(id).style.transform='';
      const changed=stable(tx.before)!==stable(recipe);
      if(cancel)recipe=tx.before;
      if(changed&&!cancel){mutated({delay:0});sync();}
      else{sync();if(!currentPreview())timer=setTimeout(()=>requestPreview('draft'),250);}
      interactionHint();
    }
  }
});
window.addEventListener('keydown',event=>{
  if(event.key==='Shift'&&!event.ctrlKey&&!event.metaKey&&!event.target.closest('input,select,textarea,[contenteditable]')){shiftHeld=true;interactionHint();}
  if(event.key==='Escape'){navigation.cancel();editMode=false;shiftHeld=false;interactionHint();}
  if((event.ctrlKey||event.metaKey)&&['z','y'].includes(event.key.toLowerCase())&&!event.target.closest('input,select,textarea,[contenteditable]')&&!document.querySelector('dialog[open]')){
    event.preventDefault();undoMap(event.key.toLowerCase()==='y'||event.shiftKey);
  }
});
window.addEventListener('keyup',event=>{if(event.key==='Shift'){shiftHeld=false;interactionHint();}});
window.addEventListener('blur',()=>{shiftHeld=false;interactionHint();});

function physicalCarousel(){
  if(capabilities?.carousel)return capabilities.carousel;
  const slots=Object.fromEntries(Array.from({length:8},(_,i)=>[i+1,{state:'empty',color:null,width_mm:null,label:''}]));
  for(const ink of capabilities?.capabilities.available_inks||[])slots[ink.source.slot]={state:'loaded',color:ink.color_srgb,width_mm:ink.width_mm,label:ink.label};
  return {revision:capabilities?.capabilities.inventory_revision||0,slots};
}
function loadedInk(slot,pen){return {label:pen.label||colorName(pen.color),color_srgb:pen.color,width_mm:pen.width_mm,source:{kind:'plotline-carousel',revision:physicalCarousel().revision,slot}};}
function newMapRecipe(){
  const value=clone(catalog.recipe);
  if(!capabilities||capabilities.offline)return value;
  const loaded=Object.entries(physicalCarousel().slots).filter(([,p])=>p.state==='loaded'&&p.color&&p.width_mm>0);
  for(const ink of value.palette.inks){
    const matches=loaded.filter(([,p])=>p.color.toLowerCase()===ink.color_srgb.toLowerCase());
    if(matches.length===1){const [slot,pen]=matches[0];Object.assign(ink,loadedInk(Number(slot),pen));}
  }
  return value;
}
function referencedInkIds(){return new Set([...Object.values(recipe.ink_roles),...Object.values(recipe.outside.ink_roles)]);}
function prunePalette(){const ids=referencedInkIds();recipe.palette.inks=recipe.palette.inks.filter(i=>ids.has(i.id));}
let inkDrawerFocus=null,inkDrawerScroll=0;
function openInkDrawer(assignment=null){
  if($('ink-drawer').hidden){
    inkDrawerFocus=document.activeElement;inkDrawerScroll=window.scrollY;
    $('ink-drawer').hidden=false;document.body.classList.add('ink-drawer-open');
    $('edit-inks').setAttribute('aria-expanded','true');
    if(matchMedia('(max-width:640px)').matches)window.scrollTo(0,0);
    resize();
  }
  if(assignment){
    $('style-region').value=assignment.region;sync();
    const role=Array.from($('palette').querySelectorAll('[data-ink-id]')).find(e=>e.dataset.inkId===assignment.role);
    if(role){revealInk($('palette'),assignment.role);role.click();}
  }else $('close-inks').focus({preventScroll:true});
}
function closeInkDrawer(){
  if($('ink-drawer').hidden)return;
  $('palette')._inkTray?.cancel();
  $('ink-drawer').hidden=true;document.body.classList.remove('ink-drawer-open');
  $('edit-inks').setAttribute('aria-expanded','false');resize();
  window.scrollTo(0,inkDrawerScroll);
  (inkDrawerFocus?.getClientRects().length?inkDrawerFocus:$('edit-inks')).focus({preventScroll:true});
}
$('edit-inks').onclick=()=>openInkDrawer();
$('close-inks').onclick=closeInkDrawer;
$('ink-warning').onclick=()=>reviewInks();
new ResizeObserver(()=>{
  const scroll=$('palette').querySelector('.ink-scroll'),nav=$('palette').querySelector('.ink-scroll-nav');
  if(scroll&&nav)nav.hidden=scroll.scrollWidth<=scroll.clientWidth+1;
}).observe($('palette'));
// Drawer controls remain one-click actions while a source role is selected.
// Register before the shared picker's outside-click cancellation listener.
document.addEventListener('click',event=>{
  if(event.target.closest('.ink-drawer-heading,#ink-attention,.ink-launcher,#ink-summary,#review-inks')&&$('palette').classList.contains('ink-selecting'))$('palette')._inkTray?.cancel();
},true);
window.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&!document.querySelector('dialog[open]'))closeInkDrawer();
});
function inkAssignments(){
  if(!recipe||!catalog)return [];
  const regions=recipe.boundary?['inside','outside']:['inside'];
  return regions.flatMap(region=>catalog.roles.map(role=>({region,role,ink:recipe.palette.inks.find(i=>i.id===(region==='outside'?recipe.outside:recipe).ink_roles[role])})));
}
function inkIssues(){
  const loaded=Object.values(physicalCarousel().slots).filter(p=>p.state==='loaded');
  const emitted=unmeasuredInks().flatMap(i=>i.roles);
  return inkAssignments().flatMap(assignment=>{
    const ink=assignment.ink;
    const reason=!(ink?.width_mm>0)?'diameter unknown':!capabilities?'':loaded.some(p=>sameMaterial(ink,p))?'':
      loaded.some(p=>colorMatches(ink,p))?'loaded diameter differs':'not loaded';
    return reason?[{...assignment,reason,blocking:emitted.some(r=>r.region===assignment.region&&r.role===assignment.role)}]:[];
  }).sort((a,b)=>Number(b.blocking)-Number(a.blocking));
}
function inkSwatch(ink){
  const swatch=document.createElement('span');swatch.className='feature-ink-swatch';swatch.style.background=ink?.color_srgb||'transparent';swatch.setAttribute('aria-hidden','true');return swatch;
}
function renderInkSummary(){
  const region=$('style-region').value,assignments=inkAssignments().filter(a=>a.region===region),summary=$('ink-summary');summary.replaceChildren();
  for(const assignment of assignments){
    const {role,ink}=assignment,button=document.createElement('button'),label=roleNames[role]||role;
    button.title=`${label} · ${ink?.label||'Unassigned'} — edit ink`;button.setAttribute('aria-controls','ink-drawer');
    button.append(inkSwatch(ink),document.createTextNode(label));
    button.onclick=()=>openInkDrawer(assignment);summary.append(button);
  }
  renderInkAttention();
}
function renderInkAttention(){
  const issues=inkIssues(),attention=$('ink-attention');attention.replaceChildren();attention.hidden=!issues.length;
  const label=issue=>(recipe.boundary?issue.region+' · ':'')+(roleNames[issue.role]||issue.role)+' — '+issue.reason;
  for(const id of ['ink-warning','review-inks']){
    $(id).hidden=!issues.length;$(id).title=issues.map(label).join('\n');
    $(id).setAttribute('aria-label',issues.some(i=>i.blocking)?'Review inks preventing plot preparation':'Review ink issues');
  }
  for(const issue of issues){
    const button=document.createElement('button');button.className='ink-issue';button.dataset.region=issue.region;button.dataset.role=issue.role;
    if(issue.blocking)button.classList.add('blocking');
    button.title=issue.reason==='not loaded'?'This authored ink is not in the loaded-pen snapshot. You can keep it or choose a different pen.':label(issue);
    button.append(inkSwatch(issue.ink),document.createTextNode(label(issue)));button.onclick=()=>openInkDrawer(issue);attention.append(button);
  }
}
function renderPalette(){
  const doc=activeDoc,style=currentStyle(),ids=referencedInkIds(),palette=recipe.palette.inks.filter(i=>ids.has(i.id)),carousel=physicalCarousel(),targets=penTargets(carousel,palette);
  const roles=catalog.roles.map(role=>({...palette.find(i=>i.id===style.ink_roles[role]),id:role,label:roleNames[role]||role}));
  const mapping=Object.fromEntries(roles.map(role=>[role.id,targets.find(t=>sameMaterial(t.ink,role))?.id||0]));
  const assign=(roles,ink)=>{
    if(activeDoc!==doc||style!==currentStyle())return false;
    if(ink.color_srgb.toLowerCase()==='#ffffff')throw Error('White clears paper; choose a colored ink.');
    let target=recipe.palette.inks.find(i=>sameMaterial(i,ink));
    if(!target){target={...ink,id:newInkId()};recipe.palette.inks.push(target);}
    else if(ink.source)target.source=clone(ink.source);
    for(const role of roles)style.ink_roles[role]=target.id;
    mutated();renderPalette();queueMicrotask(()=>revealInk($('palette'),roles[0]));return true;
  };
  renderInkSets($('palette'),{inks:roles,carousel,targets,mapping,roleLabels:true,hideEmptyCustomTargets:true,title:'Feature roles',
    onAssign:(roles,target)=>assign(roles,target.slot?loadedInk(target.slot,target.ink):target.ink),onCustom:assign,
    customSeed:roles=>recipe.palette.inks.find(i=>i.id===style.ink_roles[roles[0].id]),
    targetIssue:t=>t.slot&&t.ink.state!=='loaded'?'This slot is empty or unavailable.':(t.ink.color||t.ink.color_srgb)?.toLowerCase()==='#ffffff'?'White clears paper; choose a colored ink.':''});
  renderInventory();renderInkSummary();
}
function renderInventory(){
  if(!capabilities)return;
  const {updates,missing}=carouselInkChanges();
  $('update-map-inks').disabled=updatingMapInks||capabilities.offline||!updates.length;
  $('update-map-inks').textContent=updatingMapInks?'Updating map inks…':'Update map inks from carousel';
  $('update-map-inks').title='Adopt loaded pens’ design colors and measured diameters, including the outside-boundary style, and regenerate the draft. Recover old bindings by an unambiguous color match.';
  const changed=updates.length?`${updates.length} map ink${updates.length===1?' has':'s have'} changed in the carousel. Update map inks to adopt them and regenerate the draft.`:'Select a role, then a pen to copy its color and diameter.';
  const absent=missing.length?' Choose a pen for '+missing.map(({ink})=>ink.label).join(', ')+'; no unambiguous loaded match was found.':'';
  $('inventory-status').textContent=`${capabilities.offline?'Offline snapshot':'Loaded carousel snapshot'} · revision ${capabilities.capabilities.inventory_revision}. ${changed}${absent}`;
}
function carouselInkChanges(){
  const updates=[],missing=[];
  if(!recipe)return {updates,missing};
  const carousel=physicalCarousel(),ids=referencedInkIds();
  const available=Object.entries(carousel.slots).filter(([,p])=>p.state==='loaded'&&p.color&&p.color.toLowerCase()!=='#ffffff'&&p.width_mm>0).map(([slot,pen])=>({slot:Number(slot),pen}));
  for(const ink of recipe.palette.inks){
    if(!ids.has(ink.id))continue;
    const recorded=['plotline-carousel','squidprint-carousel'].includes(ink.source?.kind)?ink.source.slot:null;
    // The original built-in palettes predate slot provenance. Deliberately
    // authored custom inks still require an explicit role assignment.
    if(!recorded&&!['black','red','green','blue'].includes(ink.id))continue;
    // Design colors take priority. A measured-color alias recovers maps that
    // adopted scan RGB before appearance was separated from the design swatch.
    const design=available.filter(({pen})=>ink.color_srgb.toLowerCase()===pen.color.toLowerCase());
    const matches=design.length?design:available.filter(({pen})=>colorMatches(ink,pen));
    const match=available.find(p=>p.slot===recorded&&colorMatches(ink,p.pen))||(matches.length===1?matches[0]:null);
    if(!match){if(recorded||matches.length)missing.push({ink});continue;}
    if(!sameMaterial(ink,match.pen)||recorded!==match.slot)updates.push({ink,...match});
  }
  return {updates,missing};
}
async function updateMapInks(){
  const doc=activeDoc;updatingMapInks=true;renderInventory();
  try{
    await refreshInventory();
    if(activeDoc!==doc)return;
    if(capabilities.offline)throw Error('Plotline Studios is unavailable. Reconnect before updating map inks.');
    const {updates}=carouselInkChanges();
    if(!updates.length){notice('The assigned map inks already match their loaded pens.');return;}
    for(const {ink,slot,pen} of updates)Object.assign(ink,loadedInk(slot,pen));
    mutated();sync();
    notice(`Updated ${updates.length} map ink${updates.length===1?'':'s'} from the carousel. Regenerating the draft; Prepare plot will build with these measurements.`);
  }finally{updatingMapInks=false;renderInventory();}
}
$('update-map-inks').onclick=safely(updateMapInks);
$('refresh-inks').onclick=safely(refreshInventory);
async function refreshInventory(){
  $('inventory-status').textContent='Reading loaded pens…';
  capabilities=await api('/api/capabilities');renderPalette();fillMedia();updateSend();
}
function actionLink(id,url,reason){
  const element=$(id);
  if(url){
    const parsed=new URL(url,location.origin);
    if(!['http:','https:'].includes(parsed.protocol))url=null;
  }
  element.setAttribute('aria-disabled',url?'false':'true');element.tabIndex=url?0:-1;
  if(url)element.href=['pdf','bundle'].includes(id)?mapURL(url):studioURL(id==='pdf-settings'?'pdf':'hpgl',url);else element.removeAttribute('href');
  element.title=reason;
}
let printPercent=0,printConnectionLost=false;
function driverUnavailable(){
  return !capabilities?'Checking Plotline conversion service…':capabilities.offline?'The conversion service is offline. Refresh Loaded pens to reconnect.':
    !capabilities.capabilities.implemented.pdf_upload||!capabilities.capabilities.implemented.pdf_handoff?'The conversion service does not support map handoff. PDF downloads are available.':'';
}
function unmeasuredInks(){
  if(printPointer?.signature===stable(recipe)&&printTask?.render?.result?.unmeasured_inks)return printTask.render.result.unmeasured_inks;
  if(full?.signature===JSON.stringify(recipe))return full.job.result.unmeasured_inks||[];
  if(previewSnapshot?.signature===stable(recipe))return previewSnapshot.unmeasuredInks||[];
  return [];
}
function reviewInks(){
  openInkDrawer(inkIssues()[0]);
}
function updateSend(){
  const current=Boolean(full&&full.signature===JSON.stringify(recipe)),busy=Boolean(printTask&&printActive.has(printTask.phase));
  const first=(printTask?.jobs||[]).find(job=>job.job_id),ready=printTask?.phase==='ready'&&first;
  const exported=current?full.job.artifacts:printTask?.export_key?'/artifacts/'+printTask.export_key+'/':null;
  const unmeasured=unmeasuredInks();
  const updatedInks=carouselInkChanges().updates.length;
  const missing=unmeasured.map(i=>`${i.label} (${i.roles.map(r=>(recipe.boundary?r.region+' · ':'')+(roleNames[r.role]||r.role)).join(', ')})`).join('; ');
  const reason=ready?'':missing?'Ink diameter missing: '+missing+'.':driverUnavailable()||(printTask?.phase==='empty'?'This map produced no drawable pen paths.':'');
  $('send').disabled=Boolean(reason)||(busy&&!printConnectionLost)||printTask?.phase==='empty';
  $('send').textContent=ready?'Open plot ↗':printConnectionLost?'Resume preparation →':busy?'Preparing plot…':printTask?.retryable?'Retry preparation →':printTask&&['recovery','superseded'].includes(printTask.phase)?'Review PDF conversion →':'Prepare plot →';
  $('send').title=reason||'Prepare this view and open HPGL Studio';
  $('export-status').textContent=reason||(updatedInks&&ready?'Plot uses earlier ink measurements.':'');
  $('export-detail').textContent=updatedInks&&ready?'Update map inks from carousel to adopt the current measurements.':missing&&!ready?'Choose a measured pen for each affected role.':reason?'PDF downloads remain available.':'';
  $('export-status').closest('.export-explanation').hidden=!$('export-status').textContent||Boolean(missing&&!ready);
  renderInkAttention();
  $('generate').disabled=refining||Boolean(exported)||busy;$('generate').textContent=refining?'Creating PDF…':exported?'PDF ready':'Create PDF';
  actionLink('pdf',exported?exported+'map.pdf':null,exported?'Download the full-detail map PDF':'Prepare plot or create a PDF first');
  actionLink('bundle',exported?exported+'map-bundle.zip':null,exported?'Download PDF, recipe, sources and print intent':'Prepare plot or create a PDF first');
  actionLink('pdf-settings',printTask?.review_url,printTask?.review_url?'Optional conversion overrides in PDF Studio':'Available after the map is uploaded');
  actionLink('open-hp7585',ready?first.url:null,'Open the prepared plot');
  renderProgress();
}
function printLink(url,label){
  const parsed=new URL(url,location.origin);if(!['http:','https:'].includes(parsed.protocol))return;
  const a=document.createElement('a');a.href=studioURL(parsed.pathname==='/pdf'?'pdf':'hpgl',parsed.href);a.dataset.studio=parsed.pathname==='/pdf'?'pdf':'hpgl';a.className='button';a.textContent=label;$('print-links').append(a);
}
function submittedPreview(pointer,url){
  if(!url)return;
  const parsed=new URL(mapURL(url),location.origin);if(parsed.origin!==location.origin||!parsed.pathname.startsWith(mapPrefix+'/artifacts/'))return;
  const image=new Image(),stamp=epoch;
  image.onload=()=>{
    if(stamp!==epoch||pointer.signature!==stable(recipe)||printPointer!==pointer)return;
    $('preview').src=image.src;$('preview').hidden=false;$('sketch').hidden=true;
    $('preview-fidelity').hidden=true;
  };
  image.src=parsed.href;
}
function showPrint(value){
  const wasPreparing=printActive.has(printTask?.phase);
  printTask=value;printConnectionLost=false;
  const render=value.render,progress=value.phase==='rendering'?render?.progress:value.progress;
  const counts=progress?.total>0&&Number.isFinite(progress.completed)?`${progress.completed.toLocaleString()} / ${progress.total.toLocaleString()} ${progress.unit||''}`:'';
  const page=progress?.page_count>1?` · page ${progress.page_number} of ${progress.page_count}`:'';
  const names={rendering:'drawing map',queued:'queued',handoff:'preparing pen paths',uploading:'transferring PDF',inspecting:'reading PDF',submitting:'preparing pen paths',converting:'preparing pen paths',preparing:'building plot preview'};
  const error=value.error||value.connection_error;
  const text=printActive.has(value.phase)?'Preparing plot: '+(progress?.detail||progress?.label||names[value.phase])+page:
    value.phase==='ready'?'Plot ready':error?'Plot preparation: '+error:value.phase==='empty'?'No drawable pen paths':value.phase==='recovery'?'Plot preparation: check PDF conversion':value.phase==='superseded'?'Plot preparation: newer conversion selected':'Plot preparation needs attention';
  if(error)notice(error);
  if(value.export_key&&printPointer){
    const url='/artifacts/'+value.export_key+'/preview.png';
    if(printPointer.preview_url!==url){
      printPointer.preview_url=url;rememberPrint(printPointer);
      if(wasPreparing||printActive.has(value.phase))submittedPreview(printPointer,url);
    }
  }
  const phases=['extracting','preparing','reading','clips','coverage','indexing','visibility','planning','routing','verification','artifacts','previews','complete'];
  const fraction=progress?.total>0?Math.max(0,Math.min(1,progress.completed/progress.total)):0;
  const pageCount=Math.max(1,Number(progress?.page_count)||1),pageIndex=Math.max(0,Math.min(pageCount-1,(Number(progress?.page_number)||1)-1));
  const converted=(pageIndex+(Math.max(0,phases.indexOf(progress?.phase))+fraction)/phases.length)/pageCount;
  const estimate=value.phase==='rendering'?(render?.progress?.overall_percent||0)*.5:
    value.phase==='ready'?100:value.phase==='preparing'?97:value.phase==='converting'?60+35*converted:50;
  printPercent=Math.max(printPercent,estimate);
  const running=printActive.has(value.phase),now=Date.now()/1000;
  const started=value.started_at||printPointer?.started_at||value.created_at;
  const finished=value.finished_at||value.updated_at||now;
  printStatusView={text,count:counts,phase:value.phase,percent:printPercent,running,received:performance.now(),
    elapsed:started?Math.max(0,(running?now:finished)-started):null};
  $('print-links').replaceChildren();
  let first=true;
  for(const job of value.jobs||[]){
    if(job.job_id&&first){first=false;continue;}
    printLink(job.url,job.job_id?'Open page '+(job.page_index+1):'Page '+(job.page_index+1)+' · no pen paths');
  }
  if(value.phase!=='ready')for(const job of value.previous_jobs||[])if(job.job_id)printLink(job.url,'Previous plot · page '+(job.page_index+1));
  updateSend();
}
function rememberPrint(pointer){if(activeDoc)activeDoc.printPointer=pointer;remember();}
function openPrepared(value,pointer){
  const job=value.jobs?.find(job=>job.job_id);
  if(value.phase!=='ready'||!job)return;
  const url=new URL(job.url,location.origin);if(!['http:','https:'].includes(url.protocol))return;
  if(pointer){pointer.openWhenReady=false;pointer.finished=true;rememberPrint(pointer);}
  openStudio('hpgl',url.href);
}
function printDisconnected(error){
  printConnectionLost=true;
  printStatusView={percent:printPercent,elapsed:null,...printStatusView,text:'Plot preparation: connection interrupted',running:false,phase:'disconnected'};
  notice(error.message+' Resume to check the saved preparation.');updateSend();
}
async function watchPrint(pointer,first=null,restored=()=>{}){
  const stamp=++printEpoch;printPointer=pointer;
  const current=()=>stamp===printEpoch&&pointer.signature===stable(recipe);
  try{
    let value=first,wasPreparing=false;
    while(current()){
      if(!value)value=await api(pointer.kind==='preparation'?'/api/preparations/'+pointer.key:'/api/print-tasks/'+pointer.key);
      if(!current())return;
      if((pointer.kind==='preparation'?value.id:value.export_key)!==pointer.key||value.recipe_hash!==pointer.recipe_hash)throw new Error('Print receipt belongs to different artwork.');
      showPrint(value);
      restored();
      if(!printActive.has(value.phase)){
        if(value.phase==='ready'){
          pointer.finished=true;rememberPrint(pointer);
          pointer.openWhenReady=false;rememberPrint(pointer);
        }
        if(wasPreparing&&!currentPreview()){
          clearTimeout(timer);timer=setTimeout(()=>requestPreview('draft'),100);
        }
        return;
      }
      wasPreparing=true;
      await new Promise(resolve=>setTimeout(resolve,1000));value=null;
    }
  }catch(error){
    if(!current())return;
    if(error.status===404){
      printPointer=null;rememberPrint(null);printTask=null;printStatusView=null;printPercent=0;printConnectionLost=false;updateSend();
      notice('The previous pen preview is no longer on this server. Your map is intact; choose Prepare plot to rebuild it.');return;
    }
    printDisconnected(error);
  }finally{restored();}
}
function pauseDraft(){
  epoch++;clearTimeout(timer);sceneController?.abort();sceneController=null;
  if(active){api('/api/cancel',{session,revision:++revision}).catch(()=>{});active=null;}
  refining=false;
}
async function preparePlot(retry=false){
  const previewUrl=$('preview').hidden?null:$('preview').src;
  pauseDraft();printPercent=0;notice();
  const stamp=++printEpoch,pointer={kind:'preparation',signature:stable(recipe),openWhenReady:false,preview_url:previewUrl,started_at:Date.now()/1000};
  printPointer=pointer;rememberPrint(pointer);showPrint({phase:'rendering',message:'Preparing the current map at full detail'});
  try{
    // This endpoint is idempotent for the complete recipe, so a lost reply can
    // be recovered after reload without duplicating an upload or conversion.
    const value=await api('/api/prepare',{recipe:clone(recipe),retry,...(saved?{source_document:{id:saved.id,...(saved.signature===stable(recipe)?{revision:saved.revision}:{})}}:{})});
    if(stamp!==printEpoch||pointer.signature!==stable(recipe))return;
    pointer.key=value.id;pointer.recipe_hash=value.recipe_hash;rememberPrint(pointer);
    await watchPrint(pointer,value);
  }catch(error){
    if(stamp!==printEpoch||pointer.signature!==stable(recipe))return;
    if(error.status){showPrint({phase:'failed',error:error.message,retryable:true});return;}
    printDisconnected(error);
  }
}
async function retryPrint(){
  const pointer=printPointer;
  if(!pointer||pointer.signature!==stable(recipe))return;
  if(pointer.kind==='preparation')return preparePlot(true);
  const stamp=++printEpoch;pointer.openWhenReady=false;pointer.started_at=Date.now()/1000;rememberPrint(pointer);printPercent=50;
  showPrint({phase:'queued',review_url:printTask.review_url,message:'Reusing the saved PDF and document inks'});
  try{
    const value=await api('/api/print-tasks/'+pointer.key+'/retry',{recipe_hash:pointer.recipe_hash});
    if(stamp!==printEpoch||pointer.signature!==stable(recipe))return;
    await watchPrint(pointer,value);
  }catch(error){if(stamp===printEpoch&&pointer.signature===stable(recipe))printDisconnected(error);}
}
async function plotAction(){
  if(printTask?.phase==='ready')return openPrepared(printTask,printPointer);
  if(printConnectionLost&&printPointer?.key)return watchPrint(printPointer);
  if(printTask?.retryable&&printPointer?.key)return retryPrint();
  if(printTask&&['recovery','superseded'].includes(printTask.phase)&&printTask.review_url){
    $('export-tools').open=true;return $('pdf-settings').click();
  }
  return preparePlot(Boolean(printTask?.retryable));
}
function restorePrint(){
  try{
    const pointer=activeDoc?.printPointer;
    if(pointer?.signature!==stable(recipe))return;
    printPointer=pointer;
    if(pointer.kind==='preparation'&&!pointer.key)preparePlot();
    else return new Promise(resolve=>watchPrint(pointer,null,resolve));
  }catch{}
}
function thumbnailFor(doc,completedImage=null){
  if(doc!==activeDoc)return null;
  let source=completedImage;
  if(!source&&previewSnapshot?.signature===stable(recipe)&&!$('preview').hidden&&$('preview').naturalWidth)source=$('preview');
  else if(!source&&scene){drawSketch();source=$('sketch');}
  if(!source)return null;
  const w=source.naturalWidth||source.width,h=source.naturalHeight||source.height;if(!w||!h)return null;
  const canvas=document.createElement('canvas'),scale=Math.min(384/w,288/h,1);canvas.width=Math.max(1,Math.round(w*scale));canvas.height=Math.max(1,Math.round(h*scale));
  const ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(source,0,0,canvas.width,canvas.height);
  return canvas.toDataURL('image/jpeg',.85);
}
function attachThumbnail(doc,completedImage=null){
  const snapshot=doc?.saved;if(!snapshot?.hash||snapshot.signature!==stable(doc.recipe))return;
  const thumbnail=thumbnailFor(doc,completedImage);if(!thumbnail)return;
  api('/api/recipes/'+snapshot.id+'/thumbnail',{revision:snapshot.revision,recipe_hash:snapshot.hash,thumbnail}).then(()=>library()).catch(error=>{if(doc===activeDoc)notice('Document saved; thumbnail could not be updated: '+error.message);});
}
function chooseName(name,copy){return chooseDocumentName({dialog:$('save-dialog'),form:$('save-form'),input:$('save-name'),heading:$('save-heading'),cancel:$('cancel-save')},name,copy);}
function unusedName(name){const names=new Set(libraryItems.map(i=>i.title.toLocaleLowerCase()));let candidate=name,n=2,base=name.replace(/ \(\d+\)$/,'');while(names.has(candidate.toLocaleLowerCase())){const suffix=` (${n++})`;candidate=base.slice(0,100-suffix.length)+suffix;}return candidate;}
async function saveRecipe(copy=false,doc=activeDoc){
  navigation.finish();
  stashDocument();if(!doc||doc.saving)return false;
  doc.saving=true;documentState();
  try{
    let name=doc.saved?.name;
    if(copy||!doc.saved){await library();name=await chooseName(unusedName(documentName(doc)),copy);if(name===null)return false;}
    const snapshot=clone(doc.recipe),signature=stable(snapshot),thumbnail=thumbnailFor(doc);
    const response=await api('/api/recipes',{recipe:snapshot,name,thumbnail,...(!copy&&doc.saved?{id:doc.saved.id,expected_revision:doc.saved.revision}:{})});
    doc.saved={id:response.id,revision:response.revision,name:response.title,signature,hash:response.recipe_hash};
    if(doc===activeDoc){saved=doc.saved;history.replaceState(null,'','#recipe='+response.id);notice();}
    remember();documentState();if(!thumbnail)attachThumbnail(doc);library().catch(()=>{});return true;
  }catch(error){notice(error.message);return false;}
  finally{doc.saving=false;documentState();}
}
async function library(){
  const stamp=++libraryEpoch,list=await api('/api/recipes');if(stamp!==libraryEpoch)return;
  const changed=stable(list)!==stable(libraryItems);libraryItems=list;
  if(changed||!$('recipe-list').children.length)renderLibrary();
}
setInterval(()=>{if($('library').open)library().catch(()=>{});},3000);
function renderLibrary(){
  renderDocumentLibrary($('recipe-list'),{items:libraryItems.map(item=>({...item,thumbnail_url:mapURL(item.thumbnail_url),detail:'Saved '+new Date(item.updated_at).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'})})),
    query:$('document-search').value,isOpen:item=>workspaceDocs.some(doc=>doc.saved?.id===item.id),
    open:async item=>{await openRecipe(item.id);$('library').close();},safe:safely});
}
async function openRecipe(id){
  const existing=workspaceDocs.find(doc=>doc.saved?.id===id);if(existing){switchDocument(existing);return;}
  const value=await api('/api/recipes/'+encodeURIComponent(id));
  // Recheck after the read; a second Open click may have completed first.
  const opened=workspaceDocs.find(doc=>doc.saved?.id===id);if(opened){switchDocument(opened);return;}
  const doc=makeDocument(value.recipe,{id:value.id,revision:value.revision,name:value.title,hash:value.recipe_hash,signature:stable(value.recipe)});
  if(value.thumbnail_url)doc.preview={url:value.thumbnail_url,signature:stable(value.recipe),quality:'thumbnail'};
  workspaceDocs.push(doc);switchDocument(doc);
}
function activate(){
  activeDoc.historyRecipe=clone(recipe);updateUndo();editMode=false;shiftHeld=false;navigation.reset();interactionHint();
  $('preview').hidden=true;$('sketch').hidden=!scene;$('preview-fidelity').hidden=true;
  $('metrics').textContent='';$('report').replaceChildren();$('boundary-results').replaceChildren();notice();statusView=null;sync();updateSend();
  idleProgress('Loading preview…');
  const stamp=epoch,preview=previewSnapshot;
  if(preview?.signature===stable(recipe)&&(preview.quality==='thumbnail'||currentPreview(preview))){
    const url=new URL(mapURL(preview.url),location.origin);
    if(url.origin===location.origin&&(url.pathname.startsWith(mapPrefix+'/artifacts/')||url.pathname.startsWith(mapPrefix+'/api/recipes/'))){
      loadPreviewImage(url.href).then(image=>{if(stamp!==epoch)return;$('preview').src=image.src;$('preview').hidden=false;$('sketch').hidden=true;
        if(preview.quality==='thumbnail'){idleProgress('Saved thumbnail','Preparing the full preview');timer=setTimeout(()=>requestPreview('draft'),100);}
        else{idleProgress(preview.quality==='proof'?'PDF complete':'Preview complete');$('desk').dataset.previewQuality=preview.quality;}
      }).catch(()=>{
        if(stamp!==epoch)return;
        previewSnapshot=null;remember();requestPreview('draft');
      });return;
    }
  }
  if(scene){prepareTerrainSketch();queueSketch();}
  timer=setTimeout(()=>requestPreview('draft'),100);
}
function downloadRecipe(){
  const url=URL.createObjectURL(new Blob([JSON.stringify(recipe,null,2)+'\n'],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download='plotline-map.recipe.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);
}
function bindControls(){
  for(const tray of document.querySelectorAll('.tray')){
    tray.querySelector('.close-tray').onclick=()=>tray.open=false;
    tray.addEventListener('toggle',()=>{if(tray.open)for(const other of document.querySelectorAll('.tray'))if(other!==tray)other.open=false;});
  }
  window.addEventListener('keydown',event=>{if(event.key==='Escape')for(const tray of document.querySelectorAll('.tray'))tray.open=false;});

  for(const id of ['pdf','bundle','pdf-settings','open-hp7585'])$(id).onclick=event=>{if($(id).getAttribute('aria-disabled')==='true')event.preventDefault();};
  $('title').onchange=()=>{recipe.title=$('title').value;mutated();sync();};
  for(const key of Object.keys(recipe.furniture))$('furniture-'+key).onchange=()=>{recipe.furniture[key]=$('furniture-'+key).checked;mutated();};
  $('hide-furniture').onclick=()=>{for(const key of Object.keys(recipe.furniture))recipe.furniture[key]=false;mutated();sync();};
  for(const [id,index] of [['west',0],['south',1],['east',2],['north',3]])$(id).onchange=()=>{recipe.bounds[index]=Number($(id).value);mutated();sync();};
  $('scale').onchange=()=>{recipe.framing.scale_denominator=Number($('scale').value);mutated();sync();};
  for(const [id,index] of [['offset-x',0],['offset-y',1]])$(id).onchange=()=>{recipe.framing.offset_mm[index]=readPaperField($(id));mutated();sync();};
  for(const [id,key] of [['azimuth','azimuth_deg'],['elevation','elevation_deg'],['exaggeration','exaggeration']])$(id).oninput=()=>{
    recipe.camera[key]=Number($(id).value);mutated();$(id+'-value').textContent=$(id).value+(id==='exaggeration'?'×':'°');
  };
  for(const id of ['roads','shore','labels','buildings'])$(id).onchange=()=>{currentStyle().features[id]=$(id).value;mutated();};
  for(const id of ['bridges','parks','contours','hillshade'])$(id).onchange=()=>{currentStyle().features[id]=$(id).checked;mutated();sync();};
  for(const [id,key] of [['park-labels','park_labels'],['peak-labels','peak_labels'],['terrain-edges','terrain_edges']])$(id).onchange=()=>{currentStyle().features[key]=$(id).checked;mutated();};
  for(const [id,key] of [['sun-azimuth','azimuth_deg'],['sun-elevation','elevation_deg']])$(id).oninput=()=>{recipe.lighting[key]=Number($(id).value);$(id+'-value').textContent=$(id).value+'°';mutated();};
  $('water-treatment').onchange=()=>{currentStyle().features.water_treatment=$('water-treatment').value;mutated();sync();};
  $('park-treatment').onchange=()=>{currentStyle().features.park_treatment=$('park-treatment').value;mutated();sync();};
  for(const [id,key] of [['building-min','building_min_mm2'],['hatch-spacing','hatch_spacing_mm'],['water-spacing','water_spacing_mm'],['contour-interval','contour_interval_m'],['hillshade-spacing','hillshade_spacing_mm']])$(id).onchange=()=>{currentStyle().features[key]=$(id).hasAttribute('data-paper-length')?readPaperField($(id)):Number($(id).value);mutated();};
  $('fit').onclick=safely(async()=>{const stamp=epoch;const fitted=await api('/api/fit',{recipe:clone(recipe)});if(stamp!==epoch)return;recipe=fitted;mutated();sync();});
  $('retry-preview').onclick=()=>requestPreview(lastQuality);
  $('generate').onclick=()=>requestPreview('proof');$('show-guides').onchange=guides;
  $('view-mode').onchange=interactionHint;
  $('edit-map').onclick=()=>{navigation.finish();editMode=!editMode;interactionHint();};
  $('undo-map').onclick=()=>undoMap();$('redo-map').onclick=()=>undoMap(true);


  $('save').onclick=safely(()=>saveRecipe());$('save-copy').onclick=safely(()=>saveRecipe(true));
  $('revert').onclick=safely(revertRecipe);
  $('new-document').onclick=()=>addDocument(newMapRecipe());
  $('document-search').oninput=renderLibrary;
  $('open-library').onclick=safely(async()=>{await library();renderLibrary();$('library').showModal();});$('close-library').onclick=()=>$('library').close();
  $('download-recipe').onclick=downloadRecipe;$('import-button').onclick=()=>$('import-file').click();
  $('import-file').onchange=safely(async()=>{
    const file=$('import-file').files[0];if(!file)return;if(file.size>8000000)throw new Error('Recipe files must be smaller than 8 MB.');
    const value=JSON.parse(await file.text());const checked=await api('/api/validate',{recipe:value});$('library').close();addDocument(checked.recipe,null,true);$('import-file').value='';
  });
  $('send').onclick=safely(plotAction);
  $('review-inks').onclick=reviewInks;
  window.addEventListener('keydown',event=>{
    if(!(event.ctrlKey||event.metaKey)||!['s','o'].includes(event.key.toLowerCase()))return;
    event.preventDefault();if(document.querySelector('dialog[open]'))return;
    if(document.activeElement?.matches('input,select'))document.activeElement.blur();
    if(event.key.toLowerCase()==='s')saveRecipe(event.shiftKey);else $('open-library').click();
  });
}
const studioNav=studioNavigation({studio:'map',notice,open:openNavigation,exportWorkspace:()=>{remember();return JSON.parse(sessionStorage.getItem('plotline-map-workspace'));},importWorkspace});
async function importWorkspace(workspace){
  if(workspace?.version!==1||!Array.isArray(workspace.documents))throw Error('The old Map workspace could not be read.');
  stashDocument();let selected;
  for(const item of workspace.documents){
    const checked=await api('/api/validate',{recipe:item.recipe});
    let entry=workspaceDocs.find(d=>d.saved?.id===item.saved?.id&&stable(d.recipe)===stable(checked.recipe));
    if(!entry){entry=makeDocument(checked.recipe,item.saved,item.needsSave);Object.assign(entry,{baseline:item.baseline||entry.baseline,sourceRecipe:item.sourceRecipe,sourceSnapshot:item.sourceSnapshot,printPointer:item.printPointer});workspaceDocs.push(entry);}
    if(item.key===workspace.active)selected=entry;
  }
  if(selected)switchDocument(selected);remember();documentState();notice('Your open map documents are here, including unsaved edits. The original tab is unchanged.');
}
async function openNavigation(url){
  const q=url.searchParams,id=q.get('document')||new URLSearchParams(url.hash.slice(1)).get('recipe'),hash=q.get('source_recipe');
  if(id&&!q.has('snapshot')&&!q.has('pen_substitutions')){
    await openRecipe(id);
    if(hash){activeDoc.sourceRecipe=hash;documentState();remember();if(saved?.hash!==hash||documentDirty(activeDoc))notice('This plot used an earlier map version. Your current edits are preserved; Edit source can open the source version separately.');}
    return;
  }
  if(!hash)return;
  const source=await api('/api/source-recipes/'+encodeURIComponent(hash)),updated=clone(source),changes=JSON.parse(q.get('pen_substitutions')||'{}');
  for(const ink of updated.palette.inks){const change=changes[ink.id];if(change&&/^#[0-9a-f]{6}$/i.test(change.color_srgb)&&typeof change.width_mm==='number'&&change.width_mm>0&&change.width_mm<=5){Object.assign(ink,{color_srgb:change.color_srgb,width_mm:change.width_mm,label:typeof change.label==='string'?change.label:ink.label});delete ink.source;}}
  const checked=await api('/api/validate',{recipe:updated});
  let target=!q.has('pen_substitutions')&&workspaceDocs.find(d=>stable(d.recipe)===stable(checked.recipe));
  if(!target){target=makeDocument(checked.recipe,null,true);target.sourceSnapshot=true;workspaceDocs.push(target);}
  target.sourceRecipe=hash;switchDocument(target);documentState();remember();
  notice(q.has('pen_substitutions')?'Pen substitutions applied to a separate source map. Review the preview, then Prepare plot.':'Opened the map version used for this plot. Other open documents keep their edits.');
}
async function start(){
  [catalog,capabilities]=await Promise.all([api('/api/catalog'),api('/api/capabilities').catch(()=>null)]);recipe=clone(catalog.recipe);
  const requestedURL=new URL(location.href);
  let message='',selected=null;
  const normalizeSaved=(value,r)=>{
    if(!value?.id)return null;
    const original=value.signature?JSON.parse(value.signature):r;
    return {...value,name:value.name||original.title||r.title,signature:stable(original)};
  };
  try{
    const workspace=JSON.parse(sessionStorage.getItem('plotline-map-workspace')||localStorage.getItem('plotline-map-workspace'));
    if(workspace?.version===1&&Array.isArray(workspace.documents)){
      for(const item of workspace.documents){
        const checked=await api('/api/validate',{recipe:item.recipe}),doc=makeDocument(checked.recipe,normalizeSaved(item.saved,item.recipe),item.needsSave);
        Object.assign(doc,{sourceRecipe:item.sourceRecipe,sourceSnapshot:item.sourceSnapshot,key:item.key||doc.key,baseline:item.baseline||stable(item.recipe),preview:item.preview||null,printPointer:item.printPointer||null});workspaceDocs.push(doc);
      }
      selected=workspaceDocs.find(doc=>doc.key===workspace.active);
    }else{
      const draft=JSON.parse(localStorage.getItem('plotline-map-draft'));
      if(draft?.recipe){const checked=await api('/api/validate',{recipe:draft.recipe});workspaceDocs.push(makeDocument(checked.recipe,normalizeSaved(draft.saved,draft.recipe),!draft.saved));}
    }
    const legacyPrint=JSON.parse(localStorage.getItem('plotline-map-print'));
    if(!workspace)for(const doc of workspaceDocs)if(legacyPrint?.signature===stable(doc.recipe))doc.printPointer=legacyPrint;
  }catch(error){throw new Error('Could not restore browser work; its stored copy has been left unchanged. '+error.message);}
  if(!workspaceDocs.length)workspaceDocs.push(makeDocument(newMapRecipe()));
  bindControls();bindUnifiedControls();switchDocument(selected||workspaceDocs[0]);await openNavigation(requestedURL);studioNav.ready();if(message)notice(message);
  if(!capabilities)refreshInventory().catch(error=>{$('inventory-status').textContent=error.message;});library().catch(()=>{});
  new ResizeObserver(resize).observe($('desk'));
}
function bindUnifiedControls(){
  ['longitude','latitude'].forEach((id,i)=>$(id).onchange=()=>{recipe.center[i]=Number($(id).value);mutated();sync();});
  $('extent').onchange=()=>{recipe.extent=$('extent').value;mutated();sync();};
  $('relief').onchange=()=>{recipe.camera.relief=$('relief').checked;mutated();sync();};
  $('style-region').onchange=sync;
  $('ink-region').onchange=()=>{$('style-region').value=$('ink-region').value;sync();};
  $('apply-style').onclick=()=>{
    const s=clone(catalog.styles.find(s=>s.id===$('style-preset').value)),target=currentStyle();
    target.features=s.features;
    const ids=recipe.palette.inks.map(i=>i.id);target.ink_roles=Object.fromEntries(Object.entries(s.ink_roles).map(([k,v])=>[k,ids.includes(v)?v:recipe.ink_roles.labels]));
    mutated();sync();
  };
  $('boundary-clear').onclick=()=>{recipe.boundary=null;mutated();sync();};
  $('boundary-search').onclick=safely(async()=>{
    const stamp=epoch;
    $('boundary-search').disabled=true;$('boundary-results').textContent='Searching…';
    try{
      const matches=await api('/api/boundaries/search',{name:$('boundary-query').value,kind:$('boundary-kind').value});
      if(stamp!==epoch)return;
      $('boundary-results').replaceChildren(...matches.map(item=>{
        const row=document.createElement('div');row.className='boundary-result';
        const label=document.createElement('span');label.textContent=`${item.name} · ${item.state}`;
        const select=document.createElement('button');select.textContent='Select';select.onclick=safely(async()=>{
          const stamp=epoch;select.disabled=true;select.textContent='Loading…';try{const boundary=await api('/api/boundaries/select',{kind:item.kind,id:item.id});if(stamp!==epoch)return;recipe.boundary=boundary;mutated();sync();$('boundary-results').replaceChildren();}finally{select.disabled=false;select.textContent='Select';}
        });
        const go=document.createElement('button');go.textContent='Go to';go.title='Fit this place to the printable map area';
        go.onclick=safely(async()=>{
          const stamp=epoch;go.disabled=true;go.textContent='Loading…';
          try{
            const boundary=await api('/api/boundaries/select',{kind:item.kind,id:item.id});
            if(stamp!==epoch)return;
            // Reuse the paper/camera-aware fit without selecting a styling mask.
            const fitted=await api('/api/fit',{recipe:{...clone(recipe),boundary}});
            if(stamp!==epoch)return;
            fitted.boundary=recipe.boundary;recipe=fitted;mutated();sync();
          }finally{go.disabled=false;go.textContent='Go to';}
        });
        row.append(label,go,select);return row;
      }));
      if(!matches.length)$('boundary-results').textContent='No matches.';
    }finally{$('boundary-search').disabled=false;}
  });
  $('boundary-query').onkeydown=e=>{if(e.key==='Enter')$('boundary-search').click();};
  $('boundary-import').onclick=()=>$('boundary-file').click();
  $('boundary-file').onchange=safely(async()=>{
    const stamp=epoch;
    const file=$('boundary-file').files[0];if(!file)return;if(file.size>8000000)throw new Error('GeoJSON must be smaller than 8 MB.');
    const data=JSON.parse(await file.text()),feature=data.type==='FeatureCollection'&&data.features.length===1?data.features[0]:data;
    const geometry=feature.type==='Feature'?feature.geometry:feature;
    const value=await api('/api/boundaries/import',{geometry,name:feature.properties?.name||file.name});
    if(stamp!==epoch)return;
    recipe.boundary=value;mutated();sync();
  });
}
start().catch(error=>{notice(error.message);idleProgress('Unable to load studio','Refresh to try again');});
