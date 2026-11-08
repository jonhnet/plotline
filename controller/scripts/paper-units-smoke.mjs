// Presentation-only units: exact geometry, cross-tab preference and input bounds.
import {chromium,firefox} from 'playwright';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const moduleURL=new URL('../../shared/web/paper-units.js',import.meta.url);
const server=createServer(async(req,res)=>{
 if(req.url==='/paper-units.js'){res.setHeader('Content-Type','text/javascript');res.end(await readFile(moduleURL));return;}
 res.setHeader('Content-Type','text/html');res.end(`<!doctype html><label>Paper units<select id="unit" data-paper-unit-selector><option value="in">in</option><option value="mm">mm</option></select></label>
 <label>Width · <span data-paper-unit></span><input id="width" data-paper-length type="number" min="10" max="1600" value="279.4"></label>
 <input id="margin" data-paper-length type="number" min="0" max="200" value="39.425123456789">
 <label>Area · <span data-paper-unit data-paper-power="2"></span><input id="area" data-paper-length data-paper-power="2" type="number" min="0" max="1000" value="645.16"></label>
 <label>Pen diameter · mm<input id="pen" type="number" value="0.25"></label>
 <script type="module">import * as paper from '/paper-units.js';window.paper=paper;paper.bindPaperUnits();window.geometry=()=>['width','margin','area'].map(id=>paper.readPaperField(document.getElementById(id)));window.edits=0;document.addEventListener('input',()=>edits++);</script>`);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));const base=`http://127.0.0.1:${server.address().port}`;
const browser=await (process.env.PLOTLINE_BROWSER==='firefox'?firefox:chromium).launch(),context=await browser.newContext(),errors=[];
context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
async function tab(){const p=await context.newPage();await p.goto(base);await p.waitForFunction(()=>window.geometry);return p;}
try{
 const a=await tab(),b=await tab(),before=await a.evaluate(()=>geometry());
 assert.equal(await a.locator('#unit').inputValue(),'in');assert.equal(await a.locator('#width').inputValue(),'11');assert.equal(await a.locator('#area').inputValue(),'1');
 assert.equal(await a.evaluate(()=>paper.paperSize([279.4,215.9])),'11 × 8.5 in');
 for(let i=0;i<10;i++){await a.locator('#unit').selectOption('mm');await a.locator('#unit').selectOption('in');}
 assert.deepEqual(await a.evaluate(()=>geometry()),before,'Repeated toggles preserve exact canonical values');assert.equal(await a.evaluate(()=>edits),0);
 await a.locator('#unit').selectOption('mm');await b.waitForFunction(()=>document.getElementById('unit').value==='mm');
 assert.equal(await b.locator('#width').inputValue(),'279.4');assert.equal(await b.locator('#area').inputValue(),'645.16');assert.equal(await b.locator('#pen').inputValue(),'0.25');
 assert.equal(await b.evaluate(()=>paper.paperSize([160.528,254.508])),'160.53 × 254.51 mm');
 assert.deepEqual(await b.evaluate(()=>geometry()),before);
 await a.locator('#width').fill('300');await b.locator('#unit').selectOption('in');
 await a.waitForFunction(()=>document.getElementById('unit').value==='in');assert.equal((await a.evaluate(()=>geometry()))[0],300,'Cross-tab change preserves a pending edit in its original units');
 assert.equal((await a.evaluate(()=>geometry()))[1],before[1],'Editing another field does not round a measured margin');
 await a.locator('#width').fill('12');assert.equal((await a.evaluate(()=>geometry()))[0],304.8);
 await a.locator('#area').fill('0.5');assert.equal((await a.evaluate(()=>geometry()))[2],322.58);
 await a.locator('#width').fill('100');assert.equal(await a.locator('#width').evaluate(e=>e.checkValidity()),false,'Converted input bounds are enforced');
 await a.locator('#width').fill('');await b.locator('#unit').selectOption('mm');await a.waitForFunction(()=>document.getElementById('unit').value==='mm');assert.equal(await a.locator('#width').inputValue(),'');
 await a.reload();await a.waitForFunction(()=>window.geometry);assert.equal(await a.locator('#unit').inputValue(),'mm','Preference survives reload');
 const migrated=await browser.newContext();await migrated.addInitScript(()=>localStorage.setItem('squidprint-paper-unit','mm'));const old=await migrated.newPage();await old.goto(base);await old.waitForFunction(()=>window.geometry);assert.equal(await old.locator('#unit').inputValue(),'mm');await migrated.close();
 const blocked=await browser.newContext();await blocked.addInitScript(()=>Object.defineProperty(window,'localStorage',{get(){throw Error('Storage blocked');}}));const privatePage=await blocked.newPage();await privatePage.goto(base);await privatePage.waitForFunction(()=>window.geometry);await privatePage.locator('#unit').selectOption('mm');assert.equal(await privatePage.locator('#width').inputValue(),'279.4');await blocked.close();
 assert.deepEqual(errors,[]);console.log('PASS shared inch default, exact round trips, dimensions/areas, pen separation, cross-tab updates/pending edits, physical bounds, persistence, migration and blocked storage');
}finally{await browser.close();await new Promise(r=>server.close(r));}
