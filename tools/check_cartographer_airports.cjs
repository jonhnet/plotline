const assert=require('node:assert/strict'),crypto=require('node:crypto'),{chromium}=require('playwright');
const base=process.argv[2],out=process.argv[3];
(async()=>{
 const browser=await chromium.launch();try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  const catalog=await(await page.request.get(base+'/api/catalog')).json();
  await context.route('**/api/capabilities',r=>r.fulfill({json:{offline:true,carousel:{revision:0,slots:{}},capabilities:{implemented:{},media_profiles:[],available_inks:[]}}}));
  await context.route('**/api/cancel',r=>r.fulfill({json:{status:'cancelled'}}));
  await context.route('**/api/preview',async r=>{
    const b=r.request().postDataJSON(),checked=await page.request.post(base+'/api/validate',{data:{recipe:b.recipe}});
    assert(checked.ok(),await checked.text());
    const key=crypto.createHash('sha256').update(JSON.stringify(b.recipe)).digest('hex');
    return r.fulfill({json:{id:key,cache_key:key,recipe_hash:key,session:b.session,revision:b.revision,quality:b.quality,status:'ready',artifacts:'/artifacts/'+key+'/',result:{seconds:.1,source:{zoom:14,tile_count:1},warnings:[],intent_available:true,feature_counts:{},label_count:0,omitted_labels:[],fetched_bounds:[0,0,1,1]}}});
  });
  await context.route('**/artifacts/*/preview.png',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"><rect width="900" height="600" fill="white"/></svg>'}));
  const current=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('plotline-map-draft')).recipe);
  const ready=()=>page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
  const legacy=structuredClone(catalog.recipe);legacy.title='Airport controls';legacy.features.roads='none';
  const geometry={type:'Polygon',coordinates:[[[-122.34,47.5],[-122.2,47.5],[-122.2,47.8],[-122.34,47.8],[-122.34,47.5]]]};
  legacy.boundary={name:'Half',source:'Fixture',geometry,sha256:crypto.createHash('sha256').update(JSON.stringify({coordinates:geometry.coordinates,type:geometry.type})).digest('hex')};
  for(const style of [legacy,legacy.outside]){delete style.features.airports;delete style.ink_roles.airports;}
  const response=await page.request.post(base+'/api/recipes',{data:{recipe:legacy}});assert(response.ok(),await response.text());
  const stored=await response.json();await page.goto(base+'/#recipe='+stored.id);await ready();
  assert(await page.locator('#airports').isChecked());assert.equal(await page.locator('#roads').inputValue(),'none');
  const initial=await current();assert.equal(initial.ink_roles.airports,initial.ink_roles.roads);
  await page.locator('#airports').uncheck();assert(!(await current()).features.airports);
  await page.getByRole('button',{name:'Undo',exact:true}).click();assert(await page.locator('#airports').isChecked());
  await page.locator('#style-region').selectOption('outside');await page.locator('#airports').uncheck();
  await page.locator('#style-region').selectOption('inside');assert(await page.locator('#airports').isChecked());
  await page.locator('#edit-inks').click();
  const role=page.locator('#palette .ink-role').filter({hasText:/^Airports$/});assert.equal(await role.count(),1);
  await role.click();await page.keyboard.press('Escape');await page.locator('#close-inks').click();
  await ready();await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#saved-state').textContent==='Saved');
  const authored=await current();await page.reload();await ready();assert.deepEqual(await current(),authored);
  for(const key of ['center','camera','media','framing','palette'])assert.deepEqual(authored[key],legacy[key]);
  assert(authored.features.airports);assert(!authored.outside.features.airports);
  await page.screenshot({path:out+'/airport-controls.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks:['legacy airport defaults','independent of streets','boundary styles','undo','airport ink role','save/reload','mobile fit']}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
