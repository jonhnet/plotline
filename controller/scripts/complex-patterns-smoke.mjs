import {chooseGalleryFamily} from './pattern-gallery-checks.mjs';
// Saved-document and real PDF/intent acceptance for every new family. The final
// Julia preparation reaches HPGL Studio in an isolated store, without a device.
import {chromium,firefox} from 'playwright';
import {spawn,execFileSync} from 'node:child_process';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
import {complexFamilies as adjacentFamilies,complexPoint} from '../../patterns/web/complex.js';
import {defaults,pageTransform} from '../../patterns/web/generators.js';
const root=resolve(import.meta.dirname,'../..');await mkdir(root+'/build',{recursive:true});
const state=await mkdtemp(root+'/build/complex-browser-'),socket=createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1','Content-Type':'application/json'};
const server=spawn(process.env.PLOTLINE_TEST_BINARY||root+'/controller/target/debug/plotline-studios',['--listen',`127.0.0.1:${port}`,'--state',state+'/store'],{cwd:root,env:{...process.env,PLOTLINE_PDF_PROJECT:root},stdio:['ignore','ignore','pipe']});let logs='',browser;server.stderr.on('data',d=>logs+=d);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(path,body,method='POST'){const r=await fetch(base+path,body?{method,headers,body:JSON.stringify(body)}:{});const value=await r.json();assert(r.ok,JSON.stringify(value));return value;}
try{
  for(let i=0;;i++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(i>100)throw Error(logs);await sleep(100);}
  const inventory=await api('/api/carousel');
  for(const [i,color] of ['#000000','#008080','#c84253','#008000','#245bd6','#a32c99','#c19a00'].entries())inventory.slots[i+1]={state:'loaded',color,width_mm:.7,label:'Ink '+(i+1),speed_cm_s:5,force:4};
  inventory.slots[2].width_mm=1.1;
  await api('/api/carousel',inventory,'PUT');
  const carouselBefore=await api('/api/carousel');
  browser=await (process.env.PLOTLINE_BROWSER==='firefox'?firefox:chromium).launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[],exports=[];
  await context.addInitScript(()=>{const Original=Worker;window.newtonRuns=0;window.Worker=class extends Original{postMessage(data,...args){if(data.family==='newton')window.newtonRuns++;return super.postMessage(data,...args);}};});
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());await page.goto(base+'/patterns');await page.locator('#preview[data-ready=true]').waitFor();
  const active=()=>page.evaluate(()=>{const s=JSON.parse(sessionStorage.getItem('plotline-pattern-workspace'));return s.documents.find(d=>d.key===s.active);});
  for(const family of adjacentFamilies){
    await page.locator('#new-document').click();await chooseGalleryFamily(page,family.id);await page.locator('#preview[data-ready=true]').waitFor();
    let recipe=(await active()).recipe;assert.equal(recipe.family,family.id);assert.equal(recipe.inks.length,family.sequence?7:recipe.params.roots);assert.deepEqual(recipe.params,defaults(family.id));
    assert(await page.evaluate(()=>{const ids=[...document.querySelectorAll('[id]')].map(e=>e.id);return new Set(ids).size===ids.length;}),'Controls have unique IDs');
    const k=family.knobs[0];if(k.choices)await page.locator('#knob-'+k.key).selectOption(String(k.value+k.step));else await page.getByRole('button',{name:'Increase '+k.label,exact:true}).click();
    await page.locator('#preview[data-ready=true]').waitFor();assert.notDeepEqual((await active()).recipe.params,recipe.params);await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,recipe);
    if(family.id==='newton'){
      await page.locator('#edit-inks').click();await page.locator('[data-ink-id="role-2"]').click();await page.locator('.ink-loaded [data-slot="1"]').click();await page.locator('#close-inks').click();await page.locator('#preview[data-ready=true]').waitFor();
      const widthsBefore=(await active()).recipe.inks.map(i=>i.width_mm),runsBefore=await page.evaluate(()=>newtonRuns);
      await page.locator('#edit-inks').click();await page.locator('[data-ink-id="role-0"]').click();await page.locator('.ink-loaded [data-slot="2"]').click();await page.locator('#close-inks').click();await page.locator('#preview[data-ready=true]').waitFor();
      assert.deepEqual((await active()).recipe.inks.map(i=>i.width_mm),widthsBefore,'The available document inks did not change');
      assert(await page.evaluate(()=>newtonRuns)>runsBefore,'Rebinding a root to a broader existing pen recomputes spacing');
      await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,recipe);
      assert.equal(await page.locator('#knob-contours').count(),0,'Newton uses depth instead of a misleading contour count');
      await page.getByRole('button',{name:'Increase Detail depth',exact:true}).click();await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await active()).recipe.params.depth,9);
      await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,recipe);
      await page.locator('#knob-roots').evaluate(e=>{e.value='3';e.dispatchEvent(new Event('input',{bubbles:true}));});await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await active()).recipe.roles.length,3);
      await page.locator('#reset-preset').click();await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await active()).recipe.roles.length,5,'Reset knobs restores all root ink roles');recipe=(await active()).recipe;
    }
    await page.locator('#save').click();await page.locator('#save-form button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#document-tabs [role=tab][aria-selected=true]').textContent.includes('*'));
    const saved=(await active()).saved;assert.deepEqual((await api('/api/patterns/documents/'+saved.id)).recipe,recipe);
    await page.locator('summary[aria-label="PDF & other options"]').click();
    const download=page.waitForEvent('download'),exported=page.waitForResponse(r=>r.url().endsWith('/api/patterns/exports')&&r.request().method()==='POST');
    await page.locator('#download-pdf').click();const response=await exported;assert(response.ok(),await response.text());const record=await response.json();await (await download).saveAs(state+'/'+family.id+'.pdf');
    assert.equal(record.document_id,saved.id);assert.deepEqual(record.recipe,recipe);assert.equal(record.intent.authoring.application,'pattern-studio');assert.equal(record.intent.palette.inks.length,recipe.inks.length);assert.equal(record.intent.bindings.length,recipe.roles.length);assert.deepEqual(record.intent.pages[0].target_media,recipe.media);
    assert.deepEqual(record.intent.pages[0].placement,{scale:1,rotation_deg:0,offset_mm:[0,0]});exports.push({family:family.id,hash:record.hash});
    await page.getByRole('button',{name:'Close PDF options',exact:true}).click();await page.screenshot({path:state+'/'+family.id+'.png'});console.log('Saved and exported '+family.name);
  }
  await page.reload();await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await active()).recipe.family,'newton');assert.equal(await page.getByRole('tab').count(),4);
  // Explore is a document edit; shared preview zoom remains inspection.
  await page.locator('#new-document').click();await chooseGalleryFamily(page,'mandelbrot');await page.locator('#preview[data-ready=true]').waitFor();
  const sourceKey=(await active()).key,original=(await active()).recipe;
  await page.locator('#explore-plane').click();const box=await page.locator('#preview').boundingBox();
  const center=[box.x+box.width/2,box.y+box.height/2];
  await page.mouse.move(...center);await page.mouse.wheel(0,-240);await page.waitForTimeout(400);await page.locator('#preview[data-ready=true]').waitFor();
  const zoomed=(await active()).recipe;assert(zoomed.params.viewport.span<3.4);assert.notDeepEqual(zoomed,original);
  await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,original,'One undo restores the entire wheel gesture');
  await page.locator('#redo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,zoomed);
  await page.mouse.move(...center);await page.mouse.down();await page.mouse.move(center[0]+70,center[1]-35,{steps:8});await page.mouse.up();await page.locator('#preview[data-ready=true]').waitFor();
  const panned=(await active()).recipe;assert.notEqual(panned.params.viewport.real,zoomed.params.viewport.real);
  await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,zoomed,'One undo restores the entire drag');
  await page.mouse.move(...center);await page.mouse.down();await page.mouse.move(center[0]+100,center[1]+20,{steps:5});await page.keyboard.press('Escape');await page.mouse.up();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,zoomed,'Escape restores the window');
  await page.locator('#zoom-in').click();assert.deepEqual((await active()).recipe,zoomed);await page.locator('#fit-view').click();
  await page.locator('#knob-rotation').evaluate(e=>{e.value='45';e.dispatchEvent(new Event('input',{bubbles:true}));});await page.locator('#preview[data-ready=true]').waitFor();
  const source=(await active()).recipe;
  // Choose a known normalized point through an independently calculated page
  // and screen transform, including rotated artwork and asymmetric margins.
  const t=pageTransform({paths:[],frame:[-1,-1,2,2]},source),point=[.12,-.18];
  const paper=[t.ox+t.scale*(point[0]*t.cos-point[1]*t.sin),t.oy+t.scale*(point[0]*t.sin+point[1]*t.cos)],sheet=source.media.sheet_size_mm;
  const scale=Math.min((box.width-60)/sheet[0],(box.height-55)/sheet[1]);
  const click=[box.x+box.width/2+(paper[0]-sheet[0]/2)*scale,box.y+box.height/2+(paper[1]-sheet[1]/2)*scale];
  const expected=complexPoint('mandelbrot',source.params,point);
  await page.locator('#pick-julia').click();await page.mouse.click(...click);await page.locator('#preview[data-ready=true]').waitFor();
  const julia=(await active()).recipe;assert.equal(julia.family,'julia');assert(julia.params.c.every((v,i)=>Math.abs(v-expected[i])<.02),'The selected point is converted to complex coordinates');
  assert.deepEqual(julia.media,source.media);assert.deepEqual(julia.inks,source.inks);assert.equal(julia.rotation,45);
  const workspace=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('plotline-pattern-workspace')));
  assert.deepEqual(workspace.documents.find(d=>d.key===sourceKey).recipe,source,'Picking preserves the source document');assert.equal(workspace.documents.length,6);
  assert.equal(Number(await page.locator('#julia-real').inputValue()),julia.params.c[0]);
  await page.locator('#save').click();await page.locator('#save-form button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#document-tabs [role=tab][aria-selected=true]').textContent.includes('*'));
  await page.reload();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,julia,'Arbitrary Julia c survives save and reload');
  await page.screenshot({path:state+'/linked-julia.png'});
  await page.locator('#prepare').click();await page.waitForFunction(()=>!document.querySelector('#open-plot').disabled||document.querySelector('#prepare').textContent==='Retry preparation',null,{timeout:180000});
  assert(await page.locator('#open-plot').isEnabled(),await page.locator('#work-detail').textContent());
  const opening=context.waitForEvent('page');await page.locator('#open-plot').click();const hpgl=await opening;await hpgl.locator('#preview-canvas[data-ready=true]').waitFor();await hpgl.screenshot({path:state+'/julia-hpgl.png'});
  await page.bringToFront();
  const blank={...source,name:'Empty window',params:{...source.params,viewport:{real:0,imag:0,span:.01}}};
  const savedBlank=await api('/api/patterns/documents',{recipe:blank});await page.goto(base+'/patterns?document='+savedBlank.id);await page.locator('#preview[data-ready=true]').waitFor();
  assert(await page.locator('#prepare').isDisabled(),'Empty windows cannot prepare blank plots');assert.match(await page.locator('#work-detail').textContent(),/No contours/);
  await page.locator('#reset-plane').click();await page.locator('#preview[data-ready=true]').waitFor();assert(await page.locator('#prepare').isEnabled(),'A blank view remains navigable');
  await page.locator('#undo').click();await page.locator('#preview[data-ready=true]').waitFor();assert.deepEqual((await active()).recipe,blank);
  const oldNewton={...julia,name:'Legacy Newton',family:'newton',params:{roots:5,contours:8,spacing:.75},roles:julia.roles.slice(0,5)};
  delete oldNewton.ink_sequence;oldNewton.inks=oldNewton.inks.filter(i=>oldNewton.roles.some(r=>r.ink_id===i.id));
  const legacy=await api('/api/patterns/documents',{recipe:oldNewton});await page.goto(base+'/patterns?document='+legacy.id);await page.locator('#preview[data-ready=true]').waitFor();
  assert.equal((await active()).recipe.params.depth,7);assert.equal((await active()).recipe.params.contours,undefined);
  assert.deepEqual((await api('/api/patterns/documents/'+legacy.id)).recipe,oldNewton,'Opening does not rewrite the saved legacy source');
  await page.locator('#save').click();await page.waitForFunction(()=>!document.querySelector('#document-tabs [role=tab][aria-selected=true]').textContent.includes('*'));
  await page.reload();await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await active()).recipe.params.depth,7,'Chosen depth survives save and reload');
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
print('PASS all three PDFs: every segment, stroke-only paint, physical placement, finite pen bounds')
`,state],{encoding:'utf8'});console.log(audit.trim());
  assert.deepEqual(errors,[]);assert.deepEqual(await api('/api/carousel'),carouselBefore);
  console.log('PASS three saved families, categorical controls/undo, exact PDF intents, reload and Julia → HPGL. Artifacts: '+state);
}catch(e){console.error(logs);if(browser)for(const p of browser.contexts()[0]?.pages()||[])await p.screenshot({path:state+'/failure-'+new URL(p.url()).pathname.replaceAll('/','_')+'.png'}).catch(()=>{});throw e;}
finally{await browser?.close();server.kill('SIGTERM');}
