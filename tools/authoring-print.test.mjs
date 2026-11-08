import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareAuthoredPlot} from '../shared/web/authoring-print.js';

function fixture(){
  const intent={source_sha256:'a'.repeat(64),schema:'plotline.print-intent/1'},record={intent,recipe:{name:'Pattern'},pdf_url:'/drawing.pdf'};
  const state={uploads:0,conversions:0,publishes:0,doc:null,conversion:null,dropUpload:false,dropDraft:false};
  const json=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json'}});
  state.fetch=async(url,options={})=>{
    if(url==='/drawing.pdf')return new Response('%PDF-test');
    if(url.startsWith('/api/v1/submissions/'))return state.doc?json(state.doc):json({error:'Missing'},404);
    if(url==='/api/v1/documents'){
      state.uploads++;state.doc={id:'document',source_sha256:intent.source_sha256,status:'ready',conversions:[],draft:{status:'none'}};
      if(state.dropUpload){state.dropUpload=false;throw Error('Lost upload response');}return json(state.doc);
    }
    if(url==='/api/v1/documents/document')return json(state.doc);
    if(url==='/api/v1/documents/document/draft'){
      state.conversions++;state.conversion={id:'conversion',publish:true,status:'ready',intent,created_at:1};state.doc.conversions=[state.conversion];
      state.doc.draft={status:'ready',requested_conversion_id:'conversion',current_conversion_id:'conversion',jobs:[{job_id:'plot',url:'/hpgl#plot'}]};
      if(state.dropDraft){state.dropDraft=false;throw Error('Lost draft response');}return json(state.conversion);
    }
    if(url==='/api/v1/conversions/conversion')return json(state.conversion);
    if(url==='/api/v1/conversions/conversion/publish'){
      state.publishes++;state.doc.draft={status:'ready',requested_conversion_id:'conversion',current_conversion_id:'conversion',jobs:[{job_id:'plot',url:'/hpgl#plot'}]};return json(state.doc.draft);
    }
    throw Error('Unexpected request '+url);
  };
  return {record,state};
}
test('Lost upload and draft replies recover the same document and conversion',async t=>{
  const {record,state}=fixture(),submission={};state.dropUpload=true;state.dropDraft=true;t.mock.method(globalThis,'fetch',state.fetch);
  await assert.rejects(prepareAuthoredPlot({submission,exportRecord:record}),/Lost draft/);
  assert(submission.key&&submission.document);assert.equal(submission.conversion,undefined);
  const restored=JSON.parse(JSON.stringify(submission));await prepareAuthoredPlot({submission:restored,exportRecord:record});
  assert(restored.ready);assert.equal(state.uploads,1);assert.equal(state.conversions,1);assert.equal(restored.jobs[0].job_id,'plot');
});
test('Retrying a failed registration reuses already converted paths',async t=>{
  const {record,state}=fixture(),submission={};t.mock.method(globalThis,'fetch',state.fetch);
  await prepareAuthoredPlot({submission,exportRecord:record});submission.ready=false;
  state.doc.draft={status:'error',requested_conversion_id:'conversion',error:'Registration failed'};
  await prepareAuthoredPlot({submission,exportRecord:record});assert.equal(state.conversions,1);assert.equal(state.publishes,1);assert(submission.ready);
});
test('Changed source identity and a newer PDF draft cannot be silently replaced',async t=>{
  const {record,state}=fixture(),submission={};t.mock.method(globalThis,'fetch',state.fetch);
  await prepareAuthoredPlot({submission,exportRecord:record});
  state.doc.source_sha256='b'.repeat(64);await assert.rejects(prepareAuthoredPlot({submission,exportRecord:record}),/differs/);
  state.doc.source_sha256=record.intent.source_sha256;state.doc.draft.requested_conversion_id='newer';state.doc.draft.current_conversion_id='newer';
  await assert.rejects(prepareAuthoredPlot({submission,exportRecord:record}),/recipe was changed/);
  assert.equal(state.conversions,1);assert.equal(state.publishes,0);
});
