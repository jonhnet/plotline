// Registered pages with a nonzero origin, fake OH, and an offset window of the
// same size. Seed immutable converted-job fixtures; no converter/hardware needed.
import {chromium,firefox} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,writeFile,rename,readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {randomUUID} from 'node:crypto';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
const root=resolve(import.meta.dirname,'../..');
await mkdir(root+'/build',{recursive:true});
const state=await mkdtemp(root+'/build/paper-fit-');
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1','Content-Type':'application/json'},sleep=ms=>new Promise(r=>setTimeout(r,ms));
const limits=[0,-2000,8000,2000];
await writeFile(state+'/controls.json',JSON.stringify({limits}));
await writeFile(state+'/connection.json',JSON.stringify({fixture_dir:state,plotting_enabled:true}));
const server=spawn(process.env.PLOTLINE_BINARY||root+'/controller/target/release/plotline-studios',['--listen',`127.0.0.1:${port}`,'--state',state+'/jobs','--device-config',state+'/connection.json','--device-helper',root+'/controller/scripts/fake-plotter.py'],{stdio:['ignore','ignore','pipe']});
let browser,logs='';server.stderr.on('data',s=>logs+=s);
async function api(path,body,method='POST'){const r=await fetch(base+path,body?{method,headers,body:JSON.stringify(body)}:{});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;}
const near=(actual,want)=>actual.forEach((n,i)=>assert(Math.abs(n-want[i])<.001,`${actual} != ${want}`));
try{
 for(let i=0;;i++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(i>100)throw Error(logs);await sleep(50);}
 const inventory=await api('/api/carousel');inventory.slots[1]={state:'loaded',label:'black',color:'#000000',width_mm:.3,speed_cm_s:5,force:4};await api('/api/carousel',inventory,'PUT');
 await api('/api/plotter/check-paper',{});
 // Persist a deliberately old reading. A reload may show it but cannot refresh
 // its timestamp or treat it as fresh hardware acceptance.
 const observedAt='2026-01-01T12:00:00Z';
 await writeFile(state+'/jobs/device-observation.json',JSON.stringify({identity:'7585B',units_per_mm:40,limits,observed_at:observedAt}));
 browser=await(process.env.PLOTLINE_BROWSER==='firefox'?firefox:chromium).launch();
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const source='IN;CO"pen id=1 r=0 g=0 b=0 width_mm=0.3";SP1;PU4400,-1600;PD11600,-1600,11600,1600,4400,1600,4400,-1600;PU;SP0;';
 let lastJob;
 for(const rotation of [0,90,180,270]){
  const uploaded=await fetch(base+'/api/jobs?name=Registered-'+rotation+'.hpgl&units_per_mm=40',{method:'POST',headers:{'X-Plotline-Request':'1'},body:source});assert(uploaded.ok);
  const job=await uploaded.json();lastJob=job;
  job.pdf={document_id:randomUUID(),conversion_id:randomUUID(),page_index:0,raster_preview:false,registration:{
   sheet_size_mm:rotation%180?[140,240]:[240,140],rotation,limits:[4000,-2000,12000,2000],
   sheet_to_device_units:({0:[40,0,0,-40,3200,2800],90:[0,40,40,0,3200,-2800],180:[-40,0,0,40,12800,-2800],270:[0,-40,-40,0,12800,2800]})[rotation],evidence:'Isolated fixture'}};
  await writeFile(state+'/job.tmp',JSON.stringify(job));await rename(state+'/job.tmp',state+'/jobs/'+job.id+'/job.json');
  const setup=await api('/api/jobs/'+job.id+'/setup');
  const before=await fetch(base+'/api/jobs/'+job.id+'/prepared.hpgl',{method:'POST',headers,body:JSON.stringify(setup)});assert(before.ok);assert.match(await before.text(),/PU4400,-1600;/);
  // The source conversion may have been archived independently of its HPGL job.
  await page.goto(base+'/hpgl#'+job.id);await page.locator('#preview-canvas[data-ready=true]').waitFor();
  if(await page.locator('#show-pens').getAttribute('aria-expanded')==='true')await page.locator('#show-pens').click();
  assert.equal(await page.locator('#sheet-size-label').textContent(),'Document sheet');
  assert.equal(await page.locator('#sheet-size').textContent(),'9.45 × 5.51 in','Comparison dimensions use physical feed/carriage axes');
  assert.match(await page.locator('#measurement-date').textContent(),/2026/);
  assert.match(await page.locator('#paper-overflow').textContent(),/front \(\+X\) by 3.54 in/);
  assert(await page.locator('#export-plan').isEnabled());
  for(const facing of ['artwork','plotter']){
   if(facing==='plotter')await page.locator('#view-facing').click();
   await page.locator('#fit').click();await sleep(200);
   const box=await page.locator('#measured-window').evaluate(e=>['x','y','width','height'].map(a=>Number(e.getAttribute(a))));
   const expected=facing==='plotter'?[-50,0,100,200]:({0:[0,-50,200,100],90:[-50,0,100,200],180:[-200,-50,200,100],270:[-50,-200,100,200]})[rotation];near(box,expected);
   const lengths=await page.locator('#measured-dimensions text').allTextContents();
   assert.deepEqual(lengths,(facing==='plotter'||rotation%180)?['3.94 in','7.87 in']:['7.87 in','3.94 in']);
  }
 }
 await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator('#paper-unit').selectOption('mm');
 assert.equal(await page.locator('#sheet-size').textContent(),'240 × 140 mm');
 assert.equal(await page.locator('#measured-size').textContent(),'200 × 100 mm');
 assert.deepEqual(await page.locator('#measured-dimensions text').allTextContents(),['100 mm','200 mm']);
 assert.match(await page.locator('#paper-overflow').textContent(),/90 mm/);
 await page.locator('#fit').click();await sleep(250);await page.screenshot({path:state+'/desktop.png'});
 // Mobile annotations must stay inside the viewport and not collide with one
 // another. Compare transformed screen boxes, including vertical dimension text.
 await page.setViewportSize({width:420,height:900});await page.locator('#fit').click();await sleep(250);
 assert(await page.locator('#paper-comparison').isVisible());
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 const viewport=await page.locator('#viewport').boundingBox(),labels=await page.locator('#paper-dimensions text').evaluateAll(es=>es.map(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};}));
 const controls=await page.locator('#preview-tools').boundingBox();
 for(const l of labels){assert(l.x+l.width<=controls.x||l.y>=controls.y+controls.height,'A dimension is hidden under the floating controls');assert(l.x>=viewport.x&&l.y>=viewport.y&&l.x+l.width<=viewport.x+viewport.width&&l.y+l.height<=viewport.y+viewport.height,JSON.stringify({l,viewport}));}
 for(let i=0;i<labels.length;i++)for(let j=i+1;j<labels.length;j++){
  const a=labels[i],b=labels[j];assert(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y,'Dimension labels overlap');
 }
 await page.screenshot({path:state+'/mobile.png'});
 // Fresh fake measurement produces the same positional refusal, not a guessed sheet size.
 const setup=await api('/api/jobs/'+lastJob.id+'/setup');
 const check=await api('/api/plotter/check-paper',{job_id:lastJob.id,setup});
 assert.match(check.fit_error,/Document sheet 9.45 × 5.51 in.*front \(\+X\) by 3.543 in \(90.00 mm\)/);
 assert.notEqual(check.observation.observed_at,observedAt);
 const fixture=JSON.parse(await readFile(state+'/fixture.json'));assert.equal(fixture.starts,0);assert.deepEqual(fixture.writes,[]);
 assert.deepEqual(errors,[]);console.log('PASS registered paper diagnostics, offset windows, four rotations/both views, persisted timestamps, units, offline export, desktop/mobile.',state);
}catch(e){console.error(logs);throw e;}finally{await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));}
