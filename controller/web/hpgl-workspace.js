import {studioTitle} from '/studio-navigation.js';
import {migrateStudioStorage,stable,renderDocumentTabs,renderDocumentLibrary,chooseDocumentName,confirmDocumentClose,editHistory} from '/studio.js';
import {newInkId} from '/inksets.js';
// Inspection and current inventory are not edits to the paper/pen recipe.
export const recipeSetup=value=>({...structuredClone(value),facing:'artwork',carousel_revision:null});
for(const storage of [sessionStorage,localStorage])migrateStudioStorage(storage,'plotline-hpgl-workspace');
export function hpglWorkspace({api,writeOptions,current,activate,clear,notice,thumbnail,refreshLibrary}) {
  const $=id=>document.getElementById(id),key='plotline-hpgl-workspace';
  let documents=[],active=null,jobs=[],restoring=true,opening=0,plot=null,ownerAttempt=null,ownerKey=null;
  const name=e=>e.saved?.name||e.job.name.replace(/\.(hpgl|plt)$/i,''),dirty=e=>stable(e.recipe)!==stable(e.saved?.setup||e.baseline);
  const safe=fn=>(...args)=>Promise.resolve().then(()=>fn(...args)).catch(e=>notice(e.message));
  const json=(url,options)=>api(url,options).then(r=>r.json());
  function remember(){
    if(restoring)return;
    try{const value=JSON.stringify({version:1,active:active?.key,plotOwner:{attempt:ownerAttempt,key:ownerKey},documents:documents.map(e=>({key:e.key,jobId:e.job.id,saved:e.saved,recipe:e.recipe,baseline:e.baseline,view:e.view}))});sessionStorage.setItem(key,value);localStorage.setItem(key,value);}
    catch{notice('Browser storage is full or unavailable. Save your documents before leaving.');}
  }
  function capture(){
    const value=current();if(!active||active.loading||value.job?.id!==active.job.id||!value.setup)return;
    const next=recipeSetup(value.setup);active.history.record(next);
    active.recipe=next;active.view=value.view;remember();renderState();
  }
  function address(){history.replaceState(null,'',active?`/hpgl${active.saved?'?recipe='+active.saved.id:''}#${active.job.id}`:'/hpgl');}
  function renderState(){
    renderDocumentTabs($('document-tabs'),{documents,active,name,dirty,select,close,safe,
      activity:entry=>plot&&entry.key===ownerKey?{label:plot.message||'Active plot',running:['feeding','finishing'].includes(plot.state)}:null});
    $('save-state').textContent=active?.saving?'Saving…':active&&dirty(active)?'Unsaved changes':active?.saved?'Saved':'Not saved';
    for(const id of ['save','save-copy'])$(id).disabled=!active||active.loading||active.saving;
    $('revert').disabled=!active||active.loading||!dirty(active);$('undo').disabled=!active||active.loading||!active.history.canUndo;$('redo').disabled=!active||active.loading||!active.history.canRedo;
    studioTitle(active?name(active)+(dirty(active)?' *':''):'');
  }
  function renderLibrary(){
    if(!$('library').open)return;
    const items=jobs.flatMap(job=>job.recipes?.length?job.recipes.map(saved=>({key:saved.id,title:saved.name,job,saved,detail:job.name})):[{key:job.id,title:job.name.replace(/\.(hpgl|plt)$/i,''),job,detail:`${job.summary.pens.length} inks`}]);
    for(const item of items)item.thumbnail_url='data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
    renderDocumentLibrary($('jobs'),{items,query:$('search').value,isOpen:i=>documents.some(e=>e.job.id===i.job.id&&(e.saved?.id||null)===(i.saved?.id||null)),open:async i=>{$('library').close();await open(i.job,i.saved?.id||null);},safe});
    for(const image of $('jobs').querySelectorAll('img')){const item=items.find(i=>i.key===image.closest('[data-document]').dataset.document);image.dataset.job=item.job.id;thumbnail(item.job,image);}
  }
  async function make(job,snapshot={},recipeId=null){
    const saved=snapshot.saved||(recipeId?await json(`/api/jobs/${job.id}/recipes/${recipeId}`):null);
    if(saved)saved.setup=recipeSetup(saved.setup);
    const base=recipeSetup(snapshot.recipe||saved?.setup||await json(`/api/jobs/${job.id}/setup`));
    const view=snapshot.view||{facing:'artwork',travel:false,appearance:'measured',navigation:{scale:1,x:0,y:0}};
    // Old workspaces stored the measured-color default as if it were a choice.
    // Calibration starts with design swatches until the operator chooses otherwise.
    if(job.calibration&&!view.appearanceChosen)view.appearance='design';
    return {key:snapshot.key||newInkId('document'),job,saved,recipe:base,baseline:recipeSetup(snapshot.baseline||saved?.setup||base),history:editHistory(base),view};
  }
  async function select(entry){
    opening++;if(entry===active)return;capture();active=entry;notice('');
    if(entry){entry.loading=true;renderState();try{await activate(entry);}finally{entry.loading=false;}}
    else clear();
    if(active===entry){address();remember();renderState();}
  }
  async function open(job,recipeId){
    const sequence=++opening;
    let entry=documents.find(e=>e.job.id===job.id&&(recipeId===undefined||(e.saved?.id||null)===recipeId));
    if(!entry){const created=await make(job,{},recipeId===undefined?job.recipes?.[0]?.id:recipeId);entry=documents.find(e=>e.job.id===job.id&&(recipeId===undefined||(e.saved?.id||null)===recipeId));if(!entry){entry=created;documents.push(entry);}}
    if(sequence===opening)await select(entry);else renderState();
  }
  async function save(copy=false,entry=active){
    capture();if(!entry||entry.loading||entry.saving)return false;entry.saving=true;renderState();
    try{
      let label=entry.saved?.name;
      if(copy||!entry.saved){label=await chooseDocumentName({dialog:$('save-dialog'),form:$('save-form'),input:$('save-name'),heading:$('save-heading'),cancel:$('cancel-save')},name(entry),copy);if(label===null)return false;}
      const frozen=structuredClone(entry.recipe);
      entry.saved=await json(`/api/jobs/${entry.job.id}/recipes`,{method:'POST',...writeOptions({name:label,setup:frozen,...(!copy&&entry.saved?{id:entry.saved.id,expected_revision:entry.saved.revision}:{})})});
      entry.saved.setup=recipeSetup(entry.saved.setup);
      if(active===entry)address();remember();await refreshLibrary();return true;
    }catch(e){notice(e.message);return false;}finally{entry.saving=false;renderState();}
  }
  async function close(entry){
    capture();if(entry.saving)return;
    if(dirty(entry)){const decision=await confirmDocumentClose($('close-document-dialog'),$('close-document-message'),name(entry));if(decision==='cancel')return;if(decision==='save'&&!await save(false,entry))return;}
    const at=documents.indexOf(entry);documents.splice(at,1);if(entry===active)await select(documents[Math.min(at,documents.length-1)]||null);remember();renderState();renderLibrary();
  }
  async function restore(value){if(!active)return;active.recipe=recipeSetup(value);await activate(active);remember();renderState();}
  async function undo(redo=false){capture();const next=redo?active?.history.redo():active?.history.undo();if(next)await restore(next);}
  $('save').onclick=safe(()=>save());$('save-copy').onclick=safe(()=>save(true));$('revert').onclick=safe(async()=>{capture();if(active){const value=active.saved?.setup||active.baseline;active.history.record(value);await restore(value);}});
  $('undo').onclick=safe(()=>undo());$('redo').onclick=safe(()=>undo(true));
  $('search').oninput=renderLibrary;$('close-library').onclick=()=>$('library').close();
  async function showLibrary(){await refreshLibrary();$('library').showModal();renderLibrary();$('search').focus();}
  $('open-library').onclick=safe(showLibrary);$('empty-open').onclick=safe(showLibrary);
  document.addEventListener('keydown',event=>{if(document.querySelector('dialog[open]'))return;const k=event.key.toLowerCase();if(!(event.ctrlKey||event.metaKey))return;if(k==='s'){event.preventDefault();safe(()=>save(event.shiftKey))();}if(['z','y'].includes(k)&&!event.target.closest('input,select,textarea,[contenteditable]')){event.preventDefault();safe(()=>undo(k==='y'||event.shiftKey))();}});
  window.addEventListener('beforeunload',capture);
  window.addEventListener('hashchange',safe(async()=>{const id=location.hash.slice(1);if(id){const job=jobs.find(j=>j.id===id)||await json('/api/jobs/'+id);await open(job,new URL(location.href).searchParams.get('recipe')||undefined);}}));
  async function setPlot(value){
    if(plot?.id===value?.id&&plot?.state===value?.state)return;
    const newAttempt=value?.id!==plot?.id;plot=value;remember();renderState();
    if(newAttempt&&value&&!documents.some(e=>e.key===ownerKey)){
      const job=jobs.find(j=>j.id===value.job_id)||await json('/api/jobs/'+value.job_id);
      const entry=await make(job,{recipe:value.setup});
      if(plot?.id===value.id&&!documents.some(e=>e.key===ownerKey)){ownerKey=entry.key;documents.push(entry);remember();renderState();}
    }
  }
  async function focusPlot(){
    if(!plot)return;
    const entry=documents.find(e=>e.key===ownerKey);
    if(entry)await select(entry);
    else {
      const value=plot,job=jobs.find(j=>j.id===value.job_id)||await json('/api/jobs/'+value.job_id);
      const created=await make(job,{recipe:value.setup});if(plot?.id!==value.id)return;ownerKey=created.key;documents.push(created);await select(created);
    }
  }
  function isPlotDocument(ticket){
    if(!ticket)return false;
    if(ownerAttempt!==ticket.id){
      ownerAttempt=ticket.id;
      const candidates=documents.filter(e=>e.job.id===ticket.job_id),frozen=ticket.setup&&stable(recipeSetup(ticket.setup));
      const matches=candidates.filter(e=>stable(e.recipe)===frozen);
      ownerKey=(matches.find(e=>e===active)||matches[0]||candidates.find(e=>e===active)||candidates[0])?.key||null;
    }
    return !!active&&active.key===ownerKey;
  }
  return {capture,open,renderLibrary,remember,renderState,setPlot,focusPlot,isPlotDocument,setJobs(value){jobs=value;renderLibrary();},get active(){return active;},async start(){
    let snapshot;try{snapshot=JSON.parse(sessionStorage.getItem(key)||localStorage.getItem(key));}catch{}
    ownerAttempt=snapshot?.plotOwner?.attempt||null;ownerKey=snapshot?.plotOwner?.key||null;
    for(const saved of snapshot?.documents||[]){try{const job=jobs.find(j=>j.id===saved.jobId)||await json('/api/jobs/'+saved.jobId);documents.push(await make(job,saved));}catch(e){notice('Could not restore a document: '+e.message);}}
    const requested=location.hash.slice(1),recipeId=new URL(location.href).searchParams.get('recipe');
    if(requested){const job=jobs.find(j=>j.id===requested)||await json('/api/jobs/'+requested);await open(job,recipeId||undefined);}
    else if(documents.length)await select(documents.find(e=>e.key===snapshot.active)||documents[0]);else clear();
    restoring=false;remember();renderState();
  }};
}
