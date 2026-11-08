#!/usr/bin/env node
/* Real studio preview/edit cycle; never submits anything to Plotline Studios. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{chromium}=require('playwright');
const base=process.argv[2]||'http://127.0.0.1:9193',recipeFile=process.argv[3];
(async()=>{
 const browser=await chromium.launch();try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];let latest=null;
  if(recipeFile)await page.addInitScript(recipe=>localStorage.setItem('plotline-map-draft',JSON.stringify({recipe,saved:null})),JSON.parse(fs.readFileSync(recipeFile,'utf8')));
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',async response=>{
   if(/\/api\/(jobs\/[^/]+|preview)$/.test(response.url())){
    const job=await response.json().catch(()=>null);if(job?.status==='ready')latest=job;
   }
  });
  const ready=async()=>{
   await page.waitForFunction(()=>['ready','error'].includes(document.querySelector('#desk').dataset.previewState),null,{timeout:180000});
   assert.equal(await page.locator('#desk').getAttribute('data-preview-state'),'ready',await page.locator('#notice').textContent());
  };
  await page.goto(base);await ready();const first=latest;assert(first?.result.build_cache);
  for(const d of await page.locator('aside details').all())await d.evaluate(el=>el.open=true);
  await page.locator('#park-labels').uncheck();await ready();
  assert.notEqual(latest.id,first.id);assert.equal(latest.result.build_cache.built,0);
  assert(latest.result.build_cache.reused>=7);
  assert((await page.locator('#report').textContent()).includes('rebuilt 0'));
  const after=JSON.parse(await page.evaluate(()=>localStorage.getItem('plotline-map-draft')));
  assert.equal(after.recipe.features.park_labels,false);assert.equal(after.recipe.features.parks,true);
  // Exercise a real role assignment through the shared ink picker. Color and
  // logical ID change; keep width unchanged to isolate geometry dependencies.
  await page.locator('#edit-inks').click();
  await page.locator('#palette .ink-role').filter({hasText:/^Water$/}).click();
  await page.getByRole('button',{name:'Create and assign a custom ink'}).click();
  await page.locator('.ink-inline-editor input[name=label]').fill('Incremental blue');
  await page.locator('.ink-inline-editor input[name=color_srgb]').fill('#1234ff');
  await page.getByRole('button',{name:'Apply ink',exact:true}).click();await ready();
  const recolored=latest;assert.notEqual(recolored.id,first.id);assert.equal(recolored.result.build_cache.built,0);
  assert(recolored.result.build_cache.reused>=7);
  const current=JSON.parse(await page.evaluate(()=>localStorage.getItem('plotline-map-draft'))).recipe;
  assert.notEqual(current.ink_roles.water,after.recipe.ink_roles.water);
  assert.equal(current.palette.inks.find(i=>i.id===current.ink_roles.water).color_srgb,'#1234ff');
  const intent=await (await page.request.get(new URL(recolored.artifacts+'map.print-intent.json',base).href)).json();
  assert(intent.bindings.some(b=>b.feature_group.startsWith('water/')&&b.ink_id===current.ink_roles.water));
  assert.deepEqual(errors,[]);fs.mkdirSync('build/incremental-review',{recursive:true});await page.screenshot({path:'build/incremental-review/browser.png',fullPage:true});
  console.log(JSON.stringify({passed:true,first_seconds:first.elapsed_seconds,color_seconds:recolored.elapsed_seconds,cache:recolored.result.build_cache}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
