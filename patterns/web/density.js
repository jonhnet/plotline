// Point attractors are distributions, not continuous trajectories. Accumulate
// their density, smooth the field, and reuse the joined marching-squares tracer.
import {contourField} from './complex.js';
const knob=(key,label,value,min,max,step=1,choices)=>({key,label,value,min,max,step,...(choices?{choices}:{})});
export const attractorPresets={
  clifford:[[-1.4,1.6,1,.7],[1.7,1.7,.6,1.2],[-1.8,-2,-.5,-.9],[1.5,-1.8,1.6,.9]],
  dejong:[[1.4,-2.3,2.4,-2.1],[-2,-2,-1.2,2],[1.7,1.7,.6,1.2],[-2.7,-.09,-.86,-2.2]],
};
export const densityFamilies=['clifford','dejong'].map(id=>({id,name:id==='clifford'?'Clifford contours':'De Jong contours',sequence:true,roles:['Outer density','Middle density','Inner density'],knobs:[
  knob('shape','Shape',0,0,3,1,id==='clifford'?['Folded veil','Wings','Ribbon','Shell']:['Swirl','Petals','Loops','Fan']),
  knob('depth','Detail',3,1,4),knob('contours','Contours',12,6,18,2),knob('softness','Softness',1,.5,2,.25),
]}));
export const isDensity=id=>id==='clifford'||id==='dejong';
export function attractorStep(family,[a,b,c,d],[x,y]){
  return family==='clifford'?[Math.sin(a*y)+c*Math.cos(a*x),Math.sin(b*x)+d*Math.cos(b*y)]:[Math.sin(a*y)-Math.cos(b*x),Math.sin(c*x)-Math.cos(d*y)];
}
// Bilinear deposition preserves unit mass and avoids choosing one side of a
// pixel boundary. Padding leaves the entire smoothed support inside the frame.
export function deposit(field,n,x,y){
  const col=Math.floor(x),row=Math.floor(y),u=x-col,v=y-row,i=row*(n+1)+col;
  field[i]+=(1-u)*(1-v);field[i+1]+=u*(1-v);field[i+n+1]+=(1-u)*v;field[i+n+2]+=u*v;
}
export function* smoothDensity(field,n,sigma){
  const radius=Math.ceil(3*sigma),kernel=Array.from({length:2*radius+1},(_,i)=>Math.exp(-.5*((i-radius)/sigma)**2)),sum=kernel.reduce((a,b)=>a+b,0),temp=new Float64Array(field.length),output=new Float64Array(field.length);
  for(let i=0;i<kernel.length;i++)kernel[i]/=sum;
  for(let axis=0;axis<2;axis++){
    const input=axis?temp:field,target=axis?output:temp;
    for(let y=0;y<=n;y++){
      for(let x=0;x<=n;x++){let value=0;for(let j=-radius;j<=radius;j++){const xx=x+(axis?0:j),yy=y+(axis?j:0);if(xx>=0&&xx<=n&&yy>=0&&yy<=n)value+=input[yy*(n+1)+xx]*kernel[j+radius];}target[y*(n+1)+x]=value;}
      if(y%32===0)yield {fraction:(axis+y/(n+1))/2,label:'Smoothing point density'};
    }
  }
  return output;
}
export function* densityField(family,p){
  const n=128*p.depth,params=attractorPresets[family][p.shape],bound=(family==='clifford'?1+Math.max(Math.abs(params[2]),Math.abs(params[3])):2)*1.16,samples=12*n*n,field=new Float64Array((n+1)**2);
  let at=[.1,.1];for(let i=0;i<4096;i++)at=attractorStep(family,params,at);
  for(let i=0;i<samples;i++){
    at=attractorStep(family,params,at);deposit(field,n,(at[0]/bound+1)*n/2,(-at[1]/bound+1)*n/2);
    if(i%8192===0)yield {fraction:.7*i/samples,label:'Sampling the point attractor'};
  }
  const smoothing=smoothDensity(field,n,n*.008*p.softness);let step;
  while(!(step=smoothing.next()).done)yield {...step.value,fraction:.7+.3*step.value.fraction};
  // Convert counts to a density independent of the number of samples/grid size.
  const values=step.value;for(let i=0;i<values.length;i++)values[i]*=n*n/samples;
  return {values,n,samples,bound};
}
function* contours(family,p,guard){
  const steps=densityField(family,p);let step;
  while(!(step=steps.next()).done)yield {...step.value,fraction:.65*step.value.fraction};
  const {values,n}=step.value,positive=Array.from(values).filter(v=>v>.05).sort((a,b)=>a-b);
  if(!positive.length)throw Error('This attractor has no resolved density. Choose another shape.');
  const low=positive[Math.floor(positive.length*.15)],high=positive[Math.floor(positive.length*.97)];
  const levels=Array.from({length:p.contours},(_,i)=>low*(high/low)**(i/(p.contours-1))),trace=contourField(values,n,levels,l=>l%(p.inkCount||3),guard,{minPoints:8});
  while(!(step=trace.next()).done)yield {...step.value,fraction:.65+.35*step.value.fraction};
  return step.value;
}
export const densityGenerators={clifford:(p,g)=>contours('clifford',p,g),dejong:(p,g)=>contours('dejong',p,g)};
