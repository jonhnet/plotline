// Original vector ornament constructions. Star motifs use the regular-polygon
// Hankin construction; physical interlace gaps reuse the shared capsule clipper.
import {capsuleInterval} from './natural.js';
import {fieldFamilies,fieldGenerators} from './wave-fields.js';
const knob=(key,label,value,min,max,step=1,choices)=>({key,label,value,min,max,step,...(choices?{choices}:{})});
const sequence={sequence:true,roles:['First strands','Second strands','Third strands']};
export const textileFamilies=[
 {id:'starweave',name:'Star lattices & interlace',...sequence,knobs:[knob('depth','Detail',3,1,6),knob('lattice','Lattice',2,0,2,1,['Squares','Hexagons','Octagons & squares']),knob('angle','Star angle',60,25,75,5),knob('gap','Crossing clearance',1,0,3,.5)]},
 {id:'guilloche',name:'Guilloché medallions',...sequence,knobs:[knob('form','Form',0,0,2,1,['Rosette','Double rosette','Border ribbon']),knob('lobes','Lobes',12,4,24,2),knob('amplitude','Wave depth',.12,.04,.24,.04),knob('lines','Lines',36,12,72,6),knob('twist','Twist per line',4,-12,12,2)]},
 ...fieldFamilies,
];
const tau=2*Math.PI,rad=d=>d*Math.PI/180,mix=(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t),key=p=>p.map(v=>Math.round(v*1e9)).join(',');
const polygon=(center,n,r,angle)=>Array.from({length:n},(_,i)=>[center[0]+r*Math.cos(angle+tau*i/n),center[1]+r*Math.sin(angle+tau*i/n)]);
export function starTiles(p){
 const tiles=[],d=p.depth;
 if(p.lattice===0){for(let y=-d;y<=d;y++)for(let x=-d;x<=d;x++)tiles.push([[x,y],[x+1,y],[x+1,y+1],[x,y+1]]);}
 else if(p.lattice===1){for(let q=-d;q<=d;q++)for(let r=-d;r<=d;r++)if(Math.max(Math.abs(q),Math.abs(r),Math.abs(q+r))<=d)tiles.push(polygon([1.5*q,Math.sqrt(3)*(r+q/2)],6,1,0));}
 else{
  const spacing=1+Math.SQRT2,r=1/(2*Math.sin(Math.PI/8));
  for(let y=-d;y<=d;y++)for(let x=-d;x<=d;x++)tiles.push(polygon([spacing*x,spacing*y],8,r,Math.PI/8));
  for(let y=-d;y<d;y++)for(let x=-d;x<d;x++)tiles.push(polygon([spacing*(x+.5),spacing*(y+.5)],4,Math.SQRT1_2,0));
 }
 // Every detail checkpoint uses the same page frame. Normalize uniformly so
 // template polygons stay regular and incremental refinement cannot drift.
 let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
 for(const tile of tiles)for(const [x,y] of tile){x0=Math.min(x0,x);y0=Math.min(y0,y);x1=Math.max(x1,x);y1=Math.max(y1,y);}
 const scale=2/Math.max(x1-x0,y1-y0),cx=(x0+x1)/2,cy=(y0+y1)/2;
 return tiles.map(tile=>tile.map(([x,y])=>[(x-cx)*scale,(y-cy)*scale]));
}
export function starFrame(){return [-1,-1,2,2];}
export function starGraph(p){
 const nodes=[],edges=[],midpoints=new Map();
 const addNode=(point,kind)=>{const node={point,kind,id:nodes.length,edges:[]};nodes.push(node);return node.id;};
 const addEdge=(a,b)=>{const id=edges.length;edges.push({a,b,id});nodes[a].edges.push(id);nodes[b].edges.push(id);};
 for(const tile of starTiles(p)){
  const n=tile.length,center=[0,1].map(j=>tile.reduce((s,pt)=>s+pt[j],0)/n),mid=tile.map((pt,i)=>mix(pt,tile[(i+1)%n],.5));
  const ids=mid.map(pt=>{const k=key(pt);if(!midpoints.has(k))midpoints.set(k,addNode(pt,'midpoint'));return midpoints.get(k);});
  const amount=Math.cos(Math.PI/n)*Math.cos(rad(p.angle))/Math.cos(rad(p.angle)-Math.PI/n);
  for(let i=0;i<n;i++){const tip=mix(center,tile[(i+1)%n],amount),id=addNode(tip,'tip');addEdge(ids[i],id);addEdge(id,ids[(i+1)%n]);}
 }
 for(const node of nodes){
  if(node.edges.length!==2&&node.edges.length!==4)throw Error('Star template has an invalid junction');
  const angle=id=>{const edge=edges[id],pt=nodes[edge.a===node.id?edge.b:edge.a].point;return Math.atan2(pt[1]-node.point[1],pt[0]-node.point[0]);};node.edges.sort((a,b)=>angle(a)-angle(b));
 }
 const paths=[],used=new Uint8Array(edges.length),crossings=new Map();
 for(const initial of edges)if(!used[initial.id]){
  let at=nodes[initial.a].kind==='tip'?initial.a:initial.b,e=initial.id;const points=[nodes[at].point],hits=[],segmentIds=[];
  while(!used[e]){
   used[e]=1;segmentIds.push(e);const edge=edges[e];at=edge.a===at?edge.b:edge.a;points.push(nodes[at].point);
   const node=nodes[at],index=node.edges.indexOf(e);
   if(node.edges.length===4){const hit={crossing:at,pair:index%2,path:paths.length,vertex:points.length-1};hits.push(hit);if(!crossings.has(at))crossings.set(at,[]);crossings.get(at).push(hit);}
   e=node.edges[(index+node.edges.length/2)%node.edges.length];
  }
  paths.push({points,hits,segmentIds});
 }
 // Each uninterrupted strand alternates over/under. Solve the resulting binary
 // constraints on crossings, including a closed strand's parameter seam.
 const links=new Map([...crossings.keys()].map(id=>[id,[]])),choices=new Map();
 for(const path of paths)for(let i=0;i<path.hits.length;i++){
  const a=path.hits[i],b=path.hits[(i+1)%path.hits.length],parity=1^a.pair^b.pair;
  links.get(a.crossing).push([b.crossing,parity]);links.get(b.crossing).push([a.crossing,parity]);
 }
 for(const first of crossings.keys())if(!choices.has(first)){
  choices.set(first,0);const queue=[first];for(let i=0;i<queue.length;i++)for(const [other,parity] of links.get(queue[i])){
   const choice=choices.get(queue[i])^parity;if(choices.has(other)){if(choices.get(other)!==choice)throw Error('Star strands cannot alternate at these crossings');}
   else{choices.set(other,choice);queue.push(other);}
  }
 }
 for(const path of paths)for(const hit of path.hits)hit.over=hit.pair===choices.get(hit.crossing);
 return {nodes,edges,paths,crossings};
}
// Clear only the connected underpass near each crossing, following bends if a
// wide pen reaches a nearby tip. End caps of both pens are included in radius.
// Ink boundaries occur only at motif tips, never in the middle of a crossing.
export const starRole=(path,index,segment,count)=>(index+Math.floor(Math.floor(segment/2)/Math.ceil(path.segmentIds.length/2/count)))%count;
export function interlaceStars(graph,widths,gap){
 const paths=graph.paths,cuts=paths.map(p=>p.segmentIds.map(()=>[]));
 for(const hits of graph.crossings.values()){
  const over=hits.find(h=>h.over),under=hits.find(h=>!h.over),op=paths[over.path],up=paths[under.path],on=op.segmentIds.length,un=up.segmentIds.length;
  const at=(p,i)=>p.points[(i+p.segmentIds.length)%p.segmentIds.length],ov=over.vertex%on,uv=under.vertex%un;
  const legs=[[at(op,ov-1),at(op,ov)],[at(op,ov),at(op,ov+1)]],overWidth=widths[starRole(op,over.path,ov%on,widths.length)];
  for(const direction of [-1,1])for(let step=0;step<un;step++){
   const index=(uv+(direction<0?-1-step:step)+un*2)%un,a=up.points[index],b=up.points[index+1],radius=(overWidth+widths[starRole(up,under.path,index,widths.length)])/2+gap*Math.max(...widths),intervals=legs.map(([c,d])=>capsuleInterval(a,b,c,d,radius)).filter(Boolean).sort((a,b)=>a[0]-b[0]);
   if(!intervals.length)break;let lo,hi;
   if(direction>0){if(intervals[0][0]>1e-8)break;lo=0;hi=intervals[0][1];for(const interval of intervals.slice(1))if(interval[0]<=hi+1e-8)hi=Math.max(hi,interval[1]);}
   else{const reversed=intervals.slice().reverse();if(reversed[0][1]<1-1e-8)break;lo=reversed[0][0];hi=1;for(const interval of reversed.slice(1))if(interval[1]>=lo-1e-8)lo=Math.min(lo,interval[0]);}
   if(hi-lo>1e-10)cuts[under.path][index].push([lo,hi]);if(direction>0?hi<1-1e-8:lo>1e-8)break;
  }
 }
 const output=[];
 for(let pi=0;pi<paths.length;pi++){
  const source=paths[pi],parts=[];let run=[],role=0;
  const flush=()=>{if(run.length>1)parts.push({points:run,role});run=[];};
  for(let i=0;i<source.segmentIds.length;i++){
   const nextRole=starRole(source,pi,i,widths.length);if(nextRole!==role)flush();role=nextRole;
   const a=source.points[i],b=source.points[i+1],intervals=cuts[pi][i].sort((a,b)=>a[0]-b[0]);let start=0;
   const append=(lo,hi)=>{if(hi-lo<1e-10)return;if(!run.length)run.push(mix(a,b,lo));run.push(mix(a,b,hi));};
   for(const [lo,hi] of intervals){if(lo>start)append(start,lo);flush();start=Math.max(start,hi);}if(start<1)append(start,1);
  }flush();
  if(parts.length>1&&parts[0].role===parts.at(-1).role&&key(parts[0].points[0])===key(parts.at(-1).points.at(-1))){parts.at(-1).points.push(...parts[0].points.slice(1));parts.shift();}
  output.push(...parts);
 }return {paths:output,cuts};
}
function* starweave(p,guard){
 const graph=starGraph(p);guard(graph.edges.length,graph.edges.length*4);yield {fraction:.3,label:'Joining star lattice strands'};
 const count=p.inkCount||3,widths=p.inkWidths||Array(count).fill(.003),{paths}=interlaceStars(graph,widths,p.gap);return yield* batches(paths,.4,'Clearing alternating crossings',guard);
}
function* batches(paths,start,label,guard){let points=0;for(let i=0;i<paths.length;i+=64){const batch=paths.slice(i,i+64);points+=batch.reduce((s,p)=>s+p.points.length,0);const sent=i+batch.length;guard(sent,points);yield {fraction:start+(1-start)*sent/paths.length,label,batch,paths:sent,points};}return paths;}

// Rounded rectangular carrier, parameterized by arc length, with an outward
// normal. Corner radius exceeds every permitted inward wave/band displacement.
export function borderCarrier(t){
 const w=1.2,h=.8,r=.45,segments=[{l:2*(w-r),p:[-w+r,-h],d:[1,0]},{l:Math.PI*r/2,c:[w-r,-h+r],a:-Math.PI/2},{l:2*(h-r),p:[w,-h+r],d:[0,1]},{l:Math.PI*r/2,c:[w-r,h-r],a:0},{l:2*(w-r),p:[w-r,h],d:[-1,0]},{l:Math.PI*r/2,c:[-w+r,h-r],a:Math.PI/2},{l:2*(h-r),p:[-w,h-r],d:[0,-1]},{l:Math.PI*r/2,c:[-w+r,-h+r],a:Math.PI}];
 let distance=((t%1)+1)%1*segments.reduce((s,x)=>s+x.l,0);
 for(let i=0;i<segments.length;i++){const s=segments[i];if(distance<=s.l||i===segments.length-1){if(s.p)return {point:[s.p[0]+s.d[0]*distance,s.p[1]+s.d[1]*distance],normal:[s.d[1],-s.d[0]]};const a=s.a+distance/r;return {point:[s.c[0]+r*Math.cos(a),s.c[1]+r*Math.sin(a)],normal:[Math.cos(a),Math.sin(a)]};}distance-=s.l;}
}
export function guillocheFrame(p){
 const r=.95+p.amplitude*(p.form===1?1.35:1),w=1.34+p.amplitude,h=.94+p.amplitude;
 return p.form===2?[-w,-h,2*w,2*h]:[-r,-r,2*r,2*r];
}
export function guillocheLine(p,line){
 const n=48*p.lobes,phase=rad(p.twist*line),f=line/(p.lines-1),points=[];
 for(let i=0;i<n;i++){
  const t=i/n,angle=t*tau,wave=p.amplitude*Math.cos(p.lobes*angle+phase);
  if(p.form===2){const {point,normal}=borderCarrier(t),offset=.28*(f-.5)+wave;points.push(point.map((v,j)=>v+normal[j]*offset));}
  else{const r=.45+.5*f+wave+(p.form===1?.35*p.amplitude*Math.cos(p.lobes/2*angle-phase):0);points.push([r*Math.cos(angle),r*Math.sin(angle)]);}
 }points.push(points[0]);return points;
}
function* guilloche(p,guard){guard(p.lines,p.lines*(48*p.lobes+1));const paths=[];let points=0;
 for(let i=0;i<p.lines;i++){const path={role:i%(p.inkCount||3),points:guillocheLine(p,i)};paths.push(path);points+=path.points.length;yield {fraction:(i+1)/p.lines,label:'Engraving guilloché curves',batch:[path],paths:paths.length,points};}return paths;
}
export const textileGenerators={starweave,guilloche,...fieldGenerators};
