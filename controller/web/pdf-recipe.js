import {nearest} from '/inksets.js';

// A recipe is reusable; its file identity, source styles and geometry are not.
// Never transfer conversion/job IDs or an authoring link for a different source.
export function replaceSourceRecipe(previous, source) {
  const intent=structuredClone(previous.intent),inks=intent.palette.inks;
  const outputs=new Map(inks.map(ink=>[ink.id,ink])),oldMapping=intent.ink_mapping||{};
  const output=ink=>outputs.get(oldMapping[ink.id]||ink.id)||nearest(ink,inks);
  const oldSources=new Map(previous.sources.map(ink=>[ink.id,ink]));
  const colors=new Map();
  for(const ink of oldSources.values()){
    const color=ink.color_srgb.toLowerCase();
    if(!colors.has(color))colors.set(color,[]);
    colors.get(color).push(ink);
  }
  const mapping={},newStyles=new Map(source.inspection.pages.flatMap(p=>p.source_styles||[]).map(ink=>[ink.id,ink]));
  let changedStyles=0,newColors=0;
  for(const ink of newStyles.values()){
    let prior=oldSources.get(ink.id);
    if(!prior){
      const sameColor=colors.get(ink.color_srgb.toLowerCase())||[];
      const sameKind=sameColor.filter(old=>old.kind===ink.kind);
      prior=nearest(ink,sameKind.length?sameKind:sameColor);
      if(prior)changedStyles++;else newColors++;
    }
    mapping[ink.id]=(prior?output(prior):nearest(ink,inks)).id;
  }
  const layers=new Set(source.inspection.layers||[]),oldBindings=intent.bindings||[];
  intent.bindings=oldBindings.filter(b=>layers.has(b.ocg_name));
  for(const binding of intent.bindings){
    const id=oldMapping[binding.ink_id]||binding.ink_id;
    // Layer bindings use logical authored ink IDs, independent of style IDs.
    if(outputs.has(id))mapping[binding.ink_id]=id;
  }
  const oldPages=new Map(intent.pages.map(p=>[p.page_index,p]));
  let resized=0,added=0;
  intent.pages=source.inspection.pages.map(page=>{
    const old=oldPages.get(page.page_index);
    if(!old)added++;
    else if(page.source_size_mm.some((v,i)=>Math.abs(v-old.source_size_mm[i])>.001))resized++;
    const next=structuredClone(old||intent.pages[0]);
    next.page_index=page.page_index;next.source_size_mm=[...page.source_size_mm];
    // "PDF page" means the new page's dimensions, not a frozen old sheet size.
    if(next.target_media?.id==='pdf-page')delete next.target_media;
    return next;
  });
  intent.source_sha256=source.source_sha256;intent.title=source.name;
  intent.ink_mapping=mapping;
  delete intent.authoring;
  const notes=['Settings copied. Preview pen strokes to rebuild.'];
  if(resized||added||intent.pages.length!==oldPages.size)notes.push('Page sizes or count changed; check paper placement. Scale and offsets were kept.');
  if(changedStyles)notes.push('Changed stroke styles kept the assignments of their closest same-color source styles.');
  if(newColors)notes.push(`${newColors} new source color${newColors===1?' was':'s were'} mapped to the closest output pen; check Pens.`);
  if(intent.bindings.length!==oldBindings.length)notes.push('Bindings for absent layers were removed; check Pens.');
  return {intent,note:notes.join(' ')};
}
