// Original constructions: a Gosper walk, off-lattice diffusion growth, and
// analytically clipped tetrahedron edges. Coordinates remain mathematical units.
import {project3} from './natural.js';
const knob=(key,label,value,min,max,step=1,choices)=>({key,label,value,min,max,step,...(choices?{choices}:{})});
const sequence={sequence:true,roles:['First sections','Second sections','Third sections']};
export const spatialFamilies=[
  {id:'gosper',name:'Gosper curve',...sequence,knobs:[knob('depth','Detail',4,2,6),knob('round','Corner rounding',.15,0,.45,.15)]},
  {id:'dla',name:'Diffusion growth',...sequence,knobs:[knob('particles','Particles',2000,500,6000,500),knob('seed_shape','Starting seed',0,0,1,1,['Point','Line']),knob('seed','Variation',7,1,30)]},
  {id:'tetrahedron',name:'Sierpiński tetrahedra',...sequence,knobs:[knob('depth','Detail',4,1,6),knob('azimuth','Azimuth',25,-85,85,10),knob('elevation','Elevation',25,-15,85,10),knob('wireframe','Edges',0,0,1,1,['Visible edges','All edges · wireframe'])]},
];
const tau=2*Math.PI,mix=(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t);
const randomFrom=seed=>()=>{seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;return (seed>>>0)/4294967296;};

// Hexagonal Gosper's two edge-replacement rules (ABOP, figure 1.11).
// Traverse the grammar without allocating an exponentially long command string.
export function* gosperWalk(depth){
  const rules={A:'A-B--B+A++AA+B-',B:'+A-BB--B-A++A+B'},directions=Array.from({length:6},(_,i)=>[Math.cos(i*Math.PI/3),Math.sin(i*Math.PI/3)]);
  let x=0,y=0,heading=0;yield [x,y];
  function* walk(symbol,d){
    if(!d){const [dx,dy]=directions[heading];x+=dx;y+=dy;yield [x,y];return;}
    for(const command of rules[symbol])if(command==='+')heading=(heading+1)%6;else if(command==='-')heading=(heading+5)%6;else yield* walk(command,d-1);
  }
  yield* walk('A',depth);
}
function* gosper(p,guard){
  const count=p.inkCount||3,n=7**p.depth,output=[],points=[];
  guard(count,4*n+count);
  for(const pt of gosperWalk(p.depth)){
    points.push(pt);if(points.length%4096===0)yield {fraction:.4*points.length/(n+1),label:'Folding the Gosper curve'};
  }
  // Each order has the same overall direction, making detail edits easy to compare.
  const end=points.at(-1),angle=-Math.atan2(end[1],end[0]),scale=1/Math.hypot(...end),c=Math.cos(angle)*scale,s=Math.sin(angle)*scale;
  const rounded=[points[0]];
  for(let i=1;i<n;i++){
    if(!p.round){rounded.push(points[i]);continue;}
    const a=mix(points[i],points[i-1],p.round),b=points[i],d=mix(b,points[i+1],p.round);rounded.push(a);
    for(let j=1;j<=3;j++){const t=j/3;rounded.push(a.map((v,k)=>(1-t)**2*v+2*t*(1-t)*b[k]+t*t*d[k]));}
    if(i%4096===0)yield {fraction:.4+.3*i/n,label:'Rounding Gosper corners'};
  }
  rounded.push(end);
  const section=Math.ceil((rounded.length-1)/count);let total=0;
  for(let i=0;i<count;i++){
    const pts=rounded.slice(i*section,Math.min(rounded.length,(i+1)*section+1)).map(([x,y])=>[c*x-s*y,s*x+c*y]);
    if(pts.length<2)continue;const path={role:i,points:pts};output.push(path);total+=pts.length;
    yield {fraction:.7+.3*(i+1)/count,label:'Joining Gosper sections',batch:[path],paths:output.length,points:total};
  }
  return output;
}

// Incremental quadtree: nearest-particle queries prune empty/distant quadrants.
// A walking particle can jump to the boundary of a circle known to contain no
// aggregate. Uniform exit angles are the Brownian walk-on-circles construction.
export class ParticleIndex {
  constructor(){this.root={x:-8192,y:-8192,size:16384,points:[]};}
  insert(point){
    const add=(node,p)=>{
      if(node.children){const half=node.size/2,index=Number(p.x>=node.x+half)+2*Number(p.y>=node.y+half);add(node.children[index],p);return;}
      node.points.push(p);if(node.points.length<=8||node.size<=.5)return;
      const half=node.size/2;node.children=Array.from({length:4},(_,i)=>({x:node.x+(i%2)*half,y:node.y+Math.floor(i/2)*half,size:half,points:[]}));
      const old=node.points;delete node.points;for(const q of old)add(node,q);
    };add(this.root,point);
  }
  nearest(x,y){
    let distance2=Infinity,point=null;
    const lower=node=>Math.max(node.x-x,0,x-node.x-node.size)**2+Math.max(node.y-y,0,y-node.y-node.size)**2;
    const visit=node=>{
      if(lower(node)>distance2)return;
      if(node.children){const children=node.children.map(node=>({node,d:lower(node)})).sort((a,b)=>a.d-b.d);for(const child of children)if(child.d<=distance2)visit(child.node);}
      else for(const p of node.points){const d=(p.x-x)**2+(p.y-y)**2;if(d<distance2||(d===distance2&&p.id<point.id)){distance2=d;point=p;}}
    };visit(this.root);return {point,distance:Math.sqrt(distance2)};
  }
}
// A finite DLA approximation: absorbing distance 1.01, launch radius R+5,
// relaunch outside 3R+20. Particle centers never overlap (separation >= 1).
// Parents record actual attachment, never arbitrary nearest points afterward.
export function* aggregate(p){
  const random=randomFrom(p.seed),index=new ParticleIndex(),particles=[];let radius=0,steps=0;
  const add=(x,y,parent)=>{const pt={x,y,parent,id:particles.length};particles.push(pt);index.insert(pt);radius=Math.max(radius,Math.hypot(x,y));};
  if(p.seed_shape){for(let i=-24;i<=24;i++)add(i,0,i===-24?-1:particles.length-1);}else add(0,0,-1);
  const seedCount=particles.length,polar=r=>{const a=random()*tau;return [r*Math.cos(a),r*Math.sin(a)];};
  for(let i=0;i<p.particles;i++){
    let [x,y]=polar(radius+5);
    for(;;){
      if(++steps>p.particles*10000)throw Error('Growth exceeded its walk budget. Try a different variation or fewer particles.');
      if(steps%2048===0)yield {fraction:.85*i/p.particles,label:`Diffusing particles · ${i.toLocaleString()} / ${p.particles.toLocaleString()}`};
      const nearest=index.nearest(x,y);
      if(nearest.distance<=1.01){add(x,y,nearest.point.id);break;}
      const [dx,dy]=polar(nearest.distance-1);x+=dx;y+=dy;
      if(Math.hypot(x,y)>3*radius+20)[x,y]=polar(radius+5);
    }
  }
  return {particles,seedCount,steps};
}

// Join degree-two chains of a single role. Each edge is retained exactly once;
// branches are not joined by a fictitious connector or a retraced tree walk.
export function joinEdges(edges){
  const vertices=new Map(),output=[],key=(p,role)=>role+':'+p.map(v=>v.toFixed(10)).join(',');
  for(let i=0;i<edges.length;i++)for(const pt of edges[i].points){const k=key(pt,edges[i].role);if(!vertices.has(k))vertices.set(k,[]);vertices.get(k).push(i);}
  const used=new Uint8Array(edges.length);
  const trace=(i,start)=>{
    const role=edges[i].role,points=[start];let at=start;
    while(!used[i]){
      used[i]=1;const ends=edges[i].points,next=key(ends[0],role)===key(at,role)?ends[1]:ends[0];points.push(next);at=next;
      const incident=vertices.get(key(at,role));if(incident.length!==2)break;i=incident[0]===i?incident[1]:incident[0];
    }
    output.push({role,points});
  };
  for(let i=0;i<edges.length;i++)if(!used[i]){const edge=edges[i],end=edge.points.find(pt=>vertices.get(key(pt,edge.role)).length!==2);if(end)trace(i,end);}
  for(let i=0;i<edges.length;i++)if(!used[i])trace(i,edges[i].points[0]);
  return output;
}
function* batches(paths,start,label,guard){
  let points=0;for(let i=0;i<paths.length;i+=128){const batch=paths.slice(i,i+128);points+=batch.reduce((n,p)=>n+p.points.length,0);const sent=i+batch.length;guard(sent,points);yield {fraction:start+(1-start)*sent/paths.length,label,batch,paths:sent,points};}return paths;
}
function* dla(p,guard){
  guard(p.particles+49,4*(p.particles+49));const {particles}=yield* aggregate(p),count=p.inkCount||3;
  const edges=particles.filter(pt=>pt.parent>=0).map(pt=>{const parent=particles[pt.parent],role=Math.floor(((Math.atan2(pt.y,pt.x)+tau)%tau)/tau*count);return {role,points:[[parent.x,parent.y],[pt.x,pt.y]]};});
  return yield* batches(joinEdges(edges),.85,'Joining growth branches',guard);
}

// Regular tetrahedron, one vertex up; each child is a half-size copy at a corner.
export const tetraVertices=[[0,0,Math.sqrt(2/3)],[0,1/Math.sqrt(3),0],[-.5,-1/(2*Math.sqrt(3)),0],[.5,-1/(2*Math.sqrt(3)),0]];
export function tetrahedra(depth){
  let cells=[tetraVertices];for(let d=0;d<depth;d++){const next=[];for(const cell of cells)for(const corner of cell)next.push(cell.map(pt=>mix(corner,pt,.5)));cells=next;}return cells;
}
const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
// Exact parameter interval of AB behind a projected triangular face. All four
// half-plane/depth conditions are linear in t; coplanar face edges stay visible.
export function hiddenInterval(a,b,triangle){
  const [u,v,w]=triangle,area=cross(u,v,w);if(Math.abs(area)<1e-14)return null;
  let lo=0,hi=1;
  const constrain=(f0,f1)=>{if(Math.abs(f1-f0)<1e-14)return f0>=0;const t=-f0/(f1-f0);if(f1>f0)lo=Math.max(lo,t);else hi=Math.min(hi,t);return hi>lo+1e-10;};
  for(let i=0;i<3;i++)if(!constrain(cross(triangle[i],triangle[(i+1)%3],a)/area,cross(triangle[i],triangle[(i+1)%3],b)/area))return null;
  const depth=pt=>(cross(v,w,pt)*u[2]+cross(w,u,pt)*v[2]+cross(u,v,pt)*w[2])/area-pt[2]-1e-9;
  if(!constrain(depth(a),depth(b)))return null;return [lo,hi];
}
const edgePairs=[[0,1],[0,2],[0,3],[1,2],[1,3],[2,3]],faceIndices=[[0,1,2],[0,1,3],[0,2,3],[1,2,3]];
function* tetrahedron(p,guard){
  const count=p.inkCount||3;guard(6*4**p.depth,28*4**p.depth);
  const cells=tetrahedra(p.depth).map(cell=>cell.map(pt=>project3(pt,p.azimuth,p.elevation))),faces=[],grid=new Map(),edges=[],bins=Math.min(128,2**p.depth*2),size=2/bins;
  const keys=(pts)=>{const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]),out=[];for(let x=Math.floor(Math.min(...xs)/size);x<=Math.floor(Math.max(...xs)/size);x++)for(let y=Math.floor(Math.min(...ys)/size);y<=Math.floor(Math.max(...ys)/size);y++)out.push(x+','+y);return out;};
  if(!p.wireframe)for(let i=0;i<cells.length;i++){
    for(const ids of faceIndices){const tri=ids.map(j=>cells[i][j]),id=faces.length;faces.push(tri);for(const k of keys(tri)){if(!grid.has(k))grid.set(k,[]);grid.get(k).push(id);}}
    if(i%128===0)yield {fraction:.15*i/cells.length,label:'Indexing tetrahedron faces'};
  }
  for(let i=0;i<cells.length;i++){
    for(const [u,v] of edgePairs){
      const a=cells[i][u],b=cells[i][v],cuts=[];
      if(!p.wireframe)for(const id of new Set(keys([a,b]).flatMap(k=>grid.get(k)||[]))){const interval=hiddenInterval(a,b,faces[id]);if(interval)cuts.push(interval);}
      cuts.sort((a,b)=>a[0]-b[0]);let start=0;
      const append=(lo,hi)=>{if(hi-lo>1e-8){const points=[mix(a,b,lo).slice(0,2),mix(a,b,hi).slice(0,2)];if(Math.hypot(points[1][0]-points[0][0],points[1][1]-points[0][1])>1e-10)edges.push({points,role:Math.floor(i/4)%count});}};
      for(const [lo,hi] of cuts){if(lo>start)append(start,lo);start=Math.max(start,hi);}if(start<1)append(start,1);
    }
    if(i%64===0)yield {fraction:.15+.65*i/cells.length,label:p.wireframe?'Tracing tetrahedron edges':'Removing hidden tetrahedron edges'};
  }
  return yield* batches(joinEdges(edges),.8,'Joining visible tetrahedron edges',guard);
}
export const spatialGenerators={gosper,dla,tetrahedron};
