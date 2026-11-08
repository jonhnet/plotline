// Plotline's stroke-only mathematical patterns. No raster tracing or new runtime
// dependencies. Generators yield bounded batches for the existing worker UI.
const knob=(key,label,value,min,max,step=1,choices)=>({key,label,value,min,max,step,...(choices?{choices}:{})});
const ratios=[[5,2],[7,3],[9,4],[11,4],[13,5],[17,7],[19,8],[23,9],[29,11]];
const frequencies=[[3,4],[4,5],[5,7],[7,9],[8,11],[9,13],[11,15],[13,17]];
const pendulums=[[1,2],[2,3],[3,4],[3,5],[4,5],[5,7],[7,9]];
const ratioLabels=values=>values.map(([a,b])=>`${a} : ${b}`);
export const adjacentFamilies=[
  {id:'spirograph',name:'Spirograph',sequence:true,roles:['First curves','Second curves','Third curves'],knobs:[knob('gear','Gear ratio',4,0,8,1,ratioLabels(ratios)),knob('outside','Rolling circle',0,0,1,1,['Inside','Outside']),knob('offset','Pen offset',.85,.3,1.3,.05),knob('layers','Interleaved curves',8,2,16),knob('twist','Twist per curve',6,0,18,3)]},
  {id:'lissajous',name:'Lissajous curves',sequence:true,roles:['First curves','Second curves','Third curves'],knobs:[knob('ratio','Frequency ratio',4,0,7,1,ratioLabels(frequencies)),knob('phase','Phase',30,-90,90,15),knob('layers','Interleaved curves',9,2,17),knob('spread','Phase step',4,1,12),knob('aspect','Aspect',1,.5,1.5,.1)]},
  {id:'harmonograph',name:'Harmonograph',sequence:true,roles:['Early trace','Middle trace','Late trace'],knobs:[knob('ratio','Pendulum ratio',2,0,6,1,ratioLabels(pendulums)),knob('detune','Detuning',.012,0,.06,.004),knob('damping','Damping',.03,.006,.054,.004),knob('turns','Cycles',35,10,70,5),knob('phase','Phase',60,0,180,15)]},
  {id:'phyllotaxis',name:'Phyllotaxis',sequence:true,roles:['First seeds','Second seeds','Third seeds'],knobs:[knob('count','Seeds',600,200,2200,100),knob('angle','Divergence angle',137.5,136,139,.1),knob('shape','Seed shape',1,0,2,1,['Ellipse','Leaf','Diamond']),knob('seed_size','Seed size',.75,.35,1.15,.1),knob('aspect','Seed aspect',1.8,1,3,.2),knob('twist','Seed twist',30,-90,90,15)]},
  {id:'penrose',name:'Penrose tiling',roles:['Tile edges','Thin tile motifs','Thick tile motifs'],knobs:[knob('depth','Subdivisions',5,2,8),knob('patch','Starting patch',0,0,2,1,['Decagon','Triangle','Rhombus']),knob('insets','Nested outlines',1,0,3),knob('inset','Outline inset',.24,.12,.48,.06)]},
  {id:'truchet',name:'Truchet tiling',sequence:true,roles:['First strands','Second strands','Third strands'],knobs:[knob('columns','Columns',20,6,60,2),knob('rows','Rows',14,6,40,2),knob('bands','Parallel strands',3,1,6),knob('spread','Band spread',.3,.1,.4,.05),knob('seed','Arrangement',11,1,99)]},
];
const tau=2*Math.PI,phi=(1+Math.sqrt(5))/2;
const rad=a=>a*Math.PI/180;
const path=(points,role=0)=>({points,role});
const polar=(r,t)=>[r*Math.cos(t),r*Math.sin(t)];
const mix=(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t);
const key=p=>p.map(v=>Math.round(v*1e10)).join(',');
const edgeKey=(a,b)=>[key(a),key(b)].sort().join('/');
const randomFrom=seed=>()=>{seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;return (seed>>>0)/4294967296;};
const gcd=(a,b)=>b?gcd(b,a%b):a;
// 384 segments per fastest oscillation keeps smooth curves on standard large
// sheets. No sampling knob: duration/gear/frequency determine work automatically.
const samples=frequency=>Math.ceil(384*frequency);
function sampled(n,fn,closed=false){const points=Array.from({length:n+1},(_,i)=>fn(i/n));if(closed)points[n]=points[0].slice();return points;}
function reporter(label,guard){
  let paths=0,points=0;
  return (batch,fraction)=>{paths+=batch.length;points+=batch.reduce((sum,p)=>sum+p.points.length,0);guard(paths,points);return {batch,paths,points,fraction,label};};
}
function* spirograph(p,guard){
  const [R,r]=ratios[p.gear],sign=p.outside?1:-1,arm=R+sign*r,period=r/gcd(R,r),n=samples(Math.max(r,arm)/gcd(R,r));
  guard(p.layers,(n+1)*p.layers);
  const output=[],report=reporter('Drawing rolling circles',guard);
  for(let j=0;j<p.layers;j++){
    const turn=rad(p.twist*j),scale=.965**j,d=p.offset*r;
    const points=sampled(n,u=>{
      const t=u*tau*period,x=(arm*Math.cos(t)-sign*d*Math.cos(arm/r*t))*scale,y=(arm*Math.sin(t)-d*Math.sin(arm/r*t))*scale;
      return [x*Math.cos(turn)-y*Math.sin(turn),x*Math.sin(turn)+y*Math.cos(turn)];
    },true);
    const curve=path(points,j%(p.inkCount||3));output.push(curve);yield report([curve],(j+1)/p.layers);
  }
  return output;
}
function* lissajous(p,guard){
  const [fx,fy]=frequencies[p.ratio],n=samples(Math.max(fx,fy)),output=[],report=reporter('Drawing oscillations',guard);
  guard(p.layers,(n+1)*p.layers);
  for(let j=0;j<p.layers;j++){
    const phase=rad(p.phase+(j-(p.layers-1)/2)*p.spread);
    const curve=path(sampled(n,u=>[p.aspect*Math.cos(fx*tau*u+phase),Math.sin(fy*tau*u)],true),j%(p.inkCount||3));
    output.push(curve);yield report([curve],(j+1)/p.layers);
  }
  return output;
}
function* harmonograph(p,guard){
  const [fx,fy]=pendulums[p.ratio],n=samples((Math.max(fx,fy)+p.detune)*p.turns),count=p.inkCount||3,output=[],report=reporter('Tracing pendulum motion',guard);
  guard(count,n+count);
  const at=i=>{const turns=i/n*p.turns,t=turns*tau,fade=Math.exp(-p.damping*turns),late=Math.exp(-p.damping*1.35*turns);return [fade*Math.sin(fx*t+rad(p.phase))+.35*late*Math.sin((fx+p.detune)*t),fade*Math.sin(fy*t)+.35*late*Math.sin((fy-p.detune)*t+Math.PI/2)];};
  for(let j=0;j<count;j++){
    const start=Math.round(n*j/count),end=Math.round(n*(j+1)/count);
    const curve=path(Array.from({length:end-start+1},(_,i)=>at(start+i)),j);output.push(curve);yield report([curve],(j+1)/count);
  }
  return output;
}
function* phyllotaxis(p,guard){
  const output=[],report=reporter('Placing seeds',guard),a=p.seed_size/Math.sqrt(p.count),b=a/p.aspect;
  guard(p.count,37*p.count);
  for(let start=0;start<p.count;start+=40){
    const batch=[];
    for(let i=start;i<Math.min(start+40,p.count);i++){
      const theta=i*rad(p.angle),center=polar(Math.sqrt((i+.5)/p.count),theta),turn=theta+rad(p.twist);
      const points=sampled(p.shape===2?4:36,u=>{
        const t=tau*u,x=a*Math.cos(t),y=b*Math.sin(t)*(p.shape===1?Math.abs(Math.sin(t)):1);
        return [center[0]+x*Math.cos(turn)-y*Math.sin(turn),center[1]+x*Math.sin(turn)+y*Math.cos(turn)];
      },true);
      batch.push(path(points,i%(p.inkCount||3)));
    }
    output.push(...batch);yield report(batch,Math.min(start+40,p.count)/p.count);
  }
  return output;
}
// Robinson half-rhombs: acute (36° apex) and obtuse (108° apex).
// Mathematical construction: Treibergs, math.utah.edu/~treiberg/PenroseSlides.pdf;
// triangle convention illustrated at preshing.com/20110831/penrose-tiling-explained/.
export function subdivideGoldenTriangles(triangles){
  const result=[];
  for(const {kind,a,b,c} of triangles){
    if(kind===0){
      const cut=mix(a,b,1/phi);
      result.push({kind:0,a:c,b:cut,c:b},{kind:1,a:cut,b:c,c:a});
    }else{
      const along=mix(b,a,1/phi),across=mix(b,c,1/phi);
      result.push({kind:1,a:across,b:c,c:a},{kind:1,a:along,b:across,c:b},{kind:0,a:across,b:along,c:a});
    }
  }
  return result;
}
function* penrose(p,guard){
  let triangles;
  if(p.patch===0)triangles=Array.from({length:10},(_,i)=>{let b=polar(1,(2*i-1)*Math.PI/10),c=polar(1,(2*i+1)*Math.PI/10);if(i%2===0)[b,c]=[c,b];return {kind:0,a:[0,0],b,c};});
  else{const b=polar(1,-Math.PI/10),c=polar(1,Math.PI/10);triangles=[{kind:0,a:[0,0],b,c}];if(p.patch===2)triangles.push({kind:0,a:[2*b[0],0],b:c,c:b});}
  for(let level=0;level<p.depth;level++){
    guard(triangles.length*3,triangles.length*9);triangles=subdivideGoldenTriangles(triangles);
    yield {fraction:.25*(level+1)/p.depth,label:'Subdividing Penrose tiles'};
  }
  const edges=new Map(),bases=new Map();
  for(const t of triangles){
    for(const [a,b] of [[t.a,t.b],[t.a,t.c]])edges.set(edgeKey(a,b),[a,b]);
    const k=edgeKey(t.b,t.c);if(!bases.has(k))bases.set(k,[]);bases.get(k).push(t);
  }
  // Pair half-tiles on their bases; draw unpaired boundary bases to close the
  // finite patch. Every shared edge is emitted exactly once, never overtraced.
  const tiles=[];
  for(const pair of bases.values()){
    const t=pair[0];
    if(pair.length===2)tiles.push({kind:t.kind,vertices:[t.a,t.b,pair[1].a,t.c]});
    else{edges.set(edgeKey(t.b,t.c),[t.b,t.c]);tiles.push({kind:t.kind,vertices:[t.a,t.b,t.c]});}
  }
  const output=[],report=reporter('Joining Penrose edges and motifs',guard),segments=[...edges.values()];
  for(let start=0;start<segments.length;start+=256){const batch=segments.slice(start,start+256).map(points=>path(points));output.push(...batch);yield report(batch,.25+.35*Math.min(start+256,segments.length)/segments.length);}
  for(let start=0;start<tiles.length;start+=128){
    const batch=[];
    for(const tile of tiles.slice(start,start+128)){
      const center=[0,1].map(axis=>tile.vertices.reduce((sum,v)=>sum+v[axis],0)/tile.vertices.length);
      for(let j=1;j<=p.insets;j++){const points=tile.vertices.map(v=>mix(center,v,(1-p.inset)**j));points.push(points[0].slice());batch.push(path(points,1+tile.kind));}
    }
    output.push(...batch);yield report(batch,.6+.4*Math.min(start+128,tiles.length)/tiles.length);
  }
  return output;
}
function* truchet(p,guard){
  const random=randomFrom(p.seed),arcs=[],nodes=new Map(),vertices=new Map();
  guard(2*p.columns*p.rows*p.bands,50*p.columns*p.rows*p.bands);
  function add(points){
    const ends=[points[0],points.at(-1)].map(pt=>{const k=key(pt);if(!vertices.has(k))vertices.set(k,pt);if(!nodes.has(k))nodes.set(k,[]);nodes.get(k).push(arcs.length);return k;});
    points[0]=vertices.get(ends[0]);points[points.length-1]=vertices.get(ends[1]);arcs.push({points,ends});
  }
  for(let y=0;y<p.rows;y++){
    for(let x=0;x<p.columns;x++){
      const corners=random()<.5?[[x,y,0],[x+1,y+1,Math.PI]]:[[x+1,y,Math.PI/2],[x,y+1,-Math.PI/2]];
      for(let band=0;band<p.bands;band++){
        const r=.5+(p.bands===1?0:(band/(p.bands-1)-.5)*p.spread);
        for(const [cx,cy,angle] of corners)add(sampled(24,u=>[cx+r*Math.cos(angle+u*Math.PI/2),cy+r*Math.sin(angle+u*Math.PI/2)]));
      }
    }
    yield {fraction:.4*(y+1)/p.rows,label:'Laying Truchet arcs'};
  }
  const used=new Set(),output=[],report=reporter('Joining Truchet strands',guard);
  function follow(start,index){
    const points=[];let at=start;
    while(index!==undefined&&!used.has(index)){
      used.add(index);const arc=arcs[index],forward=arc.ends[0]===at,part=forward?arc.points:arc.points.slice().reverse();
      for(let j=points.length?1:0;j<part.length;j++)points.push(part[j]);at=arc.ends[forward?1:0];index=nodes.get(at).find(i=>!used.has(i));
    }
    return path(points,output.length%(p.inkCount||3));
  }
  // Begin at paper edges before collecting the remaining closed loops.
  const starts=[...nodes].filter(([,links])=>links.length===1).map(([at,links])=>[at,links[0]]);
  for(let i=0;i<arcs.length;i++)starts.push([arcs[i].ends[0],i]);
  for(let i=0;i<starts.length;i++){
    const [at,index]=starts[i];if(used.has(index))continue;
    const curve=follow(at,index);output.push(curve);yield report([curve],.4+.6*used.size/arcs.length);
  }
  return output;
}
export const adjacentGenerators={spirograph,lissajous,harmonograph,phyllotaxis,penrose,truchet};
