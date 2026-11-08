// Shared document chrome. Studios own their recipes and persistence adapters.
// Call before wiring a Studio's history controls. Existing host children are
// deliberate preview-specific extensions; common controls and order live here.
export function renderPreviewControls(host,{onFit,onZoom,fitId='preview-fit',undoId='undo',redoId='redo',idPrefix='',showHistory=true}={}){
  const extensions=[...host.childNodes];
  host.replaceChildren();host.className='view-actions';
  host.setAttribute('role','group');host.setAttribute('aria-label','Preview tools');
  const common=document.createElement('div');common.className='preview-common-controls';
  const zoom=document.createElement('div');zoom.className='preview-zoom-controls';
  const history=document.createElement('div');history.className='preview-history-controls';
  common.append(zoom,history);
  const controls={};
  for(const [action,id,label,title,handler] of [
    ['zoom-out','zoom-out','−','Zoom out (−)',()=>onZoom(.8)],
    ['fit',fitId,'Fit','Fit page (0)',onFit],
    ['zoom-in','zoom-in','+','Zoom in (+)',()=>onZoom(1.25)],
    ['undo',undoId,'↶','Undo (Ctrl/Cmd+Z)'],
    ['redo',redoId,'↷','Redo (Ctrl/Cmd+Shift+Z)'],
  ]){
    if(!showHistory&&(action==='undo'||action==='redo'))continue;
    const button=document.createElement('button');button.type='button';button.id=idPrefix+id;
    button.dataset.previewAction=action;button.textContent=label;button.title=title;
    button.setAttribute('aria-label',{fit:'Fit page','zoom-in':'Zoom in','zoom-out':'Zoom out',undo:'Undo',redo:'Redo'}[action]);
    button.disabled=action==='undo'||action==='redo';if(handler)button.onclick=handler;
    controls[action]=button;(action==='undo'||action==='redo'?history:zoom).append(button);
  }
  host.append(common);
  if(extensions.some(node=>node.nodeType===1)){
    const extra=document.createElement('div');extra.className='preview-extra-controls';
    extra.append(...extensions);host.append(extra);
  }
  return controls;
}

let activeNotice;
// One notice surface per page, including navigation. Dismissing a notice keeps
// repeated status renders from reopening it; clearing starts a new operation.
// The returned dismiss function owns only this notice, never its replacement.
export function studioNotice(message='',{kind='message',timeout=0,actions=[],key,source='app'}={}) {
  const text=String(message?.message??message??'');
  const identity=key??JSON.stringify([source,text,kind,actions.map(action=>[action.label,action.href])]);
  if(text&&activeNotice?.identity===identity)return activeNotice.dismiss;
  if(!text&&activeNotice&&activeNotice.source!==source)return ()=>{};
  clearTimeout(activeNotice?.timer);
  let host=document.getElementById('notice');
  if(!text){activeNotice=null;if(host){host.replaceChildren();host.hidden=true;}return ()=>{};}
  if(!host){host=document.createElement('div');host.id='notice';}
  host.classList.add('studio-notice');host.setAttribute('role','status');host.setAttribute('aria-live','polite');
  document.body.append(host);host.dataset.kind=kind;host.hidden=false;
  const origin=document.activeElement,state={identity,source};
  state.dismiss=()=>{
    if(activeNotice!==state)return;
    clearTimeout(state.timer);
    const focused=host.contains(document.activeElement);
    host.hidden=true;host.replaceChildren();
    if(focused&&origin?.isConnected)origin.focus();
  };
  activeNotice=state;
  const content=document.createElement('div');content.className='studio-notice-content';
  const body=document.createElement('div');body.textContent=text;content.append(body);
  if(actions.length){
    const links=document.createElement('div');links.className='studio-notice-actions';
    for(const action of actions){
      const control=document.createElement(action.href?'a':'button');control.textContent=action.label;
      if(action.href)control.href=action.href;else{control.type='button';control.onclick=action.onClick;}
      links.append(control);
    }
    content.append(links);
  }
  const close=document.createElement('button');close.type='button';close.className='studio-notice-close';close.textContent='×';
  close.setAttribute('aria-label','Dismiss message');close.title='Dismiss message';close.onclick=state.dismiss;
  host.replaceChildren(content,close);
  if(timeout>0)state.timer=setTimeout(state.dismiss,timeout);
  return state.dismiss;
}
export const stable = value => JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
export function renderDocumentTabs(host,{documents,active,name,dirty,select,close,activity=()=>null,safe=fn=>fn}) {
  const selectedChanged=host.dataset.selectedDocument!==(active?.key||'');
  host.dataset.selectedDocument=active?.key||'';
  host.replaceChildren(...documents.map(doc=>{
    const wrapper=document.createElement('div');wrapper.className='document-tab'+(doc===active?' selected':'');
    const tab=document.createElement('button');tab.type='button';tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(doc===active));tab.tabIndex=doc===active?0:-1;
    tab.textContent=name(doc)+(dirty(doc)?' *':'');tab.title=tab.textContent+(doc.saving?' · Saving…':'');tab.dataset.document=doc.key;
    const status=activity(doc);
    if(status){
      const marker=document.createElement('span');marker.className='document-activity';marker.textContent='✎';marker.setAttribute('aria-hidden','true');
      wrapper.dataset.activity=status.running?'running':'waiting';tab.prepend(marker);tab.title+=' · '+status.label;tab.setAttribute('aria-description',status.label);
    }
    tab.onclick=safe(()=>select(doc));
    tab.onkeydown=event=>{const delta=event.key==='ArrowRight'?1:event.key==='ArrowLeft'?-1:0;if(!delta)return;event.preventDefault();Promise.resolve(select(documents[(documents.indexOf(doc)+delta+documents.length)%documents.length])).then(()=>host.querySelector('[aria-selected=true]')?.focus());};
    const button=document.createElement('button');button.type='button';button.className='close-tab';button.textContent='×';button.setAttribute('aria-label','Close '+name(doc));button.onclick=safe(()=>close(doc));
    wrapper.append(tab,button);return wrapper;
  }));
  // A handoff can append a tab beyond the horizontally scrolled tab strip.
  // Reveal a new selection without scrolling the surrounding page or stealing focus.
  if(selectedChanged)requestAnimationFrame(()=>{
    const selected=host.querySelector('[aria-selected=true]')?.parentElement;if(!selected)return;
    const bounds=host.getBoundingClientRect(),tab=selected.getBoundingClientRect();
    if(tab.right>bounds.right)host.scrollLeft+=tab.right-bounds.right;
    else if(tab.left<bounds.left)host.scrollLeft+=tab.left-bounds.left;
  });
}
export function renderDocumentLibrary(host,{items,query='',isOpen=()=>false,open,empty='No saved documents yet.',safe=fn=>fn}) {
  const filtered=items.filter(item=>item.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  host.replaceChildren(...filtered.map(item=>{
    const row=document.createElement('article');row.className='document-card';row.dataset.document=item.key||item.id;
    const thumbnail=document.createElement('div');thumbnail.className='thumbnail';
    if(item.thumbnail_url){const img=document.createElement('img');img.src=item.thumbnail_url;img.alt='';img.loading='lazy';img.onerror=()=>thumbnail.replaceChildren(document.createTextNode('Preview unavailable'));thumbnail.append(img);}else thumbnail.textContent='Preview not available yet';
    const body=document.createElement('div');body.className='card-info';const title=document.createElement('strong');title.textContent=item.title;title.title=item.title;
    const info=document.createElement('small');info.textContent=item.detail||'';
    const button=document.createElement('button');button.type='button';button.textContent=isOpen(item)?'Switch to document':'Open';button.onclick=safe(()=>open(item));
    body.append(title,info,button);row.append(thumbnail,body);return row;
  }));
  if(!filtered.length){const message=document.createElement('p');message.textContent=query?'No matching documents.':empty;host.append(message);}
}
export function chooseDocumentName({dialog,form,input,heading,cancel},name,copy=false) {
  heading.textContent=copy?'Save document as':'Save document';input.value=name;dialog.returnValue='cancel';
  return new Promise(resolve=>{
    form.onsubmit=event=>{event.preventDefault();if(input.value.trim())dialog.close('save');};cancel.onclick=()=>dialog.close('cancel');
    dialog.addEventListener('close',()=>resolve(dialog.returnValue==='save'?input.value.trim():null),{once:true});dialog.showModal();input.focus();input.select();
  });
}
export function confirmDocumentClose(dialog,message,name) {
  message.textContent='Save changes to “'+name+'” before closing?';dialog.returnValue='cancel';
  return new Promise(resolve=>{dialog.addEventListener('close',()=>resolve(dialog.returnValue),{once:true});dialog.showModal();});
}
export function editHistory(initial,limit=100) {
  let past=[],present=structuredClone(initial),future=[];
  return {
    get value(){return structuredClone(present);},get canUndo(){return !!past.length;},get canRedo(){return !!future.length;},
    record(next,{coalesce=false}={}){if(stable(next)===stable(present))return false;if(!coalesce||!past.length)past.push(present);past=past.slice(-limit);present=structuredClone(next);future=[];return true;},
    refresh(map){past=past.map(map);present=map(present);future=future.map(map);},
    undo(){if(!past.length)return null;future.push(present);present=past.pop();return structuredClone(present);},
    redo(){if(!future.length)return null;past.push(present);present=future.pop();return structuredClone(present);}
  };
}
export function renderStudioProgress({bar,detail,time},view) {
  if(!view)return;
  if(view.percent==null)bar.removeAttribute('value');else{bar.max=100;bar.value=Math.max(0,Math.min(100,view.percent));}
  bar.setAttribute('aria-valuetext',view.percent==null?'In progress':Math.floor(view.percent)+'% estimated');
  if(detail.textContent!==view.text){detail.textContent=view.text;detail.title=view.text;}
  const seconds=view.elapsed==null?null:Math.max(0,Math.floor(view.elapsed+(view.running?(performance.now()-view.received)/1000:0)));
  time.textContent=seconds==null?'':(seconds<60?`${seconds}s`:`${Math.floor(seconds/60)}m ${seconds%60}s`)+' elapsed';
}
export function wireStudioTrays(root=document) {
  const trays=[...root.querySelectorAll('.tray')];
  for(const tray of trays){
    tray.addEventListener('toggle',()=>{if(tray.open)for(const other of trays)if(other!==tray)other.open=false;});
    tray.querySelector('.close-tray')?.addEventListener('click',()=>{tray.open=false;tray.querySelector('summary').focus();});
  }
  window.addEventListener('keydown',event=>{if(event.key==='Escape')for(const tray of trays)tray.open=false;});
}

// Move browser state once; removing the old pending-plot key prevents an old
// request from being resurrected after the new client reconciles and clears it.
export function migrateStudioStorage(storage,key) {
  const legacy=key.replace(/^plotline/, 'squidprint');
  try {
    const value=storage.getItem(legacy);
    if(value!==null){if(storage.getItem(key)===null)storage.setItem(key,value);storage.removeItem(legacy);}
  } catch {} // Existing unavailable/full-storage handling remains with callers.
}
