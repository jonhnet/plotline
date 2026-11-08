// Read-only browser audit of the live lab; never creates comments or plot jobs.
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
const require=createRequire(new URL('../controller/package.json',import.meta.url));
const {chromium}=require('playwright');
const base=process.env.RASTER_LAB_URL||'http://127.0.0.1:9194';
const browser=await chromium.launch(),page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[],writes=[],outside=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))writes.push(r.url());if(new URL(r.url()).origin!==base)outside.push(r.url());});
try{
 await page.goto(base+'/?family=palette');
 await page.waitForFunction(()=>document.querySelectorAll('#matrix tbody tr').length===7);
 assert.equal(await page.locator('#matrix thead th').count(),4);
 assert.equal(await page.locator('#matrix .pending').count(),0);
 await page.getByRole('button',{name:'Compare IMG_0456 · uploaded photograph: Optimized + blue/red · blue fix',exact:true}).click();
 await page.waitForFunction(()=>document.getElementById('right-info').textContent.includes('Vector SVG'));
 assert.match(await page.locator('#right-info').textContent(),/Blue · provisional #245bd6/);
 assert.match(await page.locator('#right-info').textContent(),/6 selected inks/);
 await page.selectOption('#left-select','palette-baseline');
 await page.waitForFunction(()=>document.getElementById('left-info').textContent.includes('Vector SVG'));
 await page.selectOption('#zoom','2');await page.locator('#blink').check();await page.locator('#blink').uncheck();
 assert.equal(await page.locator('#comment-target').textContent(),'Optimized + blue/red · blue fix');
 const recipe=await page.locator('#parameters').evaluate(e=>JSON.parse(e.textContent).right);
 assert.equal(recipe.palette_mapping,'swatch');assert.equal(recipe.palette_color_space,'oklab');assert.equal(recipe.black_policy,'off');assert.equal(recipe.pen_mm,.25);
 for(const name of ['Vector SVG','Paper SVG (actual size)','Recipe & metrics']){
  const link=await page.locator('#right-info').getByRole('link',{name,exact:true}).getAttribute('href');
  assert.equal((await page.request.get(base+link)).status(),200);
 }
 await mkdir('build/palette-browser',{recursive:true});
 await page.screenshot({path:'build/palette-browser/photo-comparison.png'});
 await page.getByRole('button',{name:'Close comparison'}).click();
 await page.selectOption('#family','combined');
 await page.waitForFunction(()=>document.querySelectorAll('#matrix tbody tr').length===8);
 assert.equal(await page.locator('#matrix thead th').count(),5);
 await page.selectOption('#family','palette');
 await page.waitForFunction(()=>document.querySelectorAll('#matrix tbody tr').length===7);
 assert.equal((await page.request.get(base+'/originals/img-0456.png')).status(),200);
 assert.equal((await page.request.get(base+'/palette-sources.json')).status(),404);
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);assert.deepEqual(outside,[]);
 console.log('PASS palette grid, original/optimized comparisons, zoom, comments target, downloads, provisional blue, archived-study navigation, private provenance. No writes.');
}finally{await browser.close();}
