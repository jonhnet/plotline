// Real Maze -> PDF -> HPGL round trip in an isolated store; no device configured.
import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
const root=resolve(import.meta.dirname,'../..');await mkdir(root+'/build',{recursive:true});
const state=await mkdtemp(root+'/build/maze-browser-'),socket=createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1','Content-Type':'application/json'};
const server=spawn(process.env.PLOTLINE_TEST_BINARY||root+'/controller/target/debug/plotline-studios',['--listen',`127.0.0.1:${port}`,'--state',state+'/store'],{cwd:root,env:{...process.env,PLOTLINE_PDF_PROJECT:root},stdio:['ignore','ignore','pipe']});let logs='',browser;server.stderr.on('data',d=>logs+=d);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(path,body,method='POST'){const r=await fetch(base+path,body?{method,headers,body:JSON.stringify(body)}:{});const value=await r.json();assert(r.ok,JSON.stringify(value));return value;}
try{
 for(let i=0;;i++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(i>100)throw Error(logs);await sleep(100);}
 assert.equal((await fetch(base+'/api/mazes/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).ok,false);
 const inventory=await api('/api/carousel');
 for(const [i,color] of ['#000000','#c84253'].entries())inventory.slots[i+1]={state:'loaded',color,width_mm:i===0?.3:1.2,label:['Black','Red'][i],speed_cm_s:5,force:4};
 await api('/api/carousel',inventory,'PUT');
 const installedInventory=await api('/api/carousel');
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}}),errors=[];
 context.on('page',p=>{p.on('pageerror',e=>errors.push(e.message));p.on('dialog',d=>d.accept());});
 const home=await context.newPage();await home.goto(base);assert.equal(await home.locator('.studio-card').count(),5);await home.screenshot({path:state+'/home.png',fullPage:true});
 const created=context.waitForEvent('page');await home.locator('a[data-studio=mazes]').click();const page=await created;
 await page.locator('body[data-ready=true]').waitFor();await page.locator('#preview[data-ready=true]').waitFor();
 const workspace=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('plotline-maze-workspace'))),recipe=async()=>{const s=await workspace();return s.documents.find(d=>d.key===s.active).recipe;};
 const ready=()=>page.locator('#preview[data-ready=true]').waitFor(),image=()=>page.locator('#preview').evaluate(c=>c.toDataURL());
 const change=async(id,value)=>{await page.locator('#'+id).fill(String(value));await page.locator('#'+id).press('Tab');await ready();};
 const detail=async value=>{await page.locator('#detail').evaluate((e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));},value);await ready();};
 assert.equal(await page.locator('#columns,#rows').count(),0,'One detail knob replaces both grid dimensions');
 for(const family of ['square','hexagon','triangle']){
   await page.locator('#family').selectOption(family);await ready();assert(await page.locator('#prepare').isEnabled());
   await page.screenshot({path:state+'/'+family+'.png'});
   const landscape=await recipe();await page.locator('#orientation').selectOption('90');await ready();const portrait=await recipe();
   assert(portrait.params.columns<landscape.params.columns&&portrait.params.rows>landscape.params.rows,'Paper orientation changes the grid aspect');
   await page.locator('#orientation').selectOption('0');await ready();assert.deepEqual((await recipe()).params,landscape.params);
 }
 assert.equal(await page.locator('#show-solution').count(),0,'The artwork checkbox is the only solution control');
 const source=await recipe(),plain=await image();
 await page.locator('#plot-solution').check();await ready();assert.notEqual(await image(),plain);assert.equal((await recipe()).plot_solution,true);
 await page.locator('#plot-solution').uncheck();await ready();assert.equal(await image(),plain);assert.deepEqual(await recipe(),source);
 await page.locator('#new-seed').click();await ready();assert.notEqual((await recipe()).params.seed,source.params.seed);await page.locator('#undo').click();await ready();assert.deepEqual(await recipe(),source);
 // Upload a ring mask with a transparent hole, preserving the exact input bytes.
 const ring=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=180;c.height=180;const ctx=c.getContext('2d');ctx.fillStyle='black';ctx.fillRect(0,0,180,180);ctx.clearRect(60,60,60,60);return c.toDataURL().split(',')[1];});
 await page.locator('#family').selectOption('square');await ready();
 await page.locator('#mask-upload').setInputFiles({name:'ring.png',mimeType:'image/png',buffer:Buffer.from(ring,'base64')});
 await page.waitForFunction(()=>document.querySelector('#mask-note').textContent.includes('ring.png'));await ready();
 const masked=await recipe();assert(masked.params.mask.pixels.includes(0));assert.equal(masked.params.rows,masked.params.columns);assert.deepEqual(await readFile(state+'/store/mazes/masks/'+masked.mask_source),Buffer.from(ring,'base64'));
 await page.screenshot({path:state+'/mask.png'});
 const islands=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=100;c.height=100;const ctx=c.getContext('2d');ctx.fillRect(0,0,40,100);ctx.fillRect(60,0,40,100);return c.toDataURL().split(',')[1];});
 await page.locator('#mask-upload').setInputFiles({name:'islands.png',mimeType:'image/png',buffer:Buffer.from(islands,'base64')});
 await page.waitForFunction(()=>document.querySelector('#work-detail').textContent.includes('disconnected'));
 assert(await page.locator('#prepare').isDisabled());await page.locator('#undo').click();await ready();assert.deepEqual(await recipe(),masked);
 // Freeze one masked maze, check save revisions and source snapshots.
 await detail(25);
 await page.locator('#save').click();await page.locator('#save-name').fill('Maze acceptance');await page.locator('#save-form button[type=submit]').click();
 await page.waitForFunction(()=>!document.querySelector('#document-tabs [role=tab][aria-selected=true]').textContent.includes('*'));
 const saved=(await workspace()).documents.find(d=>d.saved);assert(saved.saved.id);
 assert.equal((await api('/api/mazes/documents')).documents.length,1);assert.equal((await api('/api/patterns/documents')).documents.length,0);
 const stale=await fetch(base+'/api/mazes/documents',{method:'POST',headers,body:JSON.stringify({id:saved.saved.id,revision:0,recipe:await recipe()})});assert.equal(stale.status,409);
 await page.locator('#plot-solution').check();await ready();await page.locator('#save').click();await page.waitForFunction(()=>!document.querySelector('#document-tabs [role=tab][aria-selected=true]').textContent.includes('*'));
 const design=await recipe();await page.locator('#prepare').click();
 await page.waitForFunction(()=>!document.querySelector('#open-plot').disabled||document.querySelector('#prepare').textContent==='Retry preparation',null,{timeout:180000});
 assert(await page.locator('#open-plot').isEnabled(),await page.locator('#work-detail').textContent());
 const after=await workspace(),receipt=after.documents.find(d=>d.key===after.active).receipt;assert(receipt.ready&&receipt.jobs.length===1);
 const conversion=await api('/api/v1/conversions/'+receipt.conversion);assert.equal(conversion.intent.authoring.application,'maze-studio');assert.equal(conversion.intent.authoring.document_id,saved.saved.id);assert.equal(conversion.intent.bindings.length,2);
 assert.deepEqual(conversion.intent.pages[0].placement,{scale:1,rotation_deg:0,offset_mm:[0,0]});
 await page.reload();await ready();assert(await page.locator('#open-plot').isEnabled());
 const opened=context.waitForEvent('page');await page.locator('#open-plot').click();const hpgl=await opened;await hpgl.locator('#preview-canvas[data-ready=true]').waitFor();await hpgl.screenshot({path:state+'/hpgl.png'});
 await hpgl.locator('#studio-sources>summary').click();await hpgl.getByRole('link',{name:'Edit maze',exact:true}).click();await sleep(400);assert.equal(context.pages().length,3,'Edit maze reuses its existing tab');assert.deepEqual(await recipe(),design);
 await page.locator('#new-seed').click();await ready();assert(await page.locator('#open-plot').isDisabled());
 await hpgl.locator('#studio-sources>summary').click();await hpgl.getByRole('link',{name:'View source maze version',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('#document-tabs [role=tab]').length===2);await ready();assert.deepEqual(await recipe(),design);
 // Prepare the same maze without the solution; both jobs and exact walls survive.
 await page.locator('#plot-solution').uncheck();await ready();await page.locator('#prepare').click();
 await page.waitForFunction(()=>!document.querySelector('#open-plot').disabled||document.querySelector('#prepare').textContent==='Retry preparation',null,{timeout:180000});
 assert(await page.locator('#open-plot').isEnabled(),await page.locator('#work-detail').textContent());
 const without=(await workspace()).documents.find(d=>d.key!==saved.key&&d.receipt?.ready).receipt;
 assert.notEqual(without.jobs[0].job_id,receipt.jobs[0].job_id);
 const withScene=JSON.parse(await readFile(state+'/store/mazes/exports/'+receipt.exportHash+'/scene.json'));
 const withoutScene=JSON.parse(await readFile(state+'/store/mazes/exports/'+without.exportHash+'/scene.json'));
 assert.deepEqual(withoutScene.paths,withScene.paths.filter(p=>p.role===0),'Toggling a fatter solution pen preserves every wall point');
 assert.equal((await api('/api/v1/conversions/'+receipt.conversion)).status,'ready','Earlier solution draft remains available');
 assert.equal((await api('/api/v1/conversions/'+without.conversion)).intent.bindings.length,1);
 await page.locator('#new-document').click();await ready();await detail(100);await page.locator('#family').selectOption('triangle');await ready();await page.locator('#margins').selectOption('custom');await ready();await change('margin',5.42);assert(await page.locator('#prepare').isDisabled(),'Physical pen clearance blocks an over-dense maze');
 // Older source snapshots retain their grid until an explicit layout edit.
 const legacy=structuredClone(design);delete legacy.detail;legacy.params.columns=12;legacy.params.rows=50;legacy.params.mask=null;delete legacy.mask_source;legacy.plot_solution=false;legacy.name='Legacy grid';
 const old=await api('/api/mazes/documents',{recipe:legacy});await page.goto(base+'/mazes?document='+old.id);await ready();
 assert.deepEqual(await recipe(),legacy);assert((await page.locator('#detail-note').textContent()).includes('Saved grid'));
 await detail(50);assert.equal((await recipe()).detail,50);assert((await recipe()).params.columns>(await recipe()).params.rows);
 assert.deepEqual(await api('/api/carousel'),installedInventory,'Authoring preserves carousel');assert.deepEqual(errors,[]);
 console.log(JSON.stringify({result:'PASS',artifacts:state,job:receipt.jobs[0].url}));
} catch(error){console.error(logs);throw error;} finally{if(browser)await browser.close();server.kill();}
