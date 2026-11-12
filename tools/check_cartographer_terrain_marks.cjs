#!/usr/bin/env node
/* Control persistence/independence with deterministic preview responses. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{chromium}=require('playwright');
const base=process.argv[2]||'http://127.0.0.1:9193',out=process.argv[3]||'build/terrain-marks-review';
(async()=>{
 const browser=await chromium.launch();try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const catalog=await(await page.request.get(base+'/api/catalog')).json();
  const old=structuredClone(catalog.recipe);delete old.lighting;
  for(const f of [old.features,old.outside.features])for(const k of ['park_labels','hillshade','terrain_edges','hillshade_spacing_mm'])delete f[k];
  for(const roles of [old.ink_roles,old.outside.ink_roles])for(const k of ['silhouettes','hillshade'])delete roles[k];
  await page.addInitScript(r=>{if(!localStorage.getItem('plotline-map-draft'))localStorage.setItem('plotline-map-draft',JSON.stringify({recipe:r}));},old);
  await page.route('**/api/capabilities',r=>r.fulfill({json:{offline:true,carousel:{revision:0,slots:{}},capabilities:{implemented:{},media_profiles:[],available_inks:[]}}}));
  await page.route('**/api/preview',r=>{const b=r.request().postDataJSON();return r.fulfill({json:{id:'marks',cache_key:'a'.repeat(64),recipe_hash:'b'.repeat(64),revision:b.revision,quality:b.quality,status:'ready',artifacts:'/artifacts/marks/',result:{seconds:0,source:{zoom:12,tile_count:1},fetched_bounds:[-123,47,-122,48],feature_counts:{},label_count:0,omitted_labels:[],warnings:[],intent_available:true},progress:{stage:'complete'}}});});
  await page.route('**/artifacts/marks/preview.png',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'}));
  await page.route('**/api/scenes/marks',r=>r.fulfill({json:{lines:[],origin:[0,0],terrain:null}}));
  await page.route('**/api/cancel',r=>r.fulfill({json:{status:'cancelled'}}));
  const ready=()=>page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
  const recipe=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('plotline-map-draft')).recipe);
  await page.goto(base);await ready();
  for(const d of await page.locator('aside details').all())await d.evaluate(el=>el.open=true);
  assert(await page.locator('#park-labels').isChecked());assert(!(await page.locator('#hillshade').isChecked()));
  assert(await page.locator('#terrain-edges').isDisabled());assert(await page.locator('#terrain-edges').isChecked());
  await page.locator('#park-labels').uncheck();assert((await recipe()).features.parks);assert.equal((await recipe()).features.labels,'normal');
  await page.locator('#parks').uncheck();await page.locator('#hillshade').check();assert(await page.locator('#hillshade-controls').isVisible());
  for(const [id,value] of [['sun-azimuth',270],['sun-elevation',25]])await page.locator('#'+id).evaluate((el,v)=>{el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}));},value);
  await page.locator('#hillshade-spacing').fill('4');await page.locator('#hillshade-spacing').press('Tab');
  assert(!(await recipe()).camera.relief);assert((await recipe()).features.hillshade);
  await page.locator('#hillshade').uncheck();
  const flat=await recipe();
  await page.locator('#relief').check();assert(!(await page.locator('#terrain-edges').isDisabled()));
  assert(await page.locator('#terrain-edges').isChecked());assert(await page.locator('#hillshade').isChecked());
  assert((await recipe()).outside.features.hillshade);assert((await recipe()).outside.features.terrain_edges);
  const relief=await recipe();
  await page.locator('#undo-map').click();assert.deepEqual(await recipe(),flat,'3D defaults belong to the same undo step as the camera');
  await page.locator('#redo-map').click();assert.deepEqual(await recipe(),relief);
  // The two features remain ordinary independent choices, including after reload.
  await page.locator('#terrain-edges').uncheck();await page.locator('#hillshade').uncheck();
  await page.reload();await ready();
  assert((await recipe()).camera.relief);assert(!(await recipe()).features.hillshade);assert(!(await recipe()).features.terrain_edges);
  for(const d of await page.locator('aside details').all())await d.evaluate(el=>el.open=true);
  await page.locator('#terrain-edges').check();await page.locator('#hillshade').check();
  await page.locator('#relief').uncheck();assert((await recipe()).features.hillshade,'Flattening preserves the independent hillshade choice');
  await page.locator('#relief').check();
  const before=await recipe();await page.reload();await ready();assert.deepEqual(await recipe(),before);
  for(const d of await page.locator('aside details').all())await d.evaluate(el=>el.open=true);
  assert(await page.locator('#terrain-edges').isChecked());assert.equal(await page.locator('#sun-azimuth-value').textContent(),'270°');
  // Boundary styles remain independent while lighting is a shared sun.
  const boundary={type:'Polygon',coordinates:[[[-123.1,47.1],[-122.1,47.1],[-122.1,48.1],[-123.1,48.1],[-123.1,47.1]]]};
  const snapshot=await(await page.request.post(base+'/api/boundaries/import',{data:{geometry:boundary,name:'Test'}})).json();
  const bounded=await recipe();bounded.boundary=snapshot;
  await page.locator('#import-file').setInputFiles({name:'bounded.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(bounded))});
  await page.waitForFunction(()=>!document.querySelector('#style-region-field').hidden);
  for(const d of await page.locator('aside details').all())await d.evaluate(el=>el.open=true);
  await page.locator('#style-region').selectOption('outside');assert(await page.locator('#park-labels').isChecked());assert(await page.locator('#hillshade').isChecked());
  await page.locator('#hillshade').uncheck();assert.equal(await page.locator('#sun-elevation').inputValue(),'25');
  await page.locator('#style-region').selectOption('inside');assert(!(await page.locator('#park-labels').isChecked()));assert.equal((await recipe()).features.hillshade_spacing_mm,4);assert((await recipe()).features.hillshade);assert(!(await recipe()).outside.features.hillshade);
  await page.locator('#hillshade').uncheck();await page.locator('#apply-style').click();
  assert(await page.locator('#hillshade').isChecked(),'Style starting points use 3D defaults');
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.deepEqual(errors,[]);fs.mkdirSync(out,{recursive:true});await page.screenshot({path:out+'/controls-mobile.png',fullPage:true});
  console.log(JSON.stringify({passed:true,checks:['legacy draft migration','forest labels independent','hillshade without forest or 3D','sun controls','silhouette default','automatic 3D shading in both regions','atomic undo/redo','manual overrides survive reload','3D style starting points','reload','boundary style independence','mobile']}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
