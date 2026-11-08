// PDF preview includes plot registration. Isolated worker/store, no device.
import {chromium} from 'playwright';
import {spawn,execFileSync} from 'node:child_process';
import {mkdtemp,readFile,mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
const root=resolve(import.meta.dirname,'../..'),project=process.env.PLOTLINE_TEST_PROJECT||root;
await mkdir(root+'/build',{recursive:true});const state=await mkdtemp(root+'/build/preview-plot-');
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1'};
const server=spawn(process.env.PLOTLINE_TEST_BINARY||root+'/controller/target/release/plotline-studios',['--state',state,'--listen',`127.0.0.1:${port}`],{env:{...process.env,PLOTLINE_PDF_PROJECT:project},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',data=>logs+=data);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(path,options={}){const r=await fetch(base+path,{...options,headers:{...headers,...options.headers}});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;}
async function done(path){for(let n=0;n<300;n++){const v=await api(path);if(!['received','inspecting','queued','running'].includes(v.status))return v;await sleep(100);}throw Error('Worker timed out');}
async function upload(bytes,name,intent){const form=new FormData();form.append('document',new Blob([bytes]),name);if(intent)form.append('intent',new Blob([JSON.stringify(intent)]),'intent.json');return done((await api('/api/v1/documents',{method:'POST',body:form})).status_url);}
try{
 for(let n=0;;n++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(n>100)throw Error(logs);await sleep(100);}
 const intent=JSON.parse(await readFile(project+'/build/pdf-boundary-v1/boundary.print-intent.json'));intent.pages=intent.pages.slice(0,1);
 const doc=await upload(await readFile(project+'/build/pdf-boundary-v1/boundary.pdf'),'Registration.pdf',intent);
 browser=await chromium.launch();const context=await browser.newContext(),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));let drafts=0,publishes=0,mode='queued';
 context.on('request',r=>{if(r.method()==='POST'){if(r.url().endsWith('/draft'))drafts++;if(r.url().endsWith('/publish'))publishes++;}});
 // Delay/fail only the browser's registration receipt: the real converter and
 // registration run normally. The UI must not offer a stale or unknown plot.
 await page.route(base+'/api/v1/documents/'+doc.id,async route=>{
   const response=await route.fetch(),body=await response.json();
   if(body.draft.requested_conversion_id&&mode!=='ready'){
     body.draft.status=mode;
     if(mode==='error')body.draft.error='Test: plot registration failed';
   }
   await route.fulfill({response,json:body});
 });
 await page.goto(base+'/pdf?document='+doc.id);await page.waitForFunction(()=>!document.getElementById('convert-button').disabled);
 assert.equal(await page.locator('#update-plot').textContent(),'Open plot ↗');assert(await page.locator('#update-plot').isDisabled());
 assert.equal(drafts,0,'Opening a PDF does not convert or publish unreviewed defaults');
 await page.locator('#convert-button').click();
 await page.waitForFunction(()=>document.getElementById('work-detail').textContent==='Finishing preview: registering plot',null,{timeout:120000});
 assert(await page.locator('#update-plot').isDisabled());assert.equal(context.pages().length,1);
 const first=(await api('/api/v1/documents/'+doc.id)).draft;
 assert.equal(first.status,'ready');assert(first.jobs[0].job_id);
 const originalHPGL=await(await fetch(base+'/api/jobs/'+first.jobs[0].job_id+'/source.hpgl')).text();
 // Recovery is server-backed, even without the old tab's in-flight marker.
 await page.evaluate(()=>{for(const store of [sessionStorage,localStorage]){const s=JSON.parse(store.getItem('plotline-pdf-workspace'));for(const d of s.documents)delete d.updatingPlot;store.setItem('plotline-pdf-workspace',JSON.stringify(s));}});
 await page.reload();await page.waitForFunction(()=>document.getElementById('work-detail').textContent==='Finishing preview: registering plot');
 assert(await page.locator('#update-plot').isDisabled());assert.equal(drafts,1);
 await page.locator('#spacing').fill('3.5');await page.locator('#spacing').dispatchEvent('change');
 mode='ready';await page.waitForFunction(()=>document.getElementById('work-status').dataset.phase==='ready');
 assert(await page.locator('#update-plot').isDisabled(),'Old completion cannot enable Open for edited settings');assert(await page.locator('#convert-button').isEnabled());
 await page.locator('#convert-button').click();await page.waitForFunction(()=>!document.getElementById('update-plot').disabled,null,{timeout:120000});
 const second=(await api('/api/v1/documents/'+doc.id)).draft;assert.notEqual(second.current_conversion_id,first.current_conversion_id);assert.equal(drafts,2);
 assert.equal(await(await fetch(base+'/api/jobs/'+first.jobs[0].job_id+'/source.hpgl')).text(),originalHPGL,'Old job bytes remain immutable');
 assert.equal(context.pages().length,1,'Successful registration never navigates');
 // Registration failures keep the successful preview and can retry its exact
 // HPGL without an expensive conversion. Repeated publication is idempotent.
 mode='error';await page.locator('#spacing').fill('4');await page.locator('#spacing').dispatchEvent('change');await page.locator('#convert-button').click();
 await page.waitForFunction(()=>document.getElementById('work-status').dataset.phase==='error',null,{timeout:120000});
 assert.match(await page.locator('#notice').textContent(),/registration failed/);
 assert.equal(await page.locator('#convert-button').textContent(),'Retry plot preparation');assert(await page.locator('#update-plot').isDisabled());
 const previewURL=await page.locator('#preview').getAttribute('src');assert.match(previewURL,/conversions\/.+\/pages\/0\/pen.svg/);
 const failed=(await api('/api/v1/documents/'+doc.id)).draft;
 mode='ready';await page.locator('#convert-button').click();await page.waitForFunction(()=>!document.getElementById('update-plot').disabled);
 assert.equal(drafts,3,'Retry registers existing HPGL without conversion');assert.equal(publishes,1);
 assert.equal(await page.locator('#preview').getAttribute('src'),previewURL);
 assert.deepEqual((await api('/api/v1/documents/'+doc.id)).draft.jobs,failed.jobs);
 // Open plot is navigation only, and targets the exact completed draft.
 const popup=context.waitForEvent('page');await page.locator('#update-plot').click();const hpgl=await popup;
 await hpgl.waitForURL(base+'/hpgl#'+failed.jobs[0].job_id);assert.equal(drafts,3);assert.equal(publishes,1);await hpgl.close();
 // Merely inspecting a historical revision never promotes it over the latest.
 const old=context.pages()[0];await old.goto(base+'/pdf?document='+doc.id+'&conversion='+first.current_conversion_id+'&snapshot=1');
 await old.waitForFunction(()=>!document.getElementById('convert-button').disabled);
 assert(await old.locator('#update-plot').isDisabled());assert.equal((await api('/api/v1/documents/'+doc.id)).draft.current_conversion_id,failed.current_conversion_id);
 // Empty pages finish honestly, without an enabled Open plot or retry loop.
 execFileSync(project+'/.venv/bin/python',['-c',`import pymupdf\nd=pymupdf.open();d.new_page(width=612,height=792);d.save(${JSON.stringify(state+'/blank.pdf')})`]);
 const blank=await upload(await readFile(state+'/blank.pdf'),'Blank.pdf');assert.equal(blank.status,'ready');
 await page.goto(base+'/pdf?document='+blank.id);await page.waitForFunction(()=>!document.getElementById('convert-button').disabled);await page.locator('#convert-button').click();
 await page.waitForFunction(()=>document.getElementById('work-detail').textContent==='No drawable pen paths',null,{timeout:120000});
 assert(await page.locator('#update-plot').isDisabled());assert(await page.locator('#convert-button').isDisabled());
 assert.equal((await api('/api/v1/documents/'+blank.id)).draft.jobs[0].status,'empty');
 assert.deepEqual(errors,[]);console.log('PASS preview registers plot; delayed registration, reload recovery, stale edits, retry without conversion, exact Open-only navigation, historical isolation, empty page; artifacts',state);
}catch(error){console.error(logs);throw error;}finally{await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));}
