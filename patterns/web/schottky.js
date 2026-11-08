// Classical paired-circle Schottky construction. Each holomorphic generator
// maps the exterior of its source disk into its paired target disk.
const knob=(key,label,value,min,max,step=1)=>({key,label,value,min,max,step});
export const schottkyFamily={id:'schottky',name:'Schottky circle fractals',sequence:true,roles:['First generations','Middle generations','Small circles'],knobs:[knob('depth','Generations',5,2,8),knob('pairs','Circle pairs',2,2,3),knob('gap','Circle spacing',.02,.02,.26,.04),knob('twist','Pair twist',90,0,180,15),knob('cutoff','Smallest circle',.001,.0005,.006,.0005)]};
const tau=2*Math.PI;
export function schottkySystem(p){
 const n=2*p.pairs,r=Math.sin(Math.PI/n)/(1+p.gap),circles=Array.from({length:n},(_,i)=>({x:Math.cos(tau*i/n),y:Math.sin(tau*i/n),r})),angle=p.twist*Math.PI/180;
 const maps=circles.map((target,i)=>({source:circles[(i+p.pairs)%n],target,a:[r*r*Math.cos(angle),r*r*Math.sin(angle)],inverse:(i+p.pairs)%n}));return {circles,maps};
}
export function schottkyPoint([x,y],map){const dx=x-map.source.x,dy=y-map.source.y,q=dx*dx+dy*dy;return [map.target.x+(map.a[0]*dx+map.a[1]*dy)/q,map.target.y+(map.a[1]*dx-map.a[0]*dy)/q];}
export function schottkyCircle(circle,map){
 const dx=circle.x-map.source.x,dy=circle.y-map.source.y,q=dx*dx+dy*dy-circle.r**2;
 if(q<=1e-15)throw Error('Schottky circles cross a generator pole');
 return {x:map.target.x+(map.a[0]*dx+map.a[1]*dy)/q,y:map.target.y+(map.a[1]*dx-map.a[0]*dy)/q,r:Math.hypot(...map.a)*circle.r/q};
}
export function* schottkyCircles(p,guard=()=>{}){
 const {circles,maps}=schottkySystem(p);let level=circles.map((c,i)=>({...c,word:[i],depth:0,sector:i,parent:null})),count=0;
 for(let depth=0;depth<=p.depth;depth++){
  const next=[];
  for(const circle of level){
   yield circle;count++;if(depth===p.depth)continue;
   for(let k=0;k<circles.length;k++)if(k!==maps[circle.word.at(-1)].inverse){
    let child=circles[k];for(let j=circle.word.length-1;j>=0;j--)child=schottkyCircle(child,maps[circle.word[j]]);
    if(child.r>=p.cutoff)next.push({...child,word:[...circle.word,k],depth:depth+1,sector:circle.sector,parent:circle});
   }guard(count+next.length,(count+next.length)*24);
  }level=next;
 }
}
export function* schottky(p,guard){
 const paths=[],count=p.inkCount||3;let points=0,batch=[];
 for(const c of schottkyCircles(p,guard)){
  const n=Math.max(16,Math.ceil(Math.PI/Math.acos(Math.max(-1,1-.0002/c.r)))),pts=Array.from({length:n},(_,i)=>[c.x+c.r*Math.cos(tau*i/n),c.y+c.r*Math.sin(tau*i/n)]);pts.push(pts[0]);
  const path={role:(c.sector+c.depth)%count,points:pts};paths.push(path);batch.push(path);points+=pts.length;guard(paths.length,points);
  if(batch.length>=64){yield {fraction:c.depth/(p.depth+1),label:'Pairing Möbius circle generations',batch,paths:paths.length,points};batch=[];}
 }
 if(batch.length)yield {fraction:1,label:'Schottky circles ready',batch,paths:paths.length,points};return paths;
}
