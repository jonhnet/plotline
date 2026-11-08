// Portable authoring files contain design settings, never saved-document IDs or
// print receipts. Pattern and Maze share the same file picker and error surface.
import {studioNotice} from './studio.js';

export const recipeFileLimit=8*1024*1024;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const text=(value,max=100)=>typeof value==='string'&&value.trim().length>0&&value.length<=max&&!/[\u0000-\u001f\u007f]/.test(value);
const number=(value,min,max)=>Number.isFinite(value)&&value>=min&&value<=max;
const vector=(value,length)=>Array.isArray(value)&&value.length===length&&value.every(Number.isFinite);
const require=(ok,message)=>{if(!ok)throw Error(message);};

export function parseRecipeFile(contents,{studio,familyIds,validate=()=>{}}){
  let value;
  try{value=JSON.parse(contents.replace(/^\uFEFF/,''));}catch{throw Error('This file is not valid JSON. Choose a downloaded recipe file.');}
  // Also accept a saved snapshot envelope, without adopting its service IDs.
  const recipe=object(value)&&!('version' in value)&&object(value.recipe)?value.recipe:value;
  require(object(recipe),'Choose a recipe JSON file.');
  require(recipe.version===1,'Unsupported recipe version. This Studio opens version 1 recipes.');
  require(familyIds.includes(recipe.family),`This is not a ${studio} Studio recipe. Open it in the Studio that made it.`);
  require(text(recipe.name),'The recipe needs a name of 1–100 characters.');
  require(object(recipe.params),'The recipe is missing its drawing settings.');
  const media=recipe.media;
  require(object(media)&&media.schema==='plotline.media-profile/1'&&vector(media.sheet_size_mm,2)&&media.sheet_size_mm.every(v=>number(v,20,5080)),'The recipe has an invalid paper size.');
  require(vector(media.printable_rect_mm,4),'The recipe is missing its printable rectangle.');
  const [x,y,w,h]=media.printable_rect_mm,[pw,ph]=media.sheet_size_mm;
  require(x>=0&&y>=0&&w>0&&h>0&&x+w<=pw+.001&&y+h<=ph+.001,'The printable rectangle must fit the paper.');
  require(media.content_inset_mm===undefined||number(media.content_inset_mm,0,Math.min(w,h)/2),'The recipe has an invalid pen clearance.');
  require(text(recipe.paper_id,200)&&[0,90,180,270].includes(recipe.orientation)&&['plotter','custom'].includes(recipe.margin_mode)&&number(recipe.custom_margin_mm,0,5080),'The recipe has invalid paper settings.');
  require(Array.isArray(recipe.inks)&&recipe.inks.length>=1&&recipe.inks.length<=64,'A recipe needs 1–64 document inks.');
  const ids=new Set();
  for(const ink of recipe.inks){
    require(object(ink)&&text(ink.id,200)&&!ids.has(ink.id),'Each document ink needs a unique ID.');
    require(text(ink.label)&&/^#[0-9a-f]{6}$/i.test(ink.color_srgb)&&ink.color_srgb.toLowerCase()!=='#ffffff','Each document ink needs a name and a nonwhite color.');
    require(ink.width_mm==null||number(ink.width_mm,.01,5),'Ink diameters must be between 0.01 and 5 mm, or unknown.');
    ids.add(ink.id);
  }
  require(Array.isArray(recipe.roles)&&recipe.roles.length>=1&&recipe.roles.length<=64&&recipe.roles.every(role=>object(role)&&text(role.label)&&ids.has(role.ink_id)),'Every drawing role must name an ink in the recipe.');
  validate(recipe);
  return recipe;
}

export function wireRecipeFiles({dialog,button,input,error,download,getRecipe,open,...options}){
  let importing=false;
  const clear=()=>{error.hidden=true;error.textContent='';};
  button.onclick=()=>{clear();input.click();};
  input.onchange=async()=>{
    const file=input.files?.[0];if(!file||importing)return;
    importing=true;button.disabled=true;clear();
    try{
      if(file.size>recipeFileLimit)throw Error('Recipe files must be 8 MiB or smaller.');
      const recipe=parseRecipeFile(await file.text(),options);
      await open(recipe);
      dialog.close();
      studioNotice();
    }catch(reason){
      error.textContent=reason.message;error.hidden=false;error.focus();
    }finally{input.value='';button.disabled=false;importing=false;}
  };
  download.onclick=()=>{
    const recipe=getRecipe();if(!recipe)return;
    const blob=new Blob([JSON.stringify(recipe,null,2)+'\n'],{type:'application/json'});
    const url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download=recipe.name.replace(/[\\/:*?"<>|]/g,'-')+'.recipe.json';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
  };
}
