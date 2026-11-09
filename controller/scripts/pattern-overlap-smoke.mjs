// Run under dev-server.py run --command. Uses only that isolated session.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
const base=process.env.PLOTLINE_TEST_URL;
assert(base&&new URL(base).hostname==='127.0.0.1','An isolated loopback session is required');
const headers={'X-Plotline-Request':'1','Content-Type':'application/json'};
async function api(path,body){
  const response=await fetch(base+path,body?{method:'PUT',headers,body:JSON.stringify(body)}:{});
  const value=await response.json();assert(response.ok,JSON.stringify(value));return value;
}
assert.equal((await api('/api/status')).hardware_enabled,false);
assert.equal((await api('/api/v1/documents')).documents.length,0,'Use fresh test state');
const carousel=await api('/api/carousel');
for(const [i,color] of ['#000000','#ff0000','#0000ff'].entries())Object.assign(carousel.slots[i+1],{state:'loaded',color,width_mm:.3,label:['Black','Red','Blue'][i]});
await api('/api/carousel',carousel);
const browser=await chromium.launch({headless:true});
try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/patterns');await page.locator('#preview[data-ready=true]').waitFor();
  await page.locator('#family').selectOption('spirograph');await page.locator('#preview[data-ready=true]').waitFor();
  await page.locator('#prepare').click();
  await page.waitForFunction(()=>!document.querySelector('#open-plot').disabled||document.querySelector('#prepare').textContent==='Retry preparation',null,{timeout:120000});
  assert(await page.locator('#open-plot').isEnabled(),await page.locator('#work-detail').textContent());
  const receipt=await page.evaluate(()=>{const state=JSON.parse(sessionStorage.getItem('plotline-pattern-workspace'));return state.documents.find(d=>d.key===state.active).receipt;});
  const conversion=await api('/api/v1/conversions/'+receipt.conversion);
  assert.equal(conversion.intent.policies.overlap_strokes,true);
  assert.equal(conversion.intent.palette.inks.length,3);
  const summary=await api(conversion.result.pages[0].artifacts.summary);
  assert(summary.continuity.physical_runs<=16,'Eight curves should remain nearly continuous');
  assert.equal(summary.verification.decoded_hpgl.max_outside_allowed_area_pt2,0);
  await page.goto(base+`/pdf?document=${receipt.document}&conversion=${receipt.conversion}`);
  await page.waitForFunction(()=>document.querySelector('#work-status').dataset.phase==='ready');
  assert(await page.locator('#overlap-strokes').isChecked());
  assert(await page.locator('#convert-button').isDisabled());
  await page.locator('#overlap-strokes').uncheck();assert(await page.locator('#convert-button').isEnabled());
  await page.locator('#overlap-strokes').check();assert(await page.locator('#convert-button').isDisabled());
  assert.deepEqual(errors,[]);
  console.log('PASS Spirograph → PDF intent → HPGL → editable PDF policy:',summary.continuity.physical_runs,'physical runs');
}finally{await browser.close();}
