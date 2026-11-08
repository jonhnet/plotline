// Inspect the real HPGL browser against the isolated, hardware-disabled service.
const assert=require('node:assert/strict'),fs=require('node:fs'),{chromium}=require('playwright');
const base=process.argv[2],out=process.argv[3];
(async()=>{
  const browser=await chromium.launch();try{
    const errors=[];
    const result=JSON.parse(fs.readFileSync(out+'/results.json'));
    for(const row of result.orientations){
      const page=await browser.newPage({viewport:{width:1440,height:1000}});
      page.on('pageerror',e=>errors.push(e.message));
      await page.goto(base+'/#'+row.job_id);
      await page.locator('#preview-canvas[data-ready="true"]').waitFor();
      assert.equal(await page.locator('#angle').textContent(),row.angle+'°');
      assert.equal(await page.locator('#preview-canvas').getAttribute('data-rotation'),String(row.angle));
      const margins=await page.evaluate(()=>{
        const bounds=id=>{const e=document.getElementById(id);return ['x','y','width','height'].map(k=>e[k].baseVal.value);};
        const [x,y,w,h]=bounds('sheet-outline'),[a,b,c,d]=bounds('drawing-window');
        return [a-x,b-y,x+w-a-c,y+h-b-d];
      });
      assert.equal(margins.indexOf(Math.max(...margins)),row.angle/90,'HPGL preview keeps the requested wide-margin edge');
      assert(await page.locator('#plot-open').isDisabled(),'Isolated service cannot plot');
      if(row.angle===270)await page.screenshot({path:out+'/hpgl-bottom-margin.png',fullPage:true});
      await page.close();
    }
    assert.deepEqual(errors,[]);console.log('PASS HPGL previews retain all four margin orientations');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
