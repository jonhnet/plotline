import {pageTransform} from './generators.js';

// Render incremental paths in the worker. Only fixed-size bitmaps cross to the
// page during refinement; no repeated cloning/refitting of an expanding scene.
export class ProgressivePreview {
  constructor(recipe,anchor){this.recipe=recipe;this.anchor=anchor;this.paths=[];this.canvas=new OffscreenCanvas(1,1);this.reset();}
  reset(){
    const [w,h]=this.recipe.media.sheet_size_mm,px=1800/Math.max(w,h);
    this.canvas.width=Math.max(1,Math.ceil(w*px));this.canvas.height=Math.max(1,Math.ceil(h*px));
    const t=pageTransform(this.anchor,this.recipe);this.scale=t.scale;
    this.ctx=this.canvas.getContext('2d');this.ctx.setTransform(px*t.scale*t.cos,px*t.scale*t.sin,-px*t.scale*t.sin,px*t.scale*t.cos,px*t.ox,px*t.oy);
    this.ctx.lineCap='round';this.ctx.lineJoin='round';
    // The previous complete level gives context while the new level appears.
    this.ctx.globalAlpha=.18;this.draw(this.anchor.paths);this.ctx.globalAlpha=1;this.draw(this.paths);
  }
  update(recipe){this.recipe=recipe;this.reset();}
  draw(paths){
    const ctx=this.ctx;
    for(let role=0;role<this.recipe.roles.length;role++){
      const ink=this.recipe.inks.find(i=>i.id===this.recipe.roles[role].ink_id);ctx.strokeStyle=ink.color_srgb;ctx.lineWidth=(ink.width_mm??.3)/this.scale;ctx.beginPath();
      for(const p of paths)if(p.role===role){ctx.moveTo(...p.points[0]);for(let i=1;i<p.points.length;i++)ctx.lineTo(...p.points[i]);}ctx.stroke();
    }
  }
  append(paths){this.draw(paths);for(const p of paths)this.paths.push(p);}
  async bitmap(){return createImageBitmap(this.canvas);}
}
