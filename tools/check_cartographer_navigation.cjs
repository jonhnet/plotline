#!/usr/bin/env node
// Real studio UI, controlled render replies; no map providers or plot hardware.
const assert=require('node:assert/strict'),crypto=require('node:crypto'),{chromium}=require('playwright');
const base=process.argv[2],out=process.argv[3],sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const browser=await chromium.launch();try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>{errors.push(error.message);console.error(error.message);});page.on('dialog',dialog=>dialog.accept());
  let previews=0;
  await context.route('**/api/capabilities',r=>r.fulfill({json:{offline:false,capabilities:{implemented:{pdf_upload:true,pdf_handoff:true},available_inks:[],media_profiles:[],inventory_revision:1}}}));
  await context.route('**/api/cancel',r=>r.fulfill({json:{status:'cancelled'}}));
  await context.route('**/api/preview',r=>{
   previews++;const b=r.request().postDataJSON(),key=crypto.createHash('sha256').update(JSON.stringify(b.recipe)).digest('hex');
   return r.fulfill({json:{id:key,cache_key:key,recipe_hash:key,session:b.session,revision:b.revision,quality:b.quality,status:'ready',artifacts:'/artifacts/'+key+'/',progress:{stage:'complete'},result:{seconds:.1,source:{zoom:12,tile_count:1},fetched_bounds:[-123,47,-122,48],feature_counts:{roads:23},label_count:0,omitted_labels:[],warnings:[],intent_available:true}}});
  });
  await context.route('**/artifacts/*/preview.png',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"><rect width="900" height="600" fill="white"/><path d="M10 10L890 590M10 590L890 10" stroke="red" stroke-width="3"/></svg>'}));
  const ready=()=>page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready'&&!document.querySelector('#preview').hidden);
  const recipe=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('plotline-map-draft')).recipe);
  const zoom=async()=>parseFloat(await page.locator('#desk').getAttribute('data-preview-zoom'));
  const point=async()=>{const r=await page.locator('#paper').boundingBox();return [r.x+r.width*.45,r.y+r.height*.45];};
  const wheel=async(props={})=>{const [clientX,clientY]=await point();await page.locator('#desk').dispatchEvent('wheel',{clientX,clientY,deltaY:-90,...props});};
  await page.goto(base);await ready();const initial=await recipe(),initialCount=previews;
  const previewBounds=await page.locator('.preview-area').boundingBox(),header=await page.locator('.masthead').boundingBox(),tabs=await page.locator('.document-bar').boundingBox();
  assert(header.height<=64&&tabs.y>=header.y&&tabs.y+tabs.height<=header.y+header.height,'Tabs and file actions share one compact header');
  assert(await page.locator('#edit-state').isHidden());assert.equal(await page.locator('.desk-caption,#preview-zoom,#document-name').count(),0);
  for(const id of ['export-tools','composition-tools']){
    await page.locator('#'+id+' summary').click();assert(await page.locator('#'+id+' .tray-body').isVisible());
    assert.deepEqual(await page.locator('.preview-area').boundingBox(),previewBounds,'Opening tools does not resize the paper');
    await page.keyboard.press('Escape');assert(await page.locator('#'+id+' .tray-body').isHidden());
  }
  assert(await page.locator('#guides').isHidden());await page.locator('#show-guides').check();assert(await page.locator('#guides').isVisible());
  assert((await page.locator('#margin-guide').getAttribute('d')).includes('Z M'),'Margins contain a clear inner cutout');
  await page.screenshot({path:out+'/layout-margins.png'});await page.locator('#show-guides').uncheck();
  assert.deepEqual(await recipe(),initial);assert.equal(previews,initialCount);
  const dirtyBefore=await page.locator('#saved-state').textContent();
  await wheel();assert(await zoom()>100);const p=await point();
  await page.mouse.move(...p);await page.mouse.down();await page.mouse.move(p[0]+55,p[1]+35,{steps:5});await page.mouse.up();
  await wheel({ctrlKey:true});await sleep(1000);
  assert.deepEqual(await recipe(),initial);assert.equal(previews,initialCount);
  assert.equal(await page.locator('#saved-state').textContent(),dirtyBefore);
  await page.locator('#preview-fit').click();assert.equal(await zoom(),100);
  assert.equal(await page.locator('#paper').evaluate(el=>getComputedStyle(el).transform),'matrix(1, 0, 0, 1, 0, 0)');
  // A long drag must remain local, then commit exactly once.
  await page.keyboard.down('Shift');assert((await page.locator('#edit-state').textContent()).includes('Editing'));
  const a=await point();await page.mouse.move(...a);await page.mouse.down();await page.mouse.move(a[0]+60,a[1]+30,{steps:8});
  assert.notEqual(await page.locator('#preview').evaluate(el=>el.style.transform),'');
  await sleep(1000);assert.equal(previews,initialCount);assert.deepEqual(await recipe(),initial);
  await page.mouse.up();await page.keyboard.up('Shift');await sleep(400);await ready();
  assert.equal(previews,initialCount+1);const moved=await recipe();assert.notDeepEqual(moved.center,initial.center);
  assert.equal(moved.framing.scale_denominator,initial.framing.scale_denominator);
  await page.locator('#undo-map').click();await sleep(350);await ready();assert.deepEqual(await recipe(),initial);
  await page.locator('#redo-map').click();await sleep(350);await ready();assert.deepEqual(await recipe(),moved);
  const n=previews;
  for(let i=0;i<5;i++){await wheel({shiftKey:true});await sleep(40);}
  assert.equal(previews,n);await sleep(550);await ready();assert.equal(previews,n+1);
  assert((await recipe()).framing.scale_denominator<moved.framing.scale_denominator);
  await page.locator('#desk').focus();await page.keyboard.press('Control+z');await sleep(350);await ready();assert.deepEqual(await recipe(),moved);
  // The visible tool works without keyboard modifiers; Escape cancels edits.
  await page.locator('#edit-map').click();assert.equal(await page.locator('#edit-map').getAttribute('aria-pressed'),'true');
  const b=await point();await page.mouse.move(...b);await page.mouse.down();await page.mouse.move(b[0]-70,b[1],{steps:6});await page.keyboard.press('Escape');await page.mouse.up();
  await sleep(400);assert.deepEqual(await recipe(),moved);assert.equal(await page.locator('#edit-map').getAttribute('aria-pressed'),'false');
  await page.locator('#view-mode').selectOption('orbit');await page.locator('#edit-map').click();
  const c=await point();await page.mouse.move(...c);await page.mouse.down();await page.mouse.move(c[0]+40,c[1]);await page.mouse.up();await sleep(400);await ready();
  assert.notEqual((await recipe()).camera.azimuth_deg,moved.camera.azimuth_deg);
  await page.locator('#undo-map').click();await sleep(350);await ready();assert.deepEqual(await recipe(),moved);
  await page.locator('#edit-map').click();await page.locator('#preview-fit').click();
  // Genuine two-pointer events exercise touch inspection and explicit editing.
  const cdp=await context.newCDPSession(page),[x,y]=await point();
  const touch=async(type,points)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:points.map(([x,y,id])=>({x,y,id,radiusX:3,radiusY:3}))});
  await touch('touchStart',[[x-30,y,1],[x+30,y,2]]);await touch('touchMove',[[x-70,y,1],[x+70,y,2]]);await touch('touchEnd',[]);
  assert(await zoom()>150);assert.deepEqual(await recipe(),moved);
  await page.locator('#preview-fit').click();await page.locator('#view-mode').selectOption('ground');await page.locator('#edit-map').click();
  const d=await point();await touch('touchStart',[[...d,1]]);await touch('touchMove',[[d[0]+25,d[1]+10,1]]);await touch('touchEnd',[]);await sleep(400);await ready();
  assert.notDeepEqual((await recipe()).center,moved.center);
  await page.screenshot({path:out+'/navigation-edit.png'});
  await page.locator('#edit-map').click();await page.setViewportSize({width:700,height:900});
  const toolbar=await page.locator('.view-actions').boundingBox(),editButton=await page.locator('#edit-map').boundingBox();
  assert(editButton.y+editButton.height<=toolbar.y+toolbar.height,'Wrapped controls stay inside the toolbar');
  await page.screenshot({path:out+'/navigation-mobile.png'});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:out+'/layout-phone.png'});
  // Go to fits a search result, rather than retaining a neighborhood scale.
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('aside details').evaluateAll(rows=>rows.forEach(row=>row.open=true));
  const bounds=[-124.8,45.5,-116.9,49.1],rectangle=b=>({type:'Polygon',coordinates:[[[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]],[b[0],b[1]]]]});
  const response=await page.request.post(base+'/api/boundaries/import',{data:{geometry:rectangle(bounds),name:'Washington'}});
  assert(response.ok());const washington=await response.json();
  await page.locator('#boundary-file').setInputFiles({name:'Existing mask.geojson',mimeType:'application/geo+json',buffer:Buffer.from(JSON.stringify(rectangle([-122.4,47.5,-122.3,47.7])))});
  await page.waitForFunction(()=>document.querySelector('#boundary-status').textContent.includes('Existing mask'));
  let failLookup=false,holdLookup=false,releaseLookup,lookupStarted,holdFit=false,releaseFit,fitStarted;
  await context.route('**/api/boundaries/search',r=>r.fulfill({json:[{id:'53',kind:'state',name:'Washington',state:'WA',center:[-120,47]}]}));
  await context.route('**/api/boundaries/select',async r=>{
    if(holdLookup){lookupStarted=true;await new Promise(resolve=>releaseLookup=resolve);}
    return r.fulfill(failLookup?{status:502,json:{error:'Boundary provider unavailable'}}:{json:washington});
  });
  await context.route('**/api/fit',async r=>{const response=await r.fetch();if(holdFit){fitStarted=true;await new Promise(resolve=>releaseFit=resolve);}await r.fulfill({response});});
  await page.locator('#boundary-kind').selectOption('state');await page.locator('#boundary-query').fill('Washington');await page.locator('#boundary-search').click();
  const go=page.getByRole('button',{name:'Go to',exact:true});await go.waitFor();
  const fits=r=>{
    const rad=Math.PI/180,merc=([lon,lat])=>[6378137*lon*rad,6378137*Math.log(Math.tan(Math.PI/4+lat*rad/2))];
    const o=merc(r.center),[x,y,w,h]=r.media.printable_rect_mm,inset=r.media.content_inset_mm,scale=1000*Math.cos(r.center[1]*rad)/r.framing.scale_denominator;
    const a=r.camera.azimuth_deg*rad,se=Math.sin((r.camera.relief?r.camera.elevation_deg:90)*rad);
    for(const corner of rectangle(bounds).coordinates[0]){
      const v=merc(corner),dx=v[0]-o[0],dy=v[1]-o[1];
      const px=x+w/2+r.framing.offset_mm[0]+(Math.cos(a)*dx-Math.sin(a)*dy)*scale;
      const py=y+h/2+r.framing.offset_mm[1]-se*(Math.sin(a)*dx+Math.cos(a)*dy)*scale;
      assert(px>=x+inset+1.99&&px<=x+w-inset-1.99&&py>=y+inset+1.99&&py<=y+h-inset-1.99,'All extent corners fit inside the actual printable frame');
    }
  };
  for(const [orientation,azimuth,relief] of [['landscape',0,false],['portrait',0,false],['portrait',35,true]]){
    await page.locator('#paper-orientation').selectOption(orientation);
    await page.locator('#azimuth').evaluate((el,v)=>{el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}));},azimuth);
    await page.locator('#relief').setChecked(relief);await page.locator('#extent').selectOption('bounds');
    const before=await recipe();await go.click();await page.waitForFunction(()=>document.getElementById('extent').value==='viewport');await ready();
    const after=await recipe();assert(after.framing.scale_denominator>1000000);assert.deepEqual(after.bounds,bounds);fits(after);
    for(const key of ['boundary','media','camera','features','outside','palette','ink_roles','title'])assert.deepEqual(after[key],before[key],key+' is preserved');
    await page.locator('#undo-map').click();assert.deepEqual(await recipe(),before,'Go to is one undoable edit');
    await page.locator('#redo-map').click();assert.deepEqual(await recipe(),after);
    await page.locator('#undo-map').click();assert.deepEqual(await recipe(),before);
  }
  const beforeFailure=await recipe();failLookup=true;await go.click();await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('Boundary provider unavailable'));
  assert.deepEqual(await recipe(),beforeFailure);failLookup=false;
  // A later edit wins whether the boundary fetch or the fit response is delayed.
  for(const stage of ['lookup','fit']){
    holdLookup=stage==='lookup';holdFit=stage==='fit';lookupStarted=false;fitStarted=false;
    await go.click();for(let n=0;!(stage==='lookup'?lookupStarted:fitStarted);n++){assert(n<100);await sleep(20);}
    await page.locator('#title').fill('Edit during '+stage);await page.locator('#title').press('Tab');const edited=await recipe();
    if(stage==='lookup'){holdLookup=false;releaseLookup();}else{holdFit=false;releaseFit();}
    await page.waitForFunction(()=>[...document.querySelectorAll('#boundary-results button')].some(b=>b.textContent==='Go to'&&!b.disabled));
    assert.deepEqual(await recipe(),edited,'An obsolete '+stage+' response must not move the edited map');
  }
  assert.deepEqual(errors,[]);
  console.log('PASS Go to fits full extent on landscape, portrait and rotated relief views; keeps boundary/styles; undo/redo; failed and stale lookup/fit responses.');
  console.log('PASS map inspection preserves recipe/dirty state and makes no render requests; Shift and touch editing, wheel batching, one-step undo/redo, Escape, orbit and fit page.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
