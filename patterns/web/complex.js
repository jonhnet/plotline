// Complex-plane fields and joined vector contours. No raster enters the artwork.
const knob=(key,label,value,min,max,step=1)=>({key,label,value,min,max,step});
const locations={
  mandelbrot:[[-.65,0,3.4],[-.745,.112,.03],[-.7453,.1127,.014],[-1.75,0,.12]],
  burning_ship:[[-.45,-.5,3.5],[-1.755,-.035,.13],[-1.77,-.025,.035]],
};
export const complexFamilies=[
  {id:'mandelbrot',name:'Mandelbrot contours',sequence:true,roles:['Outer contours','Middle contours','Inner contours'],knobs:[{...knob('location','Starting view',0,0,3),choices:['Whole set','Seahorse valley','Double spiral','Satellite']},knob('contours','Contour count',16,8,24,2),knob('spacing','Contour spacing',1.25,.5,3,.25)]},
  {id:'burning_ship',name:'Burning Ship',sequence:true,roles:['Outer contours','Middle contours','Inner contours'],knobs:[{...knob('location','Starting view',0,0,2),choices:['Whole set','Little ship','Masts']},knob('contours','Contour count',16,8,24,2),knob('spacing','Contour spacing',1.25,.5,3,.25)]},
  {id:'newton',name:'Newton basins',roles:Array.from({length:7},(_,i)=>`Root ${i+1}`),knobs:[knob('roots','Roots',5,3,7),knob('depth','Detail depth',8,4,20),knob('spacing','Contour spacing',.75,.25,1.5,.25)]},
];
// Older Newton recipes specified a contour count but had an independent,
// effectively unlimited boundary pass. Preserve their outer contour scale.
export function newtonParams(params){
  const result={...params};
  if(result.depth===undefined)result.depth=result.contours===undefined?8:Math.max(4,Math.min(20,Math.round(2+(result.contours-1)*(result.spacing??.75))));
  delete result.contours;return result;
}
export const isComplex=id=>['julia','mandelbrot','burning_ship','newton'].includes(id);
export const juliaShapes=[[-.4,.6],[-.7,.27015],[-.745,.186],[-.8,.156],[-.70176,-.3842],[-.835,-.2321],[-.7269,.1889],[-.54,.54],[-.63,.4]];
export const complexFrame=[-1,-1,2,2];
export function complexWindow(family,params){
  const [real,imag,span]=locations[family]?.[params.location||0]||(family==='julia'?[0,0,3.3]:[0,0,3.6]);
  return params.viewport||{real,imag,span};
}
// Keep Julia's existing image orientation; other complex planes use +Im upward.
const imDirection=family=>family==='julia'?1:-1;
export function complexPoint(family,params,[x,y]){const v=complexWindow(family,params);return [v.real+x*v.span/2,v.imag+imDirection(family)*y*v.span/2];}
export function navigateComplex(family,params,{anchor=[0,0],delta=[0,0],factor=1}){
  const old=complexWindow(family,params),span=Math.max(1e-10,Math.min(20,old.span/factor)),ratio=span/old.span;
  return {span,real:old.real+(anchor[0]*(1-ratio)-delta[0]*ratio)*old.span/2,imag:old.imag+imDirection(family)*(anchor[1]*(1-ratio)-delta[1]*ratio)*old.span/2};
}
export function validateComplex(params){
  if(params.viewport!==undefined){const v=params.viewport;if(!v||!['real','imag','span'].every(k=>Number.isFinite(v[k]))||Math.abs(v.real)>100||Math.abs(v.imag)>100||v.span<1e-10||v.span>20)throw Error('Invalid complex-plane window');}
  if(params.c!==undefined&&(!Array.isArray(params.c)||params.c.length!==2||params.c.some(v=>!Number.isFinite(v)||Math.abs(v)>100)))throw Error('Invalid Julia parameter');
}
export function escapeValue(family,real,imag,limit=256,c=[0,0]){
  let a=family==='julia'?real:0,b=family==='julia'?imag:0,j=0;
  const cx=family==='julia'?c[0]:real,cy=family==='julia'?c[1]:imag;
  // Analytic interior tests save long bounded orbits in the overview.
  if(family==='mandelbrot'){
    const q=(real-.25)**2+imag*imag;
    if(q*(q+real-.25)<=imag*imag/4||(real+1)**2+imag*imag<=1/16)return limit;
  }
  while(a*a+b*b<256&&j<limit){const next=a*a-b*b+cx;b=(family==='burning_ship'?Math.abs(2*a*b):2*a*b)+cy;a=next;j++;}
  return j===limit?limit:j+1-Math.log2(Math.log2(Math.hypot(a,b)));
}
// Newton for z^degree - 1: ((degree-1)z + 1/z^(degree-1))/degree.
// Singular / unconverged samples remain unassigned, never a fabricated root.
export function newtonValue(a,b,degree,limit=80){
  let previous=1;
  for(let j=0;j<limit;j++){
    let u=1,v=0;for(let k=0;k<degree-1;k++){const next=u*a-v*b;v=u*b+v*a;u=next;}
    const fr=u*a-v*b-1,fi=u*b+v*a,error=fr*fr+fi*fi;
    if(error<1e-12){
      const root=(Math.round(Math.atan2(b,a)*degree/(2*Math.PI))+degree)%degree;
      const fraction=j?Math.max(0,Math.min(1,(Math.log(previous)+12*Math.LN10)/(Math.log(previous)-Math.log(Math.max(error,1e-300))))):0;
      return {root,value:Math.max(0,j-1+fraction)};
    }
    const norm=u*u+v*v;if(norm<1e-30||!Number.isFinite(norm))break;
    previous=error;a=((degree-1)*a+u/norm)/degree;b=((degree-1)*b-v/norm)/degree;
  }
  return {root:-1,value:limit};
}
// Shared grid-edge identities stitch marching-squares cells into complete runs.
// The asymptotic decider resolves four-crossing saddles without arbitrary joins.
export function* contourField(values,n,levels,roleFor,guard,{mask=null,basin=0,minPoints=2,retain=true}={}){
  const at=i=>!mask||mask[i]===basin?values[i]:1e6;
  const cells=levels.map(()=>[]);let indexed=0;
  for(let y=0;y<n;y++){
    for(let x=0;x<n;x++){
      const i=y*(n+1)+x,a=at(i),b=at(i+1),c=at(i+n+2),d=at(i+n+1),lo=Math.min(a,b,c,d),hi=Math.max(a,b,c,d);
      for(let l=0;l<levels.length;l++)if(levels[l]>=lo&&levels[l]<hi){cells[l].push(i);indexed++;}
    }
    if(y%32===0){guard(0,indexed/4);yield {fraction:.2*y/n,label:'Locating contours'};}
  }
  const output=[];let points=0,pathCount=0;
  for(let l=0;l<levels.length;l++){
    const level=levels[l],edges=[],nodes=new Map(),coord=new Map();
    const vertex=(x,y,edge,v)=>{
      const corners=[[x,y],[x+1,y],[x+1,y+1],[x,y+1]],a=corners[edge],b=corners[(edge+1)%4];
      const key=edge===0?`h${x},${y}`:edge===1?`v${x+1},${y}`:edge===2?`h${x},${y+1}`:`v${x},${y}`;
      if(!coord.has(key)){// A basin discontinuity meets at the cell edge midpoint.
        const t=v[edge]===1e6||v[(edge+1)%4]===1e6?.5:(level-v[edge])/(v[(edge+1)%4]-v[edge]);
        coord.set(key,[(a[0]+(b[0]-a[0])*t)/n*2-1,(a[1]+(b[1]-a[1])*t)/n*2-1]);
      }return key;
    };
    let work=0;
    for(const i of cells[l]){
      const y=Math.floor(i/(n+1)),x=i-y*(n+1),v=[at(i),at(i+1),at(i+n+2),at(i+n+1)],cross=[];
      for(let e=0;e<4;e++)if((v[e]>level)!==(v[(e+1)%4]>level))cross.push(e);
      let pairs=[];
      if(cross.length===2)pairs=[cross];
      else if(cross.length===4){const q=(v[0]-level)*(v[2]-level)-(v[1]-level)*(v[3]-level);pairs=q>=0?[[0,1],[2,3]]:[[0,3],[1,2]];}
      for(const pair of pairs){const ends=pair.map(e=>vertex(x,y,e,v)),index=edges.length;edges.push(ends);for(const key of ends){if(!nodes.has(key))nodes.set(key,[]);nodes.get(key).push(index);}}
      if(++work%8192===0){guard(retain?pathCount:0,(retain?points:0)+edges.length*3);yield {fraction:.2+.8*(l+work/cells[l].length*.8)/levels.length,label:'Tracing contours'};}
    }
    const used=new Set(),batch=[];
    const follow=(key,index)=>{const pts=[coord.get(key)];while(index!==undefined&&!used.has(index)){used.add(index);const ends=edges[index];key=ends[0]===key?ends[1]:ends[0];pts.push(coord.get(key));index=nodes.get(key).find(e=>!used.has(e));}if(pts.length>=minPoints){const path={points:pts,role:roleFor(l)};batch.push(path);if(retain)output.push(path);pathCount++;points+=pts.length;}};
    for(const [key,links] of nodes)if(links.length===1&&!used.has(links[0]))follow(key,links[0]);
    edges.forEach((ends,i)=>{if(!used.has(i))follow(ends[0],i);});cells[l]=null;guard(retain?pathCount:batch.length,retain?points:batch.reduce((sum,p)=>sum+p.points.length,0));
    yield {fraction:.2+.8*(l+1)/levels.length,label:'Joining contours',batch,paths:pathCount,points};
  }
  return output;
}
function uniqueRuns(paths,seen){
  const output=[];
  for(const path of paths){
    let run=[];
    const finish=()=>{if(run.length>1)output.push({points:run,role:path.role});run=[];};
    for(let i=1;i<path.points.length;i++){
      const a=path.points[i-1],b=path.points[i],ends=[a,b].map(p=>p.map(v=>v.toFixed(10)).join(','));
      const key=ends.sort().join('/');if(seen.has(key)){finish();continue;}
      seen.add(key);if(!run.length)run.push(a);run.push(b);
    }finish();
  }return output;
}
// A bounded raster proximity index, used only to select whole vector contours.
// No raster pixels enter the drawing. Prefer the earlier, coarser contours;
// skip tiny loops and contours whose length is >=90% crowded by existing ink.
// Conservative cell-error allowance avoids calling a near miss an overlap.
export class NewtonInkFilter {
  constructor(n,widths){
    this.n=n;this.cell=2/n;this.widths=widths;this.maxRadius=Math.max(...widths)/2;
    this.clearance=new Float32Array((n+1)**2);this.clearance.fill(-1e6);this.omitted=0;
  }
  accept(path){
    const {n,cell,widths,clearance}=this,width=widths[path.role],radius=width/2;
    let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity,total=0,crowded=0;
    for(const [x,y] of path.points){x0=Math.min(x0,x);x1=Math.max(x1,x);y0=Math.min(y0,y);y1=Math.max(y1,y);}
    if(Math.max(x1-x0,y1-y0)<width*1.5){this.omitted++;return null;}
    for(let i=1;i<path.points.length;i++){
      const a=path.points[i-1],b=path.points[i],dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy),count=Math.max(1,Math.ceil(length/cell));
      for(let j=0;j<count;j++){
        const t=(j+.5)/count,x=Math.max(0,Math.min(n,Math.round((a[0]+dx*t+1)/cell))),y=Math.max(0,Math.min(n,Math.round((a[1]+dy*t+1)/cell)));
        total+=length/count;
        if(clearance[y*(n+1)+x]>-radius+width*.25+cell*Math.SQRT2/2)crowded+=length/count;
      }
    }
    if(!total||crowded>=total*.9){this.omitted++;return null;}
    // Linear-time decimation; each skipped point is within 1/8 pen diameter
    // of its preceding retained point, which also lies on the replacement run.
    const points=[path.points[0]],tolerance=width/8;
    for(let i=1;i<path.points.length-1;i++)if(Math.hypot(path.points[i][0]-points.at(-1)[0],path.points[i][1]-points.at(-1)[1])>=tolerance)points.push(path.points[i]);
    points.push(path.points.at(-1));
    const reach=radius+this.maxRadius;
    for(let i=1;i<points.length;i++){
      const a=points[i-1],b=points[i],dx=b[0]-a[0],dy=b[1]-a[1],length2=dx*dx+dy*dy;
      const left=Math.max(0,Math.ceil((Math.min(a[0],b[0])-reach+1)/cell)),right=Math.min(n,Math.floor((Math.max(a[0],b[0])+reach+1)/cell));
      const top=Math.max(0,Math.ceil((Math.min(a[1],b[1])-reach+1)/cell)),bottom=Math.min(n,Math.floor((Math.max(a[1],b[1])+reach+1)/cell));
      for(let y=top;y<=bottom;y++)for(let x=left;x<=right;x++){
        const px=x*cell-1,py=y*cell-1,t=length2?Math.max(0,Math.min(1,((px-a[0])*dx+(py-a[1])*dy)/length2)):0;
        const value=radius-Math.hypot(px-a[0]-dx*t,py-a[1]-dy*t),index=y*(n+1)+x;
        if(value>clearance[index])clearance[index]=value;
      }
    }
    return {...path,points};
  }
}
export function* complexContours(family,p,guard){
  const n=p.resolution||p.detail||520,v=complexWindow(family,p),values=new Float32Array((n+1)**2),mask=family==='newton'?new Int8Array(values.length):null;
  const c=p.c||juliaShapes[p.shape]||[0,0],direction=imDirection(family);
  const limit=family==='newton'?p.depth+2:Math.min(2048,(family==='julia'?110:256)+Math.ceil(Math.max(0,Math.log2((family==='julia'?3.3:3.4)/v.span)))*64),samples=[];
  for(let y=0;y<=n;y++){
    for(let x=0;x<=n;x++){
      const i=y*(n+1)+x,real=v.real+(x/n*2-1)*v.span/2,imag=v.imag+direction*(y/n*2-1)*v.span/2;
      if(mask){const result=newtonValue(real,imag,p.roots,limit);values[i]=result.value;mask[i]=result.root;}
      else {values[i]=escapeValue(family,real,imag,limit,c);if(x%16===0&&y%16===0&&values[i]<limit)samples.push(values[i]);}
    }
    if(y%8===0)yield {fraction:y/n*.6,label:mask?'Calculating attracting roots':'Calculating escape field'};
  }
  let first=family==='newton'?2:2.7,spacing=p.spacing||(family==='julia'?1.35:1.25);
  if(family!=='newton'&&(p.viewport||p.location>0)&&samples.length){
    samples.sort((a,b)=>a-b);const low=samples[Math.floor(samples.length*.1)],high=samples[Math.floor(samples.length*.9)];
    // Blend into adaptive levels as the view magnifies. Merely starting a
    // pan at the overview scale must not replace every contour level.
    const overview=family==='julia'?3.3:family==='burning_ship'?3.5:3.4;
    const blend=Math.max(0,Math.min(1,Math.log2(overview/v.span)));
    first+=(low-first)*blend;
    spacing+=(Math.max(.1,(high-low)/(p.contours-1))*(p.spacing||(family==='julia'?1.35:1.25))/1.25-spacing)*blend;
  }
  const levels=Array.from({length:mask?Math.floor((p.depth-2)/spacing)+1:p.contours},(_,i)=>first+i*spacing),output=[];let points=0;
  if(mask&&levels.at(-1)<p.depth-1e-8)levels.push(p.depth);
  const basins=mask?p.roots:1,seen=new Set(),bandShare=.4,inkFilter=mask&&p.inkWidths?new NewtonInkFilter(n,p.inkWidths):null;
  for(let root=0;root<basins;root++){
    const role=l=>mask?root:p.inkCount?l%p.inkCount:Math.min(2,Math.floor(l*3/p.contours));
    const steps=contourField(values,n,levels,role,(paths,pts)=>guard(output.length+paths,points+pts),{mask,basin:root,minPoints:family==='julia'?4:2,retain:!mask});let step;
    while(!(step=steps.next()).done){
      const q=step.value,selected=q.batch?.map(path=>inkFilter?inkFilter.accept(path):path).filter(Boolean);
      const batch=selected?(mask?uniqueRuns(selected,seen):selected):undefined;
      if(batch)for(const path of batch){output.push(path);points+=path.points.length;}
      guard(output.length,points);
      yield {...q,batch,fraction:.6+bandShare*(root+q.fraction)/basins,paths:output.length,points};
    }
  }
  if(inkFilter)p.inkStats={omitted_contours:inkFilter.omitted};
  return output;
}
