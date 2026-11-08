import {studioNotice} from '/studio.js';
// One navigation policy for all studios. Document selection stays inside each
// studio's workspace; it never replaces an open editor or its undo history.
export const studios={
  map:{name:'Map Studio',path:'/map',color:'#59795b',description:'Design a map'},
  patterns:{name:'Pattern Studio',path:'/patterns',color:'#9b526d',description:'Draw fractals and mathematical patterns'},
  mazes:{name:'Maze Studio',path:'/mazes',color:'#377f87',description:'Grow mazes in custom shapes'},
  pdf:{name:'PDF Studio',path:'/pdf',color:'#526f9a',description:'Turn artwork into pen strokes'},
  hpgl:{name:'HPGL Studio',path:'/hpgl',color:'#a87935',description:'Choose physical pens and plot'}
};
const destinations={...studios,home:{name:'Plotline',path:'/'}};
let current;
const appOrigin=new URL(document.querySelector('meta[name=plotline-root]')?.content||location.origin).origin;
export function studioURL(studio,value){
  if(!destinations[studio])throw Error('Unknown studio');
  const url=new URL(value||destinations[studio].path,location.origin);
  if(!['http:','https:'].includes(url.protocol))throw Error('Invalid studio address');
  // Receipts retain old dev addresses. IDs belong to this Plotline installation.
  const root=new URL(appOrigin);url.protocol=root.protocol;url.host=root.host;
  url.pathname=destinations[studio].path;
  return url;
}
export const openStudio=(studio,url)=>current.navigate(studio,url);
export const studioTitle=name=>current?.title(name);
export function mapSourceURL(authoring,{snapshot=false}={}){
  const url=studioURL('map');
  if(authoring.document_id&&!snapshot)url.searchParams.set('document',authoring.document_id);
  if(authoring.recipe_hash)url.searchParams.set('source_recipe',authoring.recipe_hash);
  if(snapshot)url.searchParams.set('snapshot','1');
  return url.href;
}
export function sourceLinks({authoring,pdf}={}){
  const links=[];
  if(['pattern-studio','maze-studio'].includes(authoring?.application)){
    const kind=authoring.application==='maze-studio'?'mazes':'patterns',noun=kind==='mazes'?'maze':'pattern';
    const url=studioURL(kind);
    if(authoring.document_id)url.searchParams.set('document',authoring.document_id);
    if(authoring.recipe_hash)url.searchParams.set('source_recipe',authoring.recipe_hash);
    links.push({studio:kind,label:'Edit '+noun,url:url.href});
    if(authoring.document_id){url.searchParams.set('snapshot','1');links.push({studio:kind,label:'View source '+noun+' version',url:url.href});}
  }
  if(authoring?.application==='map-studio'){
    links.push({studio:'map',label:'Edit map',url:mapSourceURL(authoring)});
    if(authoring.document_id)links.push({studio:'map',label:'View source map version',url:mapSourceURL(authoring,{snapshot:true})});
  }
  if(pdf){
    const url=studioURL('pdf');url.searchParams.set('document',pdf.document_id);url.searchParams.set('page',pdf.page_index||0);
    if(pdf.conversion_id)url.searchParams.set('source_conversion',pdf.conversion_id);
    links.push({studio:'pdf',label:'Edit PDF recipe',url:url.href});
    if(pdf.conversion_id){url.searchParams.delete('source_conversion');url.searchParams.set('conversion',pdf.conversion_id);url.searchParams.set('snapshot','1');links.push({studio:'pdf',label:'View source PDF version',url:url.href});}
  }
  return links;
}
export function studioNavigation({studio=null,open=()=>{},exportWorkspace,importWorkspace}={}){
  const id=Date.now().toString(36)+'-'+Math.random().toString(36).slice(2),peers=new Map(),handles=new Map(),queue=[],pending=new Map(),received=new Set();
  const related=new Set();window.__plotlineRelatedWindows=related;
  const channel=typeof BroadcastChannel==='function'?new BroadcastChannel('plotline-studios-v1'):null;
  const destination=studio||'home',name='plotline-'+destination+'-'+id,self={id,studio:destination,name,navigation:2};let ready=false,chain=Promise.resolve();
  window.name=name;if(studio)document.body.dataset.studio=studio;
  function registerWithRelated(){
    // Reusing a named tab can change its opener (notably in Firefox). Keep
    // that reference before it changes, and reconnect every retained neighbor
    // after reload instead of relying on the browser's mutable opener tree.
    try{if(window.opener&&!window.opener.closed)related.add(window.opener);}catch{}
    for(const handle of related)try{
      if(handle.closed){related.delete(handle);continue;}
      if(handle.location.origin===location.origin)handle.postMessage({type:'plotline-window',...self},location.origin);
    }catch{}
  }
  const announce=()=>{channel?.postMessage({type:'presence',...self});registerWithRelated();};
  function receive(url){
    if(!ready){queue.push(url);return;}
    chain=chain.then(()=>open(new URL(url))).catch(error=>report(error.message));
  }
  let dismissFocusNotice;
  function report(text,url,{temporary=false}={}){
    const dismiss=studioNotice(text,{kind:temporary?'info':'message',timeout:temporary?5000:0,
      actions:url?[{label:'Open here',href:url}]:[],key:Symbol(),source:'navigation'});
    if(temporary)dismissFocusNotice=dismiss;
  }
  function accept(data,reply){
    if(data?.type!=='plotline-open'||data.studio!==destination)return;
    try{
      const url=new URL(data.url);if(url.origin!==location.origin||url.pathname!==destinations[destination].path)return;
      if(!received.has(data.request)){
        received.add(data.request);if(received.size>300)received.delete(received.values().next().value);
        receive(url.href);window.focus();
      }
      reply({type:'plotline-ack',request:data.request,id,focused:document.hasFocus()});
    }catch{}
  }
  function acknowledged(data){
    const request=pending.get(data.request);if(!request||request.peer&&request.peer!==data.id)return;
    clearInterval(request.timer);pending.delete(data.request);
    if(data.focused===false)setTimeout(()=>{if(document.hasFocus())report('Opened in your existing '+destinations[request.studio].name+' tab; the browser kept this tab in front.',undefined,{temporary:true});},200);
  }
  if(channel)channel.onmessage=({data})=>{
    if(!data||data.id===id)return;
    if(data.type==='hello'){announce();return;}
    if(data.type==='presence'&&destinations[data.studio]&&typeof data.name==='string')peers.set(data.id,{...data,seen:Date.now()});
    if(data.type==='bye')peers.delete(data.id);
    if(data.to!==id)return;
    if(data.type==='plotline-open')accept(data,reply=>channel.postMessage({...reply,to:data.id}));
    if(data.type==='plotline-ack')acknowledged(data);
  };
  window.addEventListener('message',event=>{
    const legacy=document.querySelector('meta[name=plotline-legacy-map-origin]')?.content;
    if(event.data?.type==='plotline-map-transfer'&&studio==='map'&&ready&&importWorkspace&&event.origin===legacy){
      if(!received.has(event.data.request)){
        received.add(event.data.request);
        chain=chain.then(()=>importWorkspace(event.data.workspace)).then(()=>event.source?.postMessage({type:'plotline-map-transferred',request:event.data.request},event.origin)).catch(error=>{received.delete(event.data.request);report(error.message);});
      }
      return;
    }
    if(event.origin!==location.origin)return;
    if(event.data?.type==='plotline-window'&&event.source){related.add(event.source);return;}
    if(event.data?.type==='plotline-ack'){acknowledged(event.data);return;}
    accept(event.data,reply=>event.source?.postMessage(reply,event.origin));
  });
  channel?.postMessage({type:'hello',id});announce();
  const heartbeat=setInterval(announce,5000);
  window.addEventListener('pagehide',event=>{channel?.postMessage({type:'bye',id});if(!event.persisted){clearInterval(heartbeat);channel?.close();}});window.addEventListener('pageshow',announce);window.addEventListener('focus',announce);
  const findPeer=target=>[...peers.values()].filter(p=>p.studio===target&&Date.now()-p.seen<120000).sort((a,b)=>b.seen-a.seen)[0];
  function knownHandle(target,peer){
    // Only use actual references. Looking up an unrelated tab by window.name
    // can CREATE a new tab even when BroadcastChannel confirms it exists.
    const queue=[handles.get(target),window.opener,...related],seen=new Set([window]);
    for(let i=0;i<queue.length&&i<64;i++)try{
      const handle=queue[i];if(!handle||seen.has(handle))continue;seen.add(handle);
      if(handle.closed){related.delete(handle);continue;}
      if(handle.location.origin!==location.origin)continue;
      if(handle.location.pathname===destinations[target].path&&(!peer||handle.name===peer.name))return handle;
      queue.push(handle.opener,...(handle.__plotlineRelatedWindows||[]));
    }catch{}
    return null;
  }
  function handoff(target,url,peer,handle){
    if(peer&&peer.navigation!==2&&!handle){report('Reload your existing '+destinations[target].name+' tab once to enable tab reuse.');return;}
    const request=id+'-'+Math.random().toString(36).slice(2);let tries=0;
    const send=()=>{
      if(++tries>40){clearInterval(pending.get(request)?.timer);pending.delete(request);report('Could not reach the existing '+destinations[target].name+' tab. Select it and reload if needed.',url.href);return;}
      const data={type:'plotline-open',id,to:peer?.id,studio:target,url:url.href,request};
      if(peer?.navigation===2)channel.postMessage(data);
      else if(handle&&!handle.closed)handle.postMessage(data,location.origin);
    };
    pending.set(request,{timer:setInterval(send,150),peer:peer?.navigation===2?peer.id:null,studio:target});
    if(handle){
      related.add(handle);
      // A verified reference proves this is a related window. Let a direct
      // user action target it natively; never do this for a discovered name alone.
      try{
        handle.postMessage({type:'plotline-window',...self},location.origin);
        if(handle.name&&navigator.userActivation?.isActive){
          const raised=window.open('',handle.name);
          if(raised&&raised!==handle&&raised.location.href==='about:blank')raised.close();
        }
        handle.focus();
      }catch{}
    }
    send();
  }
  async function navigate(target,value){
    dismissFocusNotice?.();dismissFocusNotice=null;
    const url=studioURL(target,value);
    if(appOrigin!==location.origin){window.open(url.href,'plotline-legacy-'+target);return;}
    if(target===destination){receive(url.href);return;}
    let peer=findPeer(target),handle=knownHandle(target,peer);
    if(!peer&&!handle&&channel){
      // Discovery may still be in flight after opening the landing page. Probe
      // before deciding to open anything; don't rely on a five-second heartbeat.
      channel.postMessage({type:'hello',id});
      await new Promise(resolve=>setTimeout(resolve,120));
      peer=findPeer(target);handle=knownHandle(target,peer);
    }
    if(peer||handle){handoff(target,url,peer,handle);return;}
    handle=handles.get(target);
    // A second click while a newly opened studio loads uses the saved handle.
    if(handle&&!handle.closed){try{if(handle.location.href==='about:blank'){handle.location.replace(url.href);handle.focus();return;}}catch{}}
    handle=window.open(url.href,'plotline-launch-'+target);
    if(!handle){report('The browser blocked opening '+destinations[target].name+'.',url.href);return;}
    handles.set(target,handle);related.add(handle);handle.focus();
  }
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[data-studio]');
    if(!link||!link.hasAttribute('href')||link.getAttribute('aria-disabled')==='true'||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    event.preventDefault();navigate(link.dataset.studio,link.href);link.closest('details')?.removeAttribute('open');
  });
  const header=document.querySelector('.masthead');let sources;
  if(studio&&header){
    const brand=header.querySelector('.brand');brand.href=studioURL('home');brand.dataset.studio='home';brand.replaceChildren(document.createTextNode(studios[studio].name));const small=document.createElement('small');small.textContent='Plotline';brand.append(small);
    const menu=document.createElement('details');menu.className='studio-switcher';menu.innerHTML='<summary aria-label="Switch studio" title="Switch studio">⌄</summary><nav aria-label="Studios"></nav>';
    for(const [key,value] of Object.entries(studios)){const a=document.createElement('a');a.href=studioURL(key);a.dataset.studio=key;a.textContent=value.name;a.style.setProperty('--studio-color',value.color);if(key===studio)a.setAttribute('aria-current','page');menu.querySelector('nav').append(a);}
    const home=document.createElement('a');home.href=studioURL('home');home.dataset.studio='home';home.textContent='Plotline home';menu.querySelector('nav').append(home);const identity=document.createElement('div');identity.className='studio-identity';brand.before(identity);identity.append(brand,menu);
    sources=document.createElement('details');sources.id='studio-sources';sources.className='studio-switcher studio-sources';sources.hidden=true;sources.innerHTML='<summary aria-label="Edit source" title="Edit source">Edit source</summary><nav aria-label="Source documents"></nav>';header.querySelector('.header-actions').prepend(sources);
    document.addEventListener('click',event=>{for(const item of [menu,sources])if(!item.contains(event.target))item.open=false;});document.addEventListener('keydown',event=>{if(event.key==='Escape'){menu.open=false;sources.open=false;}});
    const icon=document.createElement('link');icon.rel='icon';icon.href='data:image/svg+xml,'+encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="${studios[studio].color}"/><text x="16" y="23" fill="white" font-family="sans-serif" font-size="23" text-anchor="middle">${studio==='map'?'M':studio==='pdf'?'P':studio==='patterns'?'∿':studio==='mazes'?'▥':'H'}</text></svg>`);document.head.append(icon);
  }
  if(studio==='map'&&exportWorkspace&&appOrigin!==location.origin){
    const dismiss=studioNotice('Map Studio now shares the Plotline address.',{source:'navigation',actions:[{label:'Move open documents',onClick:()=>{
      const workspace=exportWorkspace(),target=window.open(appOrigin+'/map','plotline-map-transfer');
      if(!target){report('The browser blocked opening Map Studio.',appOrigin+'/map');return;}
      const request=id+'-transfer';let tries=0;
      const timer=setInterval(()=>{if(++tries>120){clearInterval(timer);window.removeEventListener('message',done);report('Could not transfer the documents. Your original workspace is still here.');return;}target.postMessage({type:'plotline-map-transfer',request,workspace},appOrigin);},250);
      const done=event=>{if(event.source===target&&event.origin===appOrigin&&event.data?.type==='plotline-map-transferred'&&event.data.request===request){clearInterval(timer);window.removeEventListener('message',done);dismiss();target.focus();}};window.addEventListener('message',done);
    }}]});
  }
  current={navigate,ready(){ready=true;for(const url of queue.splice(0))receive(url);},setSources(links){if(!sources)return;sources.hidden=!links.length;const nav=sources.querySelector('nav');nav.replaceChildren();for(const link of links){const a=document.createElement('a');a.textContent=link.label;a.href=studioURL(link.studio,link.url);a.dataset.studio=link.studio;nav.append(a);}},title(documentName=''){document.title=(studio?studios[studio].name:'Plotline')+(documentName?' · '+documentName:'')+' — Plotline';}};
  return current;
}
