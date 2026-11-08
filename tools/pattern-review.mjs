// Reproducible vector contact sheets for a selected batch of presets and
// every one-click neighbor. Generated artifacts stay under ignored build/.
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {adjacentFamilies} from '../patterns/web/adjacent.js';
import {complexFamilies} from '../patterns/web/complex.js';
import {naturalFamilies} from '../patterns/web/natural.js';
import {spatialFamilies} from '../patterns/web/spatial.js';
import {subdivisionFamilies} from '../patterns/web/subdivision.js';
import {collectionFamilies} from '../patterns/web/collection.js';
import {textileFamilies} from '../patterns/web/textile.js';
import {ornamentFamilies} from '../patterns/web/ornament.js';
import {recursiveFamilies} from '../patterns/web/recursive-art.js';
import {densityFamilies} from '../patterns/web/density.js';
const reviewFamilies=process.argv.includes('--collection')?collectionFamilies:process.argv.includes('--textile')?textileFamilies:process.argv.includes('--ornament')?ornamentFamilies:process.argv.includes('--recursive')?recursiveFamilies:process.argv.includes('--curves-density')?[...subdivisionFamilies,...densityFamilies]:process.argv.includes('--spatial')?spatialFamilies:process.argv.includes('--natural')?naturalFamilies:process.argv.includes('--complex')?complexFamilies:adjacentFamilies;
import {defaults,generate,fitPaths,loadLimits,usesInkSequence,physicalOptions} from '../patterns/web/generators.js';
await loadLimits();
const root=resolve(import.meta.dirname,'..'),out=resolve(process.argv[2]||root+'/build/pattern-review');await mkdir(out,{recursive:true});
const colors=['#222222','#008080','#c84253','#008000','#245bd6','#a32c99','#c19a00'];
const media={sheet_size_mm:[431.8,279.4],printable_rect_mm:[39.425,11,376.95,253.6],content_inset_mm:1};
const escape=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const html=(title,items,cols)=>`<!doctype html><meta charset="utf-8"><title>${escape(title)}</title><style>body{font:16px system-ui;background:#eceee8;margin:20px;color:#23372e}h1{font-size:22px}.grid{display:grid;grid-template-columns:repeat(${cols},1fr);gap:12px}figure{margin:0;background:white;padding:10px}img{width:100%;display:block}figcaption{font-size:14px}small{font-size:11px;color:#58685e}</style><h1>${escape(title)}</h1><div class="grid">${items.join('')}</div>`;
const previews=[];
for(const family of reviewFamilies){
  const variants=[{name:'Default',params:defaults(family.id)}],cards=[];
  for(const k of family.knobs)for(const raw of (k.choices?k.choices.map((_,i)=>k.min+i*k.step):[k.value-k.step,k.value+k.step])){
    const value=Number(raw.toFixed(6));if(value===k.value||value<k.min||value>k.max)continue;
    variants.push({name:k.label+' '+(k.choices?.[(value-k.min)/k.step]??value),params:{...defaults(family.id),[k.key]:value}});
  }
  for(const [index,v] of variants.entries()){
    const count=usesInkSequence(family.id)?colors.length:family.id==='newton'?v.params.roots:family.roles.length,inks=colors.slice(0,count).map((c,i)=>({id:String(i),color_srgb:c,width_mm:.3})),roles=inks.map(i=>({ink_id:i.id}));
    const recipe={version:1,name:family.name,family:family.id,params:v.params,media,inks,roles,size:.96,rotation:0};
    const options={...(usesInkSequence(family.id)?{inkCount:count}:{}),...physicalOptions(recipe)};
    const geometry=generate(family.id,v.params,()=>{},options),fitted=fitPaths(geometry,recipe);
    const file=`${family.id}-${index}.svg`;
    const paths=Array.from({length:count},(_,role)=>fitted.paths.filter(p=>p.role===role).map(p=>`<path stroke="${inks[role].color_srgb}" d="M${p.points.map(pt=>pt.join(',')).join('L')}"/>`).join('')).join('');
    await writeFile(out+'/'+file,`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 431.8 279.4"><rect width="431.8" height="279.4" fill="white"/><g fill="none" stroke-width=".3" stroke-linecap="round" stroke-linejoin="round">${paths}</g></svg>`);
    const card=`<figure><img src="${file}"><figcaption>${escape(index?v.name:family.name)}</figcaption><small>${geometry.paths.length.toLocaleString()} paths · ${(fitted.length_mm/1000).toFixed(1)} m</small></figure>`;cards.push(card);
    if(index===0){previews.push(card);await writeFile(out+'/'+family.id+'.json',JSON.stringify({recipe,paths:fitted.paths}));console.log(`${family.id}: ${geometry.paths.length} paths, ${geometry.points} points, ${(fitted.length_mm/1000).toFixed(1)} m`);}
  }
  await writeFile(out+'/'+family.id+'.html',html(family.name+' — default and neighboring controls',cards,4));
}
await writeFile(out+'/index.html',html('Pattern Studio — '+reviewFamilies.length+' generators',previews,3));console.log(out+'/index.html');
