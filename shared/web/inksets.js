// Shared by Map, PDF and HPGL studios. This module never writes inventory.
export const colors = {black:'#000000',red:'#ff0000',green:'#008000',blue:'#0000ff',cyan:'#00ffff',magenta:'#ff00ff',purple:'#800080',teal:'#008080',turquoise:'#40e0d0',pink:'#ffc0cb','hot pink':'#ff1493',orange:'#ffa500',yellow:'#ffff00',brown:'#a52a2a',gray:'#808080'};
export const colorName = c => Object.entries(colors).find(([,v])=>v===c?.toLowerCase())?.[0] || c || 'unknown color';
export const colorOf = i => i?.color_srgb ?? i?.color;
export const widthText = w => w==null?'?':String(Number(w.toFixed(3)));
export const usable = p => p?.state==='loaded' && !!colorOf(p);
export const colorMatches = (a,b) => !!a && !!b && [colorOf(b),b.measured_color].some(c=>c&&colorOf(a)?.toLowerCase()===c.toLowerCase());
export const equivalent = (a,b) => colorMatches(a,b) && a.width_mm!=null && b.width_mm!=null && Math.abs(a.width_mm-b.width_mm)<1e-6;
export function difference(a,b) {
  if(!b || b.state==='empty')return 'Unassigned';
  if(b.state==='unavailable')return 'Unavailable';
  const changes=[];
  const varied=a.members&&new Set(a.members.map(colorOf)).size>1;
  if(varied||!colorMatches(a,b))changes.push(`${varied?'Grouped colors':colorName(colorOf(a))} → ${colorName(colorOf(b))}`);
  if(a.width_label?.includes('–')||a.width_mm==null || b.width_mm==null || Math.abs(a.width_mm-b.width_mm)>=1e-6)changes.push(`${a.width_label||(a.kind==='fill'?'Fill':widthText(a.width_mm))} → ${widthText(b.width_mm)} mm`);
  return changes.join(' · ');
}
export function lab(hex) {
  const [r,g,b]=(hex||'#000000').slice(1).match(/../g).map(v=>{const n=parseInt(v,16)/255;return n<=.04045?n/12.92:((n+.055)/1.055)**2.4;});
  const l=Math.cbrt(.4122214708*r+.5363325363*g+.0514459929*b),m=Math.cbrt(.2119034982*r+.6806995451*g+.1073969566*b),s=Math.cbrt(.0883024619*r+.2817188376*g+.6299787005*b);
  return [.2104542553*l+.793617785*m-.0040720468*s,1.9779984951*l-2.428592205*m+.4505937099*s,.0259040371*l+.7827717662*m-.808675766*s];
}
export function nearest(ink,pens) {
  const a=lab(colorOf(ink));
  return [...pens].sort((x,y)=>{
    const distance=p=>Math.hypot(...lab(colorOf(p)).map((v,i)=>v-a[i]));
    const dc=distance(x)-distance(y);
    return Math.abs(dc)>1e-9?dc:Math.abs((x.width_mm??10)-(ink.width_mm??0))-Math.abs((y.width_mm??10)-(ink.width_mm??0));
  })[0];
}
export function matchPens(inks,carousel,{exact=false}={}) {
  const slots=Object.entries(carousel?.slots||{}).filter(([,s])=>usable(s)).map(([slot,s])=>({...s,slot:Number(slot)}));
  return Object.fromEntries(inks.map(ink=>[ink.id,(slots.find(s=>equivalent(ink,s)) || (!exact&&nearest(ink,slots)))?.slot || 0]));
}
export function substitution(ink,slot,pen) {
  return {slot:Number(slot),color:colorOf(pen).toLowerCase(),width_mm:pen.width_mm??null,requested_color:colorOf(ink).toLowerCase(),requested_width_mm:ink.width_mm??null};
}
export function accepted(ink,slot,pen,record) {
  if(!pen||!record||!usable(pen))return false;
  const current=substitution(ink,slot,pen);
  return Object.keys(current).every(k=>current[k]===record[k]);
}
export function element(tag,text,cls) {
  const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;
}
export function button(text,click,cls='ink-action') {
  const e=element('button',text,cls);e.type='button';e.onclick=click;return e;
}
export function cap(ink,click,label) {
  const e=button('',click,'pen-cap');
  const color=colorOf(ink),rgb=(color||'#ffffff').slice(1).match(/../g).map(v=>parseInt(v,16));
  e.style.backgroundColor=ink?.state==='empty'?'transparent':color||'#e8e8e8';
  e.style.color=rgb[0]*.299+rgb[1]*.587+rgb[2]*.114>155?'#172029':'white';
  if(ink?.state==='empty')e.style.color='inherit';
  e.textContent=ink?.state==='unavailable'?'×':ink?.state==='empty'?'–':ink?.width_label?.includes('–')?widthText(ink.width_mm)+'…':ink?.width_label|| (ink?.kind==='fill'?'Fill':widthText(ink?.width_mm));
  e.title=label||`${ink?.label||colorName(color)} · ${ink?.width_label || widthText(ink?.width_mm)} mm`;
  e.setAttribute('aria-label',e.title);e.disabled=!click;
  return e;
}
export function modal(title,{closable=true}={}) {
  const dialog=element('dialog',null,'ink-dialog'),head=element('div',null,'ink-dialog-head');
  head.append(element('h2',title));if(closable)head.append(button('Close',()=>dialog.close()));dialog.append(head);
  const body=element('div',null,'ink-dialog-body');dialog.append(body);document.body.append(dialog);
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  dialog.showModal();return {dialog,body};
}
export function editLoadedPen(pen,slot) {
  return new Promise(resolve=>{
    const {dialog,body}=modal(`Loaded pen — slot ${slot}`,{closable:false}),form=element('form'),fields={};
    dialog.classList.add('ink-pen-editor');form.id='loaded-pen-form-'+slot;
    const status=element('select');status.append(new Option('Loaded','loaded'),new Option('Empty','empty'),new Option('Unavailable · broken or dry','unavailable'));status.value=pen.state;
    const stateLabel=element('label','Status');stateLabel.append(status);form.append(stateLabel);
    for(const [key,title,type] of [['label','Name','text'],['color','Design color','color'],['width_mm','Diameter · mm','number'],['speed_cm_s','Speed · cm/s','number'],['force','Force · 1–8','number']]){
      const label=element('label',title),input=element('input');input.type=type;input.name=key;input.value=pen[key]??(type==='color'?'#000000':'');
      if(type==='number'){input.min=key==='width_mm'?'.01':'1';input.max=key==='width_mm'?'5':key==='force'?'8':'60';input.step=key==='width_mm'?'any':'1';input.required=key!=='width_mm';}
      if(type==='text')input.maxLength=80;
      label.append(input);form.append(label);fields[key]=input;
    }
    fields.label.placeholder='Black, cyan, magenta… or K R G B C M Y';
    fields.label.oninput=()=>{const name=fields.label.value.trim().toLowerCase(),c=colors[({k:'black',r:'red',g:'green',b:'blue',c:'cyan',m:'magenta',y:'yellow'})[name]||name];if(c)fields.color.value=c;if(status.value==='empty'&&fields.label.value.trim())status.value='loaded';};
    fields.color.oninput=()=>{if(status.value==='empty')status.value='loaded';};
    let measured=pen.measured_color;
    if(measured){
      const appearance=element('div',null,'ink-toolbar'),swatch=cap({...pen,color:measured},null,'Measured appearance '+measured);
      appearance.append(swatch,element('span','Measured appearance '+measured),button('Clear measurement',()=>{measured=null;appearance.remove();}));form.append(appearance);
    }
    const actions=element('div',null,'ink-dialog-actions'),cancel=button('Cancel',()=>dialog.close()),save=button('OK',null,'ink-action ink-confirm');
    save.type='submit';save.setAttribute('form',form.id);actions.append(cancel,save);dialog.append(actions);
    form.onsubmit=e=>{
      e.preventDefault();
      const color=status.value==='empty'?null:fields.color.value;
      // A scan of the previous ink cannot describe a newly assigned color.
      const keepMeasurement=color?.toLowerCase()===pen.color?.toLowerCase()&&status.value===pen.state&&status.value!=='empty';
      resolve({state:status.value,label:fields.label.value.trim(),color,measured_color:keepMeasurement?measured??null:null,width_mm:fields.width_mm.value===''?null:Number(fields.width_mm.value),speed_cm_s:Number(fields.speed_cm_s.value),force:Number(fields.force.value)});dialog.close();
    };
    body.append(form);dialog.addEventListener('close',()=>resolve(null),{once:true});
  });
}
// Grouping only affects presentation. Every source ID retains its own mapping.
export function groupInks(inks,mapping,mode='exact',strokeMode='preserve') {
  if(mode==='exact')return inks;
  const groups=new Map();
  for(const ink of inks){
    const color=mode==='pen'?'':colorOf(ink).slice(1).match(/../g).map(v=>Math.round(parseInt(v,16)/24)).join(',');
    const width=mode==='pen'||strokeMode==='pen'?'':ink.kind==='fill'?'fill':Math.round((ink.width_mm||0)/.05);
    const key=`${mapping[ink.id]||0}|${color}|${width}`;
    if(!groups.has(key))groups.set(key,[]);groups.get(key).push(ink);
  }
  return [...groups.values()].map(members=>{
    if(members.length===1)return members[0];
    const widths=members.map(i=>i.width_mm).filter(w=>w!=null),lo=Math.min(...widths),hi=Math.max(...widths);
    return {...members[0],id:'group-'+members[0].id,label:`${members.length} source styles`,members,
      width_label:widths.length?(hi-lo>1e-6?`${widthText(lo)}–${widthText(hi)}`:widthText(lo)):'Fill'};
  });
}

// IDs are document identities, not credentials. getRandomValues is available
// on HTTP origins too; randomUUID is restricted to secure contexts.
export function newInkId(prefix='ink') {
  const bytes=new Uint8Array(16);globalThis.crypto.getRandomValues(bytes);
  return prefix+'-'+Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
}
export function penTargets(carousel,extra=[]) {
  const targets=Array.from({length:8},(_,i)=>({id:i+1,slot:i+1,label:`Slot ${i+1}`,ink:carousel?.slots?.[i+1]||{state:'empty'}}));
  for(const ink of extra)if(!targets.some(t=>sameMaterial(t.ink,ink)))targets.push({id:'ink:'+ink.id,label:'Not loaded',ink,custom:true});
  return targets;
}
export const sameMaterial=(a,b)=>!!a&&!!b&&colorOf(a)?.toLowerCase()===colorOf(b)?.toLowerCase()&&a.width_mm===b.width_mm;
let activeTray=null;

// One source selection, followed by one destination selection. Mapping state
// lives only here, and is discarded when the owning document rerenders.
export function renderInkSets(host,options) {
  host._inkTray?.dispose();
  const {inks=[],carousel,mapping={},targets=penTargets(carousel),roleLabels=false,title='Document inks',onAssign,onCustom,onEditSlot,onRefresh,
    included,onInclude,isAccepted=()=>false,acceptedLabel='accepted',reviews=[],reviewHost,targetIssue=t=>t.slot&&!usable(t.ink)?'This slot is empty or unavailable.':'',customSeed,hideEmptyCustomTargets=false,allowOrder=true,toolbarHost}=options;
  const members=i=>i.members||[i],all=inks.flatMap(members),targetOf=i=>mapping[i.id]??0;
  let selected=null,panel=null,editor=false,working=false,error='',scrim=null,frame=0,disposed=false,toolbar=null;
  let oldScroll=host.querySelector('.ink-scroll')?.scrollLeft||0;
  const origin=document.activeElement;
  function dispose(){
    disposed=true;scrim?.remove();toolbar?.remove();cancelAnimationFrame(frame);
    if(reviewHost){reviewHost.replaceChildren();reviewHost.hidden=true;}
    document.removeEventListener('click',outside,true);document.removeEventListener('keydown',key,true);
    document.removeEventListener('scroll',shade,true);window.removeEventListener('resize',shade);
    if(activeTray===api){activeTray=null;document.body.classList.remove('ink-mapping-active');}
  }
  const api={dispose,cancel:()=>cancel(),review(id){
    if(reviewHost&&reviews.some(r=>String(r.id)===String(id))){host.dataset.reviewSelection=String(id);drawReviews();}
  }};host._inkTray=api;
  function cancel(){selected=null;panel=null;editor=false;error='';scrim?.remove();scrim=null;if(activeTray===api){activeTray=null;document.body.classList.remove('ink-mapping-active');}draw();if(origin?.isConnected)origin.focus({preventScroll:true});}
  function allowed(node){return host.contains(node)&&!!node.closest('.ink-selection-bar,.ink-selected-source,.ink-destination,.ink-inline-editor,.ink-scroll-nav');}
  function outside(e){
    if(selected&&!allowed(e.target)){e.preventDefault();e.stopImmediatePropagation();cancel();}
    else if(panel&&!host.contains(e.target)){panel=null;draw();}
  }
  function key(e){
    if((selected||panel)&&e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();cancel();}
    if(!selected)return;
    if(e.key==='Tab'){
      const items=[...host.querySelectorAll('button:not(:disabled),input,select')].filter(e=>allowed(e)&&e.getClientRects().length);
      if(!items.length)return;
      const at=items.indexOf(document.activeElement),next=(at+(e.shiftKey?-1:1)+items.length)%items.length;
      e.preventDefault();e.stopImmediatePropagation();items[next].focus();
    }
  }
  function shade(){
    if(!selected||disposed)return;
    cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{
      if(!selected||disposed)return;
      if(!scrim){scrim=document.createElementNS('http://www.w3.org/2000/svg','svg');scrim.classList.add('ink-scrim');scrim.setAttribute('aria-hidden','true');document.body.append(scrim);}
      const w=innerWidth,h=innerHeight;let path=`M0 0H${w}V${h}H0Z`;
      for(const e of host.querySelectorAll('.ink-selection-bar,.ink-selected-source,.ink-destination,.ink-inline-editor,.ink-scroll-nav')){
        let r=e.getBoundingClientRect(),left=Math.max(0,r.left-3),top=Math.max(0,r.top-3),right=Math.min(w,r.right+3),bottom=Math.min(h,r.bottom+3);
        for(let p=e.parentElement;p&&p!==document.body;p=p.parentElement){const style=getComputedStyle(p),b=p.getBoundingClientRect();if(/auto|scroll|hidden/.test(style.overflowX)){left=Math.max(left,b.left);right=Math.min(right,b.right);}if(/auto|scroll|hidden/.test(style.overflowY)){top=Math.max(top,b.top);bottom=Math.min(bottom,b.bottom);}}
        if(right>left&&bottom>top)path+=`M${left} ${top}H${right}V${bottom}H${left}Z`;
      }
      scrim.setAttribute('viewBox',`0 0 ${w} ${h}`);scrim.innerHTML=`<path fill="#17252e" fill-opacity=".54" fill-rule="evenodd" d="${path}"/>`;
    });
  }
  function begin(values){
    if(activeTray&&activeTray!==api)activeTray.cancel();activeTray=api;document.body.classList.add('ink-mapping-active');selected=values;panel=null;editor=false;error='';draw();
    host.querySelector('.ink-selection-bar')?.scrollIntoView({block:'nearest'});
    const available=targets.filter(t=>t.ink&&!targetIssue(t)),loaded=available.filter(t=>t.slot),choices=loaded.length?loaded:available;
    const nearestInk=nearest(values[0],choices.map(t=>t.ink)),suggestion=choices.find(t=>t.ink===nearestInk);
    const destination=[...host.querySelectorAll('.ink-loaded .pen-cap[data-target]')].find(e=>e.dataset.target===String(suggestion?.id));
    destination?.scrollIntoView({block:'nearest',inline:'center'});destination?.focus({preventScroll:true});shade();
  }
  function open(values){if(values.length===1)begin(values);else{panel={values,query:'',offset:0};draw();host.querySelector('.ink-member-tray')?.scrollIntoView({block:'nearest'});}}
  async function assign(target,custom){
    if(!selected||working)return;
    const issue=custom?'':targetIssue(target);if(issue){error=issue;draw();return;}
    const ids=selected.map(i=>i.id);working=true;error='';
    try{
      const result=await (custom?onCustom(ids,custom):onAssign(ids,target));
      if(disposed)return;
      if(result===false){error='The assignment was not applied. Choose another ink.';working=false;draw();return;}
      cancel();revealInk(host,ids[0]);
    }catch(e){if(!disposed){error=e.message;working=false;draw();}}
  }
  function reviewRow(review){
    const row=element('div',null,'ink-review');row.dataset.reviewInk=review.id;
    row.append(element('p',review.message));
    const actions=element('div',null,'ink-review-actions');
    for(const action of review.actions)actions.append(button(action.label,action.run));
    row.append(actions);return row;
  }
  function drawReviews(){
    if(!reviewHost)return;
    reviewHost.replaceChildren();reviewHost.hidden=!!selected||!reviews.length;
    if(reviewHost.hidden)return;
    const current=reviews.find(r=>String(r.id)===host.dataset.reviewSelection)||reviews[0];
    host.dataset.reviewSelection=String(current.id);
    const row=reviewRow(current);
    if(reviews.length>1){
      const picker=element('select');picker.setAttribute('aria-label','Pen change to review');
      for(const [i,review] of reviews.entries())picker.append(new Option(`Ink ${review.id} · ${i+1} of ${reviews.length} changes`,review.id));
      picker.value=String(current.id);picker.onchange=()=>{api.review(picker.value);reviewHost.querySelector('select')?.focus({preventScroll:true});};
      row.prepend(picker);
    }
    reviewHost.append(row);
  }
  function draw(){
    if(disposed)return;
    oldScroll=host.querySelector('.ink-scroll')?.scrollLeft??oldScroll;
    host.replaceChildren();host.classList.add('inksets');host.classList.toggle('ink-selecting',!!selected);
    toolbar?.remove();toolbar=element('div',null,selected?'ink-selection-bar':'ink-toolbar');
    if(selected){toolbar.append(cap(selected[0],null),element('strong',`${selected.length===1?(selected[0].label||'Ink '+selected[0].id):selected.length+' inks'} → choose an ink`),button('Cancel',cancel));}
    else{
      if(!roleLabels&&allowOrder){const order=element('select');order.setAttribute('aria-label','Pen arrangement');order.append(new Option('By carousel slot','mapped'),new Option('Document order','document'));order.value=host.dataset.inkOrder||'mapped';order.onchange=()=>{host.dataset.inkOrder=order.value;host.dataset.inkPage='0';panel=null;draw();};toolbar.append(order);}
      if(onRefresh)toolbar.append(button('Refresh carousel',onRefresh));
    }
    (selected?host:toolbarHost||host).append(toolbar);
    if(error){const message=element('p',error,'ink-selection-bar ink-error');message.setAttribute('role','alert');host.append(message);}
    if(panel)drawMembers();
    if(editor)drawEditor();
    host.append(element('div',title,'ink-row-label'));
    const scroll=element('div',null,'ink-scroll'),grid=element('div',null,'ink-grid');scroll.tabIndex=0;scroll.setAttribute('aria-label','Sources above destination inks; scroll for more pens');scroll.append(grid);host.append(scroll);
    let columns;
    const effectiveTargets=[...targets];if(all.some(i=>!effectiveTargets.some(t=>t.id===targetOf(i))))effectiveTargets.push({id:0,label:'Not loaded',ink:null});
    if(!roleLabels&&allowOrder&&host.dataset.inkOrder==='document'){
      columns=inks.map(i=>({target:effectiveTargets.find(t=>t.id===targetOf(members(i)[0]))||effectiveTargets.at(-1),sources:members(i)}));
      for(const t of effectiveTargets)if(!columns.some(c=>c.target.id===t.id))columns.push({target:t,sources:[]});
    }else columns=effectiveTargets.map(t=>({target:t,sources:all.filter(i=>targetOf(i)===t.id)}));
    if(hideEmptyCustomTargets&&!selected)columns=columns.filter(c=>!c.target.custom||c.sources.length);
    if(columns.length>100){const start=Math.min(Number(host.dataset.inkPage||0),Math.floor((columns.length-1)/100)*100);toolbar.append(element('span',`${start+1}–${Math.min(start+100,columns.length)} of ${columns.length}`));for(const [name,at] of [['Previous',start-100],['Next',start+100]])if(at>=0&&at<columns.length)toolbar.append(button(name,()=>{host.dataset.inkPage=at;draw();}));columns=columns.slice(start,start+100);}
    for(const {target,sources} of columns){
      const cell=element('div',null,'ink-pair');cell.dataset.inkIds=JSON.stringify(sources.map(i=>i.id));cell.dataset.targetId=target.id;
      const unresolved=sources.filter(i=>!equivalent(i,target.ink)&&!isAccepted(i,target.id,target.ink));
      if(!roleLabels&&unresolved.length)cell.classList.add('ink-difference');
      const chosen=selected&&sources.filter(i=>selected.some(s=>s.id===i.id)),visible=chosen?.length?chosen:sources;
      const top=element('div',null,roleLabels?'ink-role-list':'pen-stack');
      if(chosen?.length)top.classList.add('ink-selected-source');
      if(roleLabels){for(const ink of visible){const role=button(ink.label,()=>selected?cancel():begin([ink]),'ink-role');role.dataset.inkId=ink.id;top.append(role);}}
      else if(visible.length){
        const sorted=[...visible].sort((a,b)=>Number(unresolved.includes(b))-Number(unresolved.includes(a)));
        sorted.slice(0,3).reverse().forEach((ink,index)=>{const item=cap(ink,()=>selected?cancel():open(sources),visible.length>1?`${visible.length} document inks · select a member`:`${chosen?.length?'Selected':'Assign'} ${ink.label||'ink '+ink.id}`);item.dataset.inkId=ink.id;item.style.setProperty('--stack',Math.min(3,sorted.length)-1-index);if(included&&!included.includes(ink.id))item.classList.add('ink-excluded');top.append(item);});
        if(visible.length>1)top.append(button(String(visible.length),()=>selected?cancel():open(sources),'ink-count'));
      }else top.append(element('span','—','ink-blank'));
      cell.append(top);
      if(!roleLabels){const delta=button(sources.length?(target.ink?(difference(visible[0],target.ink)||'Matched'):'Not loaded'):'',()=>open(sources),'ink-delta');delta.disabled=!sources.length||!!selected;if(sources.length&&!unresolved.length&&difference(visible[0],target.ink))delta.textContent+=' · '+acceptedLabel;cell.append(delta);}
      const lower=element('div',null,'ink-loaded');if(selected&&target.ink)lower.classList.add('ink-destination');lower.append(element('small',target.label));
      if(target.ink){const targetCap=cap(target.ink,selected?()=>assign(target):null,`${target.slot?'Loaded pen — slot '+target.slot:target.ink.label||colorName(colorOf(target.ink))}: ${target.ink.label||colorName(colorOf(target.ink))} · ${widthText(target.ink.width_mm)} mm`);targetCap.dataset.target=target.id;if(target.slot)targetCap.dataset.slot=target.slot;targetCap.disabled=!selected||!!targetIssue(target)||working;targetCap.title=targetIssue(target)||targetCap.title;lower.append(targetCap,element('small',target.ink.state==='empty'?'Empty':target.ink.state==='unavailable'?'Unavailable':target.ink.label||colorName(colorOf(target.ink))));}
      if(!selected&&onEditSlot&&target.slot){const edit=button('Edit',()=>onEditSlot(target.slot),'ink-edit-slot');edit.setAttribute('aria-label',`Edit loaded pen — slot ${target.slot}`);lower.append(edit);}
      cell.append(lower);
      if(onInclude&&sources.length===1&&!selected){const ink=sources[0],label=element('label','Include','ink-include'),check=element('input');check.type='checkbox';check.checked=included.includes(ink.id);check.setAttribute('aria-label',`Include ink ${ink.id}`);check.onchange=()=>onInclude(ink.id,check.checked);label.prepend(check);cell.append(label);}
      grid.append(cell);
    }
    if(onCustom){const cell=element('div',null,'ink-pair ink-add'),lower=element('div',null,'ink-loaded'),add=button('+',()=>{editor=true;draw();host.querySelector('.ink-inline-editor input')?.focus();},'pen-cap');add.disabled=!selected;add.setAttribute('aria-label','Create and assign a custom ink');if(selected)lower.classList.add('ink-destination');lower.append(add,element('small','New ink'));cell.append(lower);grid.append(cell);}
    host.append(element('div','Inks · loaded slots and document colors','ink-row-label'));
    const nav=element('div',null,'ink-scroll-nav');for(const [label,direction] of [['←',-1],['→',1]]){const move=button(label,()=>{scroll.scrollBy({left:direction*180,behavior:'smooth'});},'ink-action');move.setAttribute('aria-label',direction<0?'Scroll inks left':'Scroll inks right');nav.append(move);}host.append(nav);
    scroll.scrollLeft=oldScroll;requestAnimationFrame(()=>{if(!disposed){nav.hidden=scroll.scrollWidth<=scroll.clientWidth+1;shade();}});
    if(reviewHost)drawReviews();else if(!selected)for(const review of reviews)host.append(reviewRow(review));
  }
  function drawMembers(){
    const tray=element('section',null,'ink-member-tray');tray.setAttribute('aria-label',`${panel.values.length} document inks`);
    const head=element('div',null,'ink-toolbar');head.append(element('strong',`${panel.values.length} document inks`),button('Close',()=>{panel=null;draw();}),button(`Select all ${panel.values.length}`,()=>begin(panel.values)));tray.append(head);
    const search=element('input');search.placeholder='Find color or width';search.setAttribute('aria-label','Find source ink');search.value=panel.query;tray.append(search);
    const list=element('div',null,'ink-members');tray.append(list);host.append(tray);
    const render=()=>{const filtered=panel.values.filter(i=>`${i.label||''} ${colorOf(i)} ${i.width_mm}`.toLowerCase().includes(panel.query.toLowerCase()));list.replaceChildren();
      for(const ink of filtered.slice(panel.offset,panel.offset+100)){
        const row=button('',()=>begin([ink]),'ink-member-choice'),disc=cap(ink,null);disc.removeAttribute('disabled');disc.tabIndex=-1;const icon=element('span');icon.className=disc.className;icon.style.cssText=disc.style.cssText;icon.textContent=disc.textContent;
        row.dataset.sourceId=ink.id;row.append(icon,element('span',`${ink.label||colorName(colorOf(ink))} · ${ink.width_mm??'?'} mm`));list.append(row);
        if(onInclude){const label=element('label','Include','ink-include'),check=element('input');check.type='checkbox';check.checked=included.includes(ink.id);check.setAttribute('aria-label',`Include ink ${ink.id}`);check.onchange=()=>onInclude(ink.id,check.checked);label.prepend(check);list.append(label);}
      }
      if(filtered.length>100){list.append(element('p',`${panel.offset+1}–${Math.min(panel.offset+100,filtered.length)} of ${filtered.length}`));for(const [name,at] of [['Previous',panel.offset-100],['Next',panel.offset+100]])if(at>=0&&at<filtered.length)list.append(button(name,()=>{panel.offset=at;render();}));}
    };search.oninput=()=>{panel.query=search.value;panel.offset=0;render();};render();
  }
  function drawEditor(){
    const form=element('div'),seed=customSeed?.(selected)||selected[0];form.setAttribute('role','group');form.setAttribute('aria-label','Create and assign ink');form.append(element('strong','Create and assign ink'));
    form.append(customInkFields(seed,ink=>assign(null,ink),()=>{editor=false;draw();}));host.append(form);
  }
  document.addEventListener('click',outside,true);document.addEventListener('keydown',key,true);document.addEventListener('scroll',shade,true);window.addEventListener('resize',shade);draw();
}
export function revealInk(host,id) {
  host._inkTray?.review(id);
  const pair=[...host.querySelectorAll('.ink-pair[data-ink-ids]')].find(e=>JSON.parse(e.dataset.inkIds).includes(id));
  pair?.scrollIntoView({block:'nearest',inline:'nearest'});if(pair){pair.classList.add('ink-just-assigned');setTimeout(()=>pair.classList.remove('ink-just-assigned'),1200);}
}

function customInkFields(seed,applyInk,cancel){
  const form=element('div',null,'ink-inline-editor'),fields={};
    for(const [key,label,type] of [['label','Name','text'],['color_srgb','Color','color'],['width_mm','Diameter · mm','number']]){
      const row=element('label',label),input=element('input');input.type=type;input.name=key;input.value=key==='color_srgb'?colorOf(seed)||'#000000':seed[key]??'';input.required=true;
      if(type==='number'){input.min='.02';input.max='5';input.step='any';}if(type==='text')input.maxLength=80;row.append(input);form.append(row);fields[key]=input;
    }
    fields.label.oninput=()=>{const color=colors[fields.label.value.toLowerCase()];if(color){fields.color_srgb.value=color;fields.color_srgb.setCustomValidity('');}};fields.color_srgb.oninput=()=>fields.color_srgb.setCustomValidity('');
    const apply=()=>{if(fields.color_srgb.value.toLowerCase()==='#ffffff')fields.color_srgb.setCustomValidity('White clears paper; choose a colored ink.');if(!Object.values(fields).every(input=>input.reportValidity()))return;applyInk({label:fields.label.value.trim(),color_srgb:fields.color_srgb.value,width_mm:Number(fields.width_mm.value)});};
    form.append(button('Apply ink',apply),button('Cancel new ink',cancel));
    form.onkeydown=e=>{if(e.key==='Enter'&&e.target.tagName==='INPUT'){e.preventDefault();e.stopPropagation();apply();}};
  return form;
}
// Ordered authoring palette, independent of physical slots. Choosing a pen
// copies a material; this component never writes the loaded carousel.
export function renderInkSequence(host,{selected,choices,onChange}){
  host._inkTray?.dispose();delete host._inkTray;host.classList.remove('ink-selecting');
  host.classList.add('inksets');host.replaceChildren();
  const toolbar=element('div',null,'ink-toolbar'),all=button('All loaded',()=>onChange(choices.map(c=>c.ink)));all.disabled=!choices.length;toolbar.append(element('strong','Ink sequence'),all);host.append(toolbar);
  const scroll=element('div',null,'ink-scroll'),row=element('div',null,'ink-grid');scroll.append(row);host.append(scroll);
  const options=[...selected.map((ink,selectedIndex)=>({ink,selectedIndex,label:choices.find(c=>sameMaterial(c.ink,ink))?.label||'Document ink'})),...choices.filter(c=>!selected.some(i=>sameMaterial(i,c.ink)))];
  for(const {ink,label,selectedIndex} of options){
    const index=selectedIndex??-1,cell=element('div',null,'ink-sequence-choice');
    const toggle=cap(ink,()=>onChange(index<0?[...selected,ink]:selected.filter((_,i)=>i!==index)),`${label} · ${ink.label||colorName(colorOf(ink))} · ${widthText(ink.width_mm)} mm`);
    toggle.setAttribute('aria-pressed',String(index>=0));toggle.disabled=index===0&&selected.length===1;
    cell.append(toggle,element('small',index<0?label:`${index+1} · ${label}`));
    if(index>=0){const moves=element('div',null,'ink-sequence-moves');for(const [text,delta] of [['←',-1],['→',1]]){const move=button(text,()=>{const next=selected.slice();[next[index],next[index+delta]]=[next[index+delta],next[index]];onChange(next);});move.disabled=index+delta<0||index+delta>=selected.length;move.setAttribute('aria-label',`Move ink ${index+1} ${delta<0?'earlier':'later'}`);moves.append(move);}cell.append(moves);}
    row.append(cell);
  }
  const add=button('+',()=>{add.disabled=true;const editor=customInkFields({},ink=>onChange([...selected,ink]),()=>{editor.remove();add.disabled=false;});host.append(editor);editor.querySelector('input').focus();},'pen-cap');add.setAttribute('aria-label','Add document ink');row.append(add);
}
