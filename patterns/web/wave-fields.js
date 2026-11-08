// Analytic standing-wave fields and a finite local Gray–Scott simulation.
// Only joined vector isolines enter the exported drawing.
import {contourField} from './complex.js';
const knob=(key,label,value,min,max,step=1,choices)=>({key,label,value,min,max,step,...(choices?{choices}:{})});
const sequence={sequence:true,roles:['Low contours','Middle contours','High contours']};
export const waveModes=[[3,5],[4,7],[5,8],[2,9],[6,10]];
export const reactionPresets=[{name:'Winding stripes',feed:.035,kill:.06},{name:'Spots',feed:.03,kill:.062},{name:'Cellular',feed:.022,kill:.051}];
export const fieldFamilies=[
 {id:'wave_contours',name:'Standing-wave contours',...sequence,knobs:[knob('mode','Mode pair',0,0,4,1,waveModes.map(([m,n])=>m+' × '+n)),knob('balance','Mode balance',1,.5,1.5,.1),knob('bands','Contour bands',7,1,9,2),knob('spread','Band spread',.35,.15,.65,.05),knob('depth','Detail',3,1,5)]},
 {id:'reaction',name:'Reaction–diffusion contours',...sequence,knobs:[knob('shape','Pattern',0,0,2,1,reactionPresets.map(p=>p.name)),knob('scale','Feature size',1,0,2,1,['Broad','Medium','Fine']),knob('time','Growth time',3000,1000,6000,1000),knob('bands','Contour bands',3,1,7,2),knob('seed','Variation',7,1,30)]},
];
export function waveValue(p,x,y){const [m,n]=waveModes[p.mode];return Math.cos(m*Math.PI*x)*Math.cos(n*Math.PI*y)-p.balance*Math.cos(n*Math.PI*x)*Math.cos(m*Math.PI*y);}
export function waveField(p){const n=96*p.depth,values=new Float64Array((n+1)**2);for(let y=0;y<=n;y++)for(let x=0;x<=n;x++)values[y*(n+1)+x]=waveValue(p,x/n,y/n);return {n,values};}
function* wave_contours(p,guard){
 const {n,values}=waveField(p),levels=Array.from({length:p.bands},(_,i)=>p.bands===1?1e-10:-p.spread+2*p.spread*i/(p.bands-1));levels[Math.floor(levels.length/2)]=1e-10;
 yield {fraction:.2,label:'Combining standing waves'};return yield* trace(values,n,levels,p.inkCount||3,guard,.2);
}
function* trace(values,n,levels,count,guard,start){
 const steps=contourField(values,n,levels,i=>i%count,guard);let step;
 while(!(step=steps.next()).done)yield {...step.value,fraction:start+(1-start)*step.value.fraction};return step.value;
}
const randomFrom=seed=>()=>{seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;return (seed>>>0)/4294967296;};
export function reactionInitial(p){
 const n=96+32*p.scale,u=new Float64Array(n*n).fill(1),v=new Float64Array(n*n),random=randomFrom(p.seed),centers=[];
 for(let y=16;y<n;y+=32)for(let x=16;x<n;x+=32)centers.push([x+8*(random()-.5),y+8*(random()-.5)]);
 for(let y=0;y<n;y++)for(let x=0;x<n;x++)if(centers.some(([a,b])=>(x-a)**2+(y-b)**2<25)){const i=y*n+x;u[i]=.5+.02*(random()-.5);v[i]=.25+.02*(random()-.5);}
 return {n,u,v,nextU:new Float64Array(n*n),nextV:new Float64Array(n*n),iterations:0};
}
// Explicit Euler, dt=1, five-point periodic Laplacian; fixed diffusion rates
// Du=.16, Dv=.08. Read only old buffers while writing the next generation.
export function reactionStep(s,feed,kill){
 const {n,u,v,nextU,nextV}=s;
 for(let y=0;y<n;y++){const row=y*n,up=(y?y-1:n-1)*n,down=(y+1===n?0:y+1)*n;
  for(let x=0;x<n;x++){const i=row+x,left=row+(x?x-1:n-1),right=row+(x+1===n?0:x+1),a=u[i],b=v[i],uvv=a*b*b;
   nextU[i]=a+.16*(u[left]+u[right]+u[up+x]+u[down+x]-4*a)-uvv+feed*(1-a);
   nextV[i]=b+.08*(v[left]+v[right]+v[up+x]+v[down+x]-4*b)+uvv-(feed+kill)*b;
  }
 }
 s.u=nextU;s.v=nextV;s.nextU=u;s.nextV=v;s.iterations++;
}
// One worker retains one exact simulation between its completed growth-time
// checkpoints. Rendering, resampling and earlier snapshots never mutate it.
let cached=null;
export function clearReactionCache(){cached=null;}
export function* reactionSimulation(p,{cache=true}={}){
 const signature=JSON.stringify([p.shape,p.scale,p.seed]);let s;
 if(cache&&cached?.signature===signature&&cached.state.iterations<=p.time)s=cached.state;else s=reactionInitial(p);
 const {feed,kill}=reactionPresets[p.shape];while(s.iterations<p.time){reactionStep(s,feed,kill);if(s.iterations%64===0)yield {fraction:.85*s.iterations/p.time,label:`Growing reaction pattern · ${s.iterations} / ${p.time}`};}
 if(cache)cached={signature,state:s};return s;
}
export function reactionField(s){
 // Periodic cubic interpolation at 2x gives smooth display contours without
 // changing simulation state. Clip interpolation overshoot to physical bounds.
 const n=s.n*2,values=new Float64Array((n+1)**2),at=(x,y)=>s.v[((y+s.n)%s.n)*s.n+(x+s.n)%s.n];
 const cubic=(a,b,c,d,t)=>b+.5*t*(c-a+t*(2*a-5*b+4*c-d+t*(3*(b-c)+d-a)));
 for(let y=0;y<=n;y++)for(let x=0;x<=n;x++){
  const xx=x/2,yy=y/2,ix=Math.floor(xx)%s.n,iy=Math.floor(yy)%s.n,tx=xx-Math.floor(xx),ty=yy-Math.floor(yy),rows=[];
  for(let j=-1;j<=2;j++)rows.push(cubic(at(ix-1,iy+j),at(ix,iy+j),at(ix+1,iy+j),at(ix+2,iy+j),tx));
  values[y*(n+1)+x]=Math.max(0,Math.min(1,cubic(...rows,ty)));
 }return {n,values};
}
function* reaction(p,guard){
 const s=yield* reactionSimulation(p),{n,values}=reactionField(s);let peak=0;for(const v of values)peak=Math.max(peak,v);
 if(peak<.01)throw Error('This reaction faded out. Choose another pattern or variation.');
 const levels=Array.from({length:p.bands},(_,i)=>peak*(p.bands===1?.55:.25+.6*i/(p.bands-1)));
 yield {fraction:.85,label:'Tracing reaction concentrations'};return yield* trace(values,n,levels,p.inkCount||3,guard,.85);
}
export const fieldGenerators={wave_contours,reaction};
