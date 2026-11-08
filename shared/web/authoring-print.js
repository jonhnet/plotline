// Pen-aware authoring handoff. Inventory is never written here; receiving,
// conversion, and registration stop at an HPGL draft, never physical plotting.
import {stable} from './studio.js';
export function submissionID(){
  const a=crypto.getRandomValues(new Uint8Array(16));a[6]=(a[6]&15)|64;a[8]=(a[8]&63)|128;
  const s=[...a].map(n=>n.toString(16).padStart(2,'0')).join('');return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`;
}
export async function studioRequest(url,options={}){
  const response=await fetch(url,{...options,headers:{'X-Plotline-Request':'1',...options.headers}});
  const type=response.headers.get('content-type')||'',value=type.includes('json')?await response.json():await response.text();
  if(!response.ok){const error=Error(value.error||String(value)||`Request failed (${response.status})`);error.status=response.status;throw error;}
  return value;
}
const pause=()=>new Promise(resolve=>setTimeout(resolve,800));
export async function prepareAuthoredPlot({submission,exportRecord,onChange=()=>{},onProgress=()=>{}}){
  const save=patch=>{Object.assign(submission,patch);onChange(submission);};
  // Inspection failure is a known rejection, not an ambiguous upload. An
  // explicit retry can submit the retained bytes for a fresh inspection.
  if(submission.inspectionError)save({key:submissionID(),document:null,conversion:null,inspectionError:false});
  if(!submission.key)save({key:submissionID()});
  onProgress(18,'Receiving drawing');
  if(!submission.document){
    let receipt;
    try{receipt=await studioRequest('/api/v1/submissions/'+submission.key);}catch(error){if(error.status!==404)throw error;}
    if(!receipt){
      const response=await fetch(exportRecord.pdf_url);if(!response.ok)throw Error('The exported PDF is unavailable.');
      const form=new FormData();form.append('document',await response.blob(),exportRecord.recipe.name+'.pdf');
      form.append('intent',new Blob([JSON.stringify(exportRecord.intent)],{type:'application/json'}),'intent.json');
      try{receipt=await studioRequest('/api/v1/documents',{method:'POST',headers:{'Idempotency-Key':submission.key},body:form});}
      catch(error){try{receipt=await studioRequest('/api/v1/submissions/'+submission.key);}catch{throw error;}}
    }
    save({document:receipt.id});
  }
  let document;
  for(;;){
    document=await studioRequest('/api/v1/documents/'+submission.document);
    if(document.source_sha256!==exportRecord.intent.source_sha256)throw Error('The received PDF differs from this drawing.');
    if(document.status==='ready')break;
    if(document.status==='error'){save({inspectionError:true});throw Error(document.error||'PDF inspection failed.');}
    onProgress(25,'Inspecting drawing');await pause();
  }
  const retryPublication=document.draft?.status==='error'&&document.draft?.requested_conversion_id===submission.conversion;
  if(!submission.conversion){
    // Reconcile an acknowledged or lost draft response before requesting again.
    const matching=(document.conversions||[]).filter(c=>c.publish&&c.status!=='error'&&stable(c.intent)===stable(exportRecord.intent)).sort((a,b)=>b.created_at-a.created_at)[0];
    if(matching)save({conversion:matching.id});
    else{
      onProgress(35,'Preparing pen paths');
      const conversion=await studioRequest(`/api/v1/documents/${submission.document}/draft`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(exportRecord.intent)});
      save({conversion:conversion.id});
    }
  }
  let high=35,publishRetried=false;
  for(;;){
    const conversion=await studioRequest('/api/v1/conversions/'+submission.conversion);
    if(conversion.status==='error'){save({conversion:null});throw Error(conversion.error||'Pen conversion failed.');}
    if(conversion.status==='ready'){
      document=await studioRequest('/api/v1/documents/'+submission.document);
      if(document.draft?.current_conversion_id===submission.conversion&&document.draft.status==='ready'){
        const jobs=(document.draft.jobs||[]).filter(j=>j.job_id);if(!jobs.length)throw Error('Conversion produced no plottable paths. Open PDF conversion settings to inspect it.');
        save({jobs,ready:true,review_url:conversion.review_url});onProgress(100,'Plot ready');return submission;
      }
      if(document.draft?.status==='error'){
        if(retryPublication&&!publishRetried&&document.draft?.requested_conversion_id===submission.conversion){publishRetried=true;await studioRequest(`/api/v1/conversions/${submission.conversion}/publish`,{method:'POST'});continue;}
        throw Error(document.draft.error||'Could not register the plot.');
      }
      if(document.draft?.requested_conversion_id!==submission.conversion)throw Error('The PDF recipe was changed in PDF Studio. Open it to use that version.');
      onProgress(96,'Registering plot');
    }else{
      const p=conversion.progress,phases=['extract','coverage','visibility','planning','routing','verification','preview'];
      const i=p?phases.findIndex(s=>(p.phase||'').includes(s)):-1;
      if(i>=0)high=Math.max(high,38+(i+Math.min(1,(p.completed||0)/(p.total||1)))*8);
      onProgress(Math.min(94,high),p?.label||'Preparing pen paths');
    }
    await pause();
  }
}
