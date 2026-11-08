// Browser-only inspection. Optional editing hooks are explicit transactions;
// ordinary navigation never calls them or knows anything about a document.
export function previewNavigation(element, {onChange, enabled=()=>true, edit=null, onFit=null, origin=null}={}) {
  let state={scale:1,x:0,y:0}, pointers=new Map(), gesture=null, wheelEdit=null, wheelTimer;
  const point=e=>[e.clientX,e.clientY];
  const center=points=>points.length===1?points[0]:points[0].map((v,i)=>(v+points[1][i])/2);
  const distance=points=>points.length<2?0:Math.hypot(...points[0].map((v,i)=>v-points[1][i]));
  const local=p=>{const r=element.getBoundingClientRect(),o=origin?.()||[r.width/2,r.height/2];return [p[0]-r.left-o[0],p[1]-r.top-o[1]];};
  function set(value){state={...value,scale:Math.max(.25,Math.min(100,value.scale))};onChange?.({...state});}
  function zoomBy(factor,anchor){
    const p=anchor?local(anchor):[0,0],scale=Math.max(.25,Math.min(100,state.scale*factor)),ratio=scale/state.scale;
    set({scale,x:p[0]-(p[0]-state.x)*ratio,y:p[1]-(p[1]-state.y)*ratio});
  }
  function finishWheel(cancel=false){
    clearTimeout(wheelTimer);
    if(wheelEdit){const value=wheelEdit;wheelEdit=null;edit.end(value,cancel);}
  }
  function snapshot(){
    const values=[...pointers.values()];
    if(!values.length)return null;
    return {state:{...state},center:center(values),distance:distance(values)};
  }
  element.addEventListener('wheel',event=>{
    if(!enabled()||gesture)return;
    event.preventDefault();
    // Shift-wheel may be delivered as horizontal scrolling by the browser.
    const delta=(event.deltaY||event.deltaX)*(event.deltaMode===1?16:event.deltaMode===2?element.clientHeight:1);
    const factor=Math.exp(Math.max(-.6,Math.min(.6,-delta*.002)));
    if(edit?.enabled(event)){
      if(!wheelEdit)wheelEdit=edit.start('wheel',event);
      edit.wheel(wheelEdit,factor,point(event));
      clearTimeout(wheelTimer);wheelTimer=setTimeout(()=>finishWheel(),250);
    }else{finishWheel();zoomBy(factor,point(event));}
  },{passive:false});
  element.addEventListener('pointerdown',event=>{
    if(!enabled()||(event.pointerType!=='touch'&&event.button!==0))return;
    event.preventDefault();finishWheel();element.focus({preventScroll:true});
    pointers.set(event.pointerId,point(event));element.setPointerCapture(event.pointerId);
    if(!gesture){
      const editing=Boolean(edit?.enabled(event));
      gesture={editing,transaction:editing?edit.start('pointer',event):null};
    }
    gesture.start=snapshot();
    if(gesture.editing)edit.rebase?.(gesture.transaction);
    element.classList.add('preview-dragging');
  });
  element.addEventListener('pointermove',event=>{
    if(!gesture||!pointers.has(event.pointerId))return;
    pointers.set(event.pointerId,point(event));
    const values=[...pointers.values()],p=center(values),start=gesture.start;
    const factor=start.distance&&values.length>1?distance(values)/start.distance:1;
    if(gesture.editing){
      edit.move(gesture.transaction,{dx:p[0]-start.center[0],dy:p[1]-start.center[1],factor,anchor:start.center});
    }else{
      const scale=Math.max(.25,Math.min(100,start.state.scale*factor)),ratio=scale/start.state.scale,a=local(start.center),b=local(p);
      set({scale,x:b[0]-(a[0]-start.state.x)*ratio,y:b[1]-(a[1]-start.state.y)*ratio});
    }
  });
  function finishPointer(event,cancel=false){
    if(!pointers.has(event.pointerId))return;
    pointers.delete(event.pointerId);
    if(pointers.size&&!cancel){gesture.start=snapshot();if(gesture.editing)edit.rebase?.(gesture.transaction);return;}
    const previous=gesture;gesture=null;
    const captured=[...pointers.keys(),event.pointerId];pointers.clear();
    for(const id of captured)if(element.hasPointerCapture(id))element.releasePointerCapture(id);
    element.classList.remove('preview-dragging');
    if(previous?.editing)edit.end(previous.transaction,cancel);
  }
  element.addEventListener('pointerup',event=>finishPointer(event));
  for(const name of ['pointercancel','lostpointercapture'])element.addEventListener(name,event=>finishPointer(event,true));
  function cancel(){
    finishWheel(true);
    for(const id of [...pointers.keys()])finishPointer({pointerId:id},true);
  }
  element.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();cancel();return;}
    if(event.ctrlKey||event.metaKey||event.altKey)return;
    if(['+','=','-','0'].includes(event.key)){
      event.preventDefault();if(event.key==='0'){onFit?.();if(!onFit)set({scale:1,x:0,y:0});}
      else zoomBy(event.key==='-'?1/1.25:1.25);
    }else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)){
      event.preventDefault();set({...state,x:state.x+(event.key==='ArrowLeft'?40:event.key==='ArrowRight'?-40:0),y:state.y+(event.key==='ArrowUp'?40:event.key==='ArrowDown'?-40:0)});
    }
  });
  window.addEventListener('blur',cancel);
  window.addEventListener('pointerdown',event=>{if(!element.contains(event.target))finishWheel();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)cancel();});
  // Image links and native image dragging would turn inspection into navigation.
  element.addEventListener('dragstart',event=>event.preventDefault());
  element.addEventListener('click',event=>{if(event.target.closest('a'))event.preventDefault();});
  return {get state(){return {...state};},set,zoomBy,cancel,finish:()=>finishWheel(),reset(){cancel();set({scale:1,x:0,y:0});}};
}
