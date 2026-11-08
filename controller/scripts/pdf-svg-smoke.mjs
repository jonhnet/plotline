// Mixed SVG intake through PDF review and HPGL draft; isolated state, no device.
import {chromium} from 'playwright';
import {spawn,execFileSync} from 'node:child_process';
import {mkdtemp,readFile,mkdir} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:net';
import {randomUUID,createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
await mkdir(root+'/build',{recursive:true});const state=await mkdtemp(root+'/build/pdf-svg-browser-');
execFileSync(root+'/.venv/bin/python',['-c',`
from pathlib import Path
import sys
sys.path.insert(0,sys.argv[1]+'/tests')
from test_svg_input import mixed_svg
Path(sys.argv[2]+'/Mixed text and image.svg').write_bytes(mixed_svg()[0])
`,root,state]);
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1'};
const server=spawn(root+'/controller/target/release/plotline-studios',['--state',state,'--listen',`127.0.0.1:${port}`],{env:{...process.env,PLOTLINE_PDF_PROJECT:root},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',x=>logs+=x);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(path,options={}){const r=await fetch(base+path,{...options,headers:{...headers,...options.headers}});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;}
async function done(path,expected='ready'){for(let n=0;n<1200;n++){const v=await api(path);if(!['received','inspecting','queued','running'].includes(v.status)){assert.equal(v.status,expected,v.error);return v;}await sleep(100);}throw Error('Timed out');}
async function upload(bytes,name,key){const form=new FormData();form.append('document',new Blob([bytes],{type:'image/svg+xml'}),name);return api('/api/v1/documents',{method:'POST',headers:key?{'Idempotency-Key':key}:{},body:form});}
try{
 for(let n=0;;n++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(n>100)throw Error(logs);await sleep(50);}
 const carousel=await api('/api/carousel');
 for(const p of Object.values(carousel.slots))Object.assign(p,{state:'empty',label:'',color:null,width_mm:null});
 Object.assign(carousel.slots[1],{state:'loaded',label:'Black',color:'#000000',width_mm:.25});
 await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(carousel)});
 browser=await chromium.launch();const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/pdf');await page.locator('#new-document').click();
 assert.match(await page.locator('#pdf-file').getAttribute('accept'),/image\/svg\+xml/);
 assert.match(await page.locator('#upload-dialog').textContent(),/server fonts/);
 await page.locator('#pdf-file').setInputFiles(state+'/Mixed text and image.svg');
 await page.locator('#upload button.primary').click();
 await page.waitForFunction(()=>document.querySelector('#preview').naturalWidth===1600&&!document.querySelector('#convert-button').disabled,null,{timeout:30000});
 const id=new URL(page.url()).searchParams.get('document'),doc=await done('/api/v1/documents/'+id);
 assert.equal(doc.original_format,'svg');assert.equal(doc.inspection.svg_input.embedded_images,1);
 const size=doc.inspection.pages[0].source_size_mm;
 assert(Math.abs(size[0]-431.8)<.001&&Math.abs(size[1]-279.4)<.001);
 assert.match(await page.locator('#source-size-note').textContent(),/Original SVG page.*17.*11.*in.*server/);
 assert.equal(await page.locator('#original').textContent(),'Original SVG ↓');
 assert.equal(await page.locator('#raster').inputValue(),'dither');
 const original=await readFile(state+'/Mixed text and image.svg');
 const download=await fetch(base+`/api/v1/documents/${id}/original`);
 assert.match(download.headers.get('content-disposition'),/^attachment/);
 assert.equal(download.headers.get('content-type'),'image/svg+xml');
 assert.deepEqual(Buffer.from(await download.arrayBuffer()),original);
 const pdf=Buffer.from(await(await fetch(base+`/api/v1/documents/${id}/source.pdf`)).arrayBuffer());
 assert.equal(createHash('sha256').update(pdf).digest('hex'),doc.source_sha256);
 const key=randomUUID(),keyed=await done((await upload(original,'keyed.svg',key)).status_url);
 const replay=await upload(original,'renamed.svg',key);assert.equal(replay.id,keyed.id);assert.equal(replay.source_sha256,keyed.source_sha256);
 await page.locator('#fit').click();await page.locator('#convert-button').click();
 await page.waitForFunction(()=>!document.querySelector('#update-plot').disabled,null,{timeout:180000});
 const prepared=await api('/api/v1/documents/'+id);assert.equal(prepared.draft.status,'ready');
 assert.equal(prepared.draft.jobs.length,1);assert(prepared.draft.jobs[0].job_id);
 const conversion=await api('/api/v1/conversions/'+prepared.draft.current_conversion_id);
 assert.equal(conversion.status,'ready');
 const hpgl=await(await fetch(base+conversion.result.pages[0].artifacts.hpgl)).text();
 assert.match(hpgl,/PD/);
 assert.equal(new URL(page.url()).pathname,'/pdf','Preview completion stays in PDF Studio');
 await page.screenshot({path:state+'/svg-pen-preview.png',fullPage:true});
 await page.reload();await page.waitForFunction(()=>!document.querySelector('#update-plot').disabled);
 assert.match(await page.locator('#source-size-note').textContent(),/Original SVG page/);
 const bad=Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///tmp/laptop-only.png"/></svg>');
 const rejected=await done((await upload(bad,'Linked image.svg')).status_url,'error');
 assert.match(rejected.error,/Embed images in Inkscape/);assert.doesNotMatch(rejected.error,/Traceback/);
 assert.deepEqual(Buffer.from(await(await fetch(base+`/api/v1/documents/${rejected.id}/original`)).arrayBuffer()),bad);
 await page.goto(base+'/pdf?document='+rejected.id);
 await page.waitForFunction(()=>document.querySelector('#work-detail').textContent==='Unable to read document');
 assert.match(await page.locator('#notice').textContent(),/Embed images in Inkscape/);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,state,document:id,conversion:conversion.id,page_inches:[17,11]}));
}catch(e){console.error(logs);throw e;}finally{await browser?.close();server.kill('SIGTERM');}
