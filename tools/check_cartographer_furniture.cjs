#!/usr/bin/env node
/* Furniture controls, persistence and full-area export against a running studio.
   NODE_PATH=/root/plotline/controller/node_modules node tools/check_cartographer_furniture.cjs */
const assert=require('node:assert/strict'),fs=require('node:fs'),{chromium}=require('playwright');
const base=process.argv[2]||'http://127.0.0.1:9193';
(async()=>{
 const browser=await chromium.launch();try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const ready=async(label='ready')=>{
   await page.waitForFunction(t=>[t,'error'].includes(document.querySelector('#desk').dataset.previewState),label,{timeout:180000});
   assert.equal(await page.locator('#desk').getAttribute('data-preview-state'),label,await page.locator('#notice').textContent());
  };
  const draft=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('plotline-map-draft')).recipe);
  const frame=()=>page.locator('#printable-guide').evaluate(el=>['x','y','width','height'].map(k=>Number(el.getAttribute(k))));
  await page.goto(base);await ready();
  const original=await draft(),before=await frame();assert(Object.values(original.furniture).every(v=>!v));
  original.media.printable_rect_mm.forEach((v,k)=>assert(Math.abs(v-before[k])<1e-8));
  await page.locator('#furniture-title').check();await page.locator('#furniture-compass').check();
  await ready();assert.deepEqual(await frame(),before);
  await page.reload();await ready();assert(await page.locator('#furniture-title').isChecked());assert(await page.locator('#furniture-compass').isChecked());
  await page.locator('#export-tools').evaluate(el=>el.open=true);await page.locator('#generate').click();await ready('ready');
  const url=await page.locator('#pdf').getAttribute('href');
  const pdf=await(await page.request.get(base+url)).body();assert.equal(pdf.subarray(0,5).toString(),'%PDF-');
  const recipe=await(await page.request.get(base+url.replace('map.pdf','recipe.json'))).json();
  const provenance=await(await page.request.get(base+url.replace('map.pdf','provenance.json'))).json();
  assert(recipe.furniture.title&&recipe.furniture.compass);assert(!recipe.furniture.credits&&!recipe.furniture.signature);
  assert.deepEqual(provenance.layout.frame_mm,provenance.layout.content_mm);
  fs.mkdirSync('build/furniture-review',{recursive:true});await page.screenshot({path:'build/furniture-review/browser-ornament.png',fullPage:true});
  await page.locator('#hide-furniture').click();await ready();assert(Object.values((await draft()).furniture).every(v=>!v));assert.deepEqual(await frame(),before);
  assert.equal((await draft()).framing.scale_denominator,original.framing.scale_denominator);
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:'build/furniture-review/browser-bare-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,url,checks:['full usable viewport','default all off','title/rose persistence','vector PDF export','hide all','scale preserved','mobile layout']}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
