import test from 'node:test';
import assert from 'node:assert/strict';
import {defaults,generate,generateSteps,fitPaths,physicalOptions,loadLimits} from '../patterns/web/generators.js';
import {textileFamilies,starTiles,starGraph,starRole,interlaceStars,borderCarrier,guillocheLine} from '../patterns/web/textile.js';
import {waveModes,waveValue,reactionPresets,reactionStep,reactionSimulation,reactionField,clearReactionCache} from '../patterns/web/wave-fields.js';
await loadLimits();
const finish=it=>{let q;while(!(q=it.next()).done);return q.value;};
const dist=(a,b)=>Math.hypot(...a.map((v,i)=>v-b[i])),lerp=(a,b,t)=>a.map((v,i)=>v+t*(b[i]-v));
const length=paths=>paths.reduce((s,p)=>s+p.points.slice(1).reduce((s,b,i)=>s+dist(p.points[i],b),0),0);
const key=p=>p.map(v=>Math.round(v*1e9)).join(',');
const pointDistance=(p,a,b)=>{const v=b.map((x,i)=>x-a[i]),t=Math.max(0,Math.min(1,v.reduce((s,x,i)=>s+x*(p[i]-a[i]),0)/(v[0]**2+v[1]**2||1)));return dist(p,lerp(a,b,t));};
const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
function segmentDistance(a,b,c,d){
 if(cross(a,b,c)*cross(a,b,d)<0&&cross(c,d,a)*cross(c,d,b)<0)return 0;
 return Math.min(pointDistance(a,c,d),pointDistance(b,c,d),pointDistance(c,a,b),pointDistance(d,a,b));
}

test('Star templates remain regular; motifs follow the requested edge angle and join without duplicated edges',()=>{
 for(const lattice of [0,1,2])for(const angle of [25,45,60,75]){
  const p={...defaults('starweave'),lattice,angle},tiles=starTiles(p),g=starGraph(p),seen=new Set();let offset=0;
  for(const tile of tiles){
   const side=dist(tile[0],tile[1]),n=tile.length;assert(tile.every((a,i)=>Math.abs(dist(a,tile[(i+1)%n])-side)<1e-12));
   for(let i=0;i<n;i++){
    const a=tile[i],b=tile[(i+1)%n],mid=lerp(a,b,.5),tip=g.nodes[g.edges[offset+2*i].b].point;
    const cosine=((b[0]-a[0])*(tip[0]-mid[0])+(b[1]-a[1])*(tip[1]-mid[1]))/(side*dist(mid,tip));
    assert(Math.abs(cosine-Math.cos(angle*Math.PI/180))<1e-12);
    assert(tile.every((v,j)=>cross(v,tile[(j+1)%n],tip)>=-1e-12),'Every motif tip stays inside its template');
   }offset+=2*n;
  }
  for(const e of g.edges){const k=[key(g.nodes[e.a].point),key(g.nodes[e.b].point)].sort().join('/');assert(!seen.has(k));seen.add(k);}
  assert.equal(g.paths.reduce((s,p)=>s+p.segmentIds.length,0),g.edges.length);
  assert.equal(new Set(g.paths.flatMap(p=>p.segmentIds)).size,g.edges.length);
  for(const path of g.paths){assert.deepEqual(path.points[0],path.points.at(-1));for(let i=0;i<path.hits.length;i++)assert.notEqual(path.hits[i].over,path.hits[(i+1)%path.hits.length].over);}
  for(const hits of g.crossings.values()){assert.equal(hits.length,2);assert.notEqual(hits[0].over,hits[1].over);for(const hit of hits){const path=g.paths[hit.path];for(const count of [1,7,8])assert.equal(starRole(path,hit.path,hit.vertex-1,count),starRole(path,hit.path,hit.vertex,count),'Color cannot change inside a crossing');}}
 }
});

test('Star underpasses clear both finite pen footprints, including unequal widths, round ends and generous gaps',()=>{
 for(const lattice of [0,1,2])for(const angle of [25,60,75])for(const gap of [0,1,3]){
  const g=starGraph({...defaults('starweave'),lattice,angle}),widths=[.003,.009,.005,.016,.004,.007,.002],{paths,cuts}=interlaceStars(g,widths,gap);
  for(const hits of g.crossings.values()){
   const over=hits.find(h=>h.over),under=hits.find(h=>!h.over),op=g.paths[over.path],up=g.paths[under.path],v=over.vertex;
   const legs=[[op.points[v-1],op.points[v]],[op.points[v],op.points[v+1]]],ow=widths[starRole(op,over.path,v,widths.length)];
   for(const i of [under.vertex-1,under.vertex]){
    const r=(ow+widths[starRole(up,under.path,i,widths.length)])/2+gap*Math.max(...widths),a=up.points[i],b=up.points[i+1];let t=0;
    const check=(lo,hi)=>{if(hi-lo>1e-9)for(const [c,d] of legs)assert(segmentDistance(lerp(a,b,lo),lerp(a,b,hi),c,d)>=r-1e-9,'Surviving underpass cannot touch the overpass capsule');};
    for(const [lo,hi] of cuts[under.path][i].toSorted((a,b)=>a[0]-b[0])){if(lo>t)check(t,lo);t=Math.max(t,hi);}if(t<1)check(t,1);
   }
  }
  assert(paths.every(p=>p.points.length>1&&length([p])>1e-10));
  assert(length(paths)<=length(interlaceStars(g,widths,0).paths)+1e-9,'Increasing clearance only removes ink');
 }
});

test('Star composition stays fixed across detail and physical-width changes; all selected inks remain usable',()=>{
 const p=defaults('starweave'),base={family:'starweave',params:p,media:{sheet_size_mm:[431.8,279.4],printable_rect_mm:[39,11,377,253],content_inset_mm:1},size:.96,rotation:0};
 for(const count of [1,7,8]){
  const recipe={...base,roles:Array.from({length:count},(_,i)=>({ink_id:'ink'+i})),inks:Array.from({length:count},(_,i)=>({id:'ink'+i,width_mm:.25+.1*i}))};
  const g=generate('starweave',p,()=>{},{inkCount:count,...physicalOptions(recipe)});assert.equal(new Set(g.paths.map(p=>p.role)).size,count);
  for(const depth of [1,6])assert.deepEqual(generate('starweave',{...p,depth}).frame,g.frame);
  assert.deepEqual(physicalOptions(recipe),physicalOptions({...recipe,params:{...p,depth:6,gap:3}}));
  for(const rotation of [0,37,90,180]){
   const r={...recipe,rotation},f=fitPaths(generate('starweave',p,()=>{},{inkCount:count,...physicalOptions(r)}),r),[x,y,w,h]=r.media.printable_rect_mm;
   for(const path of f.paths){const radius=r.inks[path.role].width_mm/2;for(const [px,py] of path.points)assert(px-radius>=x&&py-radius>=y&&px+radius<=x+w&&py+radius<=y+h);}
  }
 }
});

test('Guilloché closes every line and rounded-border normals remain continuous without inverted corners',()=>{
 const p=defaults('guilloche');
 for(const form of [0,1,2])for(const amplitude of [.04,.24]){
  const q={...p,form,amplitude,lobes:24,twist:12},g=generate('guilloche',q,()=>{},{inkCount:8});assert.deepEqual(g.frame,generate('guilloche',{...q,lines:12}).frame,'Refinement keeps its page frame');assert.equal(g.paths.length,p.lines);assert.equal(new Set(g.paths.map(p=>p.role)).size,8);
  for(let line=0;line<p.lines;line++){
   const pts=guillocheLine(q,line);assert.deepEqual(pts[0],pts.at(-1));
   for(let i=0;i<pts.length-1;i+=37){const t=i/(pts.length-1),phase=12*line*Math.PI/180,wave=amplitude*Math.cos(48*Math.PI*t+phase),f=line/(p.lines-1);
    if(form<2){const radius=.45+.5*f+wave+(form===1?.35*amplitude*Math.cos(24*Math.PI*t-phase):0);assert(Math.abs(Math.hypot(...pts[i])-radius)<1e-12);assert(radius>0);}
    else{const c=borderCarrier(t),offset=.28*(f-.5)+wave;assert(dist(pts[i],c.point.map((x,j)=>x+c.normal[j]*offset))<1e-12);assert(offset>-.45);}
   }
  }
 }
 for(let i=0;i<=10000;i++){const t=i/10000,a=borderCarrier(t),b=borderCarrier(t+1e-7);assert(Math.abs(Math.hypot(...a.normal)-1)<1e-12);assert(dist(a.point,b.point)<1e-6);assert(dist(a.normal,b.normal)<3e-6);}
 assert.deepEqual(borderCarrier(0),borderCarrier(1));
});

test('Standing-wave contours follow the analytic field and never jump across the grid',()=>{
 for(let mode=0;mode<waveModes.length;mode++)for(const bands of [1,7]){
  const p={...defaults('wave_contours'),mode,bands},g=generate('wave_contours',p,()=>{},{inkCount:8}),n=96*p.depth,[m,k]=waveModes[mode];
  assert(Math.abs(waveValue(p,.23,.71)-(Math.cos(m*Math.PI*.23)*Math.cos(k*Math.PI*.71)-Math.cos(k*Math.PI*.23)*Math.cos(m*Math.PI*.71)))<1e-12);
  for(const path of g.paths){const level=bands===1||path.role===3?1e-10:-p.spread+2*p.spread*path.role/(bands-1);
   for(const [x,y] of path.points)assert(Math.abs(waveValue(p,(x+1)/2,(y+1)/2)-level)<.004,'Isolines agree with an independent analytic evaluation');
   for(let i=1;i<path.points.length;i++)assert(dist(path.points[i-1],path.points[i])<=2*Math.SQRT2/n+1e-10);
  }
 }
});

test('Gray–Scott uses simultaneous periodic finite differences and preserves its uniform equilibrium',()=>{
 const n=5,u=Float64Array.from({length:n*n},(_,i)=>.4+.02*i),v=Float64Array.from({length:n*n},(_,i)=>.05+.004*i),s={n,u:u.slice(),v:v.slice(),nextU:new Float64Array(n*n),nextV:new Float64Array(n*n),iterations:0};
 reactionStep(s,.035,.06);
 for(let y=0;y<n;y++)for(let x=0;x<n;x++){
  const i=y*n+x,lap=a=>[[1,0],[-1,0],[0,1],[0,-1]].reduce((sum,[dx,dy])=>sum+a[((y+dy+n)%n)*n+(x+dx+n)%n]-a[i],0),reaction=u[i]*v[i]**2;
  assert(Math.abs(s.u[i]-(u[i]+.16*lap(u)-reaction+.035*(1-u[i])))<1e-15);
  assert(Math.abs(s.v[i]-(v[i]+.08*lap(v)+reaction-.095*v[i]))<1e-15);
 }
 s.u.fill(1);s.v.fill(0);for(let i=0;i<200;i++)reactionStep(s,.035,.06);assert(s.u.every(x=>x===1)&&s.v.every(x=>x===0));
});

test('Reaction growth checkpoints resume exactly and cannot mutate earlier emitted fields',()=>{
 const p=defaults('reaction');clearReactionCache();
 const early=finish(reactionSimulation({...p,time:1000})),field=reactionField(early),snapshot=field.values.slice(),resumed=finish(reactionSimulation(p)),fresh=finish(reactionSimulation(p,{cache:false}));
 assert.deepEqual(resumed.u,fresh.u);assert.deepEqual(resumed.v,fresh.v);assert.deepEqual(field.values,snapshot);
 for(const change of [{time:1000},{seed:8},{shape:1},{scale:0}]){const q={...p,...change},cached=finish(reactionSimulation(q)),fresh=finish(reactionSimulation(q,{cache:false}));assert.deepEqual(cached.v,fresh.v);}
});

test('Reaction presets remain finite through the supported endpoints; cubic contours preserve the periodic domain',()=>{
 for(let shape=0;shape<reactionPresets.length;shape++)for(const scale of [0,2])for(const seed of [1,30]){
  const p={...defaults('reaction'),shape,scale,seed,time:6000},s=finish(reactionSimulation(p,{cache:false}));
  assert(s.u.every(x=>Number.isFinite(x)&&x>=0&&x<=1));assert(s.v.every(x=>Number.isFinite(x)&&x>=0&&x<=1));assert(Math.max(...s.v)>.05,'Curated presets do not fade away');
  const f=reactionField(s);for(let y=0;y<=s.n;y++)for(let x=0;x<=s.n;x++)assert.equal(f.values[(2*y)*(f.n+1)+2*x],s.v[(y%s.n)*s.n+x%s.n]);
  for(let i=0;i<=f.n;i++){assert.equal(f.values[i],f.values[f.n*(f.n+1)+i]);assert.equal(f.values[i*(f.n+1)],f.values[i*(f.n+1)+f.n]);}
 }
 const p={...defaults('reaction'),shape:1},g=generate('reaction',p);assert(g.paths.length>200,'The spots preset retains separate concentration islands');
 const n=(96+32*p.scale)*2;for(const path of g.paths)for(let i=1;i<path.points.length;i++)assert(dist(path.points[i-1],path.points[i])<=2*Math.SQRT2/n+1e-10,'Periodic simulation never becomes a cross-page pen stroke');
});

test('New families deliver lossless, monotone incremental batches and one-band drawings work with one ink',()=>{
 for(const f of textileFamilies){
  const steps=generateSteps(f.id,defaults(f.id)),paths=[];let q,previous=0,count=0;
  while(!(q=steps.next()).done){assert(q.value.fraction>=previous);previous=q.value.fraction;paths.push(...q.value.batch||[]);count++;}assert(count>1);assert.deepEqual(paths,q.value.paths);
  const g=generate(f.id,{...defaults(f.id),...(['wave_contours','reaction'].includes(f.id)?{bands:1}:{})},()=>{},{inkCount:1});assert(g.paths.length>0);assert(g.paths.every(p=>p.role===0));
 }
});

test('Largest supported drawings remain within rotated full-pen page bounds',()=>{
 const maxima={starweave:{depth:6,angle:25,gap:3},guilloche:{lines:72,lobes:24,amplitude:.24,twist:12,form:2},wave_contours:{mode:4,depth:5,bands:9},reaction:{scale:2,time:6000,bands:7,shape:1}};
 for(const f of textileFamilies){
  const params={...defaults(f.id),...maxima[f.id]},recipe={family:f.id,params,media:{sheet_size_mm:[279.4,431.8],printable_rect_mm:[15,39,249.4,377.8],content_inset_mm:1},size:.96,rotation:37,inks:Array.from({length:8},(_,i)=>({id:'i'+i,width_mm:.25+.1*i})),roles:Array.from({length:8},(_,i)=>({ink_id:'i'+i}))};
  const g=generate(f.id,params,()=>{},{inkCount:8,...physicalOptions(recipe)}),fitted=fitPaths(g,recipe),[x,y,w,h]=recipe.media.printable_rect_mm;assert(fitted.paths.length>0);
  for(const path of fitted.paths){const r=recipe.inks[path.role].width_mm/2;for(const [px,py] of path.points)assert(Number.isFinite(px)&&Number.isFinite(py)&&px-r>=x&&py-r>=y&&px+r<=x+w&&py+r<=y+h);}
 }
});
