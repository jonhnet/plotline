import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseRecipeFile} from '../shared/web/recipe-files.js';

const fixture=name=>JSON.parse(readFileSync(new URL(`../docs/readme/examples/${name}/${name==='hex-maze'?'maze':'pattern'}-recipe.json`,import.meta.url)));
const options={studio:'Pattern',familyIds:['hilbert','plant','sierpinski','spirograph']};
test('Published recipes retain exact paper, inks, role bindings and drawing settings',()=>{
  for(const name of options.familyIds){const r=fixture(name);assert.deepEqual(parseRecipeFile('\uFEFF'+JSON.stringify(r),options),r);}
  const maze=fixture('hex-maze');assert.deepEqual(parseRecipeFile(JSON.stringify(maze),{studio:'Maze',familyIds:['hexagon']}),maze);
});
test('Snapshot imports use design only, without source document identity or print receipts',()=>{
  const recipe=fixture('hilbert'),snapshot={id:'another-installation',revision:7,receipt:{ready:true,jobs:['old-plot']},recipe};
  assert.deepEqual(parseRecipeFile(JSON.stringify(snapshot),options),recipe);
});
test('Reject malformed files before opening a document; unknown ink widths remain honest',()=>{
  assert.throws(()=>parseRecipeFile('{',options),/valid JSON/);
  assert.throws(()=>parseRecipeFile('null',options),/recipe JSON/);
  for(const [change,error] of [
    [r=>r.version=2,/version/], [r=>r.family='hexagon',/not a Pattern/],
    [r=>r.media.printable_rect_mm[2]=9999,/fit the paper/],
    [r=>r.inks[0].width_mm=-1,/diameters/], [r=>r.inks.push(r.inks[0]),/unique ID/],
    [r=>r.roles[0].ink_id='missing',/drawing role/], [r=>r.inks[0].color_srgb='red',/color/],
  ]){const r=fixture('hilbert');change(r);assert.throws(()=>parseRecipeFile(JSON.stringify(r),options),error);}
  const r=fixture('hilbert');r.inks[0].width_mm=null;assert.equal(parseRecipeFile(JSON.stringify(r),options).inks[0].width_mm,null);
});
