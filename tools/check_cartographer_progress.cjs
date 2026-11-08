#!/usr/bin/env node
/* Controlled slow/error browser cases. Uses a running studio passed as argv[2].
   NODE_PATH=/root/plotline/controller/node_modules node tools/check_cartographer_progress.cjs http://127.0.0.1:19195 */
const assert=require('node:assert/strict'),fs=require('node:fs'),{chromium}=require('playwright');
const base=process.argv[2]||'http://127.0.0.1:9193',wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const browser=await chromium.launch();try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  let requests=0,polls=0,cancels=0,sceneRequested=false,mode='failure',quality='draft';
  const phases=['tiles','geometry','visibility','drawing','drawing','drawing','labels','pdf','preview'];
  const percentages={planning:0,tiles:10.5,geometry:24,visibility:35,drawing:50,labels:82,pdf:87,preview:92};
  const steps={planning:1,tiles:1,geometry:2,visibility:3,drawing:4,labels:4,pdf:5,preview:6};
  const job=(phase='planning')=>({id:'progress-test',session:'fixture',quality,revision:requests,status:'running',elapsed_seconds:polls*3,worker_alive:true,scene_available:polls>=2,scene_revision:mode==='refine'&&polls>=4?2:1,progress:{stage:phase,detail:phase==='drawing'?'Drawing parks and hatching':phase==='tiles'?'Loading map tiles':'Working on '+phase,completed:phase==='tiles'?3:phase==='drawing'?(polls===5?1000:0):null,total:phase==='tiles'?8:phase==='drawing'?1000:null,unit:phase==='drawing'?'lines':'tiles',overall_percent:percentages[phase]+(phase==='drawing'&&polls>=5?1:0),overall_step:steps[phase],overall_steps:8,updated_seconds_ago:mode==='slow'?25:0}});
  await page.route('**/api/preview',r=>{requests++;polls=0;quality=r.request().postDataJSON().quality;return r.fulfill({json:job()});});
  await page.route('**/api/jobs/progress-test',r=>{
   polls++;const result=job(mode==='slow'?'drawing':phases[Math.min(polls-1,phases.length-1)]);
   if(mode==='image-error'||mode==='ready')Object.assign(result,{status:'ready',artifacts:'/artifacts/progress-fixture/',result:{seconds:1,source:{zoom:12,tile_count:8},fetched_bounds:[-123,47,-122,48],feature_counts:{},label_count:0,omitted_labels:[],warnings:[],intent_available:false},progress:{stage:'complete',detail:'Composition complete'}});
   if(mode==='failure'&&polls>phases.length)Object.assign(result,{status:'error',error:'Test source unavailable'});
   return r.fulfill({json:result});
  });
  await page.route('**/api/scenes/progress-test',async r=>{
   sceneRequested=true;await wait(mode==='sketch'?100:6000);
   try{await r.fulfill({json:{lines:[],origin:[0,0],terrain:null}});}catch{}
  });
  await page.route(/\/api\/scenes\/progress-test\?revision=2$/,r=>r.fulfill({json:{
   lines:[{kind:'parks',points:[[0,0],[1000,1000]],area_m2:10000000}],origin:[-13618077.872,6048464.903],
   terrain:{xs:[-20000,20000],ys:[-20000,20000],z:[[0,0],[0,0]]},
   terrain_mesh:{bounds:[-20000,-20000,20000,20000],vertices:[[0,0,1000],[5000,0,0],[5000,5000,0],[0,5000,0],[2500,2500,250]],indices:[[4,0,1],[4,1,2],[4,2,3],[4,3,0]],cells:[[16384,16384,4096,0,4]],center_height_m:1000}
  }}));
  await page.route('**/artifacts/progress-fixture/preview.png',async r=>{
   if(mode==='ready')await wait(1200);
   return mode==='image-error'?r.fulfill({status:503,body:'Unavailable'}):r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect width="1" height="1" fill="white"/></svg>'});
  });
  await page.route('**/api/cancel',r=>{cancels++;return r.fulfill({json:{status:'cancelled'}});});
  await page.addInitScript(()=>{
   window.overallSamples=[];
   new MutationObserver(()=>{
    const bar=document.querySelector('#work-progress');
    if(bar&&!bar.hidden)window.overallSamples.push(bar.hasAttribute('value')?Number(bar.getAttribute('value')):-1);
   }).observe(document,{subtree:true,attributes:true,attributeFilter:['value']});
  });
  await page.goto(base);
  await page.waitForFunction(()=>document.querySelector('#work-count').textContent.includes('3 / 8 tiles'));
  assert.equal(await page.locator('#work-progress').getAttribute('value'),'10.5');
  assert.equal(await page.locator('#work-progress').getAttribute('max'),'100');
  await page.waitForFunction(()=>document.querySelector('#work-detail').textContent.includes('Drawing parks and hatching'));
  assert(sceneRequested,'Sketch request must have started');
  assert(polls>=4,'Status polling must continue while the sketch transfer is blocked');
  await page.screenshot({path:'build/progress-running.png',fullPage:true});
  await page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='error');
  const samples=await page.evaluate(()=>window.overallSamples);
  assert(samples.every((n,i)=>n>=0&&n<100&&(i===0||n>=samples[i-1])),'Overall progress must stay determinate and monotone despite inner count resets');
  assert(samples.includes(50)&&samples.includes(51)&&samples.includes(92));
  assert.equal(await page.locator('#loading').count(),0);assert(await page.locator('#retry-preview').isVisible());
  assert(await page.locator('#work-progress').isVisible());assert((await page.locator('#work-detail').textContent()).includes('Test source unavailable'));
  const originalArea=await page.locator('.preview-area').boundingBox(),originalProgress=await page.locator('#work-status').boundingBox();
  const stableLayout=async()=>{assert.deepEqual(await page.locator('.preview-area').boundingBox(),originalArea);assert.deepEqual(await page.locator('#work-status').boundingBox(),originalProgress);};
  const edit=async title=>{await page.locator('#title').fill(title);await page.locator('#title').press('Tab');};
  mode='slow';await page.locator('#retry-preview').click();
  assert(await page.locator('#notice').isHidden(),'Retry must clear the previous error');
  await page.waitForFunction(()=>document.querySelector('#work-detail').textContent.includes('Drawing parks and hatching'));
  await stableLayout();assert.equal(await page.locator('#stop-preview').count(),0);
  assert.match(await page.locator('#progress-time').textContent(),/elapsed/);
  mode='sketch';await edit('Sketch preview');
  await page.waitForFunction(()=>!document.querySelector('#preview-fidelity').hidden);
  assert((await page.locator('#preview-fidelity').textContent()).includes('pending'));
  assert(await page.locator('#sketch').isVisible());assert(await page.locator('#preview').isHidden());await stableLayout();
  mode='refine';await page.locator('#relief').check();
  await page.waitForFunction(()=>document.querySelector('#preview-fidelity').textContent.startsWith('Refined terrain sketch'));
  assert(await page.locator('#sketch').isVisible());assert(await page.locator('#preview').isHidden());await stableLayout();
  mode='image-error';await edit('Image error');
  await page.waitForFunction(()=>document.querySelector('#work-detail').textContent.includes('Could not load the preview image'));
  assert(await page.locator('#retry-preview').isVisible());await stableLayout();assert(cancels>0,'Editing cancels obsolete work automatically');
  mode='ready';await page.locator('#retry-preview').click();
  await page.waitForFunction(()=>document.querySelector('#work-detail').textContent.includes('Loading the completed preview image'));
  assert.equal(await page.locator('#work-progress').getAttribute('value'),'99');
  await page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
  assert(await page.locator('#preview').isVisible());assert(await page.locator('#retry-preview').isHidden());
  assert(await page.locator('#preview-fidelity').isHidden());
  assert.equal(await page.locator('#work-progress').getAttribute('value'),'100');
  assert.equal(await page.locator('#work-detail').textContent(),'Preview complete');await stableLayout();
  assert.equal(await page.locator('progress:visible').count(),1);
  assert.equal(await page.locator('#progress-estimate,#progress-steps,#print-status,.desk-caption').count(),0);
  // Refining an already resolved image must not call it an unoccluded sketch.
  mode='sketch';await page.locator('#export-tools').evaluate(el=>el.open=true);await page.locator('#generate').click();
  await page.waitForFunction(()=>document.querySelector('#work-detail').textContent.includes('Working on visibility'));
  assert(await page.locator('#preview').isVisible());assert(await page.locator('#preview-fidelity').isHidden());
  await stableLayout();
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:'build/progress-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,requests,cancels,checks:['overall monotone percentage','inner counts do not reset bar','100% only when image is visible','counts available in details','live stages during slow sketch','failure clears loading','retry','automatic cancellation on edits','stable preview and progress geometry across states','mobile sizing','early sketch fidelity label','refined terrain sketch replaces coarse scene','image failure and retry','completed preview','retains resolved image during refinement']}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
