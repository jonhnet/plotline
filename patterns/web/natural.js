// Original stroke constructions: continuous chaotic flows, affine fern stems,
// decorative dendrites and torus-knot ribbons. No point-cloud zigzags or tracing.
const knob=(key,label,value,min,max,step=1,choices)=>({key,label,value,min,max,step,...(choices?{choices}:{})});
const sequence={sequence:true,roles:['First strokes','Second strokes','Third strokes']};
const knots=[[2,3],[2,5],[3,4],[3,5],[4,5],[3,7]];
export const naturalFamilies=[
  {id:'lorenz',name:'Lorenz attractor',...sequence,knobs:[knob('duration','Trace length',120,40,240,20),knob('rho','Convection',28,26,44,2),knob('azimuth','Azimuth',-20,-90,90,10),knob('elevation','Elevation',10,-30,70,10)]},
  {id:'rossler',name:'Rössler attractor',...sequence,knobs:[knob('duration','Trace length',300,100,600,50),knob('c','Spiral shape',5.7,5.1,6.5,.05),knob('azimuth','Azimuth',20,-90,90,10),knob('elevation','Elevation',65,15,85,10)]},
  {id:'barnsley',name:'Barnsley fern',...sequence,knobs:[knob('depth','Generations',16,12,32,2),knob('curl','Stem curl',.04,-.02,.10,.01),knob('spread','Leaf spread',1,.6,1.4,.1),knob('cutoff','Smallest twig',.03,.01,.09,.01)]},
  {id:'crystal',name:'Branching crystal',...sequence,knobs:[knob('depth','Branch generations',3,2,5),knob('arms','Arms',6,4,10,2),knob('branches','Branch pairs',6,3,10),knob('angle','Branch angle',60,40,80,5),knob('taper','Branch reach',.38,.26,.50,.04),knob('seed','Variation',7,1,30)]},
  {id:'woven',name:'Woven knots',...sequence,knobs:[knob('knot','Knot',3,0,5,1,['Trefoil · 2 : 3','Cinquefoil · 2 : 5','3 : 4','3 : 5','4 : 5','3 : 7']),knob('strands','Ribbon strands',9,3,17,2),knob('spread','Ribbon width',.22,.10,.34,.04),knob('tube','Loop depth',.65,.35,.95,.10),knob('elevation','Elevation',75,35,85,10),knob('gap','Crossing clearance',1,.5,2,.25)]},
];
const tau=2*Math.PI,rad=a=>a*Math.PI/180;
const mix=(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t);
const randomFrom=seed=>()=>{seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;return (seed>>>0)/4294967296;};
const path=(points,role)=>({points,role});
function reporter(label,guard){let paths=0,points=0;return (batch,fraction)=>{paths+=batch.length;points+=batch.reduce((s,p)=>s+p.points.length,0);guard(paths,points);return {label,batch,fraction,paths,points};};}
export function project3([x,y,z],azimuth,elevation){const a=rad(azimuth),e=rad(elevation),u=x*Math.sin(a)+y*Math.cos(a);return [x*Math.cos(a)-y*Math.sin(a),u*Math.sin(e)-z*Math.cos(e),u*Math.cos(e)+z*Math.sin(e)];}
// Equations/standard parameters: J. C. Sprott, UW Madison,
// https://sprott.physics.wisc.edu/chaos/comchaos.htm . RK4, fixed deterministic step.
export const flowDerivative=(family,p,[x,y,z])=>family==='lorenz'?[10*(y-x),x*(p.rho-z)-y,x*y-8*z/3]:[-y-z,x+.2*y,.2+z*(x-p.c)];
export function rk4(at,dt,derivative){const a=derivative(at),b=derivative(at.map((v,i)=>v+dt*a[i]/2)),c=derivative(at.map((v,i)=>v+dt*b[i]/2)),d=derivative(at.map((v,i)=>v+dt*c[i]));return at.map((v,i)=>v+dt*(a[i]+2*b[i]+2*c[i]+d[i])/6);}
function* flow(family,p,guard){
  const dt=family==='lorenz'?.008:.025,burn=family==='lorenz'?20:120,n=Math.round(p.duration/dt),count=p.inkCount||3,chunk=192,output=[],report=reporter('Tracing '+(family==='lorenz'?'Lorenz':'Rössler')+' motion',guard),derivative=at=>flowDerivative(family,p,at);
  guard(Math.ceil(n/chunk),n+Math.ceil(n/chunk));let at=[1,1,1];
  for(let i=0;i<Math.round(burn/dt);i++){at=rk4(at,dt,derivative);if(i%512===0)yield {fraction:.05*i/(burn/dt),label:'Settling onto the attractor'};}
  let first=project3(at,p.azimuth,p.elevation).slice(0,2);
  for(let start=0;start<n;start+=chunk){
    const points=[first];for(let i=start;i<Math.min(start+chunk,n);i++){at=rk4(at,dt,derivative);if(at.some(v=>!Number.isFinite(v)||Math.abs(v)>1e5))throw Error('This orbit escaped. Try a neighboring shape.');points.push(project3(at,p.azimuth,p.elevation).slice(0,2));}
    const curve=path(points,Math.floor(start/chunk)%count);first=points.at(-1);output.push(curve);yield report([curve],.05+.95*Math.min(start+chunk,n)/n);
  }
  return output;
}
// Affine IFS with the rank-one stem represented by its interval, then copies
// under the other three maps. Unlike chaos-game stippling, each line is an
// actual transformed stem; successive unrelated sample points are never joined.
export function fernMaps(p){return [[.85,p.curl,-p.curl,.85,0,1.6],[.2,-.26*p.spread,.23,.22,0,1.6],[-.15,.28*p.spread,.26,.24,0,.44]];}
const affine=(m,[x,y])=>[m[0]*x+m[1]*y+m[4],m[2]*x+m[3]*y+m[5]];
const compose=(a,b)=>[a[0]*b[0]+a[1]*b[2],a[0]*b[1]+a[1]*b[3],a[2]*b[0]+a[3]*b[2],a[2]*b[1]+a[3]*b[3],...affine(a,[b[4],b[5]])];
function* barnsley(p,guard){
  const maps=fernMaps(p),output=[],report=reporter('Growing affine fern stems',guard);let level=[[1,0,0,1,0,0]];
  for(let d=0;d<=p.depth;d++){
    const next=[];
    for(let start=0;start<level.length;start+=128){
      const batch=[];
      for(const m of level.slice(start,start+128)){
        const ends=[[0,0],[0,1.6]].map(pt=>affine(m,pt)).map(([x,y])=>[x,-y]);
        if(Math.hypot(ends[1][0]-ends[0][0],ends[1][1]-ends[0][1])>=p.cutoff)batch.push(path(ends,d%(p.inkCount||3)));
        if(d<p.depth)for(const f of maps){const child=compose(m,f);if(1.6*Math.hypot(...child.slice(0,4))>=p.cutoff)next.push(child);}
      }
      guard(output.length+batch.length+next.length,2*(output.length+batch.length)+6*next.length);
      output.push(...batch);yield report(batch,(d+Math.min(start+128,level.length)/level.length)/(p.depth+1));
    }
    level=next;if(!level.length)break;
  }
  return output;
}
// A decorative, seeded branching construction, not a diffusion or ice-physics
// simulation. Build one arm and replicate it exactly; never redraw its trunk.
function* crystal(p,guard){
  const random=randomFrom(p.seed),arm=[],queue=[{a:[0,0],angle:-Math.PI/2,length:1,depth:0}],output=[],report=reporter('Growing symmetric crystal branches',guard);
  for(let i=0;i<queue.length;i++){
    const {a,angle,length,depth}=queue[i],b=[a[0]+Math.cos(angle)*length,a[1]+Math.sin(angle)*length];arm.push({a,b,depth});
    if(depth<p.depth)for(let j=1;j<=p.branches;j++){
      const t=j/(p.branches+1),reach=length*p.taper*Math.sin(Math.PI*t)*(.85+.3*random());
      if(reach<.012)continue;const origin=mix(a,b,t),turn=rad(p.angle)*(.94+.12*random());
      for(const sign of [-1,1])queue.push({a:origin,angle:angle+sign*turn,length:reach,depth:depth+1});
    }
    if(i%128===0){guard(queue.length*p.arms,queue.length*p.arms*2);yield {fraction:.25*depth/(p.depth+1),label:'Branching the crystal arm'};}
  }
  for(let i=0;i<arm.length;i+=128){
    const batch=[];for(const twig of arm.slice(i,i+128))for(let a=0;a<p.arms;a++){
      const angle=tau*a/p.arms,points=[twig.a,twig.b].map(([x,y])=>[x*Math.cos(angle)-y*Math.sin(angle),x*Math.sin(angle)+y*Math.cos(angle)]);
      batch.push(path(points,(twig.depth+a)%(p.inkCount||3)));
    }
    output.push(...batch);yield report(batch,.25+.75*Math.min(i+128,arm.length)/arm.length);
  }
  return output;
}
export function knotCenter(p){const [a,b]=knots[p.knot],n=240*Math.max(a,b);return Array.from({length:n+1},(_,i)=>{const t=(i===n?0:i/n)*tau,r=2+p.tube*Math.cos(b*t);return project3([r*Math.cos(a*t),r*Math.sin(a*t),p.tube*Math.sin(b*t)],0,p.elevation);});}
export function knotFrame(p){const points=knotCenter(p),xs=points.map(pt=>pt[0]),ys=points.map(pt=>pt[1]),r=p.spread/2;return [Math.min(...xs)-r,Math.min(...ys)-r,Math.max(...xs)-Math.min(...xs)+2*r,Math.max(...ys)-Math.min(...ys)+2*r];}
// Parameter intervals where AB enters a capsule of radius r around CD. This
// includes the round end caps, so a crossing's clearance survives finite pens.
export function capsuleInterval(a,b,c,d,r){
  const vx=b[0]-a[0],vy=b[1]-a[1],wx=d[0]-c[0],wy=d[1]-c[1],length=Math.hypot(wx,wy),hits=[];
  function circle(e){const x=a[0]-e[0],y=a[1]-e[1],A=vx*vx+vy*vy,B=2*(x*vx+y*vy),C=x*x+y*y-r*r,D=B*B-4*A*C;if(A<1e-24){if(C<=0)hits.push([0,1]);}else if(D>=0){const s=Math.sqrt(D),lo=Math.max(0,(-B-s)/(2*A)),hi=Math.min(1,(-B+s)/(2*A));if(hi>=lo)hits.push([lo,hi]);}}
  circle(c);circle(d);
  if(length>1e-12){const ux=wx/length,uy=wy/length,x=(a[0]-c[0])*ux+(a[1]-c[1])*uy,y=-(a[0]-c[0])*uy+(a[1]-c[1])*ux,dx=vx*ux+vy*uy,dy=-vx*uy+vy*ux;let lo=0,hi=1;for(const [v,s,min,max] of [[x,dx,0,length],[y,dy,-r,r]]){if(Math.abs(s)<1e-15){if(v<min||v>max){lo=1;hi=0;}}else{lo=Math.max(lo,Math.min((min-v)/s,(max-v)/s));hi=Math.min(hi,Math.max((min-v)/s,(max-v)/s));}}if(hi>=lo)hits.push([lo,hi]);}
  return hits.length?[Math.min(...hits.map(h=>h[0])),Math.max(...hits.map(h=>h[1]))]:null;
}
function* woven(p,guard){
  const center=knotCenter(p),n=center.length-1,count=p.inkCount||3,widths=p.inkWidths||Array(count).fill(.008),maxWidth=Math.max(...widths),radius=p.spread/2+maxWidth/2+p.gap*maxWidth,cell=radius*2,grid=new Map(),output=[],report=reporter('Weaving over and under crossings',guard);
  guard(n*p.strands,n*p.strands*4);
  const cellKeys=(a,b,pad)=>{const keys=[];for(let x=Math.floor((Math.min(a[0],b[0])-pad)/cell);x<=Math.floor((Math.max(a[0],b[0])+pad)/cell);x++)for(let y=Math.floor((Math.min(a[1],b[1])-pad)/cell);y<=Math.floor((Math.max(a[1],b[1])+pad)/cell);y++)keys.push(x+','+y);return keys;};
  for(let j=0;j<n;j++)for(const key of cellKeys(center[j],center[j+1],radius+maxWidth/2)){if(!grid.has(key))grid.set(key,[]);grid.get(key).push(j);}
  const tangent=center.slice(0,n).map((_,i)=>{const a=center[(i+n-1)%n],b=center[(i+1)%n],length=Math.hypot(b[0]-a[0],b[1]-a[1]);return [-(b[1]-a[1])/length,(b[0]-a[0])/length];});tangent.push(tangent[0]);
  for(let strand=0;strand<p.strands;strand++){
    const offset=p.spread*(strand/(p.strands-1)-.5),role=strand%count,points=center.map((pt,i)=>[pt[0]+offset*tangent[i][0],pt[1]+offset*tangent[i][1],pt[2]]),batch=[];let run=[];
    const flush=()=>{if(run.length>1)batch.push(path(run,role));run=[];};
    for(let i=0;i<n;i++){
      const a=points[i],b=points[i+1],cuts=[],candidates=new Set(cellKeys(a,b,0).flatMap(k=>grid.get(k)||[]));
      for(const j of candidates){
        // Exclude only the local ribbon section. True crossings are separated
        // by at least a full loop in the parameter, regardless of sampling.
        const delta=Math.min(Math.abs(j-i),n-Math.abs(j-i));if(delta<20)continue;
        const c=center[j],d=center[j+1],interval=capsuleInterval(a,b,c,d,radius+widths[role]/2);if(!interval)continue;
        const t=(interval[0]+interval[1])/2,pt=mix(a,b,t),dx=d[0]-c[0],dy=d[1]-c[1],u=Math.max(0,Math.min(1,((pt[0]-c[0])*dx+(pt[1]-c[1])*dy)/(dx*dx+dy*dy)));
        if(c[2]+u*(d[2]-c[2])>pt[2]+.01)cuts.push(interval);
      }
      cuts.sort((a,b)=>a[0]-b[0]);let start=0;
      const append=(lo,hi)=>{if(hi-lo<1e-9)return;const first=mix(a,b,lo).slice(0,2),last=mix(a,b,hi).slice(0,2);if(!run.length)run.push(first);run.push(last);};
      for(const [lo,hi] of cuts){if(lo>start)append(start,lo);flush();start=Math.max(start,hi);}if(start<1)append(start,1);
    }
    flush();
    // Close the parameter seam without an unnecessary lift when it is visible.
    if(batch.length>1){const first=batch[0],last=batch.at(-1);if(Math.hypot(...first.points[0].map((v,i)=>v-last.points.at(-1)[i]))<1e-9){last.points.push(...first.points.slice(1));batch.shift();}}
    output.push(...batch);yield report(batch,(strand+1)/p.strands);
  }
  return output;
}
export const naturalGenerators={lorenz:(p,g)=>flow('lorenz',p,g),rossler:(p,g)=>flow('rossler',p,g),barnsley,crystal,woven};
