import assert from 'node:assert/strict';
import {defaults,generate,fitPaths,loadLimits} from '../../patterns/web/generators.js';
import {roundCurve} from '../../patterns/web/subdivision.js';

export async function checkCarpetCorners(context,base,api,template){
  await loadLimits();
  const recipe={...structuredClone(template),family:'carpet',name:'Legacy rounded carpet',params:{...defaults('carpet'),round:.3}};
  const square=generate('carpet',recipe.params,()=>{},{inkCount:recipe.roles.length});
  const paths=fitPaths({...square,paths:square.paths.map(p=>({...p,points:roundCurve(p.points,.3,true)}))},recipe).paths;
  const saved=await api('/api/patterns/documents',{recipe});
  const record=await api('/api/patterns/exports',{recipe,paths,document_id:saved.id});
  const snapshot=await api('/api/patterns/snapshots/'+record.hash),pdf=Buffer.from(await (await fetch(base+record.pdf_url)).arrayBuffer());
  const page=await context.newPage();await page.goto(base+'/patterns');await page.locator('#preview[data-ready=true]').waitFor();
  await page.evaluate(async({recipe,saved})=>{
    const {stable}=await import('/studio.js');
    const document={key:'legacy-carpet',recipe,saved:{id:saved.id,revision:saved.revision},savedRecipe:recipe,savedSignature:stable(recipe),receipt:{signature:stable(recipe),ready:true}};
    sessionStorage.setItem('plotline-pattern-workspace',JSON.stringify({active:document.key,documents:[document]}));
  },{recipe,saved});
  await page.reload();await page.locator('#preview[data-ready=true]').waitFor();
  const active=()=>page.evaluate(()=>{const s=JSON.parse(sessionStorage.getItem('plotline-pattern-workspace'));return s.documents.find(d=>d.key===s.active);});
  assert.equal((await active()).recipe.params.round,undefined);assert.equal(await page.locator('#knob-round').count(),0);
  assert(await page.locator('#open-plot').isDisabled(),'Rounded receipt cannot pass for the corrected square preview');
  assert((await page.getByRole('tab',{selected:true}).textContent()).includes('*'),'Correction is an unsaved working edit');
  assert.equal((await api('/api/patterns/documents/'+saved.id)).recipe.params.round,.3,'Opening never rewrites saved source');
  await page.locator('#revert').click();await page.locator('#preview[data-ready=true]').waitFor();assert.equal((await active()).recipe.params.round,undefined);
  await page.locator('#save').click();await page.waitForFunction(()=>!document.querySelector('#document-tabs [aria-selected=true]').textContent.includes('*'));
  assert.equal((await api('/api/patterns/documents/'+saved.id)).recipe.params.round,undefined);
  await page.goto(base+'/patterns?source_recipe='+record.hash+'&snapshot=1');await page.locator('#preview[data-ready=true]').waitFor();
  assert.equal((await active()).recipe.params.round,undefined);
  const tabs=await page.getByRole('tab').count();await page.reload();await page.locator('#preview[data-ready=true]').waitFor();assert.equal(await page.getByRole('tab').count(),tabs,'Corrected source reuses its authoring tab');
  // Export after reopening the historical snapshot must use square paths.
  await page.locator('summary[aria-label="PDF & other options"]').click();
  const response=page.waitForResponse(r=>r.url().endsWith('/api/patterns/exports')&&r.request().method()==='POST');
  await page.locator('#download-pdf').click();const exportResponse=await response;assert(exportResponse.ok());
  const newScene=exportResponse.request().postDataJSON();assert.equal(newScene.recipe.params.round,undefined);assert(newScene.paths.every(p=>p.points.length===5));
  assert.deepEqual(await api('/api/patterns/snapshots/'+record.hash),snapshot,'Immutable earlier snapshot is unchanged');
  assert.deepEqual(Buffer.from(await (await fetch(base+record.pdf_url)).arrayBuffer()),pdf,'Immutable rounded PDF is byte-identical');
  await page.close();console.log('PASS carpet correction: working recipes, old receipts, save/revert/reload, source reuse and immutable exports');
}
