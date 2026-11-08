// Real image upload, PDF wrapper, preview, and pen conversion; no hardware.
import {chromium} from 'playwright';
import {spawn,spawnSync} from 'node:child_process';
import {mkdtemp,readFile,writeFile,unlink,mkdir} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:net';
import {randomUUID,createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
await mkdir(root+'/build',{recursive:true});const state=await mkdtemp(root+'/build/pdf-image-browser-');
const fixture=spawnSync(root+'/.venv/bin/python',['-c',`
from pathlib import Path
from PIL import Image,ImageDraw
import sys
root=Path(sys.argv[1]);im=Image.new('RGB',(1024,663),'white');d=ImageDraw.Draw(im)
for x in range(0,1024,8):d.line((x,0,x,663),fill='black',width=1)
im.save(root/'test.jpg',dpi=(300,300))
im=Image.new('RGBA',(120,90),(255,255,255,0));d=ImageDraw.Draw(im);d.rectangle((20,20,100,70),fill=(0,0,0,128));im.save(root/'test.png',dpi=(300,300))
`,state]);assert.equal(fixture.status,0,fixture.stderr?.toString());
const jpeg=process.env.PDF_IMAGE_SOURCE||state+'/test.jpg';
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1'};
const server=spawn(root+'/controller/target/release/plotline-studios',['--state',state,'--listen',`127.0.0.1:${port}`],{env:{...process.env,PLOTLINE_PDF_PROJECT:root},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',x=>logs+=x);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(path,options={}){const r=await fetch(base+path,{...options,headers:{...headers,...options.headers}});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;}
async function done(path){for(let n=0;n<600;n++){const v=await api(path);if(!['received','inspecting','queued','running'].includes(v.status)){assert.equal(v.status,'ready',v.error);return v;}await sleep(100);}throw Error('Timed out');}
async function upload(bytes,name,key){const form=new FormData();form.append('document',new Blob([bytes]),name);return api('/api/v1/documents',{method:'POST',headers:key?{'Idempotency-Key':key}:{},body:form});}
try{
 for(let n=0;;n++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(n>100)throw Error(logs);await sleep(50);}
 const carousel=await api('/api/carousel');
 for(const p of Object.values(carousel.slots))Object.assign(p,{state:'empty',label:'',color:null,width_mm:null});
 Object.assign(carousel.slots[1],{state:'loaded',label:'Black',color:'#000000',width_mm:.25});
 await api('/api/carousel',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(carousel)});
 const original=await readFile(jpeg),key=randomUUID(),received=await upload(original,'Native pixels.jpg',key);
 const doc=await done(received.status_url);assert.deepEqual(doc.inspection.image_input.pixels,[1024,663]);
 const replay=await upload(original,'Same bytes.jpg',key);assert.equal(replay.id,doc.id);assert.equal(replay.source_sha256,doc.source_sha256);
 const downloaded=Buffer.from(await(await fetch(base+`/api/v1/documents/${doc.id}/original`)).arrayBuffer());assert.deepEqual(downloaded,original);
 const wrapped=Buffer.from(await(await fetch(base+`/api/v1/documents/${doc.id}/source.pdf`)).arrayBuffer());assert.equal(createHash('sha256').update(wrapped).digest('hex'),doc.source_sha256);
 // Simulate an old retained document with a low-resolution cached thumbnail.
 const legacy=await done((await upload(wrapped,'Existing PDF.pdf')).status_url),folder=state+'/documents/'+legacy.id;
 const record=await readFile(folder+'/record.json');await writeFile(folder+'/page-0.png','old low-resolution thumbnail');await unlink(folder+'/page-0-v2.png');
 const regenerated=Buffer.from(await(await fetch(base+`/api/v1/documents/${legacy.id}/pages/0`)).arrayBuffer());assert.equal(regenerated.readUInt32BE(16),1600);
 assert.deepEqual(await readFile(folder+'/record.json'),record);assert.deepEqual(await readFile(folder+'/source.pdf'),wrapped);
 browser=await chromium.launch();const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/pdf?document='+doc.id);
 await page.waitForFunction(()=>document.querySelector('#preview').naturalWidth===1600);
 assert.equal(await page.locator('#raster').inputValue(),'dither');assert.equal(await page.locator('#raster-palette').inputValue(),'selected');
 assert.match(await page.locator('#source-size-note').textContent(),/1024 × 663/);
 assert.equal(await page.locator('#original').getAttribute('href'),`/api/v1/documents/${doc.id}/original`);
 await page.screenshot({path:state+'/jpeg-preview.png',fullPage:true});
 await page.locator('#new-document').click();assert.match(await page.locator('#pdf-file').getAttribute('accept'),/image\/png/);
 await page.locator('#pdf-file').setInputFiles(state+'/test.png');await page.locator('#upload button.primary').click();
 await page.waitForFunction(()=>document.querySelector('#document-detail').textContent.startsWith('test.png')&&!document.querySelector('#convert-button').disabled);
 const pngId=new URL(page.url()).searchParams.get('document'),png=await done('/api/v1/documents/'+pngId);
 assert(png.inspection.image_input.transparency_on_white);assert.deepEqual(png.inspection.image_input.pixels,[120,90]);
 // Exercise the full raster-to-pen path. Use a small valid target sheet.
 const caps=await api('/api/v1/capabilities');const media=caps.media_profiles.find(m=>m.sheet_size_mm[0]>200&&m.sheet_size_mm[0]<300);
 assert(media);await page.locator('#media').selectOption(String(caps.media_profiles.indexOf(media)));await page.locator('#fit').click();
 await page.locator('#convert-button').click();
 await page.waitForFunction(()=>['ready','error','unsupported'].includes(document.querySelector('#work-status').dataset.phase)&&!!document.querySelector('#work-status').dataset.conversion,null,{timeout:180000});
 assert.equal(await page.locator('#work-status').getAttribute('data-phase'),'ready',await page.locator('#notice').textContent());
 const converted=(await api('/api/v1/documents/'+pngId)).conversions[0];assert.equal(converted.status,'ready',converted.error);
 await page.locator('#view').selectOption('source');
 const svg=await(await fetch(base+`/api/v1/conversions/${converted.id}/pages/0/source.svg`)).text();
 const image=Buffer.from(svg.match(/base64,([^" ]+)/)[1],'base64');assert.equal(image.readUInt32BE(16),2200);
 const pngOriginal=Buffer.from(await(await fetch(base+`/api/v1/documents/${pngId}/original`)).arrayBuffer());assert.deepEqual(pngOriginal,await readFile(state+'/test.png'));
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,state,jpeg_pixels:doc.inspection.image_input.pixels,preview_width:1600,comparison_width:2200,png_conversion:converted.id}));
}catch(e){console.error(logs);throw e;}finally{await browser?.close();server.kill('SIGTERM');}
