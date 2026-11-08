// Saved-document and real PDF/intent acceptance for the adjacent/natural batches.
// The final family reaches HPGL Studio in an isolated store, without a device.
import {chromium,firefox} from 'playwright';
import {spawn,execFileSync} from 'node:child_process';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
import {adjacentFamilies} from '../../patterns/web/adjacent.js';
import {naturalFamilies} from '../../patterns/web/natural.js';
import {spatialFamilies} from '../../patterns/web/spatial.js';
import {subdivisionFamilies} from '../../patterns/web/subdivision.js';
import {chooseGalleryFamily} from './pattern-gallery-checks.mjs';
import {collectionFamilies} from '../../patterns/web/collection.js';
import {textileFamilies} from '../../patterns/web/textile.js';
import {ornamentFamilies} from '../../patterns/web/ornament.js';
import {recursiveFamilies} from '../../patterns/web/recursive-art.js';
import {densityFamilies} from '../../patterns/web/density.js';
const collection=process.argv.includes('--collection'),textile=process.argv.includes('--textile'),ornament=process.argv.includes('--ornament'),recursive=process.argv.includes('--recursive'),natural=process.argv.includes('--natural'),spatial=process.argv.includes('--spatial'),curvesDensity=process.argv.includes('--curves-density'),families=collection?collectionFamilies:textile?textileFamilies:ornament?ornamentFamilies:recursive?recursiveFamilies:curvesDensity?[...subdivisionFamilies,...densityFamilies]:spatial?spatialFamilies:natural?naturalFamilies:adjacentFamilies,lastFamily=families.at(-1).id;
import {checkCarpetCorners} from './carpet-corner-checks.mjs';
import {defaults} from '../../patterns/web/generators.js';
const root=resolve(import.meta.dirname,'../..');await mkdir(root+'/build',{recursive:true});
const state=await mkdtemp(root+(collection?'/build/collection-browser-':textile?'/build/textile-browser-':ornament?'/build/ornament-browser-':recursive?'/build/recursive-browser-':curvesDensity?'/build/curves-density-browser-':spatial?'/build/spatial-browser-':natural?'/build/natural-browser-':'/build/adjacent-browser-')),socket=createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1','Content-Type':'application/json'};
const server=spawn(process.env.PLOTLINE_TEST_BINARY||root+'/controller/target/debug/plotline-studios',['--listen',`127.0.0.1:${port}`,'--state',state+'/store'],{cwd:root,env:{...process.env,PLOTLINE_PDF_PROJECT:root},stdio:['ignore','ignore','pipe']});let logs='',browser;server.stderr.on('data',d=>logs+=d);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(path,body,method='POST'){const r=await fetch(base+path,body?{method,headers,body:JSON.stringify(body)}:{});const value=await r.json();assert(r.ok,JSON.stringify(value));return value;}
try{
  for(let i=0;;i++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(i>100)throw Error(logs);await sleep(100);}
  const inventory=await api('/api/carousel');
  for(const [i,color] of ['#000000','#008080','#c84253','#008000','#245bd6','#a32c99','#c19a00'].entries())inventory.slots[i+1]={state:'loaded',color,width_mm:.3,label:'Ink '+(i+1),speed_cm_s:5,force:4};
  if(natural||spatial||curvesDensity||recursive||ornament||textile||collection)inventory.slots[2].width_mm=.7;
  await api('/api/carousel',inventory,'PUT');
  const carouselBefore=await api('/api/carousel');
  browser=await (process.env.PLOTLINE_BROWSER==='firefox'?firefox:chromium).launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[],exports=[];
  await context.addInitScript(()=>{const Original=Worker;window.patternRuns=[];window.Worker=class extends Original{postMessage(data,...args){if(data.family){const run={family:data.family,authoring:!!data.recipe,params:structuredClone(data.params),previews:0,frames:0};window.patternRuns.push(run);this.addEventListener('message',e=>{if(e.data.checkpoint)run.previews++;if(e.data.frame)run.frames++;if(e.data.result){run.points=e.data.result.points;run.done=true;}});}return super.postMessage(data,...args);}};});
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());await page.goto(base+'/patterns');await page.locator('#preview[data-ready=true]').waitFor();
  const active=()=>page.evaluate(()=>{const s=JSON.parse(sessionStorage.getItem('plotline-pattern-workspace'));return s.documents.find(d=>d.key===s.active);});
  for(const family of families){
    await page.locator('#new-document').click();await chooseGalleryFamily(page,family.id);await page.locator('#preview[data-ready=true]').waitFor();
    const recipe=(await active()).recipe;assert.equal(recipe.family,family.id);assert.equal(recipe.inks.length,family.sequence?7:family.roles.length);assert.deepEqual(recipe.params,defaults(family.id));
    assert(await page.evaluate(()=>{const ids=[...document.querySelectorAll('[id]')].map(e=>e.id);return new Set(ids).size===ids.length;}),'Controls have unique IDs');
    if(natural||spatial||curvesDensity||recursive||ornament||textile||collection){const run=await page.evaluate(id=>patternRuns.filter(r=>r.family===id&&r.authoring).at(-1),family.id);assert(run.previews>=1&&run.frames>=1,'A complete coarse checkpoint and incremental bitmap precede the finished drawing');}
    const k=family.knobs[0],neighbor=k.value+k.step<=k.max?k.value+k.step:k.value-k.step;if(k.choices)await page.locator('#knob-'+k.key).selectOption(String(neighbor));else await page.getByRole('button',{name:'Increase '+k.label,exact:true}).click();
    await page.locator('#preview[data-ready=true]').waitFor();assert.notDeepEqual((await active()).recipe.params,recipe.params);await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,recipe);
    if(['woven','starweave','hatched_solids'].includes(family.id)){
      const before=await page.evaluate(id=>patternRuns.filter(r=>r.authoring&&r.family===id).length,family.id);
      await page.getByRole('button',{name:'Increase Size',exact:true}).click();await page.locator('#preview[data-ready=true]').waitFor();
      assert(await page.evaluate(id=>patternRuns.filter(r=>r.authoring&&r.family===id).length,family.id)>before,'Physical crossing gaps recompute when paper scale changes');
      await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,recipe);
    }
    await page.locator('#save').click();await page.locator('#save-form button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#document-tabs [role=tab][aria-selected=true]').textContent.includes('*'));
    const saved=(await active()).saved;assert.deepEqual((await api('/api/patterns/documents/'+saved.id)).recipe,recipe);
    await page.locator('summary[aria-label="PDF & other options"]').click();
    const download=page.waitForEvent('download'),exported=page.waitForResponse(r=>r.url().endsWith('/api/patterns/exports')&&r.request().method()==='POST');
    await page.locator('#download-pdf').click();const response=await exported;assert(response.ok(),await response.text());const record=await response.json();await (await download).saveAs(state+'/'+family.id+'.pdf');
    assert.equal(record.document_id,saved.id);assert.deepEqual(record.recipe,recipe);assert.equal(record.intent.authoring.application,'pattern-studio');const scene=JSON.parse(await readFile(state+'/store/patterns/exports/'+record.hash+'/scene.json','utf8')),usedRoles=new Set(scene.paths.map(p=>p.role)),usedInks=new Set([...usedRoles].map(i=>recipe.roles[i].ink_id));assert.deepEqual(record.intent.palette.inks,recipe.inks.filter(i=>usedInks.has(i.id)));assert.equal(record.intent.bindings.length,usedRoles.size);assert.deepEqual(record.intent.pages[0].target_media,recipe.media);
    assert.deepEqual(record.intent.pages[0].placement,{scale:1,rotation_deg:0,offset_mm:[0,0]});exports.push({family:family.id,hash:record.hash});
    await page.getByRole('button',{name:'Close PDF options',exact:true}).click();await page.screenshot({path:state+'/'+family.id+'-studio.png'});console.log('Saved and exported '+family.name);
  }
  await page.reload();await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await active()).recipe.family,lastFamily);assert.equal(await page.getByRole('tab').count(),families.length+1);
  const defaultsRecipe=(await active()).recipe;
  if(natural){
    // Hold a completed checkpoint briefly to exercise Cancel deterministically,
    // even on machines that finish the small default knot before a human click.
    await page.route('**/patterns/worker.js',async route=>{const response=await route.fetch();const source=await response.text();await route.fulfill({response,body:source.replace('await yieldInput();\n  }\n  let result;', 'await yieldInput();await new Promise(resolve=>setTimeout(resolve,1200));\n  }\n  let result;')});});
    await page.locator('#knob-knot').selectOption('0');await page.waitForFunction(()=>patternRuns.some(r=>r.authoring&&r.family==='woven'&&r.params.knot===0&&r.previews));
    await page.locator('#cancel-preview').click();await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await active()).recipe.params.strands,3,'Cancel restores the completed strand count');assert.equal((await active()).recipe.params.knot,0);
    await page.unroute('**/patterns/worker.js');await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,defaultsRecipe);
  }
  if(spatial){
    await page.route('**/patterns/worker.js',async route=>{const response=await route.fetch();await route.fulfill({response,body:(await response.text()).replace('await yieldInput();\n  }\n  let result;', 'await yieldInput();await new Promise(resolve=>setTimeout(resolve,1200));\n  }\n  let result;')});});
    await page.getByRole('button',{name:'Increase Azimuth',exact:true}).click();
    await page.waitForFunction(()=>patternRuns.some(r=>r.authoring&&r.family==='tetrahedron'&&r.params.azimuth===35&&r.previews));
    await page.locator('#cancel-preview').click();await page.locator('#preview[data-ready=true]').waitFor();
    assert.equal((await active()).recipe.params.depth,2);assert.equal((await active()).recipe.params.azimuth,35);
    await page.unroute('**/patterns/worker.js');await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,defaultsRecipe);
  }
  if(curvesDensity){
    await page.route('**/patterns/worker.js',async route=>{const response=await route.fetch();await route.fulfill({response,body:(await response.text()).replace('await yieldInput();\n  }\n  let result;', 'await yieldInput();await new Promise(resolve=>setTimeout(resolve,1200));\n  }\n  let result;')});});
    await page.locator('#knob-shape').selectOption('2');
    await page.waitForFunction(()=>patternRuns.some(r=>r.authoring&&r.family==='dejong'&&r.params.shape===2&&r.previews));
    await page.locator('#cancel-preview').click();await page.locator('#preview[data-ready=true]').waitFor();
    assert.equal((await active()).recipe.params.depth,1);assert.equal((await active()).recipe.params.shape,2);
    await page.unroute('**/patterns/worker.js');await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,defaultsRecipe);
  }
  if(recursive){
    await page.route('**/patterns/worker.js',async route=>{const response=await route.fetch();await route.fulfill({response,body:(await response.text()).replace('await yieldInput();\n  }\n  let result;', 'await yieldInput();await new Promise(resolve=>setTimeout(resolve,1200));\n  }\n  let result;')});});
    await page.getByRole('button',{name:'Increase Variation',exact:true}).click();
    await page.waitForFunction(()=>patternRuns.some(r=>r.authoring&&r.family==='lightning'&&r.params.seed===8&&r.previews));
    await page.locator('#cancel-preview').click();await page.locator('#preview[data-ready=true]').waitFor();
    assert.equal((await active()).recipe.params.depth,4);assert.equal((await active()).recipe.params.seed,8);
    await page.unroute('**/patterns/worker.js');await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,defaultsRecipe);
  }
  if(ornament){
    await page.route('**/patterns/worker.js',async route=>{const response=await route.fetch();await route.fulfill({response,body:(await response.text()).replace('await yieldInput();\n  }\n  let result;', 'await yieldInput();await new Promise(resolve=>setTimeout(resolve,1200));\n  }\n  let result;')});});
    await page.getByRole('button',{name:'Increase Variation',exact:true}).click();
    await page.waitForFunction(()=>patternRuns.some(r=>r.authoring&&r.family==='architecture'&&r.params.seed===8&&r.previews));
    await page.locator('#cancel-preview').click();await page.locator('#preview[data-ready=true]').waitFor();
    assert.equal((await active()).recipe.params.depth,1);assert.equal((await active()).recipe.params.seed,8);
    await page.unroute('**/patterns/worker.js');await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,defaultsRecipe);
  }
  if(textile){
    await page.route('**/patterns/worker.js',async route=>{const response=await route.fetch();await route.fulfill({response,body:(await response.text()).replace('await yieldInput();\n  }\n  let result;', 'await yieldInput();await new Promise(resolve=>setTimeout(resolve,1200));\n  }\n  let result;')});});
    await page.getByRole('button',{name:'Increase Variation',exact:true}).click();
    await page.waitForFunction(()=>patternRuns.some(r=>r.authoring&&r.family==='reaction'&&r.params.seed===8&&r.previews));
    await page.locator('#cancel-preview').click();await page.locator('#preview[data-ready=true]').waitFor();
    assert.equal((await active()).recipe.params.time,1000);assert.equal((await active()).recipe.params.seed,8);
    await page.unroute('**/patterns/worker.js');await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,defaultsRecipe);
  }
  if(collection){
    await page.route('**/patterns/worker.js',async route=>{const response=await route.fetch();await route.fulfill({response,body:(await response.text()).replace('await yieldInput();\n  }\n  let result;', 'await yieldInput();await new Promise(resolve=>setTimeout(resolve,1200));\n  }\n  let result;')});});
    await page.getByRole('button',{name:'Increase Lean',exact:true}).click();
    await page.waitForFunction(()=>patternRuns.some(r=>r.authoring&&r.family==='lsystem'&&r.params.lean===.1&&r.previews));
    await page.locator('#cancel-preview').click();await page.locator('#preview[data-ready=true]').waitFor();
    assert.equal((await active()).recipe.params.depth,2);assert.equal((await active()).recipe.params.lean,.1);
    await page.unroute('**/patterns/worker.js');await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,defaultsRecipe);
  }
  await page.locator('#prepare').click();await page.waitForFunction(()=>!document.querySelector('#open-plot').disabled||document.querySelector('#prepare').textContent==='Retry preparation',null,{timeout:180000});
  assert(await page.locator('#open-plot').isEnabled(),await page.locator('#work-detail').textContent());
  const opening=context.waitForEvent('page');await page.locator('#open-plot').click();const hpgl=await opening;await hpgl.locator('#preview-canvas[data-ready=true]').waitFor();await hpgl.screenshot({path:state+'/'+lastFamily+'-hpgl.png'});
  // Inspect the exported PDF independently: source line count, stroke-only
  // paints, physical page, and finite pen footprints inside the target window.
  await writeFile(state+'/exports.json',JSON.stringify(exports));
  const audit=execFileSync(root+'/.venv/bin/python',['-c',`
import json, sys, pathlib, pymupdf
root=pathlib.Path(sys.argv[1])
for item in json.loads((root/'exports.json').read_text()):
    folder=root/'store/patterns/exports'/item['hash']
    scene=json.loads((folder/'scene.json').read_text()); media=scene['recipe']['media']
    with pymupdf.open(folder/'drawing.pdf') as pdf:
        page=pdf[0]; drawings=page.get_drawings(); mm=25.4/72
        assert all(abs(a-b)<.001 for a,b in zip([page.rect.width*mm,page.rect.height*mm],media['sheet_size_mm']))
        assert all(d['type']=='s' for d in drawings)
        assert sum({'l':1,'re':4,'qu':4}[item[0]] for d in drawings for item in d['items']) == sum(len(p['points'])-1 for p in scene['paths'])
        x,y,w,h=media['printable_rect_mm']
        for d in drawings:
            r=d['rect']; half=d['width']*mm/2
            assert r.x0*mm-half>=x-.001 and r.y0*mm-half>=y-.001 and r.x1*mm+half<=x+w+.001 and r.y1*mm+half<=y+h+.001
print('PASS all exported PDFs: every segment, stroke-only paint, physical placement, finite pen bounds')
`,state],{encoding:'utf8'});console.log(audit.trim());
  if(recursive)await checkCarpetCorners(context,base,api,defaultsRecipe);
  assert.deepEqual(errors,[]);assert.deepEqual(await api('/api/carousel'),carouselBefore);
  console.log('PASS '+families.length+' saved families, categorical controls/undo, exact PDF intents, reload and '+lastFamily+' → HPGL. Artifacts: '+state);
}catch(e){console.error(logs);if(browser)for(const p of browser.contexts()[0]?.pages()||[])await p.screenshot({path:state+'/failure-'+new URL(p.url()).pathname.replaceAll('/','_')+'.png'}).catch(()=>{});throw e;}
finally{await browser?.close();server.kill('SIGTERM');}
