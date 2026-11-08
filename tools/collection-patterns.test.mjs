import test from 'node:test';
import assert from 'node:assert/strict';
import {families,defaults,generate,generateSteps,fitPaths,physicalOptions,loadLimits,checkGeometrySize} from '../patterns/web/generators.js';
import {familyGroups,groupForFamily} from '../patterns/web/groups.js';
import {collectionFamilies} from '../patterns/web/collection.js';
import {flowerPaths,plantPresets,plantCommands,plantGeometry} from '../patterns/web/botanical.js';
import {subdividePinwheel,pinwheelTiles,pinwheelEdges} from '../patterns/web/pinwheel.js';
import {schottkySystem,schottkyPoint,schottkyCircle,schottkyCircles} from '../patterns/web/schottky.js';
import {solidFaces,hatchedSolidGeometry,insetSegment} from '../patterns/web/hatched-solids.js';
await loadLimits();
const finish=it=>{let q;while(!(q=it.next()).done);return q.value;},dist=(a,b)=>Math.hypot(...a.map((v,i)=>v-b[i]));
const area=([a,b,c])=>Math.abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))/2;
const length=paths=>paths.reduce((s,p)=>s+p.points.slice(1).reduce((s,b,i)=>s+dist(p.points[i],b),0),0);
const key=p=>p.map(v=>Math.round(v*1e8)).join(','),edge=p=>p.map(key).sort().join('/');

test('Gallery groups partition every generator exactly once, with stable distinct identities',()=>{
 const ids=familyGroups.flatMap(g=>g.families);assert.equal(new Set(ids).size,ids.length);assert.deepEqual(ids.toSorted(),families.map(f=>f.id).toSorted());assert.equal(new Set(familyGroups.map(g=>g.id)).size,familyGroups.length);
 for(const f of families)assert(groupForFamily(f.id));assert.equal(groupForFamily('unknown'),undefined);
});

test('Flower petals close, repeat rotationally within each whorl, and leaf pairs attach to the curved stem',()=>{
 for(const shape of [0,1,2]){
  const p={...defaults('flowers'),shape,inkCount:8},paths=flowerPaths(p);assert.equal(paths.length,2+2*p.leaves+p.petals*p.whorls);assert.equal(new Set(paths.map(p=>p.role)).size,8);
  for(const path of paths.slice(1))assert.deepEqual(path.points[0],path.points.at(-1));
  for(let row=0;row<p.whorls;row++){
   const petals=paths.slice(1+2*p.leaves+row*p.petals,1+2*p.leaves+(row+1)*p.petals),angle=2*Math.PI/p.petals;
   for(let i=0;i<petals.length;i++)for(let j=0;j<petals[i].points.length;j++){
    const [x,y]=petals[i].points[j],rotated=[x*Math.cos(angle)-y*Math.sin(angle),x*Math.sin(angle)+y*Math.cos(angle)];assert(dist(rotated,petals[(i+1)%p.petals].points[j])<1e-12);assert(Math.hypot(x,y)<=.48**row+1e-12);
   }
  }
  for(const leaf of paths.slice(1,1+2*p.leaves)){const [x,y]=leaf.points[0];assert(Math.abs(x-p.bend*Math.sin(Math.PI*y/2.6))<1e-12);}
  assert(Math.hypot(...paths[0].points[0])>=1.03,'Stem does not cross the bloom');
 }
});

test('Prepared plant grammars agree with parallel rewriting and an independent stacked turtle',()=>{
 for(let shape=0;shape<plantPresets.length;shape++)for(const depth of [2,3,4]){
  const p={...defaults('lsystem'),shape,depth,leaves:0},preset=plantPresets[shape];let word=preset.axiom;
  for(let n=0;n<depth;n++)word=[...word].map(c=>preset.rules[c]||c).join('');assert.equal([...plantCommands(p)].join(''),word);
  let x=0,y=0,a=-Math.PI/2,d=0;const stack=[],expected=new Set();
  for(const c of word){if(c==='F'){const dx=Math.cos(a)*p.taper**d,dy=Math.sin(a)*p.taper**d;expected.add(edge([[x-p.lean*y,y],[x+dx-p.lean*(y+dy),y+dy]]));x+=dx;y+=dy;}else if(c==='+')a+=preset.angle*p.spread*Math.PI/180;else if(c==='-')a-=preset.angle*p.spread*Math.PI/180;else if(c==='['){stack.push([x,y,a,d]);d++;}else if(c===']')[x,y,a,d]=stack.pop();}
  assert.equal(stack.length,0);const g=finish(plantGeometry(p));assert.deepEqual(new Set(g.edges.map(e=>edge(e.points))),expected);assert.equal(g.edges.length,expected.size,'No copied branch is retraced');
  assert(Math.abs(length(g.paths)-length(g.edges))<1e-8,'Joining adds no pen-down return walk');
 }
});

test('Plant leaves retain their real branch tips and refinement keeps a fixed paper frame',()=>{
 for(let shape=0;shape<plantPresets.length;shape++){
  const p={...defaults('lsystem'),shape},g=finish(plantGeometry(p)),degree=new Map();for(const e of g.edges)for(const pt of e.points)degree.set(key(pt),(degree.get(key(pt))||0)+1);
  for(const leaf of g.paths.filter(p=>p.role===2)){assert.equal(degree.get(key(leaf.points[0])),1);assert.deepEqual(leaf.points[0],leaf.points.at(-1));}
  assert.equal(g.paths.filter(p=>p.role===2).length,[...degree].filter(([k,d])=>d===1&&k!=='0,0').length);
  assert.deepEqual(generate('lsystem',p).frame,generate('lsystem',{...p,depth:2}).frame);
 }
});

test('Pinwheel replacements preserve 1:2 right triangles, exact integer vertices and area',()=>{
 let tiles=[[[0,0],[2,0],[0,1]]];
 for(let depth=0;depth<4;depth++){
  for(const t of tiles){const children=subdividePinwheel(t);assert.equal(children.length,5);assert.equal(children.reduce((s,c)=>s+area(c),0),100*area(t));
   for(const c of children){assert.equal(area(c),20*area(t));assert(c.flat().every(Number.isSafeInteger));const u=c[1].map((v,i)=>v-c[0][i]),v=c[2].map((v,i)=>v-c[0][i]);assert(u[0]*v[0]+u[1]*v[1]===0);assert.equal(u[0]**2+u[1]**2,4*(v[0]**2+v[1]**2));}
  }tiles=tiles.flatMap(subdividePinwheel);
 }
});

test('Pinwheel T-junctions and partially shared sides are drawn once without dropping boundary length',()=>{
 for(const depth of [1,3,5])for(const patch of [0,1]){
  const {tiles,denominator:d}=pinwheelTiles({depth,patch}),edges=pinwheelEdges(tiles,d),expected=((3+Math.sqrt(5))*(patch?2:1)*Math.sqrt(5)**depth+(patch?6:3+Math.sqrt(5)))/2;
  assert.equal(tiles.length,(patch?2:1)*5**depth);assert(Math.abs(length(edges)-expected)<1e-8);assert.equal(new Set(edges.map(e=>edge(e.points))).size,edges.length);
  const angles=new Set(tiles.map(t=>Math.atan2(t[1][1]-t[0][1],t[1][0]-t[0][0]).toFixed(8)));if(depth>=3)assert(angles.size>8);
 }
});

test('Schottky generators invert exactly; analytic circle images match transformed sample points and stay nested',()=>{
 for(const pairs of [2,3])for(const twist of [0,90,180]){
  const p={...defaults('schottky'),pairs,twist},system=schottkySystem(p);
  for(let i=0;i<system.maps.length;i++){
   const map=system.maps[i],point=[2.3,-1.8];assert(dist(schottkyPoint(schottkyPoint(point,map),system.maps[map.inverse]),point)<1e-12);
   for(let j=0;j<system.circles.length;j++)if(j!==map.inverse){const c=system.circles[j],image=schottkyCircle(c,map);for(let k=0;k<19;k++){const pt=schottkyPoint([c.x+c.r*Math.cos(k),c.y+c.r*Math.sin(k)],map);assert(Math.abs(Math.hypot(pt[0]-image.x,pt[1]-image.y)-image.r)<1e-12);}}
  }
  const circles=[...schottkyCircles(p)];for(const c of circles)if(c.parent){assert(Math.hypot(c.x-c.parent.x,c.y-c.parent.y)+c.r<=c.parent.r+1e-11);assert(c.r>=p.cutoff);assert.notEqual(c.word.at(-1),system.maps[c.word.at(-2)].inverse);}
  const uncut=[...schottkyCircles({...p,depth:2,cutoff:0})];assert.equal(uncut.length,2*pairs*(1+(2*pairs-1)+(2*pairs-1)**2));
 }
});

test('Polyhedron hulls have exact topology, outward planar faces and a single shared-edge census',()=>{
 for(const [shape,v,e,f] of [[0,8,12,6],[1,6,12,8],[2,12,30,20]]){
  const {vertices,faces}=solidFaces(shape),edges=new Map();assert.equal(vertices.length,v);assert.equal(faces.length,f);
  for(const face of faces){for(const pt of face.points)assert(Math.abs(face.normal.reduce((s,n,i)=>s+n*(pt[i]-face.center[i]),0))<1e-12);for(const pt of vertices)assert(face.normal.reduce((s,n,i)=>s+n*(pt[i]-face.center[i]),0)<1e-10);
   for(let i=0;i<face.ids.length;i++){const id=[face.ids[i],face.ids[(i+1)%face.ids.length]].sort((a,b)=>a-b).join(',');edges.set(id,(edges.get(id)||0)+1);}
  }assert.equal(edges.size,e);assert([...edges.values()].every(n=>n===2));assert.equal(v-e+f,2);
 }
});

test('Hatch clipping includes round pen footprints and respects winding; broad pens shorten hatches, not edges',()=>{
 const square=[[-1,-1],[1,-1],[1,1],[-1,1]],a=[-2,.5],b=[2,.5];assert.deepEqual(insetSegment(a,b,square,.2),[.3,.7]);assert.deepEqual(insetSegment(a,b,square.toReversed(),.2),[.3,.7]);assert.equal(insetSegment([-2,.95],[2,.95],square,.2),null);
 for(const shape of [0,1,2])for(const azimuth of [5,35,85]){
  const p={...defaults('hatched_solids'),shape,azimuth},thin=finish(hatchedSolidGeometry({...p,inkWidths:[.003,.004,.005]})),fat=finish(hatchedSolidGeometry({...p,inkWidths:[.02,.03,.04]}));assert.deepEqual(thin.edges,fat.edges);assert(length(fat.hatches)<length(thin.hatches));
  for(const h of fat.hatches){const poly=h.polygon,area=poly.reduce((s,p,i)=>s+p[0]*poly[(i+1)%poly.length][1]-p[1]*poly[(i+1)%poly.length][0],0);for(const pt of h.points)for(let i=0;i<poly.length;i++){const a=poly[i],b=poly[(i+1)%poly.length],distance=Math.sign(area)*((b[0]-a[0])*(pt[1]-a[1])-(b[1]-a[1])*(pt[0]-a[0]))/dist(a,b);assert(distance>=h.inset-1e-10);}}
 }
});

test('Five new families provide lossless progress, default one-ink support and maximum whole-pen page containment',()=>{
 const maxima={flowers:{petals:24,whorls:4,leaves:5},lsystem:{depth:6},pinwheel:{depth:5,insets:3},schottky:{depth:8,pairs:3,cutoff:.0005},hatched_solids:{density:60,copies:3}};
 for(const f of collectionFamilies){
  const steps=generateSteps(f.id,defaults(f.id)),paths=[];let q,prior=0;while(!(q=steps.next()).done){assert(q.value.fraction>=prior);prior=q.value.fraction;paths.push(...q.value.batch||[]);}assert.deepEqual(paths,q.value.paths);
  if(f.sequence)assert(generate(f.id,defaults(f.id),()=>{},{inkCount:1}).paths.every(p=>p.role===0));
  const params={...defaults(f.id),...maxima[f.id]},count=f.sequence?8:3,recipe={family:f.id,params,media:{sheet_size_mm:[431.8,279.4],printable_rect_mm:[39,11,377,253],content_inset_mm:1},size:.96,rotation:37,inks:Array.from({length:count},(_,i)=>({id:'i'+i,width_mm:.25+i*.1})),roles:Array.from({length:count},(_,i)=>({ink_id:'i'+i}))};
  const g=generate(f.id,params,()=>{},{...(f.sequence?{inkCount:count}:{}),...physicalOptions(recipe)});checkGeometrySize(g.paths.length,g.points);const fitted=fitPaths(g,recipe),[x,y,w,h]=recipe.media.printable_rect_mm;assert(fitted.paths.length>0);
  for(const path of fitted.paths){const r=recipe.inks[path.role].width_mm/2;for(const [px,py] of path.points)assert(px-r>=x&&py-r>=y&&px+r<=x+w&&py+r<=y+h);}
 }
});
