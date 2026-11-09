import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {families} from '../patterns/web/generators.js';

test('Every Pattern family has one reviewed crossing policy',async()=>{
  const policies=JSON.parse(await readFile(new URL('../patterns/print-policies.json',import.meta.url),'utf8'));
  const reviewed=Object.values(policies).flat();
  assert.equal(new Set(reviewed).size,reviewed.length);
  assert.deepEqual(reviewed.sort(),families.map(f=>f.id).sort());
  for(const id of ['spirograph','lissajous','harmonograph','guilloche','lorenz','rossler','dragon'])assert(policies.overlap_strokes.includes(id));
  for(const id of ['woven','starweave','menger','architecture','newton'])assert(policies.paint_order.includes(id));
});
