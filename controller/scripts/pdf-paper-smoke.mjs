// Real conversion/registration in isolated state. No plotter connection.
import {chromium,firefox} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
const root=resolve(import.meta.dirname,'../..'),project=process.env.PLOTLINE_TEST_PROJECT||root;
await mkdir(root+'/build',{recursive:true});const state=await mkdtemp(root+'/build/pdf-paper-');
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1'};
const server=spawn(process.env.PLOTLINE_TEST_BINARY||root+'/controller/target/release/plotline-studios',['--state',state,'--listen',`127.0.0.1:${port}`],{env:{...process.env,PLOTLINE_PDF_PROJECT:project},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',data=>logs+=data);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(path,options={}){const r=await fetch(base+path,{...options,headers:{...headers,...options.headers}});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;}
async function done(path){for(let n=0;n<300;n++){const v=await api(path);if(!['received','inspecting','queued','running'].includes(v.status))return v;await sleep(100);}throw Error('Worker timed out');}
const near=(actual,want)=>{assert.equal(actual.length,want.length);actual.forEach((v,i)=>assert(Math.abs(v-want[i])<1e-5,`${actual} != ${want}`));};
try{
 for(let n=0;;n++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(n>100)throw Error(logs);await sleep(100);}
 const pdf=await readFile(project+'/build/pdf-boundary-v1/boundary.pdf'),intent=JSON.parse(await readFile(project+'/build/pdf-boundary-v1/boundary.print-intent.json'));
 const form=new FormData();form.append('document',new Blob([pdf]),'Paper loading.pdf');form.append('intent',new Blob([JSON.stringify(intent)]),'intent.json');
 const doc=await done((await api('/api/v1/documents',{method:'POST',body:form})).status_url);assert.equal(doc.status,'ready');
 browser=await(process.env.PLOTLINE_BROWSER==='firefox'?firefox:chromium).launch();const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/pdf?document='+doc.id);await page.waitForFunction(()=>!document.getElementById('convert-button').disabled);
 const selection=()=>page.evaluate(()=>{const s=JSON.parse(sessionStorage.getItem('plotline-pdf-workspace'));const d=s.documents.find(d=>d.key===s.active);return d.recipe.pages.find(p=>p.page_index===Number(document.getElementById('page').value));});
 const initial=await selection(),original=initial.target_media,placement=initial.placement;
 // A sheet turn composes the calibrated transform; it never rotates the PDF.
 await page.locator('#turn-sheet').click();let changed=await selection();
 near(changed.target_media.sheet_size_mm,[279.4,431.8]);assert.equal(changed.target_media.orientation_deg,90);assert.deepEqual(changed.placement,placement);
 near(changed.target_media.printable_rect_mm,[14.8,39.425,253.6,376.95]);
 near(changed.target_media.calibration.sheet_to_device_units,[0,40,40,0,-9116,-5664]);
 await page.locator('#convert-button').click();await page.waitForFunction(()=>!document.getElementById('update-plot').disabled,null,{timeout:120000});
 const turnedDraft=(await api('/api/v1/documents/'+doc.id)).draft;
 const turnedJob=await api('/api/jobs/'+turnedDraft.jobs[0].job_id);
 near(turnedJob.pdf.registration.sheet_to_device_units,changed.target_media.calibration.sheet_to_device_units);
 near(turnedJob.pdf.registration.limits,original.calibration.hard_clip_units);
 await page.locator('#undo').click();assert.deepEqual((await selection()).target_media,original);
 await page.locator('#redo').click();near((await selection()).target_media.sheet_size_mm,[279.4,431.8]);
 await page.locator('#turn-sheet').click();await page.locator('#turn-sheet').click();await page.locator('#turn-sheet').click();
 near((await selection()).target_media.printable_rect_mm,original.printable_rect_mm);
 near((await selection()).target_media.calibration.sheet_to_device_units,original.calibration.sheet_to_device_units);
 // Both physical loadings on the same 17×11 sheet, all four wide-margin edges.
 for(const angle of [90,180,270,0]){
  await page.locator('#sheet-loading').selectOption(String(angle));changed=await selection();
  assert.equal(changed.target_media.orientation_deg,angle);near(changed.target_media.sheet_size_mm,[431.8,279.4]);assert.deepEqual(changed.placement,placement);
  assert(!changed.target_media.calibration,'Changing platen/feed dimensions must drop the old measurement');
  await page.locator('#fit').click();
  await page.locator('#convert-button').click();await page.waitForFunction(()=>!document.getElementById('update-plot').disabled,null,{timeout:120000});
  const draft=(await api('/api/v1/documents/'+doc.id)).draft,job=await api('/api/jobs/'+draft.jobs[0].job_id);
  const reg=job.pdf.registration;assert.equal(reg.rotation,angle);near(reg.sheet_size_mm,[431.8,279.4]);
  near(reg.sheet_to_device_units.slice(0,4),({0:[40,0,0,-40],90:[0,40,40,0],180:[-40,0,0,40],270:[0,-40,-40,0]})[angle]);
  const setup=await api('/api/jobs/'+job.id+'/setup');near([setup.paper_x_mm,setup.paper_y_mm],angle%180?[279.4,431.8]:[431.8,279.4]);
  const hpgl=await(await fetch(base+'/api/jobs/'+job.id+'/source.hpgl')).text();
  const points=[...hpgl.matchAll(/(?:PU|PD)(-?\d+),(-?\d+);/g)];assert(points.length>0);
  for(const [,x,y] of points){const [l,b,r,t]=reg.limits;assert(Number(x)>=l&&Number(x)<=r&&Number(y)>=b&&Number(y)<=t,'Registered output stays inside device limits');}
  // Reset placement separately so the next loading change can prove it is unchanged.
  await page.locator('#scale').fill(String(placement.scale));
  await page.locator('#offset-x').fill(String(placement.offset_mm[0]/25.4));await page.locator('#offset-y').fill(String(placement.offset_mm[1]/25.4));
 }
 // New Letter defaults and the platen constraints at both ends of its range.
 const caps=await api('/api/v1/capabilities');
 const letter=caps.media_profiles.findIndex(p=>Math.abs(p.sheet_size_mm[0]-279.4)<.01&&Math.abs(p.sheet_size_mm[1]-215.9)<.01);
 await page.locator('#media').selectOption(String(letter));assert.equal(await page.locator('#sheet-loading').inputValue(),'90');
 assert(await page.locator('#sheet-loading option[value="0"]').evaluate(e=>e.disabled),JSON.stringify(await selection()));assert(await page.locator('#sheet-loading option[value="90"]').evaluate(e=>!e.disabled));
 near((await selection()).target_media.sheet_size_mm,[279.4,215.9]);
 await page.locator('#turn-sheet').click();near((await selection()).target_media.sheet_size_mm,[215.9,279.4]);assert.equal(await page.locator('#sheet-loading').inputValue(),'180');
 await page.locator('#rotation').selectOption('270');assert.equal((await selection()).target_media.orientation_deg,180);
 await page.reload();await page.waitForFunction(()=>document.getElementById('sheet-loading').value==='180');assert.equal(await page.locator('#rotation').inputValue(),'270');
 await page.locator('#save').click();await page.locator('#save-name').fill('Loading recipe');await page.locator('#save-form button[type=submit]').click();await page.waitForFunction(()=>document.getElementById('saved-state').textContent==='Saved');
 await page.locator('#turn-sheet').click();await page.locator('#revert').click();assert.equal(await page.locator('#sheet-loading').inputValue(),'180');
 await page.locator('#page').selectOption('1');await page.locator('#page').selectOption('0');assert.equal(await page.locator('#sheet-loading').inputValue(),'180');
 const large=caps.media_profiles.findIndex(p=>Math.abs(p.sheet_size_mm[0]-863.6)<.01&&Math.abs(p.sheet_size_mm[1]-1117.6)<.01);
 await page.locator('#media').selectOption(String(large));assert(await page.locator('#sheet-loading option[value="0"]').evaluate(e=>e.disabled),JSON.stringify(await selection()));assert(await page.locator('#sheet-loading option[value="90"]').evaluate(e=>!e.disabled));
 await page.locator('#turn-sheet').click();assert.equal(await page.locator('#sheet-loading').inputValue(),'180');assert(await page.locator('#sheet-loading option[value="90"]').evaluate(e=>e.disabled));
 await page.locator('#revert').click();
 const selector=await page.locator('#media').boundingBox(),turn=await page.locator('#turn-sheet').boundingBox();
 assert(selector.width>220);assert(turn.y+turn.height<=selector.y,'Turn sheet stays in the label row, clear of the paper selector');
 await page.screenshot({path:state+'/desktop.png'});
 await page.setViewportSize({width:420,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:state+'/mobile.png',fullPage:true});
 // An older saved Letter loading can be invalid even with a finished preview.
 // Its blocking reason must win over the generic stale-preview message.
 const snapshot=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('plotline-pdf-workspace')));
 const entry=snapshot.documents.find(d=>d.key===snapshot.active),sheet=entry.recipe.pages.find(p=>p.page_index===entry.page);
 sheet.target_media={schema:'plotline.media-profile/1',id:'legacy-letter',revision:1,label:'Old Letter',sheet_size_mm:[215.9,279.4],printable_rect_mm:[15,39,185.9,225.4],content_inset_mm:1};
 const stale=await context.newPage();stale.on('pageerror',e=>errors.push(e.message));
 await stale.addInitScript(snapshot=>sessionStorage.setItem('plotline-pdf-workspace',JSON.stringify(snapshot)),snapshot);
 await stale.goto(base+'/pdf?document='+doc.id);await stale.locator('#review-paper').waitFor();
 assert(await stale.locator('#convert-button').isDisabled());
 assert.match(await stale.locator('#work-detail').textContent(),/11 in to 36 in across the platen/);
 assert.doesNotMatch(await stale.locator('#work-detail').textContent(),/earlier choices/);
 assert.equal(await stale.locator('#work-progress').getAttribute('value'),'0');
 const statusHeight=(await stale.locator('#work-status').boundingBox()).height;
 await stale.setViewportSize({width:420,height:900});await stale.locator('#edit-inks').click();
 await stale.locator('#review-paper').click();assert(await stale.locator('#ink-drawer').isHidden());
 assert.equal(await stale.evaluate(()=>document.activeElement.id),'sheet-loading');
 await stale.locator('#sheet-loading').selectOption('0');
 assert(await stale.locator('#convert-button').isEnabled());assert(await stale.locator('#review-paper').isHidden());
 assert.equal((await stale.locator('#work-status').boundingBox()).height,statusHeight);
 const corrected=await stale.evaluate(()=>{const s=JSON.parse(sessionStorage.getItem('plotline-pdf-workspace'));return s.documents.find(d=>d.key===s.active).recipe.pages[0];});
 assert.deepEqual(corrected.placement,sheet.placement,'Resolving paper loading does not edit artwork placement');
 assert.deepEqual(errors,[]);console.log('PASS paper turns, independent loading/artwork, all four registered device axes, clipping, Letter/E limits, undo/redo/reload/save/revert/page switching, stale-layout blocking reason and focused correction, mobile; artifacts',state);
}catch(error){console.error(logs);throw error;}finally{await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));}
