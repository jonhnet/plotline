const assert=require('node:assert/strict'),fs=require('node:fs'),crypto=require('node:crypto'),{chromium}=require('playwright');
const base=process.argv[2],out=process.argv[3];
(async()=>{
 const browser=await chromium.launch();try{
  const context=await browser.newContext({viewport:{width:1440,height:1100}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  const catalog=await(await page.request.get(base+'/api/catalog')).json();
  let profiles=[],offline=false;
  const carousel={revision:8,slots:{}};
  await context.route('**/api/capabilities',r=>r.fulfill({json:{offline,carousel,capabilities:{implemented:{pdf_upload:true,pdf_handoff:true},inventory_revision:8,media_profiles:profiles,available_inks:[]}}}));
  await context.route('**/api/cancel',r=>r.fulfill({json:{status:'cancelled'}}));
  await context.route('**/api/preview',r=>{
    const b=r.request().postDataJSON(),key=crypto.createHash('sha256').update(JSON.stringify(b.recipe)).digest('hex');
    return r.fulfill({json:{id:key,cache_key:key,recipe_hash:key,session:b.session,revision:b.revision,quality:b.quality,status:'ready',artifacts:'/artifacts/'+key+'/',result:{seconds:.1,source:{zoom:12,tile_count:1},warnings:[],intent_available:true,feature_counts:{},label_count:0,omitted_labels:[],fetched_bounds:[0,0,1,1]}}});
  });
  await context.route('**/artifacts/*/preview.png',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"><rect width="900" height="600" fill="white"/></svg>'}));
  const current=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('plotline-map-draft')).recipe);
  const ready=()=>page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
  const choose=async(id,value)=>{
    if(id==='paper-unit'&&!await page.locator('.studio-preferences').evaluate(e=>e.open))await page.locator('.studio-preferences > summary').click();
    return page.locator('#'+id).selectOption(value);
  };
  await page.goto(base);await ready();
  assert.equal(await page.locator('#paper-size').inputValue(),'tabloid');
  assert.equal(await page.locator('#paper-orientation').inputValue(),'landscape');
  assert.equal(await page.locator('#paper-margins').inputValue(),'plotter');
  assert(await page.locator('#media-note').isHidden());
  assert.equal(await page.locator('#media-note').textContent(),'');
  assert.equal(await page.locator('#paper-orientation option').count(),4);
  assert.equal(await page.locator('#media').count(),0);
  assert.equal(await page.locator('#paper-unit').inputValue(),'in');
  // Pen spacing stays in millimetres while physical paper units change.
  await choose('water-treatment','hatch');await page.locator('#hillshade').check();
  const spacings=[['hatch-spacing','hatch_spacing_mm',.25],['water-spacing','water_spacing_mm',.3],['hillshade-spacing','hillshade_spacing_mm',.35]];
  for(const [id,key,mm] of spacings){
    assert.match(await page.locator('#'+id).locator('..').textContent(),/spacing · mm/);
    await page.locator('#'+id).fill(String(mm));await page.locator('#'+id).press('Tab');
    assert.equal((await current()).features[key],mm,'Editing in an inch-based workspace must retain millimetres');
  }
  const dense=await current();
  const validation=await page.request.post(base+'/api/validate',{data:{recipe:dense}});
  assert(validation.ok(),await validation.text());
  for(const unit of ['mm','in']){
    await choose('paper-unit',unit);
    for(const [id,,mm] of spacings)assert.equal(await page.locator('#'+id).inputValue(),String(mm));
    assert.deepEqual(await current(),dense);
  }
  const displayOnly=await current();
  await choose('paper-unit','mm');
  assert.deepEqual(await current(),displayOnly,'Unit display does not alter the map');
  const before=await current();
  await choose('paper-margins','custom');
  assert.equal(await page.locator('#paper-margins').inputValue(),'custom');
  assert.deepEqual((await current()).media.calibration,before.media.calibration,'Inward custom margins retain recorded registration');
  await choose('paper-margins','plotter');
  await choose('paper-orientation','portrait');
  let r=await current();assert.deepEqual(r.media.sheet_size_mm,[279.4,431.8]);
  assert.deepEqual(r.media.printable_rect_mm,[14.8,39.425,253.6,376.95]);
  for(const key of ['center','camera','framing','ink_roles','features'])assert.deepEqual(r[key],before[key]);
  assert(!r.media.calibration);await ready();
  assert(await page.locator('#paper').evaluate(e=>e.clientHeight>e.clientWidth));
  // All named paper choices have valid server-side recipes in either orientation.
  for(const p of catalog.paper_sizes){
    await choose('paper-size',p.id);
    for(const [angle,orientation] of ['landscape','portrait','landscape-reverse','portrait-reverse'].entries()){
      await choose('paper-orientation',orientation);r=await current();
      const checked=await page.request.post(base+'/api/validate',{data:{recipe:r}});
      assert(checked.ok(),await checked.text());
      assert.deepEqual(r.media.sheet_size_mm,orientation.startsWith('portrait')?p.size_mm:[...p.size_mm].reverse());
      assert.equal(r.media.orientation_deg,angle*90);
      const [w,h]=r.media.sheet_size_mm,[x,y,rw,rh]=r.media.printable_rect_mm;
      const margins=[x,y,w-x-rw,h-y-rh];
      assert.equal(margins.indexOf(Math.max(...margins)),angle,'Wide margin follows the chosen edge');
      for(const key of ['center','camera','framing','ink_roles','features'])assert.deepEqual(r[key],before[key]);
      assert(await page.locator('#media-note').isHidden());
    }
  }
  await choose('paper-size','tabloid');await choose('paper-orientation','portrait');
  // Live discovery refreshes choices, not artwork. An explicit margin refresh applies them.
  const original=await current();
  const updated=structuredClone(original.media);updated.revision=2;updated.printable_rect_mm=[16,40,251.4,375.8];
  profiles=[updated];
  await page.locator('#edit-inks').click();await page.locator('#refresh-inks').click();await page.locator('#close-inks').click();
  await page.waitForFunction(()=>document.querySelector('#inventory-status').textContent.includes('revision 8'));
  assert.deepEqual((await current()).media,original.media);
  await page.locator('#refresh-paper').click();
  await page.waitForFunction(()=>!document.querySelector('#refresh-paper').disabled);
  assert.deepEqual((await current()).media,updated);
  offline=true;await page.locator('#refresh-paper').click();await page.waitForFunction(()=>!document.querySelector('#refresh-paper').disabled);
  assert.deepEqual((await current()).media,updated);assert.match(await page.locator('#notice').textContent(),/saved margins are unchanged/);offline=false;
  await choose('paper-orientation','portrait-reverse');
  const reversed=await current();assert.equal(reversed.media.orientation_deg,270);
  await page.locator('#refresh-paper').click();await page.waitForFunction(()=>!document.querySelector('#refresh-paper').disabled);
  assert.deepEqual((await current()).media,reversed.media,'Refresh retains the reverse orientation');
  await choose('paper-margins','custom');
  await page.locator('#margin-top').fill('45');await page.locator('#margin-top').press('Tab');
  assert.equal((await current()).media.printable_rect_mm[1],45);
  const custom=await current();await choose('paper-orientation','landscape');
  assert.equal(await page.locator('#paper-margins').inputValue(),'custom');
  assert.equal((await current()).media.printable_rect_mm[1],45);
  await choose('paper-orientation','portrait-reverse');
  // Reject an unusable frame without dirtying or replacing the valid snapshot.
  const valid=await current();await page.locator('#margin-left').fill('900');await page.locator('#margin-left').press('Tab');
  assert.deepEqual(await current(),valid);assert.match(await page.locator('#notice').textContent(),/50 mm/);
  await page.locator('#save').click();await page.locator('#save-name').fill('Portrait paper');await page.locator('#save-form button[type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('#saved-state').textContent==='Saved');
  const saved=await current();await page.reload();await ready();assert.deepEqual((await current()).media,saved.media);
  assert.equal(await page.locator('#paper-margins').inputValue(),'custom');
  assert.equal(await page.locator('#paper-orientation').inputValue(),'portrait-reverse');
  // Legacy authoring margins are preserved and have a direct plotter replacement.
  const legacy=structuredClone(catalog.recipe);legacy.media=catalog.media.find(m=>m.id==='tabloid-portrait-authoring');legacy.title='Legacy portrait';
  const stored=await(await page.request.post(base+'/api/recipes',{data:{recipe:legacy}})).json();
  await page.goto(base+'/#recipe='+stored.id);await page.reload();await ready();
  assert.deepEqual((await current()).media,legacy.media);assert.equal(await page.locator('#paper-size').inputValue(),'tabloid');
  assert.equal(await page.locator('#paper-orientation').inputValue(),'portrait');assert.equal(await page.locator('#paper-margins').inputValue(),'custom');
  await choose('paper-margins','plotter');assert.deepEqual((await current()).media,updated);
  await ready();await page.screenshot({path:out+'/paper-portrait.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:out+'/paper-mobile.png',fullPage:true});assert.deepEqual(errors,[]);
  const result={passed:true,checks:['dense pen spacing stays in mm across paper unit changes','all four orientations place the wide margin correctly','all 68 named paper/orientation combinations validate','numeric margin summary removed','camera/scale/center retained','refresh and save/reload retain reverse orientation','offline snapshot preserved','custom margins and invalid edit rollback','legacy portrait migration without rewriting save','mobile fit'],directory:out};
  fs.writeFileSync(out+'/results.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
