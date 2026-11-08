import {colorName, usable, matchPens, editLoadedPen, revealInk} from '/inksets.js';
export {colorName,usable};
export const widthLabel = width => width==null?'width unknown':`${Number(width.toFixed(3))} mm`;
export const slotLabel = slot => !slot?'unassigned':slot.state==='empty'?'empty':`${slot.label||colorName(slot.color)} · ${widthLabel(slot.width_mm)}${slot.state==='unavailable'?' · unavailable':''}`;
export function carouselControls({api,writeOptions,current,changed,notice}) {
  const $=id=>document.getElementById(id);let value=null,saving=false;
  function render(){
    $('match-loaded-pens').disabled=saving||!current().setup||!!current().job?.calibration;$('carousel-refresh').disabled=saving;
    $('carousel-state').textContent=saving?'Saving carousel…':'';
  }
  async function refresh(){
    if(saving)return;const next=await api('/api/carousel').then(r=>r.json());
    if(!value||next.revision>value.revision){value=next;render();changed();}
  }
  async function edit(slot){
    const original=structuredClone(value),pen=await editLoadedPen(original.slots[slot],slot);if(!pen)return;
    original.slots[slot]=pen;saving=true;render();
    try{value=await api('/api/carousel',{method:'PUT',...writeOptions(original)}).then(r=>r.json());}
    catch(e){notice(e.message);try{value=await api('/api/carousel').then(r=>r.json());}catch{}}
    finally{saving=false;render();changed();}
  }
  $('match-loaded-pens').onclick=()=>{
    const {job,setup}=current();setup.pen_map={...setup.pen_map,...matchPens(job.summary.pens.filter(p=>setup.pens.includes(p.id)),value)};
    changed(true);revealInk($('pens'),setup.pens[0]);notice('Mapped to loaded pens. Review any differences below to use existing paths or rebuild.');
  };
  $('carousel-refresh').onclick=()=>refresh().catch(e=>notice(e.message));
  window.addEventListener('focus',()=>refresh().catch(e=>notice(e.message)));
  return {refresh,render,edit,get value(){return value;},issue(setup){
    if(!value)return 'Load the carousel configuration.';if(saving)return 'Saving carousel…';
    for(const id of setup.pens){const n=setup.pen_map[id],slot=value.slots[n];if(!usable(slot))return `Ink ${id}: ${n?'slot '+n+' · '+slotLabel(slot):'unassigned'}. Select its cap to choose a loaded pen.`;}
    return '';
  }};
}
