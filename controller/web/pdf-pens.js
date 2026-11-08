// A PDF recipe chooses slots; submitted conversion intents freeze their material.
import {equivalent,nearest,colorName} from '/inksets.js';
import {stable} from '/studio.js';

export const linkedSlot = ink => ink?.source?.follow_slot ? ink.source.slot : 0;
export const usablePen = pen => pen?.state==='loaded' && pen.width_mm>0 && pen.color && pen.color.toLowerCase()!=='#ffffff';
export function outputSlot(ink,inventory) {
  if(linkedSlot(ink))return linkedSlot(ink);
  const prior=ink?.source?.slot;
  if(usablePen(inventory?.slots[prior])&&equivalent(ink,inventory.slots[prior]))return prior;
  return Number(Object.entries(inventory?.slots||{}).find(([,pen])=>usablePen(pen)&&equivalent(ink,pen))?.[0]||0);
}
export function entrySources(entry) {
  return entry.source.intent?.palette?.inks || (entry.stylePages||[]).flatMap(page=>page.source_styles||[]);
}
export function rasterRoles(settings,includeAuto=true) {
  if(!settings)return [];
  const roles=new Set(settings.screening?({auto:includeAuto?'CMYK':'',k:'K',cmy:'CMY',cmyk:'CMYK'}[settings.palette]||''):'');
  if(settings.black_treatment!=='off')roles.add('K');
  return [...roles];
}
export const rasterRoleLabel=(role,settings)=>role==='K'&&settings.black_treatment!=='off'?'Black treatment pen':({C:'Cyan channel pen',M:'Magenta channel pen',Y:'Yellow channel pen',K:'Black screening pen'})[role];
export function rasterAssignmentIssue(settings,inks) {
  const missing=rasterRoles(settings,false).filter(role=>!inks.some(ink=>ink.id===settings.inks?.[role]));
  if(!missing.length)return '';
  const optionalBlack=missing.length===1&&missing[0]==='K'&&settings.black_treatment!=='off'&&(!settings.screening||['selected','cmy'].includes(settings.palette));
  return 'In Images & effects, choose '+missing.map(role=>rasterRoleLabel(role,settings)).join(', ')+(optionalBlack?' or set Black treatment to Off.':'.')+' Pen names do not need to match these roles.';
}
export function rasterOutputs(recipe) {
  const used=new Set(),r=recipe.policies?.raster==='dither'?recipe.policies.raster_settings:null;
  if(r){for(const role of rasterRoles(r))if(r.inks?.[role])used.add(r.inks[role]);if(r.screening&&r.palette==='selected')for(const id of r.screen_inks||[])used.add(id);}
  return used;
}
export function usedOutputs(recipe,sources) {
  const inks=recipe.palette?.inks||[],mapping=recipe.ink_mapping||{};
  const used=new Set(sources.map(source=>inks.find(ink=>ink.id===(mapping[source.id]||source.id))||nearest(source,inks)).filter(Boolean).map(ink=>ink.id));
  for(const id of rasterOutputs(recipe))used.add(id);
  return used;
}
export function screenPenChoices(inks,selected,inventory) {
  const selectedIds=new Set(selected),matched=new Set();
  const loaded=Object.entries(inventory?.slots||{}).filter(([,pen])=>usablePen(pen)).map(([key,pen])=>{
    const slot=Number(key),matches=inks.filter(ink=>outputSlot(ink,inventory)===slot);
    const chosen=matches.filter(ink=>selectedIds.has(ink.id));
    for(const ink of chosen)matched.add(ink.id);
    return {slot,pen,ink:chosen[0]||matches.find(linkedSlot)||matches[0],selectedIds:chosen.map(ink=>ink.id)};
  });
  // Historical snapshots are not extra carousel slots. Keep selected ones
  // explicit until the user removes them or chooses the loaded palette.
  const missing=[...selectedIds].filter(id=>!matched.has(id)).map(id=>inks.find(ink=>ink.id===id)||{id,label:id});
  return {loaded,missing};
}
export function slotMaterial(pen,slot,inventory) {
  return {label:pen.label||colorName(pen.color),color_srgb:pen.color,width_mm:pen.width_mm,
    measured_color_srgb:pen.measured_color||null,
    source:{kind:'plotline-carousel',slot,revision:inventory.revision,follow_slot:true}};
}
export function resolveRecipePens(recipe,sources,inventory,previous=null) {
  const next=structuredClone(recipe),used=usedOutputs(recipe,sources),raster=rasterOutputs(recipe);
  for(const ink of next.palette?.inks||[]) {
    if(!used.has(ink.id))continue;
    let slot=linkedSlot(ink);
    // Older raster selections stored material snapshots. A current material
    // match can safely become a live slot choice, including label-only edits.
    // An old green must never silently become yellow just because its source
    // record mentions the same slot number.
    if(!slot&&raster.has(ink.id))slot=outputSlot(ink,inventory);
    // Adopt an old material-only assignment when its currently displayed slot
    // changes. Historical provenance alone never assigns an unrelated new pen.
    if(!slot&&previous) {
      const oldSlot=outputSlot(ink,previous),a=previous.slots[oldSlot],b=inventory.slots[oldSlot];
      if(oldSlot && (a?.state!==b?.state || a?.color!==b?.color || a?.width_mm!==b?.width_mm))slot=oldSlot;
    }
    if(!slot)continue;
    const pen=inventory.slots[slot];
    if(usablePen(pen)) {
      if(!linkedSlot(ink)||ink.label!==(pen.label||colorName(pen.color))||!equivalent(ink,pen)||(ink.measured_color_srgb||null)!==(pen.measured_color||null))Object.assign(ink,slotMaterial(pen,slot,inventory));
    } else ink.source={kind:'plotline-carousel',slot,revision:inventory.revision,follow_slot:true};
  }
  const settings=next.policies?.raster_settings;
  if(settings?.palette==='selected'&&settings.screen_inks){
    const seen=new Set();
    settings.screen_inks=settings.screen_inks.filter(id=>{
      const ink=next.palette.inks.find(i=>i.id===id),slot=ink&&outputSlot(ink,inventory),key=slot?'slot:'+slot:'ink:'+id;
      if(seen.has(key))return false;seen.add(key);return true;
    });
  }
  return next;
}
export function unavailableSlots(recipe,sources,inventory) {
  const used=usedOutputs(recipe,sources);
  return [...new Set((recipe.palette?.inks||[]).filter(ink=>used.has(ink.id)&&linkedSlot(ink)&&!usablePen(inventory?.slots[linkedSlot(ink)])).map(linkedSlot))];
}
// Live material is an external dependency, not an undoable recipe edit. The
// selected slot remains part of the saved recipe; conversions hash the material.
export function recipeKey(recipe) {
  const value=structuredClone(recipe);
  if(value.palette)value.palette.inks=value.palette.inks.map(ink=>linkedSlot(ink)?{id:ink.id,source:{kind:ink.source.kind,slot:linkedSlot(ink),follow_slot:true}}:ink);
  return stable(value);
}
