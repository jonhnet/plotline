// Conway–Radin five-triangle subdivision. Integer coordinates share a power-of-
// ten denominator at each level, keeping shared vertices exactly identical.
import {joinEdges} from './spatial.js';
const knob=(key,label,value,min,max,step=1,choices)=>({key,label,value,min,max,step,...(choices?{choices}:{})});
export const pinwheelFamily={id:'pinwheel',name:'Pinwheel tiling',roles:['Tile edges','Clockwise motifs','Counterclockwise motifs'],knobs:[knob('depth','Detail',3,1,5),knob('patch','Patch',1,0,1,1,['Triangle','Rectangle']),knob('insets','Nested motifs',1,0,3)]};
export function subdividePinwheel([a,b,c]){
 const weighted=(x,y,z)=>a.map((v,i)=>x*v+y*b[i]+z*c[i]),aa=weighted(10,0,0),bb=weighted(0,10,0),cc=weighted(0,0,10),d=weighted(5,5,0),e=weighted(0,6,4),f=weighted(5,1,4),g=weighted(0,2,8);
 return [[e,bb,d],[f,d,aa],[f,d,g],[e,g,d],[g,aa,cc]];
}
export function pinwheelTiles(p){let tiles=[[[0,0],[2,0],[0,1]]];if(p.patch)tiles.push([[2,1],[0,1],[2,0]]);for(let d=0;d<p.depth;d++)tiles=tiles.flatMap(subdividePinwheel);return {tiles,denominator:10**p.depth};}
const gcd=(a,b)=>b?gcd(b,a%b):Math.abs(a);
export function pinwheelEdges(tiles,denominator){
 const lines=new Map();
 for(const tile of tiles)for(let i=0;i<3;i++){
  const a=tile[i],b=tile[(i+1)%3],dx=b[0]-a[0],dy=b[1]-a[1],div=gcd(dx,dy),sign=dx<0||(!dx&&dy<0)?-1:1,u=dx/div*sign,v=dy/div*sign,normal=u*a[1]-v*a[0],id=[u,v,normal].join(',');
  if(!lines.has(id))lines.set(id,{u,v,normal,events:new Map()});const line=lines.get(id),t=[u*a[0]+v*a[1],u*b[0]+v*b[1]].sort((a,b)=>a-b);
  // Keeping all breakpoints handles T-junctions and partially shared sides.
  for(const [at,change] of [[t[0],1],[t[1],-1]])line.events.set(at,(line.events.get(at)||0)+change);
 }
 const edges=[];
 for(const {u,v,normal,events} of lines.values()){
  const breaks=[...events.keys()].sort((a,b)=>a-b),norm=u*u+v*v,point=t=>[(u*t-v*normal)/norm/denominator,(v*t+u*normal)/norm/denominator];let active=0;
  for(let i=0;i<breaks.length-1;i++){active+=events.get(breaks[i]);if(active)edges.push({role:0,points:[point(breaks[i]),point(breaks[i+1])]});}
 }return edges;
}
export function* pinwheel(p,guard){
 const total=(p.patch?2:1)*5**p.depth;guard(total*(3+p.insets),total*(6+4*p.insets));
 const {tiles,denominator}=pinwheelTiles(p);yield {fraction:.3,label:'Subdividing pinwheel triangles'};
 const paths=joinEdges(pinwheelEdges(tiles,denominator));
 for(const triangle of tiles){
  const t=triangle.map(pt=>pt.map(v=>v/denominator)),center=[0,1].map(j=>t.reduce((s,pt)=>s+pt[j],0)/3),sign=(t[1][0]-t[0][0])*(t[2][1]-t[0][1])-(t[1][1]-t[0][1])*(t[2][0]-t[0][0]);
  for(let n=0;n<p.insets;n++){const scale=.62**(n+1),points=t.map(pt=>pt.map((v,j)=>center[j]+(v-center[j])*scale));points.push(points[0]);paths.push({role:sign>0?1:2,points});}
 }
 let points=0;for(let i=0;i<paths.length;i+=128){const batch=paths.slice(i,i+128);points+=batch.reduce((s,p)=>s+p.points.length,0);const sent=i+batch.length;guard(sent,points);yield {fraction:.4+.6*sent/paths.length,label:'Joining tile boundaries',batch,paths:sent,points};}return paths;
}
