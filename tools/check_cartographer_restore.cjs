#!/usr/bin/env node
/* Isolated browser state and mocked jobs/receipts; never creates or retries a print job. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const base=process.argv[2]||'http://127.0.0.1:9193';
const stable=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
const svg='<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><rect width="400" height="300" fill="white"/><path d="M20 150L380 150" stroke="red"/></svg>';
(async()=>{
 const browser=await chromium.launch();try{
  const setup=await browser.newContext();
  const catalog=await(await setup.request.get(base+'/api/catalog')).json();await setup.close();
  catalog.composer_version='current-renderer';
  const recipe=process.argv[3]?JSON.parse(fs.readFileSync(process.argv[3])):structuredClone(catalog.recipe);
  recipe.title='Ravenna Park';
  async function check({name,phase='failed',snapshot='current',image='ok',switchAway=false,receiptMissing=false,receiptOffline=false}){
   const context=await browser.newContext(),page=await context.newPage(),errors=[],unexpected=[];
   let previews=0,polls=0,oldImages=0,terminal=phase;
   page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
   const key='a'.repeat(64),hash='b'.repeat(64),signature=stable(recipe),preview=snapshot==='none'?null:{url:'/artifacts/saved/preview.png',signature,quality:'draft',...(snapshot==='legacy'?{}:{composerVersion:snapshot==='current'?catalog.composer_version:'previous-renderer'})};
   const doc={key:'ravenna',recipe,baseline:signature,needsSave:false,preview,printPointer:{kind:'preparation',key,recipe_hash:hash,signature,finished:false,preview_url:'/artifacts/prepared/preview.png'}};
   const cabin=structuredClone(doc);cabin.key='cabin';cabin.recipe.title='Cabin';cabin.baseline=stable(cabin.recipe);cabin.preview=null;cabin.printPointer=null;
   const workspace={version:1,active:'ravenna',documents:[doc,cabin]};
   await page.addInitScript(w=>{if(!sessionStorage.getItem('plotline-map-workspace'))sessionStorage.setItem('plotline-map-workspace',JSON.stringify(w));},workspace);
   // Keep all service mutations in this browser fixture. Only static assets are live.
   await page.route('**/api/**',async r=>{
    const path=new URL(r.request().url()).pathname;
    if(path==='/api/catalog')return r.fulfill({json:catalog});
    if(path==='/api/validate')return r.fulfill({json:{recipe:r.request().postDataJSON().recipe}});
    if(path==='/api/recipes')return r.fulfill({json:[]});
    if(path==='/api/capabilities')return r.fulfill({json:{offline:false,capabilities:{implemented:{pdf_upload:true,pdf_handoff:true},available_inks:[],inventory_revision:1,media_profiles:[]}}});
    if(path==='/api/cancel')return r.fulfill({json:{status:'cancelled'}});
    if(path==='/api/preparations/'+key){
     polls++;await new Promise(resolve=>setTimeout(resolve,350));
     if(receiptMissing)return r.fulfill({status:404,json:{error:'Missing receipt'}});
     if(receiptOffline)return r.fulfill({status:503,json:{error:'Temporarily unavailable'}});
     return r.fulfill({json:{id:key,recipe_hash:hash,phase:terminal,export_key:key,created_at:1,retryable:terminal==='failed',error:terminal==='failed'?'Saved conversion failed':null,jobs:terminal==='ready'?[{job_id:'one',url:'http://example.invalid/#one',page_index:0}]:[]}});
    }
    if(path==='/api/preview'){
     previews++;const b=r.request().postDataJSON();
     return r.fulfill({json:{id:'new-preview',cache_key:key,recipe_hash:hash,revision:b.revision,quality:b.quality,status:'ready',artifacts:'/artifacts/fresh/',result:{seconds:.1,source:{zoom:13,tile_count:1},fetched_bounds:[-123,47,-122,48],feature_counts:{},label_count:0,omitted_labels:[],warnings:[],unmeasured_inks:[]},progress:{stage:'complete'}}});
    }
    unexpected.push(path);return r.fulfill({status:500,json:{error:'Unexpected request in restore test'}});
   });
   await page.route('**/artifacts/*/preview.png',async r=>{
    if(r.request().url().includes('/saved/')){
     oldImages++;
     if(image==='hang')return; // Browser timeout must recover without an image event.
     if(image==='error')return r.abort('failed');
     await new Promise(resolve=>setTimeout(resolve,120));
    }
    return r.fulfill({contentType:'image/svg+xml',body:svg});
   });
   if(image==='hang')await page.addInitScript(()=>{const original=window.setTimeout;window.setTimeout=(fn,ms,...args)=>original(fn,ms===30000?600:ms,...args);});
   if(process.env.RESTORE_BASELINE)await page.route('**/app.js',r=>r.fulfill({contentType:'text/javascript',body:execFileSync('git',['show',process.env.RESTORE_BASELINE+':cartographer/web/app.js'],{encoding:'utf8'})}));
   await page.goto(base);
   if(switchAway){await page.getByRole('tab',{name:'Cabin',exact:true}).click();}
   if(phase==='converting'&&!switchAway){
    await page.waitForFunction(()=>document.querySelector('#work-status').dataset.phase==='converting');
    await page.waitForTimeout(500);assert.equal(previews,0,name+': active preparation must not start another render');
    terminal='failed';
   }
   await page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready',null,{timeout:5000});
   await page.waitForTimeout(450);
   assert.equal(await page.locator('#loading').count(),0,name+': no duplicate loading overlay');
   assert(await page.locator('#preview').isVisible(),name+': preview visible');
   if(switchAway){assert.equal(await page.locator('[role=tab][aria-selected=true]').textContent(),'Cabin');assert.equal(await page.locator('#work-status').getAttribute('data-owner'),'preview');}
   else{
    const fresh=snapshot!=='current'||image!=='ok';
    assert.equal(previews,fresh?1:0,name+': expected render count');
    if(fresh){
     assert((await page.locator('#preview').getAttribute('src')).includes('/fresh/'),name+': old plot must not replace new draft');
     const stored=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('plotline-map-workspace')));
     assert.equal(stored.documents[0].preview.composerVersion,catalog.composer_version);
    }
    if(['old','legacy'].includes(snapshot))assert.equal(oldImages,0,name+': do not restore obsolete renderer image');
   }
   if(receiptMissing){
    const stored=await page.evaluate(()=>['sessionStorage','localStorage'].map(storage=>JSON.parse(window[storage].getItem('plotline-map-workspace'))));
    for(const w of stored){assert.equal(w.documents[0].printPointer,null);assert.deepEqual(w.documents[0].recipe,recipe);}
    assert(await page.locator('#send').isEnabled());assert.match(await page.locator('#send').textContent(),/Prepare plot/);
    const before=polls;await page.reload();
    await page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready',null,{timeout:5000});
    assert.equal(polls,before,'Missing preparation is forgotten across reloads');
    assert(!((await page.locator('#notice').textContent())||'').includes('previous pen preview'));
   }
   if(receiptOffline)assert(await page.evaluate(()=>Boolean(JSON.parse(sessionStorage.getItem('plotline-map-workspace')).documents[0].printPointer)),'Temporary outages retain the preparation');
   assert.deepEqual(errors,[],name);assert.deepEqual(unexpected,[],name);assert(polls>0,name);
   await context.close();return name;
  }
  const checks=[];
  for(const options of [
   {name:'failed preparation plus cached preview'},
   {name:'failed preparation without cached preview',snapshot:'none'},
   {name:'completed preparation with unfinished browser pointer',phase:'ready'},
   {name:'renderer upgrade invalidates preview',snapshot:'old'},
   {name:'legacy preview is refreshed',snapshot:'legacy'},
   {name:'failed image load falls back',image:'error'},
   {name:'stalled image load times out',image:'hang'},
   {name:'active preparation waits, then restores draft',phase:'converting',snapshot:'none'},
   {name:'active preparation retains current cached preview',phase:'converting'},
   {name:'active preparation with broken cached image recovers',phase:'converting',image:'error'},
   {name:'missing preparation does not block draft',receiptMissing:true,snapshot:'none'},
   {name:'unavailable preparation does not block draft',receiptOffline:true,snapshot:'none'},
   {name:'switch tabs while receipt loads',snapshot:'none',switchAway:true},
  ])checks.push(await check(options));
  console.log(JSON.stringify({passed:true,checks}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
