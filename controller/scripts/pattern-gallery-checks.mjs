import assert from 'node:assert/strict';
import {familyGroups,groupForFamily} from '../../patterns/web/groups.js';
export async function chooseGalleryFamily(page,id){await page.locator('[data-group="'+groupForFamily(id).id+'"]').click();await page.locator('[data-family="'+id+'"]').click();}
export async function checkPatternGallery(page,artifact){
 const before=await page.evaluate(()=>sessionStorage.getItem('plotline-pattern-workspace'));
 assert.equal(await page.locator('#family-groups [role=tab]').count(),familyGroups.length);
 assert(await page.locator('#family-gallery button[hidden] img').evaluateAll(images=>images.every(image=>!image.hasAttribute('src'))),'Opening the gallery does not request thumbnails in the other groups');
 for(const group of familyGroups){
  await page.locator('[data-group="'+group.id+'"]').click();
  await page.waitForFunction(()=>[...document.querySelectorAll('#family-gallery button:not([hidden])')].every(b=>b.dataset.ready==='true'));
  assert.deepEqual(await page.locator('#family-gallery button:visible').evaluateAll(bs=>bs.map(b=>b.dataset.family)),group.families);
  assert.equal(await page.locator('#family-groups [aria-selected=true]').count(),1);
  assert.equal(await page.locator('#family-groups [tabindex="0"]').count(),1);
  assert(await page.locator('#family-gallery img:visible').evaluateAll(images=>images.every(image=>{if(!image.complete||image.naturalWidth!==720||image.naturalHeight!==460)return false;const c=document.createElement('canvas');c.width=image.naturalWidth;c.height=image.naturalHeight;const ctx=c.getContext('2d');ctx.drawImage(image,0,0);const d=ctx.getImageData(0,0,c.width,c.height).data;let ink=0;for(let i=0;i<d.length;i+=4)if(d[i+3]>0&&Math.min(d[i],d[i+1],d[i+2])<245)ink++;return ink>100;})));
 }
 assert.equal(await page.locator('#family-gallery button').count(),familyGroups.reduce((n,g)=>n+g.families.length,0));
 const last=page.locator('#family-groups [aria-selected=true]');await last.focus();await page.keyboard.press('Home');assert.equal(await page.locator('#family-groups [aria-selected=true]').getAttribute('data-group'),familyGroups[0].id);
 await page.keyboard.press('End');await page.keyboard.press('ArrowLeft');assert.equal(await page.locator('#family-groups [aria-selected=true]').getAttribute('data-group'),familyGroups.at(-2).id);
 assert.equal(await page.locator('#family-groups [aria-selected=true]').evaluate(b=>document.activeElement===b),true);
 await page.keyboard.press('ArrowRight');await page.keyboard.press('ArrowRight');assert.equal(await page.locator('#family-groups [aria-selected=true]').getAttribute('data-group'),familyGroups[0].id);
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('plotline-pattern-workspace')),before,'Browsing groups is not an artwork edit');
 await page.screenshot({path:artifact});
 await page.locator('#close-gallery').click();await page.locator('#browse').click();assert.equal(await page.locator('#family-groups [aria-selected=true]').getAttribute('data-group'),familyGroups[0].id,'Reopening remembers the browsing group');
 const viewport=page.viewportSize();await page.setViewportSize({width:390,height:844});
 for(const group of familyGroups){await page.locator('[data-group="'+group.id+'"]').click();const b=await page.locator('[data-group="'+group.id+'"]').boundingBox();assert(b.x>=0&&b.x+b.width<=390);}
 assert(await page.locator('#gallery').evaluate(e=>e.scrollWidth<=e.clientWidth+1),'Mobile gallery has no horizontal overflow');
 await page.screenshot({path:artifact.replace('.png','-mobile.png')});await page.setViewportSize(viewport);
}
