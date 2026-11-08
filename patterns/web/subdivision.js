// Original edge-replacement curves and carpet hole outlines. No grammar editor.
const knob=(key,label,value,min,max,step=1,choices)=>({key,label,value,min,max,step,...(choices?{choices}:{})});
const sequence={sequence:true,roles:['First sections','Second sections','Third sections']};
export const subdivisionFamilies=[
  {id:'levy',name:'Lévy C curve',...sequence,knobs:[knob('depth','Detail',12,7,17),knob('copies','Rotated copies',1,1,4),knob('round','Corner rounding',.15,0,.45,.15)]},
  {id:'minkowski',name:'Minkowski curve',...sequence,knobs:[knob('depth','Detail',4,1,5),knob('form','Form',0,0,1,1,['Island','Open curve']),knob('round','Corner rounding',.15,0,.45,.15)]},
  {id:'carpet',name:'Sierpiński carpet',...sequence,knobs:[knob('depth','Detail',4,2,6),knob('aspect','Aspect',1,.5,1.5,.25)]},
];
export const motifs={levy:[[0,0],[.5,.5],[1,0]],minkowski:[[0,0],[.25,0],[.25,.25],[.5,.25],[.5,0],[.5,-.25],[.75,-.25],[.75,0],[1,0]]};
const mix=(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t);
export function replaceEdges(points,motif){
  const next=[];
  for(let i=1;i<points.length;i++){
    const [x,y]=points[i-1],dx=points[i][0]-x,dy=points[i][1]-y;
    for(const [u,v] of motif.slice(0,-1))next.push([x+u*dx-v*dy,y+u*dy+v*dx]);
  }
  next.push(points.at(-1));return next;
}
// Quadratic corners, sampled at thirds. Open endpoints and the closed seam are
// explicit; color boundaries are cut only after rounding, and share endpoints.
export function roundCurve(points,amount,closed=false){
  if(!amount)return points;
  const n=closed?points.length-1:points.length,rounded=closed?[]:[points[0]];
  for(let i=closed?0:1;i<(closed?n:n-1);i++){
    const b=points[i],a=mix(b,points[(i+n-1)%n],amount),c=mix(b,points[(i+1)%n],amount);rounded.push(a);
    for(let j=1;j<=3;j++){const t=j/3;rounded.push(a.map((v,k)=>(1-t)**2*v+2*t*(1-t)*b[k]+t*t*c[k]));}
  }
  rounded.push(closed?rounded[0]:points.at(-1));return rounded;
}
function* curve(family,p,guard){
  const closed=family==='minkowski'&&!p.form,copies=family==='levy'?p.copies:1,count=p.inkCount||3,motif=motifs[family],segments=(closed?4:1)*(motif.length-1)**p.depth;
  guard(copies*count,copies*(4*segments+count+1));
  let points=closed?[[0,0],[1,0],[1,1],[0,1],[0,0]]:[[0,0],[1,0]];
  for(let d=0;d<p.depth;d++){points=replaceEdges(points,motif);yield {fraction:.65*(d+1)/p.depth,label:'Replacing curve edges'};}
  points=roundCurve(points,p.round,closed);
  const output=[],section=Math.ceil((points.length-1)/count);let total=0;
  for(let copy=0;copy<copies;copy++){
    const angle=2*Math.PI*copy/copies,c=Math.cos(angle),s=Math.sin(angle);
    for(let role=0;role<count;role++){
      const pts=points.slice(role*section,Math.min(points.length,(role+1)*section+1)).map(([x,y])=>[(x-.5)*c-y*s,(x-.5)*s+y*c]);if(pts.length<2)continue;
      const path={points:pts,role:(role+copy)%count};output.push(path);total+=pts.length;
      yield {fraction:.65+.35*(copy*count+role+1)/(copies*count),label:'Joining curve sections',batch:[path],paths:output.length,points:total};
    }
  }
  return output;
}
export function* carpetHoles(depth){
  const stack=[{x:0,y:0,size:1,depth:0,region:0}];
  while(stack.length){
    const cell=stack.pop(),size=cell.size/3;yield {...cell,x:cell.x+size,y:cell.y+size,size};
    if(cell.depth+1>=depth)continue;
    // Reverse push order keeps the emitted eight sectors in row-major order.
    for(let row=2;row>=0;row--)for(let col=2;col>=0;col--)if(col!==1||row!==1){const index=row*3+col;stack.push({x:cell.x+col*size,y:cell.y+row*size,size,depth:cell.depth+1,region:cell.depth?cell.region:index>4?index-1:index});}
  }
}
function* carpet(p,guard){
  const holes=(8**p.depth-1)/7,count=p.inkCount||3,output=[];guard(holes+1,(holes+1)*5);
  const square=(x,y,size,role)=>({role,points:[[x,y],[x+size,y],[x+size,y+size],[x,y+size],[x,y]].map(([x,y])=>[x*p.aspect,y])});
  let batch=[square(0,0,1,0)],points=0;
  for(const hole of carpetHoles(p.depth)){
    batch.push(square(hole.x,hole.y,hole.size,hole.depth?(hole.region+1)%count:0));
    if(batch.length>=128){for(const path of batch){output.push(path);points+=path.points.length;}yield {fraction:output.length/(holes+1),label:'Outlining carpet openings',batch,paths:output.length,points};batch=[];}
  }
  if(batch.length){for(const path of batch){output.push(path);points+=path.points.length;}yield {fraction:1,label:'Carpet outlines ready',batch,paths:output.length,points};}
  return output;
}
export const subdivisionGenerators={levy:(p,g)=>curve('levy',p,g),minkowski:(p,g)=>curve('minkowski',p,g),carpet};
