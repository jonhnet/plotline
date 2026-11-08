// Replace an input while retaining its interpretation. Isolated state, no hardware.
import {chromium} from 'playwright';
import {spawn,spawnSync} from 'node:child_process';
import {mkdtemp,readFile,mkdir} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
await mkdir(root+'/build',{recursive:true});const state=await mkdtemp(root+'/build/pdf-replacement-');
const fixture=spawnSync(root+'/.venv/bin/python',['-',state],{input:`
import pathlib,sys,pymupdf
out=pathlib.Path(sys.argv[1])
for name,replacement in [('original',False),('replacement',True)]:
 d=pymupdf.open();keep=d.add_ocg('Keep');other=d.add_ocg('New' if replacement else 'Gone')
 for i in range(3 if replacement else 2):
  p=d.new_page(width=240 if replacement else 210,height=250)
  p.draw_line((30,40),(120,40),color=(0,0,0),width=2 if replacement else .6,oc=keep)
  p.draw_line((30,60),(120,60),color=(1,0,0),width=1,oc=other)
  if replacement:p.draw_rect((40,80,60,100),color=None,fill=(0,1,0))
 d.save(out/(name+'.pdf'))
`,encoding:'utf8'});assert.equal(fixture.status,0,fixture.stderr);
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1'};
const server=spawn(process.env.PLOTLINE_TEST_BINARY||root+'/controller/target/release/plotline-studios',['--state',state,'--listen',`127.0.0.1:${port}`],{env:{...process.env,PLOTLINE_PDF_PROJECT:root},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',data=>logs+=data);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(path,options={}){const r=await fetch(base+path,{...options,headers:{...headers,...options.headers}});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;}
const post=(path,value)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
async function done(path){for(let n=0;n<600;n++){const v=await api(path);if(!['received','inspecting','queued','running'].includes(v.status))return v;await sleep(100);}throw Error('Worker timed out');}
try{
 for(let n=0;;n++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(n>100)throw Error(logs);await sleep(100);}
 const carousel=await api('/api/carousel');
 for(const [slot,color] of [[1,'#000000'],[2,'#ff0000'],[3,'#00ff00']])carousel.slots[slot]={state:'loaded',color,label:color,width_mm:.3,speed_cm_s:5,force:4};
 await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(carousel)});
 const pdf=await readFile(state+'/original.pdf'),form=new FormData();form.append('document',new Blob([pdf]),'Artwork.pdf');
 const uploaded=await api('/api/v1/documents',{method:'POST',body:form}),original=await done(uploaded.status_url);
 assert.equal(original.status,'ready');assert.deepEqual(original.inspection.layers,['Gone','Keep']);
 const caps=await api('/api/v1/capabilities'),inks=caps.available_inks,black=inks.find(i=>i.color_srgb==='#000000'),red=inks.find(i=>i.color_srgb==='#ff0000');
 const intent={schema:'plotline.print-intent/1',source_sha256:original.source_sha256,title:'Artwork settings',
  palette:{schema:'plotline.palette/1',id:'test-palette',label:'Test pens',inks},
  pages:original.inspection.pages.map(p=>({page_index:p.page_index,source_size_mm:p.source_size_mm,placement:{scale:.6+p.page_index*.1,rotation_deg:p.page_index?0:90,offset_mm:[20,45]},target_media:caps.media_profiles[0]})),
  bindings:[{ocg_name:'Keep',ink_id:red.id},{ocg_name:'Gone',ink_id:black.id}],
  // Deliberate color substitution must survive a changed source stroke width.
  ink_mapping:Object.fromEntries(original.inspection.pages[0].source_styles.map(s=>[s.id,red.id])),
  policies:{fill:{mode:'crosshatch',spacing_mm:2,angle_deg:35},stroke_width_tolerance:.75,stroke_width_mode:'pen',thin_detail_thickening:.5,
   raster:'dither',raster_settings:{screening:true,palette:'selected',screen_inks:[black.id,red.id],fullness:.7,edge_falloff_mm:4,black_treatment:'off',inks:{}},
   gradients:'reject',transparency:'ignore-alpha',blend_modes:'reject',soft_masks:'reject'}};
 const saved=await post(`/api/v1/documents/${original.id}/recipes`,{name:'Artwork settings',intent});
 const oldDraft=await done((await post(`/api/v1/documents/${original.id}/draft`,intent)).status_url);assert.equal(oldDraft.status,'ready',oldDraft.error);
 browser=await chromium.launch();const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
 if(process.env.PLOTLINE_TEST_WEB)for(const [url,file,type] of [['/pdf','pdf.html','text/html'],['/pdf.js','pdf.js','text/javascript'],['/pdf.css','pdf.css','text/css'],['/pdf-recipe.js','pdf-recipe.js','text/javascript']])await context.route(u=>u.origin===base&&u.pathname===url,async r=>r.fulfill({body:await readFile(root+'/controller/web/'+file),contentType:type}));
 const workspace=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('plotline-pdf-workspace')));
 const active=async()=>{const s=await workspace();return s.documents.find(d=>d.key===s.active);};
 await page.goto(base+saved.review_url);await page.waitForFunction(()=>!document.getElementById('replace-source').disabled);
 await page.locator('#thin-detail').fill('0.75');await page.locator('#spacing').fill('3');
 const before=await active();assert.equal(before.recipe.policies.thin_detail_thickening,.75);
 await page.locator('#replace-source').click();assert.match(await page.locator('#replacement-hint').textContent(),/Artwork settings/);assert(await page.locator('#intent-upload').isHidden());
 await page.locator('#cancel-upload').click();assert.deepEqual((await active()).recipe,before.recipe,'Cancel changes nothing');
 // Keep inspection pending across reload: the borrowed settings must survive.
 let holdInspection=true,newId=null,conversions=0;
 await context.route(base+'/api/v1/documents/*',async route=>{
  if(route.request().method()!=='GET'||route.request().url().endsWith('/'+original.id))return route.continue();
  const response=await route.fetch(),value=await response.json();
  if(holdInspection&&value.id!==original.id){newId=value.id;value.status='inspecting';value.inspection=null;}
  return route.fulfill({json:value});
 });
 context.on('request',r=>{if(r.method()==='POST'&&/\/(draft|conversions)$/.test(new URL(r.url()).pathname))conversions++;});
 await page.locator('#replace-source').click();await page.locator('#pdf-file').setInputFiles(state+'/replacement.pdf');await page.locator('#upload-submit').click();
 await page.waitForFunction(()=>{const s=JSON.parse(sessionStorage.getItem('plotline-pdf-workspace'));return s.documents.length===2&&s.documents.find(d=>d.key===s.active).replacement;});
 assert(await page.locator('#replace-source').isDisabled());assert(await page.locator('#convert-button').isDisabled());
 assert(await page.locator('#preview').isHidden());assert.equal(await page.locator('#hpgl').getAttribute('href'),null,'No previous document download while inspection is pending');
 await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#document-tabs [role=tab]').length===2);
 assert.equal((await active()).replacement.intent.policies.thin_detail_thickening,.75);
 holdInspection=false;await page.waitForFunction(()=>!document.getElementById('replace-source').disabled);
 const updated=await active(),newDoc=await api('/api/v1/documents/'+newId);
 assert.notEqual(updated.recipe.source_sha256,before.recipe.source_sha256);assert.equal(updated.recipe.source_sha256,newDoc.source_sha256);
 assert.deepEqual(updated.recipe.policies,before.recipe.policies);assert.deepEqual(updated.recipe.palette,before.recipe.palette);
 assert.equal(updated.recipe.pages.length,3);
 for(let i=0;i<3;i++){
  assert.deepEqual(updated.recipe.pages[i].source_size_mm,newDoc.inspection.pages[i].source_size_mm);
  assert.deepEqual(updated.recipe.pages[i].placement,before.recipe.pages[i<2?i:0].placement);
  assert.deepEqual(updated.recipe.pages[i].target_media,before.recipe.pages[i<2?i:0].target_media);
 }
 const thickBlack=newDoc.inspection.pages[0].source_styles.find(i=>i.color_srgb==='#000000');
 assert.equal(updated.recipe.ink_mapping[thickBlack.id],red.id,'A width change retains an intentional color substitution');
 const oldBlack=original.inspection.pages[0].source_styles.find(i=>i.color_srgb==='#000000');
 assert.equal(updated.recipe.ink_mapping[oldBlack.id],undefined,'Removed source styles do not linger');
 assert.deepEqual(updated.recipe.bindings,[{ocg_name:'Keep',ink_id:red.id}]);
 assert.equal(updated.currentId,undefined);assert.equal(updated.saved,null);assert.equal(updated.replacement,null);
 assert(await page.locator('#update-plot').isDisabled());assert(await page.locator('#convert-button').isEnabled());assert.equal(conversions,0,'Replacement never converts or prepares a plot automatically');
 assert.match(await page.locator('#notice').textContent(),/Page sizes or count changed/);
 assert.deepEqual((await workspace()).documents.find(d=>d.documentId===original.id).recipe,before.recipe,'Unsaved original choices remain in their tab');
 assert.deepEqual(await api(`/api/v1/documents/${original.id}/recipes/${saved.id}`),saved,'Earlier saved recipe stays immutable');
 assert.deepEqual(Buffer.from(await(await fetch(base+`/api/v1/documents/${original.id}/source.pdf`)).arrayBuffer()),pdf);
 await page.locator('#save').click();await page.locator('#save-name').fill('Artwork revision');await page.locator('#save-form button[type=submit]').click();
 await page.waitForFunction(()=>document.getElementById('saved-state').textContent==='Saved');
 const newSaved=(await active()).saved;assert.equal(newSaved.document_id,newId);assert.notEqual(newSaved.id,saved.id);
 await page.reload();await page.waitForFunction(()=>document.getElementById('saved-state').textContent==='Saved');assert.deepEqual((await active()).recipe,newSaved.intent);
 await page.locator('#convert-button').click();await page.waitForFunction(()=>document.getElementById('work-status').dataset.phase==='ready',null,{timeout:120000});
 assert(await page.locator('#update-plot').isEnabled());
 const draft=(await api('/api/v1/documents/'+newId)).draft;assert(draft.jobs.some(j=>j.job_id));
 assert.deepEqual(await api(oldDraft.status_url),oldDraft,'Original conversion remains unchanged');
 // Source-sized paper and removed authoring links are also covered without a second upload.
 const special=await page.evaluate(async ({intent,source,sources})=>{
  const {replaceSourceRecipe}=await import('/pdf-recipe.js');
  intent.authoring={application:'map-studio',url:'http://localhost/map',recipe_hash:'a'.repeat(64)};
  intent.pages[0].target_media={id:'pdf-page'};
  return replaceSourceRecipe({intent,sources},source).intent;
 },{intent,source:newDoc,sources:original.inspection.pages[0].source_styles});
 assert.equal(special.authoring,undefined);assert.equal(special.pages[0].target_media,undefined);
 await page.screenshot({path:state+'/desktop.png'});await page.setViewportSize({width:420,height:900});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.locator('#replace-source').click();await page.screenshot({path:state+'/mobile.png',fullPage:true});await page.locator('#cancel-upload').click();
 await page.locator('#new-document').click();assert(await page.locator('#intent-upload').isVisible());assert(await page.locator('#replacement-hint').isHidden());assert.equal(await page.locator('#pdf-file').inputValue(),'');
 assert.deepEqual(errors,[]);console.log('PASS source replacement: working/saved settings, changed widths, intentional color mappings, paper placement, layer bindings, stale preview, pending reload, save/reopen, conversion, source immutability and mobile',state);
}catch(error){console.error(logs);throw error;}finally{if(browser)await browser.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));}
