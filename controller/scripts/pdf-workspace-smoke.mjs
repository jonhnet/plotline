// Isolated PDF recipe lifecycle and shared studio chrome; never contacts hardware.
import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,mkdir} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..'),project=process.env.PLOTLINE_TEST_PROJECT||root;
await mkdir(root+'/build',{recursive:true});const state=await mkdtemp(root+'/build/pdf-workspace-');
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1'};
const server=spawn(process.env.PLOTLINE_TEST_BINARY||root+'/controller/target/release/plotline-studios',['--state',state,'--listen',`127.0.0.1:${port}`],{env:{...process.env,PLOTLINE_PDF_PROJECT:project},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',data=>logs+=data);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(path,options={}){const r=await fetch(base+path,{...options,headers:{...headers,...options.headers}});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;}
async function done(path){for(let n=0;n<200;n++){const v=await api(path);if(!['received','inspecting','queued','running'].includes(v.status))return v;await sleep(100);}throw Error('Worker timed out');}
try{
 for(let n=0;;n++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(n>100)throw Error(logs);await sleep(100);}
 const pdf=await readFile(project+'/build/pdf-boundary-v1/boundary.pdf'),intent=JSON.parse(await readFile(project+'/build/pdf-boundary-v1/boundary.print-intent.json','utf8'));
 const documents=[];
 for(const name of ['Boundary.pdf','Other.pdf']){const form=new FormData();form.append('document',new Blob([pdf]),name);form.append('intent',new Blob([JSON.stringify(intent)]),'intent.json');const uploaded=await api('/api/v1/documents',{method:'POST',body:form});const row=await done(uploaded.status_url);assert.equal(row.status,'ready',row.error);documents.push(row);}
 browser=await chromium.launch();const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
 // Optional static override shortens local iteration; final validation uses the embedded assets.
 if(process.env.PLOTLINE_TEST_WEB)for(const [url,file,type] of [['/pdf','controller/web/pdf.html','text/html'],['/pdf.js','controller/web/pdf.js','text/javascript'],['/pdf.css','controller/web/pdf.css','text/css'],['/studio.js','shared/web/studio.js','text/javascript'],['/studio.css','shared/web/studio.css','text/css']])await context.route(base+url,async r=>r.fulfill({body:await readFile(resolve(root,file)),contentType:type}));
 const stateOf=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('plotline-pdf-workspace')));
 const active=async()=>{const s=await stateOf();return s.documents.find(d=>d.key===s.active);};
 const save=async(name)=>{await page.locator('#save').click();if(name){await page.locator('#save-name').fill(name);await page.locator('#save-form button[type=submit]').click();}await page.waitForFunction(()=>document.getElementById('saved-state').textContent==='Saved');};
 await page.goto(base+'/pdf?document='+documents[0].id);await page.locator('#save').waitFor({state:'visible'});await page.waitForFunction(()=>!document.getElementById('save').disabled);
 assert.equal(await page.getByRole('tab').count(),1);assert.equal(await page.locator('#revisions').count(),0);
 assert.equal(await page.locator('#saved-state').textContent(),'Not saved');
 await page.locator('#rotation').selectOption('90');assert(await page.getByRole('tab',{name:'Boundary.pdf *',exact:true}).isVisible());
 await page.locator('#undo').click();assert.equal(await page.locator('#rotation').inputValue(),'0');assert.equal(await page.locator('#saved-state').textContent(),'Not saved');
 await page.locator('#redo').click();assert.equal(await page.locator('#rotation').inputValue(),'90');
 await save('Boundary drawing');const saved=(await active()).saved;assert.equal(saved.revision,1);
 assert.equal((await api(`/api/v1/documents/${documents[0].id}`)).conversions.length,0,'Save never converts');
 await page.locator('#rotation').selectOption('180');await page.locator('#page').selectOption('1');await page.locator('#scale').fill('0.7');await page.locator('#page').selectOption('0');assert.equal(await page.locator('#rotation').inputValue(),'180','Page placements survive switching');
 await page.locator('#open-library').click();await page.locator('#documents img').first().waitFor();
 assert.equal(await page.locator('.controls #documents').count(),0);
 await page.locator('#documents .document-card').filter({hasText:'Other.pdf'}).getByRole('button',{name:'Open',exact:true}).click();
 await page.getByRole('tab',{name:'Other.pdf',exact:true}).waitFor();assert.equal(await page.getByRole('tab').count(),2);await page.locator('#rotation').selectOption('270');
 await page.getByRole('tab',{name:'Boundary drawing *',exact:true}).click();assert.equal(await page.locator('#rotation').inputValue(),'180');
 await page.reload();await page.waitForFunction(()=>document.getElementById('document-tabs').children.length===2);assert.equal(await page.locator('#rotation').inputValue(),'180','Unsaved work survives reload');
 await page.locator('#revert').click();assert.equal(await page.locator('#rotation').inputValue(),'90');assert.equal(await page.locator('#saved-state').textContent(),'Saved');
 await page.locator('#undo').click();assert.equal(await page.locator('#rotation').inputValue(),'180','Undo recovers a reverted recipe');
 await save();assert.equal((await active()).saved.revision,2);
 await page.locator('#save-copy').click();await page.locator('#save-name').fill('Boundary alternative');await page.locator('#save-form button[type=submit]').click();await page.waitForFunction(()=>document.title.includes('Boundary alternative'));
 assert.notEqual((await active()).saved.id,saved.id);assert.equal((await api('/api/v1/documents?summary=true')).documents.length,2,'Save As does not duplicate the PDF');
 const fresh=await api(`/api/v1/documents/${documents[0].id}/recipes/${saved.id}`);assert.equal(fresh.revision,2);
 const conflict=await fetch(base+`/api/v1/documents/${documents[0].id}/recipes`,{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({name:saved.name,id:saved.id,expected_revision:1,intent:saved.intent})});assert.equal(conflict.status,409);
 assert.deepEqual(Buffer.from(await(await fetch(base+`/api/v1/documents/${documents[0].id}/source.pdf`)).arrayBuffer()),pdf);
 // Hold a conversion response while switching tabs: its result belongs to the submitting document.
 const conversionRoute=base+`/api/v1/documents/${documents[0].id}/draft`;
 let releaseConversion,conversionStarted;const started=new Promise(r=>conversionStarted=r),held=new Promise(r=>releaseConversion=r);
 await page.route(conversionRoute,async route=>{const response=await route.fetch();conversionStarted();await held;await route.fulfill({response});});
 await page.locator('#spacing').fill('3');await page.locator('#convert-button').click();await started;
 await page.getByRole('tab',{name:'Other.pdf *',exact:true}).click();await page.locator('#rotation').selectOption('90');releaseConversion();
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('plotline-pdf-workspace')).documents.some(d=>d.currentId));
 assert.equal(await page.locator('#rotation').inputValue(),'90');assert.equal((await active()).documentId,documents[1].id);
 await page.getByRole('tab',{name:'Boundary alternative *',exact:true}).click();await page.waitForFunction(()=>document.getElementById('work-status').dataset.phase==='ready',null,{timeout:120000});
 assert.equal(await page.locator('#saved-state').textContent(),'Unsaved changes','Generating a preview never saves the recipe');await page.unroute(conversionRoute);
 // A save acknowledges its submitted snapshot, without overwriting edits made in flight.
 const saveRoute=base+`/api/v1/documents/${documents[0].id}/recipes`;
 let releaseSave,saveStarted;const saving=new Promise(r=>saveStarted=r),heldSave=new Promise(r=>releaseSave=r);
 await page.route(saveRoute,async route=>{const response=await route.fetch();saveStarted();await heldSave;await route.fulfill({response});});
 await page.locator('#save').click();await saving;await page.locator('#rotation').selectOption('270');releaseSave();
 await page.waitForFunction(()=>!document.getElementById('save').disabled);assert.equal(await page.locator('#rotation').inputValue(),'270');assert.equal(await page.locator('#saved-state').textContent(),'Unsaved changes');
 await page.unroute(saveRoute);await page.locator('#revert').click();assert.equal(await page.locator('#rotation').inputValue(),'180');
 await page.getByRole('button',{name:'Close Other.pdf',exact:true}).click();await page.locator('#close-document-dialog button[value=cancel]').click();assert.equal(await page.getByRole('tab').count(),2);
 await page.getByRole('button',{name:'Close Other.pdf',exact:true}).click();await page.locator('#close-document-dialog button[value=discard]').click();await page.waitForFunction(()=>document.querySelectorAll('[role=tab]').length===1);assert.equal(await page.getByRole('tab').count(),1);
 // Preview already registered the HPGL draft without navigating or saving.
 assert.equal(context.pages().length,1,'Preview completion never opens HPGL');
 const prepared=(await api(`/api/v1/documents/${documents[0].id}`)).draft;
 assert.equal(prepared.current_conversion_id,(await active()).currentId);
 assert(prepared.jobs.some(j=>j.job_id));assert(await page.locator('#update-plot').isEnabled());
 assert.equal(await page.locator('#update-plot').textContent(),'Open plot ↗');
 assert.equal(await page.locator('#work-detail').textContent(),'Plot ready');
 const popupPromise=context.waitForEvent('page');await page.locator('#update-plot').click();const plot=await popupPromise;await plot.waitForURL(/#[0-9a-f-]+$/);await plot.close();
 assert.equal((await active()).saved.name,'Boundary alternative');assert.equal(await page.locator('#saved-state').textContent(),'Saved');
 const before=(await active()).recipe,preview=await page.locator('.preview-area').boundingBox(),progress=await page.locator('#work-status').boundingBox();
 await page.locator('#composition-tools > summary').click();assert.deepEqual(await page.locator('.preview-area').boundingBox(),preview);assert.deepEqual(await page.locator('#work-status').boundingBox(),progress);await page.keyboard.press('Escape');
 await page.locator('.canvas').dispatchEvent('wheel',{deltaY:-180,clientX:800,clientY:350});assert.deepEqual((await active()).recipe,before,'Preview navigation is not recipe editing');
 await page.screenshot({path:state+'/desktop.png'});
 await page.locator('#edit-inks').click();assert.equal((await page.locator('#ink-drawer').boundingBox()).width,1440);await page.locator('#close-inks').click();
 await page.setViewportSize({width:420,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:state+'/mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);console.log('PASS PDF tabs, durable recipes, Save As, conflicts, undo/redo/revert, unsaved reload, per-page settings, modal thumbnails, background conversion, edits during save, shared layout, mobile; artifacts',state);
}catch(error){console.error(logs);throw error;}finally{if(browser)await browser.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));}
