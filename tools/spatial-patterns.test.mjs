import test from 'node:test';
import assert from 'node:assert/strict';
import {generate,generateSteps,defaults,loadLimits,fitPaths} from '../patterns/web/generators.js';
import {gosperWalk,ParticleIndex,aggregate,joinEdges,tetraVertices,tetrahedra,hiddenInterval,spatialFamilies} from '../patterns/web/spatial.js';
import {project3} from '../patterns/web/natural.js';
await loadLimits();
const finish=iterator=>{let step;while(!(step=iterator.next()).done);return step.value;};
const edgeKey=(a,b,role)=>role+':'+[a,b].map(pt=>pt.map(v=>v.toFixed(9)).join(',')).sort().join('/');
const segments=paths=>paths.flatMap(p=>p.points.slice(1).map((pt,i)=>edgeKey(p.points[i],pt,p.role))).sort();

test('Gosper: exact sevenfold count, unit lattice steps, distinct vertices and continuous color changes',()=>{
  for(let depth=1;depth<=5;depth++){
    const pts=[...gosperWalk(depth)];assert.equal(pts.length,7**depth+1);
    assert.equal(new Set(pts.map(pt=>pt.map(v=>v.toFixed(7)).join(','))).size,pts.length);
    for(let i=1;i<pts.length;i++)assert(Math.abs(Math.hypot(...pts[i].map((v,j)=>v-pts[i-1][j]))-1)<1e-10);
    assert(Math.abs(Math.hypot(...pts.at(-1))-7**(depth/2))<1e-8);
  }
  for(const inkCount of [1,7,8])for(const round of [0,.15,.45]){
    const g=generate('gosper',{...defaults('gosper'),round},()=>{},{inkCount});assert.equal(g.paths.length,inkCount);
    for(let i=1;i<g.paths.length;i++)assert.deepEqual(g.paths[i].points[0],g.paths[i-1].points.at(-1));
    assert(Math.abs(g.paths.at(-1).points.at(-1)[0]-1)<1e-10);assert(Math.abs(g.paths.at(-1).points.at(-1)[1])<1e-10);
  }
});
test('Incremental particle index agrees with exhaustive nearest-neighbor search, including ties',()=>{
  const index=new ParticleIndex(),pts=[];
  for(let i=0;i<600;i++){
    const p={id:i,x:Math.sin(i*71)*140,y:Math.cos(i*19)*120};index.insert(p);pts.push(p);
    for(const [x,y] of [[i*.8-220,Math.sin(i)*150],[p.x,p.y],[0,0]]){
      const expected=pts.map(pt=>({pt,d:Math.hypot(pt.x-x,pt.y-y)})).sort((a,b)=>a.d-b.d||a.pt.id-b.pt.id)[0],found=index.nearest(x,y);
      assert.equal(found.point.id,expected.pt.id);assert(Math.abs(found.distance-expected.d)<1e-10);
    }
  }
  const ties=new ParticleIndex();ties.insert({id:2,x:1,y:0});ties.insert({id:1,x:-1,y:0});assert.equal(ties.nearest(0,0).point.id,1);
});
test('DLA grows a reproducible nonoverlapping attachment tree; larger runs retain the exact coarse aggregate',()=>{
  for(const seed_shape of [0,1]){
    const p={...defaults('dla'),particles:500,seed_shape},a=finish(aggregate(p)),b=finish(aggregate({...p,particles:1000}));
    assert.equal(a.particles.length,500+a.seedCount);assert.deepEqual(b.particles.slice(0,a.particles.length),a.particles);
    for(const pt of a.particles){
      assert(pt.parent<pt.id);if(pt.id)assert(pt.parent>=0,'One connected tree, including the line seed');
      for(const other of a.particles.slice(0,pt.id))assert(Math.hypot(pt.x-other.x,pt.y-other.y)>=1-1e-9,'No particle overlaps another');
      if(pt.parent>=0){const parent=a.particles[pt.parent],distance=Math.hypot(pt.x-parent.x,pt.y-parent.y);assert(distance<=1.01+1e-9&&distance>=1-1e-9);}
    }
    const g=generate('dla',p),expected=a.particles.filter(pt=>pt.parent>=0).map(pt=>({role:Math.floor(((Math.atan2(pt.y,pt.x)+2*Math.PI)%(2*Math.PI))/(2*Math.PI)*3),points:[[a.particles[pt.parent].x,a.particles[pt.parent].y],[pt.x,pt.y]]}));
    assert.deepEqual(segments(g.paths),segments(expected),'Every attachment is drawn once, without connecting unrelated branches');
    assert(g.paths.length<a.particles.length*.7,'Joining saves lifts without dropping tree edges');
  }
});
test('Chain joining conserves branches, loops and ink changes exactly',()=>{
  const edges=[{role:0,points:[[0,0],[1,0]]},{role:0,points:[[1,0],[2,0]]},{role:0,points:[[2,0],[2,1]]},{role:1,points:[[2,1],[3,1]]},{role:0,points:[[2,0],[3,0]]},
    {role:1,points:[[5,0],[6,0]]},{role:1,points:[[6,0],[6,1]]},{role:1,points:[[6,1],[5,0]]}];
  const joined=joinEdges(edges);assert.deepEqual(segments(joined),segments(edges));assert(joined.length<edges.length);
  assert(joined.some(p=>p.points.length===4&&p.points[0][0]===p.points.at(-1)[0]));
});

const sub=(a,b)=>a.map((v,i)=>v-b[i]),dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0),cross3=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
// Independent Möller–Trumbore ray/triangle oracle in 3D, +z toward the eye.
function rayHit(origin,[a,b,c]){
  const edge1=sub(b,a),edge2=sub(c,a),h=cross3([0,0,1],edge2),det=dot(edge1,h);if(Math.abs(det)<1e-12)return false;
  const s=sub(origin,a),u=dot(s,h)/det,q=cross3(s,edge1),v=dot([0,0,1],q)/det,t=dot(edge2,q)/det;
  return u>=-1e-9&&v>=-1e-9&&u+v<=1+1e-9&&t>1e-8;
}
test('Tetrahedra remain regular half-size corner copies with the expected count and volume',()=>{
  for(let depth=0;depth<=4;depth++){
    const cells=tetrahedra(depth);assert.equal(cells.length,4**depth);
    for(const cell of cells)for(let i=0;i<4;i++)for(let j=0;j<i;j++)assert(Math.abs(Math.hypot(...sub(cell[i],cell[j]))-2**-depth)<1e-12);
    const volume=c=>Math.abs(dot(sub(c[1],c[0]),cross3(sub(c[2],c[0]),sub(c[3],c[0]))))/6;
    assert(Math.abs(cells.reduce((n,c)=>n+volume(c),0)/volume(tetraVertices)-2**-depth)<1e-10);
  }
});
test('Analytic hidden intervals agree with independent 3D ray intersections, including sloping depth',()=>{
  for(let n=0;n<80;n++){
    const tri=Array.from({length:3},(_,j)=>[Math.sin(n*5+j*3),Math.cos(n*3+j*4),Math.sin(n+j)]);
    const a=[-1,Math.sin(n),Math.cos(n*7)],b=[1,Math.cos(n),Math.sin(n*3)],interval=hiddenInterval(a,b,tri);
    for(let j=0;j<21;j++){const t=(j+.37)/21,pt=a.map((v,i)=>v+t*(b[i]-v));assert.equal(Boolean(interval&&t>interval[0]&&t<interval[1]),rayHit(pt,tri));}
  }
  assert.equal(hiddenInterval([0,0,0],[1,0,0],[[0,0,0],[1,0,0],[0,1,0]]),null,'A face never erases its own coplanar edge');
  assert.equal(hiddenInterval([0,0,0],[1,0,0],[[0,0,1],[1,0,1],[2,0,1]]),null,'An edge-on face has no occluding area');
});
test('Spatially indexed tetrahedron culling retains exactly the visible edge portions',()=>{
  const pairs=[[0,1],[0,2],[0,3],[1,2],[1,3],[2,3]],faces=[[0,1,2],[0,1,3],[0,2,3],[1,2,3]];
  for(const [azimuth,elevation] of [[25,25],[-35,45],[55,-5]]){
    const p={...defaults('tetrahedron'),depth:2,azimuth,elevation},cells=tetrahedra(2).map(c=>c.map(pt=>project3(pt,azimuth,elevation))),triangles=cells.flatMap(c=>faces.map(f=>f.map(i=>c[i]))),g=generate('tetrahedron',p),lines=g.paths.flatMap(p=>p.points.slice(1).map((b,i)=>[p.points[i],b]));
    const onLine=([x,y],[a,b])=>{const dx=b[0]-a[0],dy=b[1]-a[1],t=((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy);return t>=-1e-8&&t<=1+1e-8&&Math.hypot(x-a[0]-t*dx,y-a[1]-t*dy)<1e-8;};
    let hidden=0,visible=0;
    for(const cell of cells)for(const [i,j] of pairs)for(const t of [.137,.413,.781]){
      const pt=cell[i].map((v,k)=>v+t*(cell[j][k]-v)),occluded=triangles.some(tri=>rayHit(pt,tri));
      assert.equal(lines.some(line=>onLine(pt,line)),!occluded);if(occluded)hidden++;else visible++;
    }
    assert(hidden>20&&visible>20);assert(generate('tetrahedron',{...p,wireframe:1}).points>g.points);
  }
});
test('New families expose lossless batches, full palettes and finite-pen page bounds at maximum detail',()=>{
  for(const family of spatialFamilies){
    const params={...defaults(family.id),...(family.id==='gosper'?{depth:6}:family.id==='dla'?{particles:6000}:{depth:6})},steps=generateSteps(family.id,params,{inkCount:8}),batches=[];let done,previous=0,updates=0;
    while(!(done=steps.next()).done){const v=done.value;assert(v.fraction>=previous&&v.fraction<=1);previous=v.fraction;updates++;batches.push(...v.batch||[]);}
    assert(updates>=6);assert.deepEqual(batches,done.value.paths);assert.equal(new Set(batches.map(p=>p.role)).size,8);
    for(const rotation of [0,37,90]){
      const recipe={media:{sheet_size_mm:[215.9,279.4],printable_rect_mm:[15,39,185.9,225.4]},inks:[{width_mm:1.2}],size:1,rotation},fitted=fitPaths(done.value,recipe);
      for(const p of fitted.paths)for(const [x,y] of p.points)assert(x-.6>=15&&x+.6<=200.9&&y-.6>=39&&y+.6<=264.4);
    }
  }
});
