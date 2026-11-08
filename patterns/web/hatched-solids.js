// Convex polyhedra with face-following hatches. Back faces are removed before
// drawing; copies have disjoint projected envelopes. No general scene renderer.
import {project3} from './natural.js';
import {joinEdges} from './spatial.js';
const knob=(key,label,value,min,max,step=1,choices)=>({key,label,value,min,max,step,...(choices?{choices}:{})});
export const solidFamily={id:'hatched_solids',name:'Hatched polyhedra',roles:['Edges','Light faces','Shaded faces'],knobs:[knob('shape','Solid',2,0,2,1,['Cube','Octahedron','Icosahedron']),knob('density','Hatch density',30,10,60,5),knob('angle','Hatch angle',30,-60,60,15),knob('cross','Dark faces',1,0,1,1,['Single hatch','Crosshatch']),knob('azimuth','Azimuth',35,5,85,10),knob('elevation','Elevation',25,5,75,10),knob('copies','Copies',1,1,3)]};
const sub=(a,b)=>a.map((v,i)=>v-b[i]),dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0),cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],unit=v=>{const n=Math.hypot(...v);return v.map(x=>x/n);};
export function solidVertices(shape){
 let pts=[];
 if(shape===0)pts=Array.from({length:8},(_,i)=>[i&1?1:-1,i&2?1:-1,i&4?1:-1]);
 else if(shape===1)for(let axis=0;axis<3;axis++)for(const side of [-1,1]){const p=[0,0,0];p[axis]=side;pts.push(p);}
 else{const phi=(1+Math.sqrt(5))/2;for(let axis=0;axis<3;axis++)for(const a of [-1,1])for(const b of [-phi,phi]){const p=[0,0,0];p[(axis+1)%3]=a;p[(axis+2)%3]=b;pts.push(p);}}
 return pts.map(unit);
}
export function solidFaces(shape){
 const vertices=solidVertices(shape),faces=[],seen=new Set();
 for(let i=0;i<vertices.length;i++)for(let j=i+1;j<vertices.length;j++)for(let k=j+1;k<vertices.length;k++){
  let normal=cross(sub(vertices[j],vertices[i]),sub(vertices[k],vertices[i]));if(Math.hypot(...normal)<1e-10)continue;normal=unit(normal);
  const d=vertices.map(v=>dot(normal,sub(v,vertices[i])));if(d.some(x=>x>1e-9)&&d.some(x=>x< -1e-9))continue;
  if(dot(normal,vertices[i])<0)normal=normal.map(v=>-v);
  const ids=d.map((x,i)=>Math.abs(x)<1e-9?i:-1).filter(i=>i>=0),key=ids.join(',');if(seen.has(key))continue;seen.add(key);
  const center=[0,1,2].map(a=>ids.reduce((s,i)=>s+vertices[i][a],0)/ids.length),u=unit(sub(vertices[ids[0]],center)),v=cross(normal,u);
  ids.sort((a,b)=>Math.atan2(dot(sub(vertices[a],center),v),dot(sub(vertices[a],center),u))-Math.atan2(dot(sub(vertices[b],center),v),dot(sub(vertices[b],center),u)));
  faces.push({ids,points:ids.map(i=>vertices[i]),normal,center,u,v});
 }return {vertices,faces};
}
export function solidFrame(p){const w=2+2.25*(p.copies-1);return [-w/2,-1,w,2];}
export function insetSegment(a,b,polygon,inset){
 const area=polygon.reduce((s,p,i)=>s+p[0]*polygon[(i+1)%polygon.length][1]-p[1]*polygon[(i+1)%polygon.length][0],0),sign=Math.sign(area);if(Math.abs(area)<1e-12)return null;
 let lo=0,hi=1;
 for(let i=0;i<polygon.length;i++){
  const c=polygon[i],d=polygon[(i+1)%polygon.length],dx=d[0]-c[0],dy=d[1]-c[1],length=Math.hypot(dx,dy),value=p=>sign*(dx*(p[1]-c[1])-dy*(p[0]-c[0]))/length-inset,f0=value(a),f1=value(b);
  if(Math.abs(f1-f0)<1e-14){if(f0<0)return null;continue;}const t=-f0/(f1-f0);if(f1>f0)lo=Math.max(lo,t);else hi=Math.min(hi,t);if(hi-lo<1e-10)return null;
 }return [lo,hi];
}
export function* hatchedSolidGeometry(p){
 const {faces}=solidFaces(p.shape),edges=[],hatches=[],seen=new Set(),widths=p.inkWidths||[.004,.004,.004],light=unit([-.6,-.8,1]);
 for(let copy=0;copy<p.copies;copy++){
  const azimuth=p.azimuth+copy*25,offset=2.25*(copy-(p.copies-1)/2),project=pt=>{const q=project3(pt,azimuth,p.elevation);q[0]+=offset;return q;};
  for(let fi=0;fi<faces.length;fi++){
   const f=faces[fi],normal=project3(f.normal,azimuth,p.elevation);if(normal[2]<=1e-9)continue;
   const polygon=f.points.map(project),lightness=Math.max(0,dot(normal,light)),role=lightness>.55?1:2;
   for(let i=0;i<f.ids.length;i++){const id=[copy,...[f.ids[i],f.ids[(i+1)%f.ids.length]].sort((a,b)=>a-b)].join(',');if(!seen.has(id)){seen.add(id);edges.push({role:0,points:[polygon[i].slice(0,2),polygon[(i+1)%f.ids.length].slice(0,2)]});}}
   const spacing=2/(p.density*(.3+.7*(1-lightness))),inset=(widths[0]+widths[role])/2;
   for(let pass=0;pass<(p.cross&&role===2?2:1);pass++){
    const angle=p.angle*Math.PI/180+pass*Math.PI/2,u=f.u.map((x,i)=>Math.cos(angle)*x+Math.sin(angle)*f.v[i]),v=cross(f.normal,u);
    for(let line=-Math.ceil(1/spacing);line<=Math.ceil(1/spacing);line++){
     const ends=[-2,2].map(t=>project(f.center.map((x,i)=>x+u[i]*t+v[i]*line*spacing))),range=insetSegment(...ends,polygon,inset);if(!range)continue;
     const points=range.map(t=>ends[0].map((x,i)=>x+t*(ends[1][i]-x)).slice(0,2));
     hatches.push({role,points,face:fi,copy,polygon:polygon.map(p=>p.slice(0,2)),inset,range});
    }
   }
  }
  yield {fraction:.4*(copy+1)/p.copies,label:'Shading visible polyhedron faces'};
 }return {edges,hatches};
}
export function* hatched_solids(p,guard){
 const {edges,hatches}=yield* hatchedSolidGeometry(p),paths=[...joinEdges(edges),...hatches.map(({role,points})=>({role,points}))];let points=0;
 for(let i=0;i<paths.length;i+=64){const batch=paths.slice(i,i+64);points+=batch.reduce((s,p)=>s+p.points.length,0);const sent=i+batch.length;guard(sent,points);yield {fraction:.4+.6*sent/paths.length,label:'Clipping finite-width hatch ends',batch,paths:sent,points};}return paths;
}
