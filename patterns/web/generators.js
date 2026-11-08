// Original, bounded path generators. Coordinates are mathematical units until
// fitPaths; the preview and PDF consume those same fitted centerlines.
import {collectionFamilies,collectionGenerators} from './collection.js';
import {solidFrame} from './hatched-solids.js';
import {textileFamilies,textileGenerators,starFrame,guillocheFrame} from './textile.js';
import {ornamentFamilies,ornamentGenerators} from './ornament.js';
import {recursiveFamilies,recursiveGenerators} from './recursive-art.js';
import {subdivisionFamilies,subdivisionGenerators} from './subdivision.js';
import {densityFamilies,densityGenerators} from './density.js';
import {spatialFamilies,spatialGenerators} from './spatial.js';
import {adjacentFamilies,adjacentGenerators} from './adjacent.js';
import {naturalFamilies,naturalGenerators,knotFrame} from './natural.js';
import {complexFamilies,complexContours,isComplex,validateComplex,complexFrame,newtonParams} from './complex.js';
export {juliaShapes} from './complex.js';
// Firefox rejects JSON module imports in workers under nosniff. Load the same
// configuration as data, without weakening the server's response headers.
export const limits={};
let limitsPromise;
export function loadLimits(){
  return limitsPromise??=(async()=>{
    const url=new URL('./limits.json',import.meta.url);
    const data=url.protocol==='file:'
      ?JSON.parse(await (await import('node:fs/promises')).readFile(url,'utf8'))
      :await fetch(url).then(response=>{if(!response.ok)throw Error('Cannot load pattern limits. Reload to retry.');return response.json();});
    Object.assign(limits,data);Object.freeze(limits);
    return limits;
  })();
}
export const VERSION=12;
// Estimated peak working storage includes path objects, temporary geometry,
// transfer/fitting copies and export structures. It is not a heap measurement.
export const geometryBytes=(paths,points)=>paths*limits.bytes_per_path+points*limits.bytes_per_point;
export function checkGeometrySize(paths,points){
  if(!limits.working_memory_bytes)throw Error('Pattern limits have not loaded.');
  const bytes=geometryBytes(paths,points);
  if(bytes>limits.working_memory_bytes)throw Error(`This drawing needs about ${Math.ceil(bytes/1048576)} MiB of working memory (${paths.toLocaleString()} paths, ${points.toLocaleString()} points). The per-drawing memory allowance is ${limits.working_memory_bytes/1048576} MiB. Reduce detail or copies.`);
  return bytes;
}
const knob=(key,label,value,min,max,step=1)=>({key,label,value,min,max,step});
export const families=[
  {id:'koch',name:'Koch snowflake',roles:['Outer folds','Middle folds','Inner folds'],knobs:[knob('depth','Detail',4,2,7),knob('layers','Nested snowflakes',8,3,13),knob('angle','Peak angle',60,40,75,5),knob('twist','Twist per layer',8,-20,20,4)]},
  {id:'dragon',name:'Dragon curve',roles:['First fold','Second fold','Third fold'],knobs:[knob('depth','Folds',12,8,17),knob('angle','Fold angle',90,75,105,3),knob('copies','Rotated copies',3,1,5)]},
  {id:'hilbert',name:'Hilbert curve',roles:['First quadrant','Second quadrant','Third quadrant','Fourth quadrant'],knobs:[knob('depth','Detail',6,3,9),knob('round','Corner rounding',.25,0,.45,.05)]},
  {id:'plant',name:'Branching plant',roles:['Stem','Branches','Leaves'],knobs:[knob('depth','Branch generations',8,5,10),knob('angle','Branch spread',28,16,44,4),knob('bend','Branch curl',10,-10,25,5),knob('seed','Variation',7,1,30)]},
  {id:'sierpinski',name:'Sierpiński triangle',roles:['Large openings','Medium openings','Small openings'],knobs:[knob('depth','Detail',6,3,8),knob('height','Triangle height',.9,.5,1.3,.1)]},
  {id:'apollonian',name:'Apollonian circles',roles:['Large circles','Medium circles','Small circles'],knobs:[knob('depth','Generations',8,4,11),knob('balance','Balance',.47,.23,.77,.03),knob('cutoff','Smallest circle',.006,.002,.02,.002)]},
  {id:'julia',name:'Julia contours',roles:['Outer contours','Middle contours','Inner contours'],knobs:[knob('shape','Julia shape',3,0,8),knob('contours','Contour count',12,6,20,2),knob('detail','Resolution',520,280,760,80)]},
  {id:'menger',name:'Menger sponge',roles:['X edges','Y edges','Z edges'],knobs:[knob('depth','Detail',2,1,5),knob('azimuth','Azimuth',35,5,85,10),knob('elevation','Elevation',25,5,75,10)]},
  ...adjacentFamilies,
  ...complexFamilies,
  ...naturalFamilies,
  ...spatialFamilies,
  ...subdivisionFamilies,
  ...densityFamilies,
  ...recursiveFamilies,
  ...ornamentFamilies,
  ...textileFamilies,
  ...collectionFamilies,
];
export const familyFor=id=>families.find(f=>f.id===id);
export const refinementKey=id=>['lorenz','rossler'].includes(id)?'duration':id==='woven'?'strands':id==='dla'?'particles':id==='nested_polygons'?'layers':id==='guilloche'?'lines':id==='reaction'?'time':id==='flowers'?'whorls':id==='hatched_solids'?'density':'depth';
export const usesPhysicalWidths=id=>['newton','woven','starweave','hatched_solids'].includes(id);
export function physicalOptions(recipe){
  if(!usesPhysicalWidths(recipe.family))return {};
  const frame=recipe.family==='hatched_solids'?solidFrame(recipe.params):recipe.family==='woven'?knotFrame(recipe.params):recipe.family==='starweave'?starFrame(recipe.params):complexFrame,{scale}=pageTransform({paths:[],frame},recipe);
  return {inkWidths:recipe.roles.map(role=>(recipe.inks.find(i=>i.id===role.ink_id).width_mm??.3)/scale)};
}
export const usesInkSequence=id=>['koch','dragon','hilbert','sierpinski','julia'].includes(id)||Boolean(familyFor(id)?.sequence);
export const defaults=id=>Object.fromEntries(familyFor(id).knobs.map(k=>[k.key,k.value]));
const radians=d=>d*Math.PI/180;
const path=(points,role=0)=>({points,role});
const rotate=([x,y],a)=>[x*Math.cos(a)-y*Math.sin(a),x*Math.sin(a)+y*Math.cos(a)];
const midpoint=(a,b)=>a.map((v,i)=>(v+b[i])/2);
function circle(x,y,r,role){const n=Math.max(16,Math.min(240,Math.ceil(180*Math.sqrt(r))));return path(Array.from({length:n+1},(_,i)=>[x+r*Math.cos(i/n*2*Math.PI),y+r*Math.sin(i/n*2*Math.PI)]),role);}
function koch(p){
  let pts=Array.from({length:4},(_,i)=>[Math.sin(i*2*Math.PI/3),-Math.cos(i*2*Math.PI/3)]);
  for(let d=0;d<p.depth;d++){
    const next=[];
    for(let i=0;i<pts.length-1;i++){
      const [x,y]=pts[i],[u,v]=pts[i+1],dx=u-x,dy=v-y,h=Math.tan(radians(p.angle))/6;
      next.push([x,y],[x+dx/3,y+dy/3],[x+dx/2+dy*h,y+dy/2-dx*h],[x+dx*2/3,y+dy*2/3]);
    }
    next.push(pts.at(-1));pts=next;
  }
  return Array.from({length:p.layers},(_,j)=>path(pts.map(pt=>rotate(pt.map(v=>v*.82**j),radians(p.twist*j))),j%(p.inkCount||3)));
}
function dragon(p){
  let pts=[[0,0],[1,0]];
  for(let d=0;d<p.depth;d++){
    const end=pts.at(-1),tail=[];
    for(let i=pts.length-2;i>=0;i--){const r=rotate(pts[i].map((v,j)=>v-end[j]),radians(p.angle));tail.push(r.map((v,j)=>v+end[j]));}
    for(const pt of tail)pts.push(pt);
  }
  const result=[];
  for(let c=0;c<p.copies;c++){
    const rotated=pts.map(pt=>rotate(pt,c*Math.PI*2/p.copies));
    const count=p.inkCount||3,size=Math.ceil((pts.length-1)/count);
    for(let i=0;i<count;i++){const section=rotated.slice(i*size,Math.min(rotated.length,(i+1)*size+1));if(section.length>1)result.push(path(section,(i+c)%count));}
  }
  return result;
}
function hilbert(p){
  const n=2**p.depth,pts=[];
  for(let i=0;i<n*n;i++){
    let t=i,x=0,y=0;
    for(let s=1;s<n;s*=2){const rx=1&(t>>1),ry=1&(t^rx);if(!ry){if(rx){x=s-1-x;y=s-1-y;}[x,y]=[y,x];}x+=s*rx;y+=s*ry;t>>=2;}
    pts.push([x,y]);
  }
  const rounded=[pts[0]];
  for(let i=1;i<pts.length-1;i++){
    const a=pts[i-1],b=pts[i],c=pts[i+1],start=b.map((v,j)=>v+(a[j]-v)*p.round),end=b.map((v,j)=>v+(c[j]-v)*p.round);
    rounded.push(start);
    for(let s=1;s<=3;s++){const t=s/3;rounded.push(b.map((v,j)=>(1-t)**2*start[j]+2*(1-t)*t*v+t*t*end[j]));}
  }
  rounded.push(pts.at(-1));
  const count=p.inkCount||4,size=Math.ceil((rounded.length-1)/count);
  return Array.from({length:count},(_,i)=>path(rounded.slice(i*size,Math.min(rounded.length,(i+1)*size+1)),i));
}
function plant(p){
  let seed=p.seed|0;const random=()=>{seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;return (seed>>>0)/4294967296;};
  const paths=[];
  function grow(start,angle,length,depth){
    const bend=radians(p.bend)*(random()-.5),pts=[start];
    for(let i=1;i<=4;i++){const t=i/4;pts.push([start[0]+Math.cos(angle+bend*t)*length*t,start[1]+Math.sin(angle+bend*t)*length*t]);}
    paths.push(path(pts,depth>p.depth-3?0:1));
    const end=pts.at(-1);
    if(depth===0){
      const leaf=[];for(let i=0;i<=20;i++){const t=i/20*2*Math.PI,pt=rotate([Math.cos(t)*length*.65,Math.sin(t)*length*.24],angle);leaf.push(pt.map((v,j)=>v+end[j]));}paths.push(path(leaf,2));return;
    }
    for(const sign of [-1,1])grow(end,angle+bend+sign*radians(p.angle)*( .8+random()*.4),length*(.69+random()*.08),depth-1);
  }
  grow([0,0],-Math.PI/2,1,p.depth);return paths;
}
function sierpinski(p){
  const a=[-1,p.height],b=[1,p.height],c=[0,-p.height],result=[path([a,b,c,a],0)];
  function holes(a,b,c,d){if(!d)return;const ab=midpoint(a,b),bc=midpoint(b,c),ca=midpoint(c,a);result.push(path([ab,bc,ca,ab],(p.depth-d)%(p.inkCount||3)));holes(a,ab,ca,d-1);holes(ab,b,bc,d-1);holes(ca,bc,c,d-1);}
  holes(a,b,c,p.depth);return result;
}
function apollonian(p){
  const r=p.balance,k=-1+1/r+1/(1-r);
  const seed=[{k:-1,x:0,y:0},{k:1/r,x:r-1,y:0},{k:1/(1-r),x:r,y:0},{k,x:(r/(1-r)-(1-r)/r)/k,y:2/k}];
  const found=new Map(),queue=[{circles:seed,depth:0,last:-1}];
  const add=c=>{const key=[c.k,c.x,c.y].map(v=>v.toFixed(7)).join('/');if(found.has(key))return false;found.set(key,c);return true;};
  seed.forEach(add);
  for(let i=0;i<queue.length;i++){
    const {circles,depth,last}=queue[i];if(depth>=p.depth)continue;
    for(let j=0;j<4;j++){
      if(j===last)continue;const other=circles.filter((_,n)=>n!==j),old=circles[j],k=2*other.reduce((s,c)=>s+c.k,0)-old.k;
      if(k<=0||1/k<p.cutoff)continue;
      const c={k,x:(2*other.reduce((s,c)=>s+c.k*c.x,0)-old.k*old.x)/k,y:(2*other.reduce((s,c)=>s+c.k*c.y,0)-old.k*old.y)/k};
      if(!add(c))continue;const next=circles.slice();next[j]=c;queue.push({circles:next,depth:depth+1,last:j});
    }
  }
  return [...found.values()].map(c=>circle(c.x,c.y,Math.abs(1/c.k),c.k<8?0:c.k<35?1:2));
}
export function mengerOccupied(x,y,z,n){
  if(x<0||y<0||z<0||x>=n||y>=n||z>=n)return false;
  while(n>1){if(Number(x%3===1)+Number(y%3===1)+Number(z%3===1)>=2)return false;x=Math.floor(x/3);y=Math.floor(y/3);z=Math.floor(z/3);n/=3;}return true;
}
// Grid ray traversal is O(grid width), not a test against every cube. A slight
// camera-directed offset distinguishes a surface point from its owning solid.
export function mengerVisible(point,view,n){
  const p=point.map((v,i)=>v+view[i]*1e-6),cell=p.map(Math.floor),step=view.map(v=>v>0?1:-1);
  const delta=view.map(v=>Math.abs(1/v)),next=p.map((v,i)=>Math.abs(((step[i]>0?cell[i]+1:cell[i])-v)/view[i]));
  for(let count=0;count<3*n+6;count++){
    if(cell.some(v=>v<0||v>=n))return true;
    if(mengerOccupied(...cell,n))return false;
    const t=Math.min(...next);for(let i=0;i<3;i++)if(next[i]<=t+1e-9){cell[i]+=step[i];next[i]+=delta[i];}
  }return true;
}
function* menger(p){
  const n=3**p.depth,az=radians(p.azimuth),el=radians(p.elevation),view=[Math.cos(el)*Math.cos(az),Math.cos(el)*Math.sin(az),Math.sin(el)];
  const right=[-Math.sin(az),Math.cos(az),0],up=[-Math.sin(el)*Math.cos(az),-Math.sin(el)*Math.sin(az),Math.cos(el)];
  const project=pt=>[pt.reduce((s,v,i)=>s+(v/n-.5)*right[i],0),-pt.reduce((s,v,i)=>s+(v/n-.5)*up[i],0)],output=[];
  let sent=0;
  // Interleave edge directions so each preview increment reveals a spatial
  // portion of the drawing, rather than one entire edge color at a time.
  for(let a=0;a<n;a++)for(let axis=0;axis<3;axis++){
    const u=(axis+1)%3,v=(axis+2)%3;
      for(let b=0;b<=n;b++)for(let c=0;c<=n;c++){
      const start=[0,0,0];start[axis]=a;start[u]=b;start[v]=c;
      const around=[[0,0],[-1,0],[-1,-1],[0,-1]].map(([du,dv])=>{const cell=start.slice();cell[u]+=du;cell[v]+=dv;return mengerOccupied(...cell,n);});
      const count=around.filter(Boolean).length;
      if(count===0||count===4||(count===2&&around[0]!==around[2]))continue; // Coplanar seams are not edges.
      const samples= Math.ceil(700/n),at=t=>start.map((v,i)=>v+(i===axis?t:0));let run=null;
      for(let j=0;j<samples;j++){
        const visible=mengerVisible(at((j+.5)/samples),view,n);
        if(visible&&run===null)run=j/samples;
        if(run!==null&&(!visible||j===samples-1)){const end=visible?1:j/samples;if(end-run>1e-7)output.push(path([project(at(run)),project(at(end))],axis));run=null;}
      }
    }
    const batch=output.slice(sent);sent=output.length;checkGeometrySize(sent,sent*2);
    yield {fraction:(a+(axis+1)/3)/n,label:`Removing hidden sponge edges · detail ${p.depth}`,batch,paths:sent,points:sent*2};
  }
  return output;
}
export function validatePatternParameters(family,params){
  if(family==='newton')params=newtonParams(params);
  const definition=familyFor(family);if(!definition)throw Error('Unknown pattern family');
  const p={};for(const k of definition.knobs){const v=Number(params[k.key]);if(!Number.isFinite(v)||v<k.min-1e-8||v>k.max+1e-8||Math.abs((v-k.min)/k.step-Math.round((v-k.min)/k.step))>1e-6)throw Error('Invalid '+k.label);p[k.key]=v;}
  if(isComplex(family)){validateComplex(params);if(params.viewport)p.viewport={...params.viewport};if(family==='julia'&&params.c)p.c=[...params.c];}
  return p;
}
export function* generateSteps(family,params,options={}){
  const p=validatePatternParameters(family,params),definition=familyFor(family);
  if(options.inkCount!==undefined){if(!Number.isInteger(options.inkCount)||options.inkCount<1||options.inkCount>64)throw Error('Choose 1–64 inks');p.inkCount=options.inkCount;}
  if(options.resolution!==undefined){if(!Number.isInteger(options.resolution)||options.resolution<128||options.resolution>3072)throw Error('Invalid sampling resolution');p.resolution=options.resolution;}
  if(usesPhysicalWidths(family)&&options.inkWidths!==undefined){if(!Array.isArray(options.inkWidths)||options.inkWidths.length!==(family==='newton'?p.roots:family==='hatched_solids'?3:(p.inkCount||3))||options.inkWidths.some(w=>!Number.isFinite(w)||w<=0||w>2))throw Error('Invalid '+(family==='newton'?'Newton':definition.name)+' ink widths');p.inkWidths=options.inkWidths;}
  // Refuse allocations that can be predicted before recursive expansion.
  if(family==='koch')checkGeometrySize(p.layers,p.layers*(3*4**p.depth+1));
  if(family==='dragon')checkGeometrySize(p.copies*(p.inkCount||3),p.copies*(2**p.depth+1+(p.inkCount||3)));
  if(family==='hilbert')checkGeometrySize(p.inkCount||4,4*4**p.depth);
  const paths=collectionGenerators[family]?yield* collectionGenerators[family](p,checkGeometrySize):textileGenerators[family]?yield* textileGenerators[family](p,checkGeometrySize):ornamentGenerators[family]?yield* ornamentGenerators[family](p,checkGeometrySize):recursiveGenerators[family]?yield* recursiveGenerators[family](p,checkGeometrySize):subdivisionGenerators[family]?yield* subdivisionGenerators[family](p,checkGeometrySize):densityGenerators[family]?yield* densityGenerators[family](p,checkGeometrySize):spatialGenerators[family]?yield* spatialGenerators[family](p,checkGeometrySize):naturalGenerators[family]?yield* naturalGenerators[family](p,checkGeometrySize):adjacentGenerators[family]?yield* adjacentGenerators[family](p,checkGeometrySize):family==='menger'?yield* menger(p):isComplex(family)?yield* complexContours(family,p,checkGeometrySize):({koch,dragon,hilbert,plant,sierpinski,apollonian})[family](p);
  const points=paths.reduce((n,p)=>n+p.points.length,0);
  if(!paths.length&&!isComplex(family))throw Error('These settings produced no strokes.');
  checkGeometrySize(paths.length,points);
  return {version:VERSION,paths,points,...(p.inkStats?{ink_stats:p.inkStats}:{}),...(family==='hatched_solids'?{frame:solidFrame(p)}:family==='starweave'?{frame:starFrame(p)}:family==='guilloche'?{frame:guillocheFrame(p)}:['wave_contours','reaction','lsystem'].includes(family)?{frame:[-1,-1,2,2]}:family==='woven'?{frame:knotFrame(p)}:isComplex(family)&&(family!=='julia'||p.viewport||!paths.length)?{frame:complexFrame}:{})};
}
export function generate(family,params,progress=()=>{},options={}){
  const steps=generateSteps(family,params,options);let step;
  while(!(step=steps.next()).done)progress(step.value.fraction,step.value.label);
  progress(1,'Paths ready');return step.value;
}
export function pageTransform(geometry,recipe){
  const angle=radians(recipe.rotation||0),cos=Math.cos(angle),sin=Math.sin(angle);
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  const frame=geometry.frame;
  const corners=frame?[[frame[0],frame[1]],[frame[0]+frame[2],frame[1]],[frame[0]+frame[2],frame[1]+frame[3]],[frame[0],frame[1]+frame[3]]]:null;
  for(const p of corners?[{points:corners}]:geometry.paths)for(const [a,b] of p.points){const x=a*cos-b*sin,y=a*sin+b*cos;minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y);}
  const [x,y,w,h]=recipe.media.printable_rect_mm;
  const pen=Math.max(...recipe.inks.map(i=>i.width_mm??.5)),inset=Math.max(recipe.media.content_inset_mm||0,1)+pen/2;
  const width=w-2*inset,height=h-2*inset;if(width<=0||height<=0)throw Error('Paper is smaller than its margins and pen clearance.');
  const scale=Math.min(width/(maxX-minX||1),height/(maxY-minY||1))*(recipe.size??.96);
  const ox=x+w/2-(minX+maxX)/2*scale,oy=y+h/2-(minY+maxY)/2*scale;
  return {scale,ox,oy,cos,sin,bounds:[minX*scale+ox,minY*scale+oy,(maxX-minX)*scale,(maxY-minY)*scale]};
}
export function fitPaths(geometry,recipe){
  const {scale,ox,oy,cos,sin,bounds}=pageTransform(geometry,recipe);let length=0,count=0;
  const paths=[];
  for(const p of geometry.paths){
    const points=[];
    for(const [a,b] of p.points){
      const pt=[Number(((a*cos-b*sin)*scale+ox).toFixed(5)),Number(((a*sin+b*cos)*scale+oy).toFixed(5))],previous=points.at(-1);
      // Grid vertices can coincide at a contour level. Coalesce steps below
      // 0.1 micrometre before preview/export; PDF float coordinates cannot
      // reliably retain these as distinct segments on large sheets either.
      if(!previous||Math.hypot(pt[0]-previous[0],pt[1]-previous[1])>=.0001)points.push(pt);
    }
    if(points.length<2)continue;
    for(let i=1;i<points.length;i++)length+=Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1]);
    paths.push({...p,points});count+=points.length;
  }
  return {paths,points:count,scale,length_mm:length,bounds};
}

// Physical sampling target, not a claim of exact fractal-boundary accuracy.
// A cell's diagonal should fit in half the narrowest pen. The worker first
// measures the current shape's extent, then computes this in final paper mm.
export function fieldSampling(geometry,recipe){
  const fitted=pageTransform(geometry,recipe),widths=recipe.inks.map(i=>i.width_mm).filter(w=>w>0);
  const target=Math.min(...(widths.length?widths:[.3]))/2;
  const required=Math.ceil(2*fitted.scale*Math.SQRT2/target),resolution=Math.max(280,Math.min(3072,Math.ceil(required/32)*32));
  return {resolution,target_mm:target,cell_mm:2*fitted.scale*Math.SQRT2/resolution,limited:required>3072};
}
