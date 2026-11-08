// Original vector constructions; references and numerical limits in
// docs/pattern-studio.md. No raster tracing or external processing.
import {ParticleIndex,joinEdges,hiddenInterval} from './spatial.js';
import {project3} from './natural.js';
const knob=(key,label,value,min,max,step=1,choices)=>({key,label,value,min,max,step,...(choices?{choices}:{})});
const sequence={sequence:true,roles:['First branches','Second branches','Third branches']};
export const ornamentFamilies=[
  {id:'inversion',name:'Circle-inversion lace',...sequence,knobs:[knob('depth','Generations',5,2,8),knob('symmetry','Symmetry',5,3,8),knob('gap','Circle spacing',.04,0,.2,.02),knob('cutoff','Smallest circle',.002,.001,.008,.001)]},
  {id:'ammann',name:'Ammann–Beenker tiling',roles:['Tile edges','Square motifs','Diamond motifs'],knobs:[knob('depth','Detail',3,1,5),knob('patch','Patch shape',0,0,2,1,['Octagon','Circle','Square']),knob('insets','Nested motifs',2,0,4)]},
  {id:'colonization',name:'Organic trees & coral',...sequence,knobs:[knob('depth','Branch density',3,1,5),knob('crown','Growth shape',0,0,2,1,['Broad tree','Tall tree','Coral fan']),knob('spread','Spread',1,.6,1.6,.2),knob('bias','Sideways growth',0,-.3,.3,.1),knob('seed','Variation',7,1,30)]},
  {id:'architecture',name:'Recursive architecture',roles:['X edges','Y edges','Vertical edges'],knobs:[knob('depth','Detail',3,1,4),knob('form','Structure',0,0,1,1,['Temple','City']),knob('height','Height',1,.5,2,.25),knob('setback','Setback',.2,.1,.4,.05),knob('azimuth','Azimuth',35,5,85,10),knob('elevation','Elevation',35,15,75,10),knob('seed','Variation',7,1,30)]},
];
const tau=2*Math.PI,mix=(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t);
const randomFrom=seed=>()=>{seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;return (seed>>>0)/4294967296;};
const pointKey=p=>p.map(v=>Math.round(v*1e9)).join(','),edgeKey=(a,b)=>[pointKey(a),pointKey(b)].sort().join('/');
function* batches(paths,start,label,guard){
  let points=0;for(let i=0;i<paths.length;i+=64){const batch=paths.slice(i,i+64);points+=batch.reduce((s,p)=>s+p.points.length,0);const sent=i+batch.length;guard(sent,points);yield {fraction:start+(1-start)*sent/paths.length,label,batch,paths:sent,points};}return paths;
}

// Inverting a circle through a mirror circle. A circle through the inversion
// center becomes a line; curated disjoint/tangent seeds avoid that singularity.
export function invertCircle(circle,mirror){
  const dx=circle.x-mirror.x,dy=circle.y-mirror.y,q=dx*dx+dy*dy-circle.r*circle.r;
  if(Math.abs(q)<1e-14)return null;
  const scale=mirror.r*mirror.r/q;return {x:mirror.x+dx*scale,y:mirror.y+dy*scale,r:Math.abs(circle.r*scale)};
}
export function inversionSeeds(p){
  const r=Math.sin(Math.PI/p.symmetry)/(1+p.gap);
  const mirrors=Array.from({length:p.symmetry},(_,i)=>({x:Math.cos(tau*i/p.symmetry),y:Math.sin(tau*i/p.symmetry),r}));
  return {mirrors,circles:[...mirrors.map((c,i)=>({...c,last:i,sector:i,depth:0})),{x:0,y:0,r:1-r,last:-1,sector:0,depth:0},{x:0,y:0,r:1+r,last:-1,sector:0,depth:0}]};
}
export function* inversionCircles(p,guard=()=>{}){
  const {mirrors,circles}=inversionSeeds(p),seen=new Set();let level=circles;
  for(let depth=0;depth<=p.depth;depth++){
    const next=[];
    for(const circle of level){
      const key=pointKey([circle.x,circle.y,circle.r]);if(seen.has(key))continue;seen.add(key);yield circle;
      if(depth===p.depth)continue;
      for(let j=0;j<mirrors.length;j++)if(j!==circle.last){
        const c=invertCircle(circle,mirrors[j]);if(!c||c.r<p.cutoff)continue;
        next.push({...c,last:j,sector:j,depth:depth+1});
      }
      guard(seen.size+next.length,(seen.size+next.length)*24);
    }
    level=next;
  }
}
function* inversion(p,guard){
  const paths=[],count=p.inkCount||3;let batch=[],points=0;
  for(const c of inversionCircles(p,guard)){
    // Sagitta <= 0.0002 mathematical units, with a 16-segment floor.
    const n=Math.max(16,Math.ceil(Math.PI/Math.acos(Math.max(-1,1-.0002/c.r)))),pts=Array.from({length:n},(_,i)=>[c.x+c.r*Math.cos(tau*i/n),c.y+c.r*Math.sin(tau*i/n)]);pts.push(pts[0]);
    const path={role:(c.sector+c.depth)%count,points:pts};paths.push(path);batch.push(path);points+=pts.length;guard(paths.length,points);
    if(batch.length>=64){yield {fraction:c.depth/(p.depth+1),label:'Inverting circle generations',batch,paths:paths.length,points};batch=[];}
  }
  if(batch.length)yield {fraction:1,label:'Circle lace ready',batch,paths:paths.length,points};return paths;
}

// Cut-and-project Z^4 onto the physical plane, accepting points whose internal
// projection lies inside the centered regular octagon (projected unit 4-cube).
const root2=Math.SQRT2,half=1/root2;
export const projectOctagonal=n=>[n[0]+half*(n[1]-n[3]),n[2]+half*(n[1]+n[3])];
export const internalOctagonal=n=>[n[0]+half*(n[3]-n[1]),-n[2]+half*(n[1]+n[3])];
export function inOctagonalWindow(n){const [x,y]=internalOctagonal(n);return Math.max(Math.abs(x),Math.abs(y))<(1+root2)/2&&Math.abs(x)+Math.abs(y)<1+half;}
export function* octagonalTiles(p){
  const radius=3+3*p.depth,limit=Math.ceil((root2*(radius+2)+2)/2)+2,vertices=new Map(),a=(1+root2)/2;
  // Internal-window inequalities bound n0/n2 to at most three integers each.
  for(let n1=-limit;n1<=limit;n1++)for(let n3=-limit;n3<=limit;n3++){
    const center0=half*(n1-n3),center2=half*(n1+n3);
    for(let n0=Math.ceil(center0-a);n0<=Math.floor(center0+a);n0++)for(let n2=Math.ceil(center2-a);n2<=Math.floor(center2+a);n2++){
      const n=[n0,n1,n2,n3],pt=projectOctagonal(n);
      if(inOctagonalWindow(n)&&Math.max(Math.abs(pt[0]),Math.abs(pt[1]))<=radius+2)vertices.set(n.join(','),{n,pt});
    }
  }
  const inside=([x,y])=>p.patch===1?Math.hypot(x,y)<=radius:p.patch===2?Math.max(Math.abs(x),Math.abs(y))<=radius:Math.max(Math.abs(x),Math.abs(y),Math.abs(x+y)*half,Math.abs(x-y)*half)<=radius;
  const tiles=[];
  for(const {n,pt} of vertices.values())for(let i=0;i<4;i++)for(let j=i+1;j<4;j++){
    const ni=n.slice(),nj=n.slice(),nij=n.slice();ni[i]++;nj[j]++;nij[i]++;nij[j]++;
    const u=vertices.get(ni.join(',')),v=vertices.get(nij.join(',')),w=vertices.get(nj.join(','));if(!u||!v||!w)continue;
    const center=mix(pt,v.pt,.5);if(!inside(center))continue;
    tiles.push({points:[pt,u.pt,v.pt,w.pt],center,square:j-i===2,lattice:[n,ni,nij,nj]});
  }
  // A whole-tile crop can strand a square at a corner. Keep the central
  // edge-connected patch, using exact lattice identities rather than snapping.
  const incident=new Map(),neighbors=tiles.map(()=>[]);
  for(let i=0;i<tiles.length;i++)for(let j=0;j<4;j++){
    const key=[tiles[i].lattice[j].join(','),tiles[i].lattice[(j+1)%4].join(',')].sort().join('/');
    if(incident.has(key)){const other=incident.get(key);neighbors[i].push(other);neighbors[other].push(i);}else incident.set(key,i);
  }
  let seed=0;for(let i=1;i<tiles.length;i++)if(Math.hypot(...tiles[i].center)<Math.hypot(...tiles[seed].center))seed=i;
  const selected=new Set([seed]),queue=[seed];for(let i=0;i<queue.length;i++)for(const j of neighbors[queue[i]])if(!selected.has(j)){selected.add(j);queue.push(j);}
  for(let i=0;i<tiles.length;i++)if(selected.has(i))yield tiles[i];
}
function* ammann(p,guard){
  const paths=[],edges=[],seen=new Set();let tiles=0;
  for(const tile of octagonalTiles(p)){
    for(let i=0;i<4;i++){const a=tile.points[i],b=tile.points[(i+1)%4],key=edgeKey(a,b);if(!seen.has(key)){seen.add(key);edges.push({points:[a,b],role:0});}}
    for(let layer=1;layer<=p.insets;layer++){
      const pts=tile.points.map(pt=>mix(tile.center,pt,1-layer/(p.insets+1)));pts.push(pts[0]);paths.push({points:pts,role:tile.square?1:2});
    }
    if(++tiles%128===0){guard(edges.length+paths.length,edges.length*2+paths.length*5);yield {fraction:.5*(1-1/(1+tiles/128)),label:'Projecting octagonal tiles'};}
  }
  paths.unshift(...joinEdges(edges));return yield* batches(paths,.5,'Joining tile edges and motifs',guard);
}

// Finite 2D space colonization. Attractors steer their nearest existing node;
// neighborhoods use the shared quadtree, and new edges retain their real parent.
export function growthTargets(p){
  const random=randomFrom(p.seed),points=[],count=400*p.depth;
  for(let i=0;i<count;i++){
    const angle=tau*random(),r=Math.sqrt(random()),x=Math.cos(angle)*r,y=Math.sin(angle)*r;
    const [w,h,cy]=[[85,100,140],[42,115,150],[115,80,95]][p.crown];
    points.push({x:x*w*p.spread,y:cy+y*h,id:i});
  }return points;
}
export function* colonize(p,guard=()=>{}){
  const targets=growthTargets(p),index=new ParticleIndex(),nodes=[],step=3,kill=4.5,influence=65;
  const add=(x,y,parent)=>{const n={x,y,parent,id:nodes.length};nodes.push(n);index.insert(n);return n;};
  const trunk=p.crown===2?18:54;for(let y=0;y<=trunk;y+=step)add(0,y,nodes.length-1);
  let active=targets,iterations=0;
  for(;iterations<240&&active.length;iterations++){
    const sums=new Map(),remaining=[];
    for(const target of active){
      const {point:nearest,distance}=index.nearest(target.x,target.y);if(distance<kill)continue;remaining.push(target);if(distance>influence)continue;
      if(!sums.has(nearest.id))sums.set(nearest.id,{x:0,y:0,n:0});const s=sums.get(nearest.id);s.x+=(target.x-nearest.x)/distance;s.y+=(target.y-nearest.y)/distance;s.n++;
    }
    let added=0;for(const [parent,s] of [...sums].sort((a,b)=>a[0]-b[0])){
      const dx=s.x/s.n+p.bias,dy=s.y/s.n+.04,length=Math.hypot(dx,dy);if(length<1e-10)continue;
      const at=nodes[parent],x=at.x+step*dx/length,y=at.y+step*dy/length;
      if(index.nearest(x,y).distance<step*.55)continue;add(x,y,parent);added++;
    }
    guard(nodes.length,2*nodes.length);active=remaining;yield {fraction:.75*(iterations+1)/240,label:`Growing branches · ${targets.length-active.length} / ${targets.length} targets reached`};
    if(!added)break;
  }
  return {nodes,targets,remaining:active.length,iterations:iterations+1};
}
function* colonization(p,guard){
  guard(400*p.depth*20,400*p.depth*40);const {nodes}=yield* colonize(p,guard),count=p.inkCount||3,children=Array.from({length:nodes.length},()=>[]),roles=new Int32Array(nodes.length);let nextRole=0;
  for(const n of nodes)if(n.parent>=0)children[n.parent].push(n.id);
  for(const n of nodes)children[n.id].forEach((id,i)=>roles[id]=i?++nextRole%count:roles[n.id]);
  const edges=nodes.filter(n=>n.parent>=0).map(n=>({role:roles[n.id],points:[[nodes[n.parent].x,-nodes[n.parent].y],[n.x,-n.y]]}));
  return yield* batches(joinEdges(edges),.8,'Joining organic branches',guard);
}

// Stacked, disjoint roof footprints. Each child grows upward from its parent's
// roof; the two presets use symmetric five-tower / irregular four-tower layouts.
export function architectureBoxes(p){
  const random=randomFrom(p.seed),boxes=[],root={x:0,y:0,z:0,size:1,level:0,parent:-1},queue=[root];
  for(let i=0;i<queue.length;i++){
    const b=queue[i],height=b.size*.32*p.height*(p.form?(.6+.8*random()):(b.central?1.4:1))*(.94+.12*random());
    const box={...b,height,id:boxes.length};boxes.push(box);if(b.level>=p.depth)continue;
    const factor=p.form?(1-p.setback)/2:(1-p.setback)/3,shift=p.form?.25:1/3;
    const centers=[[-shift,-shift],[-shift,shift],[shift,-shift],[shift,shift]];if(!p.form)centers.push([0,0]);
    for(const [x,y] of centers)queue.push({x:b.x+x*b.size,y:b.y+y*b.size,z:b.z+height,size:b.size*factor,level:b.level+1,parent:box.id,central:!x&&!y});
  }return boxes;
}
export const boxCorners=b=>Array.from({length:8},(_,i)=>[b.x+((i&1)?1:-1)*b.size/2,b.y+((i&2)?1:-1)*b.size/2,b.z+((i&4)?b.height:0)]);
export const boxFaces=[[0,1,3,2],[4,6,7,5],[0,4,5,1],[2,3,7,6],[0,2,6,4],[1,5,7,3]];
export function* architectureEdges(p){
  const boxes=architectureBoxes(p),cells=boxes.map(b=>boxCorners(b).map(pt=>project3(pt,p.azimuth,p.elevation))),triangles=[],grid=new Map(),bins=32,size=2/bins,edges=[],seen=new Set();let comparisons=0;
  const keys=pts=>{const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]),keys=[];for(let x=Math.floor(Math.min(...xs)/size);x<=Math.floor(Math.max(...xs)/size);x++)for(let y=Math.floor(Math.min(...ys)/size);y<=Math.floor(Math.max(...ys)/size);y++)keys.push(x+','+y);return keys;};
  for(const cell of cells)for(const ids of boxFaces)for(const order of [[0,1,2],[0,2,3]]){
    const tri=order.map(i=>cell[ids[i]]),id=triangles.length;triangles.push(tri);for(const k of keys(tri)){if(!grid.has(k))grid.set(k,[]);grid.get(k).push(id);}
  }
  for(let i=0;i<cells.length;i++){
    const cell=cells[i];for(let u=0;u<8;u++)for(let axis=0;axis<3;axis++){
      const v=u^(1<<axis);if(u>v)continue;const a=cell[u],b=cell[v],cuts=[];
      for(const id of new Set(keys([a,b]).flatMap(k=>grid.get(k)||[]))){comparisons++;const interval=hiddenInterval(a,b,triangles[id]);if(interval)cuts.push(interval);}
      cuts.sort((a,b)=>a[0]-b[0]);let start=0;
      const append=(lo,hi)=>{if(hi-lo<1e-8)return;const points=[mix(a,b,lo),mix(a,b,hi)],key=edgeKey(...points);if(seen.has(key))return;seen.add(key);if(Math.hypot(points[1][0]-points[0][0],points[1][1]-points[0][1])>1e-7)edges.push({points,role:axis,box:i,source:[u,v],range:[lo,hi]});};
      for(const [lo,hi] of cuts){if(lo>start)append(start,lo);start=Math.max(start,hi);}if(start<1)append(start,1);
    }
    if(i%32===0)yield {fraction:.8*i/cells.length,label:'Removing hidden architecture edges'};
  }
  return {edges,boxes,triangles,comparisons};
}
function* architecture(p,guard){
  const base=p.form?4:5,total=(base**(p.depth+1)-1)/(base-1);guard(12*total,40*total);
  const {edges}=yield* architectureEdges(p);return yield* batches(joinEdges(edges.map(e=>({role:e.role,points:e.points.map(pt=>pt.slice(0,2))}))),.8,'Joining visible architecture edges',guard);
}
export const ornamentGenerators={inversion,ammann,colonization,architecture};
