import assert from 'node:assert/strict';
import {fitMaze,detailGrid,detailLevel,applyDetail} from '../mazes/web/geometry.js';
const raw={bounds:[-1,-1,10,8],paths:[{role:0,points:[[-1,-1],[10,-1],[10,8],[-1,8]]}],solution:[[0,0],[5,4]],edge_length:1,clearance:1};
const recipe={roles:[{ink_id:'walls'},{ink_id:'answer'}],inks:[{id:'walls',width_mm:.7},{id:'answer',width_mm:.25}],media:{sheet_size_mm:[431.8,279.4],printable_rect_mm:[39,11,376.95,253.6],content_inset_mm:1},plot_solution:false};
for(const rect of [[39,11,376.95,253.6],[11,15,253.6,376.95]]){
  recipe.media.printable_rect_mm=rect;
  const fit=fitMaze(raw,recipe),[x,y,w,h]=rect;
  for(const path of [...fit.paths,fit.solution])for(const [px,py] of path.points){const r=recipe.inks[path.role].width_mm/2;assert(px-r>=x+1-1e-9);assert(py-r>=y+1-1e-9);assert(px+r<=x+w-1+1e-9);assert(py+r<=y+h-1+1e-9);}
  assert.equal(fit.paths.length,1,'Solution is excluded by default');assert(!fit.blocked);
  recipe.plot_solution=true;assert.equal(fitMaze(raw,recipe).paths.length,2);recipe.plot_solution=false;
}
recipe.media.printable_rect_mm=[0,0,4,4];assert(fitMaze(raw,recipe).blocked);
recipe.inks[0].width_mm=null;assert.throws(()=>fitMaze(raw,recipe),/diameter/);
console.log('Maze physical fit, independent solution and mask aspect checks passed');

// Match the generator's actual regular-tiling bounds, not just C/R.
const gridBounds=(family,{columns:c,rows:r})=>family==='hexagon'?[-1,-Math.sqrt(3)/2,1.5*c-.5,Math.sqrt(3)*r]:family==='triangle'?[0,0,(c+1)/2,Math.sqrt(3)*r/2]:[0,0,c,r];
const base={...recipe,params:{columns:40,rows:27,mask:null},inks:[{id:'walls',width_mm:.3},{id:'answer',width_mm:1.2}],detail:50,plot_solution:false};
for(const family of ['square','hexagon','triangle'])for(const rect of [[39,11,376.95,253.6],[11,39,253.6,376.95],[10,10,400,100],[10,10,100,400]]){
  const r=structuredClone(base);r.family=family;r.media.printable_rect_mm=rect;
  let previous={columns:0,rows:0};
  for(let level=1;level<=100;level++){
    r.detail=level;const g=detailGrid(r);
    assert(g.columns>=previous.columns&&g.rows>=previous.rows,'Increasing detail never coarsens either axis');
    assert(g.columns<=160&&g.rows<=160&&g.columns*g.rows<=16000);previous=g;
  }
  r.detail=50;applyDetail(r);const bounds=gridBounds(family,r.params),geometry={...raw,bounds,paths:[{role:0,points:[[bounds[0],bounds[1]],[bounds[2],bounds[3]]]}]};
  const fitted=fitMaze(geometry,r),[start,end]=fitted.paths[0].points;
  assert((end[0]-start[0])/rect[2]>.90&&(end[1]-start[1])/rect[3]>.90,'Maze fills both printable dimensions');
  r.plot_solution=true;assert.deepEqual(fitMaze(geometry,r).paths[0],fitted.paths[0],'Wider solution pen never moves walls');
  r.params.mask={width:200,height:100};applyDetail(r);const [x,y,x2,y2]=gridBounds(family,r.params);
  assert(Math.abs((x2-x)/(y2-y)-2)<.15,'Mask proportions survive a paper/aspect change');
}
const legacy=structuredClone(base);delete legacy.detail;const frozen=structuredClone(legacy);assert(Number.isFinite(detailLevel(legacy)));assert.deepEqual(legacy,frozen,'Reading an old recipe never changes its grid');
console.log('Detail density, full-page fit, regular tilings, mask aspect and stable solution placement passed');
