// Query-only preflight and startup failures against an isolated fake bridge.
import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,writeFile,readFile,readdir,rename} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
const root=resolve(import.meta.dirname,'../..');
await mkdir(root+'/build',{recursive:true});
const state=await mkdtemp(root+'/build/check-paper-');
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1','Content-Type':'application/json'},sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function controls(value){await writeFile(state+'/controls.tmp',JSON.stringify(value));await rename(state+'/controls.tmp',state+'/controls.json');}
await controls({});await writeFile(state+'/connection.json',JSON.stringify({fixture_dir:state,plotting_enabled:true}));
const server=spawn(process.env.PLOTLINE_BINARY||root+'/controller/target/release/plotline-studios',['--listen',`127.0.0.1:${port}`,'--state',state+'/jobs','--device-config',state+'/connection.json','--device-helper',root+'/controller/scripts/fake-plotter.py'],{stdio:['ignore','ignore','pipe']});let logs='',browser;
server.stderr.on('data',s=>logs+=s);
const fixture=async()=>JSON.parse(await readFile(state+'/fixture.json'));
async function api(path,body,method='POST'){const r=await fetch(base+path,body?{method,headers,body:JSON.stringify(body)}:{});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;}
try{
 for(let i=0;;i++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(i>100)throw Error(logs);await sleep(50);}
 const inventory=await api('/api/carousel');inventory.slots[1]={state:'loaded',label:'black',color:'#000000',width_mm:.3,speed_cm_s:5,force:4};await api('/api/carousel',inventory,'PUT');
 const upload=await fetch(base+'/api/jobs?name=Paper-check.hpgl&units_per_mm=40',{method:'POST',headers:{'X-Plotline-Request':'1'},body:'IN;SP1;PU0,0;PD4000,3000;PU;SP0;'});assert(upload.ok);const job=await upload.json();
 const denied=await fetch(base+'/api/plotter/check-paper',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});assert.equal(denied.status,403);
 browser=await chromium.launch();const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/hpgl#'+job.id);await page.waitForFunction(()=>!document.getElementById('plot-open').disabled);
 await page.locator('#show-plotter').click();
 const check=async()=>{await page.locator('#check-paper').click();await page.waitForFunction(()=>document.getElementById('check-paper').textContent==='Check plotter & paper');};
 await check();assert.match(await page.locator('#paper-check-result').textContent(),/15.75 × 9.84 in.*This drawing fits/);
 if(!await page.locator('#loaded-paper').evaluate(e=>e.open))await page.locator('#loaded-paper>summary').click();
 await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator('#paper-unit').selectOption('mm');
 assert.match(await page.locator('#paper-check-result').textContent(),/400 × 250 mm/);
 assert.match(await page.locator('#device-reading').textContent(),/400 × 250 mm/);
 assert.match(await page.locator('#placement-status').textContent(),/mm drawing area/);
 await page.locator('.studio-preferences').evaluate(e=>e.open=true);await page.locator('#paper-unit').selectOption('in');
 assert.match(await page.locator('#device-reading').textContent(),/15.75 × 9.84 in/);
 let f=await fixture();assert.equal(f.starts,0);assert.equal(f.opens,1);assert.deepEqual(f.queries,['OI;','OH;']);assert.deepEqual(f.writes,[]);
 assert(!(await readdir(state+'/jobs/plots')).some(n=>/^[0-9a-f]{8}-/.test(n)),'Check creates no plot attempt');
 const measured=await readFile(state+'/jobs/device-observation.json','utf8');
 assert.match(await page.locator('#sheet-size').textContent(),/17 × 11 in/);
 assert.match(await page.locator('#measured-size').textContent(),/15.75 × 9.84 in/);
 assert.equal(await page.locator('#paper-dimensions text').count(),6);
 await page.locator('#show-plotter').click();
 assert(await page.locator('#paper-comparison').isVisible(),'Comparison remains visible with drawer closed');
 await page.locator('#fit').click();await sleep(300);
 await page.screenshot({path:state+'/desktop.png'});
 await page.locator('#show-plotter').click();
 await controls({plotter_off:true});await check();assert.match(await page.locator('#work-detail').textContent(),/Stain connected; plotter not responding/);assert(await page.locator('#plot-open').isDisabled());
 assert(await page.locator('#paper-check-result').isHidden());assert.equal(await page.locator('#work-detail').evaluate(e=>getComputedStyle(e).color),'rgb(155, 56, 39)');
 assert.equal(await readFile(state+'/jobs/device-observation.json','utf8'),measured,'Failed query cannot refresh old measurement');
 await controls({query_delay_ms:7000});const started=Date.now();await page.locator('#check-paper').click();assert.equal(await page.locator('#paper-check-result').textContent(),'Querying the plotter.');await page.waitForFunction(()=>document.getElementById('check-paper').textContent==='Check plotter & paper');
 assert(Date.now()-started<9000,'A blocked query write has a short timeout');assert.match(await page.locator('#work-detail').textContent(),/plotter not responding/);
 await controls({limits:[-1000,-1000,1000,1000]});await check();
 assert.match(await page.locator('#work-detail').textContent(),/does not fit.*Planned sheet 17.00 × 11.00 in.*Measured drawable area 1.97 × 1.97 in/);
 assert.match(await page.locator('#paper-overflow').textContent(),/back.*0.98 in.*left.*0.49 in.*front.*0.98 in.*right.*0.49 in/);
 assert(await page.locator('#plot-open').isDisabled());
 assert(await page.locator('#export-plan').isEnabled(),'Recorded paper mismatch must not disable offline export');
 const offline=await fetch(base+'/api/jobs/'+job.id+'/prepared.hpgl',{method:'POST',headers,body:JSON.stringify(await api('/api/jobs/'+job.id+'/setup'))});assert(offline.ok);
 const smallObservation=JSON.parse(await readFile(state+'/jobs/device-observation.json','utf8'));
 await page.reload();await page.locator('#preview-canvas[data-ready=true]').waitFor();
 assert.match(await page.locator('#measured-size').textContent(),/1.97 × 1.97 in/);
 assert.match(await page.locator('#measurement-date').textContent(),/Measured.*sheet edges unknown/);
 assert.equal(JSON.parse(await readFile(state+'/jobs/device-observation.json','utf8')).observed_at,smallObservation.observed_at,'Reload does not imply a fresh query');
 if(await page.locator('#show-plotter').getAttribute('aria-expanded')!=='true')await page.locator('#show-plotter').click();
 await controls({});await check();assert(await page.locator('#plot-open').isEnabled());
 assert(await page.locator('#paper-check-result').isVisible());assert.equal(await page.locator('#work-detail').evaluate(e=>e.classList.contains('invalid')),false);
 assert.equal((await fixture()).starts,0);
 // Changed paper and power loss AFTER a successful check are independently
 // caught by Plot. Both refusals precede any drawing commands.
 await controls({limits:[-1000,-1000,1000,1000]});await page.locator('#plot-open').click();
 await page.waitForFunction(()=>document.getElementById('work-detail').textContent.includes('Plot was not started'),null,{timeout:15000});
 assert.match(await page.locator('#plot-error').textContent(),/Measured drawable area 1.97 × 1.97 in/);
 assert.equal((await fixture()).starts,0);assert.deepEqual((await fixture()).writes,[],'Fresh refusal precedes every movement byte');
 await sleep(1100); // Stored observations have whole-second timestamps.
 await controls({});await check();
 await page.reload();await page.locator('#preview-canvas[data-ready=true]').waitFor();
 await sleep(300); // Allow the first status poll to return the older plot receipt.
 assert.match(await page.locator('#measured-size').textContent(),/15.75 × 9.84 in/,'An older failed-plot observation cannot replace a newer paper check');
 if(await page.locator('#show-plotter').getAttribute('aria-expanded')!=='true')await page.locator('#show-plotter').click();
 await check();
 await controls({plotter_off:true});await page.locator('#plot-open').click();
 await page.waitForFunction(()=>document.getElementById('plot-error').textContent.includes('No plotter reply'),null,{timeout:15000});
 assert.match(await page.locator('#plot-error').textContent(),/No plotter reply/);assert(await page.locator('#plot-error').isVisible());assert.equal((await fixture()).starts,0);
 assert.deepEqual(errors,[]);console.log('PASS query-only check, inch dimensions, power-off error, paper mismatch, recovery, and visible startup failure. No hardware contacted.',state);
}catch(e){console.error(logs);throw e;}finally{await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));}
