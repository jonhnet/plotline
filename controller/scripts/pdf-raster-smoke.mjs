// Full browser -> PDF worker -> registered HPGL draft, isolated from hardware.
import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,mkdir} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
await mkdir(root+'/build',{recursive:true});const state=await mkdtemp(root+'/build/pdf-raster-browser-');
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1'};
const server=spawn(process.env.PLOTLINE_BINARY||root+'/controller/target/release/plotline-studios',['--state',state,'--listen',`127.0.0.1:${port}`],{env:{...process.env,PLOTLINE_PDF_PROJECT:root},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',data=>logs+=data);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(path,options={}){const r=await fetch(base+path,{...options,headers:{...headers,...options.headers}});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;}
async function done(path){for(let n=0;n<600;n++){const v=await api(path);if(!['received','inspecting','queued','running'].includes(v.status))return v;await sleep(200);}throw Error('Worker timed out');}
try{
 for(let n=0;;n++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(n>100)throw Error(logs);await sleep(100);}
 const caps=await api('/api/v1/capabilities');assert(caps.implemented.raster_policies.includes('dither'));
 const inventory=await api('/api/carousel');
 inventory.slots[4]={state:'loaded',label:'hot pink',color:'#ff1493',width_mm:.7,speed_cm_s:5,force:4};
 for(const [slot,label,color,width] of [[1,'Blue','#245bd6',.25],[2,'Red','#ff0000',.3],[3,'Turquoise','#009f7c',.25],[5,'Purple','#800080',.3],[6,'Yellow','#ffff00',.3],[7,'Green','#008000',.3],[8,'Old reliable','#000000',.25]])
  inventory.slots[slot]={state:'loaded',label,color,width_mm:width,speed_cm_s:5,force:4};
 await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(inventory)});
 const original=await readFile(root+'/build/pdf-raster-integration/fixture/mixed-raster-vector.pdf');
 const intent=JSON.parse(await readFile(root+'/build/pdf-raster-integration/intent.json','utf8'));
 intent.pages[0].target_media=caps.media_profiles.find(p=>p.sheet_size_mm[0]>200&&p.sheet_size_mm[1]>250);
 intent.pages[0].placement.offset_mm=[20,40];
 const form=new FormData();form.append('document',new Blob([original]),'Raster boundary.pdf');form.append('intent',new Blob([JSON.stringify(intent)]),'intent.json');
 const uploaded=await api('/api/v1/documents',{method:'POST',body:form});const doc=await done(uploaded.status_url);assert.equal(doc.status,'ready',doc.error);
 // Direct/API callers also get operator guidance without an interpreter traceback.
 const missingK=structuredClone(intent);missingK.policies.raster_settings={...missingK.policies.raster_settings,palette:'selected',screen_inks:['k'],inks:{}};
 const rejected=await fetch(base+`/api/v1/documents/${doc.id}/conversions`,{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(missingK)});
 assert.equal(rejected.status,422);const rejection=(await rejected.json()).error;
 assert.match(rejection,/Images & effects.*Black treatment pen.*Off/);assert.doesNotMatch(rejection,/Traceback|exit status|pdf_driver.py/);
 browser=await chromium.launch();const context=await browser.newContext({viewport:{width:1440,height:1000}});const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 // A normal PDF upload has no authored intent. Make the image decision visible
 // and report an actionable error when Preview is pressed without choosing one.
 const rawForm=new FormData();rawForm.append('document',new Blob([original]),'Unconfigured images.pdf');
 const rawUpload=await api('/api/v1/documents',{method:'POST',body:rawForm});
 const rawDoc=await done(rawUpload.status_url);assert.equal(rawDoc.status,'ready');
 const gate=await browser.newPage();gate.on('pageerror',e=>errors.push(e.message));
 await gate.goto(base+'/pdf?document='+rawDoc.id);
 await gate.waitForFunction(()=>!document.getElementById('convert-button').disabled);
 assert.equal(await gate.locator('#raster').inputValue(),'reject');
 assert(await gate.locator('#raster').isVisible(),'Image decision is expanded on a fresh image PDF');
 assert.match(await gate.locator('#raster-decision-hint').textContent(),/2 raster images/);
 await gate.locator('#convert-button').click();
 await gate.waitForFunction(()=>document.getElementById('work-status').dataset.phase==='unsupported',null,{timeout:120000});
 assert.match(await gate.locator('#notice').textContent(),/Draw with pens.*assign channel pens/);
 assert.equal(await gate.locator('#work-detail').textContent(),'Choose how to handle raster images');
 const failedDoc=await api('/api/v1/documents/'+rawDoc.id),failed=failedDoc.conversions[0];
 assert.equal(failed.status,'unsupported');assert.match(failed.error,/Require a decision/);
 // Reopening the user's original terse error should help too, without rewriting
 // immutable server records or substituting error text for unrelated failures.
 await gate.route(base+'/api/v1/documents/'+rawDoc.id,async route=>{
  const response=await route.fetch(),value=await response.json();
  value.conversions=value.conversions.map(c=>c.id===failed.id?{...c,error:'raster image: '}:c);
  await route.fulfill({json:value});
 });
 await gate.route(base+'/api/v1/conversions/'+failed.id,async route=>{
  const response=await route.fetch(),value=await response.json();
  await route.fulfill({json:{...value,error:'raster image: '}});
 });
 await gate.reload();await gate.waitForFunction(()=>document.getElementById('work-status').dataset.phase==='unsupported');
 assert.match(await gate.locator('#notice').textContent(),/Draw with pens.*assign channel pens/);
 await gate.locator('#raster').selectOption('dither');
 assert(await gate.locator('#raster-controls').isVisible());
 assert(await gate.locator('#raster-decision-hint').isHidden());
 assert.equal(await gate.locator('#work-detail').textContent(),'Image choice updated; preview again');
 assert.equal(await gate.locator('#notice').textContent(),'');
 await gate.locator('#raster-palette').selectOption('selected');await gate.locator('#raster-loaded-pens').click();
 assert(await gate.locator('#convert-button').isDisabled());assert(await gate.locator('#update-plot').isDisabled());
 assert.match(await gate.locator('#raster-assignment-hint').textContent(),/Black treatment pen or set Black treatment to Off/);
 assert.equal(await gate.locator('#raster-ink-K').getAttribute('aria-invalid'),'true');
 assert(await gate.getByLabel('Black treatment pen',{exact:true}).isVisible());
 await gate.locator('#raster-black').selectOption('off');assert(await gate.locator('#convert-button').isEnabled());assert(await gate.locator('#raster-assignment-hint').isHidden());
 await gate.locator('#raster-black').selectOption('lines');assert(await gate.locator('#convert-button').isDisabled());
 const blackOption=await gate.locator('#raster-ink-K option').evaluateAll(options=>options.find(o=>o.textContent.includes('Old reliable'))?.value);assert(blackOption);
 await gate.locator('#raster-ink-K').selectOption(blackOption);assert(await gate.locator('#convert-button').isEnabled());
 await gate.reload();await gate.waitForFunction(()=>document.querySelector('#raster-palette').value==='selected');
 assert.equal(await gate.locator('#raster-ink-K').inputValue(),blackOption);assert(await gate.locator('#convert-button').isEnabled());
 let calibrated=await api('/api/carousel');calibrated.slots[8].width_mm=.549;await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(calibrated)});
 await gate.waitForFunction(()=>document.querySelector('#raster-ink-K').selectedOptions[0].textContent.includes('0.549'));
 assert.equal(await gate.locator('#raster-ink-K').inputValue(),blackOption,'Applying calibration keeps the explicit role assignment');
 calibrated=await api('/api/carousel');calibrated.slots[8].width_mm=.25;await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(calibrated)});
 await gate.close();
 await page.goto(base+'/pdf?document='+doc.id);await page.waitForFunction(()=>!document.getElementById('convert-button').disabled);
 // A rename must change the visible carousel caption as well as its tooltip
 // and saved inventory. Keep the swatch unchanged to catch color-name fallback.
 await page.locator('#edit-inks').click();
 await page.getByRole('button',{name:'Edit loaded pen — slot 4',exact:true}).click();
 await page.locator('.ink-dialog input[name=label]').fill('magenta');
 await page.locator('.ink-dialog input[name=color]').fill('#ff1493'); // Keep this a rename-only regression.
 await page.getByRole('button',{name:'OK',exact:true}).click();
 const slot4=page.locator('.ink-loaded').filter({has:page.locator('[data-slot="4"]')});
 await page.waitForFunction(()=>[...document.querySelectorAll('.ink-loaded')].some(e=>e.querySelector('[data-slot="4"]')&&e.textContent.includes('magenta')));
 assert.match(await slot4.textContent(),/magenta/);assert.doesNotMatch(await slot4.textContent(),/hot pink/);
 let saved=await api('/api/carousel');assert.equal(saved.slots[4].label,'magenta');assert.equal(saved.slots[4].color,'#ff1493');
 await page.reload();await page.locator('#edit-inks').click();assert.match(await slot4.textContent(),/magenta/);
 await page.locator('#close-inks').click();
 assert(await page.locator('#raster-controls').isVisible());assert.equal(await page.locator('#raster-ink-C').inputValue(),'c');
 assert(await page.locator('#raster-falloff').isDisabled());
 await page.locator('#raster-fullness').fill('75');await page.locator('#raster-fullness').dispatchEvent('input');assert(await page.locator('#raster-falloff').isEnabled());
 await page.locator('#raster-falloff').fill('0.5');await page.locator('#raster-palette').selectOption('cmyk');
 await page.locator('#raster-black').selectOption('shapes');
 await page.locator('#undo').click();assert.equal(await page.locator('#raster-black').inputValue(),'lines');
 await page.reload();await page.waitForFunction(()=>document.getElementById('raster').value==='dither');
 assert.equal(await page.locator('#raster-fullness').inputValue(),'75');assert.equal(await page.locator('#raster-falloff').inputValue(),'0.5');assert.equal(await page.locator('#raster-palette').inputValue(),'cmyk');
 await page.locator('#convert-button').click();
 await page.waitForFunction(()=>document.getElementById('work-status').dataset.phase==='ready'&&!!document.getElementById('work-status').dataset.conversion,null,{timeout:120000});
 await page.waitForFunction(()=>document.getElementById('convert-button').disabled,null,{timeout:3000}).catch(async e=>{console.error(await page.evaluate(()=>({workspace:JSON.parse(sessionStorage.getItem('plotline-pdf-workspace')),notice:document.getElementById('notice').textContent,dirty:document.getElementById('dirty').textContent})));throw e;});
 const stateOf=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('plotline-pdf-workspace')));
 let workspace=await stateOf(),active=workspace.documents.find(d=>d.key===workspace.active);
 const conversion=await api('/api/v1/conversions/'+active.currentId);
 assert.equal(conversion.status,'ready',conversion.error);assert.equal(conversion.intent.policies.raster_settings.fullness,.75);
 assert.equal(conversion.intent.policies.raster_settings.edge_falloff_mm,12.7);
 assert(conversion.result.pages[0].raster_images.every(i=>i.screen_palette==='cmyk'&&i.black_policy==='ridge'));
 assert((await fetch(base+conversion.result.pages[0].artifacts.hpgl)).ok);
 const firstDraft=await api(`/api/v1/conversions/${conversion.id}/publish`,{method:'POST'});
 const previousJob=firstDraft.jobs[0].job_id;
 // Arbitrary screening pens survive undo/reload and freeze live materials.
 await page.locator('#raster-black').selectOption('off');
 await page.locator('#raster-palette').selectOption('selected');
 assert(await page.locator('#convert-button').isDisabled());
 await page.locator('#raster-loaded-pens').click();
 assert.equal(await page.locator('#raster-screen-pens input:checked').count(),8);
 assert.equal(await page.locator('#raster-screen-pens input:not(:checked):not(:disabled)').count(),0);
 const blue=()=>page.locator('#raster-selected label').filter({hasText:/Blue ·/}).locator('input');
 await blue().uncheck();assert.equal(await page.locator('#raster-screen-pens input:checked').count(),7);
 await page.locator('#undo').click();assert(await blue().isChecked());
 await page.reload();await page.waitForFunction(()=>document.getElementById('raster-palette').value==='selected');
 assert.equal(await page.locator('#raster-screen-pens input:checked').count(),8);
 const beforeSelected=active.currentId;
 // Applying a scan retains the nominal pen identity and freezes the measured
 // color into the conversion. Reload and undo must keep live calibration.
 saved=await api('/api/carousel');saved.slots[6].measured_color='#edae60';
 await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(saved)});
 await page.waitForFunction(()=>!!document.querySelector('#raster-screen-pens [title="Scanned color #edae60"]'));
 await page.reload();await page.waitForFunction(()=>!!document.querySelector('#raster-screen-pens [title="Scanned color #edae60"]'));
 await page.locator('#convert-button').click();
 await page.waitForFunction(old=>document.getElementById('work-status').dataset.conversion!==old,beforeSelected,{timeout:120000});
 await page.waitForFunction(()=>document.getElementById('work-status').dataset.phase==='ready'&&document.getElementById('convert-button').disabled,null,{timeout:120000});
 workspace=await stateOf();active=workspace.documents.find(d=>d.key===workspace.active);
 const selected=await api('/api/v1/conversions/'+active.currentId);
 assert.equal(selected.intent.policies.raster_settings.screen_inks.length,8);
 assert(selected.result.pages[0].raster_images.every(i=>i.screen_palette==='selected'&&i.separation.color_space==='oklab'&&i.black_policy==='off'));
 assert.match(await page.locator('#metrics').textContent(),/Selected pens.*Blue/);
 const frozenYellow=selected.intent.palette.inks.find(i=>i.source?.slot===6);
 assert.equal(frozenYellow.color_srgb,'#ffff00');assert.equal(frozenYellow.measured_color_srgb,'#edae60');
 assert(selected.result.pages[0].raster_images.every(i=>i.screen_inks.find(p=>p.id===frozenYellow.id)?.color_srgb==='#edae60'));
 const penSvg=await (await fetch(base+selected.result.pages[0].artifacts.pen)).text();
 assert(penSvg.includes('stroke="rgb(237,174,96)"'),'PDF preview uses the same swatch as separation');
 const selectedHpgl=await (await fetch(base+selected.result.pages[0].artifacts.hpgl)).text();
 assert(selectedHpgl.includes('r=255 g=255 b=0'),'HPGL still requests the nominal yellow pen');
 saved=await api('/api/carousel');saved.slots[6].measured_color='#e0a050';
 await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(saved)});
 await page.waitForFunction(()=>!document.getElementById('convert-button').disabled);
 await blue().uncheck();await page.locator('#undo').click();
 workspace=await stateOf();active=workspace.documents.find(d=>d.key===workspace.active);
 assert.equal(active.recipe.palette.inks.find(i=>i.id===frozenYellow.id).measured_color_srgb,'#e0a050');
 saved=await api('/api/carousel');delete saved.slots[6].measured_color;
 await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(saved)});
 await page.waitForFunction(()=>!!document.querySelector('#raster-screen-pens [title="Declared color #ffff00 · no scan applied"]'));
 workspace=await stateOf();active=workspace.documents.find(d=>d.key===workspace.active);
 assert.equal(active.recipe.palette.inks.find(i=>i.id===frozenYellow.id).measured_color_srgb,null);
 saved=await api('/api/carousel');saved.slots[6].measured_color='#edae60';
 await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(saved)});
 await page.waitForFunction(()=>document.getElementById('convert-button').disabled);
 assert.equal((await api('/api/v1/conversions/'+selected.id)).intent.palette.inks.find(i=>i.id===frozenYellow.id).measured_color_srgb,'#edae60');
 // A changed live slot makes the preview stale, and an empty slot blocks it.
 saved=await api('/api/carousel');saved.slots[1].width_mm=.35;
 await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(saved)});
 await page.waitForFunction(()=>!document.getElementById('convert-button').disabled,null,{timeout:10000});
 assert.match(await page.locator('#raster-screen-pens').textContent(),/Blue · 0.35 mm/);
 saved=await api('/api/carousel');saved.slots[1].state='empty';
 await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(saved)});
 await page.waitForFunction(()=>document.getElementById('convert-button').disabled,null,{timeout:10000});
 await blue().click();assert(await page.locator('#convert-button').isEnabled());
 const frozenBlue=selected.intent.palette.inks.find(i=>i.source?.slot===1);
 assert.equal(frozenBlue.width_mm,.25,'Prior conversion retains its material snapshot');
 await page.screenshot({path:state+'/selected-pens.png'});
 // Black-only ink-shape treatment, preserving independent screening switch.
 await page.locator('#raster-screening').uncheck();await page.locator('#raster-black').selectOption('shapes');
 assert(await page.locator('#convert-button').isEnabled());assert(await page.locator('#raster-palette').isDisabled());
 const prior=active.currentId;await page.locator('#convert-button').click();await page.waitForFunction(old=>document.getElementById('work-status').dataset.conversion!==old,prior,{timeout:120000});await page.waitForFunction(()=>document.getElementById('work-status').dataset.phase==='ready'&&!!document.getElementById('work-status').dataset.conversion,null,{timeout:120000});
 workspace=await stateOf();active=workspace.documents.find(d=>d.key===workspace.active);
 const shapes=await api('/api/v1/conversions/'+active.currentId);assert(shapes.result.pages[0].raster_images.every(i=>i.black_policy==='luminance'&&Object.keys(i.screen_paths_by_ink).length===0));
 await page.waitForFunction(()=>!document.getElementById('update-plot').disabled,null,{timeout:120000});assert.equal(await page.locator('#update-plot').textContent(),'Open plot ↗');
 const draft=(await api(`/api/v1/documents/${doc.id}`)).draft;assert.equal(draft.status,'ready');assert(draft.jobs.length);
 const job=await api('/api/jobs/'+draft.jobs[0].job_id);assert.equal(job.pdf.raster_preview,true);
 // Synthetic crossing: the HPGL viewer must mix different inks, but not
 // darken multiple strokes of one pen. No challenge artwork enters this test.
 await context.route(base+'/raster-crossing.svg',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg><g id="pen-1"><path d="M10,50L90,50M10,50L90,50"/></g><g id="pen-2"><path d="M50,10L50,90"/></g></svg>'}));
 const pixels=await page.evaluate(async()=>{
  async function sample(mix){
   const worker=new Worker('/render-worker.js',{type:'module'});
   const message=()=>new Promise((resolve,reject)=>{worker.onmessage=e=>e.data.type==='error'?reject(Error(e.data.message)):resolve(e.data);worker.onerror=reject;});
   const ready=message();worker.postMessage({type:'load',job:{summary:{bounds:[0,0,100,100],pens:[{id:1},{id:2}]},units_per_mm:1,pdf:{raster_preview:mix,registration:{rotation:0}}},url:location.origin+'/raster-crossing.svg'});await ready;
   const frame=message();worker.postMessage({type:'render',spec:{view:[0,-100,100,100],width:100,height:100,dpr:1,rotation:0,facing:'artwork',pens:[1,2],colors:{1:'#00ffff',2:'#ff00ff'},widths:{1:10,2:10},travel:false}});
   const {bitmap}=await frame;const canvas=new OffscreenCanvas(100,100),ctx=canvas.getContext('2d');ctx.drawImage(bitmap,0,0);worker.terminate();
   return [...ctx.getImageData(50,50,1,1).data];
  }
  return {plain:await sample(false),mixed:await sample(true)};
 });
 assert.deepEqual(pixels.plain,[255,0,255,255]);assert.deepEqual(pixels.mixed,[0,0,255,255]);
 await page.screenshot({path:state+'/desktop.png'});await page.setViewportSize({width:420,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 const hpgl=await context.newPage();hpgl.on('pageerror',e=>errors.push(e.message));
 await hpgl.goto(base+'/hpgl#'+previousJob);await hpgl.locator('#version-notice').waitFor();
 assert(await hpgl.locator('#plot-open').isDisabled());assert.match(await hpgl.locator('#version-message').textContent(),/newer version/);
 await hpgl.locator('#open-latest').click();await hpgl.waitForURL('**/hpgl#'+job.id);
 assert(await hpgl.locator('#version-notice').isHidden());
 assert.equal(await hpgl.getByRole('tab').count(),2,'Opening latest retains the old tab and its settings');
 await hpgl.close();
 // Reproduce the reported old palette: a renamed magenta, obsolete purple and
 // green snapshots, plus new blue/yellow entries for those same physical slots.
 saved=await api('/api/carousel');
 saved.slots[3].state='unavailable';
 saved.slots[5]={...saved.slots[5],label:'blue',color:'#0000ff',width_mm:.694};
 saved.slots[6]={...saved.slots[6],label:'yellow',color:'#ffff00',width_mm:.723};
 saved.slots[7]={...saved.slots[7],label:'Turquoise',color:'#009f7c',width_mm:.658};
 saved=await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(saved)});
 const legacy=structuredClone(intent);
 legacy.title='Historical screening selections';
 legacy.palette.inks=[
  ...[[2,'red','#ff0000',.3],[4,'hot pink','#ff1493',.7],[5,'purple','#800080',.694],[6,'green','#008000',.723],[7,'Turquoise','#009f7c',.658],[8,'black','#000000',.25]].map(([slot,label,color,width])=>({id:'old-'+slot,label,color_srgb:color,width_mm:width,source:{kind:'plotline-carousel',slot,revision:1}})),
  ...[5,6].map(slot=>({id:'new-'+slot,label:saved.slots[slot].label,color_srgb:saved.slots[slot].color,width_mm:saved.slots[slot].width_mm,source:{kind:'plotline-carousel',slot,revision:saved.revision,follow_slot:true}}))
 ];
 legacy.palette.inks.push({...structuredClone(legacy.palette.inks[1]),id:'duplicate-pink'});
 legacy.policies.raster_settings={screening:true,palette:'selected',screen_inks:['old-2','old-4','duplicate-pink','old-6','old-7','old-8','new-5','new-6'],fullness:1,edge_falloff_mm:6.35,black_treatment:'off',inks:{}};
 const oldForm=new FormData();oldForm.append('document',new Blob([original]),'Historical screening.pdf');oldForm.append('intent',new Blob([JSON.stringify(legacy)]),'intent.json');
 const oldUpload=await api('/api/v1/documents',{method:'POST',body:oldForm});const oldDoc=await done(oldUpload.status_url);assert.equal(oldDoc.status,'ready',oldDoc.error);
 const oldPage=await context.newPage();oldPage.on('pageerror',e=>errors.push(e.message));
 await oldPage.goto(base+'/pdf?document='+oldDoc.id);
 await oldPage.waitForFunction(()=>document.getElementById('raster-palette').value==='selected'&&document.querySelectorAll('#raster-screen-pens input').length===6);
 assert(await oldPage.locator('#raster-palette').isVisible());
 assert.equal(await oldPage.locator('#image-effects').evaluate(e=>e.tagName),'FIELDSET');
 assert.equal(await oldPage.locator('#image-effects').evaluate(e=>e.parentElement.id),'settings-scroll');
 assert.equal(await oldPage.locator('#image-effects').locator('legend').textContent(),'Images & effects');
 assert.equal(await oldPage.locator('#raster-screen-pens input').count(),6);
 const caption=await oldPage.locator('#raster-screen-pens').textContent();
 assert.match(caption,/Slot 4: magenta/);assert.match(caption,/Slot 5: blue/);assert.match(caption,/Slot 6: yellow/);
 assert.doesNotMatch(caption,/hot pink|purple|green|Slot 1:|Slot 3:/);
 assert.equal(await oldPage.locator('#raster-screen-pens input:checked').count(),6);
 assert.match(await oldPage.locator('#raster-screen-count').textContent(),/6 of 6 loaded pens selected · 1 unavailable selection/);
 assert.equal(await oldPage.locator('#raster-missing-pens input').count(),1);
 assert.match(await oldPage.locator('#raster-missing-pens').textContent(),/green · 0.723 mm · not loaded/);
 assert(await oldPage.locator('#convert-button').isDisabled());
 await oldPage.locator('#raster-loaded-pens').click();
 assert(await oldPage.locator('#raster-missing-pens').isHidden());
 assert(await oldPage.locator('#convert-button').isEnabled());
 await oldPage.locator('#undo').click();assert(await oldPage.locator('#raster-missing-pens').isVisible());
 await oldPage.locator('#redo').click();assert(await oldPage.locator('#raster-missing-pens').isHidden());
 await oldPage.reload();await oldPage.waitForFunction(()=>document.getElementById('raster-palette').value==='selected'&&document.querySelectorAll('#raster-screen-pens input').length===6);
 assert.equal(await oldPage.locator('#raster-screen-pens input:checked').count(),6);
 await oldPage.locator('#convert-button').click();
 await oldPage.waitForFunction(()=>document.getElementById('work-status').dataset.phase==='ready'&&!!document.getElementById('work-status').dataset.conversion&&document.getElementById('convert-button').disabled,null,{timeout:120000});
 const fixed=await api('/api/v1/conversions/'+await oldPage.locator('#work-status').getAttribute('data-conversion'));
 assert.equal(fixed.intent.policies.raster_settings.screen_inks.length,6);
 for(const image of fixed.result.pages[0].raster_images){assert.equal(image.screen_inks.length,6);assert(image.screen_inks.some(i=>i.label==='magenta'));assert(!image.screen_inks.some(i=>['hot pink','green','purple'].includes(i.label)));}
 const retained=await api('/api/v1/documents/'+oldDoc.id);
 assert.equal(retained.intent.palette.inks.find(i=>i.id==='old-4').label,'hot pink','Source material snapshots stay immutable');
 saved=await api('/api/carousel');saved.slots[4].label='Magenta renamed';
 await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(saved)});
 await oldPage.waitForFunction(()=>document.getElementById('raster-screen-pens').textContent.includes('Slot 4: Magenta renamed'),null,{timeout:10000});
 await oldPage.screenshot({path:state+'/current-screening-pens.png'});
 await oldPage.close();
 assert.deepEqual(errors,[]);console.log('PASS raster workflow and explicit recovery from an older HPGL version; artifacts',state);
}catch(error){console.error(logs);throw error;}finally{if(browser)await browser.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));}
