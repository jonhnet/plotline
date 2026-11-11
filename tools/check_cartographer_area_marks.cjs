const assert=require('node:assert/strict'),fs=require('node:fs'),crypto=require('node:crypto'),{chromium}=require('playwright');
const base=process.argv[2],out=process.argv[3];
(async()=>{
 const browser=await chromium.launch();try{
  const context=await browser.newContext({viewport:{width:1440,height:1100}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  const catalog=await(await page.request.get(base+'/api/catalog')).json();
  await context.route('**/api/capabilities',r=>r.fulfill({json:{offline:true,carousel:{revision:0,slots:{}},capabilities:{implemented:{},media_profiles:[],available_inks:[]}}}));
  await context.route('**/api/cancel',r=>r.fulfill({json:{status:'cancelled'}}));
  await context.route('**/api/preview',async r=>{
    const b=r.request().postDataJSON(),checked=await page.request.post(base+'/api/validate',{data:{recipe:b.recipe}});
    assert(checked.ok(),await checked.text());
    const key=crypto.createHash('sha256').update(JSON.stringify(b.recipe)).digest('hex');
    return r.fulfill({json:{id:key,cache_key:key,recipe_hash:key,session:b.session,revision:b.revision,quality:b.quality,status:'ready',artifacts:'/artifacts/'+key+'/',result:{seconds:.1,source:{zoom:12,tile_count:1},warnings:[],intent_available:true,feature_counts:{},label_count:0,omitted_labels:[],fetched_bounds:[0,0,1,1]}}});
  });
  await context.route('**/artifacts/*/preview.png',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"><rect width="900" height="600" fill="white"/></svg>'}));
  const current=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('plotline-map-draft')).recipe);
  const ready=()=>page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
  const choose=(id,value)=>page.locator('#'+id).selectOption(value);
  // Load an actual old-style saved record, including a different outside fill.
  const legacy=structuredClone(catalog.recipe);legacy.title='Texture controls';
  legacy.boundary={name:'Half',source:'Fixture',sha256:'a'.repeat(64),geometry:{type:'Polygon',coordinates:[[[-122.34,47.5],[-122.2,47.5],[-122.2,47.8],[-122.34,47.8],[-122.34,47.5]]]}};
  const g=legacy.boundary.geometry;
  legacy.boundary.sha256=crypto.createHash('sha256').update(JSON.stringify({coordinates:g.coordinates,type:g.type})).digest('hex');
  for(const [f,enabled] of [[legacy.features,true],[legacy.outside.features,false]]){
    delete f.water_treatment;delete f.water_spacing_mm;delete f.peak_labels;f.water_fill=enabled;
  }
  const response=await page.request.post(base+'/api/recipes',{data:{recipe:legacy}});assert(response.ok(),await response.text());
  const stored=await response.json();
  await page.goto(base+'/#recipe='+stored.id);await ready();
  assert.equal(await page.locator('#water-treatment').inputValue(),'fill');
  assert(!(await page.locator('#peak-labels').isChecked()));await page.locator('#peak-labels').check();
  assert.equal((await current()).features.labels,'normal');assert(!(await current()).camera.relief);
  assert(await page.locator('#water-spacing-field').isHidden());
  await choose('water-treatment','concentric');await choose('park-treatment','concentric');
  assert(await page.locator('#water-spacing-field').isVisible());
  await page.locator('#water-spacing').fill('3.5');await page.locator('#water-spacing').press('Tab');
  await choose('style-region','outside');assert(!(await page.locator('#peak-labels').isChecked()));assert.equal(await page.locator('#water-treatment').inputValue(),'none');
  await choose('water-treatment','hatch');await page.locator('#water-spacing').fill('5');await page.locator('#water-spacing').press('Tab');
  await choose('style-region','inside');assert(await page.locator('#peak-labels').isChecked());assert.equal(await page.locator('#water-treatment').inputValue(),'concentric');
  assert.equal(await page.locator('#water-spacing').inputValue(),'3.5');await ready();
  const authored=await current();
  for(const k of ['center','camera','media','framing','ink_roles'])assert.deepEqual(authored[k],legacy[k]);
  await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#saved-state').textContent==='Saved');
  await page.reload();await ready();assert.deepEqual((await current()).features,authored.features);assert.deepEqual((await current()).outside,authored.outside);
  assert.equal(await page.locator('#park-treatment').inputValue(),'concentric');
  assert(await page.locator('#peak-labels').isChecked());
  await choose('style-region','outside');await page.locator('#peak-labels').check();
  await choose('style-region','inside');await page.locator('#peak-labels').uncheck();
  assert((await current()).outside.features.peak_labels);
  await page.getByRole('button',{name:'Undo',exact:true}).click();assert(await page.locator('#peak-labels').isChecked());
  await choose('park-treatment','outline');assert(await page.locator('#park-spacing-field').isHidden());
  await choose('water-treatment','none');assert(await page.locator('#water-spacing-field').isHidden());
  await choose('water-treatment','concentric');await choose('park-treatment','concentric');await ready();
  await page.screenshot({path:out+'/texture-controls.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,directory:out,checks:['legacy opaque fill retained','concentric controls and spacing','independent outside style','peak labels: legacy migration, boundary, undo and persistence','save/reopen','camera and inks unchanged','mobile fit']}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
