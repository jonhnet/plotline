// Cross-studio acceptance against isolated stores; no device or map network fetches.
import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {checkPreviewControls} from './preview-controls-checks.mjs';
const root=resolve(import.meta.dirname,'../..'),mapRoot=process.env.PLOTLINE_MAP_PROJECT||root;
await mkdir(root+'/build',{recursive:true});const state=await mkdtemp(root+'/build/studio-navigation-');
const python=process.env.PLOTLINE_MAP_PYTHON||mapRoot+'/.venv/bin/python';
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1','Content-Type':'application/json'};
const map=spawn(python,['-u',mapRoot+'/tools/serve_studio_fixture.py',state+'/map',base],{cwd:mapRoot,stdio:['ignore','pipe','pipe']});let mapLogs='';map.stderr.on('data',d=>mapLogs+=d);
const mapPort=await new Promise((resolve,reject)=>{map.stdout.once('data',d=>resolve(Number(d.toString().trim())));map.once('exit',()=>reject(Error(mapLogs)));});
const server=spawn(process.env.PLOTLINE_TEST_BINARY||root+'/controller/target/release/plotline-studios',['--listen',`127.0.0.1:${port}`,'--state',state+'/driver'],{cwd:root,env:{...process.env,PLOTLINE_PDF_PROJECT:root,PLOTLINE_MAP_UPSTREAM:`http://127.0.0.1:${mapPort}`},stdio:['ignore','ignore','pipe']});let logs='',browser;server.stderr.on('data',d=>logs+=d);const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(path,body,method='POST'){const r=await fetch(base+path,body?{method,headers,body:JSON.stringify(body)}:{});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;}
async function ready(path){for(let n=0;n<400;n++){const v=await api(path);if(!['received','inspecting','queued','running'].includes(v.status)){assert.equal(v.status,'ready',v.error);return v;}await sleep(100);}throw Error('Timed out '+path);}
try{
 for(let n=0;;n++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(n>100)throw Error(logs);await sleep(100);}
 const recipe=(await api('/map/api/catalog')).recipe;recipe.title='Navigation map';
 const saved=await api('/map/api/recipes',{recipe,name:'Navigation map'});await writeFile(state+'/map/'+saved.recipe_hash+'.json',JSON.stringify(recipe));
 const foreign=await fetch(base+'/map/api/validate',{method:'POST',headers:{'Content-Type':'application/json',Origin:'https://foreign.test'},body:JSON.stringify({recipe})});assert.equal(foreign.status,403,'Proxy preserves Map write-origin checks');
 const intent=JSON.parse(await readFile(root+'/build/pdf-boundary-v1/boundary.print-intent.json'));intent.pages=intent.pages.slice(0,1);intent.authoring={application:'map-studio',url:'http://dev.iac.jonh.net:9193/',recipe_hash:saved.recipe_hash,document_id:saved.id,document_revision:1};
 const body=new FormData();body.append('document',new Blob([await readFile(root+'/build/pdf-boundary-v1/boundary.pdf')]),'Navigation.pdf');body.append('intent',new Blob([JSON.stringify(intent)]),'intent.json');
 const upload=await fetch(base+'/api/v1/documents',{method:'POST',headers:{'X-Plotline-Request':'1'},body});assert(upload.ok);const document=await ready((await upload.json()).status_url);
 const conversion=await ready((await api(`/api/v1/documents/${document.id}/draft`,intent)).status_url);
 let doc;for(let n=0;n<200;n++){doc=await api('/api/v1/documents/'+document.id);if(doc.draft.status==='ready')break;await sleep(100);}assert.equal(doc.draft.status,'ready');const job=doc.draft.jobs[0];assert(job.url.startsWith('/hpgl#'));
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1500,height:1000}}),errors=[];
 context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
 await context.route(/\/(?:map\/)?api\/capabilities$/,r=>r.fulfill({json:{offline:false,capabilities:{implemented:{pdf_upload:true,pdf_handoff:true},available_inks:[],inventory_revision:1,media_profiles:[]}}}));
 await context.route(/\/(?:map\/)?api\/cancel$/,r=>r.fulfill({json:{status:'cancelled'}}));
 await context.route(/\/(?:map\/)?api\/preview$/,r=>{const b=r.request().postDataJSON(),key=createHash('sha256').update(JSON.stringify(b.recipe)).digest('hex');return r.fulfill({json:{id:key,cache_key:key,recipe_hash:key,session:b.session,revision:b.revision,quality:b.quality,status:'ready',artifacts:'/artifacts/'+key+'/',progress:{stage:'complete'},result:{seconds:.1,source:{zoom:12,tile_count:1},fetched_bounds:[-123,47,-122,48],feature_counts:{roads:23},label_count:0,omitted_labels:[],warnings:[],intent_available:true}}});});
 await context.route(/\/(?:map\/)?artifacts\/[^/]+\/preview\.png$/,r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"><rect width="900" height="600" fill="white"/><path d="M0 110 Q300 10 900 450" fill="none" stroke="green" stroke-width="2"/></svg>'}));
 const landing=await context.newPage();await landing.goto(base);await landing.screenshot({path:state+'/home.png'});const openedMap=context.waitForEvent('page');await landing.locator('a[data-studio=map]').click();const mapPage=await openedMap;await mapPage.waitForURL('**/map');await landing.close();await mapPage.getByRole('tab').first().waitFor();
 await mapPage.locator('#open-library').click();await mapPage.locator('.document-card[data-document="'+saved.id+'"] button').click();await mapPage.waitForFunction(()=>document.querySelector('#title').value==='Navigation map');await mapPage.locator('#title').fill('Unsaved map changes');await mapPage.locator('#title').press('Tab');
 const stateOfMap=()=>mapPage.evaluate(()=>JSON.parse(sessionStorage.getItem('plotline-map-workspace')));const before=await stateOfMap();assert.equal(before.documents.find(d=>d.key===before.active).recipe.title,'Unsaved map changes');
 const switchTo=async(page,studio)=>{await page.locator('.studio-switcher:not(.studio-sources)>summary').click();await page.locator('.studio-switcher:not(.studio-sources) a[data-studio='+studio+']').click();};
 const newPdf=context.waitForEvent('page');await switchTo(mapPage,'pdf');const pdf=await newPdf;await pdf.waitForURL('**/pdf');await pdf.locator('#open-library').click();await pdf.locator('#documents .document-card').first().getByRole('button',{name:'Open',exact:true}).click();await pdf.waitForFunction(()=>!document.querySelector('#save').disabled);
 await pdf.locator('#scale').fill('0.8');await pdf.locator('#scale').dispatchEvent('change');
 const pdfState=()=>pdf.evaluate(()=>JSON.parse(sessionStorage.getItem('plotline-pdf-workspace')));const pdfBefore=await pdfState();
 const hpgl=await context.newPage();await hpgl.goto(base+job.url);assert.equal(await hpgl.evaluate(()=>window.opener),null,'HPGL starts as an independent browser tab');await hpgl.locator('#preview-canvas[data-ready=true]').waitFor();assert.equal(await hpgl.getByRole('tab').count(),1,'Initial hash handoff does not duplicate the document');
 // One paper-unit preference spans three independent Studio tabs. It must
 // repaint only: never change recipes, register a plot or render a map.
 await mapPage.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
 const mapUnitsBefore=await stateOfMap(),pdfUnitsBefore=await pdfState(),hpglUnitsBefore=await hpgl.evaluate(()=>sessionStorage.getItem('plotline-hpgl-workspace'));
 let unitsWrites=0;const countUnits=r=>{if(r.method()==='POST'&&/\/(preview|draft|conversions|setup|recipes)$/.test(new URL(r.url()).pathname))unitsWrites++;};context.on('request',countUnits);
 assert.equal(await pdf.locator('#paper-unit').inputValue(),'in');
 await pdf.locator('.studio-preferences').evaluate(e=>e.open=true);await pdf.locator('#paper-unit').selectOption('mm');
 for(const p of [hpgl,mapPage])await p.waitForFunction(()=>document.getElementById('paper-unit').value==='mm');
 assert.match(await hpgl.locator('#paper-requirement').textContent(),/mm sheet/);assert.doesNotMatch(await hpgl.locator('#paper-requirement').textContent(),/\bin\b/);
 await mapPage.locator('.studio-preferences').evaluate(e=>e.open=true);await mapPage.locator('#paper-unit').selectOption('in');
 for(const p of [hpgl,pdf])await p.waitForFunction(()=>document.getElementById('paper-unit').value==='in');
 assert.match(await hpgl.locator('#paper-requirement').textContent(),/in sheet/);assert.doesNotMatch(await hpgl.locator('#paper-requirement').textContent(),/\bmm\b/);
 assert.deepEqual(await stateOfMap(),mapUnitsBefore);assert.deepEqual(await pdfState(),pdfUnitsBefore);
 assert.equal(await hpgl.evaluate(()=>sessionStorage.getItem('plotline-hpgl-workspace')),hpglUnitsBefore);
 assert.equal(unitsWrites,0,'Unit changes do not generate previews or save geometry');context.off('request',countUnits);
 await hpgl.locator('#studio-sources>summary').click();await hpgl.getByRole('link',{name:'Edit map',exact:true}).click();await mapPage.waitForFunction(hash=>{const s=JSON.parse(sessionStorage.getItem('plotline-map-workspace'));return s.documents.find(d=>d.key===s.active).sourceRecipe===hash;},saved.recipe_hash);
 assert.equal(context.pages().length,3,'Reuse Map instead of creating another browser tab');assert.equal(await mapPage.locator('#title').inputValue(),'Unsaved map changes');assert.equal((await stateOfMap()).active,before.active);
 await hpgl.locator('#studio-sources>summary').click();await hpgl.getByRole('link',{name:'Edit PDF recipe',exact:true}).click();await pdf.waitForFunction(()=>document.querySelector('#notice').textContent.includes('editable PDF recipe'));
 await pdf.getByRole('button',{name:'Dismiss message',exact:true}).click();assert(await pdf.locator('#notice').isHidden());
 assert.equal(context.pages().length,3);assert.equal(await pdf.locator('#scale').inputValue(),'0.8');assert.equal((await pdfState()).active,pdfBefore.active);
 await hpgl.locator('#studio-sources>summary').click();await hpgl.getByRole('link',{name:'View source map version',exact:true}).click();await mapPage.waitForFunction(()=>document.querySelectorAll('#document-tabs [role=tab]').length===3);
 await mapPage.getByRole('button',{name:'Dismiss message',exact:true}).click();assert(await mapPage.locator('#notice').isHidden());
 assert.equal(await mapPage.locator('#title').inputValue(),'Navigation map');assert((await stateOfMap()).documents.some(d=>d.recipe.title==='Unsaved map changes'));
 await hpgl.locator('#studio-sources>summary').click();await hpgl.getByRole('link',{name:'View source PDF version',exact:true}).click();await pdf.waitForFunction(()=>document.querySelectorAll('#document-tabs [role=tab]').length===2);
 assert.equal(await pdf.locator('#scale').inputValue(),'1');assert((await pdfState()).documents.some(d=>d.recipe.pages[0].placement.scale===.8));
 await pdf.getByRole('button',{name:'Open plot ↗',exact:true}).click();
 const home=await context.newPage();await home.goto(base);await home.evaluate(()=>{const c=new BroadcastChannel('plotline-studios-v1');c.onmessage=({data})=>{if(data.type==='plotline-ack'){window.handoffAcknowledged=true;c.close();}};});await home.locator('a[data-studio=hpgl]').click();await home.waitForFunction(()=>window.handoffAcknowledged);
 assert.equal(context.pages().length,4,'Open plot and landing reuse the independent HPGL tab');assert.equal(new URL(home.url()).pathname,'/');await home.close();

 await switchTo(mapPage,'hpgl');assert.equal(context.pages().length,3);await hpgl.reload();await hpgl.locator('#preview-canvas[data-ready=true]').waitFor();await switchTo(pdf,'hpgl');assert.equal(context.pages().length,3,'Reloaded tab is rediscovered');
 for(const [name,page] of [['map',mapPage],['pdf',pdf],['hpgl',hpgl]]){
   assert((await page.title()).startsWith(name==='map'?'Map Studio':name==='pdf'?'PDF Studio':'HPGL Studio'));
   await page.locator('details[open]').evaluateAll(nodes=>nodes.forEach(n=>n.open=false));
   if(await page.getByRole('button',{name:'Dismiss message',exact:true}).isVisible())await page.getByRole('button',{name:'Dismiss message',exact:true}).click();
   let writes=0;const count=r=>{if(!['GET','HEAD'].includes(r.method()))writes++;};page.on('request',count);
   const readView=()=>page.evaluate(name=>name==='hpgl'?document.querySelector('#paper-scene').getAttribute('viewBox'):name==='map'?document.querySelector('#paper').style.transform:document.querySelector('#preview-link').style.transform,name);
   await checkPreviewControls(page,readView);assert.equal(writes,0,name+' preview controls do not submit edits');page.off('request',count);
   await page.screenshot({path:state+'/'+name+'.png'});await page.setViewportSize({width:420,height:900});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),name+' mobile fits');await checkPreviewControls(page);await page.screenshot({path:state+'/'+name+'-mobile.png'});
 }
 const legacy=await context.newPage();await legacy.goto(base+'/#'+job.job_id);await legacy.waitForURL('**/hpgl#'+job.job_id);await legacy.close();
 // The old Map origin has separate browser storage. Transfer its drafts while
 // retaining both the shared-origin edits and the unchanged original workspace.
 const oldMap=await context.newPage();await oldMap.goto(`http://127.0.0.1:${mapPort}/`);await oldMap.getByRole('tab').first().waitFor();
 await oldMap.locator('#title').fill('Legacy unsaved map');await oldMap.locator('#title').press('Tab');
 const transferred=context.waitForEvent('page');await oldMap.getByRole('button',{name:'Move open documents'}).click();const moved=await transferred;
 await moved.waitForFunction(()=>document.querySelector('#title')?.value==='Legacy unsaved map');
 const movedState=await moved.evaluate(()=>JSON.parse(sessionStorage.getItem('plotline-map-workspace')));
 assert(movedState.documents.some(d=>d.recipe.title==='Unsaved map changes'),'Migration retains existing shared-origin drafts');
 assert.equal(await oldMap.locator('#title').inputValue(),'Legacy unsaved map','Original workspace remains intact');
 await oldMap.locator('#notice').waitFor({state:'hidden'});await oldMap.close();await moved.close();
 // Popup blocking gives an ordinary same-tab link instead of silently failing.
 const blocked=await browser.newContext(),blockedPage=await blocked.newPage();await blockedPage.goto(base+'/pdf');
 await blockedPage.evaluate(()=>window.open=()=>null);await switchTo(blockedPage,'hpgl');await blockedPage.getByRole('link',{name:'Open here'}).waitFor();await blocked.close();
 assert.deepEqual(errors,[]);console.log('PASS shared routes, proxy origin checks, independent tab reuse/reload, source ancestry and snapshots, unsaved map/PDF preservation, studio titles/themes, mobile, legacy bookmarks/workspace transfer, and popup fallback. Artifacts',state);
}catch(e){console.error(logs,mapLogs);if(browser)for(const page of (browser.contexts()[0]?.pages()||[]))console.error(await page.evaluate(()=>({url:location.href,name:window.name,title:document.title,notice:document.querySelector('#notice')?.textContent,navigation:document.querySelector('#studio-navigation-notice')?.textContent})).catch(()=>({closed:true})));throw e;}finally{await browser?.close();server.kill('SIGTERM');map.kill('SIGTERM');}
