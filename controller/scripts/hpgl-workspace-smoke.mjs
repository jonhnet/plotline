// Isolated spool: studio lifecycle and operator preview only; no device configured.
import {chromium,firefox} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
const root=resolve(import.meta.dirname,'../..');await mkdir(root+'/build',{recursive:true});const state=await mkdtemp(root+'/build/hpgl-workspace-');
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1','Content-Type':'application/json'},sleep=ms=>new Promise(r=>setTimeout(r,ms));
const server=spawn(process.env.PLOTLINE_BINARY||root+'/controller/target/release/plotline-studios',['--listen',`127.0.0.1:${port}`,'--state',state],{cwd:root,stdio:['ignore','ignore','pipe']});let logs='',browser;server.stderr.on('data',d=>logs+=d);
async function api(path,body,method='POST'){const r=await fetch(base+path,body?{method,headers,body:JSON.stringify(body)}:{});const value=await r.json();assert(r.ok,JSON.stringify(value));return value;}
try{
 for(let n=0;;n++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(n>100)throw Error(logs);await sleep(50);}
 const jobs=[];for(const name of ['First.hpgl','Second.hpgl']){const r=await fetch(base+'/api/jobs?units_per_mm=40&name='+name,{method:'POST',headers:{...headers,'Content-Type':'application/octet-stream'},body:'IN;SP1;PU0,0;PD4000,0,4000,2000,0,2000,0,0;PU;SP2;PU0,0;PD4000,2000;PU;SP0;'});assert(r.ok);jobs.push(await r.json());}
 let inventory=await api('/api/carousel');for(const [slot,color] of [[1,'#000000'],[2,'#ff0000'],[4,'#008000']])inventory.slots[slot]={state:'loaded',label:color,color,width_mm:.3,speed_cm_s:5,force:4};await api('/api/carousel',inventory,'PUT');
 const baseline=await api(`/api/jobs/${jobs[0].id}/setup`);
 browser=await (process.env.PLOTLINE_BROWSER==='firefox'?firefox:chromium).launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[],writes=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(['PUT','POST'].includes(r.method()))writes.push(new URL(r.url()).pathname);});
 const active=()=>page.evaluate(()=>{const s=JSON.parse(sessionStorage.getItem('plotline-hpgl-workspace'));return s.documents.find(d=>d.key===s.active);});
 const rendered=()=>page.waitForFunction(()=>document.getElementById('preview-canvas').dataset.ready==='true');
 const save=async name=>{await page.locator('#save').click();if(name){await page.locator('#save-name').fill(name);await page.locator('#save-form button[type=submit]').click();}await page.waitForFunction(()=>document.getElementById('save-state').textContent==='Saved');};
 await page.goto(base+'/#'+jobs[0].id);await rendered();assert.deepEqual(errors,[]);
 assert.equal(await page.getByRole('tab').count(),1);assert(await page.locator('#pens-pane').isVisible());assert.equal(await page.locator('aside').count(),0);
 assert.equal(await page.locator('#export-plan').isVisible(),false);assert.equal(await page.locator('#placement-status').isVisible(),false);
 const preview=await page.locator('.preview-area').boundingBox();assert(preview.height>550,'Compact drawer leaves most of the desktop height for the preview');
 assert.equal(await page.locator('#work-status').isVisible(),false,'Progress lives only in Plotter');assert.equal(await page.locator('#ink-drawer .ink-drawer-heading').count(),0);
 const tray=await page.locator('#pens').boundingBox(),tools=await page.locator('.pen-tools').boundingBox();assert(tools.x>=tray.x+tray.width,'Pen tools sit beside the tray');
 const penPositions=await page.locator('#pens .ink-loaded .pen-cap').evaluateAll(caps=>caps.map(c=>c.getBoundingClientRect().y));assert(Math.max(...penPositions)-Math.min(...penPositions)<1,'Loaded pens align with or without a document source');
 await page.locator('#choose-inks').click();await page.getByLabel('Include ink 2',{exact:true}).uncheck();await page.locator('#ink-inclusion-dialog').getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await page.locator('#save-state').textContent(),'Not saved');
 await page.locator('#choose-inks').click();await page.getByLabel('Include ink 2',{exact:true}).uncheck();await page.locator('#ink-inclusion-dialog').getByRole('button',{name:'Apply',exact:true}).click();await page.waitForFunction(()=>document.getElementById('preview-canvas').dataset.pens==='1');assert.match(await page.locator('#choose-inks').textContent(),/1 omitted/);
 assert.deepEqual(await page.locator('.preview-area').boundingBox(),preview,'Omitting an ink does not shift the tray or preview');await page.locator('#undo').click();await page.waitForFunction(()=>document.getElementById('preview-canvas').dataset.pens==='1,2');
 await page.locator('#show-plotter').click();assert(await page.locator('#work-status').isVisible());assert.deepEqual(await page.locator('.preview-area').boundingBox(),preview);
 await page.locator('#turn-sheet').click();assert(await page.getByRole('tab',{name:'First *',exact:true}).isVisible());
 assert.deepEqual(await api(`/api/jobs/${jobs[0].id}/setup`),baseline,'Editing does not overwrite the saved source setup');
 await page.locator('#undo').click();assert.equal(await page.locator('#angle').textContent(),'0°');await page.locator('#redo').click();assert.equal(await page.locator('#angle').textContent(),'90°');
 await save('First drawing');const first=(await active()).saved;assert.equal(first.revision,1);
 await page.locator('#view-facing').click();assert.equal(await page.locator('#save-state').textContent(),'Saved','Inspection does not edit the recipe');
 await page.locator('#viewport').dispatchEvent('wheel',{deltaY:-180,clientX:800,clientY:250});assert.equal(await page.locator('#save-state').textContent(),'Saved');
 await page.locator('#turn-sheet').click();
 await page.locator('#open-library').click();await page.locator('#jobs .document-card').filter({hasText:'Second'}).getByRole('button',{name:'Open',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('[role=tab]').length===2);await rendered();
 assert.equal(await page.getByRole('tab').count(),2);await page.getByRole('tab',{name:'First drawing *',exact:true}).click();await rendered();assert.equal(await page.locator('#angle').textContent(),'180°');
 await page.reload();await rendered();assert.equal(await page.locator('#angle').textContent(),'180°');assert.equal(await page.getByRole('tab').count(),2);
 await page.locator('#revert').click();await rendered();assert.equal(await page.locator('#angle').textContent(),'90°');assert.equal(await page.locator('#save-state').textContent(),'Saved');
 await page.locator('#undo').click();await rendered();assert.equal(await page.locator('#angle').textContent(),'180°');await save();
 const conflict=await fetch(base+`/api/jobs/${jobs[0].id}/recipes`,{method:'POST',headers,body:JSON.stringify({id:first.id,expected_revision:1,name:first.name,setup:first.setup})});assert.equal(conflict.status,409);
 const denied=await fetch(base+`/api/jobs/${jobs[0].id}/recipes`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'No',setup:first.setup})});assert.equal(denied.status,403);
 await page.locator('#save-copy').click();await page.locator('#save-name').fill('Alternate pens');await page.locator('#save-form button[type=submit]').click();await page.waitForFunction(()=>document.title.includes('Alternate pens'));
 assert.notEqual((await active()).saved.id,first.id);assert.equal((await api('/api/jobs')).length,2,'Save As shares the retained HPGL');
 await page.locator('#open-library').click();await page.locator('#jobs .document-card').filter({hasText:'First drawing'}).getByRole('button',{name:'Open',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('[role=tab]').length===3);await rendered();assert.equal(await page.getByRole('tab').count(),3);
 if(await page.locator('#show-pens').getAttribute('aria-expanded')!=='true')await page.locator('#show-pens').click();await page.getByLabel('Pen arrangement', {exact:true}).selectOption('document');await page.locator('#pens [data-ink-id="1"]').click();await page.locator('#pens .pen-cap[data-slot="4"]').click();
 await page.waitForFunction(()=>JSON.parse(document.getElementById('preview-canvas').dataset.colors||'{}')['1']==='#008000');assert.equal(await page.locator('#save-state').textContent(),'Unsaved changes');
 assert.deepEqual(await api(`/api/jobs/${jobs[0].id}/setup`),baseline,'Pen assignment is not autosaved');
 await page.getByRole('button',{name:'Close First drawing',exact:true}).click();await page.locator('#close-document-dialog button[value=cancel]').click();assert.equal(await page.getByRole('tab').count(),3);
 await page.getByRole('button',{name:'Close First drawing',exact:true}).click();await page.locator('#close-document-dialog button[value=discard]').click();await page.waitForFunction(()=>document.querySelectorAll('[role=tab]').length===2);await rendered();assert.equal(await page.getByRole('tab').count(),2);
 await page.locator('#export-tools > summary').click();assert(await page.locator('#export-plan').isVisible());assert.deepEqual(await page.locator('.preview-area').boundingBox(),preview);await page.keyboard.press('Escape');
 if(await page.locator('#show-pens').getAttribute('aria-expanded')!=='true')await page.locator('#show-pens').click();await page.screenshot({path:state+'/desktop.png'});
 // Incoming documents must remain visible when the existing tab strip overflows.
 for(let i=0;i<7;i++){
  const r=await fetch(base+'/api/jobs?units_per_mm=40&name='+encodeURIComponent(`Incoming drawing with a long title ${i}.hpgl`),{method:'POST',headers:{...headers,'Content-Type':'application/octet-stream'},body:`IN;SP1;PU0,0;PD${400+i},400;PU;SP0;`});
  const incoming=await r.json();assert(r.ok);
  await page.evaluate(id=>location.hash=id,incoming.id);
  await page.waitForFunction(id=>document.querySelector('[role=tab][aria-selected=true]')?.textContent.includes(`long title ${id}`),i);
  await rendered();
 }
 await page.waitForFunction(()=>{
  const host=document.getElementById('document-tabs').getBoundingClientRect(),tab=document.querySelector('.document-tab.selected').getBoundingClientRect();
  return tab.width>0&&tab.left>=host.left-1&&tab.right<=host.right+1;
 });
 assert.equal(await page.getByRole('tab').count(),9);
 await page.setViewportSize({width:420,height:900});await rendered();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert(await page.locator('#plot-open').isVisible());await page.screenshot({path:state+'/mobile.png'});
 assert(!writes.some(p=>p.endsWith('/plot')||p.endsWith('/setup')),'The lifecycle check never plots or autosaves layouts');assert.deepEqual(errors,[]);
 console.log('PASS HPGL tabs, saved recipes, Save As, stale-save conflicts, undo/revert, unsaved reload, inspection-only navigation, pen mapping, close prompts, trays and mobile; artifacts',state);
}catch(e){console.error(logs);throw e;}finally{await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));}
