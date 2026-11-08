// podman build -t plotline . && npm ci --prefix controller
// node controller/scripts/container-smoke.mjs [image]
// Isolated volume/port; never supplies a hardware connection or calls Plot.
import {chromium} from 'playwright';
import {execFileSync,spawnSync} from 'node:child_process';
import {createServer} from 'node:net';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';

const image=process.argv[2]||'localhost/plotline:latest';
const name=`plotline-container-check-${process.pid}`,volume=name+'-data';
const podman=(...args)=>execFileSync('podman',args,{encoding:'utf8',maxBuffer:8*1024*1024,stdio:['ignore','pipe','pipe']});
const inside=(...args)=>podman('exec',name,...args);
function logs(){
  const result=spawnSync('podman',['logs','--tail','100',name],{encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
  return result.stdout+result.stderr;
}
function checkLogs(){assert.doesNotMatch(logs(),/Storage cleanup failed|Traceback \(most recent call last\)/);}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const socket=createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));
const port=socket.address().port;await new Promise(r=>socket.close(r));
const base=`http://127.0.0.1:${port}`;
let browser;
async function api(path,options={}){
  const response=await fetch(base+path,{...options,headers:{'X-Plotline-Request':'1',...options.headers}});
  const value=await response.json();assert(response.ok,JSON.stringify(value));return value;
}
const post=(path,body)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
async function until(check,seconds=120){
  const deadline=Date.now()+seconds*1000;let last;
  while(Date.now()<deadline){try{const result=await check();if(result)return result;}catch(error){last=error;}await sleep(250);}
  throw Error(`Timed out: ${last?.stack||'condition not met'}`);
}
async function completed(path){
  const value=await until(async()=>{const v=await api(path);return ['received','inspecting','queued','running'].includes(v.status)?false:v;});
  assert.equal(value.status,'ready',JSON.stringify(value));return value;
}
function start(offline=false){
  podman('run','--rm','-d','--name',name,...(offline?['--network=none']:['-p',`127.0.0.1:${port}:9191`]),
    '-e',`PLOTLINE_PUBLIC_URL=${base}`,'-v',`${volume}:/data`,image);
}
function stop(){checkLogs();podman('stop','--time','10',name);}
try{
  podman('volume','create',volume);
  start(true);
  await until(()=>{inside('python3','packaging/container-entrypoint.py','--healthcheck');return true;},30);
  const defaultPolicy=JSON.parse(inside('cat','controller/retention-policy.json'));
  await until(()=>{
    checkLogs();
    const status=JSON.parse(inside('cat','/data/studios/storage-status.json'));
    assert.deepEqual(status.errors,[]);assert.deepEqual(status.policy,defaultPolicy);return status.last_sweep>0;
  },10);
  assert(logs().includes(`Map Studio is at ${base}/map`));
  assert(!logs().includes('http://127.0.0.1:9193/'),'Do not advertise the internal worker as a browser address');
  assert.notEqual(inside('id','-u').trim(),'0');
  inside('python3','-c',`from pathlib import Path
from raster_lab.engine import check_dependencies
check_dependencies()
for name in ('.git','controller/state','state','worktrees','research','docs/readme/originals'):
 assert not Path('/opt/plotline',name).exists(), name
assert not Path('/usr/bin/ssh').exists()
`);
  inside('.venv/bin/python','-c','import _cartographer_geometry, pymupdf, shapely; print(_cartographer_geometry.geos_version())');
  inside('/opt/calibration/bin/python','-c','import numpy, scipy, PIL');
  stop();console.log('Offline startup, storage cleanup, shutdown, runtime dependencies and private-file exclusion passed.');

  start();await until(()=>api('/api/status'));
  assert.equal((await api('/api/plotter')).configured,false);
  assert(Object.values((await api('/api/carousel')).slots).every(p=>p.state==='empty'));
  inside('.venv/bin/python','tools/make_boundary_fixture.py');
  const pdf=Buffer.from(inside('.venv/bin/python','-c',"import base64; from pathlib import Path; print(base64.b64encode(Path('build/pdf-boundary-v1/boundary.pdf').read_bytes()).decode())").trim(),'base64');
  const intent=JSON.parse(inside('cat','build/pdf-boundary-v1/boundary.print-intent.json'));
  const form=new FormData();form.append('document',new Blob([pdf]),'Container boundary.pdf');
  form.append('intent',new Blob([JSON.stringify(intent)]),'intent.json');
  const uploaded=await api('/api/v1/documents',{method:'POST',body:form});
  const doc=await completed(uploaded.status_url);
  // Simulate eviction of a regenerable thumbnail; preserve the original/recipe.
  inside('rm','-f',`/data/studios/documents/${doc.id}/page-0-v2.png`);
  const thumbnail=await fetch(base+`/api/v1/documents/${doc.id}/pages/0`);
  assert.equal(thumbnail.status,200,await thumbnail.clone().text());
  assert.equal(thumbnail.headers.get('content-type'),'image/png');
  assert.deepEqual(Buffer.from(await thumbnail.arrayBuffer()).subarray(0,8),Buffer.from([137,80,78,71,13,10,26,10]));
  assert.deepEqual((await api(uploaded.status_url)).inspection,doc.inspection);
  assert.deepEqual(Buffer.from(await (await fetch(base+`/api/v1/documents/${doc.id}/source.pdf`)).arrayBuffer()),pdf);
  const request=await post(`/api/v1/documents/${doc.id}/draft`,intent);
  const draft=await completed(request.status_url);
  assert.equal(draft.result.pages.length,2);
  assert(draft.result.pages[1].geometry.source_exceeds_sheet);
  assert((await api('/api/jobs')).length>0,'HPGL drafts registered');
  inside('python3','controller/scripts/retention.py','--state','/data/studios','--apply');
  assert.deepEqual(JSON.parse(inside('cat','/data/studios/storage-status.json')).errors,[]);
  console.log('PDF upload, expired thumbnail regeneration, conversion, cleanup, placement and HPGL handoff passed.');

  // Exercise the distribution-Python raster worker through the real PDF API.
  inside('python3','-m','raster_lab.pdf_fixture','--output','/data/cache/raster-fixture',
    '--pdf-python','/opt/plotline/.venv/bin/python');
  const raster=Buffer.from(inside('python3','-c',"import base64; from pathlib import Path; print(base64.b64encode(Path('/data/cache/raster-fixture/mixed-raster-vector.pdf').read_bytes()).decode())").trim(),'base64');
  const rasterForm=new FormData();rasterForm.append('document',new Blob([raster]),'Raster fixture.pdf');
  const rasterDoc=await completed((await api('/api/v1/documents',{method:'POST',body:rasterForm})).status_url);
  const rasterIntent=structuredClone(intent);rasterIntent.source_sha256=createHash('sha256').update(raster).digest('hex');delete rasterIntent.bindings;
  rasterIntent.pages=[{page_index:0,source_size_mm:[160,120],placement:{scale:1,rotation_deg:0,offset_mm:[20,40]},target_media:intent.pages[0].target_media}];
  rasterIntent.policies.raster='dither';
  rasterIntent.policies.raster_settings={screening:true,palette:'k',fullness:1,edge_falloff_mm:6.35,black_treatment:'off',inks:{K:'broad-black'}};
  const rasterDraft=await completed((await post(`/api/v1/documents/${rasterDoc.id}/draft`,rasterIntent)).status_url);
  assert(rasterDraft.result.pages[0].raster_images.length>0);
  console.log('Mixed bitmap/vector PDF screening passed.');

  browser=await chromium.launch({headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),errors=[];
  context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
  for(const route of ['/','/pdf','/hpgl','/patterns','/mazes','/map']){
    const page=await context.newPage();await page.goto(base+route);
    if(['/patterns','/mazes'].includes(route)){
      await page.locator('#preview[data-ready=true]').waitFor({timeout:120000});
      await page.locator('#save').click();await page.locator('#save-name').fill('Container '+route.slice(1));
      await page.locator('#save-form button[type=submit]').click();
      await page.waitForFunction(()=>!document.querySelector('#document-tabs [aria-selected=true]').textContent.includes('*'));
      assert.equal((await api(`/api${route}/documents`)).documents.length,1);
      await page.locator('.export-bar .tray>summary').click();const pending=page.waitForEvent('download',{timeout:120000});
      await page.locator('#download-pdf').click();assert((await pending).suggestedFilename().endsWith('.pdf'));
    }else if(route==='/map'){
      await page.waitForFunction(()=>document.getElementById('work-detail').textContent==='Preview complete',null,{timeout:180000});
      assert(await page.locator('#preview').isVisible());
      await page.locator('#save').click();await page.locator('#save-name').fill('Container map');
      await page.locator('#save-form button[type=submit]').click();
      await until(async()=> (await api('/map/api/recipes')).length===1);
      const preparationResponse=page.waitForResponse(r=>r.url()===base+'/map/api/prepare'&&r.request().method()==='POST');
      await page.locator('#send').click();
      const preparation=await (await preparationResponse).json();
      const prepared=await until(async()=>{
        const value=await api('/map/api/preparations/'+preparation.id);
        return ['ready','empty','failed','recovery','superseded'].includes(value.phase)?value:false;
      },240);
      assert.equal(prepared.phase,'ready',JSON.stringify(prepared));
      const mapJob=prepared.jobs.find(job=>job.job_id);assert(mapJob,'Default map must produce an HPGL draft');
      assert.equal(new URL(mapJob.url,base).origin,base);
      assert.equal((await fetch(base+`/api/jobs/${mapJob.job_id}/source.hpgl`)).status,200);
      assert.equal((await api('/api/plotter')).configured,false);
      console.log('Default map Prepare plot reached HPGL Studio without a hardware connection.');
    }
    await page.close();console.log(`Browser ${route} passed.`);
  }
  const galleryManifest=await (await fetch(base+'/patterns/thumbnails.js')).text();
  const thumbnails=[...galleryManifest.matchAll(/"([^"\n]+\.png)"/g)].map(m=>m[1]);
  assert(thumbnails.length>=47);
  await Promise.all(thumbnails.map(async path=>{const response=await fetch(base+path);assert.equal(response.status,200,path);assert.equal(response.headers.get('content-type'),'image/png');}));
  assert.deepEqual(errors,[]);
  await browser.close();browser=null;
  stop();start();await until(()=>api('/api/status'));
  assert.equal((await api(`/api/v1/documents/${doc.id}`)).status,'ready');
  assert.equal((await api('/api/patterns/documents')).documents.length,1);
  assert.equal((await api('/api/mazes/documents')).documents.length,1);
  const savedMaps=await until(()=>api('/map/api/recipes'));
  assert.equal(savedMaps[0].title,'Container map');
  assert.equal((await api('/api/plotter')).configured,false);
  console.log('Documents survived container replacement; hardware stayed unconfigured.');
  checkLogs();
  // A dead worker must fail the container, not leave a half-working suite.
  inside('python3','-c',`from pathlib import Path
import os,signal
for p in Path('/proc').glob('[0-9]*/cmdline'):
 try: args=p.read_bytes().split(b'\\0')
 except OSError: continue
 if b'cartographer.service' in args: os.kill(int(p.parent.name),signal.SIGKILL)
`);
  await until(()=>!podman('ps','--format','{{.Names}}').split('\n').includes(name),20);
  console.log('Worker failure stops the container. Container acceptance passed.');
}catch(error){
  try{console.error(logs());}catch{}
  throw error;
}finally{
  await browser?.close();
  try{podman('rm','--force',name);}catch{}
  podman('volume','rm',volume);
}
