const assert=require('node:assert/strict'),crypto=require('node:crypto'),{chromium}=require('playwright');
const base=process.argv[2],out=process.argv[3];
(async()=>{
 const browser=await chromium.launch({args:['--host-resolver-rules=MAP ink-map.test 127.0.0.1','--no-proxy-server']});try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];let writes=0,previews=0;
  context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  const carousel={revision:8,slots:Object.fromEntries(Array.from({length:8},(_,i)=>[i+1,{state:'empty',color:null,width_mm:null,label:'',speed_cm_s:5,force:4}]))};
  for(const [n,color,width,label] of [[1,'#000000',.5,'Black'],[2,'#ff0000',.3,'Red'],[5,'#008000',.5,'Green'],[7,'#0000ff',.5,'Blue']])carousel.slots[n]={state:'loaded',color,width_mm:width,label,speed_cm_s:5,force:4};
  carousel.slots[3].state='unavailable';carousel.slots[4]={...carousel.slots[7],label:'Pink',color:'#ff1493',width_mm:null};
  await context.route('**/api/capabilities',r=>r.fulfill({json:{offline:false,captured_at:'2026-11-01',carousel,capabilities:{implemented:{pdf_upload:true,pdf_handoff:true,pen_workflow:true},inventory_revision:carousel.revision,media_profiles:[],available_inks:Object.entries(carousel.slots).filter(([,p])=>p.state==='loaded').map(([slot,p])=>({id:'loaded-'+slot,label:p.label,color_srgb:p.color,width_mm:p.width_mm,source:{kind:'plotline-carousel',revision:carousel.revision,slot:Number(slot)}}))}}}));
  await context.route('**/api/carousel',r=>{writes++;return r.abort();});
  await context.route('**/api/cancel',r=>r.fulfill({json:{status:'cancelled'}}));
  // This small map has water, but no buildings. The real compositor tests
  // separately audit which inks the PDF emits, including boundary regions.
  await context.route('**/api/preview',r=>{previews++;
    const b=r.request().postDataJSON(),key=crypto.createHash('sha256').update(JSON.stringify(b.recipe)).digest('hex');
    const ink=b.recipe.palette.inks.find(i=>i.id===b.recipe.ink_roles.water);
    const unmeasured=ink.width_mm==null?[{id:ink.id,label:ink.label,roles:[{region:'inside',role:'water'}]}]:[];
    return r.fulfill({json:{id:key,cache_key:key,recipe_hash:key,session:b.session,revision:b.revision,quality:b.quality,status:'ready',artifacts:'/artifacts/'+key+'/',result:{seconds:.1,source:{zoom:12,tile_count:1},warnings:[],intent_available:!unmeasured.length,unmeasured_inks:unmeasured,feature_counts:{water:1},label_count:0,omitted_labels:[],fetched_bounds:[0,0,1,1]}}});
  });
  await context.route('**/artifacts/*/preview.png',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"><rect width="900" height="600" fill="white"/><path d="M40 40L800 500" stroke="green"/></svg>'}));
  const current=p=>p.evaluate(()=>JSON.parse(localStorage.getItem('plotline-map-draft')).recipe);
  const web=base.replace('127.0.0.1','ink-map.test');
  await page.goto(web);await page.waitForFunction(()=>document.querySelector('#inventory-status').textContent.includes('revision 8'));
  assert.equal(await page.evaluate(()=>isSecureContext),false);assert.equal(await page.evaluate(()=>typeof crypto.randomUUID),'undefined');
  assert.equal(await page.locator('#ink-drawer').isVisible(),false);
  await page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
  await page.locator('#desk').hover();await page.mouse.wheel(0,-200);
  await page.waitForFunction(()=>Number(document.querySelector('#desk').dataset.previewZoom)>100);
  const transform=await page.locator('#paper').evaluate(e=>e.style.transform),recipeBeforeDrawer=await current(page),previewsBeforeDrawer=previews;
  await page.locator('#edit-inks').click();
  const drawer=await page.locator('#ink-drawer').boundingBox(),desk=await page.locator('#desk').boundingBox();
  assert.equal(drawer.x,0);assert.equal(drawer.width,1440);assert(desk.height>250,'Preview remains usable above drawer');
  assert(await page.locator('#ink-warning').isVisible(),'Compact warning is discoverable without scrolling the tray');
  assert.equal(await page.locator('#palette .ink-role').count(),9);assert.equal(await page.locator('#roles,#unused-inks').count(),0);
  const before=await current(page),role=name=>page.locator('#palette .ink-role').filter({hasText:new RegExp('^'+name+'$')}),target=slot=>page.locator('#palette .ink-loaded .pen-cap[data-slot="'+slot+'"]');
  const assign=async(name,slot)=>{await role(name).click();assert.equal(await page.locator('dialog[open]').count(),0);await target(slot).click();assert.equal(await page.locator('.ink-scrim').count(),0);};
  await role('Water').click();await page.keyboard.press('Escape');assert.deepEqual(await current(page),before,'Escape cancels without mutations');
  assert(await page.locator('#ink-drawer').isVisible(),'First Escape cancels selection');
  await page.keyboard.press('Escape');assert.equal(await page.locator('#ink-drawer').isVisible(),false,'Second Escape closes drawer');
  assert.equal(await page.locator('#paper').evaluate(e=>e.style.transform),transform,'Closing preserves preview navigation');
  assert.deepEqual(await current(page),recipeBeforeDrawer,'Opening and closing do not edit the recipe');
  assert.equal(previews,previewsBeforeDrawer,'Opening, closing and selection do not request a render');
  await page.locator('#ink-warning').click();assert.equal(await page.locator('.ink-selected-source').count(),1,'Warning opens an affected role');
  await page.locator('#close-inks').click();assert.equal(await page.locator('#ink-drawer').isVisible(),false,'Close works while assigning');
  await page.locator('#edit-inks').click();
  const attention=page.locator('#ink-attention button').first(),attentionRole=await attention.getAttribute('data-role');
  await attention.click();assert.equal(await page.locator('.ink-selected-source [data-ink-id="'+attentionRole+'"]').count(),1);
  await page.keyboard.press('Escape');
  await page.locator('#palette .ink-scroll').evaluate(e=>e.scrollLeft=0);
  assert(await target(8).evaluate(e=>{const a=e.getBoundingClientRect(),b=e.closest('.ink-scroll').getBoundingClientRect();return a.left>=b.left&&a.right<=b.right;}),'All eight physical slots fit in the wide drawer');
  await page.screenshot({path:out+'/map-ink-drawer.png',fullPage:true});
  await page.setViewportSize({width:1366,height:768});
  assert(await target(8).evaluate(e=>{const a=e.getBoundingClientRect(),b=e.closest('.ink-drawer-body').getBoundingClientRect();return a.top>=b.top&&a.bottom<=b.bottom;}),'Loaded pens fit below dense role groups on a laptop');
  await page.screenshot({path:out+'/map-ink-drawer-laptop.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await role('Water').click();await page.locator('.masthead').click();assert.deepEqual(await current(page),before,'Scrim cancels without mutations');
  await role('Water').click();await page.getByRole('button',{name:'Create and assign a custom ink'}).click();
  await page.locator('.ink-inline-editor input[name=label]').fill('blue');await page.locator('.ink-inline-editor input[name=width_mm]').fill('.3');await page.getByRole('button',{name:'Apply ink',exact:true}).click();
  const thinBlue=(await current(page)).ink_roles.water;assert.equal((await current(page)).palette.inks.length,4);
  assert.equal(await role('Water').evaluate(e=>e.closest('.ink-pair').dataset.targetId),'ink:'+thinBlue);
  await role('Water').click();await page.locator('.ink-scrim path').waitFor();
  assert(await target(7).evaluate(e=>{const a=e.getBoundingClientRect(),b=e.closest('.ink-scroll').getBoundingClientRect();return a.left>=b.left&&a.right<=b.right;}),'Suggested loaded blue is already visible');await page.screenshot({path:out+'/map-selecting.png',fullPage:true});
  assert.equal(await target(3).isDisabled(),true,'Broken slots cannot be destinations');await target(7).click();
  const picked=await current(page);assert(!picked.palette.inks.some(i=>i.id===thinBlue),'Unused blue is deleted');assert.equal(picked.palette.inks.find(i=>i.id===picked.ink_roles.water).width_mm,.5);
  await assign('Labels',7);assert.equal((await current(page)).palette.inks.length,4,'Identical ink is reused');
  assert.equal((await current(page)).ink_roles.water,(await current(page)).ink_roles.labels);
  await page.reload();await page.locator('#edit-inks').click();await page.waitForFunction(()=>document.querySelector('#inventory-status').textContent.includes('revision 8'));
  assert(!(await current(page)).palette.inks.some(i=>i.id===thinBlue));
  // Custom creation is atomic with assignment, and authoring still exceeds 8 inks.
  for(const [i,name] of ['Labels','Roads','Bridges','Buildings','Parks','Water','Contours','Silhouettes','Hillshade'].entries()){
    await role(name).click();await page.getByRole('button',{name:'Create and assign a custom ink'}).click();
    await page.locator('.ink-inline-editor input[name=label]').fill('Custom '+i);await page.locator('.ink-inline-editor input[name=width_mm]').fill(String(.2+i*.01));await page.getByRole('button',{name:'Apply ink',exact:true}).click();
  }
  assert.equal((await current(page)).palette.inks.length,10);assert.equal((await page.request.post(base+'/api/validate',{data:{recipe:await current(page)}})).status(),200);
  // The outside style still references default black, but its empty column
  // must not appear among the currently displayed inside-role assignments.
  const hiddenBlack=page.locator('#palette .ink-pair[data-target-id="ink:black"]');
  assert.equal(await hiddenBlack.count(),0);assert.equal((await current(page)).outside.ink_roles.labels,'black');
  await role('Water').click();assert.equal(await hiddenBlack.count(),1,'Other document colors remain available during assignment');
  await page.keyboard.press('Escape');assert.equal(await hiddenBlack.count(),0);
  await page.reload();await page.locator('#edit-inks').click();await page.waitForFunction(()=>document.querySelector('#inventory-status').textContent.includes('revision 8'));
  assert.equal(await hiddenBlack.count(),0,'Hidden outside-only ink stays out of the tray after reload');
  const documentBeforeRefresh=await current(page);carousel.revision=9;carousel.slots[1].width_mm=.7;
  await page.locator('#refresh-inks').click();await page.waitForFunction(()=>document.querySelector('#inventory-status').textContent.includes('revision 9'));
  assert.deepEqual((await current(page)).palette,documentBeforeRefresh.palette);
  const draftReady=()=>page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
  await assign('Buildings',4);await draftReady();assert.equal(await page.locator('#send').isDisabled(),false,'Unused pink Buildings ink must not block plotting');
  // Full detail may contain a feature omitted in the draft. Its readiness
  // result must also identify the role, without requiring a fresh draft.
  await context.route('**/api/prepare',r=>{
    const recipe=r.request().postDataJSON().recipe,ink=recipe.palette.inks.find(i=>i.id===recipe.ink_roles.buildings);
    return r.fulfill({json:{id:'a'.repeat(64),recipe_hash:'b'.repeat(64),phase:'failed',error:'Ink diameter missing: Pink (inside buildings)',render:{status:'ready',result:{unmeasured_inks:[{id:ink.id,label:ink.label,roles:[{region:'inside',role:'buildings'}]}]}}}});
  });
  await page.locator('#send').click();await page.waitForFunction(()=>document.querySelector('#export-status').textContent.includes('Pink (Buildings)'));
  await page.locator('#review-inks').click();assert.equal(await page.locator('.ink-selected-source [data-ink-id="buildings"]').count(),1);
  await target(5).click();await draftReady();assert.equal(await page.locator('#send').isDisabled(),false);
  await assign('Water',4);await draftReady();assert.equal(await page.locator('#send').isDisabled(),true);
  assert.match(await page.locator('#export-status').textContent(),/Pink \(Water\)/);
  assert(!/Open the prepared plot/.test(await page.locator('#export-detail').textContent()));
  carousel.slots[4].width_mm=.7;carousel.revision=10;
  await page.getByRole('button',{name:'Refresh carousel',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#inventory-status').textContent.includes('revision 10'));
  assert.equal(await page.locator('#send').isDisabled(),true,'Refreshing physical inventory does not rewrite document roles');
  await page.locator('#review-inks').click();assert.equal(await page.locator('.ink-selected-source [data-ink-id="water"]').count(),1);
  await target(4).click();await draftReady();assert.equal(await page.locator('#send').isDisabled(),false);
  const measured=await current(page);assert.equal(measured.palette.inks.find(i=>i.id===measured.ink_roles.water)?.width_mm,.7);
  await page.locator('#boundary-file').setInputFiles({name:'half.geojson',mimeType:'application/geo+json',buffer:Buffer.from(JSON.stringify({type:'Polygon',coordinates:[[[-122.4,47.5],[-122.3,47.5],[-122.3,47.7],[-122.4,47.7],[-122.4,47.5]]]}))});
  await page.locator('#ink-region').waitFor({state:'visible'});
  const boundaryBeforeIssue=await current(page);
  await page.locator('#ink-attention button[data-region="outside"][data-role="water"]').click();
  assert.equal(await page.locator('#ink-region').inputValue(),'outside');
  assert.equal(await page.locator('.ink-selected-source [data-ink-id="water"]').count(),1,'Attention switches to the affected boundary region');
  assert.deepEqual(await current(page),boundaryBeforeIssue,'Reviewing an outside issue does not edit the map');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#style-region').inputValue(),'outside');await assign('Water',7);
  const outsideEdit=await current(page);assert.notEqual(outsideEdit.ink_roles.water,outsideEdit.outside.ink_roles.water);
  await page.locator('#style-region').selectOption('inside');assert.equal(await page.locator('#ink-region').inputValue(),'inside');
  await page.screenshot({path:out+'/map-pens.png',fullPage:true});
  // Reload fetches new calibration measurements without silently changing the
  // artwork. Explicit adoption must invalidate the ready plot and saved draft,
  // update both boundary regions, and pass the new widths/colors downstream.
  await assign('Water',7);await assign('Parks',5);await assign('Buildings',4);await assign('Labels',1);await draftReady();
  // A legacy default red without source provenance, as in the saved Fremont
  // map. Its old black remains bound to slot 1 before that pen moves to 8.
  await page.evaluate(()=>{
    const state=JSON.parse(sessionStorage.getItem('plotline-map-workspace')),doc=state.documents.find(d=>d.key===state.active);
    doc.recipe.palette.inks.push({id:'red',label:'Red',color_srgb:'#ff0000',width_mm:.141111});doc.recipe.ink_roles.bridges='red';
    doc.preview=null;doc.printPointer=null;
    for(const storage of [localStorage,sessionStorage])storage.setItem('plotline-map-workspace',JSON.stringify(state));
  });
  await page.reload();await page.locator('#edit-inks').click();await draftReady();
  const calibrationBefore=await current(page),prepared=new Map(),submitted=[];
  await context.route('**/api/prepare',r=>{
    const recipe=r.request().postDataJSON().recipe,id=crypto.createHash('sha256').update(JSON.stringify(recipe)).digest('hex');submitted.push(recipe);
    const result={id,recipe_hash:id,phase:'ready',jobs:[{job_id:id,url:'http://plot-fixture.test/#'+id}]};prepared.set(id,result);return r.fulfill({json:result});
  });
  await context.route('**/api/preparations/*',r=>r.fulfill({json:prepared.get(r.request().url().split('/').at(-1))}));
  await page.locator('#send').click();await page.waitForFunction(()=>document.querySelector('#send').textContent==='Open plot ↗');
  carousel.slots[7].measured_color='#539a8e';carousel.slots[7].width_mm=.658;
  carousel.slots[5].measured_color='#65b48f';carousel.slots[5].width_mm=.723;
  carousel.slots[2].measured_color='#dc9b8e';carousel.slots[2].width_mm=.492;
  carousel.slots[8]={...carousel.slots[1],width_mm:.555,measured_color:'#5c5a58'};carousel.slots[1].state='empty';carousel.slots[1].color=null;
  carousel.slots[4].state='empty';carousel.slots[4].color=null;carousel.revision=11;
  await page.reload();await page.locator('#edit-inks').click();await page.waitForFunction(()=>document.querySelector('#inventory-status').textContent.includes('revision 11'));
  await page.waitForFunction(()=>document.querySelector('#export-status').textContent==='Plot uses earlier ink measurements.');
  assert.deepEqual((await current(page)).palette,calibrationBefore.palette,'Reload alone preserves authored inks');
  assert.equal(submitted.length,1,'Reload must not submit a replacement plot');
  await page.getByRole('button',{name:'Update map inks from carousel',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#update-map-inks').textContent==='Update map inks from carousel');await draftReady();
  const calibrated=await current(page),inkFor=(r,role,outside=false)=>r.palette.inks.find(i=>i.id===(outside?r.outside:r).ink_roles[role]);
  assert.equal(inkFor(calibrated,'water').width_mm,.658);assert.equal(inkFor(calibrated,'water').color_srgb,'#0000ff','Design color remains schematic');
  assert.equal(inkFor(calibrated,'labels').source.slot,8,'Recover black after its old slot is emptied');
  assert.equal(inkFor(calibrated,'labels').color_srgb,'#000000');assert.equal(inkFor(calibrated,'labels').width_mm,.555);
  assert.equal(inkFor(calibrated,'bridges').source.slot,2,'Recover legacy default red without provenance');
  assert.equal(inkFor(calibrated,'bridges').color_srgb,'#ff0000');assert.equal(inkFor(calibrated,'bridges').width_mm,.492);
  assert.equal(inkFor(calibrated,'water',true).width_mm,.658,'Outside boundary ink also adopts its slot measurements');
  assert.equal(inkFor(calibrated,'parks').width_mm,.723);assert.equal(inkFor(calibrated,'parks').source.revision,11);
  assert.deepEqual(inkFor(calibrated,'roads'),inkFor(calibrationBefore,'roads'),'Custom ink is preserved');
  assert.deepEqual(inkFor(calibrated,'buildings'),inkFor(calibrationBefore,'buildings'),'Empty pink slot cannot erase its authored ink');
  assert.match(await page.locator('#inventory-status').textContent(),/Choose a pen.*Pink/);
  assert.equal(await page.locator('#send').textContent(),'Prepare plot →');assert.equal(await page.locator('#open-hp7585').getAttribute('aria-disabled'),'true');
  assert.equal(await page.locator('#update-map-inks').isDisabled(),true,'Already adopted measurements need no invalidation');
  assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('plotline-map-workspace')).documents.find(d=>d.key===JSON.parse(localStorage.getItem('plotline-map-workspace')).active).printPointer),null);
  await page.screenshot({path:out+'/map-calibration-adopted.png',fullPage:true});
  await page.locator('#send').click();await page.waitForFunction(()=>document.querySelector('#send').textContent==='Open plot ↗');
  assert.equal(submitted.length,2);assert.equal(inkFor(submitted[1],'water').width_mm,.658);
  assert.notEqual([...prepared.keys()][0],[...prepared.keys()][1],'Updated materials create a different preparation');
  // A prior calibration copied charcoal RGB into the map. The existing slot
  // binding wins even when another loaded pen now has that as its design color.
  await page.evaluate(()=>{
    const state=JSON.parse(sessionStorage.getItem('plotline-map-workspace')),doc=state.documents.find(d=>d.key===state.active);
    doc.recipe.palette.inks.find(i=>i.id===doc.recipe.ink_roles.labels).color_srgb='#5c5a58';
    doc.preview=null;doc.printPointer=null;
    for(const storage of [localStorage,sessionStorage])storage.setItem('plotline-map-workspace',JSON.stringify(state));
  });
  carousel.slots[1]={...carousel.slots[8],color:'#5c5a58',width_mm:.9,measured_color:null};carousel.revision=12;
  await page.reload();await page.locator('#edit-inks').click();await page.waitForFunction(()=>document.querySelector('#inventory-status').textContent.includes('revision 12'));
  await page.locator('#update-map-inks').click();await draftReady();
  assert.equal(inkFor(await current(page),'labels').source.slot,8);assert.equal(inkFor(await current(page),'labels').color_srgb,'#000000');
  // An unbound legacy color with two real candidates needs an explicit choice.
  carousel.slots[3]={...carousel.slots[2],width_mm:.9};carousel.revision=13;
  await page.evaluate(()=>{
    const state=JSON.parse(sessionStorage.getItem('plotline-map-workspace')),doc=state.documents.find(d=>d.key===state.active),red=doc.recipe.palette.inks.find(i=>i.id==='red');
    delete red.source;red.width_mm=.141111;doc.preview=null;doc.printPointer=null;
    for(const storage of [localStorage,sessionStorage])storage.setItem('plotline-map-workspace',JSON.stringify(state));
  });
  await page.reload();await page.locator('#edit-inks').click();await page.waitForFunction(()=>document.querySelector('#inventory-status').textContent.includes('revision 13'));
  assert.match(await page.locator('#inventory-status').textContent(),/Choose a pen for.*Red/);
  assert.equal(inkFor(await current(page),'bridges').source,undefined);assert(await page.locator('#update-map-inks').isDisabled());
  // Rebuild an older submitted recipe without replacing newer unsaved work.
  const source=structuredClone(before);source.title='Submitted map';const hash='b'.repeat(64);
  await context.route('**/api/source-recipes/'+hash,r=>r.fulfill({json:source}));
  await page.locator('#title').fill('Unsaved working map');await page.locator('#title').press('Tab');const unsaved=await current(page);
  const changes={black:{color_srgb:'#000000',width_mm:.5,label:'Black'}},reopened=await context.newPage();
  await reopened.goto(web+'/?source_recipe='+hash+'&pen_substitutions='+encodeURIComponent(JSON.stringify(changes)));
  await reopened.waitForFunction(()=>document.querySelector('#notice').textContent.includes('substitutions applied'));
  assert.equal((await current(reopened)).palette.inks.find(i=>i.id==='black').width_mm,.5);
  assert.equal(await reopened.getByRole('tab').count(),2);assert(await reopened.getByRole('tab',{name:'Unsaved working map *',exact:true}).isVisible());
  await reopened.getByRole('tab',{name:'Unsaved working map *',exact:true}).click();assert.deepEqual(await current(reopened),unsaved);
  await reopened.setViewportSize({width:420,height:900});
  await reopened.locator('#edit-inks').click();
  assert(await reopened.locator('#ink-drawer').isVisible());
  await reopened.locator('#palette .ink-scroll-nav').waitFor({state:'visible'});
  assert((await reopened.locator('#desk').boundingBox()).height>120,'Mobile retains a usable preview');
  assert(await reopened.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Drawer does not overflow the mobile viewport');
  await reopened.locator('#ink-attention button').first().click();
  assert.equal(await reopened.locator('.ink-selected-source').count(),1);
  await reopened.locator('#close-inks').click();assert.equal(await reopened.locator('#ink-drawer').isVisible(),false);
  await reopened.locator('#edit-inks').click();await reopened.screenshot({path:out+'/map-pens-mobile.png',fullPage:true});
  // When every role uses a loaded material, warnings disappear rather than
  // leaving an empty attention panel. This also exercises nine roles on one ink.
  await reopened.evaluate(()=>{
    const state=JSON.parse(sessionStorage.getItem('plotline-map-workspace')),doc=state.documents.find(d=>d.key===state.active),id=doc.recipe.ink_roles.labels;
    for(const style of [doc.recipe,doc.recipe.outside])for(const role of Object.keys(style.ink_roles))style.ink_roles[role]=id;
    doc.preview=null;doc.printPointer=null;
    for(const storage of [localStorage,sessionStorage])storage.setItem('plotline-map-workspace',JSON.stringify(state));
  });
  await reopened.reload();await reopened.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
  assert.equal(await reopened.locator('#ink-warning').isVisible(),false);assert.equal(await reopened.locator('#review-inks').isVisible(),false);
  await reopened.locator('#edit-inks').click();assert.equal(await reopened.locator('#ink-attention').isVisible(),false);
  assert.equal(await reopened.locator('#palette .ink-role').count(),9);
  assert.equal(writes,0);assert.deepEqual(errors,[]);console.log('PASS responsive ink drawer, compact warnings and boundary-role navigation, preserved preview navigation, no renders from drawer actions, read-only carousel, shared feature picker, >8 document inks, unused blue removed, role mapping without dialogs, unused unknown ink permits plotting, emitted unknown ink identifies its role in draft and proof, measured pen refresh/reselection, explicit calibration adoption after reload invalidates draft/prepared plot across both boundary styles and preserves custom/missing inks, synchronized boundary region selectors, Escape/scrim cancellation on HTTP, document edits and source rebuild preserving unsaved work',out);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
