// Original constructions using recursive walks, convex interpolation, square
// branching and seeded midpoint displacement. All output is actual pen paths.
import {roundCurve} from './subdivision.js';
import {joinEdges} from './spatial.js';
const knob=(key,label,value,min,max,step=1)=>({key,label,value,min,max,step});
const sequence={sequence:true,roles:['First sections','Second sections','Third sections']};
export const recursiveFamilies=[
  {id:'peano',name:'Peano curve',...sequence,knobs:[knob('depth','Detail',4,2,5),knob('round','Corner rounding',.2,0,.4,.1)]},
  {id:'nested_polygons',name:'Nested polygons',...sequence,knobs:[knob('sides','Sides',5,3,10),knob('layers','Layer limit',50,10,100,10),knob('step','Edge step (%)',10,5,45,5)]},
  {id:'pythagoras',name:'Pythagoras tree',...sequence,knobs:[knob('depth','Detail',9,3,12),knob('angle','Branch angle',45,20,70,5)]},
  {id:'lightning',name:'Branching lightning',...sequence,knobs:[knob('depth','Detail',8,4,11),knob('forks','Forks',18,6,42,6),knob('roughness','Jaggedness',.16,.08,.32,.04),knob('seed','Variation',7,1,30)]},
];
const mix=(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t),distance=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1]);
const randomFrom=seed=>()=>{seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;return (seed>>>0)/4294967296;};
function* batches(paths,label,guard,start=0){
  let points=0;for(let i=0;i<paths.length;i+=64){const batch=paths.slice(i,i+64),sent=i+batch.length;points+=batch.reduce((s,p)=>s+p.points.length,0);guard(sent,points);yield {fraction:start+(1-start)*sent/paths.length,label,batch,paths:sent,points};}return paths;
}

// Peano's ternary traversal, streamed from its two replacement rules. F advances
// one cell; X and Y only expand. All 9^depth cell centers are visited once.
export function* peanoWalk(depth){
  const rules={X:'XFYFX+F+YFXFY-F-XFYFX',Y:'YFXFY-F-XFYFX+F+YFXFY'},directions=[[1,0],[0,1],[-1,0],[0,-1]];
  let x=0,y=0,heading=0;yield [x,y];
  function* walk(symbol,d){
    if(!d)return;
    for(const command of rules[symbol]){
      if(command==='F'){const [dx,dy]=directions[heading];x+=dx;y+=dy;yield [x,y];}
      else if(command==='+')heading=(heading+1)%4;else if(command==='-')heading=(heading+3)%4;else yield* walk(command,d-1);
    }
  }
  yield* walk('X',depth);
}
function* peano(p,guard){
  const count=p.inkCount||3,n=9**p.depth,points=[];guard(count,4*n+count);
  for(const pt of peanoWalk(p.depth)){points.push(pt);if(points.length%4096===0)yield {fraction:.6*points.length/n,label:'Folding the Peano walk'};}
  const rounded=roundCurve(points,p.round),section=Math.ceil((rounded.length-1)/count),paths=[];let total=0;
  for(let role=0;role<count;role++){
    const pts=rounded.slice(role*section,Math.min(rounded.length,(role+1)*section+1));if(pts.length<2)continue;
    const path={points:pts,role};paths.push(path);total+=pts.length;
    yield {fraction:.6+.4*(role+1)/count,label:'Joining Peano sections',batch:[path],paths:paths.length,points:total};
  }
  return paths;
}

export function* polygonNests(p){
  let polygon=Array.from({length:p.sides},(_,i)=>[Math.sin(i*2*Math.PI/p.sides),-Math.cos(i*2*Math.PI/p.sides)]);
  for(let layer=0;layer<p.layers;layer++){
    if(distance(polygon[0],polygon[1])<1e-5)break;
    yield polygon;polygon=polygon.map((pt,i)=>mix(pt,polygon[(i+1)%polygon.length],p.step/100));
  }
}
function* nested_polygons(p,guard){
  guard(p.layers,p.layers*(p.sides+1));const paths=[...polygonNests(p)].map((polygon,i)=>({points:[...polygon,polygon[0]],role:i%(p.inkCount||3)}));
  // Small batches make the complete drawing useful during refinement too.
  let points=0;for(let i=0;i<paths.length;i++){points+=paths[i].points.length;yield {fraction:(i+1)/paths.length,label:'Nesting polygon edges',batch:[paths[i]],paths:i+1,points};}return paths;
}

export function squareBranch(a,b,angle){
  const dx=b[0]-a[0],dy=b[1]-a[1],up=[-dy,dx],left=[a[0]+up[0],a[1]+up[1]],right=[b[0]+up[0],b[1]+up[1]],t=angle*Math.PI/180,c=Math.cos(t),s=Math.sin(t);
  const apex=[left[0]+dx*c*c+up[0]*c*s,left[1]+dy*c*c+up[1]*c*s];
  return {polygon:[a,b,right,left,a],children:[[left,apex],[apex,right]]};
}
export function* squareTree(p){
  let cells=[{a:[-.5,0],b:[.5,0]}];
  for(let depth=0;depth<=p.depth;depth++){
    const next=[];
    for(const cell of cells){const square=squareBranch(cell.a,cell.b,p.angle);yield {...square,depth};for(const [a,b] of square.children)next.push({a,b});}
    cells=next;
  }
}
function* pythagoras(p,guard){
  const total=2**(p.depth+1)-1,count=p.inkCount||3,edges=[],seen=new Set();guard(4*total,12*total);let done=0;
  for(const square of squareTree(p)){
    for(let i=1;i<square.polygon.length;i++){
      const points=[square.polygon[i-1],square.polygon[i]].map(([x,y])=>[x,-y]),key=points.map(pt=>pt.map(v=>Math.round(v*1e10)).join(',')).sort().join('/');
      // Symmetric higher levels can produce coincident edges. Emit them once,
      // keeping the first generation's ink, rather than darkening with retraces.
      if(!seen.has(key)){seen.add(key);edges.push({points,role:square.depth%count});}
    }
    if(++done%128===0)yield {fraction:.8*done/total,label:'Building branching squares'};
  }
  return yield* batches(joinEdges(edges),'Joining square outlines',guard,.8);
}

// Breadth-first subdivision keeps every completed coarser polyline vertex.
// Each displacement is perpendicular to its segment and bounded by its length.
export function displacedLine(a,b,depth,roughness,seed){
  const random=randomFrom(seed);let points=[a,b];
  for(let d=0;d<depth;d++){
    const next=[];for(let i=1;i<points.length;i++){
      const a=points[i-1],b=points[i],offset=(random()*2-1)*roughness;
      next.push(a,[(a[0]+b[0])/2-(b[1]-a[1])*offset,(a[1]+b[1])/2+(b[0]-a[0])*offset]);
    }next.push(b);points=next;
  }
  return points;
}
export function* lightningBranches(p){
  const random=randomFrom(p.seed),spine={points:displacedLine([0,0],[0,1],p.depth,p.roughness,p.seed),length:1,parent:-1},branches=[spine];yield spine;
  for(let i=0;i<p.forks;i++){
    const candidates=branches.map((b,index)=>({...b,index})).filter(b=>b.length>.12),weight=candidates.reduce((s,b)=>s+b.length,0);let pick=random()*weight,parent=candidates.at(-1);
    for(const candidate of candidates){pick-=candidate.length;if(pick<=0){parent=candidate;break;}}
    const index=Math.max(1,Math.min(parent.points.length-2,Math.floor((.12+.65*random())*(parent.points.length-1)))),origin=parent.points[index],remaining=distance(origin,parent.points.at(-1)),length=remaining*(.35+.25*random()),side=random()<.5?-1:1;
    const end=[origin[0]+side*length*(.7+.4*random()),origin[1]+length*.55],depth=Math.max(2,p.depth-Math.max(0,Math.ceil(Math.log2(1/Math.max(length,.001))))),seed=((p.seed*2654435761)^(i+1)*2246822519)>>>0;
    const branch={points:displacedLine(origin,end,depth,p.roughness,seed||1),length,parent:parent.index,attachment:index};branches.push(branch);yield branch;
  }
}
function* lightning(p,guard){
  guard(p.forks+1,(p.forks+1)*(2**p.depth+1));const paths=[];let points=0;
  for(const branch of lightningBranches(p)){
    const path={points:branch.points,role:paths.length%(p.inkCount||3)};paths.push(path);points+=path.points.length;
    yield {fraction:paths.length/(p.forks+1),label:'Tracing lightning forks',batch:[path],paths:paths.length,points};
  }
  return paths;
}
export const recursiveGenerators={peano,nested_polygons,pythagoras,lightning};
