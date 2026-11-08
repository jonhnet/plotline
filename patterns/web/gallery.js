import {familyGroups,groupForFamily} from './groups.js';
import {thumbnails} from './thumbnails.js';

export function createFamilyGallery({dialog,tabs,panel,onChoose,families}){
 const cards=new Map(),buttons=new Map(),preference='plotline-pattern-gallery-group';let selected=null,built=false;
 const select=(id,focus=false)=>{
  selected=id;try{sessionStorage.setItem(preference,id);}catch{}
  for(const group of familyGroups){const button=buttons.get(group.id),active=group.id===id;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}
  for(const [family,card] of cards)card.button.hidden=groupForFamily(family).id!==id;
  panel.setAttribute('aria-labelledby',buttons.get(id).id);panel.scrollTop=0;if(focus)buttons.get(id).focus();
  // Set all visible URLs together. The browser loads/decodes them concurrently
  // and reuses its HTTP cache across tab changes, reloads and studio windows.
  for(const family of familyGroups.find(g=>g.id===selected).families){
   const card=cards.get(family);
   if(!card.image.hasAttribute('src')||card.button.dataset.ready==='error'){
    card.button.dataset.ready='false';card.status.textContent='Loading preview…';
    card.image.src=thumbnails[family];
   }
  }
 };
 function build(){
  if(built)return;built=true;
  for(const group of familyGroups){
   const button=document.createElement('button');button.type='button';button.id='family-group-'+group.id;button.dataset.group=group.id;button.setAttribute('role','tab');button.setAttribute('aria-label',group.name+', '+group.families.length+' patterns');button.setAttribute('aria-controls',panel.id);button.textContent=group.name;
   const count=document.createElement('span');count.className='group-count';count.textContent=group.families.length;button.append(count);button.onclick=()=>select(group.id);buttons.set(group.id,button);tabs.append(button);
   button.onkeydown=e=>{const i=familyGroups.indexOf(group),at=e.key==='Home'?0:e.key==='End'?familyGroups.length-1:e.key==='ArrowRight'?(i+1)%familyGroups.length:e.key==='ArrowLeft'?(i+familyGroups.length-1)%familyGroups.length:null;if(at!==null){e.preventDefault();select(familyGroups[at].id,true);}};
   for(const id of group.families){
    const f=families.find(f=>f.id===id),button=document.createElement('button'),image=document.createElement('img'),label=document.createElement('strong'),status=document.createElement('span');
    button.type='button';button.dataset.family=id;button.dataset.ready='false';image.width=720;image.height=460;image.alt='';image.decoding='async';label.textContent=f.name;status.className='thumbnail-status';button.append(image,label,status);
    image.onload=()=>{status.textContent='';button.dataset.ready='true';};
    image.onerror=()=>{status.textContent='Preview unavailable';button.dataset.ready='error';};
    button.onclick=()=>{dialog.close();onChoose(id);};panel.append(button);cards.set(id,{button,image,status});
   }
  }
 }
 return {open(currentFamily){
  build();if(!selected){let stored;try{stored=sessionStorage.getItem(preference);}catch{}selected=familyGroups.some(g=>g.id===stored)?stored:groupForFamily(currentFamily)?.id||familyGroups[0].id;}
  dialog.showModal();select(selected);buttons.get(selected).focus();
 }};
}
