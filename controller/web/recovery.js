import {previewNavigation} from '/preview-navigation.js';
import {renderPreviewControls} from '/studio.js';

export function recoveryPreview({api,start}){
  const $=id=>document.getElementById(`recovery-${id}`),dialog=$('dialog'),viewport=$('viewport'),canvas=$('canvas'),scrub=$('scrub');
  let job,attempts=[],data=null,worker=null,cutoff=0,ready=false,loading=0,request=0,painted=null,views=new Map(),starting=false;
  const nav=previewNavigation(viewport,{enabled:()=>ready,onChange:()=>draw(),onFit:()=>nav.reset()});
  renderPreviewControls($('tools'),{onFit:()=>nav.reset(),onZoom:factor=>nav.zoomBy(factor),idPrefix:'recovery-',showHistory:false});
  new ResizeObserver(()=>{if(dialog.open){draw();bands();}}).observe(viewport);
  function fail(error){$('error').textContent=error.message||String(error);$('error').hidden=false;}
  function draw(){
    if(!ready||!dialog.open)return;
    const view=nav.state;
    if(painted){const ratio=view.scale/painted.scale;canvas.style.transform=`translate(${view.x-ratio*painted.x}px,${view.y-ratio*painted.y}px) scale(${ratio})`;}
    const id=++request;views.set(id,view);
    const dpr=Math.min(devicePixelRatio||1,2);
    worker.postMessage({type:'render',id,cutoff,width:Math.max(1,Math.round(viewport.clientWidth*dpr)),height:Math.max(1,Math.round(viewport.clientHeight*dpr)),view:{scale:view.scale,x:view.x*dpr,y:view.y*dpr}});
  }
  function bands(){
    const track=$('bands'),ctx=track.getContext('2d');track.width=Math.max(1,track.clientWidth*devicePixelRatio);track.height=24*devicePixelRatio;
    ctx.fillStyle='#e6e8e9';ctx.fillRect(0,0,track.width,track.height);
    if(!data)return;
    for(const section of data.progress.sections){
      ctx.fillStyle=section.color;const x=track.width*section.start_byte/data.progress.total_bytes;
      ctx.fillRect(x,0,track.width*(section.end_byte-section.start_byte)/data.progress.total_bytes,track.height);
    }
    const fraction=data.strokes[cutoff][0]/data.progress.total_bytes;
    ctx.fillStyle='#ffffffce';ctx.fillRect(fraction*track.width,0,track.width,track.height);
  }
  function select(index){
    if(!data?.strokes.length)return;
    cutoff=Math.max(0,Math.min(data.strokes.length-1,Math.round(index)));
    scrub.value=data.strokes[cutoff][0];$('stroke').value=cutoff+1;
    const slot=data.strokes[cutoff][1],ink=data.submission.inks.find(ink=>ink.slot===slot);
    $('position').textContent=`of ${data.strokes.length.toLocaleString()} · slot ${slot}${ink?.loaded?.label?` · ${ink.loaded.label}`:''}`;
    scrub.setAttribute('aria-valuetext',`Next stroke ${cutoff+1} of ${data.strokes.length}, slot ${slot}`);
    $('previous').disabled=!ready||cutoff===0;$('next').disabled=!ready||cutoff===data.strokes.length-1;
    bands();draw();
  }
  scrub.oninput=()=>{
    const value=Number(scrub.value);let lo=0,hi=data.strokes.length-1;
    while(lo<hi){const mid=Math.ceil((lo+hi)/2);if(data.strokes[mid][0]>value)hi=mid-1;else lo=mid;}
    select(lo);
  };
  scrub.onkeydown=event=>{
    const step=event.shiftKey?10:1;
    const positions={ArrowLeft:cutoff-step,ArrowDown:cutoff-step,ArrowRight:cutoff+step,ArrowUp:cutoff+step,Home:0,End:data?.strokes.length-1};
    if(event.key in positions){event.preventDefault();select(positions[event.key]);}
  };
  $('previous').onclick=event=>select(cutoff-(event.shiftKey?10:1));
  $('next').onclick=event=>select(cutoff+(event.shiftKey?10:1));
  $('stroke').onchange=()=>select(Number($('stroke').value)-1);
  $('close').onclick=()=>dialog.close();
  dialog.addEventListener('cancel',event=>{if(starting)event.preventDefault();});
  dialog.addEventListener('close',()=>{loading++;worker?.terminate();worker=null;ready=false;nav.cancel();data=null;});
  $('attempt').onchange=()=>load($('attempt').value);
  async function load(id){
    const version=++loading;worker?.terminate();worker=null;data=null;ready=false;painted=null;views.clear();
    $('resume').disabled=true;scrub.disabled=true;$('stroke').disabled=true;$('previous').disabled=true;$('next').disabled=true;
    $('error').hidden=true;$('loading').hidden=false;$('loading').textContent='Loading recorded strokes…';canvas.dataset.ready='false';
    canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);canvas.style.transform='';nav.reset();
    try{
      const result=await api(`/api/plots/${id}/recovery`).then(r=>r.json());
      if(version!==loading)return;
      if(!result.strokes.length)throw Error('This attempt has no recorded pen strokes.');
      data=result;cutoff=0;scrub.max=data.progress.total_bytes;scrub.min=0;$('stroke').max=data.strokes.length;
      worker=new Worker('/recovery-worker.js',{type:'module'});
      worker.onerror=()=>{ready=false;$('resume').disabled=true;$('loading').hidden=true;fail(Error('Could not render the recorded plot.'));};
      worker.onmessage=({data:message})=>{
        if(version!==loading)return;
        if(message.type==='error'){ready=false;$('resume').disabled=true;$('loading').hidden=true;fail(message.message);}
        if(message.type==='ready'){
          if(message.count!==data.strokes.length){$('loading').hidden=true;fail('Recorded stroke index does not match the preview.');return;}
          ready=true;scrub.disabled=false;$('stroke').disabled=false;$('resume').disabled=false;$('loading').hidden=true;select(data.start_stroke||0);
        }
        if(message.type==='frame'){
          if(message.id===request){
            canvas.width=message.bitmap.width;canvas.height=message.bitmap.height;
            canvas.getContext('2d').drawImage(message.bitmap,0,0);painted=views.get(message.id);canvas.style.transform='';canvas.dataset.ready='true';canvas.dataset.cutoff=cutoff;
          }
          message.bitmap.close();for(const id of views.keys())if(id<=message.id)views.delete(id);
        }
      };
      worker.postMessage({type:'load',ticket:data.submission});
    }catch(error){if(version===loading){$('loading').hidden=true;fail(error);}}
  }
  $('resume').onclick=async()=>{
    if(!ready||starting)return;
    starting=true;$('resume').disabled=true;$('close').disabled=true;$('attempt').disabled=true;
    try{
      await start(job.id,data.submission.id,{source_sha256:data.submission.sha256,start_stroke:cutoff,pause_before_pen_change:$('pen-change').checked});
      dialog.close();
    }catch(error){fail(error);}
    finally{starting=false;$('resume').disabled=!ready;$('close').disabled=false;$('attempt').disabled=false;}
  };
  return {async open(selected,preferredAttempt=null){
    job=selected;data=null;ready=false;$('error').hidden=true;$('loading').hidden=false;$('loading').textContent='Finding previous plots…';
    canvas.dataset.ready='false';canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);canvas.style.transform='';
    painted=null;$('position').textContent='';$('stroke').value=1;scrub.value=0;bands();
    $('resume').disabled=true;scrub.disabled=true;$('stroke').disabled=true;$('previous').disabled=true;$('next').disabled=true;
    $('pen-change').checked=true;$('attempt').replaceChildren();dialog.showModal();
    const version=++loading;
    try{
      attempts=await api(`/api/jobs/${job.id}/attempts`).then(r=>r.json());if(version!==loading)return;
      if(!attempts.length)throw Error('Plot this document first. Recovery uses the exact program saved with a previous attempt.');
      for(const ticket of attempts){const option=document.createElement('option');option.value=ticket.id;option.textContent=`${new Date(ticket.created_at*1000).toLocaleString()} · ${ticket.execution?.cancelled&&ticket.execution.state==='finished'?'cancelled':ticket.execution?.state||ticket.phase}${ticket.recovery?' · repair':''}`;$('attempt').append(option);}
      const chosen=attempts.find(ticket=>ticket.id===preferredAttempt)||attempts[0];
      $('attempt').value=chosen.id;await load(chosen.id);
    }catch(error){if(version===loading){$('loading').hidden=true;fail(error);}}
  }};
}
