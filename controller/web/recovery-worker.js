// Immutable prepared HPGL is parsed and painted off the UI thread. No device I/O.
import {project, rectangle, layout} from './geometry.js';
let strokes=[], chunks=[], inks=new Map(), paper, windowRect, generation=0, loaded=false, pending=null, rendering=false;
const yieldTask=()=>new Promise(resolve=>setTimeout(resolve,0));
self.onmessage=async({data})=>{
  try {
    if(data.type==='load'){
      const response=await fetch(`/api/plots/${encodeURIComponent(data.ticket.id)}/program.hpgl`);
      if(!response.ok)throw Error('The recorded plot program could not be loaded.');
      const source=await response.text(), ticket=data.ticket, rotation=ticket.setup.rotation;
      for(const ink of ticket.inks)inks.set(ink.slot,{
        color:ink.loaded?.measured_color||ink.loaded?.color||ink.color||'#222222',
        width:ink.settings?.width_mm||ink.loaded?.width_mm||.3,
      });
      const area=layout(ticket.setup,ticket.observation);
      windowRect=rectangle(area.limits.map(n=>n/area.units),'artwork',rotation);
      paper=rectangle(area.paper||area.limits.map(n=>n/area.units),'artwork',rotation);
      let pen=0,start=null,current=null,deadline=performance.now()+12;
      for(const match of source.matchAll(/^(SP|PU|PD)([^;]*);\n/gm)){
        const [,op,args]=match;
        if(op==='SP'){pen=Number(args);start=null;current=null;}
        else if(op==='PU'){
          start=args?project(args.split(',').map(n=>Number(n)/40),'artwork',rotation):null;
          current=null;
        }else{
          const point=project(args.split(',').map(n=>Number(n)/40),'artwork',rotation);
          if(start){
            current={path:new Path2D(),start,pen};current.path.moveTo(...start);
            strokes.push(current);start=null;
          }
          if(!current)throw Error('A recorded stroke has no starting position.');
          current.path.lineTo(...point);
        }
        if(performance.now()>deadline){await yieldTask();deadline=performance.now()+12;}
      }
      for(let i=0;i<strokes.length;){
        const first=i,pen=strokes[i].pen,path=new Path2D();
        while(i<strokes.length&&i-first<64&&strokes[i].pen===pen)path.addPath(strokes[i++].path);
        chunks.push({first,end:i,pen,path});
      }
      loaded=true;self.postMessage({type:'ready',count:strokes.length});
    }else if(data.type==='render'&&loaded){
      pending={data,token:++generation};pump();
    }
  }catch(error){self.postMessage({type:'error',message:error.message});}
};
async function pump(){
  if(rendering)return;
  rendering=true;
  try{
    // Keep one canvas in flight and only the latest queued view during a drag.
    while(pending){const next=pending;pending=null;await render(next.data,next.token);}
  }catch(error){self.postMessage({type:'error',message:error.message});}
  finally{rendering=false;}
}
async function render({width,height,view,cutoff,id},token){
  const canvas=new OffscreenCanvas(width,height),ctx=canvas.getContext('2d');
  ctx.fillStyle='#e6e8e9';ctx.fillRect(0,0,width,height);
  const scale=Math.min(width/(paper[2]*1.08),height/(paper[3]*1.08))*view.scale;
  ctx.translate(width/2+view.x,height/2+view.y);ctx.scale(scale,scale);
  ctx.translate(-paper[0]-paper[2]/2,-paper[1]-paper[3]/2);
  ctx.fillStyle='white';ctx.fillRect(...paper);
  ctx.save();ctx.strokeStyle='#bac3c8';ctx.lineWidth=1/scale;ctx.setLineDash([4/scale,4/scale]);ctx.strokeRect(...windowRect);ctx.restore();
  ctx.lineCap='round';ctx.lineJoin='round';
  function paint(path,pen,alpha){
    const ink=inks.get(pen)||{color:'#222222',width:.3};
    ctx.globalAlpha=alpha;ctx.strokeStyle=ink.color;ctx.lineWidth=ink.width;ctx.stroke(path);
  }
  let deadline=performance.now()+12;
  // Paint faded remainder first, so completed ink retains its full strength.
  for(const completed of [false,true])for(const chunk of chunks){
    if(completed&&chunk.first>=cutoff||!completed&&chunk.end<=cutoff)continue;
    if(chunk.first<cutoff&&chunk.end>cutoff){
      for(let i=chunk.first;i<chunk.end;i++)if((i<cutoff)===completed)paint(strokes[i].path,chunk.pen,completed?1:.14);
    }else paint(chunk.path,chunk.pen,completed?1:.14);
    if(performance.now()>deadline){await yieldTask();if(token!==generation)return;deadline=performance.now()+12;}
  }
  const next=strokes[cutoff];
  if(next){
    ctx.globalAlpha=1;ctx.strokeStyle='white';ctx.lineWidth=(inks.get(next.pen)?.width||.3)+5/scale;ctx.stroke(next.path);
    ctx.strokeStyle='#e35b00';ctx.lineWidth=(inks.get(next.pen)?.width||.3)+2/scale;ctx.stroke(next.path);
    ctx.beginPath();ctx.arc(...next.start,4/scale,0,2*Math.PI);ctx.fillStyle='#e35b00';ctx.fill();
  }
  if(token!==generation)return;
  const bitmap=canvas.transferToImageBitmap();self.postMessage({type:'frame',bitmap,id},[bitmap]);
}
