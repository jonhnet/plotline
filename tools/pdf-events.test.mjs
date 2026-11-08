import assert from 'node:assert/strict';
import { test } from 'node:test';
import { extract } from './pdf-events.mjs';
import { makePdf, roadContent } from './make-fixtures.mjs';

test('actual PDF preserves white marks, source coordinates, styles and paint order', () => {
  const page = extract(makePdf(roadContent));
  assert.deepEqual(page.events.map(e => e.kind), ['stroke','stroke','stroke','stroke','fill','fill']);
  assert.deepEqual(page.events[1].color, [1,1,1]);
  assert.equal(page.events[0].style.width, 18);
  assert.equal(page.events[0].style.cap, 'Round');
  assert.deepEqual(page.events[0].matrix, [1,0,0,-1,0,220]);
  assert.equal(page.units, 'pt');
});

test('nested clips, fill rule, affine CTM, dashes and cubic survive extraction', () => {
  const p = extract(makePdf(`q 0 0 100 100 re 20 20 30 30 re W* n q 1.5 .2 .3 .7 10 20 cm
    0 0 70 70 re W n 2 J 1 j 3 w [4 2] 1 d 0 0 m 0 30 30 30 30 0 c S Q Q`));
  assert.deepEqual(p.events.map(e=>e.kind),['clip_fill','clip_fill','stroke','pop_clip','pop_clip']);
  assert.equal(p.events[0].even_odd,true);
  const s=p.events[2];
  assert.equal(s.path[1].op,'cubic');
  assert.deepEqual(s.style.dashes,[4,2]);
  assert.equal(s.style.dash_phase,1);
  assert.equal(s.style.cap,'Square');
  assert.equal(s.matrix[0],1.5);
});

test('unsupported text is recorded, never silently discarded', () => {
  const p=extract(makePdf('BT /F1 12 Tf 10 10 Td (hello) Tj ET', {Font:{F1:{Type:'Font',Subtype:'Type1',BaseFont:'Helvetica'}}}));
  assert.ok(p.events.some(e=>e.kind==='unsupported' && e.feature==='fillText'));
});

test('transparency and overprint are explicit unsupported operations', () => {
  for (const state of [{ca:0.5},{OP:true}]) {
    const p=extract(makePdf('/GS gs 10 10 20 20 re f',{ExtGState:{GS:state}}));
    assert.ok(p.events.some(e=>e.kind==='unsupported'));
  }
});

test('unsupported colors are explicit and bad pages fail', () => {
  const bytes=makePdf('1 0 0 0 k 10 10 20 20 re f');
  assert.ok(extract(bytes).events.some(e=>e.feature==='color space'));
  assert.throws(()=>extract(bytes,0),/range/);
  assert.throws(()=>extract(bytes,2),/range/);
});
