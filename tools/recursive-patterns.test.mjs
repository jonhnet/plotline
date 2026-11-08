import test from 'node:test';
import assert from 'node:assert/strict';
import {generate,generateSteps,defaults,loadLimits,fitPaths,checkGeometrySize,familyFor} from '../patterns/web/generators.js';
import {peanoWalk,polygonNests,squareBranch,squareTree,displacedLine,lightningBranches,recursiveFamilies} from '../patterns/web/recursive-art.js';
await loadLimits();
const distance=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1]);
const key=p=>p.map(v=>Math.round(v*1e10)).join(',');
const edges=g=>g.paths.flatMap(p=>p.points.slice(1).map((b,i)=>[key(p.points[i]),key(b)].sort().join('/')));

test('Carpet retains exact square corners even with a legacy rounding parameter',()=>{
  assert(!familyFor('carpet').knobs.some(k=>k.key==='round'));
  const square=generate('carpet',defaults('carpet'));
  for(const round of [0,.15,.3])assert.deepEqual(generate('carpet',{...defaults('carpet'),round}),square);
  for(const path of square.paths){assert.equal(path.points.length,5);assert.deepEqual(path.points[0],path.points[4]);for(let i=1;i<5;i++){const a=path.points[i-1],b=path.points[i];assert((a[0]===b[0])!==(a[1]===b[1]));}}
});
test('Peano visits every ternary-grid cell once with unit steps and preserves color continuity',()=>{
  for(let depth=1;depth<=5;depth++){
    const pts=[...peanoWalk(depth)],n=3**depth;assert.equal(pts.length,n*n);assert.equal(new Set(pts.map(key)).size,n*n);
    assert.deepEqual(pts[0],[0,0]);assert.deepEqual(pts.at(-1),[n-1,n-1]);
    for(let i=0;i<pts.length;i++){assert(pts[i].every(v=>Number.isInteger(v)&&v>=0&&v<n));if(i)assert.equal(distance(pts[i-1],pts[i]),1);}
  }
  for(const round of [0,.2,.4]){
    const p={...defaults('peano'),round},one=generate('peano',p,()=>{},{inkCount:1}),many=generate('peano',p,()=>{},{inkCount:8});
    for(let i=1;i<many.paths.length;i++)assert.deepEqual(many.paths[i].points[0],many.paths[i-1].points.at(-1));
    assert.deepEqual(many.paths.flatMap((p,i)=>i?p.points.slice(1):p.points),one.paths[0].points);
  }
});
test('Every nested polygon is closed and wholly contained by its convex parent',()=>{
  for(const sides of [3,5,10])for(const step of [5,10,45]){
    const rings=[...polygonNests({sides,step,layers:100})];assert(rings.length>10&&rings.length<=100);
    for(let i=1;i<rings.length;i++)for(const point of rings[i])for(let j=0;j<sides;j++){
      const a=rings[i-1][j],b=rings[i-1][(j+1)%sides];assert((b[0]-a[0])*(point[1]-a[1])-(b[1]-a[1])*(point[0]-a[0])>=-1e-12);
    }
    for(const path of generate('nested_polygons',{sides,step,layers:100}).paths)assert.deepEqual(path.points[0],path.points.at(-1));
  }
});
test('Pythagoras children meet on a right triangle and conserve the parent square area',()=>{
  for(const angle of [20,45,70]){
    const cells=[...squareTree({angle,depth:6})];assert.equal(cells.length,127);
    for(const {polygon,children} of cells){
      const side=distance(polygon[0],polygon[1]);for(let i=1;i<5;i++)assert(Math.abs(distance(polygon[i-1],polygon[i])-side)<1e-12);
      assert.deepEqual(children[0][1],children[1][0]);assert.deepEqual(children[0][0],polygon[3]);assert.deepEqual(children[1][1],polygon[2]);
      const [a,c]=children[0],[,b]=children[1];assert(Math.abs((a[0]-c[0])*(b[0]-c[0])+(a[1]-c[1])*(b[1]-c[1]))<1e-12);
      assert(Math.abs(distance(a,c)**2+distance(c,b)**2-side**2)<1e-12);
    }
    const g=generate('pythagoras',{...defaults('pythagoras'),angle}),segments=edges(g);assert.equal(new Set(segments).size,segments.length,'Whole coincident edges drawn once');
    assert.deepEqual(new Set(edges(generate('pythagoras',{...defaults('pythagoras'),angle},()=>{},{inkCount:1}))),new Set(segments));
  }
});
test('Midpoint displacement retains endpoints and coarse vertices with bounded perpendicular motion',()=>{
  const a=[-1,2],b=[3,5],r=.24;
  for(let depth=1;depth<=9;depth++){
    const coarse=displacedLine(a,b,depth-1,r,7),fine=displacedLine(a,b,depth,r,7);assert.equal(fine.length,2**depth+1);assert.deepEqual(fine.filter((_,i)=>i%2===0),coarse);
    for(let i=1;i<fine.length;i+=2){const a=fine[i-1],b=fine[i+1],d=[b[0]-a[0],b[1]-a[1]],e=[fine[i][0]-(a[0]+b[0])/2,fine[i][1]-(a[1]+b[1])/2];assert(Math.abs(d[0]*e[0]+d[1]*e[1])<1e-12);assert(Math.hypot(...e)<=Math.hypot(...d)*r+1e-12);}
  }
});
test('Lightning forks start at real parent vertices with deterministic variation and no retraced segment',()=>{
  for(const seed of [1,7,8,30]){
    const p={...defaults('lightning'),seed,forks:42},branches=[...lightningBranches(p)];assert.deepEqual(branches,[...lightningBranches(p)]);assert.equal(branches.length,43);
    for(let i=1;i<branches.length;i++){const b=branches[i];assert(b.parent<i);assert.deepEqual(b.points[0],branches[b.parent].points[b.attachment]);assert(distance(b.points[0],b.points.at(-1))>0);}
    const g=generate('lightning',p),segments=edges(g);assert.equal(new Set(segments).size,segments.length);
  }
});
test('Recursive families emit monotone, lossless batches and fit the whole pen on rotated pages',()=>{
  for(const f of recursiveFamilies){
    const iterator=generateSteps(f.id,defaults(f.id),{inkCount:8}),paths=[];let step,previous=0;
    while(!(step=iterator.next()).done){assert(step.value.fraction>=previous&&step.value.fraction<=1);previous=step.value.fraction;paths.push(...step.value.batch||[]);}
    assert.deepEqual(paths,step.value.paths);assert.equal(new Set(paths.map(p=>p.role)).size,8);
    for(const rotation of [0,37,90]){
      const recipe={media:{sheet_size_mm:[215.9,279.4],printable_rect_mm:[15,39,185.9,225.4]},inks:[{width_mm:1.2}],size:1,rotation},fitted=fitPaths(step.value,recipe);
      for(const p of fitted.paths)for(const [x,y] of p.points)assert(x-.6>=15&&x+.6<=200.9&&y-.6>=39&&y+.6<=264.4);
    }
    const params={...defaults(f.id)};for(const k of f.knobs)if(['depth','layers','forks','sides'].includes(k.key))params[k.key]=k.max;
    const g=generate(f.id,params);assert(checkGeometrySize(g.paths.length,g.points)>0);assert(g.paths.every(p=>p.points.every(pt=>pt.every(Number.isFinite))));
  }
});
