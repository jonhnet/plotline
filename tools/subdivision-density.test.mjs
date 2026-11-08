import test from 'node:test';
import assert from 'node:assert/strict';
import {generate,generateSteps,defaults,loadLimits,fitPaths,checkGeometrySize} from '../patterns/web/generators.js';
import {motifs,replaceEdges,roundCurve,carpetHoles,subdivisionFamilies} from '../patterns/web/subdivision.js';
import {attractorStep,attractorPresets,deposit,smoothDensity,densityField,densityFamilies} from '../patterns/web/density.js';
await loadLimits();
const finish=iterator=>{let step;while(!(step=iterator.next()).done);return step.value;};
const length=points=>points.slice(1).reduce((sum,b,i)=>sum+Math.hypot(b[0]-points[i][0],b[1]-points[i][1]),0);

test('Replacement curves retain endpoints and have their exact segment counts and lengths',()=>{
  for(const family of ['levy','minkowski']){
    let points=[[0,0],[1,0]];
    for(let depth=1;depth<=5;depth++){
      points=replaceEdges(points,motifs[family]);const factor=family==='levy'?2:8;
      assert.equal(points.length,factor**depth+1);assert.deepEqual(points[0],[0,0]);assert.deepEqual(points.at(-1),[1,0]);
      const step=(family==='levy'?1/Math.sqrt(2):1/4)**depth;
      for(let i=1;i<points.length;i++)assert(Math.abs(Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1])-step)<1e-12);
      assert(Math.abs(length(points)-factor**depth*step)<1e-8);
    }
  }
});
test('Rounding retains open endpoints and closes the island seam; ink sections never add connectors',()=>{
  const square=[[0,0],[1,0],[1,1],[0,1],[0,0]];
  for(const amount of [0,.15,.45]){
    const pts=roundCurve(square,amount,true);assert.deepEqual(pts[0],pts.at(-1));assert(pts.every(p=>p.every(v=>v>=0&&v<=1)));
    const open=roundCurve(square.slice(0,4),amount);assert.deepEqual(open[0],square[0]);assert.deepEqual(open.at(-1),square[3]);
    for(const family of ['levy','minkowski'])for(const inkCount of [1,7,8]){
      const p={...defaults(family),depth:family==='levy'?10:3,round:amount},one=generate(family,p,()=>{},{inkCount:1}),many=generate(family,p,()=>{},{inkCount});
      assert.equal(many.paths.length,inkCount);
      for(let i=1;i<many.paths.length;i++)assert.deepEqual(many.paths[i-1].points.at(-1),many.paths[i].points[0]);
      const joined=many.paths.flatMap((path,i)=>i?path.points.slice(1):path.points);assert.deepEqual(joined,one.paths[0].points);
      if(family==='minkowski')assert.deepEqual(joined[0],joined.at(-1));
    }
  }
});
test('Carpet holes have the correct count/area, never overlap and preserve all earlier generations',()=>{
  let earlier=new Set();
  for(let depth=1;depth<=4;depth++){
    const holes=[...carpetHoles(depth)],keys=new Set(holes.map(h=>[h.x,h.y,h.size].map(v=>v.toFixed(10)).join(',')));
    assert.equal(holes.length,(8**depth-1)/7);assert.equal(keys.size,holes.length);assert([...earlier].every(k=>keys.has(k)));earlier=keys;
    assert(Math.abs(holes.reduce((s,h)=>s+h.size*h.size,0)-(1-(8/9)**depth))<1e-12);
    for(let i=0;i<holes.length;i++){
      const a=holes[i];assert(a.x>=0&&a.y>=0&&a.x+a.size<=1&&a.y+a.size<=1);
      for(let j=0;j<i;j++){const b=holes[j];assert(Math.min(a.x+a.size,b.x+b.size)-Math.max(a.x,b.x)<1e-10||Math.min(a.y+a.size,b.y+b.size)-Math.max(a.y,b.y)<1e-10);}
    }
  }
  for(const inkCount of [1,7,8]){
    const g=generate('carpet',defaults('carpet'),()=>{},{inkCount});assert.equal(g.paths.length,1+(8**4-1)/7);assert.equal(new Set(g.paths.map(p=>p.role)).size,inkCount);
    for(const p of g.paths)assert.deepEqual(p.points[0],p.points.at(-1));
  }
});
test('Attractor equations use the previous x and y together and stay within their trigonometric bounds',()=>{
  assert.deepEqual(attractorStep('clifford',[1,2,3,4],[0,0]),[3,4]);assert.deepEqual(attractorStep('dejong',[1,2,3,4],[0,0]),[-1,-1]);
  for(const [family,expected] of [['clifford',[.8506728815684008,.8503773049771848]],['dejong',[-.8341232803611385,-.7403282882970137]]]){
    const actual=attractorStep(family,attractorPresets[family][0],[.1,.1]);assert(actual.every((v,i)=>Math.abs(v-expected[i])<1e-14));
  }
  for(const family of ['clifford','dejong'])for(const params of attractorPresets[family]){
    const [, ,c,d]=params;let at=[.1,.1];for(let i=0;i<1000;i++){
      at=attractorStep(family,params,at);
      assert(Math.abs(at[0])<=(family==='clifford'?1+Math.abs(c):2));assert(Math.abs(at[1])<=(family==='clifford'?1+Math.abs(d):2));
    }
  }
});
test('Bilinear deposition preserves point mass and location',()=>{
  const n=8,field=new Float64Array((n+1)**2);deposit(field,n,3.25,4.75);
  let mass=0,x=0,y=0;for(let row=0;row<=n;row++)for(let col=0;col<=n;col++){const w=field[row*(n+1)+col];mass+=w;x+=w*col;y+=w*row;}
  assert.equal(mass,1);assert.equal(x,3.25);assert.equal(y,4.75);assert.equal(field.filter(v=>v>0).length,4);
});
test('Separable smoothing agrees with a direct 2D Gaussian and preserves padded mass',()=>{
  const n=32,sigma=2,field=new Float64Array((n+1)**2);deposit(field,n,16.25,16.5);const actual=finish(smoothDensity(field,n,sigma)),radius=Math.ceil(3*sigma);
  const weights=Array.from({length:2*radius+1},(_,i)=>Math.exp(-.5*((i-radius)/sigma)**2)),sum=weights.reduce((a,b)=>a+b,0);
  for(let y=0;y<=n;y++)for(let x=0;x<=n;x++){
    let expected=0;for(let row=16;row<=17;row++)for(let col=16;col<=17;col++)if(Math.abs(x-col)<=radius&&Math.abs(y-row)<=radius)expected+=field[row*(n+1)+col]*weights[x-col+radius]*weights[y-row+radius]/sum**2;
    assert(Math.abs(actual[y*(n+1)+x]-expected)<1e-14);
  }
  assert(Math.abs(actual.reduce((s,v)=>s+v,0)-1)<1e-12);
});
test('All curated attractors yield closed local isolines of the sampled density, never orbit jumps',()=>{
  for(const family of densityFamilies)for(let shape=0;shape<4;shape++){
    const p={...defaults(family.id),shape,depth:2},g=generate(family.id,p),{values,n,samples}=finish(densityField(family.id,p));
    assert.equal(samples,12*n*n);assert(Math.abs(values.reduce((s,v)=>s+v,0)/(n*n)-1)<1e-10);assert(g.paths.length>15);
    const sample=([a,b])=>{const x=(a+1)*n/2,y=(b+1)*n/2,col=Math.min(n-1,Math.floor(x)),row=Math.min(n-1,Math.floor(y)),u=x-col,v=y-row,i=row*(n+1)+col;return values[i]*(1-u)*(1-v)+values[i+1]*u*(1-v)+values[i+n+1]*(1-u)*v+values[i+n+2]*u*v;};
    for(const path of g.paths){
      assert.deepEqual(path.points[0],path.points.at(-1));const level=sample(path.points[0]);
      for(let i=1;i<path.points.length;i++){const a=path.points[i-1],b=path.points[i];assert(Math.hypot(b[0]-a[0],b[1]-a[1])<=2*Math.SQRT2/n+1e-10);assert(Math.abs(sample(b)-level)<1e-7);}
    }
  }
});
test('New families retain lossless batches, deterministic ink-only changes and complete finite pen bounds',()=>{
  for(const f of [...subdivisionFamilies,...densityFamilies]){
    const p=defaults(f.id),iterator=generateSteps(f.id,p,{inkCount:8}),paths=[];let previous=0,step;
    while(!(step=iterator.next()).done){assert(step.value.fraction>=previous&&step.value.fraction<=1);previous=step.value.fraction;paths.push(...step.value.batch||[]);}
    assert.deepEqual(paths,step.value.paths);assert.equal(new Set(paths.map(p=>p.role)).size,8);
    if(!['levy','minkowski'].includes(f.id))assert.deepEqual(paths.map(p=>p.points),generate(f.id,p,()=>{},{inkCount:1}).paths.map(p=>p.points));
    for(const rotation of [0,37,90]){
      const recipe={media:{sheet_size_mm:[215.9,279.4],printable_rect_mm:[15,39,185.9,225.4]},inks:[{width_mm:1.2}],size:1,rotation},fitted=fitPaths(step.value,recipe);
      for(const p of fitted.paths)for(const [x,y] of p.points)assert(x-.6>=15&&x+.6<=200.9&&y-.6>=39&&y+.6<=264.4);
    }
  }
});
test('Maximum details stay finite and within the shared memory allowance',()=>{
  for(const f of [...subdivisionFamilies,...densityFamilies]){
    const p=defaults(f.id),params={...p,depth:f.knobs.find(k=>k.key==='depth').max},g=generate(f.id,params);assert(checkGeometrySize(g.paths.length,g.points)>0);
    assert(g.paths.every(p=>p.points.every(pt=>pt.every(Number.isFinite))));
    if(f.id==='minkowski')assert.equal(generate(f.id,{...params,round:0}).points,4*8**5+3,'Sections share their endpoints, including explicit closure');
    assert.throws(()=>generate(f.id,{...p,depth:params.depth+1}),/Invalid Detail/);
  }
});
