// HPGL preview gestures against isolated state. No device configuration or plots.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:net';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..'),state=await mkdtemp(`${tmpdir()}/plotline-navigation-`);
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));
const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const url=`http://127.0.0.1:${port}`,server=spawn(process.env.PLOTLINE_BINARY||resolve(root,'controller/target/release/plotline-studios'),['--state',state,'--listen',`127.0.0.1:${port}`],{stdio:['ignore','ignore','pipe']});
let browser,logs='';server.stderr.on('data',data=>logs+=data);
try{
  for(let i=0;;i++){
    try{if((await fetch(url+'/api/status')).ok)break;}catch{}
    if(i>100)throw Error(logs);await new Promise(r=>setTimeout(r,50));
  }
  const response=await fetch(url+'/api/jobs?name=Navigation.hpgl&units_per_mm=40',{
    method:'POST',headers:{'X-Plotline-Upload':'1'},body:'IN;PA;SP1;PU0,0;PD4000,0,4000,3000,0,3000,0,0;PU;SP0;',
  });assert.equal(response.status,201);const job=await response.json();
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const context=await browser.newContext({viewport:{width:1440,height:1100}}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(url+'/#'+job.id);
  await page.locator('#preview-canvas[data-ready="true"]').waitFor();
  await page.locator('#fit').click();await page.waitForTimeout(400);
  const setup=await (await fetch(url+`/api/jobs/${job.id}/setup`)).json();
  let writes=0;page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))writes++;});
  const box=await page.locator('#viewport').boundingBox(),x=Math.round(box.x+box.width*.42),y=Math.round(box.y+box.height*.47);
  const view=async()=> (await page.locator('#paper-scene').getAttribute('viewBox')).split(/\s+/).map(Number);
  const zoom=async()=>parseFloat(await page.locator('#zoom').textContent());
  const before=await view();
  await page.mouse.move(x,y);await page.mouse.wheel(0,-150);await page.waitForTimeout(250);
  assert(await zoom()>100);const enlarged=await view();
  for(let i=0;i<2;i++){
    const fraction=i?(y-box.y)/box.height:(x-box.x)/box.width;
    const error=before[i]+before[i+2]*fraction-enlarged[i]-enlarged[i+2]*fraction;
    assert(Math.abs(error)<.01,`Zoom keeps the point under the cursor: axis ${i}, error ${error}, box ${JSON.stringify(box)}, before ${before}, after ${enlarged}`);
  }
  await page.mouse.down();await page.mouse.move(x+50,y+25,{steps:5});await page.mouse.up();await page.waitForTimeout(200);
  const moved=await view();assert(Math.abs((enlarged[0]-moved[0])*box.width/moved[2]-50)<.01);
  // Ctrl-wheel is how trackpad pinch arrives; Shift has no editing meaning here.
  await page.locator('#viewport').dispatchEvent('wheel',{clientX:x,clientY:y,deltaY:-100,ctrlKey:true});
  await page.locator('#viewport').dispatchEvent('wheel',{clientX:x,clientY:y,deltaY:-100,shiftKey:true});
  await page.waitForTimeout(200);assert(await zoom()>150);
  await page.locator('#fit').click();await page.waitForTimeout(200);assert.deepEqual(await view(),before);
  const cdp=await context.newCDPSession(page);
  const touch=(type,points)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:points.map(([x,y,id])=>({x,y,id,radiusX:3,radiusY:3}))});
  await touch('touchStart',[[x-30,y,1],[x+30,y,2]]);await touch('touchMove',[[x-65,y,1],[x+65,y,2]]);await touch('touchEnd',[]);
  await page.waitForTimeout(200);assert(await zoom()>150);
  await page.locator('#viewport').focus();await page.keyboard.press('0');await page.waitForTimeout(200);assert.deepEqual(await view(),before);
  await page.keyboard.press('ArrowRight');await page.waitForTimeout(200);assert.notDeepEqual(await view(),before);
  await page.locator('#fit').click();await page.waitForTimeout(600);
  assert.equal(writes,0,'Inspection does not save settings or submit work');
  assert.deepEqual(await (await fetch(url+`/api/jobs/${job.id}/setup`)).json(),setup);
  assert.deepEqual(errors,[]);
  console.log('PASS HPGL mouse/pinch/keyboard navigation, cursor anchoring and Fit page; unchanged setup and zero writes.');
}finally{
  await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));await rm(state,{recursive:true,force:true});
}
