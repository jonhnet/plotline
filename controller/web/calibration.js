import {bindPaperUnits,onPaperUnitChange,paperLength} from '/paper-units.js';
import {studioNotice as notice} from '/studio.js';
bindPaperUnits();
import {cap,colorName,usable,editLoadedPen} from '/inksets.js';
const $=id=>document.getElementById(id);let carousel,targets=[],target=null,scanId=null,report=null,marks=[],pollEpoch=0,busy=false;const selected=new Set();
async function api(url,body,method=body?'POST':'GET'){
 const response=await fetch(url,{method,headers:body?{'Content-Type':'application/json','X-Plotline-Request':'1'}:{},body:body?JSON.stringify(body):undefined});
 const value=await response.json();if(!response.ok)throw Error(value.error||response.statusText);return value;
}
function safely(fn){return async event=>{event?.preventDefault();try{notice('');await fn(event);}catch(e){notice(e);}};}
function element(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;}
function lock(value){
 busy=value;for(const id of ['refresh','analyze','retry','history','scan-history','scan-file'])$(id).disabled=value;
 $('create-button').disabled=value||!selected.size;$('marker-slot').disabled=value||!Object.values(carousel?.slots||{}).some(usable);
 for(const button of $('pens').querySelectorAll('button'))button.disabled=value;
 for(const check of $('pens').querySelectorAll('input'))check.disabled=value||!usable(carousel?.slots[check.value]);
 $('apply').disabled=value||!report?.rows.some(r=>r.valid);
}
const penName=pen=>pen.state==='empty'?'Empty':pen.state==='unavailable'?'Unavailable'+(pen.label?' · '+pen.label:''):pen.label||colorName(pen.color);
function renderPens(){
 $('pens').replaceChildren();const marker=$('marker-slot').value||'auto',loaded=[];
 for(const [slot,pen] of Object.entries(carousel.slots)){
  const item=element('div',null,'pen-choice'),label=element('label',null,'measure-choice'),check=element('input');check.type='checkbox';check.value=slot;check.checked=selected.has(slot);check.setAttribute('aria-label',`Measure slot ${slot}`);
  check.onchange=()=>{check.checked?selected.add(slot):selected.delete(slot);lock(busy);};
  const disc=cap(pen,safely(()=>edit(slot)),`Edit loaded pen — slot ${slot}`);
  label.append(check,document.createTextNode('Measure'));item.append(element('span',`Slot ${slot}`,'slot-number'),disc,element('span',penName(pen),'pen-name'),label);$('pens').append(item);
  if(usable(pen))loaded.push([slot,pen]);
 }
 const darkest=[...loaded].sort((a,b)=>{const sum=pen=>[1,3,5].reduce((n,i)=>n+parseInt(pen.color.slice(i,i+2),16),0);return sum(a[1])-sum(b[1]);})[0];
 $('marker-slot').replaceChildren(new Option(darkest?`Auto · Slot ${darkest[0]} · ${penName(darkest[1])}`:'Auto · Load a dark pen first','auto'));
 for(const [slot,pen] of loaded)$('marker-slot').append(new Option(`Slot ${slot} · ${penName(pen)}`,slot));
 $('marker-slot').value=loaded.some(([slot])=>slot===marker)?marker:'auto';lock(busy);
}
function setCarousel(next){
 for(const [slot,pen] of Object.entries(next.slots)){if(!usable(pen))selected.delete(slot);else if(!usable(carousel?.slots[slot]))selected.add(slot);}
 carousel=next;renderPens();
}
async function refresh(){setCarousel(await api('/api/carousel'));}
async function edit(slot){
 if(busy)return;const next=structuredClone(carousel);lock(true);
 try{const pen=await editLoadedPen(next.slots[slot],slot);if(!pen)return;next.slots[slot]=pen;setCarousel(await api('/api/carousel',next,'PUT'));}
 catch(error){try{await refresh();}catch{}throw error;}
 finally{lock(false);}
}
async function history(){targets=await api('/api/calibrations');$('history').replaceChildren(new Option('Choose a sheet…',''));for(const t of targets)$('history').append(new Option(`${t.sheet_code} · ${new Date(t.created_at*1000).toLocaleString()}`,t.id));if(target)$('history').value=target.id;}
function endpoint(t=target.id,s=scanId){return `/api/calibrations/${t}/scans/${s}`;}
async function open(id){
 pollEpoch++;target=await api('/api/calibrations/'+id);scanId=null;report=null;marks=[];$('sheet').hidden=false;$('results').hidden=true;$('alignment').hidden=true;
 $('sheet-detail').textContent=`Sheet ${target.sheet_code} · measure slots ${target.slots.join(', ')} · registration: slot ${target.marker_slot} (${penName(target.carousel.slots[target.marker_slot])})${target.paper?' · '+target.paper:''}`;
 paperFeedback();
 $('open-plot').href='/#'+target.job_id;$('reference').href=`/api/calibrations/${id}/target.svg`;$('history').value=id;location.hash=id;
 $('status').textContent='';$('progress').hidden=true;$('scan-history').replaceChildren(new Option('Choose a scan…',''));$('scan-history-label').hidden=!target.scans.length;
 for(const s of target.scans)$('scan-history').append(new Option(`${s.id.slice(0,8)} · ${s.status}`,s.id));
 if(target.scans.length){scanId=target.scans.at(-1).id;$('scan-history').value=scanId;await poll();}
}
function drawMarks(){const svg=$('marks');svg.replaceChildren();svg.setAttribute('viewBox','0 0 1000 1000');svg.setAttribute('preserveAspectRatio','none');for(const [i,[x,y]] of marks.entries()){
 const dot=document.createElementNS('http://www.w3.org/2000/svg','circle');dot.setAttribute('cx',x*1000);dot.setAttribute('cy',y*1000);dot.setAttribute('r','7');dot.setAttribute('fill','red');svg.append(dot);
 }$('mark-status').textContent=marks.length===4?'All four crosses marked.':'Next: '+String.fromCharCode(65+marks.length);$('retry').textContent=marks.length===4?'Analyze with marked crosses':'Retry automatic registration';}
async function poll(){
 const epoch=++pollEpoch,t=target.id,s=scanId;report=null;$('results').hidden=true;$('alignment').hidden=true;
 for(;;){const value=await api(endpoint(t,s));if(epoch!==pollEpoch)return;
  $('status').textContent=value.progress?.detail||value.status;
  if(['queued','running'].includes(value.status)){$('progress').hidden=false;$('progress').max=1;$('progress').value=(value.progress?.percent||0)/100;await new Promise(r=>setTimeout(r,700));continue;}
  $('progress').hidden=true;
  if(value.status==='error'){$('status').textContent=value.error;$('alignment').hidden=false;$('align-image').hidden=!value.image;if(value.image)$('align-image').src=endpoint(t,s)+'/preview.jpg';marks=[];drawMarks();return;}
  report=value.report;render(value.applied);return;
 }
}
function swatch(color){const e=element('span',null,'swatch');e.style.backgroundColor=color;return e;}
function render(applied){
 $('results').hidden=false;$('alignment').hidden=true;$('measurements').replaceChildren();$('applied').textContent=applied?'Measurements recorded in the carousel at '+new Date(applied.at*1000).toLocaleString()+'.':'';
 paperFeedback();
 for(const row of report.rows){
  const old=target.carousel.slots[row.slot],tr=element('tr'),name=element('td',`Slot ${row.slot} · ${old.label||colorName(old.color)}`),width=element('td'),color=element('td'),apply=element('td');
  if(row.valid){
   width.append(element('strong',`${row.width_mm.toFixed(3)} mm`),element('small',`Previously ${old.width_mm==null?'unknown':old.width_mm+' mm'}`),element('small',`5–95%: ${row.width_p05_mm.toFixed(3)}–${row.width_p95_mm.toFixed(3)} mm`),element('small',`${row.accepted} accepted · ${row.rejected+row.outliers} rejected`));
   const colors=element('div',null,'swatches');colors.append(swatch(old.color),element('span','/'),swatch(row.color_srgb));color.append(colors,element('code',row.color_srgb),element('small','Design color stays unchanged.'));
   for(const kind of ['width','color']){const label=element('label'),input=element('input');input.type='checkbox';input.checked=true;input.dataset.slot=row.slot;input.dataset.kind=kind;label.append(input,document.createTextNode(' '+(kind==='color'?'measured appearance':kind)));apply.append(label);}
   for(const w of row.warnings)width.append(element('small',w,'warning'));
   const sample=element('img');sample.className='sample-crop';sample.src=endpoint()+`/slot-${row.slot}.png`;sample.alt='Magnified stroke; red width edges and green center color band';name.append(sample);
  }else width.append(element('span',row.error,'warning'));
  tr.append(name,width,color,apply);$('measurements').append(tr);
 }
 $('overlay').src=endpoint()+'/overlay.jpg';$('original').href=endpoint()+'/original.scan';$('apply').disabled=!report.rows.some(r=>r.valid)||busy;
}
$('create').onsubmit=safely(async()=>{if(busy)return;lock(true);try{const slots=[...selected].map(Number),marker_slot=$('marker-slot').value==='auto'?null:Number($('marker-slot').value);const t=await api('/api/calibrations',{slots,marker_slot,revision:carousel.revision,paper:$('paper').value});await history();await open(t.id);}finally{lock(false);}});
$('refresh').onclick=safely(async()=>{lock(true);try{await refresh();}finally{lock(false);}});$('history').onchange=safely(async()=>{if($('history').value){lock(true);try{await open($('history').value);}finally{lock(false);}}});
$('scan-history').onchange=safely(async()=>{if($('scan-history').value){lock(true);try{scanId=$('scan-history').value;await poll();}finally{lock(false);}}});
$('upload').onsubmit=safely(async()=>{
 const file=$('scan-file').files[0];if(!file)return;lock(true);$('progress').hidden=false;$('progress').max=1;$('progress').value=0;$('status').textContent='Uploading scan…';
 try{const id=target.id;const value=await new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest();xhr.open('POST',`/api/calibrations/${id}/scans`);xhr.setRequestHeader('X-Plotline-Request','1');xhr.setRequestHeader('Content-Type','application/octet-stream');
  xhr.upload.onprogress=e=>{if(e.lengthComputable)$('progress').value=e.loaded/e.total;};xhr.onerror=()=>reject(Error('Scan upload disconnected. Check Previous scans before uploading again.'));
  xhr.onload=()=>{try{const v=JSON.parse(xhr.responseText);xhr.status<300?resolve(v):reject(Error(v.error||'Scan upload failed'));}catch{reject(Error('Scan upload failed'));}};xhr.send(file);
 });scanId=value.id;await poll();}finally{lock(false);}
});
$('align-image').onclick=event=>{if(marks.length>=4)return;const box=event.currentTarget.getBoundingClientRect();marks.push([(event.clientX-box.left)/box.width,(event.clientY-box.top)/box.height]);drawMarks();};
$('mark-reset').onclick=()=>{marks=[];drawMarks();};
$('retry').onclick=safely(async()=>{lock(true);try{await api(endpoint()+'/retry',{marks:marks.length===4?marks:null});await poll();}finally{lock(false);}});
$('apply').onclick=safely(async()=>{
 const selections=report.rows.filter(r=>r.valid).map(r=>({slot:r.slot,width:$(`measurements`).querySelector(`input[data-slot="${r.slot}"][data-kind="width"]`).checked,color:$(`measurements`).querySelector(`input[data-slot="${r.slot}"][data-kind="color"]`).checked}));
 lock(true);try{const current=await api('/api/carousel'),result=await api(endpoint()+'/apply',{revision:current.revision,selections});setCarousel(result.carousel);$('applied').textContent='Applied to the loaded carousel. Refresh carousel in Map Studio to use these measurements.';}finally{lock(false);}
});
(async()=>{lock(true);try{await refresh();await history();const id=location.hash.slice(1);if(id)await open(id);}catch(e){notice(e);}finally{lock(false);}})();

function paperFeedback(){
 if(target)$('load-instructions').textContent=target.pattern_version>=2?`Load Letter landscape, with the ${paperLength(279.4)} edge across the carriage, and plot in HPGL Studio.`:`This earlier sheet uses the original loading orientation. Create a new Letter landscape sheet for the ${paperLength(279.4)} edge across the carriage.`;
 if(report)$('measurement-info').textContent=`Sheet ${report.sheet_code} · ${(25.4/report.pixel_pitch_mm).toFixed(0)} effective dpi · registration residual ${paperLength(report.registration_error_mm,{precision:4})}. ${report.notes[0]}`;
}
onPaperUnitChange(paperFeedback);
