import {studioNavigation} from '/studio-navigation.js';
// Old bookmarks used /#job-id (and occasionally ?recipe=…).
if(location.hash&&!location.hash.startsWith('#studio'))location.replace('/hpgl'+location.search+location.hash);
else {
  studioNavigation().ready();
  // Anchor the curves to the actual card edges, including wrapped text and
  // browser zoom. The same connections have a vertical layout on small screens.
  const diagram=document.querySelector('.studio-flow'),svg=diagram.querySelector('svg');
  function drawConnections(){
    const box=diagram.getBoundingClientRect(),w=box.width,h=box.height;
    svg.setAttribute('viewBox',`0 0 ${w} ${h}`);
    const card=studio=>{const r=diagram.querySelector(`[data-studio=${studio}]`).getBoundingClientRect();return{x:r.left-box.left,y:r.top-box.top,w:r.width,h:r.height};};
    const m=card('map'),f=card('patterns'),z=card('mazes'),p=card('pdf'),g=card('hpgl'),narrow=matchMedia('(max-width:820px)').matches;
    function edge(id,start,c1,c2,end,{rotate=0,label}={}){
      const path=diagram.querySelector(`[data-connection=${id}]`),caption=diagram.querySelector(`[data-caption=${id}]`);
      path.setAttribute('d',`M${start} C${c1} ${c2} ${end}`);
      const point=label||path.getPointAtLength(path.getTotalLength()/2);
      caption.style.left=point.x+'px';caption.style.top=point.y+'px';caption.style.transform=`translate(-50%,-50%) rotate(${rotate}deg)`;
    }
    const left=(r,t)=>[r.x,r.y+r.h*t],right=(r,t)=>[r.x+r.w,r.y+r.h*t],top=(r,t)=>[r.x+r.w*t,r.y],bottom=(r,t)=>[r.x+r.w*t,r.y+r.h];
    edge('app-pdf',[p.x+p.w*(narrow?.4:.65),p.y+p.h+(narrow?100:120)],[p.x+p.w*.1,p.y+p.h+(narrow?100:120)],[p.x+p.w*.25,p.y+p.h+50],bottom(p,.25),{label:{x:p.x+p.w*(narrow?.3:.45),y:p.y+p.h+65}});
    if(!narrow){
      const gap=g.x-m.x-m.w;
      for(const [r,prefix,t] of [[m,'map',.08],[f,'pattern',.32],[z,'maze',.56],[p,'pdf',.80]]){
        const start=right(r,.40),end=left(g,t);
        edge(prefix+'-plot',start,[start[0]+gap*.4,start[1]],[end[0]-gap*.4,end[1]],end);
        const back=left(g,t+.09),finish=right(r,.73);
        edge('plot-'+prefix,back,[back[0]-gap*.5,back[1]+35],[finish[0]+gap*.4,finish[1]+35],finish);
      }
      // Optional conversion runs down the outside of the authoring cards.
      edge('map-pdf',left(m,.65),[8,m.y+m.h],[8,p.y],left(p,.20),{rotate:-90,label:{x:12,y:(m.y+m.h+p.y)/2}});
      const middle=(z.y+z.h+p.y)/2;
      edge('maze-pdf',bottom(z,.65),[z.x+z.w*.8,middle-30],[p.x+p.w*.8,middle+30],top(p,.65),{label:{x:z.x+z.w*.68,y:middle}});
      edge('pattern-pdf',left(f,.65),[62,f.y+f.h],[62,p.y],left(p,.4),{rotate:-90,label:{x:68,y:z.y+z.h/2}});
      edge('pdf-map',left(p,.08),[38,p.y-20],[38,m.y+m.h+20],left(m,.85),{rotate:-90,label:{x:46,y:(m.y+m.h+p.y)/2-80}});
    }else{
      for(const [r,prefix,offset,t] of [[m,'map',7,.14],[f,'pattern',22,.38],[z,'maze',37,.62]]){
        edge(prefix+'-plot',right(r,.35),[w-offset,r.y+r.h*.35],[w-offset,g.y+g.h*t],right(g,t),{rotate:90});
        edge('plot-'+prefix,left(g,t),[offset,g.y+g.h*t],[offset,r.y+r.h*.6],left(r,.6),{rotate:-90});
      }
      for(const [from,to,forward] of [[f,p,'pattern-pdf'],[z,p,'maze-pdf'],[p,g,'pdf-plot']]){
        const middle=(from.y+from.h+to.y)/2;
        edge(forward,bottom(from,.65),[from.x+from.w*.8,middle-30],[to.x+to.w*.8,middle+30],top(to,.65),{label:{x:w/2+12,y:middle}});
      }
      edge('plot-pdf',left(g,.8),[38,g.y-35],[38,p.y+p.h+35],left(p,.88),{rotate:-90});
      edge('map-pdf',right(m,.8),[w-48,m.y+m.h],[w-48,p.y],right(p,.15),{rotate:90});
      edge('pdf-map',left(p,.15),[48,p.y],[48,m.y+m.h],left(m,.8),{rotate:-90});
    }
  }
  const observer=new ResizeObserver(drawConnections);observer.observe(diagram);for(const card of diagram.querySelectorAll('.studio-card'))observer.observe(card);
  document.fonts.ready.then(drawConnections);
}
