import test from 'node:test';
import assert from 'node:assert/strict';
import {defaults,generate,generateSteps,fitPaths,loadLimits,checkGeometrySize} from '../patterns/web/generators.js';
import {ornamentFamilies,invertCircle,inversionSeeds,inversionCircles,octagonalTiles,projectOctagonal,inOctagonalWindow,growthTargets,colonize,architectureBoxes,boxCorners,architectureEdges} from '../patterns/web/ornament.js';
import {ParticleIndex} from '../patterns/web/spatial.js';
await loadLimits();
const finish=it=>{let s;while(!(s=it.next()).done);return s.value;};
const key=p=>p.map(v=>Math.round(v*1e8)).join(','),edge=(a,b)=>[key(a),key(b)].sort().join('/');
const segments=paths=>paths.flatMap(p=>p.points.slice(1).map((b,i)=>edge(p.points[i],b)));
const distance=(a,b)=>Math.hypot(...a.map((v,i)=>v-b[i]));

test('Circle inversion agrees with point inversion, is an involution and retains old generations',()=>{
  const mirror={x:.3,y:-.2,r:.8};
  for(const c of [{x:2,y:1,r:.4},{x:0,y:0,r:2},{x:.3,y:-.2,r:.2}]){
    const image=invertCircle(c,mirror),back=invertCircle(image,mirror);assert(distance([c.x,c.y,c.r],[back.x,back.y,back.r])<1e-12);
    for(let i=0;i<37;i++){const x=c.x+c.r*Math.cos(i),y=c.y+c.r*Math.sin(i),dx=x-mirror.x,dy=y-mirror.y,k=mirror.r**2/(dx*dx+dy*dy);assert(Math.abs(Math.hypot(mirror.x+dx*k-image.x,mirror.y+dy*k-image.y)-image.r)<1e-12);}
  }
  assert.equal(invertCircle({x:1,y:0,r:1},{x:0,y:0,r:1}),null);
  const p=defaults('inversion'),old=[...inversionCircles({...p,depth:3})],all=[...inversionCircles(p)],ids=new Set(all.map(c=>key([c.x,c.y,c.r])));
  assert.equal(ids.size,all.length);assert(old.every(c=>ids.has(key([c.x,c.y,c.r]))));
  const {mirrors}=inversionSeeds(p);for(const c of all)assert(Math.hypot(c.x,c.y)+c.r<=1+mirrors[0].r+1e-10);
  for(const c of all.filter(c=>c.depth>0)){const m=mirrors[c.last];assert(Math.hypot(c.x-m.x,c.y-m.y)+c.r<=m.r+1e-10);assert(c.r>=p.cutoff);}
  for(const path of generate('inversion',p).paths)assert.deepEqual(path.points[0],path.points.at(-1));
});
test('Octagonal cut-and-project produces unit squares and 45-degree rhombi with exact lattice identities',()=>{
  const tiles=[...octagonalTiles(defaults('ammann'))],keys=new Set();let squares=0,diamonds=0;
  for(const t of tiles){
    const id=t.lattice.map(n=>n.join(',')).sort().join('/');assert(!keys.has(id));keys.add(id);
    for(let i=0;i<4;i++){assert(inOctagonalWindow(t.lattice[i]));assert.deepEqual(projectOctagonal(t.lattice[i]),t.points[i]);assert(Math.abs(distance(t.points[i],t.points[(i+1)%4])-1)<1e-12);}
    const [a,b,,d]=t.points,dot=(b[0]-a[0])*(d[0]-a[0])+(b[1]-a[1])*(d[1]-a[1]);assert(Math.abs(Math.abs(dot)-(t.square?0:Math.SQRT1_2))<1e-12);t.square?squares++:diamonds++;
  }
  assert(squares>100&&diamonds>100);
  const vertexSet=new Set(tiles.flatMap(t=>t.points.map(key)));
  for(const t of tiles)for(const [x,y] of t.points)assert(vertexSet.has(key([(x-y)*Math.SQRT1_2,(x+y)*Math.SQRT1_2])),'Centered octagonal patch has eightfold symmetry');
  const g=generate('ammann',{...defaults('ammann'),insets:0}),actual=segments(g.paths),expected=new Set(tiles.flatMap(t=>t.points.map((a,i)=>edge(a,t.points[(i+1)%4]))));
  assert.equal(actual.length,new Set(actual).size);assert.deepEqual(new Set(actual),expected,'Each shared tile edge appears once');
});
test('All organic presets retain a real connected parent tree and deterministic local growth',()=>{
  for(const crown of [0,1,2]){
    const p={...defaults('colonization'),crown},g=finish(colonize(p));assert.deepEqual(finish(colonize(p)),g);assert.deepEqual(growthTargets(p),g.targets);assert(g.nodes.length>300);assert(g.remaining<g.targets.length*.1);
    const index=new ParticleIndex();for(const n of g.nodes){
      if(n.parent>=0){assert(n.parent<n.id);const a=g.nodes[n.parent];assert(Math.abs(Math.hypot(n.x-a.x,n.y-a.y)-3)<1e-10);assert(index.nearest(n.x,n.y).distance>=1.65-1e-10);}
      index.insert(n);
    }
    // The production quadtree agrees with an independent all-node oracle on
    // points drawn from this actual fine-scale tree, not just random test space.
    for(let i=0;i<g.targets.length;i+=23){const t=g.targets[i],near=index.nearest(t.x,t.y),oracle=g.nodes.map(n=>({n,d:Math.hypot(n.x-t.x,n.y-t.y)})).sort((a,b)=>a.d-b.d||a.n.id-b.n.id)[0];assert.equal(near.point.id,oracle.n.id);assert(Math.abs(near.distance-oracle.d)<1e-10);}
    const expected=g.nodes.filter(n=>n.parent>=0).map(n=>edge([g.nodes[n.parent].x,-g.nodes[n.parent].y],[n.x,-n.y])).sort();
    for(const inkCount of [1,7,8])assert.deepEqual(segments(generate('colonization',p,()=>{},{inkCount}).paths).sort(),expected,'Joining and ink changes retain each real edge once');
  }
});
test('Architecture children rest on roofs, stay inside parents and have disjoint sibling footprints',()=>{
  for(const form of [0,1])for(const setback of [.1,.2,.4]){
    const p={...defaults('architecture'),form,setback},boxes=architectureBoxes(p),base=form?4:5;assert.equal(boxes.length,(base**(p.depth+1)-1)/(base-1));assert.deepEqual(architectureBoxes(p),boxes);
    for(const b of boxes)if(b.parent>=0){const a=boxes[b.parent];assert.equal(b.z,a.z+a.height);assert(Math.abs(b.x-a.x)+b.size/2<=a.size/2+1e-12);assert(Math.abs(b.y-a.y)+b.size/2<=a.size/2+1e-12);assert(b.height>0);}
    const groups=new Map();for(const b of boxes){if(!groups.has(b.parent))groups.set(b.parent,[]);groups.get(b.parent).push(b);}for(const siblings of groups.values())for(let i=0;i<siblings.length;i++)for(let j=0;j<i;j++){const a=siblings[i],b=siblings[j];assert(Math.abs(a.x-b.x)>=(a.size+b.size)/2-1e-12||Math.abs(a.y-b.y)>=(a.size+b.size)/2-1e-12);}
  }
});
// Independent ray / axis-aligned box oracle in original 3D coordinates. This
// does not reuse the projected triangles, half-planes, or spatial index.
function hidden(point,boxes,view){
  return boxes.some(b=>{
    const low=[b.x-b.size/2+1e-8,b.y-b.size/2+1e-8,b.z+1e-8],high=[b.x+b.size/2-1e-8,b.y+b.size/2-1e-8,b.z+b.height-1e-8];let near=0,far=Infinity;
    for(let axis=0;axis<3;axis++){if(Math.abs(view[axis])<1e-12){if(point[axis]<low[axis]||point[axis]>high[axis])return false;}else{const a=(low[axis]-point[axis])/view[axis],c=(high[axis]-point[axis])/view[axis];near=Math.max(near,Math.min(a,c));far=Math.min(far,Math.max(a,c));}}return far>near+1e-8;
  });
}
test('Architecture visible intervals agree with independent 3D box rays and cull most face comparisons',()=>{
  for(const form of [0,1])for(const [azimuth,elevation] of [[5,15],[35,35],[85,75]]){
    const p={...defaults('architecture'),depth:2,form,azimuth,elevation},g=finish(architectureEdges(p)),a=azimuth*Math.PI/180,e=elevation*Math.PI/180,view=[Math.sin(a)*Math.cos(e),Math.cos(a)*Math.cos(e),Math.sin(e)],runs=new Map();
    for(const edge of g.edges){const key=edge.box+'/'+edge.source.join(',');if(!runs.has(key))runs.set(key,[]);runs.get(key).push(edge.range);assert(distance(...edge.points)>1e-7);}
    let hiddenCount=0,visibleCount=0;
    for(const b of g.boxes){const corners=boxCorners(b);for(let u=0;u<8;u++)for(let axis=0;axis<3;axis++){const v=u^(1<<axis);if(u>v)continue;const intervals=runs.get(b.id+'/'+[u,v].join(','))||[];
      for(let k=1;k<8;k++){const t=k/8,pt=corners[u].map((x,j)=>x+(corners[v][j]-x)*t),blocked=hidden(pt,g.boxes,view),drawn=intervals.some(([lo,hi])=>t>lo&&t<hi);assert.equal(drawn,!blocked,JSON.stringify({form,azimuth,elevation,box:b.id,u,v,t}));blocked?hiddenCount++:visibleCount++;}
    }}assert(hiddenCount>10&&visibleCount>10);assert(g.comparisons<g.boxes.length*12*g.triangles.length*.4);
  }
});
test('New families preserve lossless batches, ink roles, maximum detail and full-pen page bounds',()=>{
  for(const f of ornamentFamilies){
    const p=defaults(f.id),it=generateSteps(f.id,p,{inkCount:f.sequence?8:3}),paths=[];let previous=0,s;
    while(!(s=it.next()).done){assert(s.value.fraction>=previous&&s.value.fraction<=1);previous=s.value.fraction;paths.push(...s.value.batch||[]);}
    assert.deepEqual(paths,s.value.paths);assert.equal(new Set(paths.map(p=>p.role)).size,f.sequence?8:3);
    for(const rotation of [0,37,90]){
      const fitted=fitPaths(s.value,{media:{sheet_size_mm:[215.9,279.4],printable_rect_mm:[15,39,185.9,225.4]},inks:[{width_mm:1.2}],size:1,rotation});
      for(const p of fitted.paths)for(const [x,y] of p.points)assert(x-.6>=15&&x+.6<=200.9&&y-.6>=39&&y+.6<=264.4);
    }
    const max=generate(f.id,{...p,depth:f.knobs.find(k=>k.key==='depth').max});assert(checkGeometrySize(max.paths.length,max.points)>0);assert(max.paths.every(p=>p.points.every(pt=>pt.every(Number.isFinite))));
  }
});
