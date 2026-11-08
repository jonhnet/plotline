import {chooseGalleryFamily,checkPatternGallery} from './pattern-gallery-checks.mjs';
// Real Pattern -> PDF -> HPGL acceptance. Isolated store, no device connection.
import {chromium,firefox} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
import {checkPreviewControls} from './preview-controls-checks.mjs';
const root=resolve(import.meta.dirname,'../..');await mkdir(root+'/build',{recursive:true});
const state=await mkdtemp(root+'/build/pattern-browser-'),socket=createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1','Content-Type':'application/json'};
const server=spawn(process.env.PLOTLINE_TEST_BINARY||root+'/controller/target/debug/plotline-studios',['--listen',`127.0.0.1:${port}`,'--state',state+'/store'],{cwd:root,env:{...process.env,PLOTLINE_PDF_PROJECT:root},stdio:['ignore','ignore','pipe']});let logs='',browser;server.stderr.on('data',d=>logs+=d);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(path,body,method='POST'){const r=await fetch(base+path,body?{method,headers,body:JSON.stringify(body)}:{});const value=await r.json();assert(r.ok,JSON.stringify(value));return value;}
try{
 for(let i=0;;i++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(i>100)throw Error(logs);await sleep(100);}
 const inventory=await api('/api/carousel');
 for(const [i,color] of ['#000000','#008080','#c84253','#008000','#0000ff','#ff00ff','#ffff00'].entries())inventory.slots[i+1]={state:'loaded',color,width_mm:.3,label:['Black','Teal','Red','Green','Blue','Magenta','Yellow'][i],speed_cm_s:5,force:4};
 await api('/api/carousel',inventory,'PUT');
 browser=await (process.env.PLOTLINE_BROWSER==='firefox'?firefox:chromium).launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}}),errors=[];
 await context.addInitScript(()=>{const Original=Worker;window.patternWork=[];window.Worker=class extends Original{postMessage(data,...args){const run={family:data.family,params:structuredClone(data.params),started:performance.now(),previews:0};window.patternWork.push(run);this.addEventListener('message',e=>{if(e.data.checkpoint)run.previews++;if(e.data.frame)run.frames=(run.frames||0)+1;if(e.data.progress)run.progress=e.data.progress;if(e.data.result){run.finished=performance.now();run.points=e.data.result.points;run.sampling=e.data.result.sampling;}});return super.postMessage(data,...args);}};});
 context.on('page',p=>{p.on('pageerror',e=>errors.push(e.message));p.on('dialog',d=>d.accept());});
 const home=await context.newPage();await home.goto(base);assert.equal(await home.locator('.studio-card').count(),5);await home.screenshot({path:state+'/home.png',fullPage:true});
 const created=context.waitForEvent('page');await home.locator('a[data-studio=patterns]').click();const page=await created;
 await page.locator('body[data-ready=true]').waitFor();await page.locator('#preview[data-ready=true]').waitFor();
 const workspace=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('plotline-pattern-workspace'))),recipe=async()=>{const s=await workspace();return s.documents.find(d=>d.key===s.active).recipe;};
 assert.equal((await recipe()).inks.length,7);assert.equal((await recipe()).family,'koch');
 const canvasImage=()=>page.locator('#preview').evaluate(c=>c.toDataURL());
 const inspectionRecipe=await recipe(),workersBefore=await page.evaluate(()=>patternWork.length);
 await checkPreviewControls(page,canvasImage);
 const plain=await canvasImage();await page.locator('#show-margins').check();
 assert.notEqual(await canvasImage(),plain,'Margins visibly change the finished canvas');
 await page.screenshot({path:state+'/margins.png'});
 await page.locator('#show-margins').uncheck();assert.equal(await canvasImage(),plain,'Hiding guides restores the artwork exactly');
 assert.deepEqual(await recipe(),inspectionRecipe,'Inspection never changes artwork');assert.equal(await page.evaluate(()=>patternWork.length),workersBefore,'Inspection does not regenerate geometry');
 await page.screenshot({path:state+'/studio.png'});
 // Every family has a finished actual vector preview and useful adjacent values.
 for(const family of ['dragon','hilbert','plant','sierpinski','apollonian','julia','menger','spirograph','lissajous','harmonograph','phyllotaxis','penrose','truchet','mandelbrot','burning_ship','newton','lorenz','rossler','barnsley','crystal','woven','koch']){
   await page.locator('#family').selectOption(family);await page.locator('#preview[data-ready=true]').waitFor();
   assert.equal(await page.locator('#prepare').isEnabled(),true,family+' can prepare');
   await page.screenshot({path:state+'/'+family+'.png'});
 }
 const before=await recipe();await page.getByRole('button',{name:'Increase Peak angle',exact:true}).click();await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await recipe()).params.angle,65);
 await page.locator('#undo').click();assert.deepEqual(await recipe(),before);
 await page.locator('#save').click();await page.locator('#save-name').fill('Pattern acceptance');await page.locator('#save-form button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#document-tabs [role=tab][aria-selected=true]').textContent.includes('*'));
 const saved=(await workspace()).documents.find(d=>d.saved);assert(saved.saved.id);
 await page.getByRole('button',{name:'Increase Peak angle',exact:true}).click();await page.locator('#preview[data-ready=true]').waitFor();
 await page.locator('.document-tab.selected .close-tab').click();await page.locator('#close-dialog button[value=cancel]').click();assert.equal(await page.getByRole('tab').count(),1);assert((await page.getByRole('tab').textContent()).includes('*'));
 await page.locator('#save').click();await page.waitForFunction(()=>!document.querySelector('#document-tabs [role=tab][aria-selected=true]').textContent.includes('*'));
 assert.equal((await api('/api/patterns/documents/'+saved.saved.id)).revision,2);
 // Shared sequence selects all loaded inks or a subset without changing inventory.
 await page.locator('#edit-inks').click();await page.screenshot({path:state+'/inks.png'});
 const carouselBefore=await api('/api/carousel');
 await page.getByRole('button',{name:'All loaded',exact:true}).click();assert.equal((await recipe()).inks.length,7);
 await page.locator('.ink-sequence-choice .pen-cap').first().click();
 await page.locator('#close-inks').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual(await api('/api/carousel'),carouselBefore);
 assert.equal((await recipe()).inks.length,6,'Unreferenced ink disappears');
 // Custom document colors use the same inline editor and can exceed eight inks.
 await page.locator('#edit-inks').click();
 for(let n=0;n<3;n++){
   await page.getByRole('button',{name:'Add document ink',exact:true}).click();
   await page.locator('.ink-inline-editor input[name=label]').fill('Extra '+n);
   await page.locator('.ink-inline-editor input[name=color_srgb]').fill(['#445566','#664455','#556644'][n]);
   await page.locator('.ink-inline-editor input[name=width_mm]').fill('0.4');
   await page.getByRole('button',{name:'Apply ink',exact:true}).click();
 }
 assert.equal((await recipe()).inks.length,9);await page.locator('#close-inks').click();
 for(let n=0;n<3;n++)await page.locator('#undo').click();assert.equal((await recipe()).inks.length,6);
 // Four page rotations retain real margins and whole-pen containment (server verifies export).
 for(const angle of ['90','180','270','0']){await page.locator('#orientation').selectOption(angle);await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await recipe()).orientation,Number(angle));}
 const unitsBefore=await recipe();await page.locator('.studio-preferences>summary').click();await page.locator('#paper-unit').selectOption('mm');assert.deepEqual(await recipe(),unitsBefore);await page.keyboard.press('Escape');
 await page.locator('#save').click();await page.waitForFunction(()=>!document.querySelector('#document-tabs [role=tab][aria-selected=true]').textContent.includes('*'));
 const design=await recipe();await page.locator('#prepare').click();
 await page.waitForFunction(()=>!document.querySelector('#open-plot').disabled||document.querySelector('#prepare').textContent==='Retry preparation',null,{timeout:180000});
 assert(await page.locator('#open-plot').isEnabled(),await page.locator('#work-detail').textContent());
 const after=await workspace(),receipt=after.documents.find(d=>d.key===after.active).receipt;assert(receipt.ready&&receipt.jobs.length===1);
 const pdf=await api('/api/v1/documents/'+receipt.document);assert.equal(pdf.draft.status,'ready');
 const conversion=await api('/api/v1/conversions/'+receipt.conversion);assert.equal(conversion.intent.authoring.application,'pattern-studio');assert.equal(conversion.intent.authoring.document_id,saved.saved.id);
 assert.equal(conversion.intent.bindings.length,6);assert.equal(conversion.intent.palette.inks.length,6);
 await page.reload();await page.locator('#preview[data-ready=true]').waitFor();assert(await page.locator('#open-plot').isEnabled(),'Reload retains exact prepared plot');
 const opened=context.waitForEvent('page');await page.locator('#open-plot').click();const hpgl=await opened;await hpgl.locator('#preview-canvas[data-ready=true]').waitFor();
 assert.equal(new URL(page.url()).pathname,'/patterns');await hpgl.screenshot({path:state+'/hpgl.png'});
 await hpgl.locator('#studio-sources>summary').click();await hpgl.getByRole('link',{name:'Edit pattern',exact:true}).click();await sleep(300);assert.equal(context.pages().length,3,'Edit pattern reuses authoring tab');assert.deepEqual(await recipe(),design);
 await page.getByRole('button',{name:'Increase Peak angle',exact:true}).click();await page.locator('#preview[data-ready=true]').waitFor();assert(await page.locator('#open-plot').isDisabled(),'Changed design cannot open stale plot');
 await hpgl.locator('#studio-sources>summary').click();await hpgl.getByRole('link',{name:'View source pattern version',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('#document-tabs [role=tab]').length===2);assert.deepEqual(await recipe(),design);
 await page.getByRole('button',{name:'Dismiss message',exact:true}).click();
 await page.locator('#open-library').click();await page.locator('#documents .document-card').first().getByRole('button').click();assert((await recipe()).params.angle!==design.params.angle,'Open preserves dirty working document');
 await page.locator('#new-document').click();
 await checkPatternGallery(page,state+'/gallery.png');await chooseGalleryFamily(page,'julia');await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await recipe()).family,'julia');
 const julia=await page.evaluate(()=>window.patternWork.filter(w=>w.family==='julia'&&w.sampling).at(-1));assert(julia.sampling.target_mm===.15);assert(julia.previews>0);assert.equal(await page.locator('#knob-detail').count(),0,'Julia resolution is automatic');
 await page.getByRole('button',{name:'Increase Julia shape',exact:true}).click();
 await page.waitForFunction(()=>window.patternWork.some(w=>w.family==='julia'&&w.params.shape===4&&w.previews>0));await page.locator('#cancel-preview').click();
 await page.locator('#preview[data-ready=true]').waitFor();assert([280,560].includes((await recipe()).sampling_limit));assert(await page.locator('#prepare').isEnabled());assert(await page.locator('#refine-preview').isVisible());
 await page.locator('#refine-preview').click();await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await recipe()).sampling_limit,undefined);
 await page.locator('#family').selectOption('menger');await page.locator('#preview[data-ready=true]').waitFor();
 await page.locator('#knob-depth').evaluate(e=>{e.value='4';e.dispatchEvent(new Event('input',{bubbles:true}));});
 await page.waitForFunction(()=>window.patternWork.some(w=>w.family==='menger'&&w.params.depth===4&&w.previews>0));
 assert(await page.locator('#prepare').isDisabled(),'Interim geometry is never export-ready');await page.locator('#zoom-in').click();
 await page.locator('#preview[data-ready=true]').waitFor();
 const sponge=await page.evaluate(()=>window.patternWork.filter(w=>w.family==='menger'&&w.params.depth===4).at(-1));assert.equal(sponge.previews,2);assert(sponge.points>30000);await page.locator('#fit-view').click();await page.screenshot({path:state+'/menger-detail4.png'});
 // A coarse guide cannot replace the already-complete level 4 when stopped.
 await page.locator('#knob-depth').evaluate(e=>{e.value='5';e.dispatchEvent(new Event('input',{bubbles:true}));});
 await page.waitForFunction(()=>window.patternWork.some(w=>w.family==='menger'&&w.params.depth===5&&w.previews>0));await page.locator('#cancel-preview').click();
 await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await recipe()).params.depth,4);
 // A stale/cancelled worker must not replace a newer composition or enable export.
 await page.getByRole('button',{name:'Increase Azimuth',exact:true}).click();await page.waitForFunction(()=>window.patternWork.some(w=>w.family==='menger'&&w.params.azimuth===45&&w.previews>0));await page.locator('#cancel-preview').click();await page.locator('#preview[data-ready=true]').waitFor();assert(await page.locator('#prepare').isEnabled());assert((await recipe()).params.depth<4,'Cancel keeps a completed lower detail');
 await page.getByRole('button',{name:'Decrease Azimuth',exact:true}).click();await page.locator('#preview[data-ready=true]').waitFor();assert(await page.locator('#prepare').isEnabled());
 await page.locator('#edit-inks').click();assert.equal(await page.getByRole('button',{name:'All loaded',exact:true}).count(),0,'Sponge keeps its directional roles');await page.locator('#close-inks').click();
 console.log('Progressive sponge:',JSON.stringify({ms:sponge.finished-sponge.started,points:sponge.points,previews:sponge.previews}));
 if(process.env.PLOTLINE_LARGE_PATTERNS){
   if(await page.locator('#notice').isVisible())await page.getByRole('button',{name:'Dismiss message',exact:true}).click();
   const detail=async n=>page.locator('#knob-depth').evaluate((e,n)=>{e.value=String(n);e.dispatchEvent(new Event('input',{bubbles:true}));},n);
   await detail(5);
   await page.waitForFunction(()=>(()=>{const w=window.patternWork.filter(w=>w.family==='menger'&&w.params.depth===5).at(-1);return w?.previews===3&&w.progress?.label.includes('detail 5')&&w.progress.paths>1000;})(),null,{timeout:120000});
   await page.screenshot({path:state+'/menger-detail5-progress.png'});await page.locator('#cancel-preview').click();
   await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await recipe()).params.depth,4);assert(await page.locator('#prepare').isEnabled());
   await page.locator('#undo').click();
   await page.waitForFunction(()=>window.patternWork.filter(w=>w.family==='menger'&&w.params.depth===5).at(-1)?.previews===3,null,{timeout:120000});
   await page.locator('#zoom-in').click();assert(await page.locator('#prepare').isDisabled());
   await page.locator('#preview[data-ready=true]').waitFor({timeout:180000});
   const large=await page.evaluate(()=>window.patternWork.filter(w=>w.family==='menger'&&w.params.depth===5).at(-1));assert(large.points>300000);assert(large.frames>20);
   await page.locator('#fit-view').click();await page.screenshot({path:state+'/menger-detail5.png'});
   const exported=page.waitForResponse(r=>r.url().endsWith('/api/patterns/exports')&&r.request().method()==='POST');
   await page.locator('#prepare').click();const response=await exported;assert(response.ok(),await response.text());const record=await response.json();
   await page.waitForFunction(()=>!document.querySelector('#open-plot').disabled||document.querySelector('#prepare').textContent==='Retry preparation',null,{timeout:600000});
   assert(await page.locator('#open-plot').isEnabled(),await page.locator('#work-detail').textContent());
   await writeFile(state+'/large-result.json',JSON.stringify({large,record,workspace:await workspace()},null,2));
   console.log('Large sponge: '+JSON.stringify({ms:large.finished-large.started,points:large.points,frames:large.frames,exportHash:record.hash}));
 }
 for(const [name,p] of [['home',home],['studio',page]]){await p.bringToFront();await p.setViewportSize({width:420,height:900});await p.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth); // ResizeObserver updates the flow diagram on the next frame.
 if(name==='studio')await checkPreviewControls(p);await p.screenshot({path:state+'/'+name+'-mobile.png',fullPage:true});}
 assert.deepEqual(errors,[]);assert.deepEqual(await api('/api/carousel'),carouselBefore);
 await writeFile(state+'/result.json',JSON.stringify({receipt,design,errors},null,2));console.log('PASS all forty-seven grouped gallery previews, edits/undo, persistence/conflicts, inks, four orientations, PDF/HPGL preparation, reload, exact source snapshots, tab reuse, gallery and mobile. Artifacts: '+state);
}catch(error){if(browser)for(const p of browser.contexts()[0]?.pages()||[])await p.screenshot({path:state+'/failure-'+new URL(p.url()).pathname.replaceAll('/','_')+'.png',fullPage:true}).catch(()=>{});console.error('Artifacts:',state,'\nService:',logs);throw error;}
finally{await browser?.close();server.kill('SIGTERM');}
