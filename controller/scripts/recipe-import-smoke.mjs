// Portable README downloads -> new tabs -> save/reload/export, isolated from live state.
import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
const root=resolve(import.meta.dirname,'../..');await mkdir(root+'/build',{recursive:true});
const state=await mkdtemp(root+'/build/recipe-import-'),socket=createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
const base=`http://127.0.0.1:${port}`;
const server=spawn(process.env.PLOTLINE_TEST_BINARY||root+'/controller/target/debug/plotline-studios',['--listen',`127.0.0.1:${port}`,'--state',state+'/store'],{cwd:root,env:{...process.env,PLOTLINE_PDF_PROJECT:root},stdio:['ignore','ignore','pipe']});let logs='',browser;
server.stderr.on('data',d=>logs+=d);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)),json=async path=>(await fetch(base+path)).json();
const fixture=async name=>JSON.parse(await readFile(`${root}/docs/readme/examples/${name}/${name==='hex-maze'?'maze':'pattern'}-recipe.json`));
try{
  for(let i=0;;i++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(i>100)throw Error(logs);await sleep(100);}
  const inventory=await json('/api/carousel');browser=await chromium.launch({headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),errors=[];
  context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
  for(const [studio,names] of [['patterns',['hilbert','plant','sierpinski','spirograph']],['mazes',['hex-maze']]]){
    const page=await context.newPage(),key=`plotline-${studio==='patterns'?'pattern':'maze'}-workspace`;
    await page.goto(base+'/'+studio);await page.locator('body[data-ready=true]').waitFor();
    const ready=()=>page.locator('#preview[data-ready=true]').waitFor({timeout:120000});await ready();
    const workspace=()=>page.evaluate(k=>JSON.parse(sessionStorage.getItem(k)),key);
    const active=async()=>{const w=await workspace();return w.documents.find(d=>d.key===w.active);};
    const original=await active();
    const open=async()=>{if(!await page.locator('#library').evaluate(e=>e.open))await page.locator('#open-library').click();};
    const choose=async(buffer,name='drawing.recipe.json')=>{
      await open();const chosen=page.waitForEvent('filechooser');await page.locator('#import-recipe').click();
      await (await chosen).setFiles({name,mimeType:'application/json',buffer:Buffer.isBuffer(buffer)?buffer:Buffer.from(JSON.stringify(buffer))});
    };
    const importRecipe=async(recipe)=>{
      const before=(await workspace()).documents.length;await choose(recipe);
      await page.waitForFunction(n=>document.querySelectorAll('#document-tabs [role=tab]').length===n,before+1);
      await page.locator('#library').waitFor({state:'hidden'});await ready();
      assert.deepEqual((await active()).recipe,recipe.recipe||recipe);
      assert.equal((await active()).saved,undefined);assert.equal((await active()).receipt,undefined);
      assert((await page.locator('#document-tabs [aria-selected=true]').textContent()).includes('*'));
    };
    for(const name of names)await importRecipe(await fixture(name));
    assert.deepEqual((await workspace()).documents.find(d=>d.key===original.key),original,'Existing unsaved tab survives imports');
    assert.equal((await json(`/api/${studio}/documents`)).documents.length,0,'Import does not save silently');
    const design=(await active()).recipe;
    // Actual file download, then reimport the exact downloaded bytes.
    await page.locator('.export-bar .tray>summary').click();const pending=page.waitForEvent('download');await page.locator('#download-recipe').click();
    const download=await pending,bytes=await readFile(await download.path());assert.deepEqual(JSON.parse(bytes),design);
    await page.locator('.close-tray').click();await importRecipe({id:'foreign-document',receipt:{ready:true},recipe:JSON.parse(bytes)});
    await page.locator('#save').click();await page.locator('#save-name').fill('Imported '+studio);await page.locator('#save-form button[type=submit]').click();
    await page.waitForFunction(()=>!document.querySelector('#document-tabs [aria-selected=true]').textContent.includes('*'));
    const saved=await active();assert(saved.saved.id);assert.notEqual(saved.saved.id,'foreign-document');
    await page.reload();await ready();assert.deepEqual((await active()).recipe,saved.recipe);assert.equal((await active()).saved.id,saved.saved.id);
    // Malformed/oversized/cross-Studio files never disturb the current tab or library.
    const invalid=[Buffer.from('{'),await fixture(studio==='patterns'?'hex-maze':'hilbert'),{...design,version:999},
      {...design,roles:[{label:'Missing ink',ink_id:'absent'}]},
      {...design,params:{...design.params,...(studio==='patterns'?{depth:99999,order:99999,turns:99999}:{columns:99999})}},Buffer.alloc(8*1024*1024+1,32)];
    // Use a known bounded generator parameter for the pattern error.
    if(studio==='patterns'){invalid[4]=await fixture('hilbert');invalid[4].params.depth=99999;}
    for(const bad of invalid){
      const before=await workspace();await choose(bad);await page.locator('#recipe-error:not([hidden])').waitFor();
      assert.deepEqual(await workspace(),before);assert(await page.locator('#library').evaluate(e=>e.open));
      assert.equal(await page.locator('#recipe-file').inputValue(),'');
    }
    await page.screenshot({path:state+'/'+studio+'-invalid.png'});
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:state+'/'+studio+'-mobile.png'});await page.setViewportSize({width:1440,height:1000});
    // Same picker can recover from an error; saved service identity is not imported.
    await importRecipe(design);
    if(studio==='mazes'){
      const masked=structuredClone(design);masked.name='Portable masked maze';delete masked.detail;
      masked.params={...masked.params,columns:12,rows:10,mask:{name:'ring',width:3,height:3,pixels:[1,1,1,1,0,1,1,1,1]}};
      masked.mask_source='old-installation-only.png';await importRecipe(masked);
      assert.equal((await active()).recipe.params.columns,12,'Legacy grids are not recomputed on import');
      assert(await page.locator('#prepare').isEnabled());
      // Export a PDF too: an inline mask must work without the original upload on this server.
      await page.locator('.export-bar .tray>summary').click();const pdf=page.waitForEvent('download',{timeout:120000});await page.locator('#download-pdf').click();
      const artifact=await pdf;assert((await readFile(await artifact.path())).subarray(0,5).equals(Buffer.from('%PDF-')));
    }
    // A clean browser can reopen the explicitly saved document from the library.
    const fresh=await browser.newContext(),reopened=await fresh.newPage();await reopened.goto(base+'/'+studio);
    await reopened.locator('body[data-ready=true]').waitFor();await reopened.locator('#open-library').click();
    await reopened.locator('#documents .document-card').filter({hasText:'Imported '+studio}).getByRole('button',{name:'Open',exact:true}).click();await reopened.locator('#preview[data-ready=true]').waitFor();
    const loaded=await reopened.evaluate(k=>{const w=JSON.parse(sessionStorage.getItem(k));return w.documents.find(d=>d.key===w.active);},key);
    assert.deepEqual(loaded.recipe,saved.recipe);assert.equal(loaded.saved.id,saved.saved.id);await fresh.close();
  }
  assert.deepEqual(await json('/api/carousel'),inventory,'Import never edits physical inventory');assert.deepEqual(errors,[]);
  console.log(JSON.stringify({result:'PASS',artifacts:state}));
}catch(error){console.error(logs);throw error;}finally{if(browser)await browser.close();server.kill();}
