// Recovery preview → Rust → isolated fake bridge. Never connects to stain.
import {chromium,firefox} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,writeFile,rm,mkdir,rename} from 'node:fs/promises';
import {createServer} from 'node:net';
import {tmpdir} from 'node:os';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {gunzipSync} from 'node:zlib';
import assert from 'node:assert/strict';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..'),temp=await mkdtemp(`${tmpdir()}/plotline-recovery-`);
await writeFile(`${temp}/controls.json`,JSON.stringify({delay_ms:0}));
await writeFile(`${temp}/connection.json`,JSON.stringify({fixture_dir:temp,plotting_enabled:true}));
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const url=`http://127.0.0.1:${port}`,headers={'Content-Type':'application/json','X-Plotline-Request':'1'};
const server=spawn(process.env.PLOTLINE_BINARY||`${root}/controller/target/release/plotline-studios`,['--state',`${temp}/jobs`,'--listen',`127.0.0.1:${port}`,'--device-config',`${temp}/connection.json`,'--device-helper',`${root}/controller/scripts/fake-plotter.py`],{stdio:['ignore','ignore','pipe']});
let browser,logs='';server.stderr.on('data',d=>logs+=d);
const get=async path=>{const r=await fetch(url+path);assert.equal(r.status,200,await r.clone().text());return r.json();};
const post=(path,body)=>fetch(url+path,{method:'POST',headers,body:JSON.stringify(body)});
const fixture=async()=>JSON.parse(await readFile(`${temp}/fixture.json`,'utf8'));
const setControls=async value=>{await writeFile(`${temp}/controls.tmp`,JSON.stringify(value));await rename(`${temp}/controls.tmp`,`${temp}/controls.json`);};
async function until(fn){for(let i=0;i<300;i++){if(await fn())return;await new Promise(r=>setTimeout(r,50));}throw Error('Timed out: '+logs);}
try{
  await until(async()=>{try{return (await fetch(url+'/api/status')).ok;}catch{return false;}});
  const carousel=await get('/api/carousel');
  for(const [slot,color,label] of [[1,'#000000','black'],[2,'#ff0000','red']])Object.assign(carousel.slots[slot],{state:'loaded',label,color,width_mm:1,speed_cm_s:5,force:4});
  assert.equal((await fetch(url+'/api/carousel',{method:'PUT',headers,body:JSON.stringify(carousel)})).status,200);
  let source='IN;PA;CO"pen id=1 r=0 g=0 b=0 width_mm=1";CO"pen id=2 r=255 g=0 b=0 width_mm=1";';
  for(let pen=1;pen<=2;pen++){
    source+=`SP${pen};`;
    for(let i=0;i<40;i++){const y=(i+(pen-1)*40)*100;source+=`PU0,${y};PD6000,${y};PU;`;}
  }
  source+='SP0;';
  const imported=await fetch(url+'/api/jobs?name=Recovery%20proof.hpgl&units_per_mm=40',{method:'POST',headers:{'X-Plotline-Upload':'1'},body:source});
  assert.equal(imported.status,201);const job=await imported.json(),setup=await get(`/api/jobs/${job.id}/setup`);
  const submitted=await post(`/api/jobs/${job.id}/plot`,{attempt:randomUUID(),setup});assert.equal(submitted.status,202);const ticket=await submitted.json();
  await until(async()=>(await get('/api/plotter')).device.execution?.state==='finished');
  const original=await get(`/api/plots/${ticket.id}/recovery`),program=await (await fetch(url+`/api/plots/${ticket.id}/program.hpgl`)).text();
  assert.equal(original.strokes.length,80);const before=await fixture();
  browser=await (process.env.PLOTLINE_BROWSER==='firefox'?firefox:chromium).launch({headless:true,args:process.env.PLOTLINE_BROWSER==='firefox'?[]:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1440,height:1050}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url+'/hpgl#'+job.id);await page.locator('#preview-canvas[data-ready="true"]').waitFor();
  await page.locator('#show-plotter').click();await page.waitForFunction(()=>!document.getElementById('plot-recover').disabled);
  await page.locator('#plot-recover').click();await page.locator('#recovery-canvas[data-ready="true"]').waitFor();
  assert.match(await page.locator('#recovery-attempt').textContent(),new RegExp(String(new Date().getFullYear())));
  assert(await page.locator('#recovery-pen-change').isChecked());
  const pixels=()=>page.locator('#recovery-canvas').evaluate(c=>{
    const p=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let black=0,orange=0,faint=0;
    for(let i=0;i<p.length;i+=4){if(p[i]<80&&p[i+1]<80&&p[i+2]<80)black++;if(p[i]>190&&p[i+1]>50&&p[i+1]<150&&p[i+2]<40)orange++;if(p[i]>210&&p[i]<225&&p[i]===p[i+1]&&p[i]===p[i+2])faint++;}
    return {black,orange,faint};
  });
  const initial=await pixels();assert(initial.orange>100);assert(initial.faint>1000);
  await page.locator('#recovery-scrub').focus();await page.keyboard.press('ArrowRight');await page.keyboard.press('Shift+ArrowRight');
  await page.locator('#recovery-canvas[data-cutoff="11"]').waitFor();assert.equal(await page.locator('#recovery-stroke').inputValue(),'12');
  const later=await pixels();assert(later.black>initial.black+1000,'Completed strokes are full strength');assert(later.faint<initial.faint,'Remaining strokes are faint');
  await page.keyboard.press('ArrowLeft');await page.locator('#recovery-canvas[data-cutoff="10"]').waitFor();
  await page.locator('#recovery-zoom-in').click();await page.waitForTimeout(100);
  assert.equal(await page.locator('#recovery-preview-fit').count(),1);await page.locator('#recovery-preview-fit').click();
  await mkdir(`${root}/build/controller`,{recursive:true});await page.screenshot({path:`${root}/build/controller/recovery-preview.png`});
  assert.equal((await fixture()).opens,before.opens,'Scrubbing, stepping and zooming do not open serial');
  assert.deepEqual((await fixture()).writes,before.writes,'Offline preview sends no commands');
  await page.locator('#recovery-close').click();
  await page.route(url+`/api/jobs/${job.id}/attempts`,route=>route.fulfill({contentType:'application/json',body:'[]'}),{times:1});
  await page.locator('#plot-recover').click();await page.locator('#recovery-error:not([hidden])').waitFor();
  assert.equal(await page.locator('#recovery-canvas').getAttribute('data-ready'),'false','An empty history cannot show the previous drawing');
  assert(await page.locator('#recovery-resume').isDisabled());await page.locator('#recovery-close').click();
  await page.locator('#plot-recover').click();await page.locator('#recovery-canvas[data-ready="true"]').waitFor();
  await page.locator('#recovery-stroke').fill('11');await page.locator('#recovery-stroke').blur();await page.locator('#recovery-canvas[data-cutoff="10"]').waitFor();
  const response=page.waitForResponse(r=>r.url()===url+`/api/plots/${ticket.id}/recovery`&&r.request().method()==='POST');
  await page.locator('#recovery-resume').click();const reply=await response;assert.equal(reply.status(),202,await reply.text());const repair=await reply.json();
  assert.equal(repair.recovery.attempt,ticket.id);assert.equal(repair.recovery.start_stroke,10);assert.equal(repair.recovery.sha256,original.submission.sha256);
  await page.waitForFunction(()=>document.getElementById('plot-status').textContent==='Paused');assert(await page.locator('#recovery-dialog').isHidden());
  const repaired=await (await fetch(url+`/api/plots/${repair.id}/program.hpgl`)).text();
  assert.equal(repaired,`PU;\nIN;\nPA;\nPU;\nSP1;\nVS5;\nFS4;\n${program.slice(original.strokes[10][0])}`);
  const now=await fixture(),newWrites=now.writes.slice(before.writes.length).join('');assert.equal(now.opens,before.opens+1);assert(!newWrites.includes('SP2;'),'No next-pen change buffered');assert(newWrites.endsWith('PU;\n'));
  assert(now.queries.slice(before.queries.length).includes('OA;\n'));
  assert(await page.locator('#plot-recover').isDisabled());
  await setControls({delay_ms:0,completion_hold:true});await page.locator('#plot-cancel').click();
  await page.waitForFunction(()=>document.getElementById('plot-status').textContent==='Cancelling… waiting for the pen to park');
  assert(await page.locator('#plot-complete').isHidden());assert(await page.locator('#plot-recover').isDisabled());assert(await page.locator('#plot-open').isDisabled());
  assert.equal(await page.locator('#plot-time').textContent(),'');
  await page.reload();await page.waitForFunction(()=>document.getElementById('plot-status').textContent==='Cancelling… waiting for the pen to park');
  assert(await page.locator('#plot-complete').isHidden());
  await setControls({delay_ms:0});await page.waitForFunction(()=>!document.getElementById('plot-recover').disabled);
  assert.match(await page.locator('#work-detail').textContent(),/Plot cancelled · pen parked/);assert(await page.locator('#plot-complete').isHidden());
  const parked=(await get('/api/plotter')).device.execution;assert.equal(parked.state,'finished');assert(parked.cancelled&&parked.completion);assert(parked.submitted_bytes<parked.total_bytes);
  assert((await fixture()).writes.at(-1).endsWith('PU;\nSP0;\n'));
  assert(!((await fixture()).writes.slice(before.writes.length).includes('PD;\n')));
  const repairedPreview=await get(`/api/plots/${repair.id}/recovery`);
  assert.equal(repairedPreview.submission.id,ticket.id);assert.equal(repairedPreview.start_stroke,10);assert.deepEqual(repairedPreview.strokes,original.strokes);
  await page.locator('#plot-recover').click();await page.locator('#recovery-canvas[data-ready="true"][data-cutoff="10"]').waitFor();
  assert.equal(await page.locator('#recovery-attempt').inputValue(),repair.id);
  assert.equal(await page.locator('#recovery-stroke').inputValue(),'11');assert.match(await page.locator('#recovery-position').textContent(),/of 80 · slot 1/);
  await page.locator('#recovery-stroke').fill('41');await page.locator('#recovery-stroke').blur();await page.locator('#recovery-canvas[data-cutoff="40"]').waitFor();
  assert.match(await page.locator('#recovery-position').textContent(),/of 80 · slot 2/);
  // Reopening/reloading retains original numbering and the saved restart point.
  await page.locator('#recovery-close').click();await page.reload();await page.waitForFunction(()=>!document.getElementById('plot-recover').disabled);
  if(await page.locator('#show-plotter').getAttribute('aria-expanded')!=='true')await page.locator('#show-plotter').click();
  await page.locator('#plot-recover').click();await page.locator('#recovery-canvas[data-ready="true"][data-cutoff="10"]').waitFor();
  await page.locator('#recovery-stroke').fill('12');await page.locator('#recovery-stroke').blur();
  const secondResponse=page.waitForResponse(r=>r.url()===url+`/api/plots/${ticket.id}/recovery`&&r.request().method()==='POST');
  await page.locator('#recovery-resume').click();const secondReply=await secondResponse;assert.equal(secondReply.status(),202);const second=await secondReply.json();
  assert.equal(second.recovery.attempt,ticket.id);assert.equal(second.recovery.start_stroke,11);
  await page.waitForFunction(()=>document.getElementById('plot-status').textContent==='Paused');await page.locator('#plot-cancel').click();await page.waitForFunction(()=>!document.getElementById('plot-recover').disabled);
  await page.locator('#plot-recover').click();await page.locator('#recovery-canvas[data-ready="true"][data-cutoff="11"]').waitFor();
  assert.equal(await page.locator('#recovery-stroke').inputValue(),'12');assert.match(await page.locator('#recovery-position').textContent(),/of 80 · slot 1/);await page.locator('#recovery-close').click();
  if(process.env.PLOTLINE_RECOVERY_ARCHIVE){
    // Optional read-only replay of a large real archive, copied into this fake
    // spool. Never send that drawing, even to the fixture.
    const archive=process.env.PLOTLINE_RECOVERY_ARCHIVE;
    const saved=JSON.parse(await readFile(`${archive}/record.json`,'utf8'));
    let bytes;try{bytes=await readFile(`${archive}/prepared.hpgl`);}catch{bytes=gunzipSync(await readFile(`${archive}/prepared.hpgl.gz`));}
    saved.job_id=job.id;saved.created_at=Math.floor(Date.now()/1000)+1;
    await mkdir(`${temp}/jobs/plots/${saved.id}`);await writeFile(`${temp}/jobs/plots/${saved.id}/record.json`,JSON.stringify(saved));await writeFile(`${temp}/jobs/plots/${saved.id}/prepared.hpgl`,bytes);
    await page.locator('#plot-refresh').click();await page.waitForFunction(()=>!document.getElementById('plot-recover').disabled);
    const writes=await fixture(),began=performance.now();await page.locator('#plot-recover').click();await page.locator('#recovery-canvas[data-ready="true"]').waitFor();
    await page.locator('#recovery-attempt').selectOption(saved.id);await page.locator('#recovery-canvas[data-ready="true"]').waitFor();
    const loadedMs=performance.now()-began;
    await page.locator('#recovery-stroke').fill('1000');const moved=performance.now();await page.locator('#recovery-stroke').press('Enter');await page.locator('#recovery-stroke').blur();
    await page.locator('#recovery-canvas[data-cutoff="999"]').waitFor();
    console.log(`Archived ${bytes.length} bytes: first preview ${Math.round(loadedMs)} ms; cutoff repaint ${Math.round(performance.now()-moved)} ms.`);
    await page.screenshot({path:`${root}/build/controller/recovery-archive.png`});
    await page.locator('#recovery-close').click();assert.deepEqual((await fixture()).writes,writes.writes);assert.equal((await fixture()).opens,writes.opens);
  }
  assert.deepEqual(errors,[]);
  console.log('Recovery passed: archived program, full/faint/highlight pixels, exact stepping, shared zoom, no device I/O while inspecting, frozen stroke boundary, confirmed pen-up pause before next SP, cancel without lowering. No hardware contacted.');
}finally{
  await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));await rm(temp,{recursive:true,force:true});
}
