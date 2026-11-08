const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
const [base,out]=process.argv.slice(2);
(async()=>{
 const browser=await chromium.launch();try{
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/calibration');await page.waitForSelector('#pens input');
  // Configure Jon's new carousel through the same editor as the studios,
  // including a previously empty black slot and an unavailable slot 3.
  for(const [slot,name,color] of [[1,'K','#000000'],[2,'R','#ff0000'],[3,'Broken',null],[4,'G','#008000'],[5,'B','#0000ff'],[6,'C','#00ffff'],[7,'M','#ff00ff'],[8,'Y','#ffff00']]){
    await page.getByRole('button',{name:`Edit loaded pen — slot ${slot}`,exact:true}).click();
    await page.locator('dialog input[name=label]').fill(name);
    if(color)assert.equal(await page.locator('dialog input[name=color]').inputValue(),color);
    else await page.locator('dialog select').selectOption('unavailable');
    if(slot!==5)await page.locator('dialog input[name=width_mm]').fill('');
    await page.getByRole('button',{name:'OK',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#refresh').disabled);
  }
  assert(await page.getByRole('checkbox',{name:'Measure slot 1',exact:true}).isChecked());
  assert(await page.getByRole('checkbox',{name:'Measure slot 3',exact:true}).isDisabled());
  const edited=await(await page.request.get(base+'/api/carousel')).json();
  assert.equal(edited.slots[2].measured_color,'#5c5a58','Renaming the same color preserves its measurement');
  assert.equal(edited.slots[5].measured_color,undefined,'Changing a pen color clears the former ink measurement');
  assert.match(await page.locator('#marker-slot option[value=auto]').textContent(),/Slot 1 · K/);
  await page.getByRole('button',{name:'Edit loaded pen — slot 8',exact:true}).click();await page.locator('dialog input[name=label]').fill('Stale edit');
  const external=await(await page.request.get(base+'/api/carousel')).json();external.slots[3].label='Broken · external edit';
  const externalSave=await page.request.put(base+'/api/carousel',{headers:{'X-Plotline-Request':'1'},data:external});assert(externalSave.ok());const externalResult=await externalSave.json();
  await page.getByRole('button',{name:'OK',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('#refresh').disabled&&document.querySelector('#notice').textContent.length>0);
  assert.deepEqual(await(await page.request.get(base+'/api/carousel')).json(),externalResult,'A stale pen editor never overwrites another tab’s changes');
  const configured=await(await page.request.get(base+'/api/carousel')).json();
  // Escape/Cancel must preserve inventory, including a formerly empty slot.
  await page.getByRole('button',{name:'Edit loaded pen — slot 1',exact:true}).click();await page.locator('dialog input[name=label]').fill('Wrong pen');await page.getByRole('button',{name:'Cancel',exact:true}).click();
  assert.deepEqual(await(await page.request.get(base+'/api/carousel')).json(),configured);
  for(const slot of [1,4,6,7,8])await page.getByRole('checkbox',{name:`Measure slot ${slot}`,exact:true}).uncheck();
  await page.locator('#marker-slot').selectOption('1');
  await page.screenshot({path:out+'/carousel-setup.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:out+'/carousel-mobile.png',fullPage:true});await page.setViewportSize({width:1440,height:1100});
  const create=data=>page.request.post(base+'/api/calibrations',{headers:{'X-Plotline-Request':'1'},data:{slots:[2,5],revision:configured.revision,...data}});
  for(const marker_slot of [3,9]){const invalid=await create({marker_slot});assert.equal(invalid.status(),422);assert.match(await invalid.text(),/loaded pen for registration/);}
  const explicitResponse=await create({marker_slot:5});assert(explicitResponse.ok(),await explicitResponse.text());const explicit=await explicitResponse.json();
  assert.equal(explicit.marker_slot,5);assert(explicit.paths.filter(p=>p.kind!=='sample').every(p=>p.slot===5));
  const automaticResponse=await create({});assert(automaticResponse.ok(),await automaticResponse.text());assert.equal((await automaticResponse.json()).marker_slot,1);
  await page.locator('#paper').fill('Fixture cream paper');await page.locator('#create-button').click();
  await page.waitForFunction(()=>!document.querySelector('#sheet').hidden&&document.querySelector('#open-plot').href.includes('#'));
  const id=await page.evaluate(()=>location.hash.slice(1));const target=await(await page.request.get(base+'/api/calibrations/'+id)).json();
  assert.deepEqual(target.slots,[2,5]);assert.equal(target.marker_slot,1);assert(target.paths.filter(p=>p.kind!=='sample').every(p=>p.slot===1));
  assert.match(await page.locator('#sheet-detail').textContent(),/registration: slot 1 \(K\)/);
  fs.writeFileSync(out+'/target.json',JSON.stringify(target));
  const job=await(await page.request.get(base+'/api/jobs/'+target.job_id)).json();assert(job.calibration);assert(!job.pdf);
  const setup=await(await page.request.get(base+'/api/jobs/'+target.job_id+'/setup')).json();
  assert.deepEqual(setup.registration.sheet_size_mm,[279.4,215.9]);assert.equal(setup.rotation,90);
  assert(Math.abs(setup.paper_x_mm-215.9)<1e-8);assert(Math.abs(setup.paper_y_mm-279.4)<1e-8);
  assert(setup.margins_mm.every((n,i)=>Math.abs(n-[39,15,15,15][i])<1e-8));
  assert.match(await page.locator('#load-instructions').textContent(),/11 in edge across the carriage/);
  await page.locator('#paper-unit').selectOption('mm');assert.match(await page.locator('#load-instructions').textContent(),/279.4 mm edge across the carriage/);
  await page.locator('#paper-unit').selectOption('in');
  const prepared=await page.request.post(base+'/api/jobs/'+target.job_id+'/prepared.hpgl',{headers:{'X-Plotline-Request':'1'},data:setup});assert(prepared.ok(),await prepared.text());
  fs.writeFileSync(out+'/prepared.hpgl',await prepared.body());
  const plot=await browser.newPage();plot.on('pageerror',e=>errors.push(e.message));
  await plot.addInitScript(()=>{const post=Worker.prototype.postMessage;Worker.prototype.postMessage=function(data,...args){if(data.type==='render')window.lastPenRender=structuredClone(data.spec);return post.call(this,data,...args);};});
  await plot.goto(base+'/#'+target.job_id);await plot.waitForFunction(()=>!document.querySelector('#workspace').hidden);
  assert(await plot.locator('#paper-controls').isHidden());await plot.locator('#export-tools>summary').click();assert(await plot.locator('#calibration-open').isVisible());await plot.getByRole('button',{name:'Close downloads',exact:true}).click();
  assert(await plot.locator('#plot-open').isDisabled(),'Isolated service must never plot');assert(await plot.locator('#match-loaded-pens').isDisabled());
  await plot.waitForFunction(()=>document.querySelector('#preview-canvas').dataset.ready==='true');
  assert.equal(await plot.locator('#pen-readiness').textContent(),'','Unchanged pens from the library are ready regardless of JSON key order');
  assert.equal(await plot.locator('#drawer-warning').textContent(),'');
  assert.equal(await plot.locator('#preview-ink-colors').inputValue(),'design');
  assert.equal(await plot.evaluate(()=>window.lastPenRender.colors[2]),'#ff0000','A calibration sheet starts in its declared colors despite older measurements');
  // Existing workspaces had no distinction between the old default and a choice.
  await plot.addInitScript(()=>{if(!sessionStorage.getItem('fixture-old-appearance'))return;sessionStorage.removeItem('fixture-old-appearance');const key='plotline-hpgl-workspace',snapshot=JSON.parse(localStorage.getItem(key));for(const doc of snapshot.documents){doc.view.appearance='measured';delete doc.view.appearanceChosen;}for(const storage of [sessionStorage,localStorage])storage.setItem(key,JSON.stringify(snapshot));});
  await plot.evaluate(()=>sessionStorage.setItem('fixture-old-appearance','1'));
  await plot.reload();await plot.waitForFunction(()=>document.querySelector('#preview-canvas').dataset.ready==='true');
  assert.equal(await plot.locator('#preview-ink-colors').inputValue(),'design','Previously open sheets adopt the corrected default');
  await plot.locator('#preview-ink-colors').selectOption('measured');
  await plot.waitForFunction(()=>window.lastPenRender.colors[2]==='#5c5a58');
  await plot.reload();await plot.waitForFunction(()=>document.querySelector('#preview-canvas').dataset.ready==='true');
  assert.equal(await plot.locator('#preview-ink-colors').inputValue(),'measured','An explicit appearance choice survives reload');
  await plot.locator('#preview-ink-colors').selectOption('design');await plot.waitForFunction(()=>window.lastPenRender.colors[2]==='#ff0000');
  const footprint=await plot.evaluate(()=>{
    const canvas=document.querySelector('#preview-canvas'),rect=canvas.getBoundingClientRect(),window=document.querySelector('#drawing-window').getBoundingClientRect();
    const copy=document.createElement('canvas');copy.width=canvas.width;copy.height=canvas.height;const ctx=copy.getContext('2d');ctx.drawImage(canvas,0,0);const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;let count=0,outside=0;
    for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++)if(pixels[(y*canvas.width+x)*4+3]>50){
      count++;const px=rect.x+x/canvas.width*rect.width,py=rect.y+y/canvas.height*rect.height;
      if(px<window.left-2||px>window.right+2||py<window.top-2||py>window.bottom+2)outside++;
    }
    return {count,outside};
  });
  assert(footprint.count>100);assert.equal(footprint.outside,0,'Registered calibration artwork fits its displayed drawing window');
  await plot.screenshot({path:out+'/letter-target.png',fullPage:true});
  const limit=Date.now()+30000;while(!fs.existsSync(out+'/scan.ready')){assert(Date.now()<limit,'Fixture scan timeout');await new Promise(r=>setTimeout(r,100));}
  await page.locator('#scan-file').setInputFiles(out+'/scan.png');await page.locator('#analyze').click();
  await page.waitForFunction(()=>!document.querySelector('#results').hidden||!document.querySelector('#alignment').hidden,{},{timeout:60000});
  assert(await page.locator('#results').isVisible(),await page.locator('#status').textContent());
  assert.equal(await page.locator('#measurements tr').count(),2);assert.equal(await page.locator('#measurements input:checked').count(),4);
  await page.locator('#measurements input[data-slot="5"][data-kind="width"]').uncheck();
  const before=await(await page.request.get(base+'/api/carousel')).json();
  await page.screenshot({path:out+'/scan-review.png',fullPage:true});
  await page.locator('#apply').click();await page.waitForFunction(()=>document.querySelector('#applied').textContent.startsWith('Applied'));
  const after=await(await page.request.get(base+'/api/carousel')).json();
  assert.equal(after.revision,before.revision+1);assert(Math.abs(after.slots[2].width_mm-.3)<.04);assert.equal(after.slots[5].width_mm,.7);
  assert.equal(after.slots[2].force,before.slots[2].force);assert.deepEqual(after.slots[1],before.slots[1]);
  assert.equal(after.slots[2].color,before.slots[2].color,'Calibration preserves design color');
  assert.equal(after.slots[5].color,before.slots[5].color,'Calibration preserves every design color');
  const updated=await(await page.request.get(base+'/api/calibrations/'+id)).json();const scan=updated.scans[0];
  assert.equal(scan.status,'ready');for(const row of scan.report.rows)assert(row.valid);
  assert.equal(after.slots[2].measured_color,scan.report.rows[0].color_srgb);
  assert.equal(after.slots[5].measured_color,scan.report.rows[1].color_srgb);
  assert.notEqual(after.slots[2].measured_color,after.slots[2].color);
  await plot.reload();await plot.waitForFunction(()=>document.querySelector('#preview-canvas').dataset.ready==='true');
  assert.match(await plot.locator('#pen-readiness').textContent(),/Pens changed since this sheet was created/,'Real measurement changes still require a new sheet');
  await plot.locator('#preview-ink-colors').selectOption('measured');await plot.waitForFunction(color=>window.lastPenRender.colors[2]===color,after.slots[2].measured_color);
  const pixels=()=>plot.locator('#preview-canvas').evaluate(c=>{const copy=document.createElement('canvas');copy.width=c.width;copy.height=c.height;copy.getContext('2d').drawImage(c,0,0);return copy.toDataURL();});
  const measuredPixels=await pixels();await plot.locator('#preview-ink-colors').selectOption('design');
  await plot.waitForFunction(before=>{const c=document.querySelector('#preview-canvas'),copy=document.createElement('canvas');copy.width=c.width;copy.height=c.height;copy.getContext('2d').drawImage(c,0,0);return copy.toDataURL()!==before;},measuredPixels);
  await plot.getByRole('button',{name:'Edit loaded pen — slot 2',exact:true}).click();
  assert.equal(await plot.locator('dialog input[name=color]').inputValue(),before.slots[2].color);
  assert.match(await plot.locator('dialog[open]').textContent(),/Measured appearance/);
  await plot.getByRole('button',{name:'Cancel',exact:true}).click();
  assert.deepEqual(await(await page.request.get(base+'/api/carousel')).json(),after,'Preview controls and Cancel never edit inventory');
  const stale=await page.request.post(base+'/api/jobs/'+target.job_id+'/prepared.hpgl',{headers:{'X-Plotline-Request':'1'},data:setup});assert(!stale.ok(),'Changed inventory requires a new target');
  await page.reload();await page.waitForSelector('#results:not([hidden])');assert(await page.locator('#overlay').getAttribute('src'));
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.deepEqual(errors,[]);
  fs.writeFileSync(out+'/results.json',JSON.stringify({passed:true,physical_plotting:false,checks:['Letter registration and unknown widths','editable carousel and independent registration pen','key-order-independent readiness','design-color calibration preview and restored views','stale appearance cleared on pen color change','actual browser target creation and scan upload','synthetic rotated/tinted-paper scan','independent width/color selection','durable measurements and inventory','stale-target rejection','reload and mobile layout']}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
