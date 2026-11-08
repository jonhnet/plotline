import assert from 'node:assert/strict';

export async function checkPreviewControls(page,readView){
  await page.bringToFront();
  const painted=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const tools=page.getByRole('group',{name:'Preview tools',exact:true});
  const common=tools.locator('.preview-common-controls');
  assert.deepEqual(await common.getByRole('button').evaluateAll(buttons=>buttons.map(b=>b.getAttribute('aria-label'))),['Zoom out','Fit page','Zoom in','Undo','Redo']);
  const [minus,fit,plus,undo,redo]=await common.getByRole('button').evaluateAll(buttons=>buttons.map(b=>{const r=b.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom};}));
  assert(minus.y===fit.y&&fit.y===plus.y&&minus.right<=fit.x&&fit.right<=plus.x,'Fit sits between minus and plus in the zoom row');
  assert(plus.bottom<=undo.y&&undo.y===redo.y&&undo.right<=redo.x,'History buttons share the next row');
  if(!readView)return;
  await common.getByRole('button',{name:'Fit page',exact:true}).click();
  await painted();
  const before=await readView();
  await common.getByRole('button',{name:'Zoom in',exact:true}).click();
  await painted();
  assert.notDeepEqual(await readView(),before,'Zoom changes the inspected view');
  await common.getByRole('button',{name:'Zoom out',exact:true}).click();
  await painted();
  assert.deepEqual(await readView(),before,'Opposite zoom restores the inspected view');
  await common.getByRole('button',{name:'Zoom in',exact:true}).click();
  await common.getByRole('button',{name:'Fit page',exact:true}).click();
  await painted();
  assert.deepEqual(await readView(),before,'Fit restores the inspected view');
}
