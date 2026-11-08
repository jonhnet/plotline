// Original petal constructions and a streamed turtle for curated bracketed
// L-systems. The rules are mathematical presets, never executable user input.
import {joinEdges} from './spatial.js';
const knob=(key,label,value,min,max,step=1,choices)=>({key,label,value,min,max,step,...(choices?{choices}:{})});
const tau=2*Math.PI;
export const plantPresets=[
 {name:'Branching herb',axiom:'F',rules:{F:'F[+F]F[-F]F'},angle:25.7},
 {name:'Bush',axiom:'F',rules:{F:'F[+F]F[-F][F]'},angle:20},
 {name:'Fern',axiom:'X',rules:{X:'F-[[X]+X]+F[+FX]-X',F:'FF'},angle:22.5},
 {name:'Tiered tree',axiom:'X',rules:{X:'F[+X]F[-X]+X',F:'FF'},angle:20},
];
export const botanicalFamilies=[
 {id:'flowers',name:'Flowers',sequence:true,roles:['Stems','Petals','Centers'],knobs:[knob('shape','Flower',0,0,2,1,['Daisy','Lotus','Starflower']),knob('petals','Petals',12,6,24,2),knob('whorls','Petal whorls',3,1,4),knob('width','Petal width',.75,.45,1.05,.15),knob('bend','Stem bend',.15,-.3,.3,.15),knob('leaves','Leaf pairs',3,0,5)]},
 {id:'lsystem',name:'L-system plants',roles:['Trunk','Branches','Leaves'],knobs:[knob('shape','Plant',2,0,3,1,plantPresets.map(p=>p.name)),knob('depth','Generations',5,2,6),knob('spread','Branch spread',1,.6,1.6,.2),knob('taper','Branch reach',.8,.5,1,.1),knob('lean','Lean',0,-.3,.3,.1),knob('leaves','Tips',1,0,1,1,['Bare branches','Leaf tips'])]},
];
export function* botanicalBatches(paths,start,label,guard){let points=0;for(let i=0;i<paths.length;i+=64){const batch=paths.slice(i,i+64);points+=batch.reduce((s,p)=>s+p.points.length,0);const sent=i+batch.length;guard(sent,points);yield {fraction:start+(1-start)*sent/paths.length,label,batch,paths:sent,points};}return paths;}
const cubic=(a,b,c,d,t)=>a.map((v,i)=>(1-t)**3*v+3*(1-t)**2*t*b[i]+3*(1-t)*t*t*c[i]+t**3*d[i]);
export function petalOutline(start,end,width,shape=0){
 const dx=end[0]-start[0],dy=end[1]-start[1],length=Math.hypot(dx,dy),normal=[-dy/length,dx/length],at=(t,w)=>[start[0]+dx*t+normal[0]*w,start[1]+dy*t+normal[1]*w];
 const a=shape===1?.1:.28,b=shape===2?.8:.68,points=[];
 for(const side of [1,-1])for(let i=0;i<24;i++){
  const controls=side>0?[start,at(a,width),at(b,width),end]:[end,at(b,-width),at(a,-width),start];points.push(cubic(...controls,i/24));
 }points.push(points[0]);return points;
}
export function flowerPaths(p){
 const paths=[],count=p.inkCount||3;
 const stem=t=>[p.bend*Math.sin(Math.PI*t),2.6*t];
 paths.push({role:0,points:Array.from({length:97},(_,i)=>stem(i/96))});
 for(let row=0;row<p.leaves;row++)for(const side of [-1,1]){
  const t=.35+.55*(row+1)/(p.leaves+1),a=stem(t),b=[a[0]+side*(.5-.15*t),a[1]-.35];paths.push({role:0,points:petalOutline(a,b,.13,1)});
 }
 // Concentric whorls occupy separate radial bands; petal outlines stay visible.
 for(let row=0;row<p.whorls;row++){
  const radius=.48**row,inner=.42*radius,phase=(row%2)*Math.PI/p.petals;
  for(let i=0;i<p.petals;i++){
   const angle=tau*i/p.petals+phase,u=[Math.cos(angle),Math.sin(angle)],width=radius*Math.sin(Math.PI/p.petals)*p.width*(p.shape===2?.5:1);
   paths.push({role:(1+row+i)%count,points:petalOutline(u.map(x=>x*inner),u.map(x=>x*radius),width,p.shape)});
  }
 }
 // The stem starts below the flower's outer petals. No line runs through the bloom.
 const first=paths[0].points.findIndex(pt=>Math.hypot(...pt)>=1.03);paths[0].points=paths[0].points.slice(first);
 const r=.32*.48**(p.whorls-1),ring=Array.from({length:49},(_,i)=>[r*Math.cos(tau*(i%48)/48),r*Math.sin(tau*(i%48)/48)]);
 paths.push({role:(count-1),points:ring});return paths;
}
function* flowers(p,guard){return yield* botanicalBatches(flowerPaths(p),0,'Drawing petals and leaves',guard);}

export function* plantCommands(p){
 const system=plantPresets[p.shape];
 function* expand(text,depth){for(const c of text)if(depth&&system.rules[c])yield* expand(system.rules[c],depth-1);else yield c;}
 yield* expand(system.axiom,p.depth);
}
export function* plantGeometry(p){
 let state={point:[0,0],heading:-Math.PI/2,depth:0},steps=0;const stack=[],edges=[],seen=new Set(),angle=plantPresets[p.shape].angle*p.spread*Math.PI/180;
 const key=pt=>pt.map(x=>Math.round(x*1e9)).join(','),project=([x,y])=>[x-p.lean*y,y];
 for(const command of plantCommands(p)){
  if(command==='F'){
   const reach=p.taper**state.depth,next=[state.point[0]+reach*Math.cos(state.heading),state.point[1]+reach*Math.sin(state.heading)],a=project(state.point),b=project(next),id=[key(a),key(b)].sort().join('/');
   if(!seen.has(id)){seen.add(id);edges.push({points:[a,b],role:state.depth?1:0});}state={...state,point:next};
  }else if(command==='+')state={...state,heading:state.heading+angle};
  else if(command==='-')state={...state,heading:state.heading-angle};
  else if(command==='['){stack.push(state);state={...state,depth:state.depth+1};}
  else if(command===']'){if(!stack.length)throw Error('Unbalanced plant preset');state=stack.pop();}
  if(++steps%1024===0)yield {fraction:.4*(1-1/(1+steps/1000)),label:'Growing the plant grammar'};
 }
 if(stack.length)throw Error('Unbalanced plant preset');
 const paths=joinEdges(edges),incident=new Map();
 if(p.leaves){
  for(const edge of edges)for(const pt of edge.points){const k=key(pt);if(!incident.has(k))incident.set(k,[]);incident.get(k).push(edge);}
  for(const [k,items] of incident)if(items.length===1&&k!==key([0,0])){
   const edge=items[0],b=key(edge.points[0])===k?edge.points[0]:edge.points[1],a=edge.points.find(pt=>pt!==b),dx=b[0]-a[0],dy=b[1]-a[1];
   const tip=[b[0]+dx*.6,b[1]+dy*.6];paths.push({role:2,points:petalOutline(b,tip,.18*Math.hypot(dx,dy),1)});
  }
 }return {paths,edges};
}
function* lsystem(p,guard){
 const {paths}=yield* plantGeometry(p);let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
 for(const path of paths)for(const [x,y] of path.points){x0=Math.min(x0,x);y0=Math.min(y0,y);x1=Math.max(x1,x);y1=Math.max(y1,y);}
 const scale=2/Math.max(x1-x0,y1-y0),cx=(x0+x1)/2,cy=(y0+y1)/2;
 const normalized=paths.map(path=>({...path,points:path.points.map(([x,y])=>[(x-cx)*scale,(y-cy)*scale])}));
 return yield* botanicalBatches(normalized,.4,'Joining branches and leaf tips',guard);
}
export const botanicalGenerators={flowers,lsystem};
