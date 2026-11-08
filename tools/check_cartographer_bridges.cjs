#!/usr/bin/env node
/* Replay a composed scene in an isolated browser; no jobs or print submissions. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{chromium}=require('playwright');
(async()=>{
 const base=process.argv[2]||'http://127.0.0.1:9193',dir=process.argv[3]||'build/ravenna-bridge-proof';
 const recipe=JSON.parse(fs.readFileSync(dir+'/recipe.json')),scene=JSON.parse(fs.readFileSync(dir+'/scene-detail.json'));
 const browser=await chromium.launch();try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(r=>localStorage.setItem('plotline-map-draft',JSON.stringify({recipe:r})),recipe);
  await page.route('**/app.js',route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync('cartographer/web/app.js','utf8')+`\nwindow.bridgeCheck=(r,s)=>{recipe=r;scene=s;prepareTerrainSketch();drawSketch();return document.getElementById('sketch').toDataURL();};`}));
  await page.route('**/api/preview',route=>{const b=route.request().postDataJSON();return route.fulfill({json:{id:'bridge-check',cache_key:'a'.repeat(64),recipe_hash:'b'.repeat(64),revision:b.revision,quality:b.quality,status:'ready',artifacts:'/artifacts/bridge-check/',result:JSON.parse(fs.readFileSync(dir+'/result.json')),progress:{stage:'complete'}}});});
  await page.route('**/artifacts/bridge-check/preview.png',route=>route.fulfill({contentType:'image/png',body:fs.readFileSync(dir+'/preview.png')}));
  await page.route('**/api/scenes/bridge-check*',route=>route.fulfill({json:scene}));
  await page.route('**/api/cancel',route=>route.fulfill({json:{status:'cancelled'}}));
  await page.goto(base);await page.waitForFunction(()=>document.querySelector('#desk').dataset.previewState==='ready');
  const count=scene.lines.filter(l=>l.bridge&&l.points.some(p=>p.length===3)).length;assert(count>0);
  const ground=structuredClone(scene);for(const l of ground.lines)l.points=l.points.map(p=>p.slice(0,2));
  const render=(r,s)=>page.evaluate(([r,s])=>window.bridgeCheck(r,s),[r,s]);
  const deckImage=await render(recipe,scene),groundImage=await render(recipe,ground);assert.notEqual(deckImage,groundImage);
  fs.writeFileSync(dir+'/bridge-sketch.png',Buffer.from(deckImage.split(',')[1],'base64'));
  const flat=structuredClone(recipe);flat.camera.relief=false;assert.equal(await render(flat,scene),await render(flat,ground));
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,elevatedSketchLines:count,checks:['relief honors deck heights','flat mode ignores deck heights','no browser errors']}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
