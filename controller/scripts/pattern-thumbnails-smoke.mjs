// Build-artifact/cache and gallery regression; isolated state, no plotter.
import {chromium,firefox} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import {createServer as httpServer,request as httpRequest} from 'node:http';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {checkPatternGallery,chooseGalleryFamily} from './pattern-gallery-checks.mjs';
import {familyGroups} from '../../patterns/web/groups.js';
const root=resolve(import.meta.dirname,'../..');await mkdir(root+'/build',{recursive:true});
const state=await mkdtemp(root+'/build/pattern-thumbnails-browser-'),socket=createServer();
await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
const base=`http://127.0.0.1:${port}`;
const server=spawn(process.env.PLOTLINE_TEST_BINARY||root+'/controller/target/debug/plotline-studios',['--listen',`127.0.0.1:${port}`,'--state',state+'/store'],{cwd:root,env:{...process.env,PLOTLINE_PDF_PROJECT:root},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',d=>logs+=d);
// Count real HTTP transfers, including Firefox's decoded-image cache hits
// (which do not necessarily produce PerformanceResourceTiming entries).
let imageRequests=0;
const proxy=httpServer((request,response)=>{
 if(request.url.startsWith('/patterns/thumbnails/'))imageRequests++;
 const upstream=httpRequest(new URL(request.url,base),{method:request.method,headers:{...request.headers,host:`127.0.0.1:${port}`}},reply=>{response.writeHead(reply.statusCode,reply.headers);reply.pipe(response);});
 upstream.on('error',error=>{response.writeHead(502);response.end(error.message);});request.pipe(upstream);
});
const ready=page=>page.waitForFunction(()=>[...document.querySelectorAll('#family-gallery button:not([hidden])')].every(b=>b.dataset.ready==='true'));
try{
 for(let i=0;;i++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(i>100)throw Error(logs);await new Promise(r=>setTimeout(r,100));}
 const response=await fetch(base+'/patterns/thumbnails.js'),source=await response.text();
 assert.equal(response.headers.get('cache-control'),'no-store','The manifest must refresh when a build changes');
 const manifest=JSON.parse(source.replace(/^export const thumbnails=/,'').trim().replace(/;$/,''));
 assert.deepEqual(Object.keys(manifest).sort(),familyGroups.flatMap(g=>g.families).sort());
 let totalBytes=0;
 for(const [id,url] of Object.entries(manifest)){
  const r=await fetch(base+url);assert.equal(r.status,200);assert.equal(r.headers.get('content-type'),'image/png');
  assert.equal(r.headers.get('cache-control'),'public, max-age=31536000, immutable');assert.equal(r.headers.get('x-content-type-options'),'nosniff');
  const bytes=Buffer.from(await r.arrayBuffer()),hash=createHash('sha256').update(bytes).digest('hex');totalBytes+=bytes.length;
  assert.equal(url,`/patterns/thumbnails/${id}.${hash}.png`,'The cache identity describes the bytes actually served');
  assert.equal(bytes.subarray(0,8).toString('hex'),'89504e470d0a1a0a');
 }
 for(const url of ['/patterns/thumbnails/missing.png','/patterns/thumbnails/koch.'+'0'.repeat(64)+'.png']){const r=await fetch(base+url);assert.equal(r.status,404);assert.equal(r.headers.get('cache-control'),'no-store');}
 for(const url of ['/api/carousel','/patterns','/patterns/app.js'])assert.equal((await fetch(base+url)).headers.get('cache-control'),'no-store');
 browser=await (process.env.PLOTLINE_BROWSER==='firefox'?firefox:chromium).launch({headless:true});
 await new Promise(r=>proxy.listen(0,'127.0.0.1',r));const browserBase=`http://127.0.0.1:${proxy.address().port}`;
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),errors=[];
 await context.addInitScript(()=>{const Original=Worker;window.workerStarts=0;window.Worker=class extends Original{constructor(...args){super(...args);window.workerStarts++;}};});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto(browserBase+'/patterns');await page.locator('#preview[data-ready=true]').waitFor();
 const workers=await page.evaluate(()=>workerStarts),start=performance.now();
 await page.locator('#browse').click();await ready(page);const coldMilliseconds=performance.now()-start;
 await checkPatternGallery(page,state+'/gallery.png');
 assert.equal(await page.evaluate(()=>workerStarts),workers,'Browsing all groups must start zero geometry workers');
 await page.locator('[data-group="'+familyGroups[0].id+'"]').click();await ready(page);
 const transfersBeforeReload=imageRequests;
 await page.reload();await page.locator('#preview[data-ready=true]').waitFor();
 const reloadWorkers=await page.evaluate(()=>workerStarts),warmStart=performance.now();
 await page.locator('#browse').click();await ready(page);const warmMilliseconds=performance.now()-warmStart;
 assert.equal(imageRequests,transfersBeforeReload,'Reload transfers no images: it reuses the browser HTTP cache');
 const cacheHits=await page.locator('#family-gallery img:visible').count();
 assert.equal(await page.evaluate(()=>workerStarts),reloadWorkers);
 await chooseGalleryFamily(page,'flowers');await page.locator('#preview[data-ready=true]').waitFor();
 assert(await page.locator('#prepare').isEnabled());assert((await page.evaluate(()=>workerStarts))>reloadWorkers,'Choosing a family still generates actual artwork');
 // A failed image must not block its neighbors or the family-selection button.
 const failureContext=await browser.newContext(),failed=await failureContext.newPage();let blocked=true;
 await failed.route('**/patterns/thumbnails/koch.*.png',route=>blocked?route.fulfill({status:503,body:''}):route.continue());
 await failed.goto(base+'/patterns');await failed.locator('#preview[data-ready=true]').waitFor();await failed.locator('#browse').click();
 await failed.locator('[data-family=koch][data-ready=error]').waitFor();
 await failed.locator('[data-family=dragon][data-ready=true]').waitFor();
 assert(await failed.locator('[data-family=koch]').isEnabled());blocked=false;
 await failed.locator('#close-gallery').click();await failed.locator('#browse').click();await ready(failed);
 await failureContext.close();assert.deepEqual(errors,[]);
 const result={families:Object.keys(manifest).length,totalBytes,coldMilliseconds,warmMilliseconds,cacheHits,newGalleryWorkers:0};
 await writeFile(state+'/result.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));console.log('PASS gallery images, hashes/cache, zero workers, selection, retry, keyboard and mobile: '+state);
}catch(error){console.error(logs);throw error;}
finally{await browser?.close();proxy.closeAllConnections();await new Promise(r=>proxy.close(r));server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));}
