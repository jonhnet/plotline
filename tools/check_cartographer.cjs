#!/usr/bin/env node
/* Live-source browser acceptance. Needs provider network on a cold cache.
   NODE_PATH=/root/plotline/controller/node_modules node tools/check_cartographer.cjs */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'..'),wait=ms=>new Promise(r=>setTimeout(r,ms));
const directory=fs.mkdtempSync(path.join(ROOT,'build/cartographer-browser-'));
const port=19194,base=`http://127.0.0.1:${port}`;
const server=spawn(process.env.PLOTLINE_PYTHON||path.join(ROOT,'build/cartographer-lite-venv/bin/python'),
 ['-m','cartographer.service','--host','127.0.0.1','--port',String(port),'--state',path.join(directory,'state'),'--build',path.join(directory,'cache')],{cwd:ROOT,stdio:['ignore','pipe','pipe']});
const log=fs.createWriteStream(path.join(directory,'server.log'));server.stdout.pipe(log);server.stderr.pipe(log);
let browser;
(async()=>{
 for(let i=0;i<150;i++){try{if((await fetch(base+'/api/catalog')).ok)break;}catch{}await wait(200);}
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const ready=async(type='ready')=>{
  await page.waitForFunction(t=>[t,'error'].includes(document.querySelector('#desk').dataset.previewState),type,{timeout:180000});
  assert.equal(await page.locator('#desk').getAttribute('data-preview-state'),type,await page.locator('#notice').innerText());
 };
 const value=async(id,text)=>{await page.locator('#'+id).fill(String(text));await page.locator('#'+id).press('Tab');};
 const draft=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('plotline-map-draft')).recipe);
 const proof=async()=>{
  await page.locator('#export-tools').evaluate(el=>el.open=true);await page.locator('#generate').click();await ready('ready');await page.locator('#pdf').waitFor({state:'visible'});
  const url=await page.locator('#pdf').getAttribute('href');const response=await page.request.get(base+url);const bytes=await response.body();
  assert.equal(bytes.subarray(0,5).toString(),'%PDF-');
  const recipe=await(await page.request.get(base+url.replace('map.pdf','recipe.json'))).json();
  const provenance=await(await page.request.get(base+url.replace('map.pdf','provenance.json'))).json();
  return {recipe,provenance,url};
 };
 await page.goto(base);await ready();
 assert.equal(await page.locator('#presets').count(),0);assert(!(await page.locator('body').innerText()).includes('Make it your map'));
 // Navigation is not clamped to Seattle. Rotation works with relief disabled.
 await value('longitude',-122.67);await value('latitude',45.52);await value('scale',6500);
 await page.locator('#azimuth').evaluate(el=>{el.value=24;el.dispatchEvent(new Event('input',{bubbles:true}));});
 await ready();assert.equal((await draft()).center[1],45.52);
 const before=await draft();await page.locator('#style-preset').selectOption('waterways');await page.locator('#apply-style').click();
 const after=await draft();assert.deepEqual(after.center,before.center);assert.deepEqual(after.camera,before.camera);assert.deepEqual(after.media,before.media);assert.equal(after.features.shore,'echo');
 await page.locator('#style-preset').selectOption('balanced');await page.locator('#apply-style').click();
 const portland=await proof();assert(portland.provenance.feature_counts.buildings>0);assert.equal(portland.recipe.camera.relief,false);assert.equal(portland.recipe.camera.azimuth_deg,24);
 await page.screenshot({path:path.join(directory,'portland-flat.png'),fullPage:true});
 // Save/reopen revisions and preserve optimistic conflict detection.
 await value('title','Portland acceptance');assert(await page.locator('#pdf').isVisible());assert.equal(await page.locator('#pdf').getAttribute('aria-disabled'),'true');await page.locator('#save').click();
 await page.locator('#save-form button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#saved-state').textContent==='Saved');
 await value('title','Portland revision two');await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#saved-state').textContent==='Saved');
 await page.reload();await ready();assert.equal(await page.locator('#title').inputValue(),'Portland revision two');
 const list=await(await page.request.get(base+'/api/recipes')).json(),old=await(await page.request.get(base+'/api/recipes/'+list[0].id+'?revision=1')).json();
 assert.equal(old.recipe.title,'Portland acceptance');assert.equal((await page.request.post(base+'/api/recipes',{data:{id:old.id,expected_revision:1,recipe:old.recipe}})).status(),409);
 // Independent real boundary, outside style, blue shoreline and 3D camera.
 await page.locator('details').filter({has:page.locator('#boundary-query')}).locator('summary').click();
 await page.locator('#boundary-kind').selectOption('state');await page.locator('#boundary-query').fill('Washington');await page.locator('#boundary-search').click();
 await page.locator('#boundary-results button').filter({hasText:'Select'}).first().click();
 await page.waitForFunction(()=>document.querySelector('#boundary-status').textContent.includes('Washington'));
 await page.locator('#style-preset').selectOption('waterways');await page.locator('#apply-style').click();
 await page.locator('#style-region').selectOption('outside');await page.locator('#parks').uncheck();await page.locator('#roads').selectOption('major');
 await page.locator('#style-region').selectOption('inside');await page.locator('#parks').uncheck();
 await page.locator('details').filter({has:page.locator('#palette')}).locator('summary').click();
 await page.locator('#add-ink').click();const colors=page.locator('#palette input[type=color]');await colors.last().fill('#0044cc');
 await page.locator('#palette input[type=number]').last().fill('.25');await page.locator('#palette input[type=number]').last().press('Tab');
 const r=await draft();await page.getByLabel('water ink',{exact:true}).selectOption(r.palette.inks.at(-1).id);
 await page.locator('#relief').check();await page.locator('#fit').click();await ready();
 const combined=await proof();assert(combined.recipe.camera.relief);assert.equal(combined.recipe.boundary.name,'Washington');assert.equal(combined.recipe.features.shore,'echo');
 assert.equal(combined.recipe.outside.ink_roles.water,'black');assert.equal(combined.provenance.depth_audit.maximum_frontmost_depth_error_m,0);
 assert(combined.provenance.labels.some(l=>l.text==='Seattle'),'Boundary mesh seams must not suppress interior labels');
 assert(combined.provenance.feature_counts['outside/roads/casing']>0);
 await page.screenshot({path:path.join(directory,'washington-relief.png'),fullPage:true});
 // Full-building state-scale export must refuse rather than silently simplify.
 await page.locator('#buildings').selectOption('all');await page.locator('#export-tools').evaluate(el=>el.open=true);await page.locator('#generate').click();
 await page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='error',null,{timeout:180000});
 assert((await page.locator('#notice').innerText()).includes('Every building'));
 await page.locator('#buildings').selectOption('none');await ready();
 // Browser sizing and recipe persistence are independent of desktop layout.
 await page.setViewportSize({width:390,height:844});await wait(300);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.screenshot({path:path.join(directory,'mobile.png'),fullPage:true});
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(directory,'results.json'),JSON.stringify({portland:portland.url,combined:combined.url,errors},null,2));
 console.log(JSON.stringify({passed:true,directory,portland:portland.url,washington:combined.url}));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill('SIGTERM');log.end();});
