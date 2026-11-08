// Browser → Rust → isolated fake serial bridge. Never contacts stain.
import { chromium } from "playwright";
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm, rename, mkdir } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash, randomUUID } from "node:crypto";
import assert from "node:assert/strict";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const temp = await mkdtemp(`${tmpdir()}/plotline-plot-ui-`);
const setControls = async (v) => {
  await writeFile(`${temp}/controls.tmp`, JSON.stringify(v));
  await rename(`${temp}/controls.tmp`, `${temp}/controls.json`);
};
await setControls({query_delay_ms:300,delay_ms:100});
await writeFile(`${temp}/connection.json`, JSON.stringify({fixture_dir:temp,plotting_enabled:true,
  carousel:{2:{label:"red",available:true},3:{label:"broken",available:false},5:{label:"green",available:true}}}));
const reserve=createServer(); await new Promise(r=>reserve.listen(0,"127.0.0.1",r));
const port=reserve.address().port; await new Promise(r=>reserve.close(r));
const url=`http://127.0.0.1:${port}`;
const serverArgs=["--state",`${temp}/jobs`,"--listen",`127.0.0.1:${port}`,"--device-config",`${temp}/connection.json`,"--device-helper",`${root}/controller/scripts/fake-plotter.py`];
const startServer=()=>spawn(process.env.PLOTLINE_BINARY || `${root}/controller/target/release/plotline-studios`,serverArgs,{stdio:["ignore","ignore","pipe"]});
let server=startServer(),browser,logs="";
server.stderr.on("data",d=>logs+=d);
const headers={"Content-Type":"application/json","X-Plotline-Request":"1"};
const fixture=async()=>JSON.parse(await readFile(`${temp}/fixture.json`,"utf8"));
const post=(path,body={})=>fetch(url+path,{method:"POST",headers,body:JSON.stringify(body)});
const traceFor=async(ticket)=>{
  const base=`${temp}/jobs/plots/${ticket.id}`;
  const trace=(await readFile(`${base}/serial-timing.jsonl`,'utf8')).trim().split('\n').map(JSON.parse);
  const source=await readFile(`${base}/prepared.hpgl`);
  assert.equal(trace[0].metadata.attempt,ticket.id);
  assert.equal(trace[0].metadata.prepared_sha256,createHash('sha256').update(source).digest('hex'));
  assert.equal(trace[0].pi_timing_available,true);
  let streamOffset=0,lastTime=0;
  for(const event of trace) {
    if(event.event==='write') {
      assert.equal(event.stream_offset,streamOffset);
      streamOffset+=event.data.length;
      if(event.kind==='prepared') assert.deepEqual(Buffer.from(event.data),source.subarray(event.prepared_offset,event.prepared_offset+event.data.length));
    }
    if(event.event==='tx_queue_timing_v1') for(const row of event.samples) {
      assert.ok(row[0]>=lastTime);lastTime=row[0];
      assert.ok(row[1]<=row[0]);assert.ok(row[2]<=streamOffset);
    }
  }
  assert.ok(trace.some(e=>e.event==='tx_queue_timing_v1'));
  const inspected=spawnSync('python3',[`${root}/controller/scripts/inspect-timing.py`,base],{encoding:'utf8'});
  assert.equal(inspected.status,0,inspected.stderr);
  assert.equal(JSON.parse(inspected.stdout).attempt,ticket.id);
  return trace;
};
const readyServer=async()=>{
  for(let i=0;;i++) {
    try { if((await fetch(`${url}/api/status`)).ok) return; } catch {}
    if(i>100) throw Error(logs);
    await new Promise(r=>setTimeout(r,50));
  }
};
try {
  await readyServer();
  const carousel=await (await fetch(`${url}/api/carousel`)).json();
  Object.assign(carousel.slots[2],{width_mm:.7,speed_cm_s:9,force:2});
  assert.equal((await fetch(`${url}/api/carousel`,{method:"PUT",headers,body:JSON.stringify(carousel)})).status,200);
  const source='IN;PA;CO"pen id=2 r=255 g=0 b=0 width_mm=0.7";SP2;VS9;FS2;PU0,0;'+'PD4000,3000;PD0,0;'.repeat(1000)+'PU;SP0;';
  const uploaded=await fetch(`${url}/api/jobs?name=Red%20proof.hpgl&units_per_mm=40`,{method:"POST",headers:{"X-Plotline-Upload":"1"},body:source});
  assert.equal(uploaded.status,201); const job=await uploaded.json();
  const endpoint=`/api/jobs/${job.id}/plot`;
  browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
  const page=await browser.newPage({viewport:{width:1440,height:1120}});
  const errors=[]; page.on("pageerror",e=>errors.push(e.message));
  // A failed model request must be recoverable without clicking Plot or
  // changing settings. This one injected HTTP failure sends no device work.
  await page.route(`${url}/api/jobs/${job.id}/estimate`,route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Timing worker temporarily unavailable'})}),{times:1});
  await page.goto(`${url}/#${job.id}`);
  await page.locator('#preview-canvas[data-ready="true"]').waitFor();
  const enabled=()=>page.waitForFunction(()=>!document.getElementById("plot-open").disabled);
  const refresh=async()=>{
    const response=page.waitForResponse(r=>r.url()===`${url}/api/plotter`);
    if(await page.locator("#show-plotter").getAttribute("aria-expanded")!=="true")await page.locator("#show-plotter").click();
    await page.locator("#plot-refresh").click(); await response;
  };
  const submit=async()=>{
    const response=page.waitForResponse(r=>r.url()===url+endpoint);
    await page.locator("#plot-open").click();
    const reply=await response;
    assert.equal(reply.status(),202);
    return {ticket:await reply.json(),request:reply.request().postDataJSON()};
  };
  const finish=async()=>{
    await page.waitForFunction(()=>!document.getElementById("plot-open").disabled || !document.getElementById("plot-complete").hidden);
    if(await page.locator("#plot-complete").isVisible())await page.locator("#plot-complete").click();
    await enabled();
  };
  await enabled();
  assert.equal(await page.locator("#plot-open").textContent(),"Plot");
  await page.waitForFunction(()=>document.getElementById('plot-estimate').textContent.includes('Timing worker temporarily unavailable'));
  await page.locator('#show-plotter').click();
  assert(await page.locator('#estimate-retry').isVisible());
  assert.equal((await fixture()).starts,0,'A failed estimate never starts plotting');
  await page.locator('#estimate-retry').click();
  await page.waitForFunction(()=>document.getElementById('plot-estimate').textContent.startsWith('Estimated plot time:'));
  assert(await page.locator('#estimate-retry').isHidden());
  const estimateSetup=await (await fetch(`${url}/api/jobs/${job.id}/setup`)).json();
  const previewEstimate=await (await post(`/api/jobs/${job.id}/estimate`,estimateSetup)).json();
  assert.equal(previewEstimate.model,'hp7585-snoqualmie-v1');
  assert.ok(previewEstimate.motion_seconds>previewEstimate.feed_seconds);
  assert.equal(previewEstimate.speed_extrapolation,true);
  assert.equal((await fixture()).starts,0,'Preview estimates never feed hardware');
  assert.equal(await page.locator("#plot-dialog, #plot-start").count(),0);
  await page.locator('#show-pens').click();
  const unavailable=await (await fetch(url+'/api/carousel')).json();unavailable.slots[2].state='unavailable';
  await fetch(url+'/api/carousel',{method:'PUT',headers,body:JSON.stringify(unavailable)});await page.locator('#carousel-refresh').click();
  await page.waitForFunction(()=>document.getElementById('plot-open').disabled);
  assert.equal(await page.locator("#plot-open").isDisabled(),true);
  assert.match(await page.locator("#plot-issue").textContent(),/unavailable/);
  const restored=await (await fetch(url+'/api/carousel')).json();restored.slots[2].state='loaded';
  await fetch(url+'/api/carousel',{method:'PUT',headers,body:JSON.stringify(restored)});await page.locator('#carousel-refresh').click();
  await enabled();
  let releaseOldStatus, oldStatusCaptured;
  const captured = new Promise(r=>oldStatusCaptured=r);
  const release = new Promise(r=>releaseOldStatus=r);
  await page.route(url+"/api/plotter",async route=>{
    const response=await route.fetch(); oldStatusCaptured();
    await release; await route.fulfill({response});
  },{times:1});
  if(await page.locator("#show-plotter").getAttribute("aria-expanded")!=="true")await page.locator("#show-plotter").click();
  await page.locator("#plot-refresh").click(); await captured;
  let {ticket,request}=await submit();
  releaseOldStatus();
  assert.equal(await page.locator("#plot-open").isDisabled(),true);
  assert.equal(ticket.execution.state,"connecting");
  assert.equal(await page.locator("#plot-open").textContent(),"Starting…");
  assert.equal((await fixture()).starts,0,"Starting connects before sending any drawing bytes");
  const repeats=await Promise.all([post(endpoint,request),post(endpoint,request)]);
  repeats.forEach(r=>assert.equal(r.status,202));
  assert.equal((await post(endpoint,{...request,attempt:randomUUID()})).status,422);
  const edited=structuredClone(request.setup); edited.rotation=180; edited.pen_map[2]=5;
  assert.equal((await fetch(`${url}/api/jobs/${job.id}/setup`,{method:"PUT",headers,body:JSON.stringify(edited)})).status,200);
  await page.locator("#plot-pause:not([hidden])").waitFor();
  let state=await fixture();
  assert.equal(state.starts,1); assert.equal(state.opens,1);
  assert.deepEqual(state.queries,["OI;","OH;"]);
  assert.match(state.writes.join(""),/SP2;\nVS9;\nFS2;/);
  assert.match(state.writes.join(""),/PU-2000,-1500;/);
  assert.doesNotMatch(state.writes.join(""),/CO|SP5;/);
  assert.equal(await page.locator('.document-tab[data-activity=running] .document-activity').evaluate(e=>getComputedStyle(e).animationName),'plotline-pen');
  await page.locator("#plot-pause").click();
  await page.waitForFunction(()=>document.getElementById("plot-status").textContent==="Paused");
  await page.waitForFunction(()=>document.getElementById('plot-time').textContent.includes('remaining'));
  // Changing the editor's document cannot change the physical attempt or its controls.
  const other=await (await fetch(`${url}/api/jobs?name=Other%20drawing.hpgl&units_per_mm=40`,{method:'POST',headers:{'X-Plotline-Upload':'1'},body:'IN;SP2;PU0,0;PD400,400;PU;SP0;'})).json();
  await page.locator('#open-library').click();await page.locator('#jobs .document-card').filter({hasText:'Other drawing'}).getByRole('button',{name:'Open',exact:true}).click();
  await page.waitForFunction(()=>document.title.includes('Other drawing'));await page.locator('#preview-canvas[data-ready="true"]').waitFor();
  assert.equal(new URL(page.url()).hash,'#'+other.id);assert(await page.locator('#plot-open').isDisabled());
  assert.match(await page.locator('#work-detail').textContent(),/Preview only/);
  await page.waitForFunction(()=>document.getElementById('plot-estimate').textContent.startsWith('Estimated plot time:'));
  assert(await page.locator('#plot-estimate').isVisible());assert(await page.locator('#plot-time').isHidden());
  assert(await page.locator('#plot-resume').isHidden());assert(await page.locator('#plot-panel').isHidden());
  assert.equal(await page.locator('#plot-progress').getAttribute('data-mode'),'preview');
  assert(await page.locator('#plot-progress-unsent').isHidden());assert(await page.locator('#plot-progress-marker').isHidden());
  assert(await page.locator('#show-active-plot').isVisible());
  assert.match(await page.getByRole('tab',{name:job.name.replace(/\.(hpgl|plt)$/i,''),exact:true}).getAttribute('aria-description'),/Paused/);
  await mkdir(`${root}/build/controller`,{recursive:true});
  await page.screenshot({path:`${root}/build/controller/other-document-preview.png`});
  const pausedWrites=(await fixture()).writes.join('');
  await page.locator('#plot-cancel').evaluate(e=>e.click());
  assert.equal((await fixture()).writes.join(''),pausedWrites,'Inactive document cannot control another document’s run');
  await page.locator('#show-active-plot').click();await page.locator('#preview-canvas[data-ready="true"]').waitFor();
  assert.equal(new URL(page.url()).hash,'#'+job.id);assert(await page.locator('#plot-resume').isVisible());
  assert.equal(await page.locator('#plot-progress').getAttribute('data-mode'),'active');
  // A second recipe for the same HPGL is a separate preview tab, too.
  const alternate=await (await post(`/api/jobs/${job.id}/recipes`,{name:'Another pen setup',setup:{...request.setup,rotation:90}})).json();
  assert(alternate.id);
  await page.evaluate(({id,recipe})=>{history.pushState(null,'',`/hpgl?recipe=${recipe}#${id}`);dispatchEvent(new HashChangeEvent('hashchange'));},{id:job.id,recipe:alternate.id});
  await page.waitForFunction(()=>document.title.includes('Another pen setup'));
  assert(await page.locator('#show-active-plot').isVisible());assert(await page.locator('#plot-resume').isHidden());
  assert.equal(await page.locator('.document-tab[data-activity]').count(),1);
  await page.reload();await page.locator('#show-active-plot').waitFor();
  assert.equal(await page.locator('.document-tab[data-activity]').count(),1);
  await page.locator('#show-active-plot').click();await page.waitForFunction(()=>document.getElementById('plot-resume').hidden===false);
  assert(!new URL(page.url()).searchParams.has('recipe'));
  // A clean browser workspace recovers a tab for the active job without
  // replacing the newly requested document or writing to the physical run.
  const fresh=await browser.newPage();await fresh.goto(url+'/hpgl#'+other.id);
  await fresh.locator('#show-active-plot').waitFor();
  await fresh.getByRole('tab',{name:job.name.replace(/\.(hpgl|plt)$/i,''),exact:true}).waitFor();
  assert.equal(new URL(fresh.url()).hash,'#'+other.id);
  await fresh.locator('#show-active-plot').click();await fresh.waitForURL(u=>u.hash==='#'+job.id);
  assert.equal((await fixture()).writes.join(''),pausedWrites);await fresh.close();
  const frozenTicket=(await (await fetch(`${url}/api/plots/${ticket.id}`)).json()).submission;
  assert.equal(frozenTicket.estimate.prepared_sha256,frozenTicket.sha256);
  const savedForecast=JSON.parse(await readFile(`${temp}/jobs/plots/${ticket.id}/timing-model.json`,'utf8'));
  assert.deepEqual(savedForecast.estimate,frozenTicket.estimate);
  const checkpoint=savedForecast.boundaries.find(b=>b.byte===frozenTicket.execution.submitted_bytes);
  assert.ok(Math.abs(frozenTicket.execution.timing.seconds-(savedForecast.estimate.motion_seconds-checkpoint.feed_seconds))<1e-8);
  await page.reload(); await page.locator("#plot-resume:not([hidden])").waitFor();
  assert.match(await page.locator('#plot-time').textContent(),/Paused.*remaining/);
  await page.locator("#plot-resume").click(); await page.locator("#plot-pause:not([hidden])").waitFor();
  await page.locator("#plot-cancel").click(); await finish();
  assert.match(await page.locator('#work-detail').textContent(),/Plot cancelled · pen parked/);
  assert(await page.locator('#plot-complete').isHidden());
  await page.getByRole('tab',{name:'Other drawing',exact:true}).click();await page.waitForFunction(()=>document.title.includes('Other drawing'));
  assert.equal(await page.locator('#plot-progress').getAttribute('data-mode'),'preview','A finished plot is not the progress of a different document');
  await page.getByRole('tab',{name:job.name.replace(/\.(hpgl|plt)$/i,''),exact:true}).click();await page.locator('#preview-canvas[data-ready="true"]').waitFor();
  const firstWrites=(await fixture()).writes.join("");
  const firstTrace=await traceFor(ticket);
  assert.equal(Buffer.from(firstTrace.filter(e=>e.event==='write'&&!['pause-query','cancel-query'].includes(e.kind)).flatMap(e=>e.data)).toString(),firstWrites);
  assert.deepEqual(firstTrace.filter(e=>e.kind==='pause-query').map(e=>Buffer.from(e.data).toString()),['OA;\n','OS;\n']);
  assert.deepEqual(firstTrace.filter(e=>e.kind==='cancel-query').map(e=>Buffer.from(e.data).toString()),['OA;\n','OS;\n']);
  assert.ok(firstTrace.some(e=>e.kind==='pause'));
  assert.ok(firstTrace.some(e=>e.kind==='resume'));
  assert.ok(firstTrace.some(e=>e.kind==='cancel'));
  await post(endpoint,request);
  assert.equal((await fixture()).writes.join(""),firstWrites);
  // A fresh query checks actual loaded paper, without a confirmation dialog.
  await setControls({limits:[-1000,-1000,1000,1000]});
  ({ticket,request}=await submit());
  await page.waitForFunction(()=>document.getElementById("plot-status").textContent.includes("does not fit"));
  await enabled();
  assert.equal((await fixture()).starts,1);
  assert.equal((await fixture()).opens,2);
  // A lost serial byte acknowledgement still requires operator reset, no replay.
  await setControls({lose_ack:true});
  ({ticket,request}=await submit());
  await page.locator("#plot-reset:not([hidden])").waitFor();
  assert.equal((await fixture()).starts,2);
  const partial=(await fixture()).writes.join("");
  const partialTrace=await traceFor(ticket);
  assert.ok(partialTrace.some(e=>e.event==='error'&&e.op==='write'));
  assert.ok(!partialTrace.some(e=>e.event==='reply'&&e.op==='write'));
  await page.reload(); await page.locator("#plot-reset:not([hidden])").waitFor();
  await post(endpoint,request);
  assert.equal((await fixture()).writes.join(""),partial);
  await setControls({delay_ms:0}); await refresh();
  await page.locator("#plot-reset").click(); await enabled();
  // Failure before HTTP delivery survives reload; Retry reuses the original ID.
  let missing;
  await page.route(url+endpoint,async route=>{missing=route.request().postDataJSON(); await route.abort();},{times:1});
  await page.locator("#plot-open").click();
  await page.locator("#plot-retry:not([hidden])").waitFor();
  assert.equal((await fixture()).starts,2);
  await page.reload(); await page.locator("#plot-retry:not([hidden])").waitFor();
  assert.equal(await page.locator("#plot-open").isDisabled(),true);
  let reply=page.waitForResponse(r=>r.url()===url+endpoint);
  await page.locator("#plot-retry").click();
  reply=await reply;
  assert.equal(reply.request().postDataJSON().attempt,missing.attempt);
  await finish(); assert.equal((await fixture()).starts,3);
  // Losing the HTTP reply after acceptance resolves from status, with no resend.
  let accepted;
  await page.route(url+endpoint,async route=>{
    accepted=route.request().postDataJSON();
    await route.fetch(); await route.abort();
  },{times:1});
  await page.locator("#plot-open").click(); await finish();
  assert.equal((await fixture()).starts,4);
  await post(endpoint,accepted);
  assert.equal((await fixture()).starts,4);
  assert.equal(await page.evaluate(()=>localStorage.getItem("plotline.pending-plot")),null);
  // All bytes sent does not enable another plot before the device's reply.
  await setControls({delay_ms:0,completion_hold:true,completion_chunk_bytes:2});
  ({ticket,request}=await submit());
  await page.waitForFunction(()=>document.getElementById('plot-status').textContent.startsWith('Finishing…'));
  assert.equal(await page.locator('#plot-open').isDisabled(),true);
  assert.equal(await page.locator('#plot-complete').isVisible(),false);
  const waiting=(await (await fetch(`${url}/api/plots/${ticket.id}`)).json()).submission;
  assert.equal(waiting.execution.submitted_bytes,waiting.bytes);
  const opensBefore=(await fixture()).opens;
  await page.reload();
  await page.waitForFunction(()=>document.getElementById('plot-status').textContent.startsWith('Finishing…'));
  assert.equal((await fixture()).opens,opensBefore,'Reload must not open another serial connection');
  // Advance the status sample beyond the estimated tail while OA stays held.
  // This long-vector fixture can have minutes of modeled buffered movement.
  await page.route(url+'/api/plotter',async route=>{
    const response=await route.fetch(),body=await response.json();
    const timing=body.device.execution.timing;
    body.server_time_ms=timing.updated_at_ms+timing.seconds*1000+1000;
    await route.fulfill({response,json:body});
  },{times:1});
  await refresh();
  await page.waitForFunction(()=>document.getElementById('plot-time').textContent.includes('Estimate elapsed'));
  assert.equal(await page.locator('#plot-open').isDisabled(),true,'Expired ETA must not imply completion');
  assert.equal((await post(endpoint,{...request,attempt:randomUUID()})).status,422);
  assert.equal((await post('/api/plotter/complete',{attempt:ticket.id})).status,422);
  await setControls({delay_ms:0,completion_chunk_bytes:2});
  await page.waitForFunction(()=>document.getElementById('plot-status').textContent==='Plot finished · confirmed by plotter');
  await enabled();
  const confirmed=(await (await fetch(`${url}/api/plots/${ticket.id}`)).json()).submission;
  assert.deepEqual(confirmed.execution.completion.position,[-123,456]);
  assert.equal(confirmed.execution.completion.status,16);
  const completedTrace=await traceFor(ticket);
  assert.deepEqual(completedTrace.filter(e=>e.kind==='completion-query').map(e=>Buffer.from(e.data).toString()),['OA;\n','OS;\n']);
  assert.equal(confirmed.execution.submitted_bytes,confirmed.bytes,'Completion queries do not inflate drawing progress');
  const completedWrites=(await fixture()).writes.join('');
  await post(endpoint,request);assert.equal((await fixture()).writes.join(''),completedWrites);
  // A rejected completion reply has its own recovery state; no automatic replay.
  await setControls({delay_ms:0,completion_reply:'0,0,1\r'});
  ({ticket,request}=await submit());
  await page.locator('#plot-reset:not([hidden])').waitFor();
  assert.match(await page.locator('#plot-status').textContent(),/All drawing data was sent, but completion could not be confirmed/);
  assert.equal(await page.locator('#plot-open').isDisabled(),true);
  assert.equal(await page.locator('#plot-complete').isVisible(),false);
  const unresolvedWrites=(await fixture()).writes.join('');
  await page.reload();await page.locator('#plot-reset:not([hidden])').waitFor();
  await post(endpoint,request);assert.equal((await fixture()).writes.join(''),unresolvedWrites);
  await setControls({delay_ms:0});await page.locator('#plot-reset').click();await enabled();
  // Restarting a service never resumes or resends an interrupted plot.
  await setControls({delay_ms:500});
  ({ticket,request}=await submit());
  await page.locator("#plot-pause:not([hidden])").waitFor();
  server.kill("SIGKILL"); await new Promise(r=>server.once("exit",r));
  await new Promise(r=>setTimeout(r,700));
  const stopped=(await fixture()).writes.join("");
  server=startServer(); server.stderr.on("data",d=>logs+=d); await readyServer();
  await page.reload(); await page.locator("#plot-reset:not([hidden])").waitFor();
  assert.match(await page.locator("#plot-status").textContent(),/interrupted/i);
  await post(endpoint,request);
  assert.equal((await fixture()).writes.join(""),stopped);
  await page.locator("#plot-reset").click(); await enabled();
  await setControls({offline:true}); await refresh();
  await page.waitForFunction(()=>document.getElementById("plot-open").disabled);
  assert.match(await page.locator("#mode-label").getAttribute('title'),/offline/);
  await setControls({query_delay_ms:1000,delay_ms:100}); await refresh(); await enabled();
  assert.equal((await fetch(url+endpoint,{method:"POST",headers:{...headers,Origin:"https://unrelated.example"},body:JSON.stringify(request)})).status,403);
  assert.equal((await post(`/api/jobs/${job.id}/plot-plan`,edited)).status,410);
  assert.equal((await post(`/api/plots/${ticket.id}/start`)).status,410);
  assert.equal((await fixture()).starts,7);
  await page.setViewportSize({width:390,height:844});
  ({ticket,request}=await submit());
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await mkdir(`${root}/build/controller`,{recursive:true});
  await page.screenshot({path:`${root}/build/controller/plot-phone.png`,fullPage:true});
  // Cancel while still connecting: read-only queries may finish, but no drawing.
  await page.locator("#plot-cancel:not([hidden])").waitFor();
  await page.locator("#plot-cancel").click(); await finish();
  assert.equal((await fixture()).starts,7);
  // Pen bands describe the exact sent program, including a return to a pen.
  const pens=await (await fetch(`${url}/api/carousel`)).json();
  Object.assign(pens.slots[6],{label:"purple",state:"loaded",color:"#800080",width_mm:.5});
  assert.equal((await fetch(`${url}/api/carousel`,{method:"PUT",headers,body:JSON.stringify(pens)})).status,200);
  const marks=(pen,n)=>`SP${pen};PU0,0;`+"PD4000,3000;PD0,0;".repeat(n)+"PU;";
  const bandsSource='IN;PA;CO"pen id=1 r=0 g=0 b=0 width_mm=0.3";CO"pen id=2 r=255 g=0 b=0 width_mm=0.3";CO"pen id=3 r=0 g=128 b=0 width_mm=0.3";'+marks(1,800)+marks(2,200)+marks(1,400)+marks(3,100)+'SP0;';
  const bandsJob=await (await fetch(`${url}/api/jobs?name=Pen%20progress%20proof.hpgl&units_per_mm=40`,{method:"POST",headers:{"X-Plotline-Upload":"1"},body:bandsSource})).json();
  const bandsSetup=await (await fetch(`${url}/api/jobs/${bandsJob.id}/setup`)).json();
  bandsSetup.pen_map={1:6,2:2,3:5};
  assert.equal((await fetch(`${url}/api/jobs/${bandsJob.id}/setup`,{method:"PUT",headers,body:JSON.stringify(bandsSetup)})).status,200);
  await setControls({delay_ms:80});
  await page.setViewportSize({width:1440,height:1120});
  await page.goto(`${url}/#${bandsJob.id}`);
  await page.reload();
  await page.waitForFunction(()=>document.title.includes("Pen progress proof"));
  await page.locator('#preview-canvas[data-ready="true"]').waitFor();
  await enabled();
  let bandRequests=0;
  page.on("request",r=>{if(r.url().endsWith("/progress")) bandRequests++;});
  await page.locator('#show-plotter').click();
  await page.waitForFunction(()=>document.getElementById('plot-progress').dataset.mode==='preview'&&!document.getElementById('plot-progress').hidden);
  const plannedBands=await (await post(`/api/jobs/${bandsJob.id}/estimate`,bandsSetup)).json();
  assert.deepEqual(plannedBands.progress.sections.map(s=>s.slot),[0,6,2,6,5,0]);
  const bandsReply=page.waitForResponse(r=>r.url().endsWith(`/api/jobs/${bandsJob.id}/plot`));
  await page.locator("#plot-open").click();
  const bandsTicket=await (await bandsReply).json();
  await page.waitForFunction(()=>document.getElementById("plot-progress").getAttribute("aria-valuetext").includes("Slot"));
  let running;
  for(let i=0;;i++) {
    running=(await (await fetch(`${url}/api/plots/${bandsTicket.id}`)).json()).submission;
    if(running.execution.submitted_bytes>=running.bytes*.22) break;
    if(i>200) throw Error("No progress in fake plot");
    await new Promise(r=>setTimeout(r,25));
  }
  assert.equal((await post("/api/plotter/pause",{attempt:bandsTicket.id})).status,200);
  await refresh();
  await page.waitForFunction(()=>document.getElementById("plot-status").textContent==="Paused");
  const bands=await (await fetch(`${url}/api/plots/${bandsTicket.id}/progress`)).json();
  const prepared=await readFile(`${temp}/jobs/plots/${bandsTicket.id}/prepared.hpgl`,"utf8");
  assert.equal(bands.total_bytes,Buffer.byteLength(prepared));
  assert.deepEqual(bands.sections.map(s=>s.slot),[0,6,2,6,5,0]);
  for(const section of bands.sections.filter(s=>s.slot)) {
    assert.equal(prepared.slice(section.start_byte,section.start_byte+4),`SP${section.slot};`);
    assert.equal(section.color,section.slot===6?"#800080":section.slot===2?"#ff0000":"#008000");
  }
  const pixelColors=await page.evaluate((bands)=>{
    const c=document.getElementById("plot-pen-bands"),ctx=c.getContext("2d");
    return bands.sections.filter(s=>s.slot).map(s=>Array.from(ctx.getImageData(Math.floor((s.start_byte+s.end_byte)/2/bands.total_bytes*c.width),Math.floor(c.height/2),1,1).data));
  },bands);
  assert.deepEqual(pixelColors,[[128,0,128,255],[255,0,0,255],[128,0,128,255],[0,128,0,255]]);
  assert.equal(await page.locator("#plot-pen-legend,#plot-progress-text,#plot-current-name").count(),0,"No redundant plot summary rows");
  const now=(await (await fetch(`${url}/api/plots/${bandsTicket.id}`)).json()).submission.execution;
  assert.ok(now.timing.seconds>0);
  const percentage=100*now.submitted_bytes/now.total_bytes;
  assert.ok(Math.abs(Number(await page.locator("#plot-progress").getAttribute("aria-valuenow"))-percentage)<.11);
  const boundary=await page.locator("#plot-progress-unsent").evaluate(e=>parseFloat(e.style.left));
  assert.ok(Math.abs(boundary-percentage)<.001);
  const activeBand=bands.sections.find(s=>s.start_byte<=now.submitted_bytes&&s.end_byte>now.submitted_bytes);
  assert.ok((await page.locator("#plot-progress").getAttribute("aria-valuetext")).includes(activeBand.label));
  // Editing today's inventory cannot recolor a frozen running attempt.
  const changedPens=await (await fetch(`${url}/api/carousel`)).json(); changedPens.slots[6].color="#0000ff";
  assert.equal((await fetch(`${url}/api/carousel`,{method:"PUT",headers,body:JSON.stringify(changedPens)})).status,200);
  assert.deepEqual(await (await fetch(`${url}/api/plots/${bandsTicket.id}/progress`)).json(),bands);
  await refresh(); await refresh(); assert.equal(bandRequests,1,"Pen ranges fetched once per attempt, not on every status poll");
  await page.locator("#plot-feedback").screenshot({path:`${root}/build/controller/pen-progress-desktop.png`});
  await page.reload();await page.waitForFunction(label=>document.getElementById("plot-progress").getAttribute("aria-valuetext")?.includes(label),activeBand.label);
  await page.locator("#show-pens").click();assert.equal(await page.locator("#work-status").isVisible(),false);await page.locator("#show-plotter").click();assert(await page.locator("#work-status").isVisible());
  assert.equal(bandRequests,2);
  await page.setViewportSize({width:390,height:844});
  await page.locator("#plot-panel").scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator("#plot-feedback").screenshot({path:`${root}/build/controller/pen-progress-phone.png`});
  await page.locator("#plot-cancel").click(); await finish();
  await traceFor(bandsTicket);
  // Older protocol-2 bridges still feed; their traces explicitly lack Pi measurements.
  await setControls({delay_ms:0,no_timing:true});
  const oldSetup=await (await fetch(`${url}/api/jobs/${job.id}/setup`)).json();
  oldSetup.carousel_revision=(await (await fetch(`${url}/api/carousel`)).json()).revision;
  const oldResponse=await post(endpoint,{attempt:randomUUID(),setup:oldSetup});
  assert.equal(oldResponse.status,202);
  const oldTicket=await oldResponse.json();
  await refresh(); await finish();
  const oldTrace=(await readFile(`${temp}/jobs/plots/${oldTicket.id}/serial-timing.jsonl`,'utf8')).trim().split('\n').map(JSON.parse);
  assert.equal(oldTrace[0].pi_timing_available,false);
  assert.ok(!oldTrace.some(e=>e.event==='tx_queue_timing_v1'));
  assert.ok(oldTrace.some(e=>e.event==='reply'&&e.op==='close'));
  assert.deepEqual(errors,[]);
  console.log("Plot UI passed: automatic OA/OS completion on the feeding connection; delayed/split replies, finishing at 100% without enabling Plot, reload, retained evidence, malformed-reply recovery/no replay; Pi timing capture/hash/offsets, pause/resume bytes, partial traces on lost acknowledgements and old-bridge fallback; byte-aligned physical pen bands, repeated pens, pixel colors, frozen inventory, reload and phone layout; one-click start; immutable settings; duplicate/lost HTTP deduplication and reload recovery; paper-fit rejection; pause/resume/cancel; startup cancellation; interrupted restart/no replay; offline/CSRF; retired review endpoints. No hardware contacted.");
} finally {
  await browser?.close(); server.kill("SIGTERM");
  await new Promise(r=>server.exitCode!==null?r():server.once("exit",r));
  await rm(temp,{recursive:true,force:true});
}
