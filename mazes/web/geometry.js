// Physical page fit shared by preview, downloads and the frozen PDF scene.
export function fitMaze(raw, recipe) {
  const widths=recipe.roles.map(role=>recipe.inks.find(i=>i.id===role.ink_id)?.width_mm);
  const usedWidths=recipe.plot_solution?widths:[widths[0]];
  if(usedWidths.some(w=>!Number.isFinite(w)||w<.01||w>5))throw Error('Choose a diameter for each maze ink.');
  const [x,y,w,h]=recipe.media.printable_rect_mm,[x0,y0,x1,y1]=raw.bounds;
  const inset=pageInset(recipe);
  const scale=Math.min((w-2*inset)/(x1-x0),(h-2*inset)/(y1-y0));
  if(!(scale>0))throw Error('Margins leave no room for this maze.');
  const dx=x+w/2-(x0+x1)*scale/2,dy=y+h/2-(y0+y1)*scale/2;
  const transform=p=>[p[0]*scale+dx,p[1]*scale+dy];
  const paths=raw.paths.map(p=>({role:p.role,points:p.points.map(transform)}));
  const solution={role:1,points:raw.solution.map(transform)};
  if(recipe.plot_solution)paths.push(solution);
  const clearance=raw.clearance*scale-widths[0];
  const blocked=clearance<=0||(recipe.plot_solution&&clearance<widths[1]);
  const distance=paths.reduce((sum,p)=>sum+p.points.slice(1).reduce((s,point,i)=>s+Math.hypot(point[0]-p.points[i][0],point[1]-p.points[i][1]),0),0);
  return {paths,solution,cell_mm:raw.edge_length*scale,clearance,blocked,distance};
}
// Automatic layouts reserve both role widths so adding/removing the answer
// never moves the walls. Legacy recipes retain their original physical fit.
function pageInset(recipe){
  const widths=recipe.roles.map(role=>recipe.inks.find(i=>i.id===role.ink_id)?.width_mm);
  const used=recipe.detail!=null||recipe.plot_solution?widths:[widths[0]];
  return Math.max(1,recipe.media.content_inset_mm||0)+Math.max(0,...used.filter(Number.isFinite))/2;
}
export function detailLevel(recipe){
  return recipe.detail??Math.max(1,Math.min(100,Math.round(1+99*Math.log(recipe.params.columns*recipe.params.rows/64)/Math.log(250))));
}
export function detailGrid(recipe){
  const detail=detailLevel(recipe);
  if(!Number.isFinite(detail)||detail<1||detail>100)throw Error('Detail level must be between 1 and 100.');
  const [, ,w,h]=recipe.media.printable_rect_mm,inset=pageInset(recipe),mask=recipe.params.mask;
  if(w<=2*inset||h<=2*inset)throw Error('Margins leave no room for this maze.');
  const aspect=mask?mask.width/mask.height:(w-2*inset)/(h-2*inset);
  if(!Number.isFinite(aspect)||aspect<=0)throw Error('Invalid maze shape.');
  // Exact bounding dimensions: width=a*columns+b, height=d*rows+e.
  const [a,b,d,e]=recipe.family==='hexagon'?[1.5,.5,Math.sqrt(3),Math.sqrt(3)/2]:recipe.family==='triangle'?[.5,.5,Math.sqrt(3)/2,0]:[1,0,1,0];
  const maximum=Math.max(4,Math.min(16000,160*((a*160+b)/aspect-e)/d,160*(aspect*(d*160+e)-b)/a));
  const minimum=Math.min(64,maximum),budget=minimum*Math.pow(maximum/minimum,(detail-1)/99);
  // Solve the aspect and cell-budget equations together. Flooring both axes
  // keeps density monotone and respects the generator's 16,000-cell bound.
  const linear=aspect*e-b;
  const rows=(-linear+Math.sqrt(linear*linear+4*aspect*d*a*budget))/(2*aspect*d);
  const columns=budget/rows;
  return {columns:Math.max(2,Math.min(160,Math.floor(columns+1e-9))),rows:Math.max(2,Math.min(160,Math.floor(rows+1e-9)))};
}
export function applyDetail(recipe){
  recipe.detail=detailLevel(recipe);
  Object.assign(recipe.params,detailGrid(recipe));
  return recipe;
}
