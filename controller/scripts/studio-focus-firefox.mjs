// Native Firefox tab selection, without Playwright's patched focus/visibility.
// Node 20: PLOTLINE_FIREFOX_BINARY=/path/to/firefox node --experimental-websocket ...
// Uses an isolated profile and fixture server; never connects to a plotter.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'node:http';

assert(process.env.PLOTLINE_FIREFOX_BINARY,'Set PLOTLINE_FIREFOX_BINARY to an ordinary Firefox executable (not the Playwright build).');
assert(globalThis.WebSocket,'Node 20 requires --experimental-websocket.');
const profile=await mkdtemp(join(tmpdir(),'plotline-focus-'));
await writeFile(join(profile,'user.js'),[
 'user_pref("focusmanager.testmode", false);',
 'user_pref("browser.shell.checkDefaultBrowser", false);',
 'user_pref("browser.startup.homepage_override.mstone", "ignore");',
 'user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);',
].join('\n'));
const moduleURL=new URL('../../shared/web/studio-navigation.js',import.meta.url);
const server=createServer(async(req,res)=>{
 if(req.url==='/studio-navigation.js'){res.setHeader('Content-Type','text/javascript');res.end(await readFile(moduleURL));return;}
 res.setHeader('Content-Type','text/html');
 res.end(`<header class="masthead"><a class="brand">Plotline</a><div class="header-actions"></div></header>
 <main>${['home','map','pdf','hpgl'].map(s=>`<p><a id="${s}" data-studio="${s}" href="${s==='home'?'/':'/'+s}">${s}</a></p>`).join('')}</main>
 <script type="module">import {studioNavigation} from '/studio-navigation.js';
 window.requests=[];window.unsaved='working recipe';window.loads=performance.timeOrigin;
 const nav=studioNavigation({studio:location.pathname.slice(1)||null,open:url=>requests.push(url.href)});
 nav.ready();window.ready=true;</script>`);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
const proc=spawn(process.env.PLOTLINE_FIREFOX_BINARY,['--headless','--no-remote','--profile',profile,'--remote-debugging-port','0','about:blank']);
let ws;
const exited=new Promise(r=>proc.once('exit',r));
try{
 const endpoint=await new Promise((resolve,reject)=>{
  let log='';const timer=setTimeout(()=>reject(Error('Firefox did not start: '+log)),15000);
  proc.once('error',error=>{clearTimeout(timer);reject(error);});
  proc.stderr.on('data',data=>{log+=data;const match=log.match(/WebDriver BiDi listening on (ws:\/\/\S+)/);if(match){clearTimeout(timer);resolve(match[1]);}});
 });
 ws=new WebSocket(endpoint+'/session');await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
 let id=0;const pending=new Map();
 ws.onmessage=event=>{const reply=JSON.parse(event.data),request=pending.get(reply.id);if(!request)return;pending.delete(reply.id);clearTimeout(request.timer);reply.type==='error'?request.reject(Error(JSON.stringify(reply))):request.resolve(reply.result);};
 const cmd=(method,params={})=>new Promise((resolve,reject)=>{const n=++id,timer=setTimeout(()=>{pending.delete(n);reject(Error('Timed out: '+method));},15000);pending.set(n,{resolve,reject,timer});ws.send(JSON.stringify({id:n,method,params}));});
 const evaluate=async(context,expression)=>{const r=await cmd('script.evaluate',{expression,target:{context},awaitPromise:true});assert.equal(r.type,'success',JSON.stringify(r));return r.result.value;};
 const pause=ms=>new Promise(r=>setTimeout(r,ms));
 const until=async(fn,label)=>{for(let i=0;i<100;i++){if(await fn())return;await pause(50);}throw Error('Timed out: '+label);};
 const tabs=async()=> (await cmd('browsingContext.getTree')).contexts;
 const ready=context=>until(()=>evaluate(context,'!!window.ready'),'fixture ready');
 const click=async(context,target)=>{
  await cmd('browsingContext.activate',{context});
  const [x,y]=JSON.parse(await evaluate(context,`JSON.stringify((()=>{const r=document.getElementById('${target}').getBoundingClientRect();return [Math.floor(r.x+r.width/2),Math.floor(r.y+r.height/2)]})())`));
  // A real input action matters: script.evaluate(userActivation:true) doesn't
  // exercise Firefox's native click/popup focus path.
  await cmd('input.performActions',{context,actions:[{type:'pointer',id:'mouse',parameters:{pointerType:'mouse'},actions:[{type:'pointerMove',x,y},{type:'pointerDown',button:0},{type:'pointerUp',button:0}]}]});
 };
 const session=await cmd('session.new',{capabilities:{}});
 const root=(await tabs())[0].context;
 await cmd('browsingContext.navigate',{context:root,url:base+'/',wait:'complete'});await ready(root);
 const homeLoaded=await evaluate(root,'loads');
 for(const destination of ['map','pdf','hpgl']){
  const count=(await tabs()).length;await click(root,destination);
  await until(async()=> (await tabs()).length===count+1,'new studio tab');
  for(const page of await tabs())await ready(page.context);
  assert.equal(await evaluate(root,'location.pathname'),'/','Opening a studio keeps Home open');
  assert.equal(await evaluate(root,'loads'),homeLoaded,'Home is not reloaded');
 }
 const members=await tabs();assert.equal(members.length,4);
 assert.equal(new Set(members.map(t=>t.clientWindow)).size,1,'All four pages are browser tabs in one window');
 for(let round=0;round<2;round++){
  if(round)for(const page of members){await cmd('browsingContext.reload',{context:page.context,wait:'complete'});await ready(page.context);}
  for(const source of members)for(const target of members){
   if(source.context===target.context)continue;
   const destination=new URL(target.url).pathname.slice(1)||'home';
   const before=JSON.parse(await evaluate(target.context,'JSON.stringify({loads,requests:requests.length})'));
   await click(source.context,destination);
   await until(()=>evaluate(target.context,`requests.length===${before.requests+1}`),'document handoff');
   await until(()=>evaluate(target.context,"document.visibilityState==='visible'"),`${source.url} → ${destination} foreground, round ${round}`);
   await until(()=>evaluate(source.context,"document.visibilityState==='hidden'"),`${source.url} background after switching to ${destination}, round ${round}`);
   assert.equal(await evaluate(target.context,'loads'),before.loads,'No editor reload');
   assert.equal(await evaluate(target.context,'unsaved'),'working recipe');
   assert.equal((await tabs()).length,4,'No extra tabs');
  }
 }
 console.log(`PASS Firefox ${session.capabilities.browserVersion}: all 12 directions foreground the existing tab, before and after reloading all four; editors preserved, no duplicates.`);
}finally{
 ws?.close();proc.kill();await exited;server.close();await rm(profile,{recursive:true,force:true});
}
