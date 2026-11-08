#!/usr/bin/env node
/* One-action MapStudio → HPGL browser flow; all submissions intercepted. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{chromium}=require('playwright');
const base=process.argv[2]||'http://127.0.0.1:9193',key='a'.repeat(64),hash='b'.repeat(64),wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const browser=await chromium.launch();try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',dialog=>dialog.accept());
  const catalog=await(await page.request.get(base+'/api/catalog')).json();
  catalog.recipe.palette.inks[0].source={kind:'plotline-carousel',revision:1,slot:1};
  await page.route('**/api/catalog',r=>r.fulfill({json:catalog}));
  const unknownInk={id:'unknown-pink',label:'Pink',color_srgb:'#ff1493',width_mm:null,source:{kind:'plotline-carousel',revision:2,slot:4}};
  let offline=false,handoff=true,phase='rendering',preparations=0,retries=0,polls=0,proofs=0,delay=0,pinned=[],pageNumber=1;
  await page.route('**/api/capabilities',r=>r.fulfill({json:{offline,captured_at:'2026-11-01',capabilities:{implemented:{pdf_upload:true,pdf_handoff:handoff},available_inks:[unknownInk],inventory_revision:2,media_profiles:[]}}}));
  const receipt=()=>({id:key,recipe_hash:hash,phase,created_at:Date.now()/1000-20,
   ...(phase==='rendering'?{render:{progress:{overall_percent:65,detail:'Drawing forest hatching'}}}:{export_key:key,review_url:'http://dev.example:9191/pdf?document=doc'}),
   progress:{phase:'visibility',label:'Resolving hidden lines',completed:37,total:100,unit:'paints',page_number:pageNumber,page_count:2},
   jobs:phase==='ready'?[{page_index:0,job_id:'one',url:'http://dev.example:9191/#one'},{page_index:1,job_id:'two',url:'http://dev.example:9191/#two'}]:[],
   error:phase==='failed'?'Conversion fixture needs retry':null,retryable:phase==='failed'});
  await page.route('**/api/preview',r=>{const body=r.request().postDataJSON(),ink=body.recipe.palette.inks.find(i=>i.id===body.recipe.ink_roles.water),unmeasured=ink.width_mm==null?[{id:ink.id,label:ink.label,roles:[{region:'inside',role:'water'}]}]:[];if(body.quality==='proof')proofs++;return r.fulfill({json:{id:'draft',cache_key:key,recipe_hash:hash,session:body.session,revision:body.revision,quality:body.quality,status:'ready',artifacts:'/artifacts/test/',result:{seconds:1,source:{zoom:12,tile_count:1},fetched_bounds:[-123,47,-122,48],feature_counts:{},label_count:0,omitted_labels:[],warnings:[],intent_available:!unmeasured.length,unmeasured_inks:unmeasured},progress:{stage:'complete'}}});});
  await page.route('**/artifacts/test/preview.png',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'}));
  await page.route('**/api/scenes/draft',r=>r.fulfill({json:{lines:[],origin:[0,0],terrain:null}}));
  await page.route('**/api/cancel',r=>r.fulfill({json:{status:'cancelled'}}));
  await page.route('**/api/prepare',r=>{const body=r.request().postDataJSON();preparations++;pinned.push(body.recipe);if(body.retry){retries++;phase='converting';}return r.fulfill({json:receipt()});});
  await page.route('**/api/preparations/'+key,async r=>{polls++;if(delay)await wait(delay);await r.fulfill({json:receipt()}).catch(()=>{});});
  await page.context().route('http://dev.example:9191/**',r=>r.fulfill({contentType:'text/html',body:'<h1>HPGL Studio fixture</h1>'}));
  const printed=label=>page.waitForFunction(t=>document.querySelector('#work-status').dataset.phase===t,label);
  const draft=()=>page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
  const studio=async()=>{
    const original=page.url(),popupPromise=page.context().waitForEvent('page');await page.locator('#send').click();
    const popup=await popupPromise;await popup.waitForURL('http://dev.example:9191/#one');
    assert.equal(page.url(),original,'Map Studio must keep its workspace');assert.equal(await popup.evaluate(()=>window.opener),null);
    await popup.close();
  };
  await page.goto(base);await draft();await page.waitForFunction(()=>!document.querySelector('#send').disabled);
  const previewBounds=await page.locator('.preview-area').boundingBox(),progressBounds=await page.locator('#work-status').boundingBox();
  const stableLayout=async()=>{assert.deepEqual(await page.locator('.preview-area').boundingBox(),previewBounds);assert.deepEqual(await page.locator('#work-status').boundingBox(),progressBounds);};
  assert.equal(await page.locator('#send').textContent(),'Prepare plot →');
  assert(await page.locator('#generate').isHidden());assert(await page.locator('#pdf-settings').isHidden());
  await page.locator('#send').click();await printed('rendering');
  assert.equal(preparations,1);assert.equal(proofs,0);assert.deepEqual(pinned[0],catalog.recipe);
  assert(await page.locator('#send').isDisabled());assert.equal(await page.locator('progress:visible').count(),1);
  await page.waitForFunction(()=>document.querySelector('#work-progress').getAttribute('value')==='32.5');
  await stableLayout();
  phase='converting';await printed('converting');assert.match(await page.locator('#work-detail').textContent(),/Resolving hidden lines/);
  assert.match(await page.locator('#work-count').textContent(),/37 \/ 100 paints/);assert.match(await page.locator('#work-detail').textContent(),/page 1 of 2/);
  const firstPage=Number(await page.locator('#work-progress').getAttribute('value'));assert(firstPage>60&&firstPage<80);
  pageNumber=2;await page.waitForFunction(()=>document.querySelector('#work-detail').textContent.includes('page 2 of 2'));
  assert(Number(await page.locator('#work-progress').getAttribute('value'))>firstPage);
  await stableLayout();
  await page.reload();await printed('converting');assert.equal(preparations,1,'Reload must resume, not create another preparation');
  await page.screenshot({path:'build/one-action-progress.png',fullPage:true});
  phase='ready';await printed('ready');assert.equal(proofs,0,'No manual PDF/refinement prerequisite');
  await stableLayout();assert.equal(await page.locator('progress:visible').count(),1);assert.equal(await page.locator('#work-detail').textContent(),'Plot ready');
  assert(page.url().startsWith(base),'Completing preparation must preserve the map workspace');
  assert.equal(await page.locator('#send').textContent(),'Open plot ↗');
  await page.locator('#export-tools summary').click();
  await stableLayout();
  assert.equal(await page.locator('#pdf').getAttribute('href'),'/artifacts/'+key+'/map.pdf');
  assert.equal(await page.locator('#pdf-settings').getAttribute('aria-disabled'),'false');
  await studio();assert.equal(preparations,1,'Opening an existing plot must not re-render');
  await page.reload();await printed('ready');await draft();
  await page.locator('#title').fill('Another map');await page.locator('#title').press('Tab');await draft();
  phase='failed';await page.locator('#send').click();await printed('failed');
  assert.equal(await page.locator('#send').textContent(),'Retry preparation →');
  await page.reload();await printed('failed');await page.locator('#send').click();await printed('converting');
  assert.equal(retries,1);assert.equal(proofs,0);assert.equal(preparations,3);
  delay=1200;await page.locator('#title').fill('Newer artwork');await page.locator('#title').press('Tab');phase='ready';await wait(1800);
  assert(page.url().startsWith(base),'Older preparation must not navigate away from newer artwork');
  assert.equal(await page.locator('#work-status').getAttribute('data-owner'),'preview');
  await page.locator('#export-tools').evaluate(el=>el.open=true);
  for(const id of ['pdf','bundle','pdf-settings','open-hp7585'])assert.equal(await page.locator('#'+id).getAttribute('href'),null);
  for(const d of await page.locator('aside details').all())await d.evaluate(el=>el.open=true);
  await page.locator('#edit-inks').click();offline=true;await page.locator('#refresh-inks').click();await page.waitForFunction(()=>document.querySelector('#export-status').textContent.includes('offline'));
  assert(await page.locator('#send').isDisabled());offline=false;handoff=false;await page.locator('#refresh-inks').click();
  await page.waitForFunction(()=>document.querySelector('#export-status').textContent.includes('does not support'));
  handoff=true;await page.locator('#refresh-inks').click();
  await page.locator('#palette .ink-role').filter({hasText:/^Water$/}).click();
  await page.locator('#palette .ink-loaded .pen-cap[data-slot="4"]').click();await draft();
  assert(await page.locator('#send').isDisabled());
  assert.match(await page.locator('#export-status').textContent(),/Ink diameter missing: Pink \(Water\)/);
  assert.equal(preparations,3);assert.equal(proofs,0);
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:'build/one-action-mobile.png',fullPage:true});assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,preparations,retries,polls,proofs,checks:['one preparation action from draft','HPGL opens in a new tab and preserves map context','combined progress','reload resumes','ready plot reopens without work','same-button retry','optional PDF tools','newer artwork protected','offline and unknown width','mobile']}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
