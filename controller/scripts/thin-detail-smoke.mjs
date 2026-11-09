// Real font PDF -> isolated converter -> browser overlay. No physical device.
import {chromium} from 'playwright';
import {spawn,execFileSync} from 'node:child_process';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
const root=resolve(import.meta.dirname,'../..'),state=await mkdtemp(root+'/build/thin-browser-');
execFileSync(root+'/.venv/bin/python',[root+'/tools/make_thin_fixture.py',state+'/fixture']);
const listener=createServer();await new Promise(r=>listener.listen(0,'127.0.0.1',r));const port=listener.address().port;await new Promise(r=>listener.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1','Content-Type':'application/json'},sleep=ms=>new Promise(r=>setTimeout(r,ms));
const server=spawn(process.env.PLOTLINE_BINARY||root+'/controller/target/release/plotline-studios',['--listen',`127.0.0.1:${port}`,'--state',state],{cwd:root,stdio:['ignore','ignore','pipe']});let logs='',browser;
server.stderr.on('data',s=>logs+=s);
async function api(path,body){const r=await fetch(base+path,body?{method:'POST',headers,body:JSON.stringify(body)}:{});const value=await r.json();assert(r.ok,JSON.stringify(value));return value;}
async function ready(path){for(let i=0;i<300;i++){const v=await api(path);if(!['received','inspecting','queued','running'].includes(v.status)){assert.equal(v.status,'ready',v.error);return v;}await sleep(100);}throw Error('Worker timed out');}
try{
  for(let i=0;;i++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(i>100)throw Error(logs);await sleep(50);}
  assert.equal((await api('/api/v1/capabilities')).implemented.thin_detail_thickening,true);
  const inventory=await api('/api/carousel');Object.assign(inventory.slots[1],{state:'loaded',label:'Black',color:'#000000',width_mm:.5});
  const inventoryResponse=await fetch(base+'/api/carousel',{method:'PUT',headers,body:JSON.stringify(inventory)});assert(inventoryResponse.ok);
  const intent=JSON.parse(await readFile(state+'/fixture/thin-labels.print-intent.json'));
  // Keep the fixture's clip and origin, on a supported Letter loading.
  Object.assign(intent.pages[0].target_media,{sheet_size_mm:[279.4,215.9],orientation_deg:90});
  const body=new FormData();body.append('document',new Blob([await readFile(state+'/fixture/thin-labels.pdf')]),'Thin labels.pdf');body.append('intent',new Blob([JSON.stringify(intent)]),'intent.json');
  let response=await fetch(base+'/api/v1/documents',{method:'POST',headers:{'X-Plotline-Request':'1'},body});assert(response.ok);const document=await ready((await response.json()).status_url);
  const strict=await ready((await api(`/api/v1/documents/${document.id}/conversions`,intent)).status_url);
  await api(`/api/v1/conversions/${strict.id}/publish`,{});
  browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));let posts=0;
  page.on('request',r=>{if(r.method()==='POST'&&/\/(conversions|draft|publish)$/.test(r.url()))posts++;});
  const legacyRoute='**/api/v1/documents/'+document.id;
  await page.route(legacyRoute,async route=>{const response=await route.fetch(),value=await response.json();const conversion=value.conversions.find(c=>c.id===strict.id);conversion.result.pages[0].detail_loss.version=1;await route.fulfill({json:value});});
  await page.goto(base+`/pdf?document=${document.id}&conversion=${strict.id}`);await page.waitForFunction(()=>document.querySelector('#work-status').dataset.phase==='ready');
  assert(await page.getByRole('button',{name:'Update pen preview',exact:true}).isEnabled(),'An older matching preview must be refreshable for diagnostics');assert(await page.locator('#show-loss').isDisabled());
  await page.unroute(legacyRoute);await page.reload();await page.waitForFunction(()=>document.querySelector('#work-status').dataset.phase==='ready');
  await page.locator('#preview').evaluate(i=>i.decode());assert.equal(await page.locator('#thin-detail').inputValue(),'0');assert(await page.locator('#convert-button').isDisabled());
  assert.equal(strict.result.pages[0].detail_loss.version,2);assert(strict.result.pages[0].detail_loss.zones>0);await page.locator('#show-loss').check();await page.locator('#loss-overlay').waitFor({state:'visible'});
  assert(await page.locator('#convert-button').isDisabled(),'Overlay is a view preference, not a conversion setting');assert.equal(posts,0);
  const aligns=async()=>assert(await page.evaluate(()=>{const a=document.querySelector('#preview').getBoundingClientRect(),b=document.querySelector('#loss-overlay').getBoundingClientRect();return ['x','y','width','height'].every(k=>Math.abs(a[k]-b[k])<1);}), 'Loss mask must share exact preview registration');
  await aligns();await page.screenshot({path:state+'/strict-with-loss.png',fullPage:true});
  const a=await page.locator('.canvas').screenshot();await sleep(175);const b=await page.locator('.canvas').screenshot();assert(!a.equals(b),'Barberpole texture should animate');
  await page.emulateMedia({reducedMotion:'reduce'});await sleep(100);const c=await page.locator('.canvas').screenshot();await sleep(175);const d=await page.locator('.canvas').screenshot();assert(c.equals(d),'Reduced-motion preference should stop the stripes');
  for(const mode of ['source','logical','pen']){await page.locator('#view').selectOption(mode);await page.locator('#preview').evaluate(i=>i.decode());await aligns();}
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();await aligns();await page.getByRole('button',{name:'Fit page',exact:true}).click();
  await page.locator('#thin-detail').fill('0.75');assert(await page.locator('#convert-button').isEnabled());
  await page.locator('#convert-button').click();await page.waitForURL(u=>u.searchParams.get('conversion')!==strict.id);await page.waitForFunction(()=>document.querySelector('#work-status').dataset.phase==='ready');
  const recovered=await api('/api/v1/conversions/'+new URL(page.url()).searchParams.get('conversion'));
  assert.equal(recovered.intent.policies.thin_detail_thickening,.75);assert(recovered.result.pages[0].detail_loss.recovered_paints>0);
  assert(recovered.result.pages[0].detail_loss.affected_paints<strict.result.pages[0].detail_loss.affected_paints);
  await page.locator('#loss-overlay').waitFor({state:'visible'});assert((await page.locator('#loss-overlay').getAttribute('data-url')).includes(recovered.id));await aligns();
  await page.locator('#show-loss').uncheck();assert(await page.locator('#loss-overlay').isHidden());await page.screenshot({path:state+'/recovered.png',fullPage:true});
  await page.reload();await page.waitForFunction(()=>document.querySelector('#work-status').dataset.phase==='ready');assert.equal(await page.locator('#thin-detail').inputValue(),'0.75');
  assert(await page.locator('#convert-button').isDisabled());assert.deepEqual(errors,[]);
  // A new PDF without a supplied intent gets the Studio default. Existing
  // intents above still interpret an omitted allowance as zero on reload.
  const freshBody=new FormData();freshBody.append('document',new Blob([await readFile(state+'/fixture/thin-labels.pdf')]),'New labels.pdf');
  const freshResponse=await fetch(base+'/api/v1/documents',{method:'POST',headers:{'X-Plotline-Request':'1'},body:freshBody});assert(freshResponse.ok);
  const fresh=await ready((await freshResponse.json()).status_url);
  await page.goto(base+`/pdf?document=${fresh.id}`);await page.waitForFunction(()=>document.querySelector('#document-detail').textContent.includes('New labels.pdf'));
  assert.equal(await page.locator('#thin-detail').inputValue(),'0.75');
  assert.equal(await page.locator('#stroke-mode').inputValue(),'preserve');
  await page.locator('#thin-detail').fill('0');await page.locator('#thin-detail').blur();
  await page.locator('#stroke-mode').selectOption('pen');
  await page.reload();await page.waitForFunction(()=>document.querySelector('#document-detail').textContent.includes('New labels.pdf'));
  assert.equal(await page.locator('#thin-detail').inputValue(),'0','An explicit zero survives workspace reload');
  assert.equal(await page.locator('#stroke-mode').inputValue(),'pen','An explicit single-pass choice survives workspace reload');
  assert.deepEqual(errors,[]);
  await writeFile(state+'/result.json',JSON.stringify({strict:strict.id,recovered:recovered.id,strictLoss:strict.result.pages[0].detail_loss,recoveredLoss:recovered.result.pages[0].detail_loss},null,2));
  console.log('PASS new-document defaults, preserved zero, real-label recovery, policy persistence, missing fragments, registered overlay in all views/zooms, actual barberpole animation, reduced motion, no overlay in print data',state);
}catch(error){console.error(logs);throw error;}finally{await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));}
