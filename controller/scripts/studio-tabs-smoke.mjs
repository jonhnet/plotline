// Independent browser tabs, not only windows opened by each other. No backend or device.
import {chromium,firefox} from 'playwright';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const sharedURL=new URL('../../shared/web/',import.meta.url);
const server=createServer(async(req,res)=>{
 if(['/studio-navigation.js','/studio.js','/studio.css'].includes(req.url)){res.setHeader('Content-Type',req.url.endsWith('.css')?'text/css':'text/javascript');res.end(await readFile(new URL(req.url.slice(1),sharedURL)));return;}
 res.setHeader('Content-Type','text/html');
 res.end(`<!doctype html><link rel="stylesheet" href="/studio.css"><header class="masthead"><a class="brand">Plotline</a><div class="header-actions"></div></header>
 <a id="landing-hpgl" data-studio="hpgl" href="/hpgl">HPGL Studio</a><button id="open-plot">Open plot</button><div id="notice" role="status"></div>
 <a id="nav-home" data-studio="home" href="/">Home</a><a id="nav-map" data-studio="map" href="/map">Map</a><a id="nav-pdf" data-studio="pdf" href="/pdf">PDF</a><a id="nav-hpgl" data-studio="hpgl" href="/hpgl">HPGL</a>
 <script type="module">
 import {studioNavigation,openStudio} from '/studio-navigation.js';
 const studio=location.pathname.slice(1)||null;
 window.loads=performance.timeOrigin;window.requests=[];window.unsaved='working recipe';
 const options=new URLSearchParams(location.search);
 if(options.has('dropAck')){const Native=BroadcastChannel;window.BroadcastChannel=class extends Native{postMessage(data){if(data.type==='plotline-ack'&&!this.dropped){this.dropped=true;return;}super.postMessage(data);}};}

 const nav=studioNavigation({studio,notice:text=>document.getElementById('notice').textContent=text,open:url=>{requests.push(url.href);document.body.dataset.opened=url.href;}});
 document.getElementById('open-plot').onclick=()=>openStudio('hpgl','/hpgl#new-drawing');
 if(options.has('delay'))setTimeout(()=>nav.ready(),500);else nav.ready();document.body.dataset.ready='true';
 </script>`);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base=`http://127.0.0.1:${server.address().port}`;
const browser=await (process.env.PLOTLINE_BROWSER==='firefox'?firefox:chromium).launch(),context=await browser.newContext(),errors=[];
context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
async function tab(path){const p=await context.newPage();await p.goto(base+path);await p.locator('body[data-ready=true]').waitFor();return p;}
try{
 const hpgl=await tab('/hpgl'),pdf=await tab('/pdf');let home=await tab('/');
 assert.equal(await hpgl.evaluate(()=>window.opener),null,'HPGL was opened independently');
 const loaded=await hpgl.evaluate(()=>loads);
 await pdf.locator('#open-plot').click();
 await hpgl.waitForFunction(()=>requests.some(url=>url.endsWith('#new-drawing')),null,{timeout:2000});
 assert.equal(context.pages().length,3,'Open plot reuses an independently opened HPGL tab');
 await home.locator('#landing-hpgl').click();
 await hpgl.waitForFunction(()=>requests.some(url=>url.endsWith('/hpgl')),null,{timeout:2000});
 assert.equal(context.pages().length,3,'Landing page reuses an independently opened HPGL tab');
 assert.equal(await hpgl.evaluate(()=>loads),loaded,'Handoff does not reload the existing editor');
 assert.equal(await hpgl.evaluate(()=>unsaved),'working recipe');
 // Reload and retry don't turn a discovered independent tab into a new window.
 await hpgl.reload();await hpgl.locator('body[data-ready=true]').waitFor();await pdf.locator('#open-plot').click();
 await hpgl.waitForFunction(()=>requests.length===1);assert.equal(context.pages().length,3);
 // Exercise a queued request and a lost first acknowledgement. Retrying must
 // open the document just once, even while the receiving workspace restores.
 await hpgl.goto(base+'/hpgl?delay=1&dropAck=1');await hpgl.locator('body[data-ready=true]').waitFor();
 await pdf.locator('#open-plot').click();await hpgl.waitForFunction(()=>requests.length===1);
 assert.equal(context.pages().length,3);
 await home.locator('#landing-hpgl').click();await hpgl.waitForFunction(()=>requests.length===2);
 assert.equal(await hpgl.evaluate(()=>requests.filter(url=>url.endsWith('#new-drawing')).length),1);
 // Focus denial must never cause a duplicate or claim that the tab was raised.
 await hpgl.evaluate(()=>{window.focus=()=>{};document.hasFocus=()=>false;});
 await pdf.locator('#open-plot').click();await pdf.getByRole('status').filter({hasText:'existing HPGL Studio tab'}).waitFor();assert.equal(context.pages().length,3);
 assert.equal(await pdf.locator('.studio-notice').count(),1,'Navigation uses the shared notice surface');
 await pdf.getByRole('button',{name:'Dismiss message'}).click();await pdf.locator('#notice').waitFor({state:'hidden'});
 await pdf.locator('#open-plot').click();await pdf.locator('#notice').waitFor();await pdf.locator('#notice').waitFor({state:'hidden',timeout:7000});

 // Logos and the switcher reuse a landing tab while leaving each editor intact.
 const map=await tab('/map');
 let homeRequests=0;
 for(const page of [map,pdf,hpgl]){
  const before=await page.evaluate(()=>({url:location.href,loaded:loads,recipe:unsaved}));
  await page.locator('.brand').click();await home.waitForFunction(n=>requests.length===n,++homeRequests);
  assert.equal(context.pages().length,4,'Logo reuses the independent landing tab');
  assert.deepEqual(await page.evaluate(()=>({url:location.href,loaded:loads,recipe:unsaved})),before,'Logo preserves the studio editor');
 }
 await pdf.locator('.studio-switcher:not(.studio-sources)>summary').click();
 await pdf.getByRole('link',{name:'Plotline home',exact:true}).click();await home.waitForFunction(n=>requests.length===n,++homeRequests);
 await home.reload();await home.locator('body[data-ready=true]').waitFor();await hpgl.locator('.brand').click();await home.waitForFunction(()=>requests.length===1);
 assert.equal(context.pages().length,4,'Reloaded landing tab is reused');
 await home.close();const newHome=context.waitForEvent('page');await pdf.locator('.brand').click();home=await newHome;
 await home.waitForURL(base+'/');await home.locator('body[data-ready=true]').waitFor();
 assert.equal(new URL(pdf.url()).pathname,'/pdf','Opening a landing tab keeps the studio in place');
 await hpgl.locator('.brand').click();await home.waitForFunction(()=>requests.length===1);assert.equal(context.pages().length,4,'Other studios discover the newly opened landing tab');
 await map.close();
 // Closing a discovered tab makes a subsequent user request open one replacement.
 await hpgl.close();await home.reload();await home.locator('body[data-ready=true]').waitFor();
 const homeLoaded=await home.evaluate(()=>loads),replacementTab=context.waitForEvent('page');
 await home.locator('#landing-hpgl').click();const replacement=await replacementTab;await replacement.waitForURL(base+'/hpgl');await replacement.locator('body[data-ready=true]').waitFor();
 assert.equal(home.url(),base+'/','Opening a missing studio keeps Home open');
 assert.equal(await home.evaluate(()=>loads),homeLoaded,'Home is not reloaded');
 assert.equal(context.pages().length,3,'One replacement studio tab is opened');
 await home.locator('#landing-hpgl').click();await replacement.waitForFunction(()=>requests.length===1);
 assert.equal(context.pages().length,3,'A second Home click reuses the replacement');
 // Tabs opened by Plotline retain usable window references across parent reloads.
 const family=await browser.newContext(),parent=await family.newPage();
 family.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
 await parent.goto(base+'/map');await parent.locator('body[data-ready=true]').waitFor();
 let created=family.waitForEvent('page');await parent.locator('.brand').click();const landing=await created;await landing.locator('body[data-ready=true]').waitFor();
 created=family.waitForEvent('page');await parent.locator('#open-plot').click();const child=await created;await child.locator('body[data-ready=true]').waitFor();
 await parent.reload();await parent.waitForFunction(()=>window.__plotlineRelatedWindows?.size===2);
 await child.evaluate(()=>{const open=window.open;window.nativeTargets=[];window.open=(url,target,...rest)=>{nativeTargets.push(target);return open.call(window,url,target,...rest);};});
 const landingName=await landing.evaluate(()=>window.name);await child.locator('.brand').click();await landing.waitForFunction(()=>requests.length===1);
 assert((await child.evaluate(()=>nativeTargets)).includes(landingName),'A related tab gets a native foreground request after reload');
 assert.equal(family.pages().length,3,'Native focus reuses the verified tab');
 created=family.waitForEvent('page');await parent.locator('#nav-pdf').click();const sibling=await created;await sibling.locator('body[data-ready=true]').waitFor();
 const members=[parent,landing,child,sibling];
 // Firefox's native named-target reuse changes opener. Walking all directions
 // used to sever the old parent link after a few sibling-to-sibling switches.
 for(let round=0;round<2;round++){
  if(round)for(const page of members){await page.reload();await page.locator('body[data-ready=true]').waitFor();}
  for(const page of members){
   await page.waitForFunction(()=>window.__plotlineRelatedWindows?.size>0);
   await page.evaluate(()=>{const open=window.open;window.nativeTargets=[];window.open=(url,target,...rest)=>{nativeTargets.push(target);return open.call(window,url,target,...rest);};});
  }
  for(const source of members)for(const target of members){
   if(source===target)continue;
   const destination=new URL(target.url()).pathname.slice(1)||'home',name=await target.evaluate(()=>window.name),before=await target.evaluate(()=>({loaded:loads,requests:requests.length}));
   await source.evaluate(()=>nativeTargets.length=0);await source.locator('#nav-'+destination).click();
   await target.waitForFunction(n=>requests.length===n,before.requests+1);
   assert((await source.evaluate(()=>nativeTargets)).includes(name),`Retained window reference for ${new URL(source.url()).pathname} → ${destination}, round ${round}`);
   assert.equal(await target.evaluate(()=>loads),before.loaded,'Native targeting preserves the editor');
   assert.equal(family.pages().length,4,'Switching never opens an extra tab');
  }
 }
 await family.close();
 assert.deepEqual(errors,[]);console.log('PASS independent tabs from Open plot/landing, editor preservation, reload, startup queue, lost acknowledgement, dismissible/expiring focus notice, related-window recovery, landing logo/switcher reuse, and closed-tab replacement.');
}finally{console.log('Browser tabs:',context.pages().map(p=>p.url()));await browser.close();await new Promise(r=>server.close(r));}
