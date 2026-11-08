// Shared notification behavior, including status polling and asynchronous actions.
// A static fixture only: no document storage or device connection.
import {chromium,firefox} from 'playwright';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const shared=new URL('../../shared/web/',import.meta.url);
const server=createServer(async(req,res)=>{
  if(['/studio.js','/studio.css'].includes(req.url)){
    res.setHeader('Content-Type',req.url.endsWith('.js')?'text/javascript':'text/css');
    res.end(await readFile(new URL(req.url.slice(1),shared)));return;
  }
  res.setHeader('Content-Type','text/html');
  res.end(`<!doctype html><link rel="stylesheet" href="/studio.css"><body class="studio">
    <button id="origin">Preview pen strokes</button><div style="overflow:hidden;height:1px"><div id="notice" hidden></div></div>
    <script type="module">import {studioNotice} from '/studio.js';window.show=studioNotice;</script>`);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await (process.env.PLOTLINE_BROWSER==='firefox'?firefox:chromium).launch();
const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
  await page.goto(`http://127.0.0.1:${server.address().port}`);await page.waitForFunction(()=>window.show);
  await page.locator('#origin').focus();
  await page.evaluate(()=>show('Mapped to loaded pens. Review any differences below to use existing paths or rebuild.'));
  const box=page.locator('#notice'),close=page.getByRole('button',{name:'Dismiss message',exact:true});
  assert(await close.isVisible(),'Close is outside the clipped preview container');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'origin','Showing a notice does not steal focus');
  await close.click();assert(await box.isHidden());
  assert.equal(await page.evaluate(()=>document.activeElement.id),'origin','Dismissal returns focus to the initiating control');
  await page.evaluate(()=>show('Mapped to loaded pens. Review any differences below to use existing paths or rebuild.'));
  assert(await box.isHidden(),'Repeated status renders do not resurrect a dismissed message');
  await page.evaluate(()=>{show();show('Mapped to loaded pens. Review any differences below to use existing paths or rebuild.');});
  assert(await box.isVisible(),'A new operation may show the same message again');
  await page.evaluate(()=>{window.oldDismiss=show('Opening another tab',{timeout:75,source:'navigation'});show(new Error('New conversion error <img src=x>'));oldDismiss();});
  await page.waitForTimeout(125);assert.match(await box.textContent(),/New conversion error <img src=x>/);
  assert.equal(await box.locator('img').count(),0,'Messages are text, never HTML');
  await page.evaluate(()=>show('Open the plot',{source:'navigation',actions:[{label:'Open here',href:'/hpgl'}]}));
  await page.evaluate(()=>show());assert(await box.isVisible(),'Clearing an application status does not clear a navigation action');
  assert.equal(await box.getByRole('link',{name:'Open here'}).getAttribute('href'),'/hpgl');
  await page.evaluate(()=>show('Move documents',{actions:[{label:'Move open documents',onClick:()=>window.moved=true}]}));
  await box.getByRole('button',{name:'Move open documents'}).click();assert(await page.evaluate(()=>window.moved));
  await page.evaluate(()=>show('Temporary feedback',{timeout:50}));await box.waitFor({state:'hidden'});
  for(const size of [{width:1280,height:800},{width:360,height:640}]){
    await page.setViewportSize(size);await page.evaluate(()=>{show();show(('Very long error message without truncation.\n').repeat(250));});
    const bounds=await close.boundingBox();assert(bounds&&bounds.x>=0&&bounds.x+bounds.width<=size.width&&bounds.y+bounds.height<=size.height,'Close remains on screen for long messages');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Notice fits narrow screens');
    assert.equal(await page.locator('.studio-notice').count(),1,'There is only one notice surface');
    await close.click();assert(await box.isHidden());
  }
  assert.deepEqual(errors,[]);
  console.log('PASS shared notice dismissal, polling suppression/reset, focus, text safety, stale timers/actions, navigation ownership, links/buttons and long-message mobile layout');
}finally{await browser.close();await new Promise(r=>server.close(r));}
