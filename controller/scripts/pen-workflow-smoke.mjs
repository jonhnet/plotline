// Isolated real PDF worker + both Plotline Studios UIs. Never configures a device.
import {chromium} from 'playwright';
import {spawn,execFileSync} from 'node:child_process';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const root=resolve(import.meta.dirname,'../..'),state=await mkdtemp(root+'/build/pen-browser-');
const project=process.env.PLOTLINE_TEST_PROJECT||root;
const reserve=createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const web=`http://ink-studio.test:${port}`,base=`http://127.0.0.1:${port}`,headers={'X-Plotline-Request':'1','Content-Type':'application/json'},sleep=ms=>new Promise(r=>setTimeout(r,ms));
const server=spawn(process.env.PLOTLINE_TEST_BINARY||root+'/controller/target/release/plotline-studios',['--listen',`127.0.0.1:${port}`,'--state',state],{cwd:root,env:{...process.env,PLOTLINE_PDF_PROJECT:project},stdio:['ignore','ignore','pipe']});let logs='',browser;
server.stderr.on('data',s=>logs+=s);
async function api(path,body,method='POST'){const r=await fetch(base+path,body?{method,headers,body:JSON.stringify(body)}:{});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;}
async function ready(path){for(let i=0;i<300;i++){const v=await api(path);if(!['received','inspecting','queued','running'].includes(v.status)){assert.equal(v.status,'ready',v.error);return v;}await sleep(100);}throw Error('worker timeout');}
async function upload(pdf,intent){const body=new FormData();body.append('document',new Blob([pdf]),'Pens.pdf');if(intent)body.append('intent',new Blob([JSON.stringify(intent)]),'intent.json');const r=await fetch(base+'/api/v1/documents',{method:'POST',headers:{'X-Plotline-Request':'1'},body});assert(r.ok);return ready((await r.json()).status_url);}
try{
  for(let i=0;;i++){try{if((await fetch(base+'/api/status')).ok)break;}catch{}if(i>100)throw Error(logs);await sleep(50);}
  let carousel=await api('/api/carousel');
  for(const [n,color,width] of [[1,'#000000',.5],[2,'#ff0000',.3],[5,'#008000',.4],[7,'#0000ff',null]])carousel.slots[n]={label:color,state:'loaded',color,width_mm:width,speed_cm_s:5,force:4};
  carousel.slots[3].state='unavailable';carousel=await api('/api/carousel',carousel,'PUT');
  const intent=JSON.parse(await readFile(project+'/build/pdf-boundary-v1/boundary.print-intent.json'));intent.pages=intent.pages.slice(0,1);
  const doc=await upload(await readFile(project+'/build/pdf-boundary-v1/boundary.pdf'),intent);
  const conversion=await ready((await api(`/api/v1/documents/${doc.id}/draft`,intent)).status_url);
  let saved;for(let i=0;i<100;i++){saved=await api(`/api/v1/documents/${doc.id}`);if(saved.draft.status==='ready')break;await sleep(100);}
  const jobId=saved.draft.jobs[0].job_id,initial=await api(`/api/jobs/${jobId}/setup`);
  assert.equal(initial.pen_map[1],0);assert.equal(initial.pen_map[2],0,'No arbitrary red fallback for a black ink');
  browser=await chromium.launch({headless:true,args:['--no-sandbox','--host-resolver-rules=MAP ink-studio.test 127.0.0.1','--no-proxy-server']});const context=await browser.newContext({viewport:{width:1500,height:1100}}),page=await context.newPage(),errors=[];
  let conversionPosts=0;context.on('request',r=>{if(r.method()==='POST'&&/\/documents\/[^/]+\/(conversions|draft)$/.test(new URL(r.url()).pathname))conversionPosts++;});
  context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));page.on('pageerror',e=>errors.push(e.message));
  await page.goto(web+'/#'+jobId);await page.locator('#pens .pen-cap').first().waitFor();
  assert.equal(await page.getByRole('button',{name:'Use document pens',exact:true}).count(),0);
  await page.locator('#match-loaded-pens').click();
  await page.locator('#notice').filter({hasText:'Mapped to loaded pens.'}).waitFor();
  const mapped=await page.evaluate(()=>sessionStorage.getItem('plotline-hpgl-workspace'));
  await page.getByRole('button',{name:'Dismiss message',exact:true}).click();
  await page.locator('#notice').waitFor({state:'hidden'});
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('plotline-hpgl-workspace')),mapped,'Dismissing the notice preserves the mapped pens');
  assert(await page.locator('#preview-canvas').isVisible());
  await page.getByLabel('Pen arrangement', {exact:true}).selectOption('document');
  assert.equal(await page.evaluate(()=>isSecureContext),false);assert.equal(await page.evaluate(()=>typeof crypto.randomUUID),'undefined');
  // Acceptance must be reachable without scrolling the pen drawer, even on a
  // short phone viewport. isVisible() alone misses controls clipped by overflow.
  for(const viewport of [{width:1440,height:800},{width:420,height:700}]){
    await page.setViewportSize(viewport);
    await page.getByLabel('Pen change to review',{exact:true}).selectOption('2');
    for(const bottom of [false,true]){
      await page.locator('.ink-drawer-body').evaluate((e,bottom)=>{e.scrollTop=bottom?e.scrollHeight:0;},bottom);
      assert(await page.locator('#pen-reviews button').evaluateAll(buttons=>buttons.every(b=>{
        const r=b.getBoundingClientRect(),drawer=document.querySelector('#ink-drawer').getBoundingClientRect();
        return r.top>=drawer.top&&r.bottom<=Math.min(innerHeight,drawer.bottom)&&r.left>=0&&r.right<=innerWidth&&b.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
      })),'Both substitution actions stay visible and clickable while the pens scroll');
    }
    await page.screenshot({path:state+`/review-${viewport.width}.png`});
  }
  await page.setViewportSize({width:1500,height:1100});
  await page.getByLabel('Pen change to review',{exact:true}).selectOption('1');
  for(const id of [1,2]){
    const review=page.locator(`[data-review-ink="${id}"]`);
    assert(await review.getByRole('button',{name:'Rebuild upstream',exact:true}).isVisible());
    await review.getByRole('button',{name:'Use existing paths',exact:true}).click();
  }
  await page.waitForFunction(()=>document.querySelector('#export-plan').disabled===false);await sleep(450);
  await page.locator('#save').click();await page.locator('#save-form button[type=submit]').click();
  await page.waitForFunction(()=>document.getElementById('save-state').textContent==='Saved');
  let setup=(await api(`/api/jobs/${jobId}/recipes`))[0].setup;assert.equal(setup.pen_map[1],setup.pen_map[2]);assert(setup.accepted_substitutions[1]);assert(setup.accepted_substitutions[2]);
  const output=await fetch(base+`/api/jobs/${jobId}/prepared.hpgl`,{method:'POST',headers,body:JSON.stringify(setup)});assert(output.ok,await output.text());
  await page.screenshot({path:state+'/hpgl-pens.png',fullPage:true});
  await page.locator('#pens [data-ink-id="1"]').click();await page.locator('#pens .pen-cap[data-slot="2"]').first().click();
  assert.equal(await page.locator('[data-review-ink="1"]').count(),0,'An explicit slot choice accepts its material without another confirmation');
  const workspace=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('plotline-hpgl-workspace')));
  const chosen=workspace.documents.find(d=>d.key===workspace.active).recipe;
  assert.equal(chosen.accepted_substitutions[1].width_mm,.3);
  assert.equal(chosen.accepted_substitutions[1].color,'#ff0000');
  assert.equal(chosen.accepted_substitutions[1].slot,2);
  await page.reload();await page.locator('#preview-canvas[data-ready=true]').waitFor();
  assert.equal(await page.locator('[data-review-ink="1"]').count(),0,'The explicit material choice survives reload');
  assert(await page.locator('#export-plan').isEnabled());
  await page.getByLabel('Pen arrangement',{exact:true}).selectOption('document');
  await page.locator('#pens [data-ink-id="1"]').click();await page.locator('#pens .pen-cap[data-slot="1"]').first().click();
  // Inventory drift still needs review, without silently accepting new widths.
  carousel.slots[1].width_mm=.6;carousel=await api('/api/carousel',carousel,'PUT');
  await page.locator('#carousel-refresh').click();
  await page.locator('[data-review-ink="1"]').waitFor();
  assert(await page.locator('#export-plan').isDisabled());
  const tabPromise=context.waitForEvent('page');await page.locator('[data-review-ink="1"]').getByRole('button',{name:'Rebuild upstream',exact:true}).click();const pdf=await tabPromise;
  await pdf.waitForURL(/\/pdf\?/);await pdf.locator('#edit-inks').click();await pdf.locator('#inks .pen-cap').first().waitFor();await pdf.waitForFunction(()=>document.querySelector('#notice').textContent.includes('substitutions applied'));
  assert.equal(new URL(page.url()).hash,'#'+jobId);assert.equal(await pdf.evaluate(()=>window.opener?.location.pathname),'/hpgl'); // Related studio windows retain their opener for focus.
  assert.equal(await pdf.locator('#inks input').count(),0);assert.equal(await pdf.locator('#stroke-mode').inputValue(),'preserve');
  assert.equal(await pdf.locator('#inks select').count(),0);await pdf.locator('#inks [data-ink-id="fine-black"]').click();
  if(await pdf.locator('.ink-member-tray').count())await pdf.locator('.ink-member-choice[data-source-id="fine-black"]').click();
  assert.equal(await pdf.locator('dialog[open]').count(),0);await pdf.keyboard.press('Escape');
  carousel.slots[1].width_mm=.7;carousel=await api('/api/carousel',carousel,'PUT');setup.carousel_revision=carousel.revision;
  const stale=await fetch(base+`/api/jobs/${jobId}/prepared.hpgl`,{method:'POST',headers,body:JSON.stringify(setup)});assert.equal(stale.status,422);assert.match(await stale.text(),/Use existing paths/);
  // A generic PDF has many immutable widths, default single-pass treatment,
  // and a small grouped view. Assigning one group is an explicit n:1 mapping.
  execFileSync(project+'/.venv/bin/python',['-c',`import pymupdf\nd=pymupdf.open();p=d.new_page(width=300,height=200)\nfor i in range(24):p.draw_line((30,20+i*6),(220,20+i*6),color=(0,.5,0),width=1+i*.003)\nd.save(${JSON.stringify(state+'/many.pdf')})`]);
  const generic=await upload(await readFile(state+'/many.pdf'));await pdf.goto(web+'/pdf?document='+generic.id);
  await pdf.locator('#edit-inks').click();await pdf.locator('#inks .pen-cap').first().waitFor();assert.equal(await pdf.locator('#stroke-mode').inputValue(),'pen');assert.equal(await pdf.locator('#ink-grouping,#inks select').count(),0);
  // The tiny source PDF is artwork, not a sheet wide enough for the HP7585.
  const profiles=(await api('/api/v1/capabilities')).media_profiles;
  const tabloid=profiles.findIndex(p=>p.id==='hp7585-tabloid-landscape');assert(tabloid>=0);
  await pdf.locator('#media').selectOption(String(tabloid));await pdf.locator('#fit').click();
  assert.match(await pdf.locator('#ink-note').textContent(),/24 source inks/);
  await pdf.locator('#inks .pen-stack .pen-cap').last().click();assert.equal(await pdf.locator('.ink-member-choice').count(),24);
  await pdf.getByRole('button',{name:'Select all 24',exact:true}).click();await pdf.locator('#inks .pen-cap[data-slot="2"]').click();
  assert.equal(await pdf.locator('dialog[open]').count(),0);
  await pdf.locator('#convert-button').click();await pdf.waitForURL(u=>u.searchParams.has('conversion'));await pdf.waitForFunction(()=>document.querySelector('#work-status').dataset.phase==='ready',null,{timeout:120000});
  const rev=await api('/api/v1/conversions/'+new URL(pdf.url()).searchParams.get('conversion'));assert.equal(Object.keys(rev.intent.ink_mapping).length,24);assert.equal(new Set(Object.values(rev.intent.ink_mapping)).size,1);assert.equal(rev.intent.policies.stroke_width_mode,'pen');
  // Editing an assigned physical pen must invalidate the preview without losing
  // its slot, editing the source inks, or automatically submitting another job.
  const active=()=>pdf.evaluate(()=>{const s=JSON.parse(sessionStorage.getItem('plotline-pdf-workspace'));return s.documents.find(d=>d.key===s.active);});
  const outputOf=recipe=>recipe.palette.inks.find(i=>i.id===Object.values(recipe.ink_mapping)[0]);
  const beforeEditPosts=conversionPosts;
  assert(await pdf.locator('#convert-button').isDisabled());
  await pdf.locator('#save').click();await pdf.locator('#save-name').fill('Live carousel recipe');await pdf.locator('#save-form button[type=submit]').click();
  await pdf.waitForFunction(()=>document.getElementById('saved-state').textContent==='Saved');
  await pdf.locator('#rotation').selectOption('90');
  await pdf.getByRole('button',{name:'Edit loaded pen — slot 2',exact:true}).click();
  await pdf.locator('#loaded-pen-form-2 input[name=width_mm]').fill('0.31');await pdf.locator('button.ink-confirm').click();
  await pdf.waitForFunction(()=>document.querySelector('#inks .pen-cap[data-slot="2"]').textContent==='0.31');
  assert.equal(outputOf((await active()).recipe).width_mm,.31);
  assert.equal(outputOf((await active()).recipe).source.follow_slot,true);
  await pdf.locator('#undo').click();assert.equal(await pdf.locator('#rotation').inputValue(),'0');
  assert.equal(outputOf((await active()).recipe).width_mm,.31,'Undo keeps the actual loaded pen');
  assert.equal(await pdf.locator('#saved-state').textContent(),'Saved','Loaded material is not a recipe edit');
  assert(await pdf.locator('#convert-button.primary').isEnabled());assert.equal(await pdf.locator('#update-plot.primary').count(),0);
  assert.equal(await pdf.locator('#inks .ink-pair[data-target-id="2"] .ink-count').textContent(),'24');
  assert.equal(conversionPosts,beforeEditPosts);
  assert.equal(outputOf((await api('/api/v1/conversions/'+rev.id)).intent).width_mm,.3,'Existing conversion inputs stay frozen');
  await pdf.locator('#convert-button').click();
  await pdf.waitForURL(u=>u.searchParams.has('conversion')&&u.searchParams.get('conversion')!==rev.id);
  await pdf.waitForFunction(()=>document.querySelector('#work-status').dataset.phase==='ready',null,{timeout:120000});
  const rebuilt=await api('/api/v1/conversions/'+new URL(pdf.url()).searchParams.get('conversion'));
  assert.equal(outputOf(rebuilt.intent).width_mm,.31);assert(await pdf.locator('#convert-button').isDisabled());
  // An unrelated slot and delivery-only settings do not change preview geometry.
  carousel=await api('/api/carousel');carousel.slots[1].width_mm=.71;carousel.slots[2].force=5;carousel.slots[2].speed_cm_s=6;
  carousel=await api('/api/carousel',carousel,'PUT');await pdf.getByRole('button',{name:'Refresh carousel',exact:true}).click();
  assert(await pdf.locator('#convert-button').isDisabled());
  // Identical material in another slot must not silently steal the assignment.
  carousel.slots[4]={...carousel.slots[2]};carousel.slots[2].color='#ff1493';
  carousel=await api('/api/carousel',carousel,'PUT');await pdf.getByRole('button',{name:'Refresh carousel',exact:true}).click();
  await pdf.waitForFunction(()=>!document.querySelector('#convert-button').disabled);
  assert.equal(outputOf((await active()).recipe).color_srgb,'#ff1493');
  assert.equal(await pdf.locator('#inks .ink-pair[data-target-id="2"] .ink-count').textContent(),'24');
  // Missing pen/diameter blocks conversion rather than using stale material.
  carousel.slots[2].width_mm=null;carousel=await api('/api/carousel',carousel,'PUT');await pdf.getByRole('button',{name:'Refresh carousel',exact:true}).click();
  await pdf.waitForFunction(()=>document.querySelector('#ink-note').textContent.startsWith('Slot 2:'));
  assert(await pdf.locator('#convert-button').isDisabled());assert(await pdf.locator('#update-plot').isDisabled());
  carousel.slots[2].width_mm=.32;carousel=await api('/api/carousel',carousel,'PUT');await pdf.getByRole('button',{name:'Refresh carousel',exact:true}).click();
  await pdf.waitForFunction(()=>!document.querySelector('#convert-button').disabled);
  // An inactive tab follows external changes too. Restoring the page resolves
  // the saved slot against the latest inventory without making the recipe dirty.
  await pdf.getByRole('tab').first().click();
  carousel.slots[2].width_mm=.33;carousel=await api('/api/carousel',carousel,'PUT');
  await pdf.waitForFunction(()=>{const s=JSON.parse(sessionStorage.getItem('plotline-pdf-workspace')),r=s.documents.find(d=>d.saved?.name==='Live carousel recipe').recipe;return r.palette.inks.find(i=>i.id===Object.values(r.ink_mapping)[0]).width_mm===.33;},null,{timeout:10000});
  await pdf.getByRole('tab',{name:'Live carousel recipe',exact:true}).click();
  await pdf.reload();await pdf.waitForFunction(()=>document.getElementById('saved-state').textContent==='Saved');
  assert.equal(outputOf((await active()).recipe).width_mm,.33);assert(await pdf.locator('#convert-button.primary').isEnabled());
  assert.equal(conversionPosts,beforeEditPosts+1,'Only the requested preview rebuild was submitted');
  // Restore red for the remaining assignment checks.
  carousel.slots[2].width_mm=.3;carousel.slots[2].color='#ff0000';carousel=await api('/api/carousel',carousel,'PUT');
  const grouped=await pdf.evaluate(async()=>{const {groupInks}=await import('/inksets.js');const inks=Array.from({length:10000},(_,i)=>({id:'s'+i,color_srgb:'#008000',width_mm:.3+i/100000})),mapping=Object.fromEntries(inks.map(i=>[i.id,1]));return groupInks(inks,mapping,'pen','preserve').map(g=>g.members.length);});assert.deepEqual(grouped,[10000]);
  // Twelve different unmatched source inks form one visible stack. Clicking
  // any part opens all twelve, with explicit group and individual assignment.
  execFileSync(project+'/.venv/bin/python',['-c',`import pymupdf\nd=pymupdf.open();p=d.new_page(width=300,height=200)\nfor i in range(12):p.draw_line((30,25+i*8),(220,25+i*8),color=(0,128/255,0),width=(.229+i*.003)*72/25.4,oc=d.add_ocg('green-'+str(i)))\np.draw_line((30,140),(220,140),color=(1,0,0),width=.3*72/25.4,oc=d.add_ocg('red'))\nd.save(${JSON.stringify(state+'/unmatched.pdf')})`]);
  const unmatchedBytes=await readFile(state+'/unmatched.pdf'),unmatchedIntent=structuredClone(intent);
  unmatchedIntent.source_sha256=createHash('sha256').update(unmatchedBytes).digest('hex');
  unmatchedIntent.pages=[{page_index:0,source_size_mm:[300*25.4/72,200*25.4/72],placement:{scale:1,rotation_deg:0,offset_mm:[0,0]}}];
  unmatchedIntent.palette.inks=[...Array.from({length:12},(_,i)=>({id:'green-'+i,label:'Green '+i,color_srgb:'#008000',width_mm:.229+i*.003})),{id:'red',label:'Red',color_srgb:'#ff0000',width_mm:.3}];
  unmatchedIntent.bindings=unmatchedIntent.palette.inks.map(i=>({ocg_name:i.id,ink_id:i.id}));
  const unmatched=await upload(unmatchedBytes,unmatchedIntent);await pdf.goto(web+'/pdf?document='+unmatched.id);await pdf.locator('#edit-inks').click();await pdf.locator('#inks .ink-count').waitFor();
  const stack=pdf.locator('#inks .ink-pair').filter({has:pdf.locator('.ink-count')});assert.equal(await stack.locator('.ink-count').textContent(),'12');
  await stack.locator('.pen-stack .pen-cap').last().click();assert.equal(await pdf.locator('.ink-member-choice').count(),12);assert.equal(await pdf.locator('dialog[open]').count(),0);
  // The text portion is just as clickable as the cap.
  await pdf.locator('.ink-member-choice').first().locator('span').last().click();
  assert.equal(await pdf.locator('.ink-selected-source .pen-cap').count(),1);assert.equal(await pdf.locator('.ink-member-tray').count(),0);
  await pdf.locator('.ink-scrim path').waitFor();await pdf.screenshot({path:state+'/assignment-scrim.png',fullPage:true});
  await pdf.locator('#inks .pen-cap[data-slot="5"]').click();assert.match(await pdf.locator('#notice').textContent(),/Assigned 1 source ink to slot 5/);
  const remaining=pdf.locator('#inks .ink-pair').filter({has:pdf.locator('.ink-count')});assert.equal(await remaining.locator('.ink-count').textContent(),'11');
  await remaining.locator('.ink-delta').click();await pdf.getByRole('button',{name:'Select all 11',exact:true}).click();
  await pdf.locator('#inks .pen-cap[data-slot="5"]').click();assert.equal(await pdf.locator('#inks .ink-count').textContent(),'12');
  // Red to pink exercises creating an output ink on a non-secure HTTP origin.
  carousel.slots[4]={state:'loaded',color:'#ff1493',width_mm:.5,label:'Pink',speed_cm_s:5,force:4};carousel=await api('/api/carousel',carousel,'PUT');
  await pdf.getByRole('button',{name:'Refresh carousel',exact:true}).click();await pdf.locator('#inks [data-ink-id="red"]').click();
  await pdf.locator('#inks .pen-cap[data-slot="4"]').click();assert.match(await pdf.locator('#notice').textContent(),/slot 4: Pink/);
  // PDF destinations are real loaded pens. Automatic assignment compares
  // color before width, and uses width to choose between identical colors.
  assert.equal(await pdf.getByRole('button',{name:'Create and assign a custom ink'}).count(),0);
  carousel.slots[6]={state:'loaded',color:'#008000',width_mm:.24,label:'Fine green',speed_cm_s:5,force:4};
  carousel.slots[8]={state:'loaded',color:'#008001',width_mm:.229,label:'Almost green',speed_cm_s:5,force:4};
  carousel=await api('/api/carousel',carousel,'PUT');await pdf.getByRole('button',{name:'Refresh carousel',exact:true}).click();
  const beforeAuto=conversionPosts;await pdf.getByRole('button',{name:'Map automatically',exact:true}).click();
  assert.match(await pdf.locator('#notice').textContent(),/Mapped 13 source inks/);
  assert.equal(await pdf.locator('#inks .ink-pair[data-target-id="6"] .ink-count').textContent(),'12','Exact green beats an almost-green pen with an exact width; finer exact green beats broad green');
  assert.equal(await pdf.locator('#inks .ink-pair[data-target-id="2"] [data-ink-id="red"]').count(),1);
  await sleep(100);assert.equal(conversionPosts,beforeAuto,'Automatic mapping must not submit the PDF form');
  await pdf.setViewportSize({width:420,height:900});await pdf.locator('#inks .ink-count').click();await pdf.getByRole('button',{name:'Select all 12',exact:true}).click();await pdf.locator('.ink-scrim path').waitFor();
  await pdf.screenshot({path:state+'/assignment-mobile.png',fullPage:true});
  await pdf.locator('#inks .pen-cap[data-slot="5"]').scrollIntoViewIfNeeded();await pdf.locator('#inks .pen-cap[data-slot="5"]').click();
  await pdf.setViewportSize({width:1500,height:1100});await pdf.locator('#convert-button').click();await pdf.waitForURL(u=>u.searchParams.has('conversion'));await pdf.waitForFunction(()=>document.querySelector('#work-status').dataset.phase==='ready',null,{timeout:120000});
  const assigned=await api('/api/v1/conversions/'+new URL(pdf.url()).searchParams.get('conversion'));
  const targets=Object.entries(assigned.intent.ink_mapping).filter(([id])=>id.startsWith('green-')).map(([,id])=>id);assert.equal(targets.length,12);assert.equal(new Set(targets).size,1);
  const green=assigned.intent.palette.inks.find(i=>i.id===targets[0]);assert.equal(green.color_srgb,'#008000');assert.equal(green.width_mm,.4);
  assert(await pdf.locator('#settings-scroll').evaluate(e=>e.scrollWidth<=e.clientWidth+1),'Only the pen strip scrolls horizontally');
  await pdf.locator('#inks').scrollIntoViewIfNeeded();await pdf.screenshot({path:state+'/pdf-pens.png',fullPage:true});await pdf.setViewportSize({width:700,height:900});assert(await pdf.locator('#convert-button').isVisible());
  assert.deepEqual(errors,[]);await writeFile(state+'/result.json',JSON.stringify({jobId,source:doc.id,generic:generic.id,conversion:rev.id}));
  console.log('PASS live carousel width/color invalidation, stable slots, undo and saved recipes, inactive-tab refresh, immutable conversions, shared pen UI, n:1 physical mapping, explicit substitution, stale acceptance, upstream PDF rebuild, immutable source groups, single-pass conversion, 10,000-style grouping, twelve-ink stack/individual assignment, carousel-only PDF destinations, color-first/width-second automatic mapping, non-secure HTTP red-to-pink and mobile direct assignment',state);
}catch(e){console.error(logs);throw e;}finally{await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));}
