// Real IPP capture → durable studio intake → conversion → HPGL → studio round trip.
// Isolated state and listeners, discovery disabled, no device connection.
import { chromium } from "playwright";
import { spawn, execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, readdir } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import assert from "node:assert/strict";
const root=resolve(dirname(fileURLToPath(import.meta.url)),"../..");
await mkdir(resolve(root,"build/ipp-flow"),{recursive:true});
const temp=await mkdtemp(resolve(root,"build/ipp-flow/run-"));
async function port(){const s=createServer();await new Promise(r=>s.listen(0,"127.0.0.1",r));const p=s.address().port;await new Promise(r=>s.close(r));return p;}
const webPort=await port(),ippPort=await port(),url=`http://127.0.0.1:${webPort}`,ippUri=`ipp://127.0.0.1:${ippPort}/ipp/print`;
const headers={"X-Plotline-Request":"1","Content-Type":"application/json"};
let server,sink,browser,logs="";
const launch=(command,args,extra={})=>{
  const child=spawn(command,args,{cwd:root,env:{...process.env,...extra},stdio:["ignore","ignore","pipe"]});
  child.stderr.on("data",b=>logs+=b);return child;
};
async function api(path,options={}){
  const r=await fetch(url+path,{...options,headers:{...headers,...options.headers}});
  const data=await r.json();assert(r.ok,JSON.stringify(data));return data;
}
async function waitFor(fn){for(let n=0;n<300;n++){const value=await fn();if(value)return value;await new Promise(r=>setTimeout(r,200));}throw Error("Timed out\n"+logs.slice(-5000));}
async function stop(child){if(!child || child.exitCode!==null)return;child.kill("SIGTERM");await new Promise(r=>child.once("exit",r));}
const startStudio=(extraArgs=[])=>launch(resolve(root,"controller/target/release/plotline-studios"),["--state",`${temp}/jobs`,"--listen",`127.0.0.1:${webPort}`,...extraArgs],{
  PLOTLINE_IPP_INBOX:`${temp}/ipp/captures`,PLOTLINE_PDF_PROJECT:root});
try{
  await mkdir(`${temp}/jobs`);
  const slots=Object.fromEntries(Array.from({length:8},(_,i)=>[i+1,{label:i?"":"Black .3",state:i?"empty":"loaded",color:i?null:"#000000",width_mm:i?null:.3,speed_cm_s:5,force:4}]));
  await writeFile(`${temp}/jobs/carousel.json`,JSON.stringify({revision:1,slots}));
  execFileSync(resolve(root,".venv/bin/python"),["-c",`
import pymupdf,sys
d=pymupdf.open();pt=72/25.4
for w,h,x,y in [(215.9,279.4,50,65),(431.8,279.4,60,35)]:
 p=d.new_page(width=w*pt,height=h*pt)
 p.draw_line((x*pt,y*pt),((x+100)*pt,y*pt),color=(0,0,0),width=.3*pt)
 p.insert_text((x*pt,(y-8)*pt),'100 mm ruler',fontsize=12)
d.new_page(width=215.9*pt,height=279.4*pt) # A blank page must not block the drawable sheets.
d.save(sys.argv[1])
`,`${temp}/input.pdf`]);
  sink=launch(resolve(root,"tools/print_sink/run.sh"),[],{PLOTLINE_SINK_PORT:String(ippPort),PLOTLINE_SINK_STATE:`${temp}/ipp`,PLOTLINE_SINK_DISCOVERY:"off",PLOTLINE_REVIEW_URL:url});
  await waitFor(async()=>{try{return(await fetch(`http://127.0.0.1:${ippPort}/`)).ok}catch{return false}});
  await writeFile(`${temp}/print.test`,`{
OPERATION Print-Job
GROUP operation
ATTR charset attributes-charset utf-8
ATTR language attributes-natural-language en
ATTR uri printer-uri $uri
ATTR mimeMediaType document-format application/pdf
ATTR name job-name "IPP placement proof.pdf"
GROUP job
ATTR keyword print-scaling none
FILE $filename
STATUS successful-ok
}`);
  execFileSync("ipptool",["-tv","-f",`${temp}/input.pdf`,ippUri,`${temp}/print.test`]);
  const captures=await waitFor(async()=>{try{const names=await readdir(`${temp}/ipp/captures`);return names.length&&names}catch{return false}});
  await waitFor(async()=>{try{return JSON.parse(await readFile(`${temp}/ipp/captures/${captures[0]}/capture.json`))}catch{return false}});
  // Capture completed while studio was offline. Startup must discover it once.
  server=startStudio();
  await waitFor(async()=>{try{return(await fetch(url+"/api/status")).ok}catch{return false}});
  let document=await waitFor(async()=>{
    const docs=(await api("/api/v1/documents")).documents;
    assert(docs.length<=1,"Capture imported more than once");
    if(docs[0]?.draft?.status==="error")throw Error(docs[0].draft.error);
    return docs[0]?.status==="ready"&&docs[0];
  });
  document=await api(document.status_url);
  assert.equal(document.conversions.length,0,"IPP must wait for PDF interpretation choices");
  assert.equal((await api('/api/jobs')).length,0,"Receiving a PDF must not commit an HPGL draft");
  await api(`/api/v1/documents/${document.id}/draft`,{method:'POST',body:JSON.stringify({})});
  document=await waitFor(async()=>{const row=await api(document.status_url);if(row.draft?.status==='error')throw Error(row.draft.error);return row.draft?.status==='ready'&&row;});
  assert.equal(document.draft.jobs.length,3);
  assert.equal(document.draft.jobs[2].status,"empty");
  assert.equal(document.print_ticket.IPP_PRINT_SCALING,"none");
  assert.deepEqual(Buffer.from(await(await fetch(url+document.status_url+"/source.pdf")).arrayBuffer()),await readFile(`${temp}/input.pdf`));
  const before=(await api("/api/jobs")),first=before.find(j=>j.pdf.page_index===0),second=before.find(j=>j.pdf.page_index===1);
  // Letter must load with its 11-inch side across the platen. The source PDF
  // stays portrait and full scale; this registration puts the wide margin left.
  assert.equal(before.length,2);assert.equal(first.pdf.registration.rotation,0);
  assert.deepEqual(first.pdf.registration.sheet_size_mm,[215.9,279.4]);
  const original=await(await fetch(url+`/api/jobs/${first.id}/source.hpgl`)).text();
  const rulerPoints=s=>[...s.matchAll(/(?:PU|PD)(-?\d+),2988;/g)].map(m=>Number(m[1]));
  const ruler=rulerPoints(original);
  assert(ruler.length>=2,"The converted ruler has both ends");
  assert(Math.abs(Math.min(...ruler)+2798)<=6,"50,65 mm Letter point retains its paper position within the allowed cap approximation");
  assert(Math.abs(Math.max(...ruler)-1202)<=6,"100 mm ruler retains scale within the allowed cap approximation");
  const settings=await api(`/api/jobs/${first.id}/setup`);
  let prepared=await fetch(url+`/api/jobs/${first.id}/prepared.hpgl`,{method:"POST",headers,body:JSON.stringify(settings)});
  assert(prepared.ok,await prepared.clone().text());
  assert.deepEqual(rulerPoints(await prepared.text()),ruler,"Preparation must not recenter the registered page");
  const inventory=await api("/api/carousel");inventory.slots[1].width_mm=.6;
  const changed=await api("/api/carousel",{method:"PUT",body:JSON.stringify(inventory)});
  const fresh=await api(`/api/jobs/${first.id}/setup`);
  prepared=await fetch(url+`/api/jobs/${first.id}/prepared.hpgl`,{method:"POST",headers,body:JSON.stringify(fresh)});
  assert.equal(prepared.status,422);
  assert.match((await prepared.json()).error,/Ink 1 → slot 1 differs from the prepared pen.*Use existing paths or Rebuild upstream/);
  changed.slots[1].width_mm=.3;await api("/api/carousel",{method:"PUT",body:JSON.stringify(changed)});
  // Restart must reuse the intake receipt and current drafts.
  await stop(server);
  // Simulate a crash after immutable conversion publication but before draft promotion.
  const pending={...document.draft,status:"queued",jobs:[]};
  await writeFile(`${temp}/jobs/documents/${document.id}/draft.json`,JSON.stringify(pending));
  server=startStudio();
  await waitFor(async()=>{try{return(await fetch(url+"/api/status")).ok}catch{return false}});
  await waitFor(async()=>(await api("/api/jobs")).length===2);
  assert.deepEqual((await api("/api/jobs")).map(j=>j.id).sort(),before.map(j=>j.id).sort());
  assert.equal((await api("/api/v1/documents")).documents.length,1);
  browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
  const context=await browser.newContext({viewport:{width:1500,height:1050}}),errors=[];
  context.on('page',page=>page.on("pageerror",e=>errors.push(e.message)));
  const page=await context.newPage();
  await page.goto(url+`/hpgl#${first.id}`);
  await page.waitForSelector('#preview-canvas[data-ready="true"]');
  assert(await page.locator("#paper-controls").isHidden());
  assert.match(await page.locator("#paper-requirement").textContent(),/Load the planned/);
  assert(await page.locator('#pens-pane').isVisible());
  assert.match(await page.locator("#placement-status").textContent(),/Fixed page placement/);
  await page.screenshot({path:`${temp}/hp7585.png`});
  await page.locator("#export-tools > summary").click();
  const opened=context.waitForEvent('page');
  await page.locator("#pdf-edit").click();const pdf=await opened;
  await pdf.waitForFunction(()=>document.getElementById('convert-button')?.disabled===false);
  assert.equal(new URL(page.url()).hash,'#'+first.id,"Editing PDF preserves the HPGL tab");
  await pdf.locator("#offset-x").fill("0.1");await pdf.locator("#offset-x").dispatchEvent('change');
  assert(await pdf.locator('#update-plot').isDisabled(),"Edited placement needs a new pen preview");
  await pdf.locator('#convert-button').click();
  await pdf.waitForFunction(()=>!document.getElementById('update-plot').disabled,null,{timeout:60000});
  const updated=await api(document.status_url),newId=updated.draft.jobs.find(j=>j.page_index===0).job_id;
  assert.notEqual(newId,first.id);const conversions=updated.conversions.length;
  await pdf.locator("#update-plot").click();
  await page.waitForURL(url+'/hpgl#'+newId,{timeout:60000});
  assert.equal((await api(document.status_url)).conversions.length,conversions,"Open plot only navigates; the preview already registered it");
  await page.waitForSelector('#preview-canvas[data-ready="true"]');
  const after=await api("/api/jobs");assert.equal(after.length,2);
  assert(after.some(j=>j.id===second.id),"Updating one page preserves the other page draft");
  assert(!after.some(j=>j.id===first.id),"Superseded draft is replaced in library");
  assert.equal(await(await fetch(url+`/api/jobs/${first.id}/source.hpgl`)).text(),original,"Earlier conversion bytes remain immutable");
  const newSource=await(await fetch(url+`/api/jobs/${newId}/source.hpgl`)).text();
  assert(Math.abs(Math.min(...rulerPoints(newSource))-Math.min(...ruler)-101.6)<=1,"0.1 inch X adjustment moves device X by 102 units; no recentering");
  const replay=await api(`/api/v1/conversions/${after.find(j=>j.id===newId).pdf.conversion_id}/publish`,{method:"POST"});
  assert.equal(replay.jobs.find(j=>j.page_index===0).job_id,newId,"Repeat handoff reuses the same draft");
  assert.deepEqual(errors,[]);
  assert.equal((await api("/api/status")).hardware_enabled,false);
  await stop(server);
  await writeFile(`${temp}/controls.json`,JSON.stringify({limits:[-8000,-5000,8000,5000]}));
  await writeFile(`${temp}/connection.json`,JSON.stringify({fixture_dir:temp,plotting_enabled:true}));
  server=startStudio(["--device-config",`${temp}/connection.json`,"--device-helper",`${root}/controller/scripts/fake-plotter.py`]);
  await waitFor(async()=>{try{return(await fetch(url+"/api/status")).ok}catch{return false}});
  await page.reload();
  try { await page.waitForFunction(()=>!document.querySelector("#plot-open").disabled); }
  catch(error) { await page.screenshot({path:`${temp}/plot-error.png`});throw Error(error.message+"\n"+await page.locator("#plot-issue").textContent()); }
  const receipt=page.waitForResponse(r=>r.url()===url+`/api/jobs/${newId}/plot`);
  await page.locator("#plot-open").click();
  const submitted=await receipt;assert.equal(submitted.status(),202);
  const ticket=await submitted.json();
  await page.waitForFunction(()=>document.getElementById('plot-status').textContent==='Plot finished · confirmed by plotter');
  const sent=await readFile(`${temp}/jobs/plots/${ticket.id}/prepared.hpgl`,"utf8");
  assert.deepEqual(rulerPoints(sent),rulerPoints(newSource),"Explicit Plot sends registered coordinates without centering");
  assert.equal(await page.locator('#plot-complete').isVisible(),false);
  const stale=await fetch(url+`/api/jobs/${first.id}/plot`,{method:"POST",headers,body:JSON.stringify({attempt:"9ef1ab88-d840-4cee-a08a-358156326ea3",setup:await api(`/api/jobs/${first.id}/setup`)})});
  assert.equal(stale.status,422);assert.match(await stale.text(),/newer version/);
  assert.deepEqual(errors,[]);
  console.log(`PASS IPP offline capture, idempotent intake/restart, PDF review before explicit multi-page/blank-page draft, physical registration, diameter guard, browser update, immutable replacement, and explicit Plot through an isolated serial fixture. Evidence: ${temp}`);
}finally{
  await browser?.close();await stop(server);await stop(sink);
  await writeFile(`${temp}/services.log`,logs);
}
