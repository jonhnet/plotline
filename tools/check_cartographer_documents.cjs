#!/usr/bin/env node
const assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),{chromium}=require('playwright');
const base=process.argv[2],out=process.argv[3],sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const browser=await chromium.launch();try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept());
  let previews=0,holdSave=false,releaseSave=null,holdPreview=false,releasePreview=null;
  await context.route('**/api/capabilities',r=>r.fulfill({json:{offline:false,captured_at:'2026-11-01',capabilities:{implemented:{pdf_upload:true,pdf_handoff:true},available_inks:[],inventory_revision:1,media_profiles:[]}}}));
  await context.route('**/api/cancel',r=>r.fulfill({json:{status:'cancelled'}}));
  await context.route('**/api/preview',async r=>{
   previews++;const b=r.request().postDataJSON(),key=crypto.createHash('sha256').update(JSON.stringify(b.recipe)).digest('hex');
   if(holdPreview){holdPreview=false;await new Promise(resolve=>releasePreview=resolve);}
   return r.fulfill({json:{id:key,cache_key:key,recipe_hash:key,session:b.session,revision:b.revision,quality:b.quality,status:'ready',artifacts:'/artifacts/'+key+'/',progress:{stage:'complete'},result:{seconds:.1,source:{zoom:12,tile_count:1},fetched_bounds:[-123,47,-122,48],feature_counts:{roads:23},label_count:0,omitted_labels:[],warnings:[],intent_available:true}}});
  });
  await context.route('**/artifacts/*/preview.png',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"><rect width="900" height="600" fill="#fffefa"/><path d="M0 110 Q300 10 900 450 M0 160 Q300 60 900 500 M0 210 Q300 110 900 550 M0 260 Q300 160 900 600 M0 310 Q300 210 900 650" fill="none" stroke="#657959" stroke-width="2"/><path d="M40 0 Q480 200 380 600 M80 0 Q520 200 420 600" fill="none" stroke="#526a82" stroke-width="5"/></svg>'}));
  await page.route('**/api/recipes',async r=>{
   if(holdSave&&r.request().method()==='POST'){
    holdSave=false;const response=await r.fetch();await new Promise(resolve=>releaseSave=resolve);await r.fulfill({response});
   }else await r.continue();
  });
  const draft=()=>page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
  const edit=async(id,value)=>{await page.locator('#'+id).fill(String(value));await page.locator('#'+id).press('Tab');};
  const saved=()=>page.waitForFunction(()=>document.querySelector('#saved-state').textContent==='Saved');
  const list=async()=>await(await page.request.get(base+'/api/recipes')).json();
  const current=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('plotline-map-draft')));
  const namedSave=async(button,name)=>{await page.locator(button).click();await page.locator('#save-dialog').waitFor();if(name!==undefined)await page.locator('#save-name').fill(name);await page.locator('#save-form button[type=submit]').click();await saved();};
  const tab=name=>page.getByRole('tab',{name,exact:true});
  const open=async id=>{await page.locator('#open-library').click();await page.locator('.document-card[data-document="'+id+'"] button').click();await page.locator('#library').waitFor({state:'hidden'});await draft();};
  await page.goto(base);await draft();
  assert(await page.locator('#revert').isDisabled(),'An unsaved document has no saved recipe to restore');
  await edit('title','Ridges');await edit('longitude',-122.44);await draft();
  assert.match(await page.locator('#saved-state').textContent(),/Unsaved/);
  await namedSave('#save','Mountain study');const first=(await list())[0];
  assert.equal(first.title,'Mountain study');assert.equal(first.revision,1);assert(first.thumbnail_url);
  assert.equal((await current()).recipe.title,'Ridges');assert(await tab('Mountain study').isVisible());
  const thumbnail=await page.request.get(base+first.thumbnail_url);assert.equal(thumbnail.status(),200);assert.match(thumbnail.headers()['content-type'],/image\/jpeg/);
  await edit('scale',42000);await draft();await page.keyboard.press('Control+s');await saved();
  assert.equal((await list()).length,1);assert.equal((await list())[0].id,first.id);assert.equal((await list())[0].revision,2);
  await edit('longitude',-121.6);await draft();const beforeReload=previews;
  await page.reload();await draft();assert.equal(await page.locator('#longitude').inputValue(),'-121.6');assert(await tab('Mountain study *').isVisible());
  assert.equal(previews,beforeReload,'Reload should use the existing preview');
  // Revert the reopened draft to its most recent save in the same tab. It must
  // not create a revision, and Undo must recover the discarded composition.
  const editedBeforeRevert=(await current()).recipe,tabCount=await page.getByRole('tab').count();
  const savedBeforeRevert=await(await page.request.get(base+'/api/recipes/'+first.id)).json();
  assert(await page.locator('#revert').isEnabled());await page.locator('#revert').click();await saved();await draft();
  assert.deepEqual((await current()).recipe,savedBeforeRevert.recipe);
  assert.equal(await page.getByRole('tab').count(),tabCount);assert(await tab('Mountain study').isVisible());
  assert(await page.locator('#revert').isDisabled());
  assert.equal((await list()).find(r=>r.id===first.id).revision,2);
  await page.locator('#undo-map').click();await draft();assert.deepEqual((await current()).recipe,editedBeforeRevert);
  assert(await page.locator('#revert').isEnabled());
  await page.locator('#save-copy').click();await page.locator('#save-dialog').waitFor();assert.equal(await page.locator('#save-name').inputValue(),'Mountain study (2)');
  await page.locator('#cancel-save').click();assert(await tab('Mountain study *').isVisible());
  await namedSave('#save-copy');const second=(await list())[0];assert.equal(second.title,'Mountain study (2)');assert.notEqual(second.id,first.id);
  assert.equal((await page.request.get(base+'/api/recipes/'+first.id)).status(),200);
  await page.locator('#azimuth').fill('43');await page.locator('#azimuth').dispatchEvent('input');await draft();
  await page.locator('#new-document').click();await draft();assert.equal(await page.getByRole('tab').count(),2);
  await page.locator('#open-library').click();await page.locator('.document-card img').first().waitFor();
  assert.equal(await page.locator('.document-card').count(),2);assert.equal(await page.locator('.document-card').first().getAttribute('data-document'),second.id);
  await page.locator('#document-search').fill('(2)');assert.equal(await page.locator('.document-card').count(),1);await page.locator('#document-search').fill('');
  await page.screenshot({path:out+'/open-documents.png',fullPage:true});
  await page.locator('.document-card[data-document="'+first.id+'"] button').click();await page.locator('#library').waitFor({state:'hidden'});await draft();assert.equal(await page.getByRole('tab').count(),3);
  const beforeSwitch=previews;await tab('Mountain study (2) *').click();await draft();assert.equal(await page.locator('#azimuth').inputValue(),'43');assert.equal(previews,beforeSwitch);
  await page.getByRole('button',{name:'Close Mountain study (2)',exact:true}).click();await page.locator('#close-document-dialog').waitFor();await page.locator('#close-document-dialog button[value=cancel]').click();assert.equal(await page.getByRole('tab').count(),3);
  await page.getByRole('button',{name:'Close Mountain study (2)',exact:true}).click();await page.locator('#close-document-dialog button[value=discard]').click();await page.waitForFunction(()=>document.querySelectorAll('[role=tab]').length===2);
  await open(second.id);assert.notEqual(await page.locator('#azimuth').inputValue(),'43');
  // An in-flight save must update the document that requested it, even after
  // switching tabs, and must leave newer edits dirty.
  await tab('Mountain study').click();await draft();await edit('latitude',47.7);await draft();
  holdSave=true;await page.locator('#save').click();for(let i=0;i<100&&!releaseSave;i++)await sleep(20);assert(releaseSave);
  assert(await page.locator('#revert').isDisabled(),'Revert cannot race an in-flight save');
  await edit('latitude',47.8);await tab('Mountain study (2)').click();const secondBefore=await current();releaseSave();releaseSave=null;
  await sleep(250);assert.deepEqual((await current()).recipe,secondBefore.recipe);
  assert(await tab('Mountain study *').isVisible());await tab('Mountain study *').click();assert.equal(await page.locator('#latitude').inputValue(),'47.8');
  const onDisk=await(await page.request.get(base+'/api/recipes/'+first.id)).json();assert.equal(onDisk.recipe.center[1],47.7);
  // Optimistic saving prevents a different browser window from being overwritten.
  await page.request.post(base+'/api/recipes',{data:{recipe:onDisk.recipe,id:first.id,expected_revision:onDisk.revision,name:onDisk.title}});
  await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('another window'));
  assert.equal(await page.locator('#latitude').inputValue(),'47.8');assert(await tab('Mountain study *').isVisible());
  await namedSave('#save-copy');const third=(await list())[0];assert.equal(third.title,'Mountain study (3)');
  await edit('latitude',47.9);await page.getByRole('button',{name:'Close Mountain study (3)',exact:true}).click();await page.locator('#close-document-dialog button[value=save]').click();
  await page.waitForFunction(()=>!document.querySelector('#close-document-dialog').open);await sleep(150);
  assert.equal((await(await page.request.get(base+'/api/recipes/'+third.id)).json()).recipe.center[1],47.9);
  assert.equal(await page.getByRole('tab',{name:/Mountain study \(3\)/}).count(),0);
  await page.reload();await draft();assert.equal(await page.getByRole('tab').count(),2);
  // Saving is immediate even before a preview exists; its thumbnail arrives
  // later and stays bound to the exact saved revision.
  holdPreview=true;await page.locator('#new-document').click();for(let i=0;i<100&&!releasePreview;i++)await sleep(20);assert(releasePreview);
  await namedSave('#save','Early save');const early=(await list())[0];assert.equal(early.thumbnail_url,null);
  releasePreview();await draft();
  for(let i=0;i<100&&!(await list()).find(r=>r.id===early.id).thumbnail_url;i++)await sleep(20);
  assert((await list()).find(r=>r.id===early.id).thumbnail_url);
  await page.screenshot({path:out+'/workspace.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.locator('#open-library').click();await page.screenshot({path:out+'/mobile-library.png',fullPage:true});
  const legacyRecipe=(await(await page.request.get(base+'/api/catalog')).json()).recipe;legacyRecipe.title='Legacy map';
  legacyRecipe.palette.inks.push({...legacyRecipe.palette.inks[0],id:'unused-saved-ink'});
  const legacySave=await(await page.request.post(base+'/api/recipes',{data:{recipe:legacyRecipe}})).json();
  const edited=structuredClone(legacyRecipe);edited.center[1]=48.1;
  const legacy=await context.newPage();legacy.on('pageerror',error=>errors.push(error.message));
  await legacy.addInitScript(({recipe,saved})=>{localStorage.removeItem('plotline-map-workspace');sessionStorage.removeItem('plotline-map-workspace');localStorage.setItem('plotline-map-draft',JSON.stringify({recipe,saved}));},
    {recipe:edited,saved:{id:legacySave.id,revision:1,signature:JSON.stringify(legacyRecipe)}});
  await legacy.goto(base+'/#recipe='+legacySave.id);await legacy.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
  assert.equal(await legacy.locator('#latitude').inputValue(),'48.1');assert(await legacy.getByRole('tab',{name:'Legacy map *',exact:true}).isVisible());
  await legacy.locator('#revert').click();await legacy.waitForFunction(()=>document.querySelector('#saved-state').textContent==='Saved');
  assert.deepEqual(await legacy.evaluate(()=>JSON.parse(localStorage.getItem('plotline-map-draft')).recipe),legacyRecipe,'Revert preserves the entire saved recipe, including unused inks');
  assert.deepEqual(errors,[]);
  const result={passed:true,directory:out,previews,checks:['named Save updates in place','Save As creates numbered copies','durable thumbnails','late preview attaches thumbnail','recent searchable Open library','unsaved edits survive saved URL reload','Revert restores the last save without creating a revision','Undo recovers reverted edits','Revert preserves unused saved inks','legacy browser draft migration','independent document tabs','cached previews survive switching','dirty close cancel/discard/save','late save stays with original document','new edits stay dirty','concurrent browser save conflict','mobile fit']};
  fs.writeFileSync(out+'/results.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
