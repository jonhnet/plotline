import test from 'node:test';
import assert from 'node:assert/strict';
import {families,defaults,generate,fitPaths,mengerOccupied,mengerVisible,fieldSampling,generateSteps,checkGeometrySize,limits,loadLimits} from '../patterns/web/generators.js';
import {adjacentFamilies,subdivideGoldenTriangles} from '../patterns/web/adjacent.js';
import {naturalFamilies,flowDerivative,rk4,knotCenter,capsuleInterval} from '../patterns/web/natural.js';

await loadLimits();

const recipe={media:{sheet_size_mm:[431.8,279.4],printable_rect_mm:[39.425,11,376.95,253.6],content_inset_mm:1},inks:[{width_mm:.7}],size:.96,rotation:0};
function audit(family,params){
  const geometry=generate(family.id,params),fitted=fitPaths(geometry,recipe),roles=new Set();
  assert(geometry.points>(family.id==='menger'?50:100),'Preset remains a substantial drawing');
  assert(checkGeometrySize(geometry.paths.length,geometry.points)<=limits.working_memory_bytes);
  for(const p of fitted.paths){assert(p.points.length>=2);assert(p.role>=0&&p.role<family.roles.length);roles.add(p.role);for(const [x,y] of p.points){assert(Number.isFinite(x)&&Number.isFinite(y));assert(x>=40.775-1e-4&&x<=415.025+1e-4&&y>=12.35-1e-4&&y<=263.25+1e-4,'Whole pen remains inside printable area');}}
  assert(roles.size>=((['penrose','ammann','pinwheel'].includes(family.id)&&params.insets===0)||(['wave_contours','reaction'].includes(family.id)&&params.bands===1)?1:2),'Preset uses multiple ink roles unless motifs are explicitly off');return geometry;
}
for(const f of families)test(f.name+': default and every neighboring knob setting are finite, bounded and different',()=>{
  const p=defaults(f.id),base=audit(f,p),serial=JSON.stringify(base.paths);
  assert.equal(JSON.stringify(generate(f.id,p).paths),serial,'Seeded regeneration is identical');
  for(const k of f.knobs)for(const direction of [-1,1]){
    const value=Number((p[k.key]+direction*k.step).toFixed(6));if(value<k.min||value>k.max)continue;
    const result=audit(f,{...p,[k.key]:value});assert.notEqual(JSON.stringify(result.paths),serial,k.key+' changes the drawing');
  }
});
test('Known combinatorics, closed circles, joined contours and continuous Hilbert sections',()=>{
  const snow=generate('koch',defaults('koch'));assert.equal(snow.paths[0].points.length,3*4**4+1);
  const gasket=generate('sierpinski',defaults('sierpinski'));assert.equal(gasket.paths.length,1+(3**6-1)/2);
  for(const p of generate('apollonian',defaults('apollonian')).paths)assert(Math.hypot(...p.points[0].map((v,i)=>v-p.points.at(-1)[i]))<1e-10);
  for(const p of generate('julia',defaults('julia')).paths)assert.deepEqual(p.points[0],p.points.at(-1),'Default Julia contours are closed');
  const hilbert=generate('hilbert',defaults('hilbert')).paths;for(let i=1;i<hilbert.length;i++)assert.deepEqual(hilbert[i-1].points.at(-1),hilbert[i].points[0]);
});
test('Sponge voxel traversal agrees with an independent box-intersection oracle',()=>{
  const n=9,view=[.7,.5,.3];
  const boxes=[];for(let x=0;x<n;x++)for(let y=0;y<n;y++)for(let z=0;z<n;z++)if(mengerOccupied(x,y,z,n))boxes.push([x,y,z]);
  assert.equal(boxes.length,400);
  function oracle(point){const p=point.map((v,i)=>v+view[i]*1e-6);return !boxes.some(box=>{let near=0,far=Infinity;for(let i=0;i<3;i++){near=Math.max(near,(box[i]-p[i])/view[i]);far=Math.min(far,(box[i]+1-p[i])/view[i]);}return far>Math.max(near,0)+1e-9;});}
  for(let i=0;i<300;i++){const point=[(i*37%91)/10,(i*19%91)/10,(i*61%91)/10];assert.equal(mengerVisible(point,view,n),oracle(point));}
});
test('Invalid recursion and nonfinite parameters fail before expansion',()=>{
  assert.throws(()=>generate('koch',{...defaults('koch'),depth:30}),/Invalid/);
  assert.throws(()=>generate('plant',{...defaults('plant'),angle:NaN}),/Invalid/);
});

test('Seven/eight-pen sequences cover real generations, retain geometry and allow a single ink',()=>{
  for(const family of ['koch','dragon','hilbert','sierpinski','julia'])for(const inkCount of [1,7,8]){
    const params={...defaults(family),...(family==='sierpinski'?{depth:7}:{})};
    const geometry=generate(family,params,()=>{},{inkCount});
    const roles=new Set(geometry.paths.map(p=>p.role));
    assert.equal(roles.size,Math.min(inkCount,family==='sierpinski'?7:Infinity));
    assert(geometry.paths.every(p=>p.role>=0&&p.role<inkCount));
    if(family==='sierpinski')assert.equal(geometry.paths.at(-1).role,6%inkCount,'Deepest opening follows generation, not a misleading Small label');
    if(family==='koch'||family==='sierpinski')assert.deepEqual(geometry.paths.map(p=>p.points),generate(family,params).paths.map(p=>p.points));
  }
});
test('Julia automatic sampling follows physical size and narrowest pen with an honest cap',()=>{
  const g=generate('julia',defaults('julia'));
  const r={...recipe,inks:[{width_mm:1},{width_mm:.7}]};
  const q=fieldSampling(g,r);assert(!q.limited);assert(q.cell_mm<=.35);assert.equal(q.target_mm,.35);
  const finer=fieldSampling(g,{...r,inks:[{width_mm:.35}]});assert(finer.resolution>=q.resolution);
  const huge=fieldSampling(g,{...r,media:{...r.media,printable_rect_mm:[0,0,1500,1500]},inks:[{width_mm:.1}]});assert(huge.limited);assert.equal(huge.resolution,3072);assert(huge.cell_mm>huge.target_mm);
  const small=fieldSampling(g,{...r,size:.48});assert(small.resolution<q.resolution);
});
test('Menger level four fits the export budget and reports frequent progress',()=>{
  const updates=[],g=generate('menger',{...defaults('menger'),depth:4},f=>updates.push(f));
  assert(g.paths.length>15000&&g.paths.length<50000);assert(g.points<=400000);
  assert(updates.length>200);assert(updates.every((f,i)=>!i||f>=updates[i-1]));
  assert.throws(()=>generate('menger',{...defaults('menger'),depth:6}),/Invalid Detail/);
});

test('Old count ceilings are not limits; the shared guard explains estimated memory',()=>{
  assert(checkGeometrySize(178316,356632)<limits.working_memory_bytes);
  assert(checkGeometrySize(1,800000)<limits.working_memory_bytes);
  assert.throws(()=>checkGeometrySize(1000000,2000000),/MiB of working memory/);
  const large=generate('koch',{...defaults('koch'),depth:7,layers:13});
  assert(large.points>400000);assert(large.paths.length===13);
});
test('Incremental geometry batches are lossless and progress is monotone',()=>{
  for(const family of ['menger','julia',...adjacentFamilies.map(f=>f.id),...naturalFamilies.map(f=>f.id)]){
    const steps=generateSteps(family,defaults(family)),paths=[];let done,previous=0,progress=0;
    while(!(done=steps.next()).done){const value=done.value;assert(value.fraction>=previous);previous=value.fraction;progress++;for(const p of value.batch||[])paths.push(p);}
    assert(progress>=(family==='harmonograph'?3:6));assert.deepEqual(paths,done.value.paths);assert.deepEqual(done.value,generate(family,defaults(family)));
  }
});

test('New ink sequences preserve curves, close rolling/oscillation paths, and join temporal sections',()=>{
  for(const family of adjacentFamilies.filter(f=>f.sequence))for(const inkCount of [1,7,8]){
    const params=defaults(family.id),g=generate(family.id,params,()=>{},{inkCount});
    const roles=new Set(g.paths.map(p=>p.role));assert.equal(roles.size,Math.min(inkCount,params.layers||Infinity));
    assert(g.paths.every(p=>p.role<inkCount));
    if(['spirograph','lissajous','phyllotaxis'].includes(family.id)){
      for(const p of g.paths)assert.deepEqual(p.points[0],p.points.at(-1),'Explicit closure has no final seam');
      assert.deepEqual(g.paths.map(p=>p.points),generate(family.id,params).paths.map(p=>p.points),'Ink selection only changes attribution');
    }
    if(family.id==='harmonograph')for(let i=1;i<g.paths.length;i++)assert.deepEqual(g.paths[i-1].points.at(-1),g.paths[i].points[0],'Color changes share an endpoint');
  }
});
test('Golden triangle subdivision conserves area, stays inside its parent, and retains the two Robinson shapes',()=>{
  const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
  const area=t=>Math.abs(cross(t.a,t.b,t.c))/2;
  let tris=[{kind:0,a:[0,0],b:[Math.cos(Math.PI/10),-Math.sin(Math.PI/10)],c:[Math.cos(Math.PI/10),Math.sin(Math.PI/10)]}];
  for(let depth=0;depth<6;depth++){
    for(const parent of tris){
      const children=subdivideGoldenTriangles([parent]);assert.equal(children.length,parent.kind===0?2:3);
      assert(Math.abs(children.reduce((s,t)=>s+area(t),0)-area(parent))<1e-12);
      for(const child of children){
        const u=child.b.map((v,i)=>v-child.a[i]),v=child.c.map((v,i)=>v-child.a[i]),ab=Math.hypot(...u),ac=Math.hypot(...v);
        assert(Math.abs(ab-ac)<1e-12,'Both equal legs remain equal');
        assert(Math.abs((u[0]*v[0]+u[1]*v[1])/(ab*ac)-Math.cos((child.kind===0?36:108)*Math.PI/180))<1e-10);
        for(const pt of [child.a,child.b,child.c]){
          const portions=[cross(parent.a,parent.b,pt),cross(parent.b,parent.c,pt),cross(parent.c,parent.a,pt)];
          assert(portions.every(v=>v>=-1e-12)||portions.every(v=>v<=1e-12),'Subdivisions remain inside the parent');
        }
      }
    }
    tris=subdivideGoldenTriangles(tris);
  }
});
test('Penrose emits shared edges once and paired thin/thick rhomb motifs',()=>{
  for(const patch of [0,1,2]){
    const g=generate('penrose',{...defaults('penrose'),patch}),edges=g.paths.filter(p=>p.role===0);
    const keys=edges.map(p=>p.points.map(pt=>pt.map(v=>v.toFixed(9)).join(',')).sort().join('/'));
    assert.equal(new Set(keys).size,keys.length,'No doubled shared edges');
    const rhombs=g.paths.filter(p=>p.role>0&&p.points.length===5);assert(rhombs.length>20);
    for(const p of rhombs){
      assert.deepEqual(p.points[0],p.points.at(-1));
      const lengths=p.points.slice(1).map((v,i)=>Math.hypot(...v.map((x,j)=>x-p.points[i][j])));
      assert(Math.max(...lengths)-Math.min(...lengths)<1e-10,'Paired halves form actual rhombs');
    }
  }
});
test('Truchet joins every arc exactly once; open strands end only at the patch boundary',()=>{
  const p=defaults('truchet'),g=generate('truchet',p),arcs=2*p.columns*p.rows*p.bands;
  assert.equal(g.paths.reduce((n,p)=>n+p.points.length-1,0),arcs*24,'Joining preserves every arc segment');
  assert(g.paths.length<arcs/2,'Tile seams do not cause pen lifts');
  const edge=([x,y])=>Math.min(Math.abs(x),Math.abs(y),Math.abs(x-p.columns),Math.abs(y-p.rows))<1e-9;
  for(const p of g.paths)if(Math.hypot(...p.points[0].map((v,i)=>v-p.points.at(-1)[i]))>1e-9)assert(edge(p.points[0])&&edge(p.points.at(-1)),'No broken interior strands');
});

// Independent mathematical and placement checks for the complex-plane tools.
const {complexFamilies,complexWindow,complexPoint,navigateComplex,escapeValue,newtonValue,contourField,NewtonInkFilter,newtonParams}=await import('../patterns/web/complex.js');
test('Complex formulas agree with direct iteration and known roots',()=>{
  function oracle(family,c,limit=256){let a=0,b=0,j=0;while(a*a+b*b<256&&j<limit){const u=a*a-b*b+c[0],v=(family==='burning_ship'?2*Math.abs(a)*Math.abs(b):2*a*b)+c[1];a=u;b=v;j++;}return j===limit?limit:j+1-Math.log2(Math.log2(Math.hypot(a,b)));}
  for(const family of ['mandelbrot','burning_ship'])for(const c of [[0,0],[-1,0],[.5,.4],[-.75,.1],[-1.76,-.04],[-2,.5],[2,2]])assert(Math.abs(escapeValue(family,...c)-oracle(family,c))<1e-10);
  assert.equal(escapeValue('mandelbrot',0,0),256);assert.equal(escapeValue('julia',0,0,110,[0,0]),110);
  for(let degree=3;degree<=7;degree++){
    assert.equal(newtonValue(0,0,degree).root,-1,'The derivative singularity is unassigned');
    for(let root=0;root<degree;root++){
      const theta=2*Math.PI*root/degree;
      assert.equal(newtonValue(Math.cos(theta),Math.sin(theta),degree).root,root);
      assert.equal(newtonValue(1.08*Math.cos(theta),1.08*Math.sin(theta),degree).root,root);
    }
  }
});
test('Contour joins preserve analytic lines, short clipped runs and empty windows',()=>{
  const n=24,values=Float32Array.from({length:(n+1)**2},(_,i)=>(i%(n+1)+Math.floor(i/(n+1)))/n),steps=contourField(values,n,[.23,.91,1.77],i=>i,()=>{}),paths=[];
  for(const q of steps)paths.push(...q.batch||[]);
  assert.equal(paths.length,3);
  for(const p of paths)for(const [x,y] of p.points)assert(Math.abs((x+y+2)/2-[.23,.91,1.77][p.role])<1e-6);
  const empty=generate('mandelbrot',{...defaults('mandelbrot'),viewport:{real:0,imag:0,span:.01}});
  assert.equal(empty.paths.length,0);assert.deepEqual(empty.frame,[-1,-1,2,2]);assert(fitPaths(empty,recipe).bounds.every(Number.isFinite));
});
test('Depth-limited Newton contours never retrace an identical segment',()=>{
  const g=generate('newton',defaults('newton')),seen=new Set();
  for(const p of g.paths)for(let i=1;i<p.points.length;i++){
    const key=[p.points[i-1],p.points[i]].map(pt=>pt.map(v=>v.toFixed(10)).join(',')).sort().join('/');assert(!seen.has(key),'No duplicate boundary between roots or contour levels');seen.add(key);
  }
});
test('Complex navigation preserves the zoom anchor and uses a fixed paper window',()=>{
  for(const family of ['mandelbrot','burning_ship','newton','julia']){
    const params=defaults(family),anchor=[.25,-.4],before=complexPoint(family,params,anchor);
    const viewport=navigateComplex(family,params,{anchor,factor:2});
    const after=complexPoint(family,{...params,viewport},anchor);assert(after.every((v,i)=>Math.abs(v-before[i])<1e-12));
    const panned=navigateComplex(family,params,{delta:[.2,-.3]});
    assert(complexPoint(family,{...params,viewport:panned},[.2,-.3]).every((v,i)=>Math.abs(v-complexPoint(family,params,[0,0])[i])<1e-12));
    assert.equal(navigateComplex(family,params,{factor:1e30}).span,1e-10);
    for(const v of [{real:NaN,imag:0,span:1},{real:0,imag:0,span:1e-12},{real:101,imag:0,span:1}])assert.throws(()=>generate(family,{...params,viewport:v}),/Invalid complex-plane/);
  }
  const a=generate('mandelbrot',defaults('mandelbrot')),b=generate('mandelbrot',{...defaults('mandelbrot'),viewport:{real:-.75,imag:.1,span:.2}});
  for(const rotation of [0,45,90,180,270])assert.deepEqual(fitPaths(a,{...recipe,rotation}).bounds,fitPaths(b,{...recipe,rotation}).bounds,'Navigation does not recenter the ink bounds');
});
test('Every complex family delivers lossless monotone batches and honors explicit Julia parameters',()=>{
  for(const f of complexFamilies){
    const steps=generateSteps(f.id,defaults(f.id)),paths=[];let q,previous=0;
    while(!(q=steps.next()).done){assert(q.value.fraction>=previous);previous=q.value.fraction;paths.push(...q.value.batch||[]);}
    assert.deepEqual(paths,q.value.paths);
  }
  for(const family of ['mandelbrot','burning_ship','julia']){const p=defaults(family);assert.deepEqual(generate(family,{...p,viewport:complexWindow(family,p)}).paths,generate(family,p).paths,'Starting exploration does not change contour levels');}
  const c=[-.8,.156];assert.deepEqual(generate('julia',{...defaults('julia'),c}).paths,generate('julia',defaults('julia')).paths);
  assert.notDeepEqual(generate('julia',{...defaults('julia'),c:[-.7,.27]}).paths,generate('julia',defaults('julia')).paths);
});

test('Newton depth bounds detail while resolution only refines the selected shapes',()=>{
  const p=defaults('newton');assert.equal(p.depth,8);assert.equal(p.contours,undefined);
  let previous=0;
  for(const depth of [4,6,8,10,12]){
    const g=generate('newton',{...p,depth},()=>{},{resolution:560});assert(g.points>previous);previous=g.points;
    assert.equal(new Set(g.paths.map(p=>p.role)).size,p.roots);
  }
  const g=generate('newton',p,()=>{},{resolution:2240});
  assert(g.paths.length<5000&&g.points<180000,'Default refinement does not append the former 149,000-path boundary pass');
  const low=fitPaths(generate('newton',p,()=>{},{resolution:560}),recipe),high=fitPaths(g,recipe);
  assert(high.length_mm/low.length_mm<1.2,'Higher sampling accuracy does not expose another fractal depth');
  assert.deepEqual(newtonParams({roots:5,contours:8,spacing:.75}),{roots:5,depth:7,spacing:.75});
  assert.deepEqual(generate('newton',{roots:5,contours:8,spacing:.75}).paths,generate('newton',{roots:5,depth:7,spacing:.75}).paths,'Legacy recipes receive an explicit finite depth');
});
test('Newton pen spacing suppresses crowded whole contours, preserves useful curves and responds to each role width',()=>{
  const line=(y,role=0)=>({role,points:[[-.8,y],[.8,y]]});
  const filter=new NewtonInkFilter(1000,[.02,.04]);
  assert(filter.accept(line(0)));assert.equal(filter.accept(line(.001,1)),null,'A nearly coincident other-color contour is redundant');
  assert(filter.accept(line(.08,1)),'A separated line is retained');
  assert(filter.accept({role:1,points:[[0,-.8],[0,.8]]}),'An isolated crossing does not remove a useful contour');
  assert.equal(filter.accept({role:0,points:[[.7,.7],[.705,.7],[.7,.705],[.7,.7]]}),null,'Sub-pen loops do not become ink blobs');
  const p={...defaults('newton'),depth:12},plain=generate('newton',p,()=>{},{resolution:560}),widths=Array(5).fill(.006);
  const filtered=generate('newton',p,()=>{},{resolution:560,inkWidths:widths});
  assert(filtered.paths.length<plain.paths.length*.5);assert(filtered.points<plain.points*.8);assert(filtered.ink_stats.omitted_contours>0);
  assert.equal(new Set(filtered.paths.map(p=>p.role)).size,5,'All roots retain their large shapes');
  const larger=generate('newton',p,()=>{},{resolution:560,inkWidths:widths.map(w=>w*2)});assert(larger.points<filtered.points,'Broader pens retain less tiny detail');
  assert.throws(()=>generate('newton',p,()=>{},{inkWidths:[.006]}),/Invalid Newton ink widths/);
  const steps=generateSteps('newton',p,{resolution:560,inkWidths:widths}),paths=[];let q;
  while(!(q=steps.next()).done)paths.push(...q.value.batch||[]);
  assert.deepEqual(paths,filtered.paths,'Progressive batches contain exactly the final selected contours');
});

test('Continuous attractors integrate the published fields and retain every color-transition endpoint',()=>{
  assert.deepEqual(flowDerivative('lorenz',{rho:28},[1,2,3]),[10,23,-6]);
  assert(flowDerivative('rossler',{c:5.7},[1,2,3]).every((v,i)=>Math.abs(v-[-5,1.4,-13.9][i])<1e-12));
  let a=[1,2,3];for(let i=0;i<10;i++)a=rk4(a,.1,p=>p);assert(a.every((v,i)=>Math.abs(v/(i+1)-Math.E)<3e-6),'RK4 agrees with an independent analytic exponential');
  for(const family of ['lorenz','rossler']){
    const p=defaults(family),field=x=>flowDerivative(family,p,x);let coarse=[1,1,1],fine=coarse;
    for(let i=0;i<200;i++)coarse=rk4(coarse,.005,field);for(let i=0;i<1000;i++)fine=rk4(fine,.001,field);
    assert(Math.hypot(...coarse.map((v,i)=>v-fine[i]))<.0001,'Integration converges over a bounded time horizon');
    for(const inkCount of [1,7,8]){const g=generate(family,p,()=>{},{inkCount});assert.equal(new Set(g.paths.map(p=>p.role)).size,inkCount);for(let i=1;i<g.paths.length;i++)assert.deepEqual(g.paths[i-1].points.at(-1),g.paths[i].points[0]);}
  }
});
test('Fern strokes are individual affine stem images, with no chaos-game connecting lines',()=>{
  const p=defaults('barnsley'),g=generate('barnsley',p,()=>{},{inkCount:8});
  assert.equal(new Set(g.paths.map(p=>p.role)).size,8);assert(g.paths.every(p=>p.points.length===2));
  assert(g.paths[0].points.flat().every((v,i)=>Math.abs(v-[0,0,0,-1.6][i])<1e-12));
  assert.deepEqual(g.paths[1].points,[[0,-1.6],[.064,-2.96]]);
  for(const stroke of g.paths)assert(Math.hypot(...stroke.points[0].map((v,i)=>v-stroke.points[1][i]))>=p.cutoff-1e-12);
  assert(generate('barnsley',{...p,cutoff:.02}).paths.length>g.paths.length,'Smaller twigs expose additional real stems');
});
test('Crystal branches have exact rotational symmetry, unique segments and all selected inks',()=>{
  const p=defaults('crystal'),g=generate('crystal',p,()=>{},{inkCount:8}),key=pt=>pt.map(v=>v.toFixed(8).replace('-0.00000000','0.00000000')).join(','),edge=pts=>pts.map(key).sort().join('/'),edges=new Set(g.paths.map(p=>edge(p.points)));
  assert.equal(edges.size,g.paths.length,'Shared trunks are drawn once');assert.equal(new Set(g.paths.map(p=>p.role)).size,8);
  for(const s of g.paths){const rotated=s.points.map(([x,y])=>[x*Math.cos(2*Math.PI/p.arms)-y*Math.sin(2*Math.PI/p.arms),x*Math.sin(2*Math.PI/p.arms)+y*Math.cos(2*Math.PI/p.arms)]);assert(edges.has(edge(rotated)));}
});
test('Capsule clipping agrees with an independent distance oracle, including tangencies and end caps',()=>{
  const dist=(p,c,d)=>{const dx=d[0]-c[0],dy=d[1]-c[1],t=Math.max(0,Math.min(1,((p[0]-c[0])*dx+(p[1]-c[1])*dy)/(dx*dx+dy*dy||1)));return Math.hypot(p[0]-c[0]-t*dx,p[1]-c[1]-t*dy);};
  for(let i=0;i<100;i++){
    const a=[Math.sin(i)*2,Math.cos(i*2)*2],b=[Math.cos(i*3)*2,Math.sin(i*5)*2],c=[-1,0],d=i%5?[1,.3]:c,r=.2+.1*(i%4),cut=capsuleInterval(a,b,c,d,r);
    for(let j=0;j<=100;j++){const t=j/100,p=a.map((v,k)=>v+(b[k]-v)*t);assert.equal(Boolean(cut&&t>=cut[0]-1e-10&&t<=cut[1]+1e-10),dist(p,c,d)<=r+1e-10);}
  }
  assert.deepEqual(capsuleInterval([-2,1],[2,1],[-1,0],[1,0],1),[.25,.75]);
});
test('Woven knots clear the under strand at every projected center crossing and respond to pen widths',()=>{
  const p=defaults('woven'),g=generate('woven',p),center=knotCenter(p),cross=(a,b)=>a[0]*b[1]-a[1]*b[0],sub=(a,b)=>a.map((v,i)=>v-b[i]),role=4%3;
  const segments=g.paths.filter(s=>s.role===role).flatMap(s=>s.points.slice(1).map((b,i)=>[s.points[i],b]));
  function visible(index,t){const a=center[index],b=center[index+1],v=sub(b,a),norm=v[0]*v[0]+v[1]*v[1];return segments.some(([c,d])=>{if(Math.abs(cross(v,sub(c,a)))>1e-9||Math.abs(cross(v,sub(d,a)))>1e-9)return false;const u=pt=>((pt[0]-a[0])*v[0]+(pt[1]-a[1])*v[1])/norm;return t>=Math.min(u(c),u(d))-1e-8&&t<=Math.max(u(c),u(d))+1e-8;});}
  let crossings=0;
  for(let i=0;i<center.length-1;i++)for(let j=i+24;j<center.length-1;j++){
    if(j-i>center.length-24)continue;const a=center[i],b=center[i+1],c=center[j],d=center[j+1],v=sub(b,a),w=sub(d,c),den=cross(v,w);if(Math.abs(den)<1e-10)continue;
    const t=cross(sub(c,a),w)/den,u=cross(sub(c,a),v)/den;if(t<=0||t>=1||u<=0||u>=1)continue;
    const top=a[2]+t*(b[2]-a[2])>c[2]+u*(d[2]-c[2]);assert.equal(visible(i,t),top);assert.equal(visible(j,u),!top);crossings++;
  }
  assert.equal(crossings,10,'The (3,5) projection has ten proper crossings');
  const length=g=>g.paths.reduce((s,p)=>s+p.points.slice(1).reduce((s,b,i)=>s+Math.hypot(...sub(b,p.points[i])),0),0);
  const thin=generate('woven',p,()=>{},{inkCount:8,inkWidths:Array(8).fill(.004)}),fat=generate('woven',p,()=>{},{inkCount:8,inkWidths:Array(8).fill(.02)});
  assert.equal(new Set(thin.paths.map(p=>p.role)).size,8);assert.deepEqual(thin.frame,fat.frame,'Gap changes never rescale the composition');assert(length(fat)<length(thin));
});
