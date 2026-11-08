import test from 'node:test';
import assert from 'node:assert/strict';
import {migrateStudioStorage} from '../shared/web/studio.js';

function storage(entries={}) {
  const values=new Map(Object.entries(entries));
  return {getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
}
test('unsaved PDF and HPGL workspaces survive the product rename',()=>{
  const snapshot=JSON.stringify({active:'unsaved',documents:[{key:'unsaved',recipe:{title:'Work in progress'}}]});
  for(const kind of ['pdf','hpgl']){
    const old=`squidprint-${kind}-workspace`,key=`plotline-${kind}-workspace`,s=storage({[old]:snapshot});
    migrateStudioStorage(s,key);assert.equal(s.getItem(key),snapshot);assert.equal(s.getItem(old),null);
  }
});
test('an existing renamed workspace wins over an older copy',()=>{
  const s=storage({'squidprint-pdf-workspace':'older','plotline-pdf-workspace':'current'});
  migrateStudioStorage(s,'plotline-pdf-workspace');assert.equal(s.getItem('plotline-pdf-workspace'),'current');
});
test('a reconciled pending plot is never resurrected by a legacy key',()=>{
  const key='plotline.pending-plot',s=storage({'squidprint.pending-plot':'{"id":"uncertain-request"}'});
  migrateStudioStorage(s,key);assert.ok(s.getItem(key));s.removeItem(key);
  migrateStudioStorage(s,key);assert.equal(s.getItem(key),null);
});
test('full storage retains the only copy of unsaved work',()=>{
  const s=storage({'squidprint-pdf-workspace':'unsaved'});s.setItem=()=>{throw Error('quota');};
  migrateStudioStorage(s,'plotline-pdf-workspace');assert.equal(s.getItem('squidprint-pdf-workspace'),'unsaved');
});
